#!/usr/bin/env bash
# ======================================================================
#  Configura la PC Linux del homelab de una sola vez (Ubuntu/Debian, Fedora o Arch):
#    git clone https://github.com/JohnKun136NVCP/MyHomeLab.git && cd MyHomeLab
#    ./scripts/install-server.sh
#
#  Qué hace (pregunta lo necesario; Enter = valor sugerido):
#   1. Instala Docker + Compose y te agrega al grupo docker.
#   2. Detecta tu GPU: Intel/AMD → aceleración en Jellyfin y Frigate (OpenVINO en Intel);
#      NVIDIA → te dice qué falta.
#   3. Crea .env de la PC (carpetas de películas y grabaciones, contraseñas RTSP).
#   4. Levanta Frigate (cámaras), Jellyfin (streaming) y node-exporter (métricas).
#   5. Firewall: servicios solo para la casa, métricas solo para la Pi, SSH solo laboratorio.
#   6. La registra en la Pi (Grafana, alertas, enlaces del dashboard, URL de Frigate).
#   7. Respaldo semanal cifrado de su configuración → bot del homelab.
#  Se puede volver a correr cuando quieras: no borra nada.
# ======================================================================
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
YES=0; PI=""; PI_PORT=""; PI_SSH=""
while (($#)); do
  case "$1" in
    --si|-y) YES=1 ;;
    --pi) PI="$2"; shift ;;
    --puerto) PI_PORT="$2"; shift ;;
    --pi-ssh) PI_SSH="$2"; shift ;;
    *) echo "Opción desconocida: $1"; exit 2 ;;
  esac
  shift
done
[[ $EUID -ne 0 ]] || { echo "Córrelo como tu usuario normal (pedirá sudo cuando haga falta)."; exit 1; }
B=$'\e[1m'; G=$'\e[32m'; Y=$'\e[33m'; N=$'\e[0m'
step() { echo; echo "${B}== $*${N}"; }
ask() {  # ask VAR "pregunta" "valor sugerido"
  local __v="$1" q="$2" def="${3:-}" a
  if ((YES)); then printf -v "$__v" '%s' "$def"; return; fi
  read -r -p "  $q${def:+ [$def]}: " a
  printf -v "$__v" '%s' "${a:-$def}"
}
envset() {  # envset CLAVE valor  (en .env de la PC, sin cambiar el inodo)
  local k="$1" v="$2" tmp; tmp="$(mktemp)"
  K="$k" V="$v" awk 'BEGIN{k=ENVIRON["K"];v=ENVIRON["V"];d=0} $0 ~ "^"k"=" {if(!d)print k"="v; d=1; next} {print} END{if(!d)print k"="v}' .env > "$tmp"
  cat "$tmp" > .env; rm -f "$tmp"
}
envget() { grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2-; }
rand() { openssl rand -base64 33 | tr -d '/+=\n' | cut -c1-${1:-24}; }

# ---------------------------------------------------------------- 1. Docker
step "1/7 Docker"
. /etc/os-release
case "${ID_LIKE:-$ID}" in
  *debian*|*ubuntu*|debian|ubuntu) PM=apt ;;
  *fedora*|*rhel*|fedora) PM=dnf ;;
  *arch*|arch) PM=pacman ;;
  *) PM=unknown ;;
esac
pkgs() {
  case "$PM" in
    apt) sudo apt-get update -qq && sudo apt-get install -y "$@" ;;
    dnf) sudo dnf install -y "$@" ;;
    pacman) sudo pacman -S --needed --noconfirm "$@" ;;
    *) echo "  Instala a mano: $*" ;;
  esac
}
command -v curl >/dev/null && command -v openssl >/dev/null && command -v ufw >/dev/null || \
  pkgs curl openssl ufw pciutils python3 python3-yaml 2>/dev/null || pkgs curl openssl ufw pciutils python3
if ! command -v docker >/dev/null; then
  if [[ "$PM" == pacman ]]; then pkgs docker docker-compose
  else curl -fsSL https://get.docker.com | sudo sh; fi
fi
sudo systemctl enable --now docker >/dev/null
if ! id -nG "$USER" | grep -qw docker; then
  sudo usermod -aG docker "$USER"
  echo "  ${Y}Te agregué al grupo docker: después de este script cierra sesión y vuelve a entrar.${N}"
fi
D=(sudo docker); docker info >/dev/null 2>&1 && D=(docker)
"${D[@]}" compose version >/dev/null || { echo "Falta docker compose"; exit 1; }
echo "  ${G}✔${N} $("${D[@]}" --version)"

# ---------------------------------------------------------------- 2. GPU
step "2/7 Tarjeta gráfica"
GPU="cpu"; GPU_TXT="$(lspci 2>/dev/null | grep -Ei 'vga|3d|display' || true)"
echo "${GPU_TXT:-  (no pude leer lspci)}" | sed 's/^/  /'
if grep -qi nvidia <<<"$GPU_TXT"; then
  GPU="nvidia"
  echo "  NVIDIA: para usarla instala el driver y nvidia-container-toolkit; por ahora va por CPU."
elif [[ -e /dev/dri/renderD128 ]] && grep -qi intel <<<"$GPU_TXT"; then GPU="intel"
elif [[ -e /dev/dri/renderD128 ]]; then GPU="amd"; fi
echo "  → modo: ${B}$GPU${N}"

# ---------------------------------------------------------------- 3. .env
step "3/7 Configuración (.env de la PC)"
[[ -f .env ]] || { cp hosts/server/.env.example .env 2>/dev/null || touch .env; }
chmod 600 .env
MY_IP="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}')"
echo "  IP de esta PC: ${B}${MY_IP:-?}${N}  (ponle IP fija en el módem: reserva DHCP)"
[[ -n "$PI" ]] || ask PI "IP de la Raspberry" "$(envget HOMELAB_PI || echo 192.168.1.5)"
[[ -n "$PI_PORT" ]] || ask PI_PORT "Puerto HTTPS del dashboard en la Pi" "$(envget HOMELAB_PI_PORT || echo 8443)"
ask MEDIA "Carpeta de películas/series para Jellyfin" "$(envget MEDIA_DIR || echo "$HOME/Videos")"
ask FMEDIA "Carpeta para grabaciones de cámaras (disco grande)" "$(envget FRIGATE_MEDIA || echo /srv/frigate)"
mkdir -p "$MEDIA" 2>/dev/null || sudo mkdir -p "$MEDIA"
sudo mkdir -p "$FMEDIA"; sudo chown "$USER" "$FMEDIA"
if [[ -z "${HL_TODO_EN_UNO:-}" ]] && ! grep -q '^COMPOSE_PROFILES=.*pc-sola' .env; then
  cur="$(envget COMPOSE_PROFILES)"; envset COMPOSE_PROFILES "${cur:+$cur,}pc-sola"
fi
envset HOMELAB_PI "$PI"; envset HOMELAB_PI_PORT "$PI_PORT"; envset SERVER_IP "$MY_IP"
envset MEDIA_DIR "$MEDIA"; envset FRIGATE_MEDIA "$FMEDIA"; envset SERVER_GPU "$GPU"
[[ -n "$(envget FRIGATE_RTSP_USER)" ]] || envset FRIGATE_RTSP_USER admin
[[ -n "$(envget FRIGATE_RTSP_PASSWORD)" ]] || { ask RP "Contraseña RTSP de tus cámaras Tapo (Enter = después)" ""; envset FRIGATE_RTSP_PASSWORD "$RP"; }
[[ -n "$(envget HOMELAB_BACKUP_PASSWORD)" ]] || envset HOMELAB_BACKUP_PASSWORD "$(rand 32)"
[[ -n "$(envget TZ)" ]] || envset TZ "$(timedatectl show -p Timezone --value 2>/dev/null || echo America/Mexico_City)"

[[ -f config/frigate/config.yml ]] || cp config/frigate/config.example.yml config/frigate/config.yml

# Aceleración por hardware según la GPU
OV=hosts/server/docker-compose.override.yml
if [[ "$GPU" == intel || "$GPU" == amd ]]; then
  cat > "$OV" <<EOF
# Generado por install-server.sh (GPU: $GPU). Bórralo para volver a solo CPU.
services:
  frigate:
    devices: ["/dev/dri:/dev/dri"]
  jellyfin:
    devices: ["/dev/dri:/dev/dri"]
    group_add: ["$(getent group render | cut -d: -f3 || echo 109)"]
EOF
  echo "  ${G}✔${N} /dev/dri para Frigate y Jellyfin ($OV)"
  if [[ "$GPU" == intel ]] && grep -q 'type: cpu' config/frigate/config.yml; then
    sed -i 's/^detectors:/# detectors (CPU, cambiado por install-server.sh):/; s/^  cpu1:/#  cpu1:/; s/^    type: cpu/#    type: cpu/' config/frigate/config.yml
    printf '\ndetectors:\n  ov:\n    type: openvino\n    device: GPU\nmodel:\n  path: /openvino-model/ssdlite_mobilenet_v2.xml\n  labelmap_path: /openvino-model/coco_91cl_bkgr.txt\n  width: 300\n  height: 300\n  input_tensor: nhwc\n  input_pixel_format: bgr\n' >> config/frigate/config.yml
    echo "  ${G}✔${N} Frigate detecta con OpenVINO en la GPU Intel"
  fi
else
  rm -f "$OV"
fi

# ---------------------------------------------------------------- 4. levantar
step "4/7 Frigate, Jellyfin y node-exporter"
CS=("${D[@]}" compose -f hosts/server/docker-compose.yml)
[[ -f "$OV" ]] && CS+=(-f "$OV")
CS+=(--env-file .env)
./services/cine/descargar-letras.sh >/dev/null 2>&1 && echo "  ${G}✔${N} Letras del portal guardadas en la PC" || true
"${CS[@]}" pull -q
"${CS[@]}" up -d
"${CS[@]}" ps --format 'table {{.Service}}\t{{.Status}}'

if [[ -z "${HL_TODO_EN_UNO:-}" ]]; then         # migrar.sh lo llama aparte
  if ((YES)); then IM=s; else read -r -p "  ¿Instalo también Immich (respaldo automático de fotos de los celulares)? [S/n] " IM; fi
  [[ "${IM:-s}" =~ ^[sSyY] ]] && ./scripts/instalar-immich.sh $( ((YES)) && echo --si ) || true
fi

# ---------------------------------------------------------------- 5. firewall
step "5/7 Firewall"
LAN="$(ip -4 route | awk -v ip="$MY_IP" '$0 ~ "src "ip && $1 ~ "/" {print $1; exit}')"; LAN="${LAN:-192.168.1.0/24}"
ask LAB "Red del laboratorio para SSH (vacío = toda la casa)" "192.168.100.0/24"
sudo ufw default deny incoming >/dev/null; sudo ufw default allow outgoing >/dev/null
for p in 5000 1984 8554 8555 8096 8097; do sudo ufw allow from "$LAN" to any port "$p" comment "homelab $p" >/dev/null; done
sudo ufw allow from "$PI" to any port 9100 proto tcp comment "métricas para la Pi" >/dev/null
HL_REPO="$REPO"; . scripts/lib/ssh.sh
for sp in $(ssh_ports); do            # el puerto real del SSH de esta PC (no siempre es 22)
  sudo ufw allow from "${LAB:-$LAN}" to any port "$sp" proto tcp comment "SSH" >/dev/null
  [[ -n "$LAB" ]] && sudo ufw allow from "$PI" to any port "$sp" proto tcp comment "SSH desde la Pi" >/dev/null
done
sudo ufw --force enable >/dev/null
echo "  ${G}✔${N} UFW: servicios para $LAN, métricas solo para $PI, SSH desde ${LAB:-$LAN}"
echo "  (Docker publica sus puertos por fuera de UFW; las reglas de arriba cubren lo demás.)"

# ---------------------------------------------------------------- 6. registrar en la Pi
step "6/7 Registrar esta PC en la Pi"
[[ -n "$PI_SSH" || -n "${HL_TODO_EN_UNO:-}" ]] || ask PI_SSH "Usuario SSH de la Pi (usuario@$PI, o usuario@$PI:PUERTO si no es el 22; vacío = te doy los pasos)" "$(id -un)@$PI"
# MAC de la tarjeta por la que sale a la red: para encenderla desde el panel/bot (Wake-on-LAN)
MY_MAC=""
if [[ -z "${HL_TODO_EN_UNO:-}" ]]; then
  ifc="$(ip route show default 2>/dev/null | awk '{print $5; exit}')"
  [[ -n $ifc && -r /sys/class/net/$ifc/address ]] && MY_MAC="$(cat "/sys/class/net/$ifc/address")"
  [[ -n $ifc && -d /sys/class/net/$ifc/wireless ]] && MY_MAC=""     # por Wi-Fi casi nunca funciona
  if [[ -n $MY_MAC ]]; then          # que la tarjeta escuche el «paquete mágico» también tras reiniciar
    con="$(nmcli -g GENERAL.CONNECTION dev show "$ifc" 2>/dev/null || true)"
    [[ -n $con ]] && sudo nmcli con modify "$con" 802-3-ethernet.wake-on-lan magic 2>/dev/null || true
    command -v ethtool >/dev/null && sudo ethtool -s "$ifc" wol g 2>/dev/null || true
  fi
fi
REG="./scripts/agregar-servidor.sh '$MY_IP' '$(hostname)' '$MY_MAC'"
if [[ -n "${HL_TODO_EN_UNO:-}" ]]; then          # la «Pi» es esta misma máquina
  out="$(eval "$REG")"; echo "$out" | sed 's/^/  /' | head -3
  tok="$(echo "$out" | tail -1 | tr -d '[:space:]')"
  [[ ${#tok} -ge 20 && -z "$(envget BACKUP_TOKEN)" ]] && envset BACKUP_TOKEN "$tok"
elif [[ -n "$PI_SSH" ]] && { HL_REPO="$REPO"; . scripts/lib/ssh.sh; ssh_target "$PI_SSH"; } && \
     "${RSSH[@]}" -o BatchMode=yes -o ConnectTimeout=5 "$SSH_HOST" true 2>/dev/null; then
  if out="$("${RSSH[@]}" "$SSH_HOST" "cd ~/MyHomeLab 2>/dev/null || cd ~/myhomelab; $REG")"; then
    echo "$out" | sed 's/^/  /' | head -3
    tok="$(echo "$out" | tail -1 | tr -d '[:space:]')"
    [[ ${#tok} -ge 20 && -z "$(envget BACKUP_TOKEN)" ]] && envset BACKUP_TOKEN "$tok" && echo "  ${G}✔${N} Token de respaldo guardado"
  fi
else
  echo "  No pude entrar por SSH con llave a $PI_SSH. En la Pi corre:"
  echo "     cd ~/MyHomeLab && $REG"
fi

# ---------------------------------------------------------------- 7. respaldo
step "7/7 Respaldo semanal de esta PC"
TOKEN="$(envget BACKUP_TOKEN)"
if [[ -z "$TOKEN" ]]; then
  echo "  En el dashboard: Ajustes → «Equipos que respaldan» → agrega ${B}pc-server${N} (linux) → Guardar."
  ask TOKEN "Pega aquí el token que te muestra (Enter = después)" ""
  envset BACKUP_TOKEN "$TOKEN"
fi
[[ -n "$(envget BACKUP_DEVICE)" ]] || envset BACKUP_DEVICE pc-server
curl -sk --max-time 10 "https://$PI:$PI_PORT/ca.crt" -o run-ca.crt 2>/dev/null && grep -q BEGIN run-ca.crt && mv run-ca.crt .homelab-ca.crt || rm -f run-ca.crt
sudo tee /etc/systemd/system/homelab-server-backup.service >/dev/null <<UNIT
[Unit]
Description=MyHomeLab: respaldo de la PC del homelab
After=docker.service network-online.target
[Service]
Type=oneshot
ExecStart=$REPO/scripts/respaldo-server.sh
UNIT
sudo tee /etc/systemd/system/homelab-server-backup.timer >/dev/null <<UNIT
[Unit]
Description=MyHomeLab: respaldo semanal de la PC
[Timer]
OnCalendar=Sun *-*-* 05:00:00
Persistent=true
[Install]
WantedBy=timers.target
UNIT
sudo systemctl daemon-reload; sudo systemctl enable --now homelab-server-backup.timer >/dev/null
echo "  ${G}✔${N} Cada domingo 05:00 (Frigate config + Jellyfin) → bot del homelab"

echo
echo "${G}${B}✅ PC lista.${N}"
echo "   🍿 Cine en casa: http://$MY_IP:8097   (en la tele: http://$MY_IP:8097/?tele)"
echo "   Frigate:  http://$MY_IP:5000      Jellyfin (administrar): http://$MY_IP:8096"
echo "   Cámaras:  dashboard → Cámaras → «Detectar cámara genérica» te da el bloque para config/frigate/config.yml"
echo "   Después de editar config/frigate/config.yml:  ${D[*]} compose -f hosts/server/docker-compose.yml --env-file .env restart frigate"
echo "   🔐 Contraseña de respaldo de esta PC (guárdala en Vaultwarden): $(envget HOMELAB_BACKUP_PASSWORD)"
