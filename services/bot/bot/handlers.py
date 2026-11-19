"""Comandos del bot. Solo responde a los IDs de TELEGRAM_ALLOWED_IDS."""
from __future__ import annotations

import datetime as dt
import logging
import os
import re
import tempfile
import time
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

from homelab_common import http_json, human_bytes

from . import reports
from .backups import safe_name
from .social import NETWORKS
from .tg import TelegramError, esc

log = logging.getLogger("bot.cmd")

HELP = """🛡 <b>MyHomeLab</b>

<b>Estado</b>
/estado – resumen de casa, VPS y nodos
/reporte – reporte completo ahora
/vps – estado y tráfico del VPS
/dispositivos – quién está conectado
/nuevos – dispositivos sin revisar
/eventos – últimos eventos anómalos
/conexiones – quién se conectó a la Pi (24 h)
/dns – resumen de AdGuard

<b>Red</b>
/encender [nombre] – prende la PC por Wake-on-LAN
/bloquear MAC [23:00-07:00] – ACL (corta el internet en modo puente)
/acl – reglas actuales · /desbloquear ID

<b>Cámaras</b>
/camaras – lista · /foto NOMBRE – foto actual

<b>Recordatorios</b>
/recordar mañana 9:00 pagar la luz · /recordar cada mes 5 pagar el internet
/recordatorios – los tuyos · /olvidar N

<b>Respaldos</b>
/respaldos – último respaldo de cada dispositivo
Mándame cualquier archivo, foto o video y lo guardo como respaldo del teléfono.
Escribe <code>#nombre</code> en el pie para guardarlo en otro dispositivo.

<b>Redes sociales</b>
/programar FECHA HORA REDES texto – ej:
<code>/programar 2026-11-20 18:00 fb,ig,tg ¡Nueva promo!</code>
<code>/programar mañana 09:30 todas Buenos días</code>
(para foto o video: mándalo con ese texto en el pie)
/programadas · /cancelar ID · /publicar_ya ID
Redes: fb = Facebook, ig = Instagram, tg = canal de Telegram"""


MEMBER_HELP = """🛡 <b>MyHomeLab</b> · hola, {user}

/misrespaldos – tus equipos y su último respaldo
Mándame cualquier archivo, foto o video y lo guardo en tu respaldo.
/recordar mañana 9:00 pagar la luz – también <code>cada mes 5 …</code>, <code>cada año 15/03 …</code>, <code>en 2h …</code>
/recordatorios – los tuyos · /olvidar N – borrar uno
/desvincular – dejar de recibir avisos aquí
{extra}
Todo lo demás está en el dashboard → <b>Mis respaldos</b>."""
LOGS_HELP = """
<b>Puedes ver los registros del homelab</b>
/estado · /reporte · /vps · /dispositivos · /nuevos · /eventos · /conexiones · /dns · /camaras · /foto NOMBRE"""
# Comandos de solo lectura para cuentas con permiso de ver registros
READ_CMDS = {"estado", "reporte", "vps", "dispositivos", "nuevos", "eventos", "conexiones", "dns",
             "camaras", "foto", "respaldos"}


def parse_schedule(text: str, tz: ZoneInfo, now: dt.datetime | None = None):
    """'/programar 2026-11-20 18:00 fb,ig texto…' → (datetime, [redes], texto)."""
    now = now or dt.datetime.now(tz)
    body = re.sub(r"^/programar(@\w+)?\s*", "", text.strip(), flags=re.I)
    m = re.match(r"^(\S+)\s+(\d{1,2}:\d{2})\s+(\S+)\s*(.*)$", body, flags=re.S)
    if not m:
        raise ValueError("Formato: /programar AAAA-MM-DD HH:MM fb,ig,tg texto")
    day, hhmm, nets, rest = m.groups()
    d = day.lower()
    if d == "hoy":
        date = now.date()
    elif d in ("mañana", "manana"):
        date = now.date() + dt.timedelta(days=1)
    elif re.match(r"^\d{4}-\d{2}-\d{2}$", d):
        date = dt.date.fromisoformat(d)
    elif re.match(r"^\d{1,2}/\d{1,2}(/\d{4})?$", d):
        parts = [int(x) for x in d.split("/")]
        date = dt.date(parts[2] if len(parts) == 3 else now.year, parts[1], parts[0])
    else:
        raise ValueError(f"No entiendo la fecha «{day}» (usa AAAA-MM-DD, DD/MM, hoy o mañana)")
    h, mi = (int(x) for x in hhmm.split(":"))
    when = dt.datetime.combine(date, dt.time(h, mi), tzinfo=tz)
    networks = list(NETWORKS) if nets.lower() in ("todas", "all") else \
        [n.strip().lower() for n in nets.split(",") if n.strip()]
    bad = [n for n in networks if n not in NETWORKS]
    if bad:
        raise ValueError(f"Redes desconocidas: {', '.join(bad)} (usa fb, ig, tg o todas)")
    if when < now - dt.timedelta(minutes=1):
        raise ValueError("Esa fecha ya pasó")
    return when, networks, rest.strip()


class Handlers:
    def __init__(self, app):
        self.a = app   # BotApp: tg, store, backups, social, cfg, owner

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo((self.a.cfg.get("social", {}) or {}).get("timezone", "America/Mexico_City"))

    def reply(self, chat, text):
        try:
            self.a.tg.send_message(chat, text)
        except TelegramError as e:
            log.error("No pude responder: %s", e)

    # ------------------------------------------------------------ entrada
    def handle(self, upd: dict) -> None:
        msg = upd.get("message")
        if not msg:
            return
        user = msg.get("from", {})
        chat = msg["chat"]["id"]
        if user.get("id") not in self.a.allowed:
            return self.handle_member(chat, user, msg)
        text = msg.get("text") or msg.get("caption") or ""
        if text.startswith("/programar"):
            return self.cmd_programar(chat, text, msg)
        if text.lower().startswith("/recordar") and not text.lower().startswith("/recordatorios"):
            return self.cmd_recordar(chat, text)
        if any(k in msg for k in ("document", "photo", "video", "audio", "voice")):
            return self.save_phone_file(chat, msg, text)
        if not text.startswith("/"):
            return self.reply(chat, "Escribe /ayuda para ver lo que puedo hacer.")
        cmd, _, arg = text.partition(" ")
        cmd = cmd.split("@")[0].lower()
        fn = getattr(self, "cmd_" + cmd[1:], None)
        if fn is None:
            return self.reply(chat, "No conozco ese comando. /ayuda")
        try:
            fn(chat, arg.strip())
        except Exception as e:  # noqa: BLE001
            log.exception("Error en %s", cmd)
            self.reply(chat, f"⚠ Error: {esc(e)}")

    # ---------------------------------------------- familia (cuentas vinculadas)
    def handle_member(self, chat, user: dict, msg: dict) -> None:
        text = (msg.get("text") or msg.get("caption") or "").strip()
        cmd, _, arg = text.partition(" ")
        cmd = cmd.split("@")[0].lower()
        if cmd in ("/vincular", "/start") and re.search(r"\d{6}", arg or ""):
            return self.link(chat, user, arg)
        who = self.a.who(chat)
        if not who:
            if cmd == "/start":
                return self.reply(chat, "Hola 👋 Este es el bot de un homelab privado. Si tienes cuenta, entra "
                                        "al dashboard → <b>Mis respaldos</b> → «Vincular Telegram» y mándame "
                                        "<code>/vincular CÓDIGO</code>.")
            if cmd == "/vincular":
                return self.reply(chat, "Escribe el código de 6 números: <code>/vincular 123456</code>")
            return self.a.unauthorized(user, msg)
        name, role = who["user"], who["role"]
        if any(k in msg for k in ("document", "photo", "video", "audio", "voice")):
            return self.save_member_file(chat, name, msg)
        if cmd in ("/start", "/ayuda", "/help") or not cmd.startswith("/"):
            return self.reply(chat, MEMBER_HELP.format(user=esc(name),
                                                       extra=LOGS_HELP if role in ("admin", "registros") else ""))
        if cmd == "/misrespaldos":
            return self.my_backups(chat, name)
        if cmd in ("/recordar", "/recordatorios", "/olvidar"):
            return getattr(self, "cmd_" + cmd[1:])(chat, text if cmd == "/recordar" else arg.strip())
        if cmd == "/desvincular":
            try:
                self.a.nw("/internal/telegram/unlink", {"chat_id": chat})
            except RuntimeError as e:
                return self.reply(chat, f"⚠ {esc(e)}")
            return self.reply(chat, "Listo, ya no te llegarán avisos aquí. Puedes volver a vincularte desde el dashboard.")
        if role in ("admin", "registros") and cmd[1:] in READ_CMDS:
            fn = getattr(self, "cmd_" + cmd[1:])
            try:
                return fn(chat, arg.strip())
            except Exception as e:  # noqa: BLE001
                log.exception("Error en %s", cmd)
                return self.reply(chat, f"⚠ Error: {esc(e)}")
        return self.reply(chat, "Ese comando no está disponible para tu cuenta. /ayuda")

    def link(self, chat, user: dict, arg: str) -> None:
        key = f"link:{chat}"
        tries = [t for t in self.a.store.get(key, []) if t > time.time() - 3600]
        if len(tries) >= 5:
            return self.reply(chat, "Demasiados intentos. Espera una hora.")
        try:
            r = self.a.nw("/internal/telegram/link", {"code": re.search(r"\d{6}", arg).group(0), "chat_id": chat,
                                                      "name": (user.get("first_name") or "")
                                                      + (f" @{user['username']}" if user.get("username") else "")})
        except RuntimeError as e:
            return self.reply(chat, f"⚠ No pude vincularte: {esc(e)}")
        if not r.get("ok"):
            self.a.store.set(key, tries + [time.time()])
            return self.reply(chat, f"❌ {esc(r.get('error', 'Código incorrecto'))}. Genera otro en el dashboard.")
        self.a._rcpt = ([], 0.0)                  # recalcular quién recibe alertas
        role = r.get("role", "familia")
        self.reply(chat, f"✅ Listo, <b>{esc(r['user'])}</b>. Aquí te llegarán los avisos de tus respaldos"
                         + (" y las alertas del homelab." if role in ("admin", "registros") else ".")
                         + "\n\n" + MEMBER_HELP.format(user=esc(r["user"]),
                                                        extra=LOGS_HELP if role in ("admin", "registros") else ""))

    def save_member_file(self, chat, name: str, msg: dict) -> None:
        try:
            device = self.a.nw("/internal/telegram-device", {"user": name})["device"]
        except (RuntimeError, KeyError, TypeError) as e:
            return self.reply(chat, f"⚠ No pude guardar: {esc(e)}")
        if "photo" in msg:
            f = msg["photo"][-1]
            fname = f"foto_{dt.datetime.fromtimestamp(msg['date']).strftime('%Y%m%d_%H%M%S')}.jpg"
        else:
            kind = next(k for k in ("document", "video", "audio", "voice") if k in msg)
            f = msg[kind]
            fname = f.get("file_name") or f"{kind}_{msg['date']}.{f.get('mime_type', 'x/bin').split('/')[-1]}"
        size = f.get("file_size", 0)
        if size > self.a.tg.limit:
            return self.reply(chat, f"Ese archivo pesa {human_bytes(size)}; mi límite es {human_bytes(self.a.tg.limit)}. "
                                    "Súbelo desde el dashboard → Mis respaldos.")
        dest = self.a.backups.root / safe_name(device) / dt.date.today().isoformat() / safe_name(fname)
        if dest.exists():
            dest = dest.with_name(f"{dest.stem}_{int(time.time())}{dest.suffix}")
        self.a.tg.download(f["file_id"], dest)
        n = dest.stat().st_size
        self.a.store.add_backup(device, "telegram", str(dest), n, "telegram")
        self.a.notice(name, f"✅ Guardé {dest.name} ({human_bytes(n)}) que mandaste por Telegram.", "ok")
        self.reply(chat, f"💾 Guardado en tu respaldo: {esc(dest.name)} ({human_bytes(n)})")

    def my_backups(self, chat, name: str) -> None:
        try:
            devs = self.a.nw(f"/internal/devices-of?user={name}").get("devices", [])
        except RuntimeError as e:
            return self.reply(chat, f"⚠ {esc(e)}")
        if not devs:
            return self.reply(chat, "Aún no tienes equipos. Agrégalos en el dashboard → Mis respaldos.")
        lines = ["📦 <b>Tus respaldos</b>"]
        for d in devs:
            rows = [r for r in self.a.store.backups_of(d) if Path(r["path"]).exists()]
            last = reports.ago(rows[0]["ts"]) if rows else "nunca"
            lines.append(f"  {esc(d)}: {last}" + (f" · {len(rows)} archivo(s), "
                                                   f"{human_bytes(sum(r['size'] or 0 for r in rows))}" if rows else ""))
        self.reply(chat, "\n".join(lines))

    # ------------------------------------------------------------- estado
    def cmd_start(self, chat, _):
        self.reply(chat, HELP)

    cmd_ayuda = cmd_help = cmd_start

    def cmd_estado(self, chat, _):
        self.reply(chat, reports.home_block() + "\n" + reports.vps_block() + "\n" +
                   reports.nodes_block() + "\n" + reports.alerts_block())

    def cmd_reporte(self, chat, _):
        self.reply(chat, reports.daily_report(self.a.cfg, self.a.backups))

    def cmd_vps(self, chat, _):
        self.reply(chat, reports.vps_block())

    def _devices(self):
        return reports._netwatch("/api/devices")["devices"]

    def cmd_dispositivos(self, chat, _):
        devs = [d for d in self._devices() if d["online"]]
        by_ap: dict[str, list] = {}
        for d in devs:
            by_ap.setdefault(d.get("ap") or "Sin asignar", []).append(d)
        lines = [f"📶 <b>{len(devs)} en línea</b>"]
        for ap, ds in sorted(by_ap.items()):
            lines.append(f"\n<b>{esc(ap)}</b>")
            for d in sorted(ds, key=lambda x: x["display_name"].lower()):
                lines.append(f"{d['icon']} {esc(d['display_name'])} · <code>{d['ip']}</code>"
                             + (" 🆕" if not d["known"] else ""))
        self.reply(chat, "\n".join(lines))

    def cmd_nuevos(self, chat, _):
        devs = [d for d in self._devices() if not d["known"]]
        if not devs:
            return self.reply(chat, "✅ No hay dispositivos sin revisar.")
        self.reply(chat, "🆕 <b>Sin revisar</b> (márcalos en la web → Dispositivos)\n" + "\n".join(
            f"{d['icon']} {esc(d['display_name'])} · <code>{d['ip']}</code> · <code>{d['mac']}</code>"
            f" · {esc(d.get('vendor') or '?')} · {reports.ago(d['first_seen'])}" for d in devs))

    def cmd_eventos(self, chat, _):
        from .formatting import netwatch_events
        evs = reports._netwatch("/api/events?limit=10")["events"]
        self.reply(chat, netwatch_events(evs) or "Sin eventos. Todo tranquilo. ✅")

    def cmd_conexiones(self, chat, _):
        j = reports._netwatch("/api/inbound?hours=24")
        if not j["ips"]:
            return self.reply(chat, "Sin conexiones entrantes registradas en 24 h.")
        icon = {"permitida": "✅", "bloqueada": "⛔", "honeypot": "🍯"}
        lines = [f"🔌 <b>Conexiones entrantes (24 h)</b> · {len(j['ips'])} IPs"]
        for r in j["ips"][:15]:
            acts = " ".join(f"{icon.get(a, a)}{n}" for a, n in r["actions"].items())
            svcs = ", ".join(sorted(set(r["services"].values())))
            lines.append(f"<code>{r['ip']}</code> {esc(r['name'] or '')} · {esc(r['origin'])} · {esc(svcs)} · {acts}")
        self.reply(chat, "\n".join(lines))

    def cmd_dns(self, chat, _):
        d = reports._netwatch("/api/dns")
        if not d.get("enabled"):
            return self.reply(chat, "No encuentro AdGuard. ¿Está levantado (perfil «dns» en Ajustes)? "
                                    "Si usa otra dirección, ponla en Ajustes → AdGuard.")
        if d.get("error"):
            return self.reply(chat, f"⚠ {esc(d['error'])}")
        self.reply(chat, f"🛡 <b>DNS</b>: {d['queries']} consultas · {d['blocked']} bloqueadas · "
                         f"{d['malware']} malware/phishing\n<b>Más consultas</b>\n" + "\n".join(
                             f"  {esc(c['name'] or c['ip'])}: {c['n']}" for c in d["top_clients"][:8])
                   + ("\n<b>Más bloqueados</b>\n" + "\n".join(f"  {esc(b['domain'])}: {b['n']}"
                                                              for b in d["top_blocked"][:5]) if d["top_blocked"] else "")
                   + ("\n🧭 Sale por: " + ", ".join(esc(u) for u in d.get("upstream") or []) if d.get("upstream") else ""))

    # ---------------------------------------------------------------- ACL
    def cmd_bloquear(self, chat, arg):
        m = re.match(r"^([0-9a-fA-F:]{17})(?:\s+(\d{2}:\d{2})-(\d{2}:\d{2}))?$", arg)
        if not m:
            return self.reply(chat, "Uso: /bloquear aa:bb:cc:dd:ee:ff [23:00-07:00]")
        base = os.environ.get("NETWATCH_URL", "http://host.docker.internal:8000").rstrip("/")
        r = http_json(f"{base}/api/acl", "POST", {"mac": m.group(1), "start": m.group(2) or "",
                                                 "end": m.group(3) or "", "note": "desde Telegram"})
        self.reply(chat, f"⛔ Regla #{r['id']} creada para <code>{m.group(1).lower()}</code>"
                   + (f" de {m.group(2)} a {m.group(3)}" if m.group(2) else " (siempre)"))

    def cmd_encender(self, chat, arg):
        base = os.environ.get("NETWATCH_URL", "http://host.docker.internal:8000").rstrip("/")
        targets = http_json(f"{base}/api/wol").get("targets", [])
        if not targets:
            return self.reply(chat, "No hay equipos para encender. Agrégalos en homelab.yml → wake_on_lan "
                                    "(add-server.sh pone la PC sola).")
        if not arg and len(targets) > 1:
            return self.reply(chat, "¿Cuál? " + " · ".join(f"/encender {esc(t['name'])}" for t in targets))
        t = targets[0] if not arg else next((t for t in targets if t["name"].lower() == arg.lower()), None)
        if not t:
            return self.reply(chat, "No conozco ese equipo. Opciones: " + ", ".join(esc(t["name"]) for t in targets))
        if t.get("online"):
            return self.reply(chat, f"✅ {esc(t['name'])} ya está prendido ({esc(t.get('ip') or '')}).")
        http_json(f"{base}/api/wol", "POST", {"mac": t["mac"]})
        self.reply(chat, f"⏻ Señal de encendido enviada a <b>{esc(t['name'])}</b>. En 1–2 minutos debería aparecer "
                         "en línea (/dispositivos).")

    def cmd_acl(self, chat, _):
        j = reports._netwatch("/api/acl")
        if not j["rules"]:
            return self.reply(chat, "Sin reglas de bloqueo.")
        self.reply(chat, ("" if j["enforced"] else "⚠ Sin modo puente: solo aviso, no corta.\n") + "\n".join(
            f"#{r['id']} {'⛔' if r['active_now'] else '⏸'} {esc(r['name'] or r['mac'])} "
            f"<code>{r['mac']}</code> {r['start'] + '-' + r['end'] if r['start'] else 'siempre'}"
            for r in j["rules"]))

    def cmd_desbloquear(self, chat, arg):
        if not arg.isdigit():
            return self.reply(chat, "Uso: /desbloquear ID  (ve los ID con /acl)")
        base = os.environ.get("NETWATCH_URL", "http://host.docker.internal:8000").rstrip("/")
        http_json(f"{base}/api/acl/{arg}", "DELETE")
        self.reply(chat, f"✅ Regla #{arg} eliminada.")

    # ------------------------------------------------------------ cámaras
    def _frigate(self) -> str:
        url = (self.a.cfg.get("cameras", {}) or {}).get("frigate_url", "")
        if not url:
            raise RuntimeError("Configura cameras.frigate_url en homelab.yml")
        return url.rstrip("/")

    def cmd_camaras(self, chat, _):
        cams = list((http_json(f"{self._frigate()}/api/config", timeout=8) or {}).get("cameras", {}))
        self.reply(chat, "📷 " + (", ".join(f"<code>{esc(c)}</code>" for c in cams) or "ninguna")
                   + "\nUsa /foto NOMBRE")

    def cmd_foto(self, chat, arg):
        if not re.match(r"^[\w-]+$", arg or ""):
            return self.reply(chat, "Uso: /foto NOMBRE  (ve los nombres con /camaras)")
        with tempfile.NamedTemporaryFile(suffix=".jpg") as f:
            with urllib.request.urlopen(f"{self._frigate()}/api/{arg}/latest.jpg", timeout=10) as r:
                f.write(r.read())
            f.flush()
            self.a.tg.send_file(chat, Path(f.name), "sendPhoto", "photo",
                                f"📷 {arg} · {time.strftime('%H:%M:%S')}")

    # ---------------------------------------------------------- respaldos
    def cmd_respaldos(self, chat, _):
        self.reply(chat, reports.backups_block(self.a.backups)
                   + f"\nGuardados en la Pi: <code>{self.a.backups.root}</code>")

    def save_phone_file(self, chat, msg, caption: str):
        """Lo que le mandes al bot se guarda como respaldo (del teléfono por defecto)."""
        device = "telefono"
        m = re.search(r"#([\w-]+)", caption or "")
        if m and m.group(1) in self.a.backups.devices:
            device = m.group(1)
        if "photo" in msg:
            f = msg["photo"][-1]
            name = f"foto_{dt.datetime.fromtimestamp(msg['date']).strftime('%Y%m%d_%H%M%S')}.jpg"
        else:
            kind = next(k for k in ("document", "video", "audio", "voice") if k in msg)
            f = msg[kind]
            name = f.get("file_name") or f"{kind}_{msg['date']}.{f.get('mime_type', 'x/bin').split('/')[-1]}"
        size = f.get("file_size", 0)
        if size > self.a.tg.limit:
            return self.reply(chat, f"Ese archivo pesa {human_bytes(size)}; mi límite es "
                                    f"{human_bytes(self.a.tg.limit)}.")
        dest = self.a.backups.root / safe_name(device) / dt.date.today().isoformat() / safe_name(name)
        if dest.exists():
            dest = dest.with_name(f"{dest.stem}_{int(time.time())}{dest.suffix}")
        self.a.tg.download(f["file_id"], dest)
        self.a.store.add_backup(device, self.a.backups.devices.get(device, {}).get("os", "telefono"),
                                str(dest), dest.stat().st_size, "telegram")
        self.reply(chat, f"💾 Guardado en <b>{esc(device)}</b>: {esc(dest.name)} "
                         f"({human_bytes(dest.stat().st_size)})")

    # ------------------------------------------------------ redes sociales
    def cmd_programar(self, chat, text, msg):
        try:
            when, nets, body = parse_schedule(text, self.tz)
        except ValueError as e:
            return self.reply(chat, f"⚠ {esc(e)}\n\n" + HELP.split("<b>Redes sociales</b>")[1])
        media, kind = "", ""
        if "photo" in msg or "video" in msg:
            kind = "photo" if "photo" in msg else "video"
            f = msg["photo"][-1] if kind == "photo" else msg["video"]
            d = self.a.data / "social"
            media = str(self.a.tg.download(
                f["file_id"], d / f"{int(time.time())}_{f['file_unique_id']}.{'jpg' if kind == 'photo' else 'mp4'}"))
        if "ig" in nets and kind != "photo":
            return self.reply(chat, "⚠ Instagram necesita una foto: mándala con el texto en el pie.")
        if not body and not media:
            return self.reply(chat, "⚠ Falta el texto de la publicación.")
        missing = [NETWORKS[n] for n in nets if not self.a.social.available().get(n)]
        pid = self.a.store.add_post(when.timestamp(), nets, body, media, kind)
        self.reply(chat, f"🗓 Publicación #{pid} programada para <b>{when.strftime('%d/%m/%Y %H:%M')}</b> "
                         f"en {', '.join(NETWORKS[n] for n in nets)}"
                         + (" con " + ("foto" if kind == "photo" else "video") if kind else "")
                         + (f"\n⚠ Falta configurar: {', '.join(missing)}" if missing else ""))

    def cmd_programadas(self, chat, _):
        posts = self.a.store.pending_posts()
        if not posts:
            return self.reply(chat, "No hay publicaciones programadas.")
        self.reply(chat, "🗓 <b>Programadas</b>\n" + "\n".join(
            f"#{p['id']} · {dt.datetime.fromtimestamp(p['run_at'], self.tz).strftime('%d/%m %H:%M')} · "
            f"{','.join(p['networks'])}{' 🖼' if p['media'] else ''} · {esc(p['text'][:60])}"
            for p in posts))

    def cmd_cancelar(self, chat, arg):
        p = self.a.store.post(int(arg)) if arg.isdigit() else None
        if not p or p["status"] != "pending":
            return self.reply(chat, "No encontré esa publicación pendiente.")
        self.a.store.finish_post(p["id"], "cancelled", "")
        if p["media"]:
            Path(p["media"]).unlink(missing_ok=True)
            self.a.store.x("UPDATE posts SET media='' WHERE id=?", (p["id"],))
        self.reply(chat, f"🗑 Publicación #{p['id']} cancelada.")

    def cmd_publicar_ya(self, chat, arg):
        p = self.a.store.post(int(arg)) if arg.isdigit() else None
        if not p or p["status"] != "pending":
            return self.reply(chat, "No encontré esa publicación pendiente.")
        self.a.store.x("UPDATE posts SET run_at=? WHERE id=?", (time.time() - 1, p["id"]))
        self.reply(chat, f"⏩ La publico en menos de un minuto (#{p['id']}).")

    # ------------------------------------------------------- recordatorios
    def cmd_recordar(self, chat, text):
        from .reminders import describe, parse
        try:
            when, rep, hhmm, what = parse(text if text.startswith("/") else "/recordar " + text, self.tz)
        except ValueError as e:
            return self.reply(chat, f"⚠ {e}")
        rid = self.a.reminders.add(chat, when, rep, hhmm, what)
        extra = f" y luego {describe(rep, hhmm)}" if rep else ""
        self.reply(chat, f"⏰ Listo (#{rid}): te aviso el {when.strftime('%d/%m/%Y a las %H:%M')}{extra}.\n"
                         f"«{esc(what)}»")

    def cmd_recordatorios(self, chat, _):
        from .reminders import describe
        rs = self.a.reminders.of(chat)
        if not rs:
            return self.reply(chat, "No tienes recordatorios. Ej.: <code>/recordar mañana 9:00 pagar la luz</code>")
        lines = []
        for r in rs:
            when = dt.datetime.fromtimestamp(r["run_at"], self.tz).strftime("%d/%m %H:%M")
            rep = f" <i>({describe(r['rep'], r['hhmm'])})</i>" if r["rep"] else ""
            lines.append(f"#{r['id']} · {when} · {esc(r['text'])}{rep}")
        self.reply(chat, "⏰ <b>Tus recordatorios</b>\n" + "\n".join(lines) + "\n\nBorra uno con /olvidar N")

    def cmd_olvidar(self, chat, arg):
        m = re.search(r"\d+", arg or "")
        if not m:
            return self.reply(chat, "¿Cuál? <code>/olvidar 3</code> (el número sale en /recordatorios)")
        ok = self.a.reminders.delete(chat, int(m.group(0)))
        self.reply(chat, "🗑 Borrado." if ok else "No encontré ese recordatorio entre los tuyos.")
