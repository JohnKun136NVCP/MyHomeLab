"""Respaldos de cada persona de la casa (dashboard → Mis respaldos).

- Cada quien agrega sus equipos con un clic y recibe un instalador ya configurado:
  Windows (.cmd de doble clic), Linux y macOS (un comando), o sube desde el navegador
  (Android, iPhone, cualquier equipo) sin instalar nada.
- Los archivos los recibe el bot (/api/backup/<equipo>/…); el bot le pregunta aquí de quién
  es cada equipo (/internal/backup-auth) y le avisa SOLO a su dueño: por Telegram si lo
  vinculó, y siempre en «Mis respaldos».
- /internal/* es para el bot (INTERNAL_TOKEN); Caddy no lo deja pasar desde fuera.
"""
from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.request
from pathlib import Path

from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse
from starlette.routing import Route

from . import auth

OS_NAMES = {"windows": "Windows", "linux": "Linux", "macos": "Mac", "android": "Android (navegador)",
            "web": "Navegador", "telegram": "Telegram"}
SETUP_TTL = 7 * 86400          # el enlace del instalador sirve 7 días
CLIENTS = Path(os.environ.get("HOMELAB_CLIENTS", "/homelab/clients"))
# Carpetas por defecto (simples: lo que la familia sí necesita)
FOLDERS = {
    "windows": ["::MyDocuments", "::Desktop", "::MyPictures"],      # carpetas reales (también OneDrive)
    "unix": ["DOCUMENTS", "DESKTOP", "PICTURES"],     # se traducen con xdg-user-dir / macOS
}


def slug(s: str) -> str:
    s = s.lower().strip()
    for a, b in zip("áéíóúñü", "aeiounu"):
        s = s.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:30]


def internal_ok(request: Request) -> bool:
    tok = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    exp = os.environ.get("INTERNAL_TOKEN", "")
    return bool(exp and tok and hmac.compare_digest(tok, exp))


def bot_get(path: str, timeout: float = 8):
    base = os.environ.get("BOT_URL", "http://127.0.0.1:8080").rstrip("/")
    req = urllib.request.Request(base + path, headers={
        "Authorization": f"Bearer {os.environ.get('INTERNAL_TOKEN', '')}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


_bot_name = {"v": "", "ts": 0.0}


def bot_username() -> str:
    """@usuario del bot (se lo pregunta al bot; cacheado 1 h)."""
    if os.environ.get("TELEGRAM_BOT_USERNAME"):
        return os.environ["TELEGRAM_BOT_USERNAME"].lstrip("@")
    if time.time() - _bot_name["ts"] > 3600 or not _bot_name["v"]:
        try:
            _bot_name["v"] = bot_get("/internal/info", 5).get("username") or ""
        except (urllib.error.URLError, OSError, ValueError):
            pass
        _bot_name["ts"] = time.time()
    return _bot_name["v"]


def public_base(request: Request) -> str:
    """https://IP:PUERTO tal como el navegador abrió el dashboard."""
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    if not host:
        h, p = os.environ.get("HOMELAB_HOST", ""), os.environ.get("HTTPS_PORT", "443")
        host = h if p == "443" else f"{h}:{p}"
    return f"https://{host}"


def routes(db, mon) -> list[Route]:
    def me(request: Request) -> dict | None:
        return auth.from_request(request)

    def notice(user: str, text: str, level: str = "info") -> None:
        db.x("INSERT INTO notices (username, ts, text, level) VALUES (?,?,?,?)", (user, time.time(), text, level))
        db.x("DELETE FROM notices WHERE username=? AND id NOT IN (SELECT id FROM notices WHERE username=? "
             "ORDER BY ts DESC LIMIT 100)", (user, user))

    def new_device(owner: str, os_: str, label: str = "") -> dict:
        base = f"{slug(owner)}-{slug(label) or os_}"
        name, n = base, 2
        while db.q("SELECT 1 FROM backup_devices WHERE name=?", (name,)) or \
                name in {d.get("name") for d in (mon.cfg.get("backups", {}) or {}).get("devices") or []}:
            name, n = f"{base}-{n}", n + 1
        d = {"name": name, "owner": owner, "os": os_, "label": label or OS_NAMES.get(os_, os_),
             "token": secrets.token_urlsafe(30), "clave": secrets.token_urlsafe(12),
             "created": time.time(), "setup_code": secrets.token_urlsafe(24),
             "setup_expires": time.time() + SETUP_TTL}
        db.x("INSERT INTO backup_devices (name, owner, os, label, token, clave, created, setup_code, setup_expires) "
             "VALUES (:name,:owner,:os,:label,:token,:clave,:created,:setup_code,:setup_expires)", d)
        return d

    def prefs(user: str) -> dict:
        """Por defecto: se lo manda por Telegram si lo vinculó y, ya que lo tiene, lo borra de la Pi.
        Al admin principal no se le borra nada si no lo pide."""
        acc = auth.account(user)
        r = db.q("SELECT send_telegram, delete_after FROM backup_prefs WHERE username=?", (user,))
        r = r[0] if r else {"send_telegram": 1, "delete_after": -1}
        da = r["delete_after"]
        if da is None or da < 0:
            da = 0 if (acc and acc["main"]) else 1
        return {"send_telegram": bool(r["send_telegram"]), "delete_after": bool(da)}

    def bot_delete(device: str, fid: int) -> bool:
        base = os.environ.get("BOT_URL", "http://127.0.0.1:8080").rstrip("/")
        req = urllib.request.Request(f"{base}/internal/backup-file/{fid}?device={device}", method="DELETE",
                                     headers={"Authorization": f"Bearer {os.environ.get('INTERNAL_TOKEN', '')}"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.status == 200
        except (urllib.error.URLError, OSError):
            return False

    def ensure_device(owner: str, os_: str) -> dict:
        r = db.q("SELECT * FROM backup_devices WHERE owner=? AND os=?", (owner, os_))
        return r[0] if r else new_device(owner, os_, OS_NAMES[os_])

    def stats(names: list[str]) -> dict:
        if not names:
            return {}
        try:
            return bot_get("/internal/backups?devices=" + ",".join(names)).get("devices", {})
        except (urllib.error.URLError, OSError, ValueError):
            return {}

    def setup_info(request: Request, d: dict) -> dict:
        base = public_base(request)
        url = f"{base}/setup/{d['setup_code']}"
        return {"code": d["setup_code"], "expires": d["setup_expires"],
                "windows": f"{url}/Respaldo-{d['name']}.cmd",
                "unix": f"curl -fsSk {url}/instalar.sh | bash",
                "web": "Mis respaldos → Subir archivos"}

    # ---------------------------------------------------------- de cada usuario
    async def my_backups(request: Request):
        s = me(request)
        if not s:
            return JSONResponse({"error": "Sesión caducada"}, 401)
        devs = db.q("SELECT name, os, label, clave, created, setup_code, setup_expires FROM backup_devices "
                    "WHERE owner=? ORDER BY created", (s["user"],))
        if s["main"]:                         # los equipos de siempre (.env) son del admin principal
            for d in (mon.cfg.get("backups", {}) or {}).get("devices") or []:
                devs.append({"name": d["name"], "os": d.get("os", ""), "label": "configurado en homelab.yml",
                             "legacy": True})
        import asyncio
        st = await asyncio.to_thread(stats, [d["name"] for d in devs])
        for d in devs:
            d.update(st.get(d["name"]) or {"last": None, "count": 0, "size": 0, "files": []})
            if d.get("setup_code") and d.get("setup_expires", 0) > time.time():
                d["setup"] = setup_info(request, d)
            d.pop("setup_code", None)
        link = db.q("SELECT name, linked FROM telegram_links WHERE username=?", (s["user"],))
        nots = db.q("SELECT id, ts, text, level, seen FROM notices WHERE username=? ORDER BY ts DESC LIMIT 30",
                    (s["user"],))
        return JSONResponse({"user": s["user"], "role": s["role"], "devices": devs, "notices": nots,
                             "telegram": link[0] if link else None, "prefs": prefs(s["user"]),
                             "bot": await asyncio.to_thread(bot_username),
                             "max_age_days": int((mon.cfg.get("backups", {}) or {}).get("max_age_days", 14) or 0)})

    async def my_device_add(request: Request):
        s = me(request)
        if not s:
            return JSONResponse({"error": "Sesión caducada"}, 401)
        b = await request.json()
        os_ = b.get("os")
        if os_ not in ("windows", "linux", "macos"):
            return JSONResponse({"error": "Elige Windows, Linux o Mac"}, 400)
        if len(db.q("SELECT 1 FROM backup_devices WHERE owner=?", (s["user"],))) >= 10:
            return JSONResponse({"error": "Máximo 10 equipos por persona"}, 400)
        d = new_device(s["user"], os_, str(b.get("label", ""))[:30])
        notice(s["user"], f"Agregaste el equipo «{d['label']}» ({OS_NAMES[os_]}). Instálalo con el enlace de Mis respaldos.")
        return JSONResponse({"ok": True, "device": d["name"], "setup": setup_info(request, d), "clave": d["clave"]})

    def own_device(s: dict, name: str) -> dict | None:
        r = db.q("SELECT * FROM backup_devices WHERE name=?", (name,))
        if r and (r[0]["owner"] == s["user"] or s["role"] == "admin"):
            return r[0]
        return None

    async def my_device_setup(request: Request):
        s = me(request)
        d = own_device(s, request.path_params["name"]) if s else None
        if not d:
            return JSONResponse({"error": "No existe"}, 404)
        code, exp = secrets.token_urlsafe(24), time.time() + SETUP_TTL
        db.x("UPDATE backup_devices SET setup_code=?, setup_expires=? WHERE name=?", (code, exp, d["name"]))
        return JSONResponse({"ok": True, "setup": setup_info(request, {**d, "setup_code": code, "setup_expires": exp})})

    async def my_device_delete(request: Request):
        s = me(request)
        d = own_device(s, request.path_params["name"]) if s else None
        if not d:
            return JSONResponse({"error": "No existe"}, 404)
        db.x("DELETE FROM backup_devices WHERE name=?", (d["name"],))
        return JSONResponse({"ok": True, "note": "El equipo ya no puede subir respaldos. Los que ya están en la Pi "
                                                 "se borran solos con la limpieza de siempre."})

    async def my_web_token(request: Request):
        s = me(request)
        if not s:
            return JSONResponse({"error": "Sesión caducada"}, 401)
        d = ensure_device(s["user"], "web")
        return JSONResponse({"device": d["name"], "token": d["token"]})

    async def my_prefs(request: Request):
        s = me(request)
        if not s:
            return JSONResponse({"error": "Sesión caducada"}, 401)
        b = await request.json()
        cur = prefs(s["user"])
        st_ = int(bool(b.get("send_telegram", cur["send_telegram"])))
        da = int(bool(b.get("delete_after", cur["delete_after"])))
        db.x("INSERT INTO backup_prefs (username, send_telegram, delete_after) VALUES (?,?,?) "
             "ON CONFLICT(username) DO UPDATE SET send_telegram=excluded.send_telegram, delete_after=excluded.delete_after",
             (s["user"], st_, da))
        return JSONResponse({"ok": True, "prefs": prefs(s["user"])})

    async def my_file_send(request: Request):
        s = me(request)
        d = own_device(s, request.path_params["name"]) if s else None
        if not d:
            return JSONResponse({"error": "No existe"}, 404)
        link = db.q("SELECT chat_id FROM telegram_links WHERE username=?", (s["user"],))
        if not link:
            return JSONResponse({"error": "Primero vincula tu Telegram (abajo en esta página)"}, 400)
        base = os.environ.get("BOT_URL", "http://127.0.0.1:8080").rstrip("/")
        body = json.dumps({"device": d["name"], "chat": link[0]["chat_id"], "owner": s["user"],
                           "prefs": prefs(s["user"])}).encode()
        req = urllib.request.Request(f"{base}/internal/backup-file/{int(request.path_params['fid'])}/send",
                                     data=body, method="POST",
                                     headers={"Authorization": f"Bearer {os.environ.get('INTERNAL_TOKEN', '')}",
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return JSONResponse(json.loads(r.read() or b"{}"))
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read()).get("error", "")
            except ValueError:
                msg = ""
            return JSONResponse({"error": msg or f"El bot respondió {e.code}"}, e.code)
        except (urllib.error.URLError, OSError) as e:
            return JSONResponse({"error": f"El bot no responde: {e}"}, 502)

    async def my_file_delete(request: Request):
        s = me(request)
        d = own_device(s, request.path_params["name"]) if s else None
        if not d:
            return JSONResponse({"error": "No existe"}, 404)
        ok = bot_delete(d["name"], int(request.path_params["fid"]))
        return JSONResponse({"ok": ok} if ok else {"error": "No pude borrarlo (¿el bot está prendido?)"}, 200 if ok else 502)

    async def my_download(request: Request):
        """Descarga uno de SUS respaldos (para restaurar). Si pidió «borrar de la Pi cuando ya lo tenga»
        y no se lo manda Telegram, se borra al terminar la descarga completa."""
        s = me(request)
        d = own_device(s, request.path_params["name"]) if s else None
        if not d:
            return JSONResponse({"error": "No existe"}, 404)
        fid = int(request.path_params["fid"])
        base = os.environ.get("BOT_URL", "http://127.0.0.1:8080").rstrip("/")
        req = urllib.request.Request(f"{base}/internal/backup-file/{fid}?device={d['name']}",
                                     headers={"Authorization": f"Bearer {os.environ.get('INTERNAL_TOKEN', '')}"})
        try:
            r = urllib.request.urlopen(req, timeout=30)        # noqa: SIM115 (se cierra al terminar)
        except urllib.error.HTTPError as e:
            return JSONResponse({"error": f"El bot respondió {e.code}"}, e.code)
        except (urllib.error.URLError, OSError) as e:
            return JSONResponse({"error": f"El bot no responde: {e}"}, 502)

        total = int(r.headers.get("Content-Length") or 0)
        p = prefs(s["user"])
        tg = db.q("SELECT 1 FROM telegram_links WHERE username=?", (s["user"],))
        drop = p["delete_after"] and not (tg and p["send_telegram"])

        def chunks():
            got = 0
            with r:
                while True:
                    b = r.read(1024 * 256)
                    if not b:
                        break
                    got += len(b)
                    yield b
            if drop and total and got == total:          # descarga completa: ya lo tiene
                if bot_delete(d["name"], fid):
                    notice(s["user"], "⬇ Descargaste tu respaldo y se borró de la Pi para ahorrar espacio.", "ok")
        hdr = {"Content-Disposition": r.headers.get("Content-Disposition", "attachment")}
        if r.headers.get("Content-Length"):
            hdr["Content-Length"] = r.headers["Content-Length"]
        return StreamingResponse(chunks(), media_type="application/octet-stream", headers=hdr)

    async def my_telegram_code(request: Request):
        s = me(request)
        if not s:
            return JSONResponse({"error": "Sesión caducada"}, 401)
        code = f"{secrets.randbelow(10**6):06d}"
        db.x("DELETE FROM link_codes WHERE username=? OR expires<?", (s["user"], time.time()))
        db.x("INSERT INTO link_codes (code, username, expires) VALUES (?,?,?)", (code, s["user"], time.time() + 900))
        import asyncio
        return JSONResponse({"code": code, "bot": await asyncio.to_thread(bot_username), "minutes": 15})

    async def my_telegram_unlink(request: Request):
        s = me(request)
        if not s:
            return JSONResponse({"error": "Sesión caducada"}, 401)
        db.x("DELETE FROM telegram_links WHERE username=?", (s["user"],))
        return JSONResponse({"ok": True})

    async def my_notices_seen(request: Request):
        s = me(request)
        if s:
            db.x("UPDATE notices SET seen=1 WHERE username=?", (s["user"],))
        return JSONResponse({"ok": True})

    # ------------------------------------------------- instalador (sin sesión)
    def setup_device(code: str) -> dict | None:
        if not code or len(code) < 20:
            return None
        r = db.q("SELECT * FROM backup_devices WHERE setup_code=? AND setup_expires>?", (code, time.time()))
        return r[0] if r else None

    async def setup_file(request: Request):
        d = setup_device(request.path_params["code"])
        if not d:
            return PlainTextResponse("Este enlace ya caducó: genera otro en Mis respaldos.", 404)
        f = request.path_params["file"]
        base = public_base(request)
        cfg = {"url": base, "device": d["name"], "token": d["token"], "clave": d["clave"],
               "owner": d["owner"], "frecuencia_semanas": 4}
        setup = f"{base}/setup/{d['setup_code']}"
        if f == "config.json":
            cfg["carpetas"] = FOLDERS["windows"] if d["os"] == "windows" else FOLDERS["unix"]
            return JSONResponse(cfg)
        if f == "respaldo.conf":                   # Linux / Mac (sin python ni jq)
            import shlex
            vals = {"URL": base, "DEVICE": d["name"], "TOKEN": d["token"], "CLAVE": d["clave"],
                    "SEMANAS": cfg["frecuencia_semanas"], "CARPETAS": " ".join(FOLDERS["unix"])}
            return PlainTextResponse("".join(f"{k}={shlex.quote(str(v))}\n" for k, v in vals.items()))
        if f.endswith(".cmd"):                     # Windows: doble clic y listo
            body = WINDOWS_CMD.replace("{SETUP}", setup).replace("{DEVICE}", d["name"])
            return Response(body.replace("\n", "\r\n"), media_type="application/octet-stream",
                            headers={"Content-Disposition": f'attachment; filename="Respaldo-{d["name"]}.cmd"'})
        if f == "Respaldo-Homelab.ps1":
            return Response((CLIENTS / "windows" / "Respaldo-Homelab.ps1").read_bytes(), media_type="text/plain")
        if f == "instalar.sh":
            return PlainTextResponse(UNIX_INSTALL.replace("{SETUP}", setup).replace("{DEVICE}", d["name"]))
        if f == "respaldo-facil.sh":
            return PlainTextResponse((CLIENTS / "unix" / "respaldo-facil.sh").read_text())
        return PlainTextResponse("No existe", 404)

    # ------------------------------------------------------------- para el bot
    def guard(fn):
        async def wrapper(request: Request):
            if not internal_ok(request):
                return JSONResponse({"error": "no autorizado"}, 401)
            return await fn(request)
        return wrapper

    def recipients() -> list[int]:
        rows = db.q("SELECT t.chat_id, t.username FROM telegram_links t")
        out = []
        for r in rows:
            acc = auth.account(r["username"])
            if acc and acc["role"] in auth.CAN_SEE_LOGS:
                out.append(r["chat_id"])
        return out

    async def i_backup_auth(request: Request):
        b = await request.json()
        r = db.q("SELECT name, owner, os, token FROM backup_devices WHERE name=?", (str(b.get("device", "")),))
        if not r or not b.get("token") or not hmac.compare_digest(r[0]["token"], str(b["token"])):
            return JSONResponse({"ok": False})
        owner = r[0]["owner"]
        acc = auth.account(owner)
        if not acc:                           # borraste la cuenta: sus equipos ya no suben
            return JSONResponse({"ok": False})
        link = db.q("SELECT chat_id FROM telegram_links WHERE username=?", (owner,))
        return JSONResponse({"ok": True, "device": r[0]["name"], "os": r[0]["os"], "owner": owner,
                             "prefs": prefs(owner),
                             "owner_role": acc["role"] if acc else "", "owner_main": bool(acc and acc["main"]),
                             "chat": link[0]["chat_id"] if link else None})

    async def i_recipients(_request: Request):
        return JSONResponse({"alerts": recipients()})

    async def i_who(request: Request):
        chat = int(request.query_params.get("chat", "0") or 0)
        r = db.q("SELECT username FROM telegram_links WHERE chat_id=?", (chat,))
        acc = auth.account(r[0]["username"]) if r else None
        if not acc:
            return JSONResponse({"user": None})
        return JSONResponse({"user": acc["user"], "role": acc["role"]})

    async def i_link(request: Request):
        b = await request.json()
        code = re.sub(r"\D", "", str(b.get("code", "")))
        r = db.q("SELECT username FROM link_codes WHERE code=? AND expires>?", (code, time.time()))
        if not r:
            return JSONResponse({"ok": False, "error": "Código incorrecto o caducado"})
        user, chat = r[0]["username"], int(b.get("chat_id"))
        db.x("DELETE FROM link_codes WHERE code=?", (code,))
        db.x("DELETE FROM telegram_links WHERE chat_id=? OR username=?", (chat, user))
        db.x("INSERT INTO telegram_links (username, chat_id, name, linked) VALUES (?,?,?,?)",
             (user, chat, str(b.get("name", ""))[:60], time.time()))
        acc = auth.account(user)
        notice(user, f"Vinculaste Telegram ({b.get('name', '')}). Ahí te llegarán los avisos de tus respaldos.")
        return JSONResponse({"ok": True, "user": user, "role": acc["role"] if acc else "familia"})

    async def i_unlink(request: Request):
        chat = int((await request.json()).get("chat_id", 0))
        db.x("DELETE FROM telegram_links WHERE chat_id=?", (chat,))
        return JSONResponse({"ok": True})

    async def i_notice(request: Request):
        b = await request.json()
        if b.get("user"):
            notice(str(b["user"]), str(b.get("text", ""))[:1000], str(b.get("level", "info")))
        return JSONResponse({"ok": True})

    async def i_telegram_device(request: Request):
        """Dispositivo para lo que un familiar le manda al bot por Telegram."""
        user = str((await request.json()).get("user", ""))
        if not auth.account(user):
            return JSONResponse({"error": "no existe"}, 404)
        d = ensure_device(user, "telegram")
        return JSONResponse({"device": d["name"]})

    async def i_devices_of(request: Request):
        user = request.query_params.get("user", "")
        return JSONResponse({"devices": [r["name"] for r in db.q("SELECT name FROM backup_devices WHERE owner=?", (user,))]})

    return [
        Route("/api/my/backups", my_backups),
        Route("/api/my/devices", my_device_add, methods=["POST"]),
        Route("/api/my/devices/{name}/setup", my_device_setup, methods=["POST"]),
        Route("/api/my/devices/{name}", my_device_delete, methods=["DELETE"]),
        Route("/api/my/devices/{name}/files/{fid:int}", my_download),
        Route("/api/my/devices/{name}/files/{fid:int}", my_file_delete, methods=["DELETE"]),
        Route("/api/my/devices/{name}/files/{fid:int}/send", my_file_send, methods=["POST"]),
        Route("/api/my/prefs", my_prefs, methods=["POST"]),
        Route("/api/my/web-token", my_web_token, methods=["POST"]),
        Route("/api/my/telegram", my_telegram_code, methods=["POST"]),
        Route("/api/my/telegram", my_telegram_unlink, methods=["DELETE"]),
        Route("/api/my/notices/seen", my_notices_seen, methods=["POST"]),
        Route("/setup/{code}/{file}", setup_file),
        Route("/internal/backup-auth", guard(i_backup_auth), methods=["POST"]),
        Route("/internal/recipients", guard(i_recipients)),
        Route("/internal/who", guard(i_who)),
        Route("/internal/telegram/link", guard(i_link), methods=["POST"]),
        Route("/internal/telegram/unlink", guard(i_unlink), methods=["POST"]),
        Route("/internal/notice", guard(i_notice), methods=["POST"]),
        Route("/internal/telegram-device", guard(i_telegram_device), methods=["POST"]),
        Route("/internal/devices-of", guard(i_devices_of)),
    ]


WINDOWS_CMD = r"""@echo off
chcp 65001 >nul
title Respaldo MyHomeLab - {DEVICE}
echo.
echo  ==============================================
echo   Configurando el respaldo de esta computadora
echo   (equipo: {DEVICE})
echo  ==============================================
echo.
set "HL=%LOCALAPPDATA%\MyHomeLab"
if not exist "%HL%" mkdir "%HL%"
curl.exe -fsSk -o "%HL%\Respaldo-Homelab.ps1" "{SETUP}/Respaldo-Homelab.ps1"
if errorlevel 1 (
  echo  No pude conectarme al homelab. Revisa que estes en la red de la casa y vuelve a intentarlo.
  pause
  exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%HL%\Respaldo-Homelab.ps1" -Auto "{SETUP}"
echo.
pause
"""

UNIX_INSTALL = r"""#!/usr/bin/env bash
# Instalador del respaldo fácil de MyHomeLab (equipo: {DEVICE})
set -euo pipefail
DEST="$HOME/.local/share/myhomelab"
mkdir -p "$DEST"
echo "==> Descargando el programa de respaldo…"
curl -fsSk "{SETUP}/respaldo-facil.sh" -o "$DEST/respaldo-facil.sh"
chmod +x "$DEST/respaldo-facil.sh"
"$DEST/respaldo-facil.sh" --install "{SETUP}"
"""
