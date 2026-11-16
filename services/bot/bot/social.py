"""Publicación en Facebook, Instagram y un canal de Telegram (API oficiales).

Facebook/Instagram usan la Graph API de Meta con un token de página
(ver docs/redes-sociales.md). Instagram exige que la imagen esté en una URL
pública; para no tener que exponer la Raspberry, primero se sube la foto a tu
página de Facebook como NO publicada y se usa la URL que Meta le asigna.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .tg import Telegram, TelegramError

log = logging.getLogger("bot.social")
NETWORKS = {"fb": "Facebook", "ig": "Instagram", "tg": "Canal de Telegram"}


class SocialError(RuntimeError):
    pass


class Social:
    def __init__(self, cfg: dict, tg: Telegram):
        s = cfg.get("social", {}) or {}
        self.page = str(s.get("facebook_page_id") or "")
        self.ig = str(s.get("instagram_user_id") or "")
        self.channel = s.get("telegram_channel") or ""
        self.token = os.environ.get("META_PAGE_TOKEN", "")
        self.graph = os.environ.get("META_GRAPH_URL", "https://graph.facebook.com/v21.0").rstrip("/")
        self.tg = tg

    def available(self) -> dict[str, bool]:
        return {"fb": bool(self.page and self.token), "ig": bool(self.ig and self.page and self.token),
                "tg": bool(self.channel)}

    # ---------------------------------------------------------- Graph API
    def _graph(self, path: str, fields: dict, files: dict | None = None, method: str = "POST") -> dict:
        """Llamada a la Graph API con curl (sube archivos en streaming)."""
        url = f"{self.graph}/{path.lstrip('/')}"
        if method == "GET":
            q = urllib.parse.urlencode({**fields, "access_token": self.token})
            try:
                with urllib.request.urlopen(f"{url}?{q}", timeout=60) as r:
                    res = json.loads(r.read())
            except urllib.error.HTTPError as e:
                res = json.loads(e.read() or b"{}")
            except OSError as e:
                raise SocialError(f"Meta no responde: {e}") from e
        else:
            conf = [f'url = "{url}"', f'form-string = "access_token={_q(self.token)}"']
            conf += [f'form-string = "{k}={_q(str(v))}"' for k, v in fields.items()]
            conf += [f'form = "{k}=@\\"{_q(str(p))}\\""' for k, p in (files or {}).items()]
            p = subprocess.run(["curl", "-sS", "-K", "-"], input="\n".join(conf),
                               capture_output=True, text=True, timeout=1800)
            try:
                res = json.loads(p.stdout)
            except ValueError as e:
                raise SocialError(f"Respuesta inválida de Meta: {p.stdout[:200]} {p.stderr[:200]}") from e
        if "error" in res:
            err = res["error"]
            raise SocialError(f"Meta: {err.get('message', err)}".replace(self.token, "<token>"))
        return res

    def facebook(self, text: str, media: Path | None, kind: str) -> str:
        if media and kind == "video":
            r = self._graph(f"{self.page}/videos", {"description": text}, {"source": media})
        elif media:
            r = self._graph(f"{self.page}/photos", {"caption": text}, {"source": media})
        else:
            r = self._graph(f"{self.page}/feed", {"message": text})
        return r.get("post_id") or r.get("id", "")

    def instagram(self, text: str, media: Path | None, kind: str) -> str:
        if not media or kind != "photo":
            raise SocialError("Instagram solo acepta publicaciones con foto aquí (sin texto solo ni video).")
        up = self._graph(f"{self.page}/photos", {"published": "false", "temporary": "true"},
                         {"source": media})
        info = self._graph(up["id"], {"fields": "images"}, method="GET")
        images = sorted(info.get("images", []), key=lambda i: i.get("width", 0), reverse=True)
        if not images:
            raise SocialError("Meta no regresó la URL de la imagen")
        cont = self._graph(f"{self.ig}/media", {"image_url": images[0]["source"], "caption": text})
        for _ in range(10):     # Instagram procesa la imagen unos segundos
            st = self._graph(cont["id"], {"fields": "status_code"}, method="GET")
            if st.get("status_code") in ("FINISHED", None):
                break
            if st.get("status_code") == "ERROR":
                raise SocialError("Instagram no pudo procesar la imagen")
            time.sleep(3)
        pub = self._graph(f"{self.ig}/media_publish", {"creation_id": cont["id"]})
        return pub.get("id", "")

    def telegram_channel(self, text: str, media: Path | None, kind: str) -> str:
        try:
            if media and kind == "video":
                r = self.tg.send_file(self.channel, media, "sendVideo", "video", text)
            elif media:
                r = self.tg.send_file(self.channel, media, "sendPhoto", "photo", text)
            else:
                r = self.tg.call("sendMessage", chat_id=self.channel, text=text)
        except TelegramError as e:
            raise SocialError(str(e)) from e
        return str(r.get("message_id", ""))

    def publish(self, networks: list[str], text: str, media: str, kind: str) -> dict[str, str]:
        """Publica en cada red. Regresa {red: "ok id" | "error: …"}."""
        path = Path(media) if media else None
        out = {}
        for n in networks:
            fn = {"fb": self.facebook, "ig": self.instagram, "tg": self.telegram_channel}.get(n)
            if not fn:
                out[n] = "error: red desconocida"
                continue
            if not self.available().get(n):
                out[n] = "error: falta configurarla (homelab.yml / .env)"
                continue
            try:
                out[n] = "ok " + fn(text, path, kind)
            except SocialError as e:
                out[n] = f"error: {e}"
        return out


def _q(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
