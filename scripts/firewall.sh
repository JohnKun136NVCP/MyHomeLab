#!/usr/bin/env bash
# ======================================================================
#  Firewall de la Pi (UFW) a partir de config/homelab.yml → firewall:
#    ./hl firewall                 muestra el plan y pregunta antes de aplicar
#    ./hl firewall --yes            aplica sin preguntar
#    ./hl firewall --key-only    además: SSH solo con llave (sin contraseña)
#
#  Qué hace (solo AGREGA reglas; las tuyas, como nginx o el 8090, se quedan):
#   • Entrante bloqueado por defecto, saliente permitido.
#   • SSH solo desde los equipos de firewall.ssh.allow y por firewall.ssh.interface, en el
#     puerto en el que de verdad escucha tu SSH (lo detecta; o firewall.ssh.port en homelab.yml).
#     Quita las reglas «SSH desde cualquier lado» de ese puerto.
#   • DNS (53) desde la casa, el laboratorio y los contenedores (Uptime Kuma).
#   • Dashboard (HTTP_PORT/HTTPS_PORT) desde las redes de la casa.
#   • Con --key-only: revisa que tu llave ya esté instalada ANTES de apagar contraseñas.
#  Nunca te deja fuera: si tu SSH actual no viene de un equipo permitido, se detiene.
# ======================================================================
set -euo pipefail
. "$(dirname "$(readlink -f "$0")")/lib/flags.sh"; hl_flags "$@"; set -- "${HL_ARGS[@]}"   # opciones en inglés (acepta las de antes en español)
cd "$(dirname "$0")/.."
YES=0; KEYONLY=0; FORCE=0
for a in "$@"; do
  case "$a" in
    --yes|-y) YES=1 ;;
    --key-only) KEYONLY=1 ;;
    --force) FORCE=1 ;;
    *) echo "Opción desconocida: $a"; exit 2 ;;
  esac
done
[[ $EUID -eq 0 ]] || exec sudo -E "$0" "$@"
command -v ufw >/dev/null || { echo "Instalando UFW…"; apt-get install -y ufw >/dev/null; }

envv() { grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2- | tr -d "\"'"; }
HTTPS_PORT="$(envv HTTPS_PORT)"; HTTPS_PORT="${HTTPS_PORT:-443}"
HTTP_PORT="$(envv HTTP_PORT)"; HTTP_PORT="${HTTP_PORT:-80}"
PROFILES=",$(envv COMPOSE_PROFILES),"
HL_REPO="$PWD"; . scripts/lib/ssh.sh
mapfile -t SSH_PORTS < <(ssh_ports)

# Lee la sección firewall (y las redes) de homelab.yml
eval "$(python3 - <<'PY'
import shlex, yaml
cfg = yaml.safe_load(open("config/homelab.yml")) or {}
fw = cfg.get("firewall") or {}
nets = [n.get("cidr") for n in (cfg.get("network", {}) or {}).get("networks", []) or [] if n.get("cidr")]
lan = fw.get("lan_networks") or nets or ["192.168.1.0/24"]
ssh = fw.get("ssh") or {}
allow = []
for x in ssh.get("allow") or []:
    allow.append(str(x.get("ip") if isinstance(x, dict) else x))
names = [str(x.get("nombre") or x.get("name") or x.get("ip")) if isinstance(x, dict) else str(x)
         for x in ssh.get("allow") or []]
q = lambda v: " ".join(shlex.quote(str(i)) for i in v)
print(f"LAN=({q(lan)})")
print(f"SSH_ALLOW=({q(allow)})")
print(f"SSH_NAMES=({q(names)})")
print(f"SSH_IFACE={shlex.quote(str(ssh.get('interface') or ''))}")
print(f"EXTRA=({q(fw.get('extra') or [])})")
PY
)"

if ((${#SSH_ALLOW[@]} == 0)); then
  cat <<EOF
✘ No hay equipos para SSH en config/homelab.yml. Agrega algo así y vuelve a correrlo:

firewall:
  lan_networks: [192.168.1.0/24, 192.168.100.0/24]
  ssh:
    interface: wlan0                 # solo por la red del laboratorio
    allow:
      - {ip: 192.168.100.10, nombre: laptop}
      - {ip: 192.168.100.11, nombre: pc-lab}
      - {ip: 192.168.100.12, nombre: tablet}

(Pon IPs fijas a esos 3 equipos en la Tenda del laboratorio: reserva DHCP.)
EOF
  exit 1
fi

# ¿Desde dónde estás conectado ahora? (para no dejarte fuera)
MYIP="${SSH_CLIENT%% *}"; MYIP="${MYIP:-${SSH_CONNECTION%% *}}"
if [[ -n "$MYIP" ]] && ! printf '%s\n' "${SSH_ALLOW[@]}" | grep -qxF "$MYIP"; then
  echo "⚠ Estás conectado por SSH desde $MYIP, que NO está en firewall.ssh.allow."
  echo "  Si aplico esto se cortaría tu sesión. Agrégala o corre esto desde un equipo permitido."
  ((FORCE)) || exit 1
fi

PLAN=()
plan() { PLAN+=("$(printf '%q ' "$@")"); }
plan default deny incoming
plan default allow outgoing
for sp in "${SSH_PORTS[@]}"; do
  for i in "${!SSH_ALLOW[@]}"; do
    ip="${SSH_ALLOW[$i]}"
    if [[ -n "$SSH_IFACE" ]]; then
      plan allow in on "$SSH_IFACE" from "$ip" to any port "$sp" proto tcp comment "SSH ${SSH_NAMES[$i]}"
    else
      plan allow from "$ip" to any port "$sp" proto tcp comment "SSH ${SSH_NAMES[$i]}"
    fi
  done
done
for n in "${LAN[@]}"; do
  plan allow from "$n" to any port "$HTTPS_PORT" proto tcp comment "Dashboard homelab"
  plan allow from "$n" to any port "$HTTP_PORT" proto tcp comment "Dashboard (redirige a https)"
  if [[ "$PROFILES" == *,dns,* ]]; then
    plan allow from "$n" to any port 53 comment "DNS AdGuard"
  fi
  # Normalmente Docker se salta UFW; estas reglas importan si Docker tiene "iptables": false
  [[ -f hosts/server/immich/.env ]] && plan allow from "$n" to any port 2283 proto tcp comment "homelab immich"
  [[ "$PROFILES" == *,speedtest,* ]] && plan allow from "$n" to any port 8765 proto tcp comment "homelab speedtest"
done
if [[ "$PROFILES" == *,dns,* ]]; then
  plan allow from 172.16.0.0/12 to any port 53 comment "DNS desde Docker"
fi
for wg in $(wg show interfaces 2>/dev/null || true); do
  plan allow in on "$wg" to any port 9100 proto tcp comment "node-exporter por VPN"
done
for e in "${EXTRA[@]}"; do plan $e; done

# Reglas de SSH abiertas a todos que se van (IPv4 e IPv6), en cualquiera de sus puertos
pat="OpenSSH"; for sp in "${SSH_PORTS[@]}"; do pat="$sp|$sp/tcp|$pat"; done
mapfile -t OPEN22 < <(ufw status numbered 2>/dev/null | grep -E "\] ($pat)( \(v6\))? +ALLOW IN +Anywhere" | sed -E 's/^\[ *([0-9]+)\].*/\1/' | sort -rn)

echo "SSH de esta máquina en el puerto: ${SSH_PORTS[*]}"
echo "Plan para UFW:"
for p in "${PLAN[@]}"; do echo "  ufw $p"; done
((${#OPEN22[@]})) && echo "  (se borran ${#OPEN22[@]} regla(s) de SSH abiertas a cualquiera)"
((KEYONLY)) && echo "  + SSH solo con llave (sin contraseña ni root)"
echo
echo "Nota: los puertos que publica Docker (AdGuard 3002, Uptime Kuma 3001…) no pasan por UFW;"
echo "      se pueden abrir desde la red local aunque UFW no tenga regla."
if ((!YES)); then
  read -r -p "¿Aplicar? [s/N] " r
  [[ "$r" =~ ^[sSyY] ]] || { echo "No cambié nada."; exit 0; }
fi

for p in "${PLAN[@]}"; do eval "ufw $p" >/dev/null; done
for n in "${OPEN22[@]}"; do yes | ufw delete "$n" >/dev/null; done
ufw logging low >/dev/null
ufw --force enable >/dev/null
echo "✅ UFW aplicado."

if ((KEYONLY)); then
  U="${SUDO_USER:-$(stat -c %U .)}"
  H="$(getent passwd "$U" | cut -d: -f6)"
  AK="$H/.ssh/authorized_keys"
  if [[ ! -s "$AK" ]] || ! grep -qE '^(ssh-|ecdsa-|sk-)' "$AK"; then
    cat <<EOF
✘ $U no tiene llaves en $AK: NO apagué las contraseñas (te quedarías fuera).
  Desde cada uno de tus 3 equipos del laboratorio corre:
     ssh-keygen -t ed25519            (si aún no tienes llave)
     ssh-copy-id -p ${SSH_PORTS[0]} $U@IP-de-la-Pi
  y luego otra vez:  ./hl firewall --key-only
EOF
    exit 1
  fi
  n=$(grep -cE '^(ssh-|ecdsa-|sk-)' "$AK")
  echo "   $U tiene $n llave(s) autorizada(s):"
  awk '/^(ssh-|ecdsa-|sk-)/{print "     · " $1 " … " $NF}' "$AK"
  cat > /etc/ssh/sshd_config.d/10-homelab.conf <<EOF
# MyHomeLab (scripts/firewall.sh --key-only): solo llaves, sin root
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
PubkeyAuthentication yes
EOF
  if sshd -t; then
    systemctl reload ssh 2>/dev/null || systemctl reload sshd
    echo "✅ SSH ya solo acepta llave. NO cierres esta sesión: abre otra terminal y prueba entrar."
    echo "   Si algo falla:  sudo rm /etc/ssh/sshd_config.d/10-homelab.conf && sudo systemctl reload ssh"
  else
    rm -f /etc/ssh/sshd_config.d/10-homelab.conf
    echo "✘ La configuración de SSH tenía un error; la quité y todo quedó como antes."
  fi
fi

mkdir -p run
ufw status verbose > run/firewall.txt 2>&1 || true
echo
ufw status numbered
