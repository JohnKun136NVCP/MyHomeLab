#!/usr/bin/env bash
# ======================================================================
#  Instala el homelab COMPLETO en una PC o servidor Linux más potente (todo en uno):
#  lo de la Raspberry (monitoreo, bot, DNS, panel…) + lo de la PC (cámaras, Cine, fotos).
#
#    git clone https://github.com/JohnKun136NVCP/MyHomeLab && cd MyHomeLab
#    ./scripts/migrar.sh                          te pregunta cómo
#    ./scripts/migrar.sh --desde-cero             instalación nueva, sin nada de la Pi
#    ./scripts/migrar.sh --desde-pi usuario@IP    copia TODO de la Raspberry por SSH (con llave)
#
#  Opciones: --con-respaldos  (trae también los respaldos de tus equipos; pueden ser GB)
#            --con-tunel      (mueve tu túnel WireGuard al VPS a esta máquina)
#            --sin-pc         (solo lo de la Pi; sin Frigate/Jellyfin/Cine/Immich)
#            --limpiar-pi     (al final deja la Raspberry limpia con limpiar-pi.sh)
#
#  Desde la Pi trae: .env, config/, Vaultwarden, cuentas y equipos del dashboard, bot y
#  publicaciones, AdGuard, Uptime Kuma, Grafana, el certificado de Caddy (los equipos que ya lo
#  instalaron no ven avisos) y, si quieres, tus respaldos y el túnel. Luego adapta lo que cambia
#  en otra máquina: nombres de las tarjetas de red, IP, rutas y el servidor de la Bot API.
# ======================================================================
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
MODE=""; PI=""; WITH_BK=0; WITH_WG=0; WITH_PC=1; CLEAN_PI=0
while (($#)); do
  case "$1" in
    --desde-cero) MODE=cero ;;
    --desde-pi) MODE=pi; PI="${2:?Falta usuario@IP de la Pi}"; shift ;;
    --con-respaldos) WITH_BK=1 ;; --con-tunel) WITH_WG=1 ;; --sin-pc) WITH_PC=0 ;; --limpiar-pi) CLEAN_PI=1 ;;
    -h|--help) sed -n '3,22p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Opción desconocida: $1"; exit 2 ;;
  esac
  shift
done
[[ $EUID -ne 0 ]] || { echo "Córrelo con tu usuario normal (pedirá sudo cuando haga falta)."; exit 1; }
B=$'\e[1m'; G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; N=$'\e[0m'
step() { echo; echo "${B}== $*${N}"; }
ok() { echo "  ${G}✔${N} $*"; }
ask() { local a; read -r -p "  $1${2:+ [$2]}: " a; echo "${a:-${2:-}}"; }
yes_no() { local a; read -r -p "  $1 [s/N] " a; [[ "$a" =~ ^[sSyY] ]]; }
envget() { grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2-; }
envset() {
  local k="$1" v="$2" tmp; tmp="$(mktemp)"; touch .env
  K="$k" V="$v" awk 'BEGIN{k=ENVIRON["K"];v=ENVIRON["V"];d=0} $0 ~ "^"k"=" {if(!d)print k"="v; d=1; next} {print} END{if(!d)print k"="v}' .env > "$tmp"
  cat "$tmp" > .env; rm -f "$tmp"; chmod 600 .env
}

if [[ -z "$MODE" ]]; then
  echo "${B}¿Cómo instalo el homelab en esta máquina?${N}"
  echo "  1) Copiar TODO lo que ya tienes en la Raspberry (por SSH, con llave)"
  echo "  2) Desde cero (sin nada de la Raspberry)"
  case "$(ask "Elige" 1)" in
    2) MODE=cero ;;
    *) MODE=pi; PI="$(ask "Usuario e IP de la Raspberry" "$(id -un)@192.168.1.69")" ;;
  esac
fi

# ---------------------------------------------------------------- 1. lo básico
step "1. Docker y herramientas"
. /etc/os-release
case "${ID_LIKE:-$ID} $ID" in
  *debian*|*ubuntu*) PM=apt ;; *fedora*|*rhel*) PM=dnf ;; *arch*) PM=pacman ;; *) PM=unknown ;;
esac
pkgs() {
  case "$PM" in
    apt) sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y "$@" ;;
    dnf) sudo dnf install -y "$@" ;;
    pacman) sudo pacman -S --needed --noconfirm "$@" ;;
    *) echo "  Instala a mano: $*" ;;
  esac
}
case "$PM" in
  apt) pkgs curl openssl rsync ufw wireguard-tools nftables iproute2 python3-yaml pciutils ;;
  dnf) pkgs curl openssl rsync ufw wireguard-tools nftables iproute python3-pyyaml pciutils ;;
  pacman) pkgs curl openssl rsync ufw wireguard-tools nftables iproute2 python-yaml pciutils ;;
esac >/dev/null
if ! command -v docker >/dev/null; then
  if [[ "$PM" == pacman ]]; then pkgs docker docker-compose >/dev/null; else curl -fsSL https://get.docker.com | sudo sh >/dev/null; fi
fi
sudo systemctl enable --now docker >/dev/null
id -nG "$USER" | grep -qw docker || sudo usermod -aG docker "$USER"
D=(sudo docker)
ok "$(docker --version 2>/dev/null || sudo docker --version)"
sudo mkdir -p /var/lib/telegram-bot-api

# ---------------------------------------------------------------- 2. traer de la Pi
PI_IP=""
if [[ "$MODE" == pi ]]; then
  step "2. Conectando con la Raspberry ($PI)"
  PI_IP="${PI#*@}"
  if ! ssh -o BatchMode=yes -o ConnectTimeout=6 "$PI" true 2>/dev/null; then
    echo "  Todavía no entro con llave. Creo una (si no tienes) y la copio a la Pi:"
    [[ -f ~/.ssh/id_ed25519 ]] || ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519 -C "homelab-migracion@$(hostname)" >/dev/null
    echo "  (te pide UNA vez la contraseña de la Pi; si SSH ya solo acepta llave, cópiala desde un equipo del laboratorio)"
    ssh-copy-id -i ~/.ssh/id_ed25519.pub "$PI" || { echo "${R}✘ No pude copiar la llave.${N}"; exit 1; }
  fi
  ok "Entro a la Pi con llave"
  PIREPO="$(ssh "$PI" 'for d in ~/MyHomeLab ~/myhomelab; do [ -f "$d/hl" ] && { echo "$d"; break; }; done')"
  [[ -n "$PIREPO" ]] || { echo "${R}✘ No encontré el repo en la Pi (~/MyHomeLab).${N}"; exit 1; }
  echo "  Repo en la Pi: $PIREPO"
  PIUSER="${PI%@*}"
  echo "  Haciendo el respaldo cifrado en la Pi (te pide la contraseña de sudo de la Pi)…"
  ssh -t "$PI" "cd $PIREPO && git pull -q --ff-only || true; sudo ./scripts/respaldo-homelab.sh --copia-para $PIUSER"
  scp -q "$PI:homelab-migracion.tar.gz.enc" /tmp/homelab-migracion.tar.gz.enc
  BKPASS="$(ssh "$PI" "grep -E '^HOMELAB_BACKUP_PASSWORD=' $PIREPO/.env | cut -d= -f2-")"
  BKPASS="${BKPASS//\$\$/\$}"
  ssh "$PI" 'rm -f ~/homelab-migracion.tar.gz.enc'
  ok "Respaldo traído ($(du -h /tmp/homelab-migracion.tar.gz.enc | cut -f1))"

  step "3. Restaurando aquí"
  HL_RESTORE_PASSWORD="$BKPASS" HL_RESTORE_YES=1 HL_RESTORE_NO_UP=1 ./scripts/restaurar-homelab.sh /tmp/homelab-migracion.tar.gz.enc
  rm -f /tmp/homelab-migracion.tar.gz.enc
  sudo chown "$USER" .env config -R 2>/dev/null || true

  if ((WITH_BK)) || yes_no "¿Traigo también los respaldos de tus equipos y celulares? (pueden ser varios GB)"; then
    OLDBK="$(envget BACKUPS_DIR)"
    NEWBK="$(ask "¿Dónde los guardo aquí? (un disco grande)" "${OLDBK:-/srv/respaldos}")"
    sudo mkdir -p "$NEWBK"
    echo "  Empaquetando en la Pi (te pide sudo de la Pi; necesita espacio libre allá)…"
    ssh -t "$PI" "sudo tar -C '$OLDBK' -cf ~/homelab-respaldos.tar . && sudo chown $PIUSER ~/homelab-respaldos.tar"
    rsync -ah --info=progress2 --remove-source-files "$PI:homelab-respaldos.tar" /tmp/homelab-respaldos.tar
    sudo tar -C "$NEWBK" -xf /tmp/homelab-respaldos.tar && rm -f /tmp/homelab-respaldos.tar
    envset BACKUPS_DIR "$NEWBK"; ok "Respaldos en $NEWBK"
  fi
  if ((WITH_WG)) || { ssh "$PI" 'ls /etc/wireguard/*.conf' >/dev/null 2>&1 && yes_no "¿Muevo tu túnel WireGuard al VPS a esta máquina? (la Pi lo apaga)"; }; then
    WG="$(ssh "$PI" 'ls /etc/wireguard/ | grep -E "\.conf$" | grep -v "^wg0.conf$" | head -1')"; WG="${WG%.conf}"
    if [[ -n "$WG" ]]; then
      ssh -t "$PI" "sudo install -m 600 -o $PIUSER /etc/wireguard/$WG.conf ~/$WG.conf"
      scp -q "$PI:$WG.conf" /tmp/"$WG".conf && ssh "$PI" "rm -f ~/$WG.conf"
      sudo install -m 600 -o root /tmp/"$WG".conf "/etc/wireguard/$WG.conf"; rm -f /tmp/"$WG".conf
      ssh -t "$PI" "sudo systemctl disable --now wg-quick@$WG" >/dev/null 2>&1 || true
      sudo systemctl enable --now "wg-quick@$WG" && ok "Túnel $WG activo aquí (misma llave: el VPS no cambia nada)"
    fi
  fi
else
  step "2. Configuración nueva"
  [[ -f config/homelab.yml ]] || cp config/homelab.example.yml config/homelab.yml
  ./scripts/gen-secrets.sh
fi

# ---------------------------------------------------------------- 4. adaptar a esta máquina
step "4. Adaptando a esta máquina"
MY_IP="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}')"
WAN_IF="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev") print $(i+1)}')"
echo "  Tarjetas de red:"; ip -br -4 addr | grep -v '^lo\|^docker\|^br-\|^veth' | sed 's/^/    /'
OLD_IP="$(envget HOMELAB_HOST)"
CASA_IF="$(ask "Tarjeta conectada a la red de la casa" "$WAN_IF")"
LAB_IF="$(ask "Tarjeta de la red del laboratorio (vacío = no hay)" "$(ip -br link | awk '$1 ~ /^(wl|wlan)/ {print $1; exit}')")"
envset HOMELAB_HOST "$MY_IP"
envset CAPTURE_INTERFACES "$CASA_IF${LAB_IF:+,$LAB_IF}"
python3 - "$CASA_IF" "$LAB_IF" "$OLD_IP" "$MY_IP" <<'PY'
import sys, yaml
casa, lab, old, new = sys.argv[1:]
p = "config/homelab.yml"
txt = open(p).read()
if old and new and old != new:
    txt = txt.replace(old, new)                     # enlaces, DNS, etc. que apuntaban a la Pi
c = yaml.safe_load(txt) or {}
nets = (c.get("network") or {}).get("networks") or []
for i, n in enumerate(nets):
    if i == 0:
        n["interface"] = casa
    elif lab and n.get("interface", "").startswith(("wl", "wlan")):
        n["interface"] = lab
    elif not lab and n.get("interface", "").startswith(("wl", "wlan")):
        n["enabled"] = False
fw = c.get("firewall") or {}
if fw.get("ssh") and lab:
    fw["ssh"]["interface"] = lab
with open(p, "w") as f:
    f.write("# Adaptado por scripts/migrar.sh\n")
    yaml.safe_dump(c, f, sort_keys=False, allow_unicode=True)
print(f"  ✔ homelab.yml: casa={casa}" + (f", laboratorio={lab}" if lab else "") + (f", IP {old} → {new}" if old and old != new else ""))
PY
# Rutas que en la Pi apuntaban a sus discos
for k in BACKUPS_DIR VW_DATA; do
  v="$(envget $k)"; [[ -z "$v" || "$v" != /* ]] && continue
  sudo mkdir -p "$v"
done
# La Bot API local: en la Pi era tuya (fuera de Docker); aquí, el contenedor del homelab
if [[ "$(envget TELEGRAM_API_URL)" == *host.docker.internal:8081* || -z "$(envget TELEGRAM_API_URL)" ]]; then
  echo "  El bot sube archivos de hasta 2 GB con un servidor local de la Bot API."
  echo "  Necesita tu api_id y api_hash (https://my.telegram.org → API development tools)."
  AID="$(ask "api_id (vacío = usar el de Telegram, máx. 50 MB)" "$(envget TELEGRAM_API_ID)")"
  if [[ -n "$AID" ]]; then
    AHASH="$(ask "api_hash" "$(envget TELEGRAM_API_HASH)")"
    envset TELEGRAM_API_ID "$AID"; envset TELEGRAM_API_HASH "$AHASH"
    envset TELEGRAM_API_URL "http://telegram-bot-api:8081"; envset TELEGRAM_API_DATA /var/lib/telegram-bot-api
    prof="$(envget COMPOSE_PROFILES)"; [[ ",$prof," == *,botapi,* ]] || envset COMPOSE_PROFILES "${prof:+$prof,}botapi"
    ok "Bot API local en contenedor"
  else
    envset TELEGRAM_API_URL "https://api.telegram.org"
    echo "  ${Y}⚠${N} Con la API normal de Telegram el límite de archivos es 50 MB."
  fi
fi
# Puertos 80/443: si están libres aquí, el panel queda en https://IP sin puerto
if ! sudo ss -ltnH 'sport = :443' | grep -q .; then envset HTTPS_PORT 443; envset HTTP_PORT 80; fi
ok "Panel: https://$MY_IP$( [[ "$(envget HTTPS_PORT)" != 443 ]] && echo ":$(envget HTTPS_PORT)")"

# ---------------------------------------------------------------- 5. levantar
step "5. Levantando el homelab"
"${D[@]}" compose -f hosts/pi/docker-compose.yml --env-file .env up -d --build
sudo ./scripts/install-apply-service.sh >/dev/null && ok "«Guardar y aplicar» del panel"
sudo ./scripts/respaldo-homelab.sh --instalar >/dev/null && ok "Respaldo semanal programado"
sleep 5; ./hl ca >/dev/null 2>&1 || true

if ((WITH_PC)); then
  step "6. Lo de la PC (Frigate, Jellyfin, Cine en casa, Immich)"
  HL_TODO_EN_UNO=1 ./scripts/install-server.sh --pi "$MY_IP" --puerto "$(envget HTTPS_PORT)"
  [[ -x scripts/instalar-immich.sh ]] && ./scripts/instalar-immich.sh --si || true
fi

step "7. Firewall"
./scripts/firewall.sh || true

if [[ "$MODE" == pi ]]; then
  echo
  echo "${B}Falta hacer a mano (2 minutos):${N}"
  echo "  • Si el módem o las Tenda usaban la Pi ($PI_IP) como DNS, cámbialo a $MY_IP."
  echo "  • Uptime Kuma: revisa los monitores que apuntaban a $PI_IP."
  echo "  • Los equipos que respaldan siguen funcionando si cambias su dirección a https://$MY_IP:$(envget HTTPS_PORT)"
  echo "    (Mis respaldos → Nuevo enlace de instalación lo hace por ti)."
  if ((CLEAN_PI)) || yes_no "¿Dejo la Raspberry limpia ahora? (borra el homelab de la Pi)"; then
    ssh -t "$PI" "cd $PIREPO && sudo ./scripts/limpiar-pi.sh --sin-respaldo"
  else
    echo "  Cuando quieras:  ssh -t $PI 'cd $PIREPO && sudo ./scripts/limpiar-pi.sh'"
  fi
fi
echo
echo "${G}${B}✅ Homelab instalado en $(hostname) ($MY_IP).${N}  Revisa todo con:  ./hl revisar"
id -nG "$USER" | grep -qw docker || echo "   (cierra sesión y vuelve a entrar para usar docker sin sudo)"
