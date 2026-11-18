#!/usr/bin/env bash
# ======================================================================
#  Deja la Raspberry limpia: quita TODO lo que instaló el homelab.
#    sudo ./scripts/limpiar-pi.sh                 pregunta paso por paso (recomendado)
#    sudo ./scripts/limpiar-pi.sh --plan          solo muestra lo que haría
#    Opciones (si ya sabes lo que quieres):
#      --sin-respaldo     no hace el respaldo cifrado antes de borrar
#      --ssh              también quita «SSH solo con llave y solo desde el laboratorio»
#      --borrar-datos     también borra .env, homelab.yml, respaldos y Vaultwarden
#      --quitar-docker    también desinstala Docker
#      --si               no pregunta (salvo escribir LIMPIAR al final)
#
#  Quita: contenedores, volúmenes, imágenes y redes del homelab (Pi y PC si estaban aquí),
#  servicios de systemd del homelab, tablas de nftables, el puente br0, reglas de UFW
#  que agregó el homelab, NUT del no-break (si lo puso el homelab) y la caché de Docker.
#  NO toca lo tuyo: nginx, el 8090, tu túnel wg-hoshimy, tu telegram-bot-api, ni otros
#  contenedores que no sean del homelab.
# ======================================================================
set -uo pipefail
cd "$(dirname "$0")/.."
REPO="$(pwd)"
PLAN=0; BACKUP=1; SSH=0; DATA=0; DOCKER=0; YES=0
for a in "$@"; do
  case "$a" in
    --plan) PLAN=1 ;; --sin-respaldo) BACKUP=0 ;; --ssh) SSH=1 ;; --borrar-datos) DATA=1 ;;
    --quitar-docker) DOCKER=1 ;; --si|-y) YES=1 ;;
    *) echo "Opción desconocida: $a"; exit 2 ;;
  esac
done
[[ $EUID -eq 0 || $PLAN -eq 1 ]] || exec sudo "$0" "$@"
B=$'\e[1m'; R=$'\e[31m'; Y=$'\e[33m'; G=$'\e[32m'; N=$'\e[0m'
ask() { ((YES)) && return 0; read -r -p "  $1 [s/N] " r; [[ "$r" =~ ^[sSyY] ]]; }
envv() { grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2- | tr -d "\"'"; }
OWNER="$(stat -c %U "$REPO")"
HAVE_DOCKER=0; command -v docker >/dev/null && docker info >/dev/null 2>&1 && HAVE_DOCKER=1

# ------------------------------------------------------------ qué hay
echo "${B}Esto es lo que el homelab dejó en esta máquina:${N}"
PROJECTS=()
if ((HAVE_DOCKER)); then
  for p in homelab-pi homelab-server homelab-immich homelab-seclab; do
    n=$(docker ps -aq --filter "label=com.docker.compose.project=$p" | wc -l)
    v=$(docker volume ls -q --filter "label=com.docker.compose.project=$p" | wc -l)
    ((n + v)) && { PROJECTS+=("$p"); echo "  • Docker «$p»: $n contenedor(es), $v volumen(es)"; }
  done
  [[ -n "$(docker ps -aq --filter name=immich_)" ]] && { PROJECTS+=("immich"); echo "  • Immich (contenedores immich_*)"; }
fi
UNITS=()
for u in homelab-apply.path homelab-apply.service homelab-backup.timer homelab-backup.service \
         homelab-bridge-fw.service homelab-restore-test.timer homelab-restore-test.service \
         homelab-server-backup.timer homelab-server-backup.service; do
  [[ -f "/etc/systemd/system/$u" ]] && UNITS+=("$u")
done
((${#UNITS[@]})) && echo "  • systemd: ${UNITS[*]}"
NFT=0; command -v nft >/dev/null && nft list table bridge homelab >/dev/null 2>&1 && { NFT=1; echo "  • nftables: tabla bridge homelab (ACL)"; }
BR=0; command -v nmcli >/dev/null && nmcli -t -f NAME con show 2>/dev/null | grep -qx br0 && { BR=1; echo "  • Red: puente br0 (modo puente)"; }
UFWN=(); UFWSSH=()

if command -v ufw >/dev/null; then
  while read -r n; do UFWN+=("$n"); done < <(ufw status numbered 2>/dev/null \
    | grep -E '# (Dashboard homelab|Dashboard \(redirige a https\)|DNS AdGuard|DNS desde Docker|node-exporter por VPN|homelab|honeypot|NUT)' \
    | sed -E 's/^\[ *([0-9]+)\].*/\1/')
  while read -r n; do UFWSSH+=("$n"); done < <(ufw status numbered 2>/dev/null | grep -E '# SSH ' | sed -E 's/^\[ *([0-9]+)\].*/\1/')
  ((${#UFWN[@]})) && echo "  • UFW: ${#UFWN[@]} regla(s) del homelab"
  ((${#UFWSSH[@]})) && echo "  • UFW: ${#UFWSSH[@]} regla(s) de SSH solo para el laboratorio"
fi
SSHD=0; [[ -f /etc/ssh/sshd_config.d/10-homelab.conf ]] && { SSHD=1; echo "  • SSH: solo con llave (/etc/ssh/sshd_config.d/10-homelab.conf)"; }
NUT=0; [[ -f /etc/nut/.homelab ]] && { NUT=1; echo "  • No-break: NUT configurado por el homelab"; }
WZA=0; [[ -f /var/ossec/.homelab ]] && { WZA=1; echo "  • Agente de Wazuh (lab/seguridad/agente-wazuh.sh)"; }
WG0=0; [[ -f /etc/wireguard/wg0.conf ]] && grep -q "10.8.0.0/24" /etc/wireguard/wg0.conf 2>/dev/null && { WG0=1; echo "  • VPN wg0 (de scripts/pi-vpn.sh; tu wg-hoshimy no se toca)"; }
BK="$(envv BACKUPS_DIR)"; VW="$(envv VW_DATA)"; VW="${VW:-./vaultwarden}"; [[ "$VW" != /* ]] && VW="$REPO/hosts/pi/$VW"
echo "  • Datos: .env, config/homelab.yml, run/${BK:+, respaldos en $BK}${VW:+, Vaultwarden en $VW}"
echo

# ------------------------------------------------------------ avisos
if ((HAVE_DOCKER)) && docker ps --format '{{.Names}}' | grep -q 'adguard'; then
  echo "${Y}⚠ AdGuard está corriendo aquí.${N} Si el módem o las Tenda le dan esta IP como DNS a la casa,"
  echo "  al quitarlo NADIE tendrá internet. Antes cambia el DNS del módem/Tenda a 1.1.1.1 o al de la PC nueva."
  ((YES)) || ask "¿Ya lo cambiaste (o no lo usan)?" || { echo "Cámbialo primero y vuelve a correr esto."; exit 1; }
fi
if ((PLAN)); then
  echo "Con --plan no cambio nada. Opciones extra: --ssh --borrar-datos --quitar-docker"; exit 0
fi

# ------------------------------------------------------------ respaldo
if ((BACKUP)) && ((HAVE_DOCKER)) && [[ -f .env ]] && [[ " ${PROJECTS[*]} " == *" homelab-pi "* ]]; then
  if ask "¿Hago un respaldo cifrado de todo antes de borrar? (muy recomendado)"; then
    if ./scripts/respaldo-homelab.sh; then
      last="$(python3 -c 'import json;print(json.load(open("run/homelab-backup.json"))["file"])' 2>/dev/null)"
      mkdir -p /root/homelab-respaldo-final && cp -a "$last" /root/homelab-respaldo-final/ 2>/dev/null
      echo "  ${G}✔${N} Respaldo: $last (copia en /root/homelab-respaldo-final)"
      echo "     Llévatelo antes de borrar datos:  scp $OWNER@$(hostname -I | awk '{print $1}'):$last ."
    else
      echo "  ${R}✘ El respaldo falló.${N}"; ask "¿Sigo de todos modos?" || exit 1
    fi
  fi
fi

# ------------------------------------------------------------ opciones peligrosas
if ((!YES)); then
  ((SSH)) || { ((SSHD + ${#UFWSSH[@]})) && ask "¿Quito también lo de SSH (vuelve a aceptar contraseña y cualquier equipo de la casa)?" && SSH=1; }
  ((DATA)) || { ask "¿Borro también los DATOS (.env, respaldos, Vaultwarden)? No hay vuelta atrás" && DATA=1; }
  ((DOCKER)) || { ask "¿Desinstalo Docker por completo?" && DOCKER=1; }
fi
echo
echo "${B}Voy a quitar:${N} contenedores/volúmenes/imágenes del homelab, servicios, nftables, br0, reglas de UFW$( ((NUT)) && echo ", NUT")$( ((WZA)) && echo ", agente de Wazuh")"
((SSH)) && echo "  + SSH solo con llave y solo laboratorio"
((DATA)) && echo "  ${R}+ TODOS los datos: .env, homelab.yml, run/, ${BK:-respaldos}, $VW${N}"
((DOCKER)) && echo "  + Docker completo"
read -r -p "Escribe LIMPIAR para empezar: " c
[[ "$c" == LIMPIAR ]] || { echo "No cambié nada."; exit 0; }

# ------------------------------------------------------------ manos a la obra
step() { echo; echo "${B}== $*${N}"; }
if ((HAVE_DOCKER)); then
  step "Contenedores, volúmenes e imágenes"
  for f in hosts/pi/docker-compose.yml hosts/server/docker-compose.yml hosts/server/immich/docker-compose.yml lab/seguridad/docker-compose.yml; do
    [[ -f "$f" ]] || continue
    ef=.env; [[ $f == hosts/server/immich/* ]] && ef=hosts/server/immich/.env     # Immich trae su propio .env
    [[ -f $ef ]] || ef=/dev/null
    docker compose -f "$f" --env-file "$ef" --profile '*' down --volumes --rmi all --remove-orphans 2>/dev/null \
      || docker compose -f "$f" --env-file "$ef" down --volumes --rmi all --remove-orphans 2>/dev/null || true
  done
  for p in homelab-pi homelab-server homelab-immich homelab-seclab immich; do
    docker ps -aq --filter "label=com.docker.compose.project=$p" | xargs -r docker rm -f >/dev/null
    docker volume ls -q --filter "label=com.docker.compose.project=$p" | xargs -r docker volume rm -f >/dev/null
    docker network ls -q --filter "label=com.docker.compose.project=$p" | xargs -r docker network rm >/dev/null 2>&1
  done
  docker ps -aq --filter name=immich_ | xargs -r docker rm -f >/dev/null
  if [[ -f hosts/server/immich/.env ]]; then
    echo "  (las fotos de Immich NO se borran: siguen en $(grep -E '^UPLOAD_LOCATION=' hosts/server/immich/.env | cut -d= -f2-) y su base en $(grep -E '^DB_DATA_LOCATION=' hosts/server/immich/.env | cut -d= -f2-))"
  fi
  docker images --format '{{.Repository}}:{{.Tag}} {{.ID}}' | awk '$1 ~ /^homelab-(pi|server)-/ {print $2}' | xargs -r docker rmi -f >/dev/null 2>&1
  docker builder prune -af >/dev/null 2>&1
  docker image prune -f >/dev/null 2>&1
  echo "  ${G}✔${N} Docker del homelab quitado"
fi

step "Servicios de systemd"
for u in "${UNITS[@]}"; do systemctl disable --now "$u" >/dev/null 2>&1; rm -f "/etc/systemd/system/$u"; echo "  - $u"; done
systemctl daemon-reload

((NFT)) && { step "nftables"; nft delete table bridge homelab && echo "  - tabla bridge homelab"; }
((BR)) && { step "Puente br0"; ./scripts/pi-bridge.sh desactivar || true; }
if ((WG0)); then step "VPN wg0"; systemctl disable --now wg-quick@wg0 >/dev/null 2>&1; rm -f /etc/wireguard/wg0.conf; echo "  - wg0"; fi
if ((NUT)); then
  step "No-break (NUT)"
  systemctl disable --now nut-server nut-monitor nut-client >/dev/null 2>&1
  for f in /etc/nut/*.homelab-orig; do [[ -f "$f" ]] && mv -f "$f" "${f%.homelab-orig}"; done
  rm -f /etc/nut/.homelab /etc/nut/.homelab-pass /etc/nut/homelab-notify.sh /etc/nut/homelab-notify.env \
    /etc/nut/homelab-notify-both.sh /etc/nut/homelab-upssched.sh; echo "  - configuración de NUT devuelta a la original"
fi
rm -f /etc/sudoers.d/90-homelab-immich-mudanza    # por si una mudanza de Immich se quedó a medias
if ((WZA)); then step "Agente de Wazuh"; ./lab/seguridad/agente-wazuh.sh --quitar; fi

if command -v ufw >/dev/null; then
  step "UFW"
  dels=("${UFWN[@]}"); ((SSH)) && dels+=("${UFWSSH[@]}")
  if ((SSH)) && ((${#UFWSSH[@]})); then           # que no te quedes fuera
    for net in $(python3 -c 'import yaml;c=yaml.safe_load(open("config/homelab.yml"));print(" ".join(n["cidr"] for n in c.get("network",{}).get("networks",[]) if n.get("cidr")))' 2>/dev/null || echo 192.168.0.0/16); do
      for sp in $(HL_REPO="$REPO"; . scripts/lib/ssh.sh; ssh_ports); do
        ufw allow from "$net" to any port "$sp" proto tcp >/dev/null && echo "  + SSH (puerto $sp) desde $net (para que no te quedes fuera)"
      done
    done
  fi
  for n in $(printf '%s\n' "${dels[@]}" | sort -rn); do yes | ufw delete "$n" >/dev/null && echo "  - regla [$n]"; done
fi
if ((SSH)) && ((SSHD)); then
  step "SSH"; rm -f /etc/ssh/sshd_config.d/10-homelab.conf
  sshd -t && { systemctl reload ssh 2>/dev/null || systemctl reload sshd; } && echo "  - SSH vuelve a aceptar contraseña"
fi

if ((DATA)); then
  step "Datos"
  rm -f .env .env.bak* config/homelab.yml; rm -rf run config/authelia/db.sqlite3 config/authelia/users.yml config/authelia/notification.txt config/authelia/configuration.yml config/caddy/sso/sso.caddy
  [[ -n "$BK" && -d "$BK" && "$BK" != / ]] && rm -rf "$BK" && echo "  - $BK"
  [[ -d "$VW" && "$VW" != / ]] && rm -rf "$VW" && echo "  - $VW"
  echo "  - .env, homelab.yml, run/"
fi

if ((DOCKER)); then
  step "Docker"
  systemctl disable --now docker docker.socket containerd >/dev/null 2>&1
  apt-get purge -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin docker-ce-rootless-extras docker.io >/dev/null 2>&1
  apt-get autoremove -y >/dev/null 2>&1
  rm -rf /var/lib/docker /var/lib/containerd /etc/apt/sources.list.d/docker.list /etc/apt/keyrings/docker.*
  gpasswd -d "$OWNER" docker >/dev/null 2>&1
  echo "  - Docker desinstalado"
fi

echo
echo "${G}${B}✅ Listo: la Raspberry quedó sin el homelab.${N}"
echo "   Se quedaron (son tuyos o del sistema): nginx, el 8090, wg-hoshimy, telegram-bot-api, UFW activo,"
echo "   y los paquetes wireguard-tools/nftables (tu túnel los usa)."
((DATA)) || echo "   Tus datos siguen en: .env, config/, ${BK:-} y $VW  (bórralos con --borrar-datos)."
echo "   La carpeta del repo la puedes borrar tú:  rm -rf $REPO"
