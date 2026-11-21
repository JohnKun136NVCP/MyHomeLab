#!/usr/bin/env bash
# Trampa (honeypot) en la red de la casa con OpenCanary.
#
# La Pi finge ser un NAS viejo con FTP, Telnet, SSH, MySQL, RDP, VNC, Redis y una página de
# acceso. Nadie de la casa tiene por qué tocarlos: si algo se conecta, es un equipo infectado,
# un intruso en tu Wi-Fi o alguien escaneando, y te llega un aviso 🍯 por Telegram y en Eventos.
#
#   sudo ./hl honeypot              enciende (elige puertos libres y abre UFW solo a tus redes)
#   ./hl honeypot status            puertos y los últimos que cayeron
#   ./hl honeypot test              manda un aviso de prueba (para ver que llega a Telegram)
#   sudo ./hl honeypot remove       lo apaga y quita sus reglas
#
# Tu SSH de verdad no se toca (está en otro puerto); si el 22 está libre, el señuelo usa el 22.
# Las conexiones de la propia Pi (inventario, escaneos) no cuentan.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."
REPO="$PWD"
if docker info >/dev/null 2>&1; then D=(docker); else D=(sudo docker); fi
C=("${D[@]}" compose -f hosts/pi/docker-compose.yml --env-file .env)
DIR=run/honeypot
CONF="$DIR/opencanary.conf"
G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; B=$'\e[1m'; N=$'\e[0m'
cmd="${1:-enable}"; shift || true

profiles() { grep -E '^COMPOSE_PROFILES=' .env | tail -1 | cut -d= -f2- | tr -d "\"' "; }
set_profiles() {
  local tmp; tmp="$(mktemp)"
  V="$1" awk 'BEGIN{v=ENVIRON["V"]; d=0} /^COMPOSE_PROFILES=/ {if(!d) print "COMPOSE_PROFILES="v; d=1; next} {print} END{if(!d) print "COMPOSE_PROFILES="v}' .env > "$tmp"
  cat "$tmp" > .env; rm -f "$tmp"
}
lan_nets() {
  python3 - <<'PY'
import yaml
cfg = yaml.safe_load(open("config/homelab.yml")) or {}
fw = cfg.get("firewall") or {}
nets = [n.get("cidr") for n in (cfg.get("network", {}) or {}).get("networks", []) or [] if n.get("cidr")]
for n in fw.get("lan_networks") or nets or ["192.168.1.0/24"]:
    print(n)
PY
}
port_free() { ! ss -Hltn "sport = :$1" 2>/dev/null | grep -q .; }
ufw_drop() {
  command -v ufw >/dev/null || return 0
  local nums; nums=$(ufw status numbered 2>/dev/null | grep -i "# honeypot" | sed -E 's/^\[ *([0-9]+)\].*/\1/' | sort -rn)
  for n in $nums; do ufw --force delete "$n" >/dev/null; done
}

case "$cmd" in
  enable|on|install)
    [[ $EUID -eq 0 ]] || exec sudo "$0" enable "$@"
    mkdir -p "$DIR"
    # señuelo → puerto preferido (si está ocupado, el alterno)
    declare -A WANT=([ssh]="22 2222" [ftp]="21 2121" [telnet]="23 2323" [http]="8008 8088" [mysql]="3306 3307"
                     [rdp]="3389 13389" [vnc]="5900 5901" [redis]="6379 16379" [mssql]="1433 11433")
    declare -A USE=()
    for svc in ssh ftp telnet http mysql rdp vnc redis mssql; do
      for p in ${WANT[$svc]}; do
        if [[ -f $CONF ]] && grep -q "\"$svc.port\": $p," "$CONF"; then USE[$svc]=$p; break; fi   # ya era nuestro
        if port_free "$p"; then USE[$svc]=$p; break; fi
      done
    done
    [[ ${#USE[@]} -gt 0 ]] || { echo "${R}No encontré puertos libres para los señuelos${N}"; exit 1; }
    SVCS="$(for k in "${!USE[@]}"; do echo "$k=${USE[$k]}"; done)" python3 - "$CONF" <<'PY'
import json, os, sys
use = dict(l.split("=") for l in os.environ["SVCS"].split())
conf = {
    "device.node_id": "nas-respaldos",
    "ip.ignorelist": [],
    "logtype.ignorelist": [],
    "git.enabled": False, "httpproxy.enabled": False, "llmnr.enabled": False, "portscan.enabled": False,
    "sip.enabled": False, "smb.enabled": False, "snmp.enabled": False, "ntp.enabled": False,
    "tftp.enabled": False, "tcpbanner.enabled": False,
    "ftp.banner": "FTP server ready",
    "http.banner": "Apache/2.2.22 (Ubuntu)", "http.skin": "nasLogin",
    "ssh.version": "SSH-2.0-OpenSSH_7.4p1 Debian-10+deb9u7",
    "telnet.banner": "nas-respaldos login:", "telnet.honeycreds": [{"username": "admin", "password": "admin"}],
    "mysql.banner": "5.7.33-0ubuntu0.16.04.1",
    "mssql.version": "2012",
    "logger": {"class": "PyLogger", "kwargs": {
        "formatters": {"plain": {"format": "%(message)s"}},
        "handlers": {"file": {"class": "logging.FileHandler", "filename": "/var/tmp/opencanary/opencanary.log"}}}},
}
for svc in ("ssh", "ftp", "telnet", "http", "mysql", "rdp", "vnc", "redis", "mssql"):
    conf[f"{svc}.enabled"] = svc in use
    conf[f"{svc}.port"] = int(use.get(svc, 0)) or {"ssh": 22}.get(svc, 1)
with open(sys.argv[1], "w") as f:
    json.dump(conf, f, indent=2)
PY
    touch "$DIR/opencanary.log"; chmod 644 "$DIR/opencanary.log" "$CONF"
    # UFW: los señuelos solo se ven desde tus redes
    if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
      ufw_drop
      for net in $(lan_nets); do
        for p in "${USE[@]}"; do ufw allow from "$net" to any port "$p" proto tcp comment honeypot >/dev/null; done
      done
      echo "  UFW: señuelos abiertos solo para $(lan_nets | paste -sd' ' -)"
    fi
    pr="$(profiles)"; [[ ",$pr," == *",honeypot,"* ]] || set_profiles "${pr:+$pr,}honeypot"
    chown "$(stat -c %U "$REPO")": "$REPO/.env" "$DIR" 2>/dev/null || true
    "${C[@]}" pull -q honeypot 2>/dev/null || true
    "${C[@]}" up -d honeypot
    "${C[@]}" up -d netwatch >/dev/null      # lee la bitácora de la trampa
    echo "${G}✔${N} Trampa encendida como «nas-respaldos»:"
    for k in "${!USE[@]}"; do printf "     %-7s %s\n" "$k" "${USE[$k]}"; done
    echo "  Prueba desde OTRO equipo (no la Pi):  telnet IP-de-la-Pi ${USE[telnet]:-23}"
    echo "  O sin moverte:  ./hl honeypot test" ;;

  status|estado)
    if ! "${D[@]}" ps --format '{{.Names}}' | grep -q '^homelab-pi-honeypot'; then
      echo "${Y}La trampa está apagada.${N} Enciéndela: sudo ./hl honeypot"; exit 0
    fi
    echo "${G}● Trampa encendida${N}"
    [[ -f $CONF ]] && python3 - "$CONF" <<'PY'
import json, sys
c = json.load(open(sys.argv[1]))
on = [k[:-len(".enabled")] for k in sorted(c) if k.endswith(".enabled") and c[k] and k[:-len(".enabled")] + ".port" in c]
print("  Señuelos: " + ", ".join(f"{s}:{c[s + '.port']}" for s in on))
PY
    echo "  Últimas conexiones:"
    python3 - "$DIR/opencanary.log" <<'PY' || true
import json, sys
rows = []
for line in open(sys.argv[1], errors="replace"):
    try:
        e = json.loads(line)
    except ValueError:
        continue
    if int(e.get("logtype", 0)) >= 2000 and e.get("src_host"):
        rows.append(e)
for e in rows[-15:]:
    extra = {k: v for k, v in (e.get("logdata") or {}).items() if k in ("USERNAME", "PASSWORD", "PATH")}
    print(f"   {e.get('local_time', '')[:19]}  {e['src_host']:>15}  → puerto {e.get('dst_port')}  {extra or ''}")
if not rows:
    print("   (nadie todavía: buena señal)")
PY
    ;;

  test|prueba)
    [[ $EUID -eq 0 ]] || exec sudo "$0" test "$@"
    mkdir -p "$DIR"
    printf '{"dst_host": "", "dst_port": 23, "local_time": "%s", "logdata": {"USERNAME": "prueba", "PASSWORD": "prueba"}, "logtype": 6001, "node_id": "nas-respaldos", "src_host": "192.0.2.123", "src_port": 40000}\n' \
      "$(date '+%F %T')" >> "$DIR/opencanary.log"
    echo "  Escribí una conexión falsa desde 192.0.2.123 (IP de documentación)."
    echo "  En menos de un minuto debe aparecer en Eventos y llegarte «🍯 Honeypot» por Telegram." ;;

  remove|off|disable)
    [[ $EUID -eq 0 ]] || exec sudo "$0" remove "$@"
    pr="$(profiles)"; set_profiles "$(tr ',' '\n' <<<"$pr" | grep -vx honeypot | paste -sd, -)"
    "${C[@]}" --profile honeypot rm -sf honeypot >/dev/null 2>&1 || true
    ufw_drop
    echo "${G}✔${N} Trampa apagada y sus reglas de UFW quitadas (la bitácora queda en $DIR)." ;;

  -h|--help|help) sed -n 2,14p "$0" ;;
  *) echo "Comando desconocido: $cmd"; sed -n 2,14p "$0"; exit 1 ;;
esac
