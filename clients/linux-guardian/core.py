"""
Linux Guardian - núcleo (sin interfaz gráfica).

Aquí vive toda la lógica: detectar paquetes, temas y apps, armar el
respaldo, comprimirlo en .7z cifrado, partirlo si pesa mucho, enviarlo
a Telegram y gestionar el recordatorio trimestral.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

HOME = Path.home()
APP_DIR = Path(__file__).resolve().parent
CONFIG_DIR = HOME / ".config" / "linux-guardian"
CONFIG_FILE = CONFIG_DIR / "config.json"
TELETHON_SESSION = CONFIG_DIR / "telethon"

Log = Callable[[str], None]

MiB = 1024 * 1024
# Límites de Telegram:
#  - Bot API pública (api.telegram.org): 50 MB por archivo.
#  - Cuenta personal (Telethon/MTProto) o servidor Bot API local: 2 GB.
LIMIT_BOT_PUBLIC = 48 * MiB
LIMIT_2GB = 1900 * MiB  # < 2000 MB decimales (límite real de Telegram)


# ----------------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------------
def run(cmd: list[str] | str, timeout: int = 60, **kw) -> tuple[int, str]:
    """Ejecuta un comando y regresa (código, salida). Nunca lanza excepción."""
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            shell=isinstance(cmd, str), **kw,
        )
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return 127, ""
    except subprocess.TimeoutExpired:
        return 124, "tiempo agotado"


def have(prog: str) -> bool:
    return shutil.which(prog) is not None


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TB"


def load_config() -> dict:
    cfg = {
        "telegram_mode": "bot",          # "bot" | "user"
        "bot_token": "",
        "chat_id": "",
        "api_base": "https://api.telegram.org",
        "api_id": "",
        "api_hash": "",
        "dest_dir": str(HOME / "Respaldos"),
        "custom_paths": [],
        "password_files": [],
        "last_backup": "",
        "last_reminder": "",
        "remind_days": 90,
        "homelab_url": "",         # https://IP-de-la-Pi  (MyHomeLab)
        "homelab_device": "",
        "homelab_token": "",
        "homelab_ca": "",
        "apps_excluded": [],       # claves "apt:x", "flatpak:x", "snap:x", "manual:/ruta"
        "themes_selected": None,   # None = selección por defecto
    }
    try:
        cfg.update(json.loads(CONFIG_FILE.read_text()))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(CONFIG_DIR, 0o700)
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
    os.chmod(tmp, 0o600)  # contiene el token del bot
    tmp.replace(CONFIG_FILE)


# ----------------------------------------------------------------------
# Detección del sistema
# ----------------------------------------------------------------------
def distro_name() -> str:
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip('"')
    except OSError:
        pass
    return "Linux"


def apt_manual_packages() -> list[str]:
    if not have("apt-mark"):
        return []
    code, out = run(["apt-mark", "showmanual"])
    return sorted(l.strip() for l in out.splitlines() if l.strip()) if code == 0 else []


def flatpak_apps() -> list[tuple[str, str]]:
    """[(app_id, remote)]"""
    if not have("flatpak"):
        return []
    code, out = run(["flatpak", "list", "--app", "--columns=application,origin"])
    res = []
    if code == 0:
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2 and "." in parts[0]:
                res.append((parts[0], parts[1]))
    return res


def flatpak_remotes() -> list[tuple[str, str]]:
    """[(nombre, url)]"""
    if not have("flatpak"):
        return []
    code, out = run(["flatpak", "remotes", "--columns=name,url"])
    res = []
    if code == 0:
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                res.append((parts[0], parts[1]))
    return res


def snap_apps() -> list[tuple[str, str, bool]]:
    """[(nombre, canal, classic)]"""
    if not have("snap"):
        return []
    code, out = run(["snap", "list"])
    res = []
    if code == 0:
        for line in out.splitlines()[1:]:
            cols = line.split()
            if len(cols) >= 4:
                name = cols[0]
                if name in ("core", "core18", "core20", "core22", "core24",
                            "snapd", "bare") or name.startswith("gnome-") \
                        or name.startswith("gtk-common"):
                    continue
                tracking = cols[3] if len(cols) > 3 else "latest/stable"
                notes = cols[-1]
                res.append((name, tracking, "classic" in notes))
    return res


def vscode_extensions() -> list[str]:
    for prog in ("code", "codium"):
        if have(prog):
            code, out = run([prog, "--list-extensions"], timeout=60)
            if code == 0:
                return [l.strip() for l in out.splitlines() if "." in l]
    return []


THEME_KEYS = [
    ("org.gnome.desktop.interface", "gtk-theme"),
    ("org.gnome.desktop.interface", "icon-theme"),
    ("org.gnome.desktop.interface", "cursor-theme"),
    ("org.gnome.desktop.interface", "font-name"),
    ("org.gnome.desktop.interface", "monospace-font-name"),
    ("org.gnome.desktop.interface", "color-scheme"),
    ("org.gnome.desktop.wm.preferences", "theme"),
    ("org.gnome.desktop.background", "picture-uri"),
    ("org.gnome.desktop.background", "picture-uri-dark"),
    ("org.cinnamon.desktop.interface", "gtk-theme"),
    ("org.cinnamon.desktop.interface", "icon-theme"),
    ("org.mate.interface", "gtk-theme"),
    ("org.mate.interface", "icon-theme"),
]


def theme_settings() -> list[tuple[str, str, str]]:
    """[(schema, key, valor)] de gsettings (GNOME/Cinnamon/MATE)."""
    if not have("gsettings"):
        return []
    res = []
    for schema, key in THEME_KEYS:
        code, out = run(["gsettings", "get", schema, key], timeout=5)
        if code == 0 and out.strip():
            res.append((schema, key, out.strip()))
    return res


def theme_folders() -> list[tuple[str, list[str]]]:
    """Temas, íconos y fuentes instalados por el usuario."""
    res = []
    for rel in (".themes", ".local/share/themes", ".icons",
                ".local/share/icons", ".fonts", ".local/share/fonts"):
        p = HOME / rel
        if p.is_dir():
            items = sorted(x.name for x in p.iterdir())
            res.append((rel, items))
    return res


# Apps "importantes": cómo detectarlas y cómo reinstalarlas.
# source: apt | deb_url | repo | flatpak | snap | git
APPS = {
    "Zoom":        {"apt": "zoom", "flatpak": "us.zoom.Zoom",
                    "deb_url": "https://zoom.us/client/latest/zoom_amd64.deb"},
    "Brave":       {"apt": "brave-browser", "flatpak": "com.brave.Browser",
                    "snap": "brave", "repo": "brave"},
    "VS Code":     {"apt": "code", "flatpak": "com.visualstudio.code",
                    "snap": "code", "repo": "vscode"},
    "Telegram":    {"apt": "telegram-desktop", "flatpak": "org.telegram.desktop",
                    "snap": "telegram-desktop"},
    "OpenShot":    {"apt": "openshot-qt", "flatpak": "org.openshot.OpenShot"},
    "Kitty":       {"apt": "kitty"},
    "Zsh":         {"apt": "zsh"},
    "Oh My Zsh":   {"dir": ".oh-my-zsh"},
    "Powerlevel10k": {"dir": [".oh-my-zsh/custom/themes/powerlevel10k",
                              "powerlevel10k", ".powerlevel10k"]},
    "Flatpak + Flathub": {"apt": "flatpak"},
    "Git":         {"apt": "git"},
    "Timeshift":   {"apt": "timeshift"},
    "7-Zip":       {"apt": ["7zip", "p7zip-full"]},
}


def _dpkg_installed(pkg: str) -> str | None:
    code, out = run(["dpkg-query", "-W", "-f=${Status}|${Version}", pkg], timeout=10)
    if code == 0 and out.startswith("install ok installed"):
        return out.split("|", 1)[1].strip()
    return None


def detect_apps(flatpaks=None, snaps=None) -> list[dict]:
    """Regresa [{name, installed, source, version, id}]"""
    flatpaks = flatpak_apps() if flatpaks is None else flatpaks
    snaps = snap_apps() if snaps is None else snaps
    fl_ids = {a for a, _ in flatpaks}
    sn_ids = {s for s, _, _ in snaps}
    res = []
    for name, r in APPS.items():
        info = {"name": name, "installed": False, "source": "", "version": "", "id": ""}
        apts = r.get("apt", [])
        apts = [apts] if isinstance(apts, str) else apts
        for pkg in apts:
            if have("dpkg-query"):
                v = _dpkg_installed(pkg)
                if v:
                    src = "repo" if r.get("repo") else ("deb_url" if r.get("deb_url") else "apt")
                    info.update(installed=True, source=src, version=v, id=pkg)
                    break
        if not info["installed"] and r.get("flatpak") in fl_ids:
            info.update(installed=True, source="flatpak", id=r["flatpak"])
        if not info["installed"] and r.get("snap") in sn_ids:
            info.update(installed=True, source="snap", id=r["snap"])
        if not info["installed"] and r.get("dir"):
            dirs = r["dir"] if isinstance(r["dir"], list) else [r["dir"]]
            for d in dirs:
                if (HOME / d).is_dir():
                    info.update(installed=True, source="git", id=d)
                    break
        res.append(info)
    return res


def apt_sources() -> list[str]:
    d = Path("/etc/apt/sources.list.d")
    return sorted(p.name for p in d.iterdir()) if d.is_dir() else []


# ----------------------------------------------------------------------
# Dependencias
# ----------------------------------------------------------------------
def parse_depends(field_value: str) -> list[list[str]]:
    """'a (>= 1), b | c:any' -> [['a'], ['b', 'c']]"""
    groups = []
    for chunk in field_value.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        alts = []
        for alt in chunk.split("|"):
            alt = re.sub(r"\(.*?\)|\[.*?\]|<.*?>", "", alt).strip()
            alt = alt.split(":")[0].strip()
            if alt:
                alts.append(alt)
        if alts:
            groups.append(alts)
    return groups


def check_dep_groups(groups: list[list[str]]) -> list[tuple[str, bool]]:
    """[(texto del grupo, satisfecho)]"""
    res = []
    for alts in groups:
        ok = any(_dpkg_installed(a) for a in alts) if have("dpkg-query") else False
        res.append((" | ".join(alts), ok))
    return res


def deb_file_depends(path: str) -> tuple[dict, list[tuple[str, bool]]]:
    """Información y dependencias de un archivo .deb."""
    info = {}
    if not have("dpkg-deb"):
        return info, []
    for f in ("Package", "Version", "Architecture", "Description", "Depends"):
        code, out = run(["dpkg-deb", "-f", path, f], timeout=20)
        info[f] = out.strip() if code == 0 else ""
    return info, check_dep_groups(parse_depends(info.get("Depends", "")))


def package_depends(pkg: str) -> tuple[list[tuple[str, bool]], list[str]]:
    """(dependencias de pkg, paquetes instalados que dependen de pkg)"""
    deps: list[tuple[str, bool]] = []
    code, out = run(["dpkg-query", "-W", "-f=${Depends}", pkg], timeout=10)
    if code == 0:
        deps = check_dep_groups(parse_depends(out))
    rdeps: list[str] = []
    if have("apt-cache"):
        code, out = run(["apt-cache", "rdepends", "--installed", pkg], timeout=30)
        if code == 0:
            lines = out.splitlines()
            rdeps = sorted({l.strip().lstrip("|") for l in lines[2:] if l.strip()})
    return deps, rdeps


# ----------------------------------------------------------------------
# Dueño de archivos (dpkg -S en lote)
# ----------------------------------------------------------------------
def dpkg_owners(paths: list[str]) -> dict[str, str]:
    """{ruta: paquete} para las rutas que pertenecen a un paquete .deb."""
    owners: dict[str, str] = {}
    if not have("dpkg") or not paths:
        return owners
    for i in range(0, len(paths), 200):
        chunk = paths[i:i + 200]
        _, out = run(["dpkg", "-S", *chunk], timeout=120)
        for line in out.splitlines():
            if ": /" not in line or line.startswith("dpkg-query:"):
                continue
            pkgs, path = line.split(": /", 1)
            pkg = pkgs.split(",")[0].strip().split(":")[0]
            owners.setdefault("/" + path.strip(), pkg)
    return owners


# ----------------------------------------------------------------------
# Todas las aplicaciones instaladas
# ----------------------------------------------------------------------
DESKTOP_DIRS = [
    ("/var/lib/flatpak/exports/share/applications", "flatpak"),
    (str(HOME / ".local/share/flatpak/exports/share/applications"), "flatpak"),
    ("/var/lib/snapd/desktop/applications", "snap"),
    ("/usr/share/applications", "system"),
    ("/usr/local/share/applications", "system"),
    (str(HOME / ".local/share/applications"), "user"),
]
KIND_LABEL = {"apt": "apt", "flatpak": "Flatpak", "snap": "Snap",
              "manual": "Manual (AppImage, /opt…)", "apt-cli": "apt (sin ventana)"}


def parse_desktop(path: Path) -> dict | None:
    name = name_es = exec_ = ""
    typ, hidden, in_entry = "", False, False
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return None
    for line in lines:
        line = line.strip()
        if line.startswith("["):
            in_entry = line == "[Desktop Entry]"
            continue
        if not in_entry or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        if k == "Name":
            name = v
        elif k in ("Name[es]", "Name[es_MX]") and not name_es:
            name_es = v
        elif k == "Exec":
            exec_ = v
        elif k == "Type":
            typ = v
        elif k in ("NoDisplay", "Hidden") and v.strip().lower() == "true":
            hidden = True
    if typ.strip() != "Application":
        return None
    return {"name": name_es or name or path.stem, "exec": exec_, "hidden": hidden}


def exec_binary(exec_line: str) -> str:
    """Primer ejecutable real de una línea Exec= (sin env, VAR=x ni %u)."""
    import shlex
    try:
        toks = shlex.split(exec_line)
    except ValueError:
        toks = exec_line.split()
    for t in toks:
        if t == "env" or ("=" in t and not t.startswith("/")) or t.startswith("%"):
            continue
        return t if t.startswith("/") else (shutil.which(t) or t)
    return ""


def _manual_payload(binary: str) -> list[str]:
    """Qué copiar para reinstalar una app manual."""
    if not binary.startswith("/"):
        return []
    b = Path(binary)
    parts = b.parts
    if len(parts) > 2 and parts[1] == "opt":
        return [str(Path("/opt") / parts[2])]
    try:
        rel = b.relative_to(HOME)
    except ValueError:
        return [binary] if b.exists() else []
    if b.suffix.lower() == ".appimage" or len(rel.parts) <= 1 or \
            str(rel).startswith((".local/bin", "bin/")):
        return [binary]
    return [str(HOME / rel.parts[0] / rel.parts[1])] if len(rel.parts) > 2 else [str(b.parent)]


def list_applications(include_hidden: bool = False) -> list[dict]:
    """Todas las apps: con ventana (.desktop), Flatpak, Snap y paquetes apt manuales.

    Cada fila: {row, key, name, kind, id, where, paths}
    `key` es lo que se marca o desmarca (dos .desktop del mismo paquete comparten key).
    """
    fl = dict(flatpak_apps())
    sn = {n for n, _, _ in snap_apps()}
    seen_files: set[str] = set()
    raw = []
    for d, scope in DESKTOP_DIRS:
        p = Path(d)
        if not p.is_dir():
            continue
        for f in sorted(p.glob("*.desktop")):
            if f.name in seen_files:
                continue
            info = parse_desktop(f)
            if not info or (info["hidden"] and not include_hidden):
                continue
            seen_files.add(f.name)
            raw.append((f, scope, info))

    sys_files = [str(f) for f, s, _ in raw if s == "system"]
    bins = {str(f): exec_binary(i["exec"]) for f, s, i in raw if s in ("system", "user")}
    owners = dpkg_owners(sys_files + [b for b in bins.values() if b.startswith("/")])

    rows, keys = [], set()
    for f, scope, info in raw:
        sf = str(f)
        if scope == "flatpak":
            kind, ident, where = "flatpak", f.stem, fl.get(f.stem, "flathub")
        elif scope == "snap":
            kind, ident, where = "snap", f.stem.split("_")[0], "snap"
        elif sf in owners or owners.get(bins.get(sf, "")):
            kind, ident = "apt", owners.get(sf) or owners[bins[sf]]
            where = sf
        else:
            kind, ident, where = "manual", sf, bins.get(sf, "") or sf
        key = f"{'apt' if kind == 'apt' else kind}:{ident}"
        paths = [sf] + _manual_payload(bins.get(sf, "")) if kind == "manual" else []
        rows.append({"row": sf, "key": key, "name": info["name"], "kind": kind,
                     "id": ident if kind != "manual" else Path(where).name,
                     "where": where, "paths": paths})
        keys.add(key)

    for app, origin in fl.items():
        if f"flatpak:{app}" not in keys:
            rows.append({"row": "flatpak:" + app, "key": f"flatpak:{app}", "name": app,
                         "kind": "flatpak", "id": app, "where": origin, "paths": []})
            keys.add(f"flatpak:{app}")
    for name in sn:
        if f"snap:{name}" not in keys:
            rows.append({"row": "snap:" + name, "key": f"snap:{name}", "name": name,
                         "kind": "snap", "id": name, "where": "snap", "paths": []})
            keys.add(f"snap:{name}")
    for pkg in apt_manual_packages():
        if f"apt:{pkg}" not in keys:
            rows.append({"row": "apt-cli:" + pkg, "key": f"apt:{pkg}", "name": pkg,
                         "kind": "apt-cli", "id": pkg, "where": "", "paths": []})
            keys.add(f"apt:{pkg}")
    rows.sort(key=lambda r: (r["kind"] == "apt-cli", r["name"].lower()))
    return rows


# ----------------------------------------------------------------------
# Temas, íconos, cursores, fuentes, SDDM, Plasma
# ----------------------------------------------------------------------
THEME_SOURCES = [
    ("GTK / Shell", ".themes"), ("GTK / Shell", ".local/share/themes"),
    ("GTK / Shell", "/usr/share/themes"),
    ("Íconos", ".icons"), ("Íconos", ".local/share/icons"), ("Íconos", "/usr/share/icons"),
    ("Fuentes", ".fonts"), ("Fuentes", ".local/share/fonts"),
    ("Fuentes", "/usr/local/share/fonts"), ("Fuentes", "/usr/share/fonts"),
    ("SDDM", "/usr/share/sddm/themes"),
    ("Plasma", ".local/share/plasma/desktoptheme"), ("Plasma", "/usr/share/plasma/desktoptheme"),
    ("Plasma Look&Feel", ".local/share/plasma/look-and-feel"),
    ("Plasma Look&Feel", "/usr/share/plasma/look-and-feel"),
    ("Esquema de color", ".local/share/color-schemes"), ("Esquema de color", "/usr/share/color-schemes"),
    ("Decoración ventanas", ".local/share/aurorae/themes"),
    ("Decoración ventanas", "/usr/share/aurorae/themes"),
    ("Kvantum", ".config/Kvantum"), ("Kvantum", "/usr/share/Kvantum"),
    ("Fondos", ".local/share/wallpapers"), ("Fondos", ".local/share/backgrounds"),
]


def _ini_value(path: Path, section: str, key: str) -> str:
    try:
        cur = ""
        for line in path.read_text(errors="replace").splitlines():
            line = line.strip()
            if line.startswith("["):
                cur = line.strip("[]")
            elif cur == section and line.startswith(key + "="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def sddm_config_files() -> list[Path]:
    files = [Path("/etc/sddm.conf")]
    d = Path("/etc/sddm.conf.d")
    if d.is_dir():
        files += sorted(d.glob("*.conf"))
    return [f for f in files if f.is_file()]


def active_themes() -> set[str]:
    """Nombres de temas en uso (GNOME/Cinnamon/MATE, KDE y SDDM)."""
    act = {v.strip("'\"") for _, _, v in theme_settings()}
    cfg = HOME / ".config"
    for f, sec, key in ((cfg / "kdeglobals", "Icons", "Theme"),
                        (cfg / "kdeglobals", "General", "ColorScheme"),
                        (cfg / "kdeglobals", "KDE", "LookAndFeelPackage"),
                        (cfg / "kcminputrc", "Mouse", "cursorTheme"),
                        (cfg / "plasmarc", "Theme", "name"),
                        (cfg / "Kvantum" / "kvantum.kvconfig", "General", "theme")):
        v = _ini_value(f, sec, key)
        if v:
            act.add(v)
    deco = _ini_value(cfg / "kwinrc", "org.kde.kdecoration2", "theme")
    if deco:
        act.add(deco.replace("__aurorae__svg__", ""))
    for f in sddm_config_files():
        v = _ini_value(f, "Theme", "Current")
        if v:
            act.add(v)
    return {a for a in act if a}


def list_themes() -> list[dict]:
    """[{row, kind, name, path, scope, package, active}]"""
    active = active_themes()
    items = []
    for kind, d in THEME_SOURCES:
        base = Path(d) if d.startswith("/") else HOME / d
        if not base.is_dir():
            continue
        scope = "sistema" if d.startswith("/") else "usuario"
        try:
            entries = sorted(base.iterdir())
        except OSError:
            continue
        for p in entries:
            if p.name.startswith(".") or p.name in ("default", "hicolor", "locolor") \
                    or p.name.endswith((".cache", ".dir", ".uuid")):
                continue
            k = kind
            if kind == "Íconos" and p.is_dir():
                if (p / "cursors").is_dir():
                    others = [x for x in p.iterdir() if x.is_dir() and x.name != "cursors"]
                    k = "Cursores" if not others else "Íconos + cursores"
                elif not any(x.is_dir() for x in p.iterdir()):
                    continue
            if kind == "Fuentes" and scope == "sistema" and p.is_dir():
                # /usr/share/fonts/truetype/dejavu → un nivel más abajo
                subs = [x for x in sorted(p.iterdir()) if x.is_dir()]
                if subs:
                    for s in subs:
                        items.append({"kind": "Fuentes", "name": f"{p.name}/{s.name}",
                                      "path": str(s), "scope": scope})
                    continue
            if kind == "Esquema de color":
                name = p.stem
            else:
                name = p.name
            items.append({"kind": k, "name": name, "path": str(p), "scope": scope})
    owners = dpkg_owners([i["path"] for i in items if i["scope"] == "sistema"])
    for i in items:
        i["row"] = i["path"]
        i["package"] = owners.get(i["path"], "")
        i["active"] = i["name"] in active or Path(i["path"]).name in active
    return items


def default_theme_selection(items: list[dict]) -> list[str]:
    """Por defecto: lo tuyo, lo del sistema sin paquete y lo que está en uso."""
    return [i["path"] for i in items
            if i["scope"] == "usuario" or not i["package"] or i["active"]]


def theme_info(path: str) -> str:
    p = Path(path)
    out = [f"Ruta: {p}"]
    code, du = run(["du", "-sh", str(p)], timeout=15)
    if code == 0 and du:
        out.append("Tamaño: " + du.split()[0])
    for meta in ("index.theme", "metadata.desktop", "metadata.json", "theme.conf"):
        f = p / meta
        if f.is_file():
            out.append(f"\n{meta}:\n" + f.read_text(errors="replace")[:1500])
            break
    if p.is_dir() and p.name and any(x.suffix.lower() in (".ttf", ".otf") for x in p.iterdir()):
        fonts = [x.name for x in p.iterdir() if x.suffix.lower() in (".ttf", ".otf", ".ttc")]
        out.append(f"\n{len(fonts)} archivos de fuente: " + ", ".join(fonts[:15]))
    return "\n".join(out)


def theme_preview_image(path: str) -> Path | None:
    """Busca una imagen representativa (preview de SDDM/Plasma, ícono de carpeta…)."""
    p = Path(path)
    if p.is_file():
        return p if p.suffix.lower() in (".png", ".jpg", ".jpeg") else None
    for pat in ("preview*.png", "preview*.jpg", "screenshot*.png", "screenshot*.jpg",
                "contents/previews/*.png", "contents/previews/*.jpg",
                "*/places/folder.png", "48x48/places/folder.png", "places/48/folder.png",
                "thumbnail.png", "*.png"):
        found = sorted(p.glob(pat))
        if found:
            return found[0]
    return None


# ----------------------------------------------------------------------
# PDF
# ----------------------------------------------------------------------
def render_pdf_page(path: str, page: int, box=(540, 460)) -> tuple[bytes | None, int, str]:
    """(png de la página, número de páginas, texto/info). Usa PyMuPDF o poppler."""
    try:
        import fitz  # PyMuPDF (viene en el venv)
        doc = fitz.open(path)
        if doc.needs_pass:
            return None, 0, "PDF protegido con contraseña."
        n = doc.page_count
        page = max(0, min(page, n - 1))
        pg = doc[page]
        zoom = min(box[0] / pg.rect.width, box[1] / pg.rect.height)
        png = pg.get_pixmap(matrix=fitz.Matrix(zoom, zoom)).tobytes("png")
        meta = doc.metadata or {}
        info = "\n".join(f"{k}: {v}" for k, v in meta.items() if v)
        text = pg.get_text()[:4000]
        return png, n, f"{info}\n\n── Texto de la página {page + 1} ──\n{text}"
    except ImportError:
        pass
    except Exception as e:  # noqa: BLE001
        return None, 0, f"No se pudo abrir el PDF: {e}"
    if not have("pdftoppm"):
        return None, 0, ("Para ver PDFs instala PyMuPDF en el venv (./install.sh lo hace) "
                         "o poppler-utils:  sudo apt install poppler-utils")
    _, info = run(["pdfinfo", path], timeout=20)
    m = re.search(r"Pages:\s+(\d+)", info)
    n = int(m.group(1)) if m else 1
    page = max(0, min(page, n - 1))
    with tempfile.TemporaryDirectory() as td:
        prefix = os.path.join(td, "p")
        run(["pdftoppm", "-f", str(page + 1), "-l", str(page + 1), "-png", "-singlefile",
             "-scale-to-x", str(box[0]), "-scale-to-y", "-1", path, prefix], timeout=60)
        png = Path(prefix + ".png").read_bytes() if Path(prefix + ".png").exists() else None
    _, text = run(["pdftotext", "-f", str(page + 1), "-l", str(page + 1), path, "-"], timeout=30)
    return png, n, f"{info.strip()}\n\n── Texto de la página {page + 1} ──\n{text[:4000]}"


# ----------------------------------------------------------------------
# Categorías de respaldo
# ----------------------------------------------------------------------
@dataclass
class Category:
    key: str
    label: str
    paths: list[str] = field(default_factory=list)   # relativas a $HOME
    default: bool = True
    sensitive: bool = False
    note: str = ""


CATEGORIES = [
    Category("packages", "Listas de paquetes y apps (apt, Flatpak, Snap, VS Code)",
             note="Solo listas de texto: pesa casi nada y es lo que permite reinstalar todo."),
    Category("shell", "Shell: bash, zsh, Oh My Zsh custom, Powerlevel10k",
             [".bashrc", ".bash_aliases", ".profile", ".zshrc", ".zprofile",
              ".zshenv", ".p10k.zsh", ".oh-my-zsh/custom"]),
    Category("kitty", "Kitty (configuración y temas)", [".config/kitty"]),
    Category("git", "Git (.gitconfig)", [".gitconfig", ".config/git"]),
    Category("vscode", "VS Code: settings, atajos y snippets",
             [".config/Code/User/settings.json", ".config/Code/User/keybindings.json",
              ".config/Code/User/snippets"]),
    Category("apps", "Aplicaciones elegidas en la pestaña «Aplicaciones»",
             note="Las de apt/Flatpak/Snap se reinstalan; las manuales (AppImage, /opt) se copian."),
    Category("themes", "Temas, íconos, cursores, fuentes y SDDM elegidos en «Temas»",
             note="Los que vienen de un paquete se reinstalan con apt; los demás se copian."),
    Category("kde", "Ajustes de apariencia KDE Plasma / GTK (kdeglobals, kwin, cursor…)",
             [".config/kdeglobals", ".config/kwinrc", ".config/plasmarc",
              ".config/plasma-org.kde.plasma.desktop-appletsrc", ".config/plasmashellrc",
              ".config/kcminputrc", ".config/kglobalshortcutsrc", ".config/ksplashrc",
              ".config/kscreenlockerrc", ".config/Kvantum", ".config/gtk-3.0",
              ".config/gtk-4.0", ".gtkrc-2.0", ".config/fontconfig"]),
    Category("desktop", "Ajustes del escritorio (dconf: atajos, dock, extensiones)",
             note="Se guarda un volcado dconf; al restaurar se pregunta antes de cargarlo."),
    Category("ssh", "Llaves SSH (~/.ssh)", [".ssh"], sensitive=True,
             note="Se protegen con la contraseña del .7z (cifrado AES-256)."),
    Category("gnupg", "Llaves GPG (~/.gnupg)", [".gnupg"], default=False, sensitive=True),
    Category("passwords", "Contraseñas exportadas por ti (archivos que elijas)",
             sensitive=True,
             note="Exporta tú mismo el CSV desde Brave/Firefox/KeePassXC y agrégalo."),
    Category("custom", "Rutas personalizadas (las que agregues en el Explorador)"),
]

SKIP_NAMES = {"Cache", "CachedData", "GPUCache", "Code Cache", "__pycache__",
              "node_modules", ".cache"}


def _ignore(dirpath, names):
    out = set()
    for n in names:
        p = os.path.join(dirpath, n)
        if n in SKIP_NAMES:
            out.add(n)
            continue
        try:
            st = os.lstat(p)
            if stat.S_ISSOCK(st.st_mode) or stat.S_ISFIFO(st.st_mode):
                out.add(n)
        except OSError:
            out.add(n)
    return out


def copy_into(src: Path, dst: Path, log: Log) -> int:
    """Copia src -> dst preservando permisos y symlinks. Regresa bytes copiados."""
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_symlink() and not src.is_dir():
            if dst.is_symlink() or dst.exists():
                dst.unlink()
            os.symlink(os.readlink(src), dst)
            return 0
        if src.is_dir():
            shutil.copytree(src, dst, symlinks=True, ignore=_ignore,
                            ignore_dangling_symlinks=True, dirs_exist_ok=True)
            return dir_size(dst)
        shutil.copy2(src, dst)
        return dst.stat().st_size
    except (OSError, shutil.Error) as e:
        log(f"  ⚠ No se pudo copiar {src}: {e}")
        return 0


def dir_size(p: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(p):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


def find_7z() -> str | None:
    for prog in ("7zz", "7z", "7za"):
        if have(prog):
            return prog
    return None


def write_package_lists(base: Path, log: Log, excluded: set[str] | None = None,
                        app_rows: list[dict] | None = None) -> None:
    """Escribe las listas para restore.sh, quitando lo que desmarcaste en «Aplicaciones»."""
    excluded = excluded or set()
    pk = base / "packages"
    pk.mkdir(parents=True, exist_ok=True)
    log("• Leyendo paquetes apt…")
    apt = set(apt_manual_packages())
    if app_rows:
        apt |= {r["id"] for r in app_rows if r["kind"] == "apt"}
    skipped = {p for p in apt if f"apt:{p}" in excluded}
    apt -= skipped
    (pk / "apt-manual.txt").write_text("\n".join(sorted(apt)) + "\n")
    if skipped:
        log(f"    {len(skipped)} paquetes apt desmarcados no se incluyen")
    log("• Leyendo Flatpaks…")
    fl = [(a, o) for a, o in flatpak_apps() if f"flatpak:{a}" not in excluded]
    (pk / "flatpak.txt").write_text("".join(f"{a}\t{o}\n" for a, o in fl))
    (pk / "flatpak-remotes.txt").write_text(
        "".join(f"{n}\t{u}\n" for n, u in flatpak_remotes()))
    log("• Leyendo Snaps…")
    sn = [x for x in snap_apps() if f"snap:{x[0]}" not in excluded]
    (pk / "snap.txt").write_text(
        "".join(f"{n}\t{c}\t{'classic' if cl else ''}\n" for n, c, cl in sn))
    log("• Leyendo extensiones de VS Code…")
    (pk / "vscode-extensions.txt").write_text("\n".join(vscode_extensions()) + "\n")
    log("• Detectando apps importantes…")
    apps = detect_apps(fl, sn)
    (pk / "apps.txt").write_text("".join(
        f"{a['name']}\t{a['source']}\t{a['id']}\t{a['version']}\n"
        for a in apps if a["installed"]))
    (pk / "apt-sources.txt").write_text("\n".join(apt_sources()) + "\n")
    (pk / "themes.txt").write_text("".join(
        f"{s}\t{k}\t{v}\n" for s, k, v in theme_settings()))
    shell = os.environ.get("SHELL", "")
    (pk / "shell.txt").write_text(f"SHELL={os.path.basename(shell)}\n")


def create_backup(selected: Iterable[str], dest_dir: str, password: str,
                  split_limit: int, custom_paths: list[str],
                  password_files: list[str], log: Log,
                  progress: Callable[[float], None] = lambda x: None,
                  apps_excluded: Iterable[str] = (),
                  themes_selected: list[str] | None = None) -> list[Path]:
    """Crea el respaldo. Regresa la lista de archivos .7z (o volúmenes)."""
    selected = set(selected)
    seven = find_7z()
    if not seven:
        raise RuntimeError("No encontré 7-Zip. Instálalo con: sudo apt install 7zip "
                           "(o p7zip-full en versiones viejas).")
    if not password:
        raise RuntimeError("Pon una contraseña para cifrar el respaldo.")

    stamp = _dt.datetime.now().strftime("%Y-%m-%d_%H%M")
    host = os.uname().nodename
    name = f"respaldo_{host}_{stamp}"
    dest = Path(dest_dir).expanduser()
    dest.mkdir(parents=True, exist_ok=True)

    work = Path(tempfile.mkdtemp(prefix="guardian-"))
    os.chmod(work, 0o700)
    base = work / name
    (base / "home").mkdir(parents=True)
    manifest = {"created": stamp, "host": host, "user": os.environ.get("USER", ""),
                "distro": distro_name(), "categories": sorted(selected),
                "files": []}
    excluded = set(apps_excluded)

    def stash(src: Path) -> int:
        """Copia src: lo de $HOME va a home/, lo demás a rootfs/ (se restaura con sudo)."""
        try:
            target = base / "home" / src.relative_to(HOME)
        except ValueError:
            target = base / "rootfs" / str(src).lstrip("/")
        manifest["files"].append(str(src))
        return copy_into(src, target, log)

    try:
        progress(0.05)
        app_rows = None
        if "apps" in selected:
            log("• Leyendo todas las aplicaciones instaladas…")
            app_rows = list_applications()
        if "packages" in selected or "apps" in selected:
            write_package_lists(base, log, excluded, app_rows)
        if app_rows:
            manual = [r for r in app_rows if r["kind"] == "manual" and r["key"] not in excluded]
            if manual:
                log(f"• Copiando {len(manual)} apps manuales (AppImage, /opt…)")
            lines = []
            for r in manual:
                total = sum(stash(Path(p)) for p in r["paths"] if Path(p).exists())
                lines.append(f"{r['name']}\t" + "\t".join(r["paths"]))
                log(f"    {r['name']}  ({human(total)})")
            (base / "packages" / "manual-apps.txt").write_text("\n".join(lines) + "\n")
        progress(0.15)

        if "themes" in selected:
            log("• Temas, íconos, cursores, fuentes y SDDM…")
            items = list_themes()
            chosen = set(themes_selected if themes_selected is not None
                         else default_theme_selection(items))
            pkgs = set()
            for it in items:
                if it["path"] not in chosen:
                    continue
                if it["scope"] == "sistema" and it["package"]:
                    pkgs.add(it["package"])
                    log(f"    {it['kind']}: {it['name']}  → se reinstala con apt ({it['package']})")
                else:
                    n = stash(Path(it["path"]))
                    log(f"    {it['kind']}: {it['name']}  ({human(n)})")
            (base / "packages").mkdir(exist_ok=True)
            (base / "packages" / "theme-packages.txt").write_text("\n".join(sorted(pkgs)) + "\n")
            sddm = sddm_config_files()
            if sddm:
                d = base / "packages" / "sddm"
                d.mkdir(parents=True, exist_ok=True)
                for f in sddm:
                    copy_into(f, d / ("sddm.conf" if f.name == "sddm.conf" and
                                      f.parent == Path("/etc") else f"conf.d/{f.name}"), log)
                log(f"    configuración de SDDM ({len(sddm)} archivo(s))")
        progress(0.2)

        for cat in CATEGORIES:
            if cat.key not in selected or not cat.paths:
                continue
            log(f"• Copiando: {cat.label}")
            for rel in cat.paths:
                src = HOME / rel
                if src.exists() or src.is_symlink():
                    n = copy_into(src, base / "home" / rel, log)
                    manifest["files"].append(rel)
                    log(f"    {rel}  ({human(n)})")

        if "desktop" in selected and have("dconf"):
            log("• Volcando ajustes de dconf…")
            code, out = run(["dconf", "dump", "/"], timeout=60)
            if code == 0:
                (base / "packages").mkdir(exist_ok=True)
                (base / "packages" / "dconf.ini").write_text(out)

        if "passwords" in selected and password_files:
            log("• Agregando contraseñas exportadas…")
            sec = base / "secrets"
            sec.mkdir()
            os.chmod(sec, 0o700)
            for f in password_files:
                p = Path(f).expanduser()
                if p.is_file():
                    copy_into(p, sec / p.name, log)
                    log(f"    {p.name}")

        if "custom" in selected and custom_paths:
            log("• Copiando rutas personalizadas…")
            for f in custom_paths:
                p = Path(f).expanduser().resolve()
                if not p.exists():
                    continue
                if p.suffix == ".deb" and p.is_file():
                    copy_into(p, base / "debs" / p.name, log)
                    log(f"    .deb → debs/{p.name}")
                    continue
                n = stash(p)
                log(f"    {p}  ({human(n)})")

        # Script de restauración y manifiesto
        shutil.copy2(APP_DIR / "restore.sh", base / "restore.sh")
        os.chmod(base / "restore.sh", 0o755)
        (base / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
        (base / "LEEME.txt").write_text(
            "Para restaurar en un sistema nuevo:\n"
            "  1) 7z x respaldo.7z            (o 7z x respaldo.7z.001 si viene en partes)\n"
            "  2) cd respaldo_*/ && ./restore.sh\n"
            "     ./restore.sh --yes  instala todo sin preguntar\n")
        progress(0.45)

        raw = dir_size(base)
        log(f"• Tamaño sin comprimir: {human(raw)}")
        archive = dest / f"{name}.7z"
        log("• Comprimiendo con 7-Zip (LZMA2, -mx=9, cifrado AES-256)…")
        cmd = [seven, "a", "-t7z", "-m0=lzma2", "-mx=9", "-mfb=273", "-md=64m",
               "-ms=on", "-mmt=on", "-mhe=on", f"-p{password}", "-bsp1", "-bso0",
               str(archive), name]
        p = subprocess.Popen(cmd, cwd=work, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, bufsize=1)
        buf = ""
        assert p.stdout
        while True:
            ch = p.stdout.read(1)
            if not ch:
                break
            if ch in "\r\n\b":
                m = re.search(r"(\d+)%", buf)
                if m:
                    progress(0.45 + 0.5 * int(m.group(1)) / 100)
                buf = ""
            else:
                buf += ch
        if p.wait() != 0:
            raise RuntimeError("7-Zip falló al comprimir.")
        size = archive.stat().st_size
        log(f"✔ Comprimido: {archive.name}  ({human(size)}, "
            f"{100 * size / max(raw, 1):.0f}% del original)")

        parts = split_file(archive, split_limit, log) if size > split_limit else [archive]
        progress(1.0)
        return parts
    finally:
        shutil.rmtree(work, ignore_errors=True)


def split_file(path: Path, limit: int, log: Log) -> list[Path]:
    """Parte el .7z en volúmenes .7z.001, .7z.002… (formato que 7-Zip une solo)."""
    parts = []
    with open(path, "rb") as f:
        i = 1
        while True:
            part = path.with_name(f"{path.name}.{i:03d}")
            written = 0
            with open(part, "wb") as out:
                while written < limit:
                    chunk = f.read(min(8 * MiB, limit - written))
                    if not chunk:
                        break
                    out.write(chunk)
                    written += len(chunk)
            if written == 0:
                part.unlink()
                break
            parts.append(part)
            i += 1
    path.unlink()
    log(f"• Pesaba más del límite: se partió en {len(parts)} volúmenes "
        f"({', '.join(p.name for p in parts)}).")
    log("  Para extraer: 7z x " + parts[0].name)
    return parts


# ----------------------------------------------------------------------
# Telegram
# ----------------------------------------------------------------------
# ----------------------------------------------------------------------
# MyHomeLab: subir el respaldo a la Raspberry (ella lo reenvía al bot)
# ----------------------------------------------------------------------
def _homelab_curl(cfg: dict, extra: list[str], timeout: int) -> tuple[int, str]:
    """curl con el token por stdin (no aparece en `ps`)."""
    conf = [f'header = "Authorization: Bearer {cfg.get("homelab_token", "")}"']
    if cfg.get("homelab_ca"):
        conf.append(f'cacert = "{cfg["homelab_ca"]}"')
    else:
        conf.append("insecure")  # certificado local de Caddy sin instalar
    conf += extra
    try:
        p = subprocess.run(["curl", "-sS", "-K", "-", "-w", "\\n%{http_code}"], input="\n".join(conf),
                           capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return 0, str(e)
    body, _, code = (p.stdout or "").rpartition("\n")
    return int(code) if code.strip().isdigit() else 0, (body or p.stderr).strip()


def homelab_upload(cfg: dict, files: list[Path], log: Log) -> bool:
    url, dev = cfg.get("homelab_url", "").rstrip("/"), cfg.get("homelab_device", "")
    if not (url and dev and cfg.get("homelab_token")):
        log("✘ Configura URL, dispositivo y token del homelab (pestaña Telegram).")
        return False
    session = _dt.datetime.now().strftime("%Y%m%d%H%M%S")
    for i, f in enumerate(files, 1):
        log(f"• Subiendo {f.name} ({human(f.stat().st_size)}) [{i}/{len(files)}]…")
        target = f"{url}/api/backup/{urllib.parse.quote(dev)}/{urllib.parse.quote(f.name)}"
        code, out = _homelab_curl(cfg, [f'url = "{target}"', f'upload-file = "{f}"',
                                        f'header = "X-Backup-Session: {session}"'], 7200)
        if code != 200:
            log(f"✘ El homelab respondió {code}: {out[:300]}")
            return False
        log(f"✔ {f.name} guardado en la Pi")
    return True


def homelab_check(cfg: dict) -> tuple[bool, str]:
    url = cfg.get("homelab_url", "").rstrip("/")
    if not url:
        return False, "Falta la URL del homelab."
    code, out = _homelab_curl(cfg, [f'url = "{url}/api/backup/{cfg.get("homelab_device", "x")}/.prueba"',
                                    'request = "GET"'], 20)
    if code == 200:
        return True, f"El homelab responde y aceptó tu token ({url})."
    if code == 401:
        return False, "El homelab responde, pero el token o el nombre del dispositivo no coinciden."
    return False, f"No responde ({code}): {out[:200]}"


def telegram_limit(cfg: dict) -> int:
    if cfg.get("telegram_mode") == "user":
        return LIMIT_2GB
    base = cfg.get("api_base", "").rstrip("/")
    return LIMIT_BOT_PUBLIC if base in ("", "https://api.telegram.org") else LIMIT_2GB


def _curl_bot(cfg: dict, method: str, fields: dict[str, str], timeout: int = 3600) -> tuple[bool, str]:
    """Llama a la Bot API con curl. El token va por stdin (no aparece en `ps`)."""
    base = cfg.get("api_base", "https://api.telegram.org").rstrip("/")
    conf = [f'url = "{base}/bot{cfg["bot_token"]}/{method}"']
    for k, v in fields.items():
        v = v.replace("\\", "\\\\").replace('"', '\\"')
        conf.append(f'form = "{k}={v}"')
    try:
        p = subprocess.run(["curl", "-sS", "--fail-with-body", "-K", "-"],
                           input="\n".join(conf), capture_output=True, text=True,
                           timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    out = (p.stdout or "") + (p.stderr or "")
    try:
        ok = json.loads(p.stdout).get("ok", False)
    except ValueError:
        ok = False
    return ok, out.replace(cfg["bot_token"], "<token>")


def telegram_send_message(cfg: dict, text: str) -> tuple[bool, str]:
    if cfg.get("telegram_mode") == "user":
        return _telethon(cfg, None, text, lambda s: None)
    if not (cfg.get("bot_token") and cfg.get("chat_id")):
        return False, "Falta el token del bot o el chat_id."
    return _curl_bot(cfg, "sendMessage", {"chat_id": cfg["chat_id"], "text": text}, 30)


def telegram_send_files(cfg: dict, files: list[Path], log: Log) -> bool:
    limit = telegram_limit(cfg)
    for f in files:
        if f.stat().st_size > limit:
            log(f"✘ {f.name} pesa más que el límite de este modo ({human(limit)}).")
            return False
    if cfg.get("telegram_mode") == "user":
        ok = True
        for i, f in enumerate(files, 1):
            r, msg = _telethon(cfg, f, f"Respaldo {i}/{len(files)}: {f.name}", log)
            log(("✔ Enviado " if r else "✘ Error enviando ") + f.name + ("" if r else f": {msg}"))
            ok = ok and r
        return ok
    if not (cfg.get("bot_token") and cfg.get("chat_id")):
        log("✘ Configura el bot de Telegram primero (pestaña Telegram).")
        return False
    for i, f in enumerate(files, 1):
        log(f"• Enviando {f.name} ({human(f.stat().st_size)}) [{i}/{len(files)}]…")
        ok, out = _curl_bot(cfg, "sendDocument", {
            "chat_id": cfg["chat_id"],
            "caption": f"🛡 Respaldo {i}/{len(files)}: {f.name}",
            "document": "@" + str(f),
        })
        if not ok:
            log(f"✘ Telegram respondió: {out[:300]}")
            return False
        log(f"✔ Enviado {f.name}")
    return True


def _telethon(cfg: dict, file: Path | None, text: str, log: Log) -> tuple[bool, str]:
    """Envía a 'Mensajes guardados' de tu cuenta (o al chat_id si lo pusiste)."""
    try:
        from telethon.sync import TelegramClient  # type: ignore
    except ImportError:
        return False, "Telethon no está instalado en el venv: ~/.local/share/linux-guardian/venv/bin/pip install telethon"
    if not TELETHON_SESSION.with_suffix(".session").exists():
        return False, "Primero inicia sesión (botón «Iniciar sesión en Telegram»)."
    try:
        target = cfg.get("chat_id") or "me"
        if isinstance(target, str) and target.lstrip("-").isdigit():
            target = int(target)
        last = [-1]

        def cb(sent, total):
            pct = int(100 * sent / max(total, 1))
            if pct >= last[0] + 10:
                last[0] = pct
                log(f"    {pct}%")

        with TelegramClient(str(TELETHON_SESSION), int(cfg["api_id"]), cfg["api_hash"]) as c:
            if file is None:
                c.send_message(target, text)
            else:
                c.send_file(target, str(file), caption=text, progress_callback=cb,
                            force_document=True)
        return True, "ok"
    except Exception as e:  # noqa: BLE001 - mostrar cualquier error al usuario
        return False, str(e)


def telethon_login_cli() -> None:
    """Login interactivo en terminal (pide teléfono y código una sola vez)."""
    cfg = load_config()
    try:
        from telethon.sync import TelegramClient  # type: ignore
    except ImportError:
        print("Instala Telethon en el venv:  ~/.local/share/linux-guardian/venv/bin/pip install telethon")
        input("Enter para salir…")
        return
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with TelegramClient(str(TELETHON_SESSION), int(cfg["api_id"]), cfg["api_hash"]) as c:
        me = c.get_me()
        print(f"\n✔ Sesión iniciada como {me.first_name} (@{me.username}).")
        c.send_message("me", "🛡 Linux Guardian conectado. Aquí llegarán tus respaldos.")
    os.chmod(TELETHON_SESSION.with_suffix(".session"), 0o600)
    input("Listo. Enter para cerrar…")


# ----------------------------------------------------------------------
# Recordatorio trimestral (systemd --user, con respaldo en cron)
# ----------------------------------------------------------------------
UNIT_DIR = HOME / ".config" / "systemd" / "user"
UNIT = "linux-guardian-reminder"


def reminder_install() -> str:
    # Usa el mismo intérprete que corre el programa (el del venv si se instaló así)
    import sys
    py = sys.executable or shutil.which("python3") or "/usr/bin/python3"
    script = APP_DIR / "guardian.py"
    if have("systemctl"):
        UNIT_DIR.mkdir(parents=True, exist_ok=True)
        (UNIT_DIR / f"{UNIT}.service").write_text(
            "[Unit]\nDescription=Linux Guardian: recordatorio de respaldo\n\n"
            f"[Service]\nType=oneshot\nExecStart={py} {script} --remind\n")
        (UNIT_DIR / f"{UNIT}.timer").write_text(
            "[Unit]\nDescription=Revisa a diario si ya toca respaldo (cada 3 meses)\n\n"
            "[Timer]\nOnCalendar=*-*-* 12:00:00\nOnStartupSec=10min\nPersistent=true\n\n"
            "[Install]\nWantedBy=timers.target\n")
        run(["systemctl", "--user", "daemon-reload"])
        code, out = run(["systemctl", "--user", "enable", "--now", f"{UNIT}.timer"])
        if code == 0:
            return "Recordatorio activado (systemd). Revisa a diario y avisa si pasaron 90 días."
        return f"systemd falló: {out}"
    if have("crontab"):
        _, cur = run(["crontab", "-l"])
        lines = [l for l in cur.splitlines() if "guardian.py --remind" not in l
                 and not l.startswith("no crontab")]
        lines.append(f"0 12 * * * DISPLAY=:0 {py} {script} --remind")
        p = subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", text=True)
        return "Recordatorio activado (cron)." if p.returncode == 0 else "cron falló."
    return "No encontré systemd ni cron."


def reminder_remove() -> str:
    if have("systemctl"):
        run(["systemctl", "--user", "disable", "--now", f"{UNIT}.timer"])
        for ext in ("service", "timer"):
            (UNIT_DIR / f"{UNIT}.{ext}").unlink(missing_ok=True)
        run(["systemctl", "--user", "daemon-reload"])
    if have("crontab"):
        _, cur = run(["crontab", "-l"])
        lines = [l for l in cur.splitlines() if "guardian.py --remind" not in l
                 and not l.startswith("no crontab")]
        subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", text=True)
    return "Recordatorio desactivado."


def reminder_status() -> str:
    if have("systemctl"):
        code, out = run(["systemctl", "--user", "is-active", f"{UNIT}.timer"])
        if out.strip() == "active":
            _, nxt = run(["systemctl", "--user", "list-timers", f"{UNIT}.timer", "--no-legend"])
            return "Activo (systemd). Próxima revisión: " + (" ".join(nxt.split()[:3]) or "?")
    if have("crontab"):
        _, cur = run(["crontab", "-l"])
        if "guardian.py --remind" in cur:
            return "Activo (cron), revisión diaria a las 12:00."
    return "Inactivo."


def days_since_backup(cfg: dict) -> int | None:
    if not cfg.get("last_backup"):
        return None
    try:
        d = _dt.date.fromisoformat(cfg["last_backup"][:10])
        return (_dt.date.today() - d).days
    except ValueError:
        return None


def notify(title: str, body: str) -> None:
    if have("notify-send"):
        run(["notify-send", "-u", "critical", "-i", "drive-harddisk", "-a",
             "Linux Guardian", title, body], timeout=10)


def remind_cli(force: bool = False) -> None:
    """Lo ejecuta el temporizador: avisa solo si ya pasaron N días."""
    cfg = load_config()
    days = days_since_backup(cfg)
    limit = int(cfg.get("remind_days", 90))
    today = _dt.date.today().isoformat()
    if not force:
        if days is not None and days < limit:
            return
        if cfg.get("last_reminder") == today:
            return
    msg = ("Nunca has hecho un respaldo con Linux Guardian." if days is None
           else f"Tu último respaldo fue hace {days} días.")
    notify("🛡 Hora de respaldar tu Linux", msg + " Abre Linux Guardian y dale a «Crear respaldo».")
    if cfg.get("bot_token") or cfg.get("telegram_mode") == "user":
        telegram_send_message(cfg, "🛡 Recordatorio: " + msg)
    cfg["last_reminder"] = today
    save_config(cfg)


# ----------------------------------------------------------------------
# Timeshift (instantáneas del sistema = "máquina del tiempo" del sistema)
# ----------------------------------------------------------------------
def timeshift_available() -> bool:
    return have("timeshift")


def timeshift_snapshot_cmd(comment: str) -> list[str]:
    return ["pkexec", "timeshift", "--create", "--comments", comment, "--tags", "O"]


def timeshift_list() -> str:
    code, out = run(["pkexec", "timeshift", "--list"], timeout=120)
    return out if code == 0 else "No se pudieron listar las instantáneas:\n" + out
