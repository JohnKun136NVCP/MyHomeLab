#!/usr/bin/env bash
# ======================================================================
#  En la Pi: ¿por qué Prometheus dice que el VPS está abajo?
#    ./hl vps            revisa homelab.yml, el túnel, el puerto 9100 y el error de Prometheus
#    ./hl vps IP:9100    agrega/corrige el VPS en homelab.yml con esa dirección
#  En el VPS corre la otra mitad:  ./hosts/vps/check.sh --fix
# ======================================================================
set -uo pipefail
. "$(dirname "$(readlink -f "$0")")/lib/flags.sh"; hl_flags "$@"; set -- "${HL_ARGS[@]}"   # opciones en inglés (acepta las de antes en español)
cd "$(dirname "$0")/.."
G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; B=$'\e[1m'; N=$'\e[0m'
ok() { echo "  ${G}✔${N} $*"; }; bad() { echo "  ${R}✘${N} $*"; BAD=1; }; tip() { echo "      → $*"; }
BAD=0
if docker info >/dev/null 2>&1; then D=(docker); else D=(sudo docker); fi
C=("${D[@]}" compose -f hosts/pi/docker-compose.yml --env-file .env)

nodes_vps() { python3 - <<'PY'
import yaml
c = yaml.safe_load(open("config/homelab.yml")) or {}
for n in (c.get("infrastructure") or {}).get("nodes") or []:
    if "vps" in str(n.get("name", "")).lower():
        print(n.get("name"), n.get("target"))
PY
}

if [[ -n "${1:-}" ]]; then        # ./hl vps 10.66.0.1:9100
  T="$1"; [[ $T == *:* ]] || T="$T:9100"
  python3 - "$T" <<'PY'
import sys, yaml
p = "config/homelab.yml"; c = yaml.safe_load(open(p)) or {}
inf = c.setdefault("infrastructure", {})
nodes = [n for n in inf.get("nodes") or [] if "vps" not in str(n.get("name", "")).lower()]
nodes.append({"name": "vps", "target": sys.argv[1]})
inf["nodes"] = nodes
ip = sys.argv[1].rsplit(":", 1)[0]
pings = [p_ for p_ in inf.get("ping_targets") or [] if p_.get("name") != "vps"] + [{"name": "vps", "host": ip}]
inf["ping_targets"] = pings
with open(p, "w") as f:
    yaml.safe_dump(c, f, sort_keys=False, allow_unicode=True)
print(f"  ✔ homelab.yml: vps → {sys.argv[1]} (Prometheus y ping)")
PY
  "${C[@]}" restart netwatch >/dev/null 2>&1 && echo "  ✔ Panel reiniciado: Prometheus ve la lista nueva en ~1 minuto."
fi

echo "${B}== 1. ¿Está el VPS en homelab.yml?${N}"
read -r NAME TARGET < <(nodes_vps | head -1)
if [[ -z ${TARGET:-} ]]; then
  bad "No hay ningún nodo «vps» en infrastructure.nodes"
  tip "./hl vps IP-DEL-VPS-EN-EL-TÚNEL:9100     (ej. ./hl vps 10.66.0.1:9100)"
  tip "o en el panel: VPN → «Monitorear el VPS»"
  exit 1
fi
ok "$NAME → $TARGET"
IP="${TARGET%:*}"; PORT="${TARGET##*:}"
if ip -4 -o addr 2>/dev/null | awk '{print $4}' | grep -q "^$IP/"; then
  bad "$IP es la IP de ESTA máquina (la Pi), no la del VPS"
  tip "Pon la IP del VPS dentro del túnel:  ./hl vps IP-DEL-VPS:9100   (ver: sudo wg show → endpoint/allowed ips del peer)"
fi
case "$IP" in
  10.*|172.1[6-9].*|172.2[0-9].*|172.3[01].*|192.168.*|100.*) ;;
  *) bad "$IP parece la IP PÚBLICA del VPS"; tip "Usa la IP del VPS DENTRO del túnel (en el VPS: sudo wg show / ip -4 addr). node-exporter no escucha en la pública." ;;
esac

echo "${B}== 2. ¿Llega el túnel?${N}"
if ! sudo wg show 2>/dev/null | grep -q interface; then bad "No veo ningún túnel WireGuard activo en la Pi"; tip "sudo systemctl status wg-quick@wg-hoshimy"; fi
hs="$(sudo wg show all latest-handshakes 2>/dev/null | awk -v now="$(date +%s)" '$3>0 {print $1, now-$3}' | sort -k2 -n | head -1)"
[[ -n $hs ]] && { read -r ifc age <<<"$hs"; ((age < 180)) && ok "Túnel $ifc vivo (último contacto hace ${age}s)" || bad "Túnel $ifc sin contacto hace ${age}s"; }
route="$(ip -4 route get "$IP" 2>/dev/null | head -1)"
[[ $route == *" dev wg"* || $route == *hoshimy* ]] && ok "La ruta a $IP va por el túnel ($route)" || { bad "La ruta a $IP no va por el túnel: $route"; tip "Revisa AllowedIPs del peer del VPS en la Pi"; }
if ping -c 2 -W 2 "$IP" >/dev/null 2>&1; then ok "Ping a $IP"; else echo "  ${Y}⚠${N} Sin ping a $IP (puede ser normal si el VPS no responde ping)"; fi

echo "${B}== 3. ¿Contesta el puerto $PORT?${N}"
if timeout 4 bash -c "</dev/tcp/$IP/$PORT" 2>/dev/null; then
  ok "Desde la Pi se abre $IP:$PORT"
  curl -fsS -m 5 "http://$TARGET/metrics" 2>/dev/null | grep -q '^node_' && ok "…y da métricas de node-exporter" || bad "…pero no son métricas de node-exporter"
else
  err="$(timeout 4 bash -c "</dev/tcp/$IP/$PORT" 2>&1 || true)"
  if [[ $err == *refused* ]]; then bad "Conexión rechazada: llega al VPS, pero node-exporter no escucha en $IP:$PORT"
    tip "En el VPS: ./hosts/vps/check.sh --fix   (casi siempre VPN_VPS_IP mal o el contenedor no corre)"
  else bad "Sin respuesta (se agota el tiempo): el firewall del VPS lo bloquea o el túnel no pasa"
    tip "En el VPS: ./hosts/vps/check.sh --fix   (agrega la regla de UFW para la Pi)"; fi
fi

echo "${B}== 4. ¿Qué dice Prometheus?${N}"
js="$(curl -fsS -m 5 'http://127.0.0.1:9090/prometheus/api/v1/targets?state=active' 2>/dev/null || true)"
if [[ -z $js ]]; then
  bad "No pude preguntarle a Prometheus (¿está corriendo?)"; tip "./hl ps prometheus"
else
  JS="$js" python3 - "$TARGET" <<'PY'
import json, os, sys
t = sys.argv[1]
GREEN, RED, YEL, END = "\x1b[32m", "\x1b[31m", "\x1b[33m", "\x1b[0m"
d = json.loads(os.environ["JS"])["data"]["activeTargets"]
m = [x for x in d if x.get("labels", {}).get("instance") == "vps" or t in x.get("scrapeUrl", "")]
if not m:
    print(f"  {YEL}⚠{END} Prometheus aún no tiene el VPS en su lista (espera 1 min o: ./hl restart netwatch)")
for x in m:
    h, e = x.get("health"), x.get("lastError") or ""
    mark = f"{GREEN}✔" if h == "up" else f"{RED}✘"
    print(f"  {mark}{END} {x.get('scrapeUrl')}: {h}" + (f" — {e}" if e else ""))
    low = e.lower()
    if "refused" in low:
        print("      → node-exporter no escucha ahí (en el VPS: ./hosts/vps/check.sh --fix)")
    elif "deadline" in low or "timeout" in low:
        print("      → el firewall del VPS o el túnel no deja pasar (en el VPS: ./hosts/vps/check.sh --fix)")
    elif "no route" in low or "unreachable" in low:
        print("      → el contenedor de Prometheus no encuentra ruta: revisa el túnel en la Pi")
PY
fi
echo
((BAD)) && echo "Cuando arregles lo marcado con ${R}✘${N}, vuelve a correr ./hl vps. Prometheus tarda hasta 30 s en verlo." \
  || echo "${G}Todo bien desde la Pi.${N} En Grafana el VPS aparece en el tablero de nodos."
