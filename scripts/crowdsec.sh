#!/usr/bin/env bash
# ======================================================================
#  CrowdSec en la Pi: detecta ataques (SSH, el panel/Caddy, escaneos) y bloquea esas IPs en el
#  firewall, con la lista que comparte la comunidad. Te avisa por Telegram (sin sonido).
#    sudo ./hl crowdsec                instala y configura
#    sudo ./hl crowdsec status         qué detectó y a quién bloqueó
#    sudo ./hl crowdsec unban IP       quita un bloqueo
#    sudo ./hl crowdsec enroll CLAVE   lo conecta a tu consola web gratis (app.crowdsec.net)
#    sudo ./hl crowdsec remove         lo quita y deja todo como antes
#  No bloquea la red de la casa ni la VPN (lista blanca); la red del laboratorio sí la vigila.
# ======================================================================
set -euo pipefail
. "$(dirname "$(readlink -f "$0")")/lib/flags.sh"; hl_flags "$@"; set -- "${HL_ARGS[@]}"
REPO="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
cd "$REPO"
[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"
G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; B=$'\e[1m'; N=$'\e[0m'
ok() { echo "  ${G}✔${N} $*"; }; warn() { echo "  ${Y}⚠${N} $*"; }; die() { echo "${R}✘${N} $*"; exit 1; }
LAPI=127.0.0.1:8085            # el 8080 ya es del bot
envget() { grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2- | sed 's/\$\$/$/g' || true; }

whitelist_nets() {
  python3 - <<'PY'
import yaml, subprocess
c = yaml.safe_load(open("config/homelab.yml")) or {}
nets = [n["cidr"] for n in (c.get("network") or {}).get("networks") or []
        if n.get("cidr") and "lab" not in str(n.get("name", "")).lower()]
# túneles WireGuard de esta máquina
out = subprocess.run(["ip", "-4", "-o", "addr"], capture_output=True, text=True).stdout
for ln in out.splitlines():
    p = ln.split()
    if p[1].startswith(("wg", "wg-")):
        import ipaddress
        nets.append(str(ipaddress.ip_interface(p[3]).network))
print("\n".join(dict.fromkeys(nets + ["127.0.0.0/8", "172.16.0.0/12"])))
PY
}

case "${1:-install}" in
install)
  echo "${B}== CrowdSec${N}"
  # El 8080 lo usa el bot: CrowdSec escucha en 8085 desde el primer arranque
  mkdir -p /etc/crowdsec
  cat > /etc/crowdsec/config.yaml.local <<YML
api:
  server:
    listen_uri: $LAPI
YML
  if ! command -v cscli >/dev/null; then
    curl -fsSL https://install.crowdsec.net | sh >/dev/null    # repositorio oficial
    apt-get install -y -qq crowdsec >/dev/null || true
  fi
  command -v cscli >/dev/null || die "No se instaló crowdsec (mira: apt-get install crowdsec)"
  touch /etc/crowdsec/.homelab
  sed -i -E "s#^url: .*#url: http://$LAPI/#" /etc/crowdsec/local_api_credentials.yaml 2>/dev/null || true
  # Qué vigila
  cscli collections install crowdsecurity/linux crowdsecurity/sshd crowdsecurity/caddy \
        crowdsecurity/base-http-scenarios crowdsecurity/http-cve >/dev/null 2>&1 || true
  mkdir -p /etc/crowdsec/acquis.d
  cat > /etc/crowdsec/acquis.d/homelab.yaml <<'YML'
# MyHomeLab: SSH (journald) y la bitácora JSON de Caddy (contenedor)
source: journalctl
journalctl_filter: ["_SYSTEMD_UNIT=ssh.service"]
labels: { type: syslog }
---
source: journalctl
journalctl_filter: ["_SYSTEMD_UNIT=sshd.service"]
labels: { type: syslog }
---
source: docker
container_name_regexp: ["homelab-pi-caddy-.*"]
labels: { type: caddy }
YML
  # Lista blanca: la casa y la VPN no se bloquean nunca
  mkdir -p /etc/crowdsec/parsers/s02-enrich
  { echo "name: homelab/whitelist"; echo "description: \"MyHomeLab: no bloquear la red de la casa ni la VPN\""
    echo "whitelist:"; echo "  reason: \"red de la casa / VPN\""; echo "  cidr:"
    whitelist_nets | sed 's/^/    - "/; s/$/"/'; } > /etc/crowdsec/parsers/s02-enrich/homelab-whitelist.yaml
  ok "Lista blanca: $(whitelist_nets | paste -sd' ' -)"
  # Aviso por Telegram (sin sonido) cuando bloquea a alguien
  tok="$(envget INTERNAL_TOKEN)"
  mkdir -p /etc/crowdsec/notifications
  cat > /etc/crowdsec/notifications/http_homelab.yaml <<YML
type: http
name: http_homelab
log_level: info
format: |
  {{ with index . 0 }}{"silent": true, "text": {{ printf "🛑 CrowdSec bloqueó %s (%s) por %s" .Source.IP .Source.Cn .Scenario | toJson }}}{{ end }}
url: http://127.0.0.1:8080/notify
method: POST
headers:
  Authorization: Bearer $tok
  Content-Type: application/json
YML
  chmod 600 /etc/crowdsec/notifications/http_homelab.yaml
  python3 - <<'PY'
import yaml
p = "/etc/crowdsec/profiles.yaml"
docs = [d for d in yaml.safe_load_all(open(p)) if d]
for d in docs:
    if d.get("name") == "default_ip_remediation":
        n = d.setdefault("notifications", []) or []
        if "http_homelab" not in n:
            n.append("http_homelab")
        d["notifications"] = n
open(p, "w").write("---\n".join(yaml.safe_dump(d, sort_keys=False, allow_unicode=True) for d in docs))
PY
  systemctl restart crowdsec
  # El que bloquea en el firewall (nftables)
  if ! dpkg -s crowdsec-firewall-bouncer-nftables >/dev/null 2>&1; then
    apt-get install -y -qq crowdsec-firewall-bouncer-nftables >/dev/null
  fi
  sed -i -E "s#^api_url: .*#api_url: http://$LAPI/#" /etc/crowdsec/bouncers/crowdsec-firewall-bouncer.yaml
  systemctl restart crowdsec-firewall-bouncer
  sleep 3
  systemctl is-active -q crowdsec && ok "CrowdSec vigilando (API en $LAPI)" || warn "CrowdSec no arrancó: journalctl -u crowdsec -n 30"
  systemctl is-active -q crowdsec-firewall-bouncer && ok "Bloqueo en el firewall activo" || warn "El bloqueador no arrancó: journalctl -u crowdsec-firewall-bouncer -n 30"
  # Caddy debe estar escribiendo su bitácora JSON (Caddyfile nuevo)
  ./hl restart caddy >/dev/null 2>&1 || true
  echo
  echo "  Mira qué detecta:   sudo ./hl crowdsec status"
  echo "  Consola web (opcional, gratis): crea cuenta en app.crowdsec.net y  sudo ./hl crowdsec enroll CLAVE" ;;
status|estado)
  command -v cscli >/dev/null || die "CrowdSec no está instalado (sudo ./hl crowdsec)"
  echo "${B}== Bloqueos activos${N}"; cscli decisions list -o human 2>/dev/null | head -30
  echo "${B}== Últimas alertas${N}"; cscli alerts list -l 10 -o human 2>/dev/null
  echo "${B}== Qué está leyendo${N}"; cscli metrics show acquisition 2>/dev/null | head -20 ;;
unban)
  ip="${2:?Falta la IP}"; cscli decisions delete --ip "$ip" && ok "Desbloqueada $ip" ;;
enroll)
  cscli console enroll "${2:?Falta la clave de app.crowdsec.net}" && systemctl restart crowdsec && ok "Acéptalo en app.crowdsec.net" ;;
remove|quitar)
  apt-get purge -y -qq crowdsec-firewall-bouncer-nftables crowdsec >/dev/null 2>&1 || true
  rm -rf /etc/crowdsec; rm -f /etc/apt/sources.list.d/crowdsec_crowdsec.list
  ok "CrowdSec quitado" ;;
*) sed -n 2,11p "$(readlink -f "$0")" | sed -E 's/^# ?//' ;;
esac
