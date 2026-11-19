#!/usr/bin/env bash
# ======================================================================
#  Menú del homelab: todas las herramientas en un solo lugar.
#    ./hl                 (en una terminal) abre este menú
#    ./hl menu [section]  sections: status services photos backups security ups lab devices
#  Cada opción muestra el comando que corre, para que lo aprendas y lo uses directo si quieres.
# ======================================================================
set -uo pipefail
REPO="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
cd "$REPO"
B=$'\e[1m'; DIM=$'\e[2m'; G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; C_=$'\e[36m'; N=$'\e[0m'
[[ -t 1 ]] || { B=""; DIM=""; G=""; Y=""; R=""; C_=""; N=""; }
if docker info >/dev/null 2>&1; then D=(docker); else D=(sudo docker); fi

envv() { grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2- | tr -d "\"'" || true; }
my_ip() { ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}'; }
arch_x86() { [[ "$(uname -m)" == x86_64 ]]; }
pause() { echo; read -r -p "${DIM}Enter para volver…${N}" _ || true; }
run() {       # muestra el comando y lo corre
  echo; echo "${DIM}\$ $*${N}"; echo
  "$@"; local rc=$?
  ((rc)) && echo && echo "${Y}⚠ Terminó con código $rc${N}"
  return 0
}
ask() { local a; read -r -p "  $1${2:+ [$2]}: " a; echo "${a:-${2:-}}"; }
yes_no() { local a; read -r -p "  $1 [s/N] " a; [[ "$a" =~ ^[sSyY] ]]; }

# ------------------------------------------------------------ perfiles (servicios opcionales)
PROFILE_LIST=(dns servicios ids speedtest diun botapi)
declare -A PROFILE_DESC=(
  [dns]="AdGuard: DNS sin anuncios para la casa (puerto 53, web :3002)"
  [servicios]="Uptime Kuma (:3001) y Vaultwarden (/vault)"
  [ids]="Suricata: detección de intrusos en la red"
  [speedtest]="Velocidad del internet cada 6 h (:8765)"
  [diun]="Aviso por Telegram cuando hay versión nueva de una imagen"
  [botapi]="Servidor local de la Bot API (archivos de hasta 2 GB)"
)
profiles() { envv COMPOSE_PROFILES | tr ',' '\n' | grep -v '^$' || true; }
has_profile() { profiles | grep -qx "$1"; }
set_profiles() {   # reescribe COMPOSE_PROFILES en el mismo archivo (mismo inodo)
  local v="$1" tmp; tmp="$(mktemp)"
  V="$v" awk 'BEGIN{v=ENVIRON["V"]; d=0} /^COMPOSE_PROFILES=/ {if(!d) print "COMPOSE_PROFILES="v; d=1; next} {print} END{if(!d) print "COMPOSE_PROFILES="v}' .env > "$tmp"
  cat "$tmp" > .env; rm -f "$tmp"
}
toggle_profile() {
  local p="$1" cur new
  cur="$(profiles)"
  if grep -qx "$p" <<<"$cur"; then new="$(grep -vx "$p" <<<"$cur")"; echo "  Apagando ${B}$p${N}…"
  else new="$(printf '%s\n%s' "$cur" "$p")"; echo "  Encendiendo ${B}$p${N}…"; fi
  set_profiles "$(grep -v '^$' <<<"$new" | paste -sd, -)"
  ./scripts/gen-secrets.sh >/dev/null 2>&1 || true          # llaves nuevas que pida (p. ej. Speedtest)
  run ./hl up -d --remove-orphans
}

# ------------------------------------------------------------ encabezado
header() {
  clear 2>/dev/null || true
  local ip port up total ufw im ups
  ip="$(envv HOMELAB_HOST)"; ip="${ip:-$(my_ip)}"; port="$(envv HTTPS_PORT)"; port="${port:-443}"
  read -r up total < <("${D[@]}" compose -f hosts/pi/docker-compose.yml --env-file .env ps -a --format '{{.State}}' 2>/dev/null |
    awk '{t++} /running/{u++} END{print u+0, t+0}')
  if ufw="$(sudo -n ufw status 2>/dev/null | head -1)"; then ufw="${ufw#Status: }"; [[ $ufw == active ]] && ufw="${G}activo${N}" || ufw="${Y}apagado${N}"
  else ufw="${DIM}? (pide sudo)${N}"; fi
  if [[ -f hosts/server/immich/.env ]]; then
    curl -fsS -m 1 http://127.0.0.1:2283/api/server/ping 2>/dev/null | grep -q pong && im="${G}funciona${N}" || im="${R}no contesta${N}"
  else im="${DIM}no instalado${N}"; fi
  ups="${DIM}no configurado${N}"
  [[ -n "$(envv NUT_HOST)" ]] && ups="$(upsc ups@localhost ups.status 2>/dev/null || echo '?')"
  echo "${B}${C_}MyHomeLab${N} · $(hostname) · $(my_ip)"
  echo "  Panel:       https://$ip$([[ $port != 443 ]] && echo ":$port")"
  echo "  Contenedores: $([[ $up == "$total" && $total -gt 0 ]] && echo "${G}$up/$total encendidos${N}" || echo "${Y}$up/$total encendidos${N}")   Firewall: $ufw"
  echo "  Fotos:       $im   No-break: $ups"
  echo "  Opcionales:  $(profiles | paste -sd' ' - | sed 's/^$/ninguno/')"
  echo
}

# ------------------------------------------------------------ secciones
menu_estado() {
  while true; do
    header; echo "${B}Estado y diagnóstico${N}"
    cat <<EOF
  1) Ver contenedores
  2) Diagnóstico completo (qué falta y cómo arreglarlo)
  3) Ver la bitácora de un servicio
  4) Reiniciar un servicio
  5) Diagnóstico de Immich (fotos)
  6) ¿Por qué Prometheus ve el VPS abajo?
  0) Volver
EOF
    case "$(ask "Elige")" in
      1) run ./hl status; pause ;;
      2) run ./hl check; pause ;;
      3) s="$(ask "¿Cuál? (netwatch, bot, caddy, adguard, uptime-kuma, speedtest, diun…)" netwatch)"
         echo "${DIM}(Ctrl+C para salir)${N}"; run ./hl logs "$s"; pause ;;
      4) s="$(ask "¿Cuál? (vacío = todos)")"; run ./hl restart $s; pause ;;
      5) run ./scripts/immich.sh --diagnose; pause ;;
      6) run ./hl vps; pause ;;
      0|"") return ;;
    esac
  done
}

menu_servicios() {
  while true; do
    header; echo "${B}Servicios opcionales${N} (elige un número para encender/apagar)"
    local i=1
    for p in "${PROFILE_LIST[@]}"; do
      if has_profile "$p"; then mark="${G}[encendido]${N}"; else mark="${DIM}[apagado]  ${N}"; fi
      printf '  %d) %s %-10s %s\n' "$i" "$mark" "$p" "${PROFILE_DESC[$p]}"; i=$((i + 1))
    done
    echo "  7) Inicio de sesión único (Authelia) → ./hl sso"
    echo "  0) Volver"
    c="$(ask "Elige")"
    case "$c" in
      [1-6]) p="${PROFILE_LIST[$((c - 1))]}"
             if [[ $p == botapi ]] && ! has_profile botapi && [[ -z "$(envv TELEGRAM_API_ID)" ]]; then
               echo "  ${Y}⚠${N} botapi necesita TELEGRAM_API_ID y TELEGRAM_API_HASH en .env (my.telegram.org)."
               echo "    En la Pi normalmente ya tienes tu propio telegram-bot-api: no lo enciendas aquí."; pause; continue
             fi
             toggle_profile "$p"; pause ;;
      7) if has_profile sso; then yes_no "¿Apagar el inicio de sesión único?" && run ./hl sso --remove
         else run ./hl sso; fi; pause ;;
      0|"") return ;;
    esac
  done
}

menu_fotos() {
  while true; do
    header; echo "${B}Fotos (Immich)${N}"
    cat <<EOF
  1) Estado (versión, espacio, quién reconoce caras)
  2) Diagnóstico «no abre»
  3) Abrir el firewall para la casa (puerto 2283)
  4) Instalar Immich aquí
  5) Actualizar Immich
  6) Usar el reconocimiento de caras de la PC (forma intermedia)
  7) Volver a reconocer caras aquí
  8) (En la PC) solo reconocimiento de caras para el Immich de la Pi
  9) (En la PC) mudar Immich de la Pi a esta máquina
  0) Volver
EOF
    case "$(ask "Elige")" in
      1) run ./scripts/immich.sh --status; pause ;;
      2) run ./scripts/immich.sh --diagnose; pause ;;
      3) run ./scripts/immich.sh --open-firewall; pause ;;
      4) run ./scripts/immich.sh; pause ;;
      5) run ./scripts/immich.sh --update; pause ;;
      6) u="$(ask "Dirección del reconocimiento en la PC" "http://IP-de-la-PC:3003")"; run ./scripts/immich.sh --ml-remote "$u"; pause ;;
      7) run ./scripts/immich.sh --ml-local; pause ;;
      8) p="$(ask "IP de la Pi")"; run ./scripts/immich.sh --ml-only "$p"; pause ;;
      9) p="$(ask "usuario@IP de la Pi (agrega :PUERTO si tu SSH no es el 22)")"; run ./scripts/immich.sh --pull-from "$p"; pause ;;
      0|"") return ;;
    esac
  done
}

menu_respaldos() {
  while true; do
    header; echo "${B}Respaldos${N}"
    cat <<EOF
  1) Respaldo cifrado del homelab ahora
  2) Probar que el último respaldo sirve
  3) Programar la prueba cada mes
  4) Programar el respaldo semanal
  5) Restaurar un respaldo
  6) Respaldo de la PC (si esta es la PC)
  0) Volver
EOF
    case "$(ask "Elige")" in
      1) run ./hl backup; pause ;;
      2) run sudo ./hl test-backup; pause ;;
      3) run sudo ./hl test-backup --install; pause ;;
      4) run sudo ./scripts/backup.sh --install; pause ;;
      5) echo "  Respaldos encontrados:"
         ls -1t "$(envv BACKUPS_DIR)"/homelab/homelab-*.tar.gz.enc backups/homelab/homelab-*.tar.gz.enc 2>/dev/null | head -10 | nl -w4 -s') '
         f="$(ask "Ruta del archivo (copia y pega)")"
         [[ -n $f ]] && yes_no "Esto reemplaza la configuración actual (lo de ahora se guarda aparte). ¿Seguro?" && run ./hl restore "$f"
         pause ;;
      6) run sudo ./scripts/backup-server.sh; pause ;;
      0|"") return ;;
    esac
  done
}

menu_seguridad() {
  while true; do
    header; echo "${B}Seguridad y acceso${N}"
    cat <<EOF
  1) Firewall (UFW): ver el plan y aplicarlo
  2) SSH solo con llave (sin contraseña)
  3) Cambiar la contraseña del panel
  4) Certificado para que el navegador confíe en el panel
  5) Inicio de sesión único (Authelia)
  6) ¿En qué puerto está mi SSH?
  0) Volver
EOF
    case "$(ask "Elige")" in
      1) run ./hl firewall; pause ;;
      2) echo "  ${Y}No cierres esta sesión hasta probar entrar desde otra terminal.${N}"; run ./hl firewall --key-only; pause ;;
      3) run ./hl password; pause ;;
      4) run ./hl ca; pause ;;
      5) run ./hl sso; pause ;;
      6) echo; (HL_REPO="$REPO"; . scripts/lib/ssh.sh; echo "  SSH escucha en: $(ssh_ports | paste -sd' ' -)"); pause ;;
      0|"") return ;;
    esac
  done
}

menu_nobreak() {
  while true; do
    header; echo "${B}No-break (UPS)${N}"
    cat <<EOF
  1) Configurar (con el USB del no-break conectado)
  2) Mandar un aviso de prueba al bot
  3) Ver cómo está ahora
  4) Dejar que la PC lo lea también
  5) Apagar todo si la luz no regresa en N minutos
  0) Volver
EOF
    case "$(ask "Elige")" in
      1) run sudo ./hl ups; pause ;;
      2) run sudo ./hl ups --test; pause ;;
      3) run upsc ups@localhost; pause ;;
      4) p="$(ask "IP de la PC")"; [[ -n $p ]] && run sudo ./hl ups --allow "$p"; pause ;;
      5) m="$(ask "Minutos" 10)"; run sudo ./hl ups --shutdown-after "$m"; pause ;;
      0|"") return ;;
    esac
  done
}

menu_lab() {
  while true; do
    header; echo "${B}Laboratorio de seguridad${N}"
    arch_x86 || echo "  ${Y}Esta máquina es $(uname -m): Wazuh y los objetivos van en una PC x86. Aquí solo el agente (opción 6).${N}"
    cat <<EOF
  1) Instalar / encender Wazuh (SIEM)
  2) Encender los objetivos vulnerables (DVWA, Juice Shop)
  3) Abrir una terminal de Kali en la red aislada
  4) Estado del laboratorio
  5) Apagar el laboratorio
  6) Instalar el agente de Wazuh en ESTA máquina
  7) Guía de prácticas
  0) Volver
EOF
    case "$(ask "Elige")" in
      1) run ./lab/security/lab.sh wazuh; pause ;;
      2) yes_no "¿Con Metasploitable también? (más pesado)" && run ./lab/security/lab.sh targets --metasploitable || run ./lab/security/lab.sh targets; pause ;;
      3) ./lab/security/lab.sh attack; pause ;;
      4) run ./lab/security/lab.sh status; pause ;;
      5) run ./lab/security/lab.sh stop; pause ;;
      6) m="$(ask "IP de la PC con Wazuh")"; [[ -n $m ]] && run sudo ./lab/security/wazuh-agent.sh "$m"; pause ;;
      7) ${PAGER:-less} docs/laboratorio-seguridad.md ;;
      0|"") return ;;
    esac
  done
}

menu_equipos() {
  while true; do
    header; echo "${B}Equipos: PC, mudanza y limpieza${N}"
    cat <<EOF
  1) Encender la PC a distancia (Wake-on-LAN)
  2) (En la PC) instalar lo de la PC y conectarla con la Pi
  3) (En la PC nueva) mudar todo el homelab aquí
  4) Ver qué quitaría la limpieza de la Pi (sin tocar nada)
  5) Limpiar la Pi (quitar todo lo del homelab)
  0) Volver
EOF
    case "$(ask "Elige")" in
      1) out="$(curl -fsS -m 5 http://172.17.0.1:8000/api/wol 2>/dev/null || curl -fsS -m 5 http://127.0.0.1:8000/api/wol 2>/dev/null)"
         if [[ -z $out ]]; then echo "  ${R}✘${N} No pude hablar con el panel."; pause; continue; fi
         python3 -c 'import json,sys; [print(f"  - {t[\"name\"]}: {t[\"mac\"]} ({\"prendido\" if t[\"online\"] else \"apagado\"})") for t in json.load(sys.stdin)["targets"]] or print("  No hay equipos en wake_on_lan (homelab.yml).")' <<<"$out"
         m="$(ask "MAC a encender (copia y pega)")"
         [[ -n $m ]] && curl -fsS -m 5 -X POST -H 'Content-Type: application/json' -d "{\"mac\":\"$m\"}" http://172.17.0.1:8000/api/wol >/dev/null && echo "  ${G}✔${N} Señal enviada"
         pause ;;
      2) p="$(ask "IP de la Pi")"; run ./scripts/install-server.sh ${p:+--pi "$p"}; pause ;;
      3) run ./hl migrate; pause ;;
      4) run sudo ./hl clean --plan; pause ;;
      5) echo "  ${Y}Antes: si AdGuard de la Pi es el DNS de la casa, cámbialo en el módem.${N}"
         run sudo ./hl clean; pause ;;
      0|"") return ;;
    esac
  done
}

menu_principal() {
  while true; do
    header
    cat <<EOF
  ${B}1)${N} Estado y diagnóstico
  ${B}2)${N} Actualizar el homelab (bajar cambios y reconstruir)
  ${B}3)${N} Servicios opcionales (encender/apagar)
  ${B}4)${N} Fotos (Immich)
  ${B}5)${N} Respaldos
  ${B}6)${N} Seguridad y acceso (firewall, SSH, contraseñas)
  ${B}7)${N} No-break
  ${B}8)${N} Laboratorio de seguridad
  ${B}9)${N} Equipos (encender la PC, mudanza, limpieza)
  ${B}a)${N} Ayuda: todos los comandos de ./hl
  ${B}0)${N} Salir
EOF
    case "$(ask "Elige")" in
      1) menu_estado ;;
      2) run ./hl update; ./scripts/gen-secrets.sh >/dev/null 2>&1 || true
         echo "  Sugerencia: corre el diagnóstico (1 → 2)."; pause ;;
      3) menu_servicios ;;
      4) menu_fotos ;;
      5) menu_respaldos ;;
      6) menu_seguridad ;;
      7) menu_nobreak ;;
      8) menu_lab ;;
      9) menu_equipos ;;
      a|A) ./hl help; pause ;;
      0|q|"") echo; exit 0 ;;
    esac
  done
}

[[ -f .env ]] || { echo "Falta .env: corre ./scripts/gen-secrets.sh"; exit 1; }
case "${1:-}" in
  status|estado) menu_estado ;; services|servicios) menu_servicios ;; photos|fotos|immich) menu_fotos ;;
  backups|respaldos) menu_respaldos ;; security|seguridad) menu_seguridad ;; ups|nobreak) menu_nobreak ;;
  lab|laboratorio) menu_lab ;; devices|equipos) menu_equipos ;;
  *) menu_principal ;;
esac
