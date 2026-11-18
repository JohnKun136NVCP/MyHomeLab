#!/usr/bin/env bash
# ======================================================================
#  (En la Pi) Registra la PC Linux del homelab:  ./scripts/agregar-servidor.sh IP [nombre]
#  Lo llama install-server.sh desde la PC por SSH; también se puede correr a mano.
#   • Prometheus/Grafana la monitorean (node-exporter :9100) y hay alerta si se cae.
#   • El dashboard usa su Frigate (cámaras) y muestra Frigate/Jellyfin en Inicio.
#   • Agrega «pc-server» a los equipos que respaldan (genera su token).
# ======================================================================
set -euo pipefail
cd "$(dirname "$0")/.."
IP="${1:?Uso: ./scripts/agregar-servidor.sh IP [nombre]}"; NAME="${2:-pc}"
[[ -f config/homelab.yml ]] || cp config/homelab.example.yml config/homelab.yml
mkdir -p run; cp -a config/homelab.yml "run/homelab.yml.$(date +%Y%m%d-%H%M%S).bak"
python3 - "$IP" "$NAME" <<'PY'
import sys, yaml
ip, name = sys.argv[1], sys.argv[2]
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
for lk_name, port, icon in (("Frigate (cámaras)", 5000, "🎥"), ("Jellyfin (streaming)", 8096, "🎬")):
    for lk in links:
        if lk.get("name") == lk_name:
            lk["url"] = f"http://{ip}:{port}"
            break
    else:
        links.append({"name": lk_name, "url": f"http://{ip}:{port}", "icon": icon})
devs = c.setdefault("backups", {}).setdefault("devices", [])
if not any(d.get("name") == "pc-server" for d in devs):
    devs.append({"name": "pc-server", "os": "linux"})
with open(p, "w") as f:                        # mismo archivo (el contenedor lo ve)
    f.write("# Editado por scripts/agregar-servidor.sh (copia anterior en run/)\n")
    yaml.safe_dump(c, f, sort_keys=False, allow_unicode=True)
print(f"  homelab.yml: nodo pc={ip}:9100, Frigate http://{ip}:5000, enlaces y equipo pc-server")
PY
./scripts/gen-secrets.sh </dev/null >/dev/null 2>&1 || true          # token BACKUP_TOKEN_PC_SERVER
if docker info >/dev/null 2>&1; then D=(docker); else D=(sudo docker); fi
"${D[@]}" compose -f hosts/pi/docker-compose.yml --env-file .env restart netwatch bot >/dev/null
echo "✅ PC $IP registrada. Token de respaldo para la PC (BACKUP_TOKEN en su .env):"
grep -E '^BACKUP_TOKEN_PC_SERVER=' .env | cut -d= -f2-
