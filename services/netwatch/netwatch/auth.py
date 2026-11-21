"""Inicio de sesión de la web: cookie firmada (HMAC) + Caddy forward_auth.

Caddy pregunta a /auth/check antes de cada página. Si la cookie es válida deja
pasar; si no, manda al usuario a /login. El usuario y la contraseña son los de
.env (WEB_USER y WEB_PASSWORD_HASH, bcrypt): se leen en cada intento, así que
cambiarlos en Ajustes no requiere reiniciar nada.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict, deque

from . import settings as st

COOKIE = "hl_session"
TTL = 7 * 86400
MAX_FAILS, WINDOW = 5, 600          # 5 intentos fallidos por IP en 10 minutos

_secret = None
_fails: dict[str, deque] = defaultdict(deque)


def secret() -> bytes:
    global _secret
    if _secret is None:
        base = os.environ.get("SESSION_SECRET") or os.environ.get("INTERNAL_TOKEN") or ""
        # Sin secreto fijo se usa uno al azar: las sesiones duran hasta reiniciar netwatch
        _secret = hashlib.sha256(("homelab-session:" + base).encode()).digest() if base \
            else secrets.token_bytes(32)
    return _secret


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# Base de datos de netwatch (cuentas de la familia). La pone build_app().
_db = None
ROLES = {"admin": "Administrador", "registros": "Ve registros y alertas (sin cambiar nada)",
         "familia": "Familia (sus respaldos)"}
# «registros»: ve todo en modo lectura y recibe las alertas del bot, menos esto
REG_DENY = ("/api/settings", "/api/apply", "/api/vpn", "/api/acl", "/api/users", "/api/system",
            "/api/lookup", "/api/cameras/probe", "/api/parental", "/api/bots", "/api/passwords", "/prometheus", "/metrics", "/vault/admin", "/internal")
# «familia»: solo sus respaldos, cámaras, servicios y enlaces (allowlist)
FAMILY_ALLOW = ("/api/me", "/api/my/", "/api/services", "/api/cameras", "/api/links")
FAMILY_DENY = REG_DENY + ("/api/inbound",)     # compatibilidad (pruebas y otros módulos)
# Lo único que alguien sin rol admin puede cambiar: su cuenta y sus respaldos
FAMILY_WRITE_OK = ("/api/me/password", "/api/my/")
CAN_SEE_LOGS = ("admin", "registros")


def bind(db) -> None:
    global _db
    _db = db


def _fp(h: str) -> str:
    """Huella del hash: si cambia la contraseña, las sesiones abiertas dejan de valer."""
    return hashlib.sha256(("fp:" + (h or "")).encode()).hexdigest()[:10]


def account(user: str) -> dict | None:
    """{'user','role','hash','main'} de la cuenta principal (.env) o de la familia (base)."""
    if not user:
        return None
    main_user, main_hash = credentials()
    if user == main_user:
        return {"user": user, "role": "admin", "hash": main_hash, "main": True}
    if _db is not None:
        r = _db.q("SELECT username, hash, role FROM users WHERE username=?", (user,))
        if r:
            return {"user": r[0]["username"], "role": r[0]["role"], "hash": r[0]["hash"], "main": False}
    return None


def make_token(user: str, now: float | None = None, ttl: int = TTL) -> str:
    exp = int((now or time.time()) + ttl)
    acc = account(user)
    payload = f"{user}|{exp}|{_fp(acc['hash'] if acc else '')}".encode()
    sig = hmac.new(secret(), payload, hashlib.sha256).digest()
    return f"{_b64(payload)}.{_b64(sig)}"


def session(tok: str, now: float | None = None) -> dict | None:
    """{'user','role'} si la cookie es válida, no caducó y la cuenta sigue igual."""
    try:
        p64, s64 = (tok or "").split(".", 1)
        payload = _unb64(p64)
        if not hmac.compare_digest(_unb64(s64), hmac.new(secret(), payload, hashlib.sha256).digest()):
            return None
        parts = payload.decode().split("|")
        user, exp = parts[0], int(parts[1])
        if exp < (now or time.time()):
            return None
        acc = account(user)               # borraste la cuenta o cambiaste el usuario principal
        if not acc or (len(parts) > 2 and parts[2] != _fp(acc["hash"])):
            return None                   # cambió la contraseña
        return {"user": acc["user"], "role": acc["role"], "main": acc["main"]}
    except (ValueError, UnicodeDecodeError, IndexError):
        return None


def from_request(request) -> dict | None:
    """Sesión del panel: su cookie o, si se entra por el inicio de sesión único, lo que validó Authelia."""
    ses = session(request.cookies.get(COOKIE, ""))
    if ses:
        return ses
    from . import sso
    user = sso.trusted_user(request.headers)
    acc = account(user) if user else None
    if acc:
        return {"user": acc["user"], "role": acc["role"], "main": acc["main"], "sso": True}
    return None


def check_token(tok: str, now: float | None = None) -> str | None:
    s = session(tok, now)
    return s["user"] if s else None


def allowed(role: str, method: str, uri: str) -> bool:
    if role == "admin":
        return True
    path = (uri or "/").split("?", 1)[0]
    if path.startswith("/internal") or path.startswith("/api/cameras/probe"):
        return False
    if method.upper() not in ("GET", "HEAD", "OPTIONS"):
        return path.startswith(FAMILY_WRITE_OK)
    if role == "registros":
        return not path.startswith(REG_DENY)
    # familia: páginas y estáticos sí; de la API solo lo suyo
    if path.startswith(("/api/", "/prometheus", "/metrics", "/vault/admin")):
        return path.startswith(FAMILY_ALLOW) and not path.startswith("/api/cameras/probe")
    return True


def authenticate(user: str, pw: str) -> dict | None:
    acc = account(user)
    if acc and verify_password(pw, acc["hash"]):
        return acc
    return None


def credentials() -> tuple[str, str]:
    env = st.read_env()
    user = env.get("WEB_USER") or os.environ.get("WEB_USER", "admin")
    h = env.get("WEB_PASSWORD_HASH") or os.environ.get("WEB_PASSWORD_HASH", "").replace("$$", "$")
    return user, h


def verify_password(pw: str, hashed: str) -> bool:
    if not hashed or not pw:
        return False
    try:
        import bcrypt
        return bcrypt.checkpw(pw.encode(), hashed.encode())
    except (ImportError, ValueError):
        return False


def too_many(ip: str, now: float | None = None) -> bool:
    now = now or time.time()
    q = _fails[ip]
    while q and q[0] < now - WINDOW:
        q.popleft()
    return len(q) >= MAX_FAILS


def record_fail(ip: str, now: float | None = None) -> int:
    _fails[ip].append(now or time.time())
    return len(_fails[ip])


def clear_fails(ip: str) -> None:
    _fails.pop(ip, None)


def safe_next(n: str) -> str:
    """Solo rutas internas (evita redirecciones a otros sitios)."""
    return n if n.startswith("/") and not n.startswith("//") and "\\" not in n else "/"


LOGIN_HTML = """<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>MyHomeLab · Entrar</title>
<link rel="stylesheet" href="/static/style.css">
<style>.login{max-width:360px;margin:12vh auto;padding:28px}.login input{width:100%;margin:6px 0 14px}
.login button{width:100%;padding:10px}.err{color:var(--bad);margin:0 0 12px}</style></head>
<body><form class="card login" method="post" action="/auth/login">
<h2>🛡 MyHomeLab</h2>{msg}
<label>Usuario</label><input name="user" autocomplete="username" autofocus required>
<label>Contraseña</label><input name="password" type="password" autocomplete="current-password" required>
<input type="hidden" name="next" value="{next}">
<button class="primary" type="submit">Entrar</button>
<p class="muted small" style="margin-top:14px">La sesión dura 7 días en este navegador.</p>
</form></body></html>"""
