#!/usr/bin/env bash
# ======================================================================
#  (En la Pi) Registra la PC Linux del homelab:  ./scripts/add-server.sh IP [nombre] [MAC]
#  Lo llama install-server.sh desde la PC por SSH; también se puede correr a mano.
#   • Prometheus/Grafana la monitorean (node-exporter :9100) y hay alerta si se cae.
#   • El dashboard usa su Frigate (cámaras) y muestra Frigate/Jellyfin en Inicio.
#   • Agrega «pc-server» a los equipos que respaldan (genera su token).
#   • Con MAC: botón «Encender» en Inicio y /encender en el bot (Wake-on-LAN).
# ======================================================================
set -euo pipefail
. "$(dirname "$(readlink -f "$0")")/lib/flags.sh"; hl_flags "$@"; set -- "${HL_ARGS[@]}"   # opciones en inglés (acepta las de antes en español)
cd "$(dirname "$0")/.."
IP="${1:?Uso: ./scripts/add-server.sh IP [nombre] [MAC]}"; NAME="${2:-pc}"; MAC="${3:-}"
[[ -f config/homelab.yml ]] || cp config/homelab.example.yml config/homelab.yml
mkdir -p run; cp -a config/homelab.yml "run/homelab.yml.$(date +%Y%m%d-%H%M%S).bak"
python3 - "$IP" "$NAME" "$MAC" <<'PY'
import re, sys, yaml
ip, name, mac = sys.argv[1], sys.argv[2], sys.argv[3].lower()
p = "config/homelab.yml"
c = yaml.safe_load(open(p)) or {}
inf = c.setdefault("infrastructure", {})
nodes = [n for n in inf.get("nodes") or [] if n.get("name") not in ("pc", name)]
nodes.append({"name": "pc", "target": f"{ip}:9100"})
inf["nodes"] = nodes
pings = [t for t in inf.get("ping_targets") or [] if t.get("name") not in ("pc", name)]
pings.append({"name": "pc", "host": ip})
inf["ping_targets"] = pings
cam = c.setdefault("cameras", {})
cam["frigate_url"] = f"http://{ip}:5000"
cam["go2rtc_url"] = f"http://{ip}:1984"
links = c.setdefault("links", [])
import os
immich_here = os.path.exists("hosts/server/immich/.env")      # Immich vive en la Pi: su botón se queda
for lk_name, port, icon in (("Cine en casa", 8097, "🍿"), ("Fotos (Immich)", 2283, "📸"),
                            ("Frigate (cámaras)", 5000, "🎥"), ("Jellyfin (administrar)", 8096, "🎬")):
    if immich_here and lk_name.startswith("Fotos"):
        continue
    for lk in links:
        if lk.get("name") == lk_name or (lk_name.startswith("Jellyfin") and str(lk.get("name", "")).startswith("Jellyfin")):
            lk["url"] = f"http://{ip}:{port}"
            lk["name"] = lk_name
            break
    else:
        links.append({"name": lk_name, "url": f"http://{ip}:{port}", "icon": icon})
if re.fullmatch(r"([0-9a-f]{2}:){5}[0-9a-f]{2}", mac):
    wol = [w for w in c.get("wake_on_lan") or [] if w.get("name") != "PC" and w.get("mac", "").lower() != mac]
    wol.append({"name": "PC", "mac": mac})
    c["wake_on_lan"] = wol
devs = c.setdefault("backups", {}).setdefault("devices", [])
if not any(d.get("name") == "pc-server" for d in devs):
    devs.append({"name": "pc-server", "os": "linux"})
with open(p, "w") as f:                        # mismo archivo (el contenedor lo ve)
    f.write("# Editado por scripts/add-server.sh (copia anterior en run/)\n")
    yaml.safe_dump(c, f, sort_keys=False, allow_unicode=True)
print(f"  homelab.yml: nodo pc={ip}:9100, Frigate http://{ip}:5000, enlaces y equipo pc-server")
PY
./scripts/gen-secrets.sh </dev/null >/dev/null 2>&1 || true          # token BACKUP_TOKEN_PC_SERVER
if docker info >/dev/null 2>&1; then D=(docker); else D=(sudo docker); fi
"${D[@]}" compose -f hosts/pi/docker-compose.yml --env-file .env restart netwatch bot >/dev/null
echo "✅ PC $IP registrada. Token de respaldo para la PC (BACKUP_TOKEN en su .env):"
grep -E '^BACKUP_TOKEN_PC_SERVER=' .env | cut -d= -f2-
