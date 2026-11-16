"""Cliente mínimo de la Bot API de Telegram (servidor local de 2 GB u oficial).

Las subidas usan curl: así un archivo de 2 GB se manda en streaming sin
cargarlo en memoria (importante en una Raspberry Pi 4).
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

log = logging.getLogger("bot.tg")
MiB = 1024 * 1024
LIMIT_LOCAL = 1900 * MiB      # < 2000 MB decimales del servidor local
LIMIT_PUBLIC = 48 * MiB       # API pública: 50 MB


class TelegramError(RuntimeError):
    pass


class Telegram:
    def __init__(self, token: str, api_url: str = "https://api.telegram.org"):
        self.token = token
        self.api = api_url.rstrip("/")
        self.local = self.api != "https://api.telegram.org"
        self.limit = LIMIT_LOCAL if self.local else LIMIT_PUBLIC

    def _url(self, method: str) -> str:
        return f"{self.api}/bot{self.token}/{method}"

    def _scrub(self, text: str) -> str:
        return text.replace(self.token, "<token>") if self.token else text

    def call(self, method: str, _timeout: float = 30, **params) -> dict | list | bool:
        data = json.dumps({k: v for k, v in params.items() if v is not None}).encode()
        req = urllib.request.Request(self._url(method), data=data,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=_timeout) as r:
                res = json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            raise TelegramError(self._scrub(f"{method}: HTTP {e.code} {body[:300]}")) from e
        except (urllib.error.URLError, OSError) as e:
            raise TelegramError(self._scrub(f"{method}: {e}")) from e
        if not res.get("ok"):
            raise TelegramError(self._scrub(f"{method}: {res.get('description')}"))
        return res["result"]

    # ------------------------------------------------------------ texto
    def send_message(self, chat_id, text: str, **kw):
        # Telegram corta en 4096 caracteres: partimos los mensajes largos
        chunks, cur = [], ""
        for line in text.split("\n"):
            if len(cur) + len(line) + 1 > 4000:
                chunks.append(cur)
                cur = ""
            cur += line + "\n"
        chunks.append(cur)
        res = None
        for c in chunks:
            if c.strip():
                res = self.call("sendMessage", chat_id=chat_id, text=c, parse_mode="HTML",
                                disable_web_page_preview=True, **kw)
        return res

    def get_updates(self, offset: int, timeout: int = 50) -> list[dict]:
        return self.call("getUpdates", _timeout=timeout + 15, offset=offset, timeout=timeout,
                         allowed_updates=["message"]) or []

    # ---------------------------------------------------------- archivos
    def send_file(self, chat_id, path: Path, method: str = "sendDocument", field: str = "document",
                  caption: str = "", timeout: int = 7200, extra: dict | None = None) -> dict:
        """sendDocument/sendPhoto/sendVideo en streaming con curl. El token va por stdin."""
        size = path.stat().st_size
        if size > self.limit:
            raise TelegramError(f"{path.name} pesa {size / MiB:.0f} MB; el límite es "
                                f"{self.limit / MiB:.0f} MB")
        conf = [f'url = "{self._url(method)}"', f'form = "chat_id={chat_id}"',
                f'form = "{field}=@\\"{_q(str(path))}\\""', 'form = "parse_mode=HTML"']
        if caption:
            conf.append(f'form-string = "caption={_q(caption[:1024])}"')
        for k, v in (extra or {}).items():
            conf.append(f'form-string = "{k}={_q(str(v))}"')
        return self._curl(conf, timeout)

    def _curl(self, conf: list[str], timeout: int) -> dict:
        try:
            p = subprocess.run(["curl", "-sS", "-K", "-"], input="\n".join(conf),
                               capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            raise TelegramError("Tiempo agotado subiendo el archivo") from e
        try:
            res = json.loads(p.stdout)
        except ValueError as e:
            raise TelegramError(self._scrub(f"Respuesta inválida: {p.stdout[:200]} {p.stderr[:200]}")) from e
        if not res.get("ok"):
            raise TelegramError(self._scrub(str(res.get("description"))))
        return res["result"]

    def download(self, file_id: str, dest: Path) -> Path:
        """Baja un archivo que te mandaron. Con el servidor local (--local) el archivo ya
        está en disco: se copia (hasta 2 GB). Con la API pública el máximo es 20 MB."""
        info = self.call("getFile", file_id=file_id)
        fp = info.get("file_path", "")
        dest.parent.mkdir(parents=True, exist_ok=True)
        if fp.startswith("/") and Path(fp).exists():
            shutil.copy2(fp, dest)
            return dest
        url = f"{self.api}/file/bot{self.token}/{urllib.parse.quote(fp)}"
        try:
            with urllib.request.urlopen(url, timeout=600) as r, open(dest, "wb") as out:
                shutil.copyfileobj(r, out, 8 * MiB)
        except (urllib.error.URLError, OSError) as e:
            raise TelegramError(self._scrub(f"No pude bajar el archivo: {e}")) from e
        return dest


def _q(s: str) -> str:
    """Escapa para el formato de configuración de curl (-K)."""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def esc(s) -> str:
    """Escapa texto para parse_mode=HTML."""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def allowed_ids() -> list[int]:
    out = []
    for x in os.environ.get("TELEGRAM_ALLOWED_IDS", "").replace(" ", "").split(","):
        if x.lstrip("-").isdigit():
            out.append(int(x))
    return out
