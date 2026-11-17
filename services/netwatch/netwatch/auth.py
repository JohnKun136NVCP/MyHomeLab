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


def make_token(user: str, now: float | None = None, ttl: int = TTL) -> str:
    exp = int((now or time.time()) + ttl)
    payload = f"{user}|{exp}".encode()
    sig = hmac.new(secret(), payload, hashlib.sha256).digest()
    return f"{_b64(payload)}.{_b64(sig)}"


def check_token(tok: str, now: float | None = None) -> str | None:
    """Regresa el usuario si la cookie es válida y no ha caducado."""
    try:
        p64, s64 = (tok or "").split(".", 1)
        payload = _unb64(p64)
        if not hmac.compare_digest(_unb64(s64), hmac.new(secret(), payload, hashlib.sha256).digest()):
            return None
        user, exp = payload.decode().rsplit("|", 1)
        if int(exp) < (now or time.time()):
            return None
        # Si cambiaste el usuario en Ajustes, las sesiones viejas dejan de valer
        current = credentials()[0]
        return user if not current or user == current else None
    except (ValueError, UnicodeDecodeError):
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
