"""Ajustes desde la web: lee y escribe .env y homelab.yml, y pide aplicar cambios.

El contenedor no controla Docker. Para aplicar deja un archivo en run/ y el
servicio homelab-apply del host (scripts/install-apply-service.sh) corre
`docker compose up -d` y escribe el resultado en run/apply-result.json.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import time
from pathlib import Path

import yaml

ENV_PATH = Path(os.environ.get("HOMELAB_ENV", "/homelab/.env"))
RUN_DIR = Path(os.environ.get("HOMELAB_RUN", "/homelab/run"))
PROFILES = {"dns": "AdGuard Home (DNS de la casa)",
            "servicios": "Uptime Kuma y Vaultwarden",
            "ids": "Suricata (IDS)"}

# kind: text | secret | number | bool | profiles | password (se guarda como hash)
FIELDS: list[dict] = [
    {"group": "Telegram", "key": "TELEGRAM_BOT_TOKEN", "label": "Token del bot", "kind": "secret",
     "help": "De @BotFather. Sin comillas ni espacios.", "restart": ["bot"]},
    {"group": "Telegram", "key": "TELEGRAM_ALLOWED_IDS", "label": "IDs permitidos",
     "help": "Separados por coma. El primero recibe alertas y reportes.", "restart": ["bot"]},
    {"group": "Telegram", "key": "TELEGRAM_API_URL", "label": "Servidor Bot API",
     "help": "Tu Bot API local, p. ej. http://host.docker.internal:8081", "restart": ["bot"]},
    {"group": "Telegram", "key": "TELEGRAM_API_DATA", "label": "Carpeta de datos del Bot API",
     "help": "El --dir de tu telegram-bot-api (para recibir archivos de 2 GB).", "restart": ["bot"]},
    {"group": "Web", "key": "HOMELAB_HOST", "label": "IP de la Pi (casa)",
     "help": "La de eth0 (ip -4 -br addr show eth0).", "restart": ["caddy", "grafana", "vaultwarden"]},
    {"group": "Web", "key": "HTTPS_PORT", "label": "Puerto HTTPS", "kind": "number",
     "help": "8443 si nginx ya usa el 443.", "restart": ["caddy", "grafana", "vaultwarden"]},
    {"group": "Web", "key": "HTTP_PORT", "label": "Puerto HTTP", "kind": "number",
     "help": "8088 si nginx ya usa el 80.", "restart": ["caddy"]},
    {"group": "Web", "key": "WEB_USER", "label": "Usuario de la web", "restart": [],
     "help": "Al cambiarlo se cierran las sesiones abiertas."},
    {"group": "Web", "key": "WEB_PASSWORD_HASH", "label": "Contraseña de la web", "kind": "password",
     "help": "Escribe una nueva para cambiarla (se guarda cifrada). Vale desde el siguiente inicio de sesión.",
     "restart": []},
    {"group": "Servicios", "key": "COMPOSE_PROFILES", "label": "Servicios opcionales", "kind": "profiles",
     "help": "Marca lo que quieres encendido y presiona «Guardar y aplicar».", "restart": ["*"]},
    {"group": "Servicios", "key": "CAPTURE_INTERFACES", "label": "Interfaces de Suricata",
     "help": "Separadas por espacio: «eth0 wlan0» = casa + laboratorio. br0 en modo puente.",
     "restart": ["suricata"]},
    {"group": "AdGuard", "key": "ADGUARD_USER", "label": "Usuario de AdGuard", "restart": ["netwatch"]},
    {"group": "AdGuard", "key": "ADGUARD_PASSWORD", "label": "Contraseña de AdGuard", "kind": "secret",
     "restart": ["netwatch"]},
    {"group": "Vaultwarden", "key": "VW_SIGNUPS_ALLOWED", "label": "Permitir crear cuentas", "kind": "bool",
     "help": "Desactívalo en cuanto crees la tuya.", "restart": ["vaultwarden"]},
    {"group": "Vaultwarden", "key": "VW_DATA", "label": "Carpeta de datos",
     "help": "Ideal en el disco USB. Respáldala.", "restart": ["vaultwarden"]},
    {"group": "Vaultwarden", "key": "VW_ADMIN_TOKEN", "label": "Token de /vault/admin", "kind": "secret",
     "generate": True, "restart": ["vaultwarden"]},
    {"group": "Respaldos", "key": "BACKUPS_DIR", "label": "Carpeta de respaldos",
     "help": "Monta ahí un disco USB (no la microSD).", "restart": ["bot"]},
    {"group": "Redes sociales", "key": "META_PAGE_TOKEN", "label": "Token de página de Meta", "kind": "secret",
     "help": "docs/redes-sociales.md", "restart": ["bot"]},
    {"group": "Grafana", "key": "GRAFANA_ADMIN_PASSWORD", "label": "Contraseña inicial de admin", "kind": "secret",
     "help": "Solo aplica la primera vez; después cámbiala dentro de Grafana.", "restart": ["grafana"]},
]
FIELD_BY_KEY = {f["key"]: f for f in FIELDS}

# Campos de homelab.yml que se editan con formulario (lo demás, con el editor avanzado)
YAML_FIELDS: list[dict] = [
    {"group": "Red", "path": "infrastructure.modem.ip", "label": "IP del módem"},
    {"group": "Red", "path": "infrastructure.modem.admin_url", "label": "URL de administración del módem"},
    {"group": "Red", "path": "network.scan_interval", "label": "Segundos entre escaneos", "kind": "number"},
    {"group": "Red", "path": "dns.adguard_url", "label": "URL de AdGuard",
     "help": "Vacío = autodetectar http://127.0.0.1:3002"},
    {"group": "DNS de salida", "path": "dns.upstream", "label": "A quién le pregunta AdGuard", "kind": "select",
     "options": "UPSTREAMS",
     "help": "Control D familiar bloquea contenido para adultos, malware y anuncios para toda la casa."},
    {"group": "DNS de salida", "path": "dns.controld_resolver", "label": "ID de tu perfil de Control D",
     "help": "Solo con «Control D con tu perfil»: lo ves en controld.com → Endpoints (DoH: dns.controld.com/ESTE_ID)."},
    {"group": "Reportes", "path": "reports.daily_time", "label": "Hora del reporte diario (HH:MM)"},
    {"group": "Respaldos", "path": "backups.keep_per_device", "label": "Respaldos a guardar por equipo",
     "kind": "number"},
    {"group": "Respaldos", "path": "backups.max_age_days", "label": "Borrar respaldos con más de (días)",
     "kind": "number", "help": "0 = sin límite. Siempre se conserva el más reciente de cada equipo."},
    {"group": "Respaldos", "path": "backups.forward_to_telegram", "label": "Reenviarlos por Telegram", "kind": "bool"},
    {"group": "Redes sociales", "path": "social.telegram_channel", "label": "Canal de Telegram",
     "help": "@tu_canal o -100…; el bot debe ser admin."},
    {"group": "Redes sociales", "path": "social.facebook_page_id", "label": "ID de la página de Facebook"},
    {"group": "Redes sociales", "path": "social.instagram_user_id", "label": "ID de Instagram Business"},
]


# ----------------------------------------------------------------- .env
def _unescape(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1]
    return v.replace("$$", "$")


def _escape(v: str) -> str:
    v = str(v).replace("\n", " ").replace("\r", "").strip()
    return v.replace("$", "$$")


def read_env(path: Path | None = None) -> dict[str, str]:
    path = path or ENV_PATH
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^\s*([A-Z_][A-Z0-9_]*)\s*=(.*)$", line)
        if m:
            out[m.group(1)] = _unescape(m.group(2))
    return out


def write_env(updates: dict[str, str | None], path: Path | None = None) -> list[str]:
    """Cambia solo las líneas indicadas (None = borrar); conserva comentarios y orden.
    Escribe en el mismo archivo (mismo inodo) para que los montajes de Docker lo vean."""
    path = path or ENV_PATH
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    changed, done = [], set()
    out = []
    for line in lines:
        m = re.match(r"^\s*([A-Z_][A-Z0-9_]*)\s*=(.*)$", line)
        if m and m.group(1) in updates:
            k = m.group(1)
            done.add(k)
            if updates[k] is None:
                changed.append(k)
                continue
            new = f"{k}={_escape(updates[k])}"
            if new != line:
                changed.append(k)
            out.append(new)
        else:
            out.append(line)
    for k, v in updates.items():
        if k not in done and v is not None:
            out.append(f"{k}={_escape(v)}")
            changed.append(k)
    if changed:
        backup(path)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    return changed


def backup(path: Path) -> None:
    if path.exists() and RUN_DIR.exists():
        dst = RUN_DIR / f"{path.name}.{time.strftime('%Y%m%d-%H%M%S')}.bak"
        shutil.copy2(path, dst)
        try:
            os.chmod(dst, 0o600)
        except OSError:
            pass
        olds = sorted(RUN_DIR.glob(f"{path.name}.*.bak"))
        for old in olds[:-10]:          # se quedan las 10 copias más recientes
            old.unlink(missing_ok=True)


def mask(v: str) -> str:
    return "" if not v else ("•" * 6 + v[-4:] if len(v) > 8 else "•" * 6)


def hash_password(pw: str) -> str:
    import bcrypt  # en el contenedor (requirements.txt)
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(12)).decode()


def new_token() -> str:
    return secrets.token_urlsafe(30)


def device_env_key(name: str) -> str:
    return "BACKUP_TOKEN_" + re.sub(r"[^A-Z0-9]", "_", name.upper())


# ------------------------------------------------------------ homelab.yml
def yaml_get(data: dict, path: str):
    cur = data
    for p in path.split("."):
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur


def yaml_set(data: dict, path: str, value) -> None:
    parts = path.split(".")
    cur = data
    for p in parts[:-1]:
        if not isinstance(cur.get(p), dict):
            cur[p] = {}
        cur = cur[p]
    cur[parts[-1]] = value


def read_yaml(path: Path) -> tuple[dict, str]:
    raw = path.read_text(encoding="utf-8") if path.exists() else ""
    return (yaml.safe_load(raw) or {}), raw


def write_yaml_raw(path: Path, raw: str) -> None:
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ValueError("homelab.yml debe ser un mapa (claves: valores)")
    backup(path)
    with open(path, "w", encoding="utf-8") as f:     # mismo inodo
        f.write(raw if raw.endswith("\n") else raw + "\n")


def write_yaml(path: Path, data: dict) -> None:
    write_yaml_raw(path, "# Editado desde Ajustes de la web (copia anterior en run/)\n"
                   + yaml.safe_dump(data, sort_keys=False, allow_unicode=True))


# --------------------------------------------------------------- aplicar
def request_apply(services: list[str] | str = "all", force: list[str] | None = None) -> dict:
    if not RUN_DIR.exists():
        raise RuntimeError("Falta la carpeta run/ (corre scripts/install-apply-service.sh en la Pi)")
    req = {"ts": time.time(), "services": services, "force": force or []}
    tmp = RUN_DIR / "apply-request.tmp"
    tmp.write_text(json.dumps(req))
    tmp.replace(RUN_DIR / "apply-request")
    return req


def apply_status() -> dict:
    installed = (RUN_DIR / "apply-service.installed").exists()
    res = {}
    f = RUN_DIR / "apply-result.json"
    if f.exists():
        try:
            res = json.loads(f.read_text())
        except ValueError:
            res = {}
    pending = (RUN_DIR / "apply-request").exists() and \
        (RUN_DIR / "apply-request").stat().st_mtime > res.get("ts", 0)
    return {"installed": installed, "pending": pending, **res}


def restart_for(keys: list[str]) -> list[str] | str:
    svcs: set[str] = set()
    for k in keys:
        r = (FIELD_BY_KEY.get(k) or {}).get("restart") or (["bot"] if k.startswith("BACKUP_TOKEN_") else [])
        if "*" in r:
            return "all"
        svcs.update(r)
    return sorted(svcs)
