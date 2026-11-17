"""Bot + API HTTP (alertas, eventos de netwatch y subida de respaldos)."""
from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import hmac
import logging
import os
import time
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from homelab_common import data_dir, http_json, human_bytes, load_config

from . import formatting, reports
from .backups import Backups
from .handlers import Handlers
from .social import NETWORKS, Social
from .store import Store
from .tg import Telegram, TelegramError, allowed_ids, esc

log = logging.getLogger("bot")
MAX_UPLOAD = int(os.environ.get("MAX_UPLOAD_GB", "4")) * 1024 ** 3


class BotApp:
    def __init__(self):
        self.cfg = load_config()
        self.data = data_dir()
        self.allowed = allowed_ids()
        if not self.allowed:
            log.warning("TELEGRAM_ALLOWED_IDS está vacío: el bot no le responderá a nadie")
        self.owner = self.allowed[0] if self.allowed else None
        self.tg = Telegram(os.environ.get("TELEGRAM_BOT_TOKEN", ""),
                           os.environ.get("TELEGRAM_API_URL", "https://api.telegram.org"))
        self.store = Store(self.data / "bot.db")
        self.backups = Backups(self.cfg, self.store)
        self.social = Social(self.cfg, self.tg)
        self.handlers = Handlers(self)
        self.internal_token = os.environ.get("INTERNAL_TOKEN", "")
        self._warned: dict[int, float] = {}
        self._frigate_seen: set[str] = set()
        self._frigate_last: dict[str, float] = {}

    # ---------------------------------------------------------- utilidades
    def notify(self, text: str) -> None:
        if not self.owner or not text:
            return
        try:
            self.tg.send_message(self.owner, text)
        except TelegramError as e:
            log.error("No pude avisar: %s", e)

    def unauthorized(self, user: dict, msg: dict) -> None:
        """Ignora a desconocidos y te avisa (máx. una vez al día por persona)."""
        uid = user.get("id", 0)
        if time.time() - self._warned.get(uid, 0) < 86400:
            return
        self._warned[uid] = time.time()
        who = f"{user.get('first_name', '')} @{user.get('username', '—')} (id <code>{uid}</code>)"
        self.notify(f"🚫 Alguien intentó usar tu bot: {esc(who)}\n"
                    f"Mensaje: {esc((msg.get('text') or '[archivo]')[:200])}")

    # ------------------------------------------------------------- ciclos
    async def poll_loop(self) -> None:
        offset = self.store.get("offset", 0)
        while True:
            try:
                updates = await asyncio.to_thread(self.tg.get_updates, offset, 50)
            except TelegramError as e:
                log.warning("getUpdates: %s", e)
                await asyncio.sleep(10)
                continue
            for u in updates:
                offset = u["update_id"] + 1
                self.store.set("offset", offset)
                await asyncio.to_thread(self.handlers.handle, u)

    async def scheduler_loop(self) -> None:
        tz = ZoneInfo((self.cfg.get("reports", {}) or {}).get("timezone", "America/Mexico_City"))
        report_at = (self.cfg.get("reports", {}) or {}).get("daily_time", "09:00")
        while True:
            try:
                for p in self.store.due_posts(time.time()):
                    res = await asyncio.to_thread(self.social.publish, p["networks"], p["text"],
                                                  p["media"], p["media_kind"])
                    ok = all(v.startswith("ok") for v in res.values())
                    self.store.finish_post(p["id"], "done" if ok else "error", str(res))
                    if ok and p["media"]:          # ya está en las redes: no ocupar disco
                        Path(p["media"]).unlink(missing_ok=True)
                        self.store.x("UPDATE posts SET media='' WHERE id=?", (p["id"],))
                    self.notify(f"{'✅' if ok else '⚠'} Publicación #{p['id']}:\n" + "\n".join(
                        f"  {NETWORKS.get(k, k)}: {esc(v)}" for k, v in res.items()))
                now = dt.datetime.now(tz)
                today = now.date().isoformat()
                if self.store.get("last_prune") != today:          # limpieza diaria
                    self.store.set("last_prune", today)
                    removed = await asyncio.to_thread(self.backups.prune_all)
                    removed_media = await asyncio.to_thread(self.prune_social_media)
                    if removed:
                        self.notify("🧹 Respaldos viejos borrados: " + ", ".join(
                            f"{esc(d)} ({len(r)})" for d, r in removed.items()))
                    if removed_media:
                        log.info("Multimedia de publicaciones borrada: %d archivos", removed_media)
                if now.strftime("%H:%M") >= report_at and self.store.get("last_report") != today:
                    self.store.set("last_report", today)
                    text = await asyncio.to_thread(reports.daily_report, self.cfg, self.backups)
                    self.notify(text)
            except Exception:  # noqa: BLE001
                log.exception("scheduler")
            await asyncio.sleep(30)

    async def frigate_loop(self) -> None:
        """Avisa con foto cuando Frigate detecta personas/autos (sin MQTT)."""
        cam = self.cfg.get("cameras", {}) or {}
        url = (cam.get("frigate_url") or "").rstrip("/")
        if not url:
            return
        labels = set(cam.get("notify_labels") or ["person"])
        cooldown = int(cam.get("notify_cooldown", 300))
        since = time.time()
        while True:
            await asyncio.sleep(15)
            try:
                evs = await asyncio.to_thread(
                    lambda s=since: http_json(f"{url}/api/events?after={s:.0f}&limit=25&has_snapshot=1",
                                      timeout=10))
            except RuntimeError:
                continue
            for e in reversed(evs or []):
                if e["id"] in self._frigate_seen or e.get("label") not in labels:
                    continue
                self._frigate_seen.add(e["id"])
                key = f"{e.get('camera')}:{e.get('label')}"
                if time.time() - self._frigate_last.get(key, 0) < cooldown:
                    continue
                self._frigate_last[key] = time.time()
                try:
                    tmp = self.data / "frigate.jpg"
                    with urllib.request.urlopen(f"{url}/api/events/{e['id']}/snapshot.jpg?bbox=1", timeout=10) as r:
                        tmp.write_bytes(r.read())
                    caption = (f"📷 <b>{esc(e.get('camera'))}</b>: {esc(e.get('label'))} "
                               f"({(e.get('top_score') or e.get('data', {}).get('top_score') or 0) * 100:.0f}%) "
                               f"· {dt.datetime.fromtimestamp(e['start_time']).strftime('%H:%M:%S')}")
                    await asyncio.to_thread(self.tg.send_file, self.owner, tmp, "sendPhoto", "photo", caption)
                except (OSError, TelegramError) as ex:
                    log.warning("Foto de Frigate: %s", ex)
            if evs:
                since = max(since, max(e.get("start_time", 0) for e in evs))
            if len(self._frigate_seen) > 2000:
                self._frigate_seen = set(list(self._frigate_seen)[-500:])

    def prune_social_media(self) -> int:
        """Borra multimedia de publicaciones que ya no se van a usar: publicadas, canceladas
        o con error de hace más de 7 días."""
        n = 0
        cutoff = time.time() - 7 * 86400
        for p in self.store.q("SELECT id, media, status, run_at FROM posts WHERE media != ''"):
            if p["status"] in ("done", "cancelled") or (p["status"] == "error" and p["run_at"] < cutoff):
                Path(p["media"]).unlink(missing_ok=True)
                self.store.x("UPDATE posts SET media='' WHERE id=?", (p["id"],))
                n += 1
        d = self.data / "social"
        if d.exists():                        # huérfanos (de publicaciones borradas)
            used = {r["media"] for r in self.store.q("SELECT media FROM posts WHERE media != ''")}
            for f in d.iterdir():
                if str(f) not in used and f.stat().st_mtime < cutoff:
                    f.unlink(missing_ok=True)
                    n += 1
        return n

    # -------------------------------------------------------------- HTTP
    def _internal_ok(self, request: Request) -> bool:
        tok = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        return bool(self.internal_token) and hmac.compare_digest(tok, self.internal_token)

    async def http_alert(self, request: Request):
        """Webhook de Alertmanager (solo llega desde la red interna de Docker)."""
        payload = await request.json()
        await asyncio.to_thread(self.notify, formatting.alertmanager(payload))
        return JSONResponse({"ok": True})

    async def http_notify(self, request: Request):
        if not self._internal_ok(request):
            return JSONResponse({"error": "no autorizado"}, 401)
        body = await request.json()
        text = formatting.netwatch_events(body.get("events", [])) if "events" in body else esc(body.get("text", ""))
        await asyncio.to_thread(self.notify, text)
        return JSONResponse({"ok": True})

    async def http_backup(self, request: Request):
        """PUT /api/backup/<dispositivo>/<archivo>  (curl -T archivo …)
        Cabeceras: Authorization: Bearer <token del dispositivo>
                   X-Backup-Session: <id>  (opcional: junta las partes .001, .002… en una carpeta)"""
        device = request.path_params["device"]
        filename = request.path_params["filename"]
        token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not self.backups.auth(device, token):
            await asyncio.to_thread(self.notify, f"🚫 Subida de respaldo rechazada para «{esc(device)}» "
                                                 f"desde {esc(request.client.host if request.client else '?')}")
            return JSONResponse({"error": "token inválido o dispositivo no registrado"}, 401)
        length = int(request.headers.get("content-length") or 0)
        if length > MAX_UPLOAD:
            return JSONResponse({"error": f"máximo {human_bytes(MAX_UPLOAD)}"}, 413)
        dest = self.backups.target(device, filename, request.headers.get("x-backup-session", ""))
        part = dest.with_name(dest.name + ".parcial")
        size = 0
        try:
            with open(part, "wb") as f:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_UPLOAD:
                        raise ValueError("demasiado grande")
                    f.write(chunk)
        except (ValueError, OSError) as e:
            part.unlink(missing_ok=True)
            return JSONResponse({"error": f"subida interrumpida: {e}"}, 400)
        if length and size != length:
            part.unlink(missing_ok=True)
            return JSONResponse({"error": f"llegaron {size} de {length} bytes"}, 400)
        part.replace(dest)
        os_ = self.backups.devices.get(device, {}).get("os", "")
        bid = self.store.add_backup(device, os_, str(dest), size, "http")
        asyncio.create_task(self._after_backup(bid, device, os_, dest, size))
        return JSONResponse({"ok": True, "id": bid, "path": str(dest.relative_to(self.backups.root)),
                             "size": size})

    async def _after_backup(self, bid, device, os_, dest: Path, size: int):
        icon = {"windows": "🪟", "linux": "🐧", "macos": "🍎", "android": "📱", "ios": "📱"}.get(os_, "💾")
        text = (f"{icon} Respaldo recibido de <b>{esc(device)}</b> ({esc(os_)})\n"
                f"{esc(dest.name)} · {human_bytes(size)}\n<code>{esc(dest.parent)}</code>")
        removed = self.backups.prune(device)
        if removed:
            text += f"\n🧹 Borré {len(removed)} respaldo(s) viejo(s) de este dispositivo"
        if self.backups.forward and self.owner:
            if size <= self.tg.limit:
                try:
                    await asyncio.to_thread(self.tg.send_file, self.owner, dest, "sendDocument",
                                            "document", text)
                    self.store.x("UPDATE backups SET forwarded=1 WHERE id=?", (bid,))
                    return
                except TelegramError as e:
                    text += f"\n⚠ No pude reenviarlo a Telegram: {esc(e)}"
            else:
                text += f"\n(No lo reenvío: pesa más de {human_bytes(self.tg.limit)}; usa partes .7z.001)"
        await asyncio.to_thread(self.notify, text)

    async def http_backup_check(self, request: Request):
        """GET /api/backup/<dispositivo>/<algo>: los clientes prueban su token sin subir nada."""
        token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        ok = self.backups.auth(request.path_params["device"], token)
        return JSONResponse({"ok": ok, "limit": self.tg.limit}, 200 if ok else 401)

    async def http_health(self, _):
        return JSONResponse({"ok": True, "telegram_local": self.tg.local, "limit": self.tg.limit})

    def asgi(self) -> Starlette:
        @contextlib.asynccontextmanager
        async def lifespan(_app):
            tasks = []
            if self.tg.token and os.environ.get("BOT_NO_POLL") != "1":
                tasks.append(asyncio.create_task(self.poll_loop()))
            tasks.append(asyncio.create_task(self.scheduler_loop()))
            tasks.append(asyncio.create_task(self.frigate_loop()))
            log.info("Bot listo (servidor %s, límite %s)", self.tg.api if self.tg.local else "oficial",
                     human_bytes(self.tg.limit))
            yield
            for t in tasks:
                t.cancel()

        return Starlette(routes=[
            Route("/health", self.http_health),
            Route("/alert", self.http_alert, methods=["POST"]),
            Route("/notify", self.http_notify, methods=["POST"]),
            Route("/api/backup/{device}/{filename:path}", self.http_backup, methods=["PUT", "POST"]),
            Route("/api/backup/{device}/{filename:path}", self.http_backup_check, methods=["GET"]),
        ], lifespan=lifespan)
