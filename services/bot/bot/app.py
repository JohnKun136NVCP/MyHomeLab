"""Bot + API HTTP (alertas, eventos de netwatch y subida de respaldos)."""
from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import hashlib
import hmac
import logging
import os
import time
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
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
        self._rcpt: tuple[list[int], float] = ([], 0.0)
        self._auth_cache: dict[str, tuple[dict | None, float]] = {}
        self.username = ""

    # ------------------------------------------- netwatch (cuentas de la casa)
    def nw(self, path: str, body: dict | None = None, timeout: float = 6):
        """Llama a la API interna de netwatch (/internal/…) con INTERNAL_TOKEN."""
        base = os.environ.get("NETWATCH_URL", "http://host.docker.internal:8000").rstrip("/")
        return http_json(f"{base}{path}", "POST" if body is not None else "GET", body,
                         {"Authorization": f"Bearer {self.internal_token}"}, timeout=timeout)

    def alert_chats(self) -> list[int]:
        """Quién recibe alertas y reportes: TELEGRAM_ALLOWED_IDS + cuentas con permiso de ver
        registros que vincularon su Telegram (dashboard → Cuentas). Cache de 60 s."""
        now = time.time()
        if now - self._rcpt[1] > 60:
            extra: list[int] = []
            try:
                extra = [int(x) for x in (self.nw("/internal/recipients") or {}).get("alerts", [])]
            except (RuntimeError, ValueError, TypeError):
                extra = self._rcpt[0][len(self.allowed):] if self._rcpt[0] else []
            self._rcpt = (list(dict.fromkeys(self.allowed + extra)), now)
        return self._rcpt[0] or list(self.allowed)

    def who(self, chat_id: int) -> dict | None:
        """Cuenta del dashboard vinculada a ese chat (o None)."""
        try:
            r = self.nw(f"/internal/who?chat={int(chat_id)}") or {}
        except (RuntimeError, ValueError):
            return None
        return r if r.get("user") else None

    def notice(self, user: str, text: str, level: str = "info") -> None:
        try:
            self.nw("/internal/notice", {"user": user, "text": text, "level": level})
        except RuntimeError as e:
            log.warning("No pude guardar el aviso para %s: %s", user, e)

    def device_auth(self, device: str, token: str) -> dict | None:
        """Equipos de homelab.yml (.env, del admin) o de «Mis respaldos» (netwatch)."""
        if self.backups.auth(device, token):
            return {"device": device, "owner": "", "owner_main": True, "chat": None,
                    "os": self.backups.devices.get(device, {}).get("os", "")}
        key = hashlib.sha256(f"{device}\0{token}".encode()).hexdigest()
        hit = self._auth_cache.get(key)
        if hit and time.time() - hit[1] < 300:
            return hit[0]
        try:
            r = self.nw("/internal/backup-auth", {"device": device, "token": token})
        except RuntimeError as e:
            log.warning("netwatch no contestó la autenticación de %s: %s", device, e)
            return None
        info = r if r and r.get("ok") else None
        self._auth_cache[key] = (info, time.time())
        return info

    # ---------------------------------------------------------- utilidades
    def in_quiet_hours(self, now: dt.datetime | None = None) -> bool:
        q = (self.cfg.get("anomalies", {}) or {}).get("quiet_hours")
        if not q or len(q) != 2:
            return False
        tz = ZoneInfo((self.cfg.get("reports", {}) or {}).get("timezone", "America/Mexico_City"))
        t = (now or dt.datetime.now(tz)).time()
        s, e = dt.time.fromisoformat(q[0]), dt.time.fromisoformat(q[1])
        return s <= t < e if s <= e else (t >= s or t < e)

    def notify(self, text: str, urgent: bool = True) -> None:
        """Alertas y registros del homelab: solo a quien tiene permiso de verlos.
        urgent=False en horario de silencio: llega igual, pero sin sonido."""
        if not text:
            return
        kw = {"disable_notification": True} if (not urgent and self.in_quiet_hours()) else {}
        for chat in self.alert_chats():
            try:
                self.tg.send_message(chat, text, **kw)
            except TelegramError as e:
                log.error("No pude avisar a %s: %s", chat, e)

    def tell(self, chat: int | None, text: str) -> None:
        """Mensaje a una sola persona (avisos de SUS respaldos)."""
        if not chat or not text:
            return
        try:
            self.tg.send_message(chat, text)
        except TelegramError as e:
            log.error("No pude avisar a %s: %s", chat, e)

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
                    for chat in self.alert_chats():
                        await asyncio.to_thread(self.tg.send_file, chat, tmp, "sendPhoto", "photo", caption)
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
        urgent = any((a.get("labels") or {}).get("severity") == "critical" and a.get("status") != "resolved"
                     for a in payload.get("alerts", []))
        await asyncio.to_thread(self.notify, formatting.alertmanager(payload), urgent)
        return JSONResponse({"ok": True})

    async def http_notify(self, request: Request):
        if not self._internal_ok(request):
            return JSONResponse({"error": "no autorizado"}, 401)
        body = await request.json()
        urgent = True
        if "events" in body:
            text = formatting.netwatch_events(body.get("events", []))
            urgent = any(e.get("level") == "critical" for e in body.get("events", []))
        elif "heartbeat" in body or "monitor" in body or "msg" in body:      # webhook de Uptime Kuma
            text = formatting.uptime_kuma(body)
            urgent = (body.get("heartbeat") or {}).get("status") == 0      # caído = con sonido
        else:
            text = esc(body.get("text", ""))
            urgent = not body.get("silent")
        await asyncio.to_thread(self.notify, text, urgent)
        return JSONResponse({"ok": True})

    async def http_backup(self, request: Request):
        """PUT /api/backup/<dispositivo>/<archivo>  (curl -T archivo …)
        Cabeceras: Authorization: Bearer <token del dispositivo>
                   X-Backup-Session: <id>  (opcional: junta las partes .001, .002… en una carpeta)"""
        device = request.path_params["device"]
        filename = request.path_params["filename"]
        token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        info = await asyncio.to_thread(self.device_auth, device, token)
        if not info:
            src = request.headers.get("x-forwarded-for", "") or (request.client.host if request.client else "?")
            if time.time() - self._warned.get(hash(device), 0) > 600:      # sin inundar el chat
                self._warned[hash(device)] = time.time()
                await asyncio.to_thread(self.notify, f"🚫 Subida de respaldo rechazada para «{esc(device)}» "
                                                     f"desde {esc(src.split(',')[0])}")
            return JSONResponse({"error": "token inválido o dispositivo no registrado"}, 401)
        session = request.headers.get("x-backup-session", "")
        if filename == "__fin__":                 # fin de una subida por lotes (navegador)
            asyncio.create_task(self._after_batch(info, session))
            return JSONResponse({"ok": True})
        length = int(request.headers.get("content-length") or 0)
        if length > MAX_UPLOAD:
            return JSONResponse({"error": f"máximo {human_bytes(MAX_UPLOAD)}"}, 413)
        dest = self.backups.target(device, filename, session)
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
        os_ = info.get("os") or self.backups.devices.get(device, {}).get("os", "")
        bid = self.store.add_backup(device, os_, str(dest), size, "http")
        if request.headers.get("x-backup-batch") != "1":           # en lote: un solo aviso al final
            asyncio.create_task(self._after_backup(bid, device, os_, dest, size, info))
        return JSONResponse({"ok": True, "id": bid, "path": str(dest.relative_to(self.backups.root)),
                             "size": size})

    async def _after_backup(self, bid, device, os_, dest: Path, size: int, info: dict | None = None):
        icon = {"windows": "🪟", "linux": "🐧", "macos": "🍎", "android": "📱", "ios": "📱",
                "web": "🌐", "telegram": "✈"}.get(os_, "💾")
        info = info or {"owner_main": True}
        removed = self.backups.prune(device)
        clean = f"\n🧹 Borré {len(removed)} respaldo(s) viejo(s) de este equipo" if removed else ""
        if info.get("owner_main"):                 # equipos del admin: como siempre
            text = (f"{icon} Respaldo recibido de <b>{esc(device)}</b> ({esc(os_)})\n"
                    f"{esc(dest.name)} · {human_bytes(size)}\n<code>{esc(dest.parent)}</code>{clean}")
            await self._deliver(bid, dest, size, text, self.owner, admin_copy=False)
            return
        # Equipo de un familiar: el archivo y el detalle SOLO a su dueño
        owner = info.get("owner", "")
        text = (f"{icon} Llegó tu respaldo de <b>{esc(device)}</b>\n{esc(dest.name)} · {human_bytes(size)}"
                f"{clean}")
        await asyncio.to_thread(self.notice, owner, f"✅ Respaldo de {device} recibido: {dest.name} "
                                                    f"({human_bytes(size)})", "ok")
        await self._deliver(bid, dest, size, text, info.get("chat"), admin_copy=True, owner=owner)

    async def _deliver(self, bid, dest: Path, size: int, text: str, chat, admin_copy: bool, owner: str = ""):
        if chat and self.backups.forward and size <= self.tg.limit:
            try:
                await asyncio.to_thread(self.tg.send_file, chat, dest, "sendDocument", "document", text)
                self.store.x("UPDATE backups SET forwarded=1 WHERE id=?", (bid,))
            except TelegramError as e:
                await asyncio.to_thread(self.tell, chat, text + f"\n⚠ No pude reenviarte el archivo: {esc(e)}")
        elif chat:
            extra = "" if size <= self.tg.limit else f"\n(No te lo reenvío: pesa más de {human_bytes(self.tg.limit)})"
            await asyncio.to_thread(self.tell, chat, text + extra)
        if admin_copy:                             # al admin solo una línea, sin el archivo
            await asyncio.to_thread(self.notify, f"📦 Respaldo de <b>{esc(owner)}</b> recibido "
                                                 f"({esc(dest.parent.parent.name)}, {human_bytes(size)})", False)

    async def _after_batch(self, info: dict, session: str):
        """Resumen de una subida desde el navegador (muchas fotos = un solo aviso)."""
        device = info["device"]
        d = next((p.parent for p in (self.backups.root / device).glob("*/.session")
                  if p.read_text().strip() == session), None) if session else None
        if not d:
            return
        files = [f for f in d.iterdir() if f.is_file() and f.name != ".session"]
        size = sum(f.stat().st_size for f in files)
        removed = self.backups.prune(device)
        owner = info.get("owner") or "admin"
        msg = f"🌐 Subiste {len(files)} archivo(s) ({human_bytes(size)}) a <b>{esc(device)}</b>"
        if removed:
            msg += f"\n🧹 Se borraron {len(removed)} respaldo(s) viejo(s)"
        if info.get("owner_main"):
            await asyncio.to_thread(self.notify, msg)
            return
        await asyncio.to_thread(self.notice, owner, f"✅ Subiste {len(files)} archivo(s) ({human_bytes(size)}).", "ok")
        await asyncio.to_thread(self.tell, info.get("chat"), msg)
        await asyncio.to_thread(self.notify, f"📦 {esc(owner)} subió {len(files)} archivo(s) "
                                             f"({human_bytes(size)}) desde el navegador", False)

    # ------------------------------------------------- API interna (netwatch)
    async def i_info(self, request: Request):
        if not self._internal_ok(request):
            return JSONResponse({"error": "no autorizado"}, 401)
        if not self.username and self.tg.token:
            try:
                me = await asyncio.to_thread(self.tg.call, "getMe")
                self.username = me.get("username", "")
            except TelegramError:
                pass
        return JSONResponse({"username": self.username, "limit": self.tg.limit})

    async def i_backups(self, request: Request):
        """Resumen y archivos de los equipos pedidos (para «Mis respaldos»)."""
        if not self._internal_ok(request):
            return JSONResponse({"error": "no autorizado"}, 401)
        out = {}
        for dev in [d for d in request.query_params.get("devices", "").split(",") if d][:50]:
            rows = [r for r in self.store.backups_of(dev) if Path(r["path"]).exists()]
            out[dev] = {"last": rows[0]["ts"] if rows else None, "count": len(rows),
                        "size": sum(r["size"] or 0 for r in rows),
                        "files": [{"id": r["id"], "name": Path(r["path"]).name, "size": r["size"], "ts": r["ts"],
                                   "source": r["source"]} for r in rows[:30]]}
        return JSONResponse({"devices": out})

    async def i_backup_file(self, request: Request):
        if not self._internal_ok(request):
            return JSONResponse({"error": "no autorizado"}, 401)
        r = self.store.q("SELECT * FROM backups WHERE id=?", (int(request.path_params["bid"]),))
        if not r or r[0]["device"] != request.query_params.get("device"):
            return JSONResponse({"error": "no existe"}, 404)
        p = Path(r[0]["path"])
        if not p.exists():
            return JSONResponse({"error": "ya se borró"}, 404)
        return FileResponse(p, filename=p.name, media_type="application/octet-stream")

    async def http_backup_check(self, request: Request):
        """GET /api/backup/<dispositivo>/<algo>: los clientes prueban su token sin subir nada."""
        token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        ok = bool(await asyncio.to_thread(self.device_auth, request.path_params["device"], token))
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
            Route("/internal/info", self.i_info),
            Route("/internal/backups", self.i_backups),
            Route("/internal/backup-file/{bid:int}", self.i_backup_file),
        ], lifespan=lifespan)
