#!/usr/bin/env bash
# ======================================================================
#  Herramientas para la casa (en la Pi o en cualquier PC del homelab).
#    ./hl home                      lista: qué hay, qué está encendido y dónde entrar
#    ./hl home enable NAME          enciende una (crea carpetas, contraseñas, firewall y botón)
#    ./hl home disable NAME         la apaga (sus datos se quedan)
#    ./hl home status               contenedores
#  NAME: homeassistant paperless navidrome syncthing umami cloudflared dns-backup
#  Datos en HOME_DATA (por defecto /srv/homelab-home). Configuración: hosts/home/.env
# ======================================================================
set -euo pipefail
. "$(dirname "$(readlink -f "$0")")/lib/flags.sh"; hl_flags "$@"; set -- "${HL_ARGS[@]}"   # opciones en inglés (acepta las de antes en español)
REPO="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
cd "$REPO"
ENVF=hosts/home/.env
G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; B=$'\e[1m'; DIM=$'\e[2m'; N=$'\e[0m'
ok() { echo "  ${G}✔${N} $*"; }; warn() { echo "  ${Y}⚠${N} $*"; }; die() { echo "${R}✘${N} $*"; exit 1; }
if docker info >/dev/null 2>&1; then D=(docker); else D=(sudo docker); fi
C=("${D[@]}" compose -f hosts/home/docker-compose.yml --env-file "$ENVF")
YES=0; for a in "$@"; do [[ $a == --yes ]] && YES=1; done

ALL=(homeassistant paperless navidrome syncthing umami cloudflared dns-backup)
declare -A DESC=(
  [homeassistant]="Home Assistant: focos, enchufes y cámaras con automatizaciones"
  [paperless]="Paperless-ngx: documentos escaneados con búsqueda (OCR en español)"
  [navidrome]="Navidrome: tu música en streaming, con app en el celular"
  [syncthing]="Syncthing: carpetas sincronizadas entre tus equipos (p. ej. apuntes de Obsidian)"
  [umami]="Umami: visitas a tu página web, sin Google Analytics"
  [cloudflared]="Túnel de Cloudflare: publica Umami a internet sin abrir puertos"
  [dns-backup]="DNS de respaldo: segundo AdGuard que copia la configuración del principal"
)
declare -A PORT=([homeassistant]=8123 [paperless]=8010 [navidrome]=4533 [syncthing]=8384 [umami]=3007 [dns-backup]=3002)
declare -A ICON=([homeassistant]="🏠" [paperless]="📄" [navidrome]="🎵" [syncthing]="🔄" [umami]="📊" [dns-backup]="🛡")
declare -A NAME=([homeassistant]="Home Assistant" [paperless]="Documentos (Paperless)" [navidrome]="Música (Navidrome)"
  [syncthing]="Syncthing" [umami]="Visitas de la página (Umami)" [dns-backup]="DNS de respaldo")
declare -A CHECK=([homeassistant]=/manifest.json [paperless]=/ [navidrome]=/ping [syncthing]=/rest/noauth/health [umami]=/api/heartbeat [dns-backup]=/)

envget() { grep -E "^$1=" "$ENVF" 2>/dev/null | tail -1 | cut -d= -f2- || true; }
putv() {
  local k="$1" v="$2" tmp; tmp="$(mktemp)"
  K="$k" V="$v" awk 'BEGIN{k=ENVIRON["K"]; v=ENVIRON["V"]; d=0} $0 ~ "^"k"=" {if(!d) print k"="v; d=1; next} {print} END{if(!d) print k"="v}' \
    "$ENVF" > "$tmp"; cat "$tmp" > "$ENVF"; rm -f "$tmp"
}
setdef() { [[ -n "$(envget "$1")" ]] || putv "$1" "$2"; }
rand() { openssl rand -hex 16; }
my_ip() { ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}' || hostname -I | awk '{print $1}'; }
rootenv() { grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2- || true; }
profiles() { envget COMPOSE_PROFILES | tr ',' '\n' | grep -v '^$' || true; }
lan_nets() {
  python3 - <<'PY' 2>/dev/null || ip -4 route | awk '$3 !~ /docker|br-|veth/ && /proto kernel/ && $1 !~ /^172\.(1[6-9]|2|3[01])\./ {print $1}' | head -1
import yaml
c = yaml.safe_load(open("config/homelab.yml")) or {}
nets = (c.get("firewall") or {}).get("lan_networks") or [n["cidr"] for n in (c.get("network") or {}).get("networks") or [] if n.get("cidr")]
assert nets
print("\n".join(nets))
PY
}

init_env() {
  [[ -f $ENVF ]] || { umask 077; : > "$ENVF"; }
  chmod 600 "$ENVF"
  setdef HOME_DATA /srv/homelab-home
  setdef HOME_HOST "$(my_ip)"
  setdef HOME_NAME "$(hostname)"
  setdef TZ "$(timedatectl show -p Timezone --value 2>/dev/null || echo America/Mexico_City)"
  setdef COMPOSE_PROFILES ""
}

prepare() {   # lo que necesita cada herramienta antes de encenderla
  local p="$1" data; data="$(envget HOME_DATA)"
  sudo mkdir -p "$data"
  case "$p" in
    paperless)
      setdef PAPERLESS_DB_PASSWORD "$(rand)"; setdef PAPERLESS_SECRET_KEY "$(rand)$(rand)"
      setdef PAPERLESS_ADMIN_USER admin; setdef PAPERLESS_ADMIN_PASSWORD "$(rand | cut -c1-12)"
      sudo mkdir -p "$data/paperless/consume"; sudo chown -R 1000:1000 "$data/paperless" ;;
    navidrome)
      local m; m="$(envget MUSIC_DIR)"
      if [[ -z $m ]]; then
        if ((YES)); then m=/srv/musica; else read -r -p "  Carpeta con tu música [/srv/musica]: " m; m="${m:-/srv/musica}"; fi
        putv MUSIC_DIR "$m"
      fi
      sudo mkdir -p "$m" "$data/navidrome"; sudo chown 1000:1000 "$data/navidrome" ;;
    syncthing) sudo mkdir -p "$data/syncthing"; sudo chown 1000:1000 "$data/syncthing" ;;
    umami) setdef UMAMI_DB_PASSWORD "$(rand)"; setdef UMAMI_APP_SECRET "$(rand)$(rand)" ;;
    cloudflared)
      if [[ -z "$(envget CLOUDFLARE_TUNNEL_TOKEN)" ]]; then
        echo "  Necesitas el token de un túnel de Cloudflare (Zero Trust → Networks → Tunnels → Create)."
        echo "  En «Public hostname» pon, por ejemplo, visitas.tudominio.com → http://umami:3000"
        ((YES)) && die "Falta CLOUDFLARE_TUNNEL_TOKEN en $ENVF"
        read -r -s -p "  Pega el token (no se muestra): " t; echo; [[ -n $t ]] || die "Sin token"
        putv CLOUDFLARE_TUNNEL_TOKEN "$t"
      fi
      profiles | grep -qx umami || warn "El túnel es para Umami: enciéndelo también (./hl home enable umami)" ;;
    dns-backup)
      if grep -qs '^COMPOSE_PROFILES=.*\bdns\b' .env; then
        die "Esta máquina ya tiene el AdGuard principal; el de respaldo va en OTRA (./hl roles lo pone solo)."
      fi
      setdef ADGUARD_PRIMARY ""
      [[ -n "$(envget ADGUARD_PRIMARY)" ]] || { read -r -p "  IP del AdGuard principal (la Pi): " a; putv ADGUARD_PRIMARY "$a"; }
      for k in ADGUARD_USER ADGUARD_PASSWORD; do
        [[ -n "$(envget $k)" ]] || { read -r -p "  $k del AdGuard de la Pi: " v; putv "$k" "$v"; }
      done
      if sudo ss -lunH 'sport = :53' | grep -q .; then
        warn "El puerto 53 ya lo usa algo (¿systemd-resolved?). Apágalo: sudo sed -i 's/^#\\?DNSStubListener=.*/DNSStubListener=no/' /etc/systemd/resolved.conf && sudo systemctl restart systemd-resolved"
      fi
      sudo mkdir -p "$data/adguard" ;;
  esac
}

open_fw() {
  local p="$1" n
  command -v ufw >/dev/null && sudo ufw status 2>/dev/null | grep -q "Status: active" || return 0
  for n in $(lan_nets); do
    case "$p" in
      syncthing) sudo ufw allow from "$n" to any port 8384,22000 proto tcp comment "homelab home syncthing" >/dev/null
                 sudo ufw allow from "$n" to any port 22000,21027 proto udp comment "homelab home syncthing" >/dev/null ;;
      homeassistant) sudo ufw allow from "$n" to any port 8123 proto tcp comment "homelab home homeassistant" >/dev/null ;;
      dns-backup) sudo ufw allow from "$n" to any port 53 comment "homelab home dns-backup" >/dev/null
                  sudo ufw allow from "$n" to any port 3002 proto tcp comment "homelab home dns-backup" >/dev/null ;;
      *) [[ -n ${PORT[$p]:-} ]] && sudo ufw allow from "$n" to any port "${PORT[$p]}" proto tcp comment "homelab home $p" >/dev/null ;;
    esac
  done
}

link() {   # botón en el panel (en esta máquina si tiene el panel; si no, lo dice)
  local p="$1" act="$2" ip url; ip="$(envget HOME_HOST)"
  [[ -n ${PORT[$p]:-} ]] || return 0
  url="http://$ip:${PORT[$p]}"
  if [[ $act == add ]]; then LINK_GROUP="En casa" python3 scripts/lib/links.py add "${NAME[$p]}" "$url" "${ICON[$p]}" "${DESC[$p]#*: }" "$url${CHECK[$p]}"
  else python3 scripts/lib/links.py remove "${NAME[$p]}"; fi
  [[ -f config/homelab.yml ]] || echo "  ${DIM}En la Pi:  ./hl link add \"${NAME[$p]}\" $url ${ICON[$p]}${N}"
}

set_profiles() { putv COMPOSE_PROFILES "$(grep -v '^$' <<<"$1" | sort -u | paste -sd, -)"; }

cmd="${1:-list}"; shift || true
init_env
case "$cmd" in
  list|lista|"")
    echo "${B}Herramientas para la casa${N} (en $(hostname), datos en $(envget HOME_DATA))"
    for p in "${ALL[@]}"; do
      if profiles | grep -qx "$p"; then m="${G}[encendido]${N}"; else m="${DIM}[apagado]  ${N}"; fi
      printf '  %s %-14s %s%s\n' "$m" "$p" "${DESC[$p]}" "$([[ -n ${PORT[$p]:-} ]] && profiles | grep -qx "$p" && echo " → http://$(envget HOME_HOST):${PORT[$p]}")"
    done
    echo; echo "  ./hl home enable NAME   ·   ./hl home disable NAME" ;;
  enable|on|encender)
    p="${1:?Falta el nombre: ${ALL[*]}}"
    [[ " ${ALL[*]} " == *" $p "* ]] || die "No conozco «$p». Opciones: ${ALL[*]}"
    echo "${B}== Encendiendo $p${N}"
    prepare "$p"
    set_profiles "$(profiles; echo "$p")"
    "${C[@]}" --profile "$p" pull -q
    "${C[@]}" up -d --remove-orphans
    open_fw "$p"; link "$p" add
    ok "${DESC[$p]}"
    [[ -n ${PORT[$p]:-} ]] && echo "     → http://$(envget HOME_HOST):${PORT[$p]}"
    case "$p" in
      paperless) echo "     Usuario: $(envget PAPERLESS_ADMIN_USER) · contraseña: en hosts/home/.env (PAPERLESS_ADMIN_PASSWORD)"
                 echo "     Escanea a la carpeta $(envget HOME_DATA)/paperless/consume y aparecen solos." ;;
      umami) echo "     Entra con admin / umami y CAMBIA la contraseña. Luego: Sitios → Agregar → copia el <script> a tu página."
             echo "     Para que lleguen visitas de internet: ./hl home enable cloudflared (guía: docs/casa.md)" ;;
      homeassistant) echo "     La primera vez tarda ~2 min. Crea tu usuario y agrega dispositivos." ;;
      syncthing) echo "     Ponle usuario y contraseña a la web (Acciones → Ajustes → GUI) y conecta tus otros equipos." ;;
      dns-backup) echo "     En el módem/AP pon como DNS: 1° la Pi, 2° $(envget HOME_HOST). Se sincroniza cada 10 min." ;;
    esac ;;
  disable|off|apagar)
    p="${1:?Falta el nombre}"
    set_profiles "$(profiles | grep -vx "$p")"
    "${C[@]}" --profile "$p" stop >/dev/null 2>&1 || true
    "${C[@]}" up -d --remove-orphans
    link "$p" remove
    ok "$p apagado (sus datos siguen en $(envget HOME_DATA))" ;;
  links)   # para ./hl roles: los botones de lo encendido aquí, listos para links.py add
    ip="$(envget HOME_HOST)"
    for p in $(profiles); do
      [[ -n ${PORT[$p]:-} ]] || continue
      printf '"%s" "%s" "%s" "%s" "%s"\n' "${NAME[$p]}" "http://$ip:${PORT[$p]}" "${ICON[$p]}" "${DESC[$p]#*: }" "http://$ip:${PORT[$p]}${CHECK[$p]}"
    done ;;
  status|estado) "${C[@]}" ps --format 'table {{.Service}}\t{{.Status}}\t{{.Ports}}' ;;
  *) sed -n 2,10p "$(readlink -f "$0")" | sed -E 's/^# ?//' ;;
esac
