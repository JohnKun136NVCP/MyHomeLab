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
        from .reminders import Reminders
        self.reminders = Reminders(self.store)
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
                for r in self.reminders.due(time.time()):
                    try:
                        await asyncio.to_thread(self.tg.send_message, r["chat"], f"⏰ <b>Recordatorio</b>\n{esc(r['text'])}")
                    except TelegramError as e:
                        log.warning("Recordatorio %s: %s", r["id"], e)
                    self.reminders.done(r, tz)
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
                if now.strftime("%H:%M") >= report_at and now.weekday() == 6 and self.store.get("last_weekly") != today:
                    self.store.set("last_weekly", today)
                    self.notify(await asyncio.to_thread(reports.weekly_digest))
                month = now.strftime("%Y-%m")
                if now.strftime("%H:%M") >= report_at and now.day == 1 and self.store.get("last_monthly") != month:
                    self.store.set("last_monthly", month)
                    self.notify(await asyncio.to_thread(reports.monthly_internet))
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
        if "diun_version" in body:                   # webhook de Diun (imágenes nuevas)
            text, urgent = formatting.diun(body), False
        elif "events" in body:
            text = formatting.netwatch_events(body.get("events", []))
            urgent = any(e.get("level") == "critical" for e in body.get("events", []))
        elif "heartbeat" in body or "monitor" in body or "msg" in body:      # webhook de Uptime Kuma
            text = formatting.uptime_kuma(body)
            urgent = (body.get("heartbeat") or {}).get("status") == 0      # caído = con sonido
        else:
            text = esc(body.get("text", ""))
            urgent = not body.get("silent")
        if body.get("to") == "owner":           # códigos de acceso (Authelia): solo a ti, nunca a «registros»
            for chat in self.allowed:
                await asyncio.to_thread(self.tell, chat, text)
            return JSONResponse({"ok": True})
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
            await self._deliver(bid, dest, size, text, self.owner, admin_copy=False, prefs=info.get("prefs"))
            return
        # Equipo de un familiar: el archivo y el detalle SOLO a su dueño
        owner = info.get("owner", "")
        text = (f"{icon} Llegó tu respaldo de <b>{esc(device)}</b>\n{esc(dest.name)} · {human_bytes(size)}"
                f"{clean}")
        await asyncio.to_thread(self.notice, owner, f"✅ Respaldo de {device} recibido: {dest.name} "
                                                    f"({human_bytes(size)})", "ok")
        await self._deliver(bid, dest, size, text, info.get("chat"), admin_copy=True, owner=owner,
                            prefs=info.get("prefs"))

    def send_big(self, chat, path: Path, caption: str) -> int:
        """Manda un archivo por Telegram; si pasa del límite (2 GB con la Bot API local), en partes
        nombre.001, .002… que se juntan con 7-Zip/ZArchiver o «cat». Regresa cuántas partes mandó."""
        size = path.stat().st_size
        if size <= self.tg.limit:
            self.tg.send_file(chat, path, "sendDocument", "document", caption)
            return 1
        part_size = self.tg.limit - 16 * 1024 * 1024
        n = -(-size // part_size)
        with open(path, "rb") as src:
            for i in range(1, n + 1):
                part = path.with_name(f"{path.name}.{i:03d}")
                with open(part, "wb") as out:
                    left = part_size
                    while left > 0:
                        b = src.read(min(8 * 1024 * 1024, left))
                        if not b:
                            break
                        out.write(b)
                        left -= len(b)
                try:
                    self.tg.send_file(chat, part, "sendDocument", "document",
                                      (caption + "\n" if i == 1 else "") + f"Parte {i} de {n}")
                finally:
                    part.unlink(missing_ok=True)
                time.sleep(1)                       # Telegram: no más de ~1 mensaje por segundo al mismo chat
        self.tell(chat, f"🧩 <b>{esc(path.name)}</b> llegó en {n} partes. Para juntarlas: descarga todas en la misma "
                        "carpeta y abre la <b>.001</b> con 7-Zip (Windows) o ZArchiver (Android); en Linux/Mac: "
                        f"<code>cat {esc(path.name)}.0* &gt; {esc(path.name)}</code>")
        return n

    def send_zips(self, chat, files: list[Path], workdir: Path, base: str, caption: str) -> int:
        """Empaca en .zip (sin comprimir: fotos y videos ya vienen comprimidos) y los manda.
        Si no cabe en uno, hace varios .zip que se abren cada uno por separado; un archivo que
        él solo pase del límite va en partes .001/.002. Regresa cuántos .zip mandó."""
        import zipfile
        room = self.tg.limit - 32 * 1024 * 1024
        groups, cur, size = [], [], 0
        for f in sorted(files):
            fs = f.stat().st_size
            if cur and size + fs > room:
                groups.append(cur)
                cur, size = [], 0
            cur.append(f)
            size += fs
        if cur:
            groups.append(cur)
        n = len(groups)
        stamp = dt.datetime.now().strftime("%Y-%m-%d")
        for i, g in enumerate(groups, 1):
            name = f"{base}-{stamp}" + (f"-parte{i}de{n}" if n > 1 else "") + ".zip"
            z = workdir / name
            with zipfile.ZipFile(z, "w", zipfile.ZIP_STORED, allowZip64=True) as zf:
                for f in g:
                    zf.write(f, f"{f.parent.name}/{f.name}" if len({x.parent for x in files}) > 1 else f.name)
            try:
                self.send_big(chat, z, (caption + "\n" if i == 1 else "") + (f"📦 .zip {i} de {n} · {len(g)} archivo(s)"))
            finally:
                z.unlink(missing_ok=True)
            time.sleep(1)
        if n > 1:
            self.tell(chat, f"✅ Te mandé {n} archivos .zip. Cada uno se abre por separado (no hay que juntarlos).")
        return n

    def drop_backup(self, bid: int, path: Path) -> None:
        """Borra un respaldo de la Pi (ya lo tiene su dueño) y su carpeta si quedó vacía."""
        path.unlink(missing_ok=True)
        self.store.delete_backup_row(bid)
        d = path.parent
        with contextlib.suppress(OSError):
            rest = [f for f in d.iterdir() if f.name != ".session"]
            if not rest:
                (d / ".session").unlink(missing_ok=True)
                d.rmdir()

    async def _deliver(self, bid, dest: Path, size: int, text: str, chat, admin_copy: bool, owner: str = "",
                       prefs: dict | None = None):
        prefs = prefs or {}
        send = self.backups.forward and prefs.get("send_telegram", True)
        if chat and send:
            try:
                await asyncio.to_thread(self.send_big, chat, dest, text)
                self.store.x("UPDATE backups SET forwarded=1 WHERE id=?", (bid,))
                if prefs.get("delete_after"):
                    await asyncio.to_thread(self.drop_backup, bid, dest)
                    await asyncio.to_thread(self.tell, chat, "🧹 Ya lo tienes aquí en Telegram, así que lo borré de la Pi "
                                                             "para ahorrar espacio.")
                    if owner:
                        await asyncio.to_thread(self.notice, owner, f"📲 {dest.name} se te mandó por Telegram y se "
                                                                    "borró de la Pi.", "ok")
            except (TelegramError, OSError) as e:
                await asyncio.to_thread(self.tell, chat, text + f"\n⚠ No pude mandarte el archivo: {esc(e)}. "
                                                               "Sigue en la Pi: descárgalo en «Mis respaldos».")
        elif chat:
            await asyncio.to_thread(self.tell, chat, text + "\nDescárgalo cuando quieras en «Mis respaldos».")
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
        await asyncio.to_thread(self.notify, f"📦 {esc(owner)} subió {len(files)} archivo(s) "
                                             f"({human_bytes(size)}) desde el navegador", False)
        chat, prefs = info.get("chat"), info.get("prefs") or {}
        if not (chat and self.backups.forward and prefs.get("send_telegram", True)):
            await asyncio.to_thread(self.tell, chat, msg + "\nDescárgalos cuando quieras en «Mis respaldos».")
            return
        await asyncio.to_thread(self.tell, chat, msg + "\nTe los mando por aquí…")
        try:
            await asyncio.to_thread(self._send_batch, chat, d, files, device)
        except (TelegramError, OSError) as e:
            await asyncio.to_thread(self.tell, chat, f"⚠ No pude mandártelos todos: {esc(e)}. Siguen en la Pi "
                                                     "(«Mis respaldos»).")
            return
        rows = [r for r in self.store.backups_of(device) if Path(r["path"]).parent == d]
        for r in rows:
            self.store.x("UPDATE backups SET forwarded=1 WHERE id=?", (r["id"],))
        if prefs.get("delete_after"):
            for r in rows:
                await asyncio.to_thread(self.drop_backup, r["id"], Path(r["path"]))
            import shutil
            await asyncio.to_thread(shutil.rmtree, d, True)
            await asyncio.to_thread(self.tell, chat, "🧹 Ya los tienes aquí en Telegram; los borré de la Pi.")
            await asyncio.to_thread(self.notice, owner, f"📲 {len(files)} archivo(s) se te mandaron por Telegram "
                                                        "y se borraron de la Pi.", "ok")

    def _send_batch(self, chat, d: Path, files: list[Path], device: str) -> None:
        """Pocos archivos: uno por uno. Muchos (o muy grandes juntos): un .zip sin comprimir
        (las fotos y videos ya vienen comprimidos), en partes si pasa del límite."""
        if len(files) == 1:
            self.send_big(chat, files[0], f"📎 {esc(files[0].name)}")
            return
        self.send_zips(chat, files, d, device, f"🗜 {len(files)} archivos que subiste")

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

    async def i_backup_send(self, request: Request):
        """«Mis respaldos» → 📲: manda a su dueño un respaldo que ya estaba en la Pi."""
        if not self._internal_ok(request):
            return JSONResponse({"error": "no autorizado"}, 401)
        b = await request.json()
        r = self.store.q("SELECT * FROM backups WHERE id=?", (int(request.path_params["bid"]),))
        if not r or r[0]["device"] != b.get("device"):
            return JSONResponse({"error": "no existe"}, 404)
        p = Path(r[0]["path"])
        if not p.exists():
            return JSONResponse({"error": "ya se borró de la Pi"}, 404)
        chat, prefs, owner = b.get("chat"), b.get("prefs") or {}, b.get("owner", "")
        if not chat:
            return JSONResponse({"error": "primero vincula tu Telegram"}, 400)
        text = f"📦 Tu respaldo de <b>{esc(r[0]['device'])}</b> (n.º {r[0]['id']})\n{esc(p.name)} · {human_bytes(r[0]['size'] or 0)}"
        if not hasattr(self, "_send_lock"):
            self._send_lock = asyncio.Lock()

        async def one_at_a_time():          # «mandar todos»: uno tras otro, no todos juntos
            async with self._send_lock:
                await self._deliver(r[0]["id"], p, r[0]["size"] or 0, text, chat, admin_copy=False,
                                    owner=owner, prefs={**prefs, "send_telegram": True})
                await asyncio.sleep(1)
        asyncio.create_task(one_at_a_time())
        return JSONResponse({"ok": True, "parts": -(-(r[0]["size"] or 0) // self.tg.limit) or 1})

    async def i_backups_send_zip(self, request: Request):
        """«Mandarme los respaldos por Telegram»: todos (o los elegidos) en .zip."""
        if not self._internal_ok(request):
            return JSONResponse({"error": "no autorizado"}, 401)
        b = await request.json()
        device, chat, prefs, owner = b.get("device", ""), b.get("chat"), b.get("prefs") or {}, b.get("owner", "")
        if not chat:
            return JSONResponse({"error": "primero vincula tu Telegram"}, 400)
        ids = {int(i) for i in b.get("ids") or []}
        rows = [r for r in self.store.backups_of(device) if Path(r["path"]).exists() and (not ids or r["id"] in ids)]
        if not rows:
            return JSONResponse({"error": "no hay respaldos en la Pi"}, 404)
        total = sum(r["size"] or 0 for r in rows)
        if not hasattr(self, "_send_lock"):
            self._send_lock = asyncio.Lock()

        async def job():
            async with self._send_lock:
                import tempfile           # fuera de la carpeta de respaldos (la limpieza diaria no lo toca)
                work = Path(tempfile.mkdtemp(prefix="envio-", dir=self.backups.root.parent))
                try:
                    await asyncio.to_thread(self.tell, chat, f"🗜 Empacando {len(rows)} respaldo(s) de <b>{esc(device)}</b> "
                                                             f"({human_bytes(total)}) en .zip…")
                    await asyncio.to_thread(self.send_zips, chat, [Path(r["path"]) for r in rows], work, device,
                                            f"📦 Respaldos de <b>{esc(device)}</b>")
                except (TelegramError, OSError) as e:
                    await asyncio.to_thread(self.tell, chat, f"⚠ No pude mandártelos: {esc(e)}. Siguen en la Pi.")
                    return
                finally:
                    with contextlib.suppress(OSError):
                        for f in work.iterdir():
                            f.unlink()
                        work.rmdir()
                for r in rows:
                    self.store.x("UPDATE backups SET forwarded=1 WHERE id=?", (r["id"],))
                if prefs.get("delete_after"):
                    for r in rows:
                        await asyncio.to_thread(self.drop_backup, r["id"], Path(r["path"]))
                    await asyncio.to_thread(self.tell, chat, "🧹 Ya los tienes aquí en Telegram; los borré de la Pi.")
                    if owner:
                        await asyncio.to_thread(self.notice, owner, f"📲 {len(rows)} respaldo(s) de {device} se te "
                                                                    "mandaron por Telegram y se borraron de la Pi.", "ok")
        asyncio.create_task(job())
        zips = -(-total // (self.tg.limit - 32 * 1024 * 1024)) or 1
        return JSONResponse({"ok": True, "files": len(rows), "size": total, "zips": zips})

    async def i_backup_file(self, request: Request):
        if not self._internal_ok(request):
            return JSONResponse({"error": "no autorizado"}, 401)
        r = self.store.q("SELECT * FROM backups WHERE id=?", (int(request.path_params["bid"]),))
        if not r or r[0]["device"] != request.query_params.get("device"):
            return JSONResponse({"error": "no existe"}, 404)
        p = Path(r[0]["path"])
        if request.method == "DELETE":
            await asyncio.to_thread(self.drop_backup, r[0]["id"], p)
            return JSONResponse({"ok": True})
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
            Route("/internal/backup-file/{bid:int}", self.i_backup_file, methods=["GET", "DELETE"]),
            Route("/internal/backup-file/{bid:int}/send", self.i_backup_send, methods=["POST"]),
            Route("/internal/backups/send-zip", self.i_backups_send_zip, methods=["POST"]),
        ], lifespan=lifespan)
