"""Inventario de Ansible a partir de homelab.yml → machines (lo usa ./hl roles).

machines:
  - name: pi
    host: 192.168.1.69
    user: john
    ssh_port: 22
    local: true                 # la máquina donde corres ./hl roles
    roles: [core]
  - name: minipc
    host: 192.168.1.50
    user: john
    roles: [media, photos, home, dns_backup]
    home: [paperless, navidrome]
  - name: laptop-lab
    host: 192.168.1.60
    user: john
    laptop: true                # no se duerme con la tapa cerrada, límite de batería
    battery_limit: 80
    roles: [lab]
    lab: [wazuh, greenbone]
"""
import json
import sys
from pathlib import Path

import yaml

ROLES = ["core", "media", "photos", "home", "lab", "dns_backup"]
ROLE_INFO = {
    "core": "Panel, bot, DNS, VPN, monitoreo (lo de la Pi)",
    "media": "Jellyfin, Cine en casa, Frigate (cámaras)",
    "photos": "Immich (fotos de la familia)",
    "home": "Herramientas de la casa (home: [paperless, navidrome, …])",
    "lab": "Laboratorio de seguridad (lab: [wazuh, greenbone, targets])",
    "dns_backup": "Segundo AdGuard que copia al principal",
}


def load(cfg: Path) -> list[dict]:
    c = yaml.safe_load(cfg.read_text()) or {}
    ms = c.get("machines") or []
    errs = []
    for m in ms:
        for k in ("name", "host"):
            if not m.get(k):
                errs.append(f"una máquina no tiene «{k}»: {m}")
        for r in m.get("roles") or []:
            if r not in ROLES:
                errs.append(f"{m.get('name')}: rol desconocido «{r}» (usa {', '.join(ROLES)})")
    cores = [m["name"] for m in ms if "core" in (m.get("roles") or [])]
    if ms and len(cores) != 1:
        errs.append(f"debe haber exactamente una máquina con el rol core (hay {len(cores)})")
    dns = [m for m in ms if "dns_backup" in (m.get("roles") or []) and "core" in (m.get("roles") or [])]
    if dns:
        errs.append("dns_backup va en una máquina distinta a la de core")
    if errs:
        raise SystemExit("homelab.yml → machines:\n  - " + "\n  - ".join(errs))
    return ms


def inventory(ms: list[dict], repo_url: str) -> dict:
    core = next((m for m in ms if "core" in (m.get("roles") or [])), {})
    hosts, groups = {}, {r: {"hosts": {}} for r in ROLES + ["laptops"]}
    for m in ms:
        hv = {"ansible_host": m["host"], "ansible_user": m.get("user") or "pi",
              "ansible_port": int(m.get("ssh_port") or 22),
              "home_services": m.get("home") or [], "lab_services": m.get("lab") or [],
              "battery_limit": int(m.get("battery_limit") or 80)}
        if m.get("local"):
            hv["ansible_connection"] = "local"
        hosts[m["name"]] = hv
        for r in m.get("roles") or []:
            groups[r]["hosts"][m["name"]] = {}
        if m.get("laptop"):
            groups["laptops"]["hosts"][m["name"]] = {}
    return {"all": {"hosts": hosts,
                    "vars": {"repo_url": repo_url, "repo_dir": "~/MyHomeLab",
                             "core_name": core.get("name", ""), "core_ip": core.get("host", "")},
                    "children": {g: v for g, v in groups.items() if v["hosts"]}}}


def main() -> int:
    repo = Path(__file__).resolve().parents[2]
    ms = load(repo / "config" / "homelab.yml")
    if sys.argv[1:2] == ["plan"]:
        if not ms:
            print("  No hay máquinas en homelab.yml → machines (mira el ejemplo en config/homelab.example.yml).")
            return 1
        for m in ms:
            extra = []
            if m.get("home"):
                extra.append("casa: " + ", ".join(m["home"]))
            if m.get("lab"):
                extra.append("lab: " + ", ".join(m["lab"]))
            if m.get("laptop"):
                extra.append(f"laptop (batería al {m.get('battery_limit', 80)} %)")
            print(f"  {m['name']:<14} {m['host']:<16} {', '.join(m.get('roles') or []) or '(sin roles)'}"
                  + (f"   [{'; '.join(extra)}]" if extra else ""))
        return 0
    out = repo / "run" / "ansible"
    out.mkdir(parents=True, exist_ok=True)
    url = "https://github.com/JohnKun136NVCP/MyHomeLab.git"
    try:
        import subprocess
        url = subprocess.check_output(["git", "-C", str(repo), "remote", "get-url", "origin"], text=True).strip() or url
    except Exception:  # noqa: BLE001
        pass
    (out / "inventory.yml").write_text(yaml.safe_dump(inventory(ms, url), sort_keys=False, allow_unicode=True))
    print(json.dumps({"ok": True, "machines": len(ms)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
