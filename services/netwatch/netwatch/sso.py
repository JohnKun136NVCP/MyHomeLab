"""Inicio de sesión único (Authelia) — opcional, `./scripts/instalar-sso.sh`.

- Las cuentas siguen viviendo en el panel (Ajustes → Cuentas). Aquí se copian a
  config/authelia/users.yml (mismo hash bcrypt) y Authelia las relee sola.
- Caddy, después de que Authelia aprueba, manda a netwatch `Remote-User` y la cabecera
  secreta `X-Homelab-SSO`. Sin esa cabecera (o si no coincide) no se confía en Remote-User.
- Los códigos de Authelia (registrar el 2FA) llegan a config/authelia/notification.txt:
  se te reenvían por Telegram, solo a ti.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger("netwatch.sso")

GROUPS = {"admin": ["admin"], "registros": ["registros"], "familia": ["familia"]}


def secret() -> str:
    return os.environ.get("SSO_SECRET", "").strip()


def domain() -> str:
    return os.environ.get("SSO_DOMAIN", "").strip()


def enabled() -> bool:
    return bool(secret() and domain())


def trusted_user(headers) -> str:
    """Usuario que Authelia ya validó, o '' si la petición no viene de Caddy+Authelia."""
    sec = secret()
    got = headers.get("x-homelab-sso", "")
    if not sec or not got or not hmac.compare_digest(got, sec):
        return ""
    return (headers.get("remote-user") or "").strip()


def _q(v: str) -> str:
    return json.dumps(v, ensure_ascii=False)      # una cadena JSON es YAML válido


def render_users(accounts: list[dict], dom: str) -> str:
    out = ["# Lo genera el panel (Ajustes → Cuentas). No lo edites: se reescribe solo.", "users:"]
    for a in accounts:
        h = a.get("hash") or ""
        if not h.startswith(("$2a$", "$2b$", "$2y$")):
            continue
        u = a["user"]
        out += [f"  {_q(u)}:",
                f"    disabled: false",
                f"    displayname: {_q(a.get('display') or u)}",
                f"    email: {_q(u.lower().replace(' ', '.') + '@' + dom)}",
                f"    password: {_q(h)}",
                f"    groups: {json.dumps(GROUPS.get(a.get('role'), ['familia']))}"]
    if len(out) == 2:
        out[-1] = "users: {}"
    return "\n".join(out) + "\n"


class Sync:
    def __init__(self, db, config_dir: Path, notify) -> None:
        self.db = db
        self.dir = config_dir / "authelia"
        self.notify = notify
        self._last = ""
        self._note_mtime = 0.0
        self._checked = 0.0

    def accounts(self) -> list[dict]:
        from . import auth
        main_user, main_hash = auth.credentials()
        accs = [{"user": main_user, "hash": main_hash, "role": "admin"}] if main_hash else []
        for r in self.db.q("SELECT username, hash, role FROM users ORDER BY username"):
            if r["username"] != main_user:
                accs.append({"user": r["username"], "hash": r["hash"], "role": r["role"]})
        return accs

    def run(self, force: bool = False) -> None:
        if not enabled() or not self.dir.is_dir():
            return
        if not force and time.time() - self._checked < 20:
            return
        self._checked = time.time()
        text = render_users(self.accounts(), domain())
        f = self.dir / "users.yml"
        if text != self._last:
            try:
                cur = f.read_text() if f.exists() else ""
                if cur != text:
                    tmp = f.with_suffix(".tmp")
                    tmp.write_text(text)
                    os.chmod(tmp, 0o600)
                    tmp.replace(f)
                    log.info("Authelia: cuentas actualizadas (%d)", text.count("password:"))
                self._last = text
            except OSError as e:
                log.warning("No pude escribir %s: %s", f, e)
        self.relay_codes()

    def relay_codes(self) -> None:
        n = self.dir / "notification.txt"
        try:
            m = n.stat().st_mtime
        except OSError:
            return
        if m <= self._note_mtime:
            return
        first = self._note_mtime == 0.0
        self._note_mtime = m
        if first and time.time() - m > 600:       # al arrancar no reenvíes uno viejo
            return
        body = n.read_text(errors="replace")[-3000:]
        self.notify("🔐 Authelia (inicio de sesión único) te mandó esto:\n\n" + body.strip(), owner=True)
