#!/usr/bin/env bash
# Restablecer la contraseña de un servicio sin ir terminal en terminal (panel → Contraseñas).
#
#   ./hl passwords                         qué servicios se pueden restablecer y si están encendidos
#   sudo ./hl passwords reset grafana      genera una nueva, la aplica y te la muestra UNA vez
#   sudo ./hl passwords reset homeassistant TU_USUARIO
#
# Servicios: grafana adguard vaultwarden-admin speedtest uptime-kuma ntopng homeassistant paperless
#            mealie syncthing umami immich, y «linux» (tu usuario de la Pi, el de sudo)
# Las cuentas del panel (y Authelia, que usa las mismas) se cambian en el panel → Cuentas.
# Guarda la nueva en tu Vaultwarden: aquí no se queda en ningún lado salvo en .env cuando el
# servicio la lee de ahí.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."
if docker info >/dev/null 2>&1; then D=(docker); else D=(sudo docker); fi
C=("${D[@]}" compose -f hosts/pi/docker-compose.yml --env-file .env)
H=("${D[@]}" compose -f hosts/home/docker-compose.yml --env-file hosts/home/.env)
RUN=run/passwords
G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; B=$'\e[1m'; N=$'\e[0m'
SERVICES=(grafana adguard vaultwarden-admin speedtest uptime-kuma ntopng homeassistant paperless mealie syncthing umami immich linux)
declare -A WHAT=(
  [grafana]="Grafana (usuario admin)"
  [adguard]="AdGuard Home (ADGUARD_USER)"
  [vaultwarden-admin]="Vaultwarden: token de /admin (no toca las bóvedas)"
  [speedtest]="Velocidad del internet (Speedtest Tracker)"
  [uptime-kuma]="Uptime Kuma (usuario admin o el que digas)"
  [ntopng]="ntopng: vuelve a admin/admin y te pide cambiarla"
  [mealie]="Recetas y súper (Mealie): correo o usuario"
  [linux]="Tu usuario de Linux en la Pi (el de sudo)"
  [homeassistant]="Home Assistant (un usuario)"
  [paperless]="Paperless (PAPERLESS_ADMIN_USER o el que digas)"
  [syncthing]="Syncthing: acceso a su página"
  [umami]="Umami (usuario admin o el que digas)"
  [immich]="Immich (el administrador)"
)
declare -A CONTAINER=([grafana]=homelab-pi-grafana [adguard]=homelab-pi-adguard [vaultwarden-admin]=homelab-pi-vaultwarden
  [homeassistant]=homelab-home-homeassistant [paperless]=homelab-home-paperless [syncthing]=homelab-home-syncthing
  [umami]=homelab-home-umami [immich]=immich_server [speedtest]=homelab-pi-speedtest
  [uptime-kuma]=homelab-pi-uptime-kuma [ntopng]=homelab-pi-ntopng-redis [mealie]=homelab-home-mealie [linux]=)

newpw() { openssl rand -base64 24 | tr -d '/+=\n' | cut -c1-18; }
envget() { grep -E "^$1=" "${2:-.env}" 2>/dev/null | tail -1 | cut -d= -f2- | sed -E "s/^['\"]|['\"]$//g"; }
putv() {   # mismo archivo, mismo inodo (el panel monta .env)
  local k="$1" v="$2" f="${3:-.env}" tmp; [[ -f $f ]] || return 0
  tmp="$(mktemp)"
  K="$k" V="$v" awk 'BEGIN{k=ENVIRON["K"]; v=ENVIRON["V"]; d=0} $0 ~ "^"k"=" {if(!d) print k"="v; d=1; next} {print} END{if(!d) print k"="v}' "$f" > "$tmp"
  cat "$tmp" > "$f"; rm -f "$tmp"
}
running() { [[ -z ${CONTAINER[$1]} ]] || "${D[@]}" ps --format '{{.Names}}' | grep -q "^${CONTAINER[$1]}"; }
mountsrc() {  # mountsrc CONTENEDOR /ruta/dentro → carpeta en la Pi
  local id; id="$("${D[@]}" ps -q --filter "name=^$1" | head -1)"
  "${D[@]}" inspect "$id" -f "{{range .Mounts}}{{if eq .Destination \"$2\"}}{{.Source}}{{end}}{{end}}"
}
# sqlite_pw COMPOSE… -- SERVICIO ARCHIVO.db SQL USUARIO HASH: apaga el servicio, cambia el hash y lo prende
# (en la Pi, con el sqlite3 de Python: los contenedores no siempre traen uno)
sqlite_pw() {
  local cc=(); while [[ $1 != -- ]]; do cc+=("$1"); shift; done; shift
  local svc="$1" db="$2" sql="$3" u="$4" h="$5" n
  [[ -f $db ]] || fail "No encontré la base $db"
  "${cc[@]}" stop "$svc" >/dev/null 2>&1
  n="$(SQL="$sql" U="$u" HASH="$h" python3 - "$db" <<'PY'
import os, sqlite3, sys
c = sqlite3.connect(sys.argv[1], timeout=30)
cur = c.execute(os.environ["SQL"], {"h": os.environ["HASH"], "u": os.environ["U"]})
c.commit(); print(cur.rowcount)
PY
)" || n=0
  "${cc[@]}" start "$svc" >/dev/null 2>&1
  [[ $n -ge 1 ]] || fail "No existe el usuario «$u»"
}
bcrypt() {  # hash bcrypt con el Python del panel (trae bcrypt)
  printf '%s' "$1" | "${C[@]}" exec -T netwatch python3 -c 'import bcrypt,sys; print(bcrypt.hashpw(sys.stdin.buffer.read(), bcrypt.gensalt(10)).decode())'
}
result() {  # result servicio usuario contraseña nota  → para el panel (se borra al mostrarla)
  mkdir -p "$RUN"; chmod 700 "$RUN"
  ( umask 077; python3 -c 'import json,sys,time; print(json.dumps({"service":sys.argv[1],"user":sys.argv[2],"password":sys.argv[3],"note":sys.argv[4],"ts":time.time()}))' \
      "$1" "$2" "$3" "$4" > "$RUN/$1.json" )
  echo "${G}✔${N} ${B}${WHAT[$1]}${N}"
  if [[ -n ${HL_QUIET:-} ]]; then echo "   contraseña nueva lista en el panel"; return; fi   # nunca a la bitácora
  [[ -n $2 ]] && echo "   usuario:     $2"
  echo "   contraseña:  ${B}$3${N}"
  [[ -n $4 ]] && echo "   $4"
  echo "   Guárdala en tu Vaultwarden: no se vuelve a mostrar."
}
fail() { mkdir -p "$RUN"; ( umask 077; python3 -c 'import json,sys,time; print(json.dumps({"service":sys.argv[1],"error":sys.argv[2],"ts":time.time()}))' "$SVC" "$1" > "$RUN/$SVC.json" ); echo "${R}✘${N} $1"; exit 1; }

cmd="${1:-list}"; shift || true
case "$cmd" in
  list|ls)
    echo "${B}Contraseñas que puedo restablecer${N}  (sudo ./hl passwords reset SERVICIO [USUARIO])"
    for s in "${SERVICES[@]}"; do
      if running "$s"; then st="${G}encendido${N}"; else st="${Y}apagado${N}  "; fi
      printf "  %-18s %b  %s\n" "$s" "$st" "${WHAT[$s]}"
    done
    echo "  Cuentas del panel y de Authelia: panel → Cuentas (o ./hl password para el admin)." ;;

  reset)
    SVC="${1:?Uso: ./hl passwords reset SERVICIO [USUARIO]}"; USR="${2:-}"
    [[ -n ${WHAT[$SVC]:-} ]] || { echo "No conozco «$SVC». Opciones: ${SERVICES[*]}"; exit 1; }
    [[ -z $USR || $USR =~ ^[A-Za-z0-9@._-]{1,64}$ ]] || fail "Usuario inválido"
    running "$SVC" || fail "${WHAT[$SVC]} no está encendido"
    PW="$(newpw)"
    case "$SVC" in
      grafana)
        "${C[@]}" exec -T grafana grafana cli admin reset-admin-password "$PW" >/dev/null 2>&1 \
          || "${C[@]}" exec -T grafana grafana-cli admin reset-admin-password "$PW" >/dev/null 2>&1 \
          || fail "Grafana no aceptó el cambio (./hl logs grafana)"
        putv GRAFANA_ADMIN_PASSWORD "$PW"
        result grafana admin "$PW" "" ;;

      adguard)
        u="${USR:-$(envget ADGUARD_USER)}"; [[ -n $u ]] || fail "No sé el usuario de AdGuard (ADGUARD_USER en .env)"
        hash="$(bcrypt "$PW")" || fail "No pude generar el hash (¿el panel está encendido?)"
        id="$("${C[@]}" ps -q adguard)"
        conf="$("${D[@]}" inspect "$id" -f '{{range .Mounts}}{{if eq .Destination "/opt/adguardhome/conf"}}{{.Source}}{{end}}{{end}}')/AdGuardHome.yaml"
        [[ -f $conf ]] || fail "No encontré AdGuardHome.yaml"
        "${C[@]}" stop adguard >/dev/null 2>&1     # AdGuard reescribe su archivo al apagarse
        cp -a "$conf" "$conf.bak"
        if ! U="$u" HASH="$hash" python3 - "$conf" <<'PY'
import os, sys, yaml
p = sys.argv[1]; c = yaml.safe_load(open(p))
users = c.get("users") or []
hit = [x for x in users if x.get("name") == os.environ["U"]]
if not hit:
    sys.exit(f"No existe el usuario {os.environ['U']} en AdGuard")
hit[0]["password"] = os.environ["HASH"]
yaml.safe_dump(c, open(p, "w"), sort_keys=False, allow_unicode=True)
PY
        then cp -a "$conf.bak" "$conf"; "${C[@]}" start adguard >/dev/null 2>&1; fail "No existe el usuario $u en AdGuard"; fi
        "${C[@]}" start adguard >/dev/null 2>&1
        [[ $u == "$(envget ADGUARD_USER)" ]] && { putv ADGUARD_PASSWORD "$PW"; putv ADGUARD_PASSWORD "$PW" hosts/home/.env 2>/dev/null || true
          "${C[@]}" up -d netwatch >/dev/null 2>&1 || true; }
        result adguard "$u" "$PW" "El panel ya usa la nueva (ADGUARD_PASSWORD en .env)." ;;

      vaultwarden-admin)
        PW="$(newpw)$(newpw)"
        putv VW_ADMIN_TOKEN "$PW"
        "${C[@]}" up -d vaultwarden >/dev/null 2>&1 || fail "No pude reiniciar Vaultwarden"
        result vaultwarden-admin "" "$PW" "Es el token de https://…/vault/admin. Las bóvedas y sus contraseñas maestras no cambian." ;;

      speedtest)
        db="$(mountsrc homelab-pi-speedtest /config)"; db="$(ls "$db"/database.sqlite "$db"/*/database.sqlite 2>/dev/null | head -1 || true)"
        u="${USR:-$(python3 -c 'import sqlite3,sys; print(sqlite3.connect(sys.argv[1]).execute("SELECT email FROM users ORDER BY id LIMIT 1").fetchone()[0])' "$db" 2>/dev/null || true)}"
        hash="$(bcrypt "$PW")" || fail "No pude generar el hash"
        sqlite_pw "${C[@]}" --profile speedtest -- speedtest "$db" \
          "UPDATE users SET password = :h WHERE email = :u OR name = :u" "$u" "${hash/#\$2b\$/\$2y\$}"   # Laravel: $2y$
        putv SPEEDTEST_ADMIN_PASSWORD "$PW"
        result speedtest "$u" "$PW" "Entra con tu correo." ;;

      uptime-kuma)
        u="${USR:-admin}"; db="$(mountsrc homelab-pi-uptime-kuma /app/data)/kuma.db"
        hash="$(bcrypt "$PW")" || fail "No pude generar el hash"
        if [[ -z $USR ]]; then
          u="$(python3 -c 'import sqlite3,sys; print(sqlite3.connect(sys.argv[1]).execute("SELECT username FROM user ORDER BY id LIMIT 1").fetchone()[0])' "$db" 2>/dev/null || echo admin)"
        fi
        sqlite_pw "${C[@]}" --profile servicios -- uptime-kuma "$db" \
          "UPDATE user SET password = :h WHERE username = :u" "$u" "$hash"
        result uptime-kuma "$u" "$PW" "" ;;

      ntopng)
        "${C[@]}" --profile ntopng exec -T ntopng-redis redis-cli del ntopng.user.admin.password >/dev/null 2>&1 \
          || fail "No pude hablar con el Redis de ntopng"
        "${C[@]}" --profile ntopng restart ntopng >/dev/null 2>&1 || true
        PW=admin
        result ntopng admin admin "Al entrar te pide poner una nueva: ponla y guárdala." ;;

      mealie)
        db="$(mountsrc homelab-home-mealie /app/data)/mealie.db"
        u="${USR:-$(python3 -c 'import sqlite3,sys; print(sqlite3.connect(sys.argv[1]).execute("SELECT email FROM users WHERE admin=1 ORDER BY created_at LIMIT 1").fetchone()[0])' "$db" 2>/dev/null || true)}"
        hash="$(bcrypt "$PW")" || fail "No pude generar el hash"
        sqlite_pw "${H[@]}" --profile mealie -- mealie "$db" \
          "UPDATE users SET password = :h, login_attemps = 0, locked_at = NULL WHERE email = :u OR username = :u" "$u" "$hash"
        result mealie "$u" "$PW" "Entra con ese correo (o usuario). Si estaba bloqueada por intentos, ya se desbloqueó." ;;

      linux)
        u="${USR:-$(stat -c %U .)}"
        [[ $EUID -eq 0 ]] || fail "Necesito sudo: sudo ./hl passwords reset linux"
        id "$u" >/dev/null 2>&1 || fail "No existe el usuario $u"
        [[ $u != root ]] || fail "No cambio la de root"
        printf '%s:%s\n' "$u" "$PW" | chpasswd || fail "chpasswd falló"
        result linux "$u" "$PW" "Es la de sudo y la de entrar en la pantalla. Tu SSH sigue con llave (no cambia)." ;;

      homeassistant)
        HP=("${H[@]}" --profile homeassistant)
        users="$("${HP[@]}" exec -T homeassistant hass --script auth --config /config list 2>/dev/null | awk 'NF && $0 !~ /Total/ {print $1}')"
        u="${USR:-$(head -1 <<<"$users")}"
        grep -qx -- "$u" <<<"$users" || fail "Usuarios de Home Assistant: $(paste -sd' ' <<<"$users"). Elige uno."
        "${HP[@]}" stop homeassistant >/dev/null 2>&1
        "${HP[@]}" run --rm --no-deps --entrypoint hass homeassistant --script auth --config /config change_password "$u" "$PW" >/dev/null 2>&1 \
          || { "${HP[@]}" start homeassistant >/dev/null 2>&1; fail "Home Assistant no aceptó el cambio"; }
        "${HP[@]}" start homeassistant >/dev/null 2>&1
        result homeassistant "$u" "$PW" "Home Assistant se reinició (tarda ~1 min en volver)." ;;

      paperless)
        u="${USR:-$(envget PAPERLESS_ADMIN_USER hosts/home/.env)}"; u="${u:-admin}"
        "${H[@]}" --profile paperless exec -T -e U="$u" -e NEWPW="$PW" paperless python3 manage.py shell -c \
          "import os,sys; from django.contrib.auth.models import User; q=User.objects.filter(username=os.environ['U']); q.exists() or sys.exit(3); u=q.first(); u.set_password(os.environ['NEWPW']); u.save()" \
          >/dev/null 2>&1 || fail "No existe el usuario $u en Paperless (o no respondió)"
        [[ $u == "$(envget PAPERLESS_ADMIN_USER hosts/home/.env)" || -z $USR ]] && putv PAPERLESS_ADMIN_PASSWORD "$PW" hosts/home/.env
        result paperless "$u" "$PW" "" ;;

      syncthing)
        SC=("${H[@]}" --profile syncthing exec -T syncthing syncthing cli --home=/var/syncthing/config config gui)
        "${SC[@]}" user set admin >/dev/null 2>&1 && "${SC[@]}" password set "$PW" >/dev/null 2>&1 \
          || fail "Syncthing no aceptó el cambio"
        result syncthing admin "$PW" "" ;;

      umami)
        u="${USR:-admin}"
        hash="$(bcrypt "$PW")" || fail "No pude generar el hash (¿el panel está encendido?)"
        n="$(printf 'UPDATE "user" SET password = :%s WHERE username = :%s RETURNING 1;\n' "'h'" "'u'" \
          | "${H[@]}" --profile umami exec -T umami-db psql -U umami -d umami -v h="$hash" -v u="$u" -qtA 2>&1 | grep -x 1 || true)"
        [[ $n == 1 ]] || fail "No existe el usuario $u en Umami"
        result umami "$u" "$PW" "" ;;

      immich)
        out="$(printf '\n' | "${D[@]}" exec -i immich_server immich-admin reset-admin-password 2>&1 || true)"
        PW="$(grep -oE 'updated to:? *[^ ]+' <<<"$out" | awk '{print $NF}' | tail -1)"
        [[ -n $PW ]] || fail "Immich no dio una contraseña nueva. Hazlo a mano: docker exec -it immich_server immich-admin reset-admin-password"
        result immich "(administrador)" "$PW" "Immich la generó; cámbiala por una tuya al entrar si quieres." ;;
    esac ;;

  -h|--help|help) sed -n 2,13p "$0" ;;
  *) echo "Comando desconocido: $cmd"; sed -n 2,13p "$0"; exit 1 ;;
esac
