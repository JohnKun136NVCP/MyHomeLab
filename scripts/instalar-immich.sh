#!/usr/bin/env bash
# ======================================================================
#  Immich: las fotos y videos de los celulares se respaldan SOLOS.
#  Funciona en la Raspberry (64 bits, 8 GB) o en la PC.
#
#    ./scripts/instalar-immich.sh                 instala aquí (pregunta dónde guardar las fotos)
#    ./scripts/instalar-immich.sh --si            igual, con los valores sugeridos
#    ./scripts/instalar-immich.sh --actualizar    baja la versión nueva de Immich y reinicia
#    ./scripts/instalar-immich.sh --estado        versión, fotos, dónde vive y quién reconoce caras
#    ./scripts/instalar-immich.sh --diagnostico   «no abre»: revisa contenedores, red y firewall
#    ./scripts/instalar-immich.sh --abrir-firewall  deja entrar a la casa al 2283
#
#  Forma intermedia (Immich en la Pi, reconocimiento de caras en la PC):
#    (en la PC)  ./scripts/instalar-immich.sh --solo-ml IP-de-la-Pi
#    (en la Pi)  ./scripts/instalar-immich.sh --ml-remoto http://IP-de-la-PC:3003
#    (en la Pi)  ./scripts/instalar-immich.sh --ml-local       todo otra vez en la Pi
#
#  Mudar Immich completo (fotos, base, cuentas, álbumes) de la Pi a la PC:
#    (en la PC)  ./scripts/instalar-immich.sh --traer-de usuario@IP-de-la-Pi[:puerto-ssh]
#    (migrar.sh lo hace solo si la Pi ya tenía Immich)
#
#  Usa el docker-compose.yml y .env OFICIALES de Immich (la base va fijada por versión).
#  Quedan en hosts/server/immich/. Web: http://IP:2283 · App: «Immich».
# ======================================================================
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
YES=0; MODE=install; ARG=""; FORCE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --si|-y) YES=1 ;;
    --actualizar) MODE=update ;;
    --estado) MODE=status ;;
    --diagnostico|--diagnóstico) MODE=diag ;;
    --abrir-firewall) MODE=openfw ;;
    --solo-ml) MODE=mlonly; ARG="${2:-}"; [[ -n $ARG && $ARG != -* ]] && shift || ARG="" ;;
    --ml-remoto) MODE=mlremote; ARG="${2:?Falta la URL, ej. http://192.168.1.50:3003}"; shift ;;
    --ml-local) MODE=mllocal ;;
    --traer-de) MODE=pull; ARG="${2:?Falta usuario@IP de la Pi}"; shift ;;
    --exportar) MODE=export; ARG="${2:-}"; [[ -n $ARG && $ARG != -* ]] && shift || ARG="" ;;
    --fin-exportar) MODE=exportdone; ARG="${2:-}"; [[ -n $ARG && $ARG != -* ]] && shift || ARG="" ;;
    --forzar) FORCE=1 ;;
    -h|--help) sed -n 3,22p "$0" | sed -E 's/^# ?//'; exit 0 ;;
    *) echo "Opción desconocida: $1"; exit 2 ;;
  esac; shift
done
DIR=hosts/server/immich
REL=https://github.com/immich-app/immich/releases
G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; B=$'\e[1m'; N=$'\e[0m'
ok() { echo "  ${G}✔${N} $*"; }; warn() { echo "  ${Y}⚠${N} $*"; }; die() { echo "${R}✘${N} $*"; exit 1; }
if docker info >/dev/null 2>&1; then D=(docker); else D=(sudo docker); fi
mkdir -p "$DIR"
envget() { grep -E "^$1=" "$DIR/.env" 2>/dev/null | tail -1 | cut -d= -f2- || true; }
envset() {
  local k="$1" v="$2"
  if grep -qE "^#? *$k=" "$DIR/.env"; then sed -i -E "s|^#? *$k=.*|$k=$v|" "$DIR/.env"; else echo "$k=$v" >> "$DIR/.env"; fi
}
compose() {
  local f=(-f "$DIR/docker-compose.yml")
  [[ -f "$DIR/homelab.override.yml" ]] && f+=(-f "$DIR/homelab.override.yml")
  "${D[@]}" compose "${f[@]}" --env-file "$DIR/.env" "$@"
}
my_ip() { ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}'; }
# Redes de la casa: las de homelab.yml; si no hay, la red de la tarjeta por la que sale a internet
# (antes tomaba la primera ruta del sistema, que a veces es la de Docker: 172.17.0.0/16)
lan_nets() {
  local n=""
  [[ -f config/homelab.yml ]] && n="$(python3 - <<'PY' 2>/dev/null || true
import yaml
c = yaml.safe_load(open("config/homelab.yml")) or {}
fw = (c.get("firewall") or {}).get("lan_networks") or []
nets = fw or [x.get("cidr") for x in (c.get("network") or {}).get("networks") or [] if x.get("cidr") and x.get("enabled", True)]
print("\n".join(nets))
PY
)"
  if [[ -z $n ]]; then
    local dev; dev="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev") print $(i+1)}')"
    n="$(ip -4 -o addr show dev "$dev" 2>/dev/null | awk '{print $4}' | head -1 | python3 -c 'import ipaddress,sys; print(ipaddress.ip_interface(sys.stdin.read().strip()).network)' 2>/dev/null || true)"
  fi
  printf '%s\n' "${n:-192.168.1.0/24}"
}
lan_net() { lan_nets | head -1; }
ufw_on() { command -v ufw >/dev/null && sudo ufw status 2>/dev/null | grep -q "Status: active"; }
running_version() {   # versión de Immich que corre en HOST (http://HOST:2283)
  curl -fsS -m 5 "http://$1:2283/api/server/version" 2>/dev/null | python3 -c \
    'import json,sys; d=json.load(sys.stdin); print(f"v{d[\"major\"]}.{d[\"minor\"]}.{d[\"patch\"]}")' 2>/dev/null || true
}

# Baja el compose oficial (de la última versión o de una versión fija) y el .env de ejemplo
fetch() {
  local ver="${1:-}" base="$REL/latest/download"
  [[ -n $ver ]] && base="$REL/download/$ver"
  curl -fsSL "$base/docker-compose.yml" -o "$DIR/docker-compose.yml.new"
  grep -q "immich-server" "$DIR/docker-compose.yml.new" || die "No pude bajar el compose de Immich ${ver:-(última)}"
  mv "$DIR/docker-compose.yml.new" "$DIR/docker-compose.yml"
  if [[ ! -f "$DIR/.env" ]]; then
    curl -fsSL "$base/example.env" -o "$DIR/.env"; chmod 600 "$DIR/.env"
  fi
  [[ -n $ver ]] && envset IMMICH_VERSION "$ver"
  return 0
}

# Aceleración del reconocimiento de caras/búsqueda: gráfica Intel → OpenVINO (solo PC x86)
hwaccel() {
  [[ "$(uname -m)" == x86_64 ]] || return 0
  lspci 2>/dev/null | grep -Ei 'vga|display' | grep -qi intel && [[ -e /dev/dri/renderD128 ]] || return 0
  curl -fsSL "$REL/latest/download/hwaccel.ml.yml" -o "$DIR/hwaccel.ml.yml" || return 0
  python3 - "$DIR/docker-compose.yml" <<'PY' && ok "Reconocimiento con la gráfica Intel (OpenVINO)" || warn "No pude activar OpenVINO; el reconocimiento usa el procesador"
import re, sys, yaml
p = sys.argv[1]; orig = open(p).read(); out = []; inml = False; done = 0
if "-openvino" in orig:
    sys.exit(0)
for ln in orig.splitlines():
    if re.match(r"^  [a-z]", ln):
        inml = ln.strip().startswith("immich-machine-learning:")
    ind = re.match(r"^(\s*)", ln).group(1)
    if inml and re.match(r"^\s+image: ghcr.io/immich-app/immich-machine-learning:\$\{IMMICH_VERSION:-release\}\s*$", ln):
        ln = ln.rstrip() + "-openvino"; done += 1
    elif inml and re.match(r"^\s+# extends:", ln):
        ln = f"{ind}extends:"; done += 1
    elif inml and re.match(r"^\s+#\s+file: hwaccel.ml.yml", ln):
        ln = f"{ind}  file: hwaccel.ml.yml"; done += 1
    elif inml and re.match(r"^\s+#\s+service: ", ln):
        ln = f"{ind}  service: openvino"; done += 1
    out.append(ln)
new = "\n".join(out) + "\n"
ml = (yaml.safe_load(new)["services"]["immich-machine-learning"])
assert done == 4 and ml["extends"]["service"] == "openvino"
open(p, "w").write(new)
PY
}

# Configura el .env nuevo: carpeta de fotos, base de datos, contraseña, zona horaria
first_env() {
  local fotos="${1:-}" def="/srv/fotos" dbdir
  if [[ -z $fotos ]]; then
    if ((YES)); then fotos="$def"; else read -r -p "  Carpeta para las fotos y videos (en el disco grande / SSD) [$def]: " fotos; fotos="${fotos:-$def}"; fi
  fi
  sudo mkdir -p "$fotos"; sudo chown "$(id -u):$(id -g)" "$fotos"
  envset UPLOAD_LOCATION "$fotos"
  # La base va en un disco local rápido. Si el repo vive en la microSD, mejor junto a las fotos (SSD).
  dbdir="$REPO/$DIR/postgres"
  if findmnt -no SOURCE --target "$REPO" 2>/dev/null | grep -q mmcblk; then
    dbdir="$(dirname "$fotos")/immich-postgres"
    warn "El repo está en la microSD: la base de Immich va en $dbdir (junto a las fotos) para no gastar la tarjeta"
  fi
  sudo mkdir -p "$dbdir"
  envset DB_DATA_LOCATION "$dbdir"
  [[ -n "${KEEP_DB_PASSWORD:-}" ]] || envset DB_PASSWORD "$(openssl rand -hex 16)"   # solo letras y números
  envset TZ "$(timedatectl show -p Timezone --value 2>/dev/null || echo America/Mexico_City)"
  ok "Fotos en $fotos · base en $dbdir"
}

open_fw() {
  ufw_on || return 0
  local n
  for n in $(lan_nets); do
    sudo ufw allow from "$n" to any port 2283 proto tcp comment "homelab immich" >/dev/null && ok "Firewall: 2283 abierto para $n"
  done
}

set_link() {   # botón «Fotos (Immich)» en el panel, apuntando a URL
  local url="$1"
  [[ -f config/homelab.yml ]] || return 0
  python3 - "$url" <<'PY'
import sys, yaml
p = "config/homelab.yml"; c = yaml.safe_load(open(p)) or {}
links = [l for l in c.get("links") or [] if l.get("name") != "Fotos (Immich)"]
links.append({"name": "Fotos (Immich)", "url": sys.argv[1], "icon": "📸"})
c["links"] = links
with open(p, "w") as f:
    yaml.safe_dump(c, f, sort_keys=False, allow_unicode=True)
PY
}

wait_db() {
  local u; u="$(envget DB_USERNAME)"; u="${u:-postgres}"
  for _ in $(seq 1 60); do
    "${D[@]}" exec immich_postgres pg_isready -U "$u" >/dev/null 2>&1 && return 0; sleep 2
  done
  die "La base de Immich no arrancó: ${D[*]} logs immich_postgres"
}

finish_msg() {
  local ip; ip="$(my_ip)"
  cat <<EOF

${G}${B}✅ Immich listo:${N} http://$ip:2283
  1. Ábrelo y crea tu cuenta: la primera es la de administrador.
  2. Administración → Usuarios → crea una cuenta para cada quien (mamá, papá…).
  3. Administración → Configuración → Copia de seguridad: deja activa la copia diaria de la base
     (el respaldo cifrado del homelab se lleva la más reciente).
  4. En cada celular: app «Immich» → servidor http://$ip:2283 → entra → ☁ Respaldo → elige
     «Cámara» (y WhatsApp si quieres) → activa respaldo en segundo plano.
  Guía para la familia: docs/fotos.md
EOF
}

case "$MODE" in
# ------------------------------------------------------------------ instalar aquí
install|update)
  [[ "$(uname -m)" =~ ^(x86_64|aarch64)$ ]] || die "Immich necesita un sistema de 64 bits (x86_64 o aarch64); este es $(uname -m)."
  mem=$(awk '/MemTotal/{print int($2/1048576+0.5)}' /proc/meminfo)
  ((mem >= 6)) || warn "Immich pide 6 GB de RAM y aquí hay ${mem} GB: apaga el reconocimiento de caras o usa --ml-remoto."
  echo "${B}== Bajando Immich (versión oficial más reciente)${N}"
  fetch
  [[ $MODE == update ]] && envset IMMICH_VERSION release
  [[ -n "$(envget UPLOAD_LOCATION)" && "$(envget UPLOAD_LOCATION)" != ./library ]] || first_env
  hwaccel
  echo "${B}== Levantando Immich${N}"
  compose pull -q
  compose up -d
  [[ $MODE == update ]] && { ok "Immich actualizado a $(sleep 8; running_version 127.0.0.1)"; exit 0; }
  open_fw
  set_link "http://$(my_ip):2283"
  finish_msg
  if [[ "$(uname -m)" == aarch64 ]]; then
    echo
    echo "  En la Raspberry el reconocimiento de caras funciona, pero lento (la primera pasada tarda horas)."
    echo "  Cuando tengas la PC:  (PC) ./scripts/instalar-immich.sh --solo-ml $(my_ip)"
    echo "                        (Pi) ./scripts/instalar-immich.sh --ml-remoto http://IP-de-la-PC:3003"
    echo "  O mudar todo a la PC: (PC) ./scripts/instalar-immich.sh --traer-de $(id -un)@$(my_ip)"
  fi
  ;;

# ------------------------------------------------------------------ diagnóstico
openfw)
  ufw_on || { ok "UFW está apagado: no bloquea nada."; exit 0; }
  open_fw ;;
diag)
  bad=0; IP="$(my_ip)"
  pass() { echo "  ${G}✔${N} $*"; }; fail() { echo "  ${R}✘${N} $*"; bad=1; }; hint() { echo "      → $*"; }
  echo "${B}== Diagnóstico de Immich${N}"
  if [[ ! -f "$DIR/.env" ]]; then
    fail "Immich no está instalado en esta máquina ($DIR/.env no existe)"
    hint "Instálalo:  ./scripts/instalar-immich.sh"; exit 1
  fi
  pass "Instalado (fotos en $(envget UPLOAD_LOCATION))"
  for c in immich_server immich_machine_learning immich_redis immich_postgres; do
    st="$("${D[@]}" ps -a --filter "name=^${c}\$" --format '{{.Status}}' | head -1)"
    if [[ -z $st ]]; then fail "$c no existe"; hint "./scripts/instalar-immich.sh  (lo vuelve a crear sin borrar fotos)"
    elif [[ $st == Up*unhealthy* ]]; then fail "$c: $st"; hint "${D[*]} logs --tail 40 $c"
    elif [[ $st == Up* ]]; then pass "$c: $st"
    else fail "$c: $st"; hint "${D[*]} logs --tail 40 $c   y luego  ./scripts/instalar-immich.sh --actualizar"; fi
  done
  if curl -fsS -m 5 http://127.0.0.1:2283/api/server/ping 2>/dev/null | grep -q pong; then pass "Contesta en la propia máquina (127.0.0.1:2283)"
  else fail "No contesta ni en la propia máquina"; hint "Si acaba de arrancar espera 1–2 min; si no:  ${D[*]} logs --tail 40 immich_server"; fi
  if curl -fsS -m 5 "http://$IP:2283/api/server/ping" 2>/dev/null | grep -q pong; then pass "Contesta en la red: http://$IP:2283"
  else fail "No contesta en http://$IP:2283"; fi
  sudo ss -ltnH 'sport = :2283' | grep -q . && pass "El puerto 2283 está escuchando" || { fail "Nadie escucha en el 2283"; hint "¿Otro programa usa el puerto?  sudo ss -ltnp | grep 2283"; }
  # Firewall. Docker normalmente se salta UFW, salvo que Docker tenga "iptables": false o haya reglas en DOCKER-USER
  if ufw_on; then
    rules="$(sudo ufw status | grep -E '^2283' || true)"
    if [[ -n $rules ]]; then pass "UFW tiene regla para el 2283:"; sed 's/^/        /' <<<"$rules"
      for n in $(lan_nets); do grep -q "$n" <<<"$rules" || { fail "…pero no para tu red $n"; hint "./scripts/instalar-immich.sh --abrir-firewall"; }; done
    else
      fail "UFW está activo y no tiene regla para el 2283"; hint "./scripts/instalar-immich.sh --abrir-firewall"
    fi
    grep -qs '"iptables"[[:space:]]*:[[:space:]]*false' /etc/docker/daemon.json && \
      echo "      (Docker tiene \"iptables\": false: aquí UFW SÍ decide quién entra a los contenedores)"
    if sudo iptables -S DOCKER-USER 2>/dev/null | grep -qE -- '-j (DROP|REJECT)'; then
      fail "Hay reglas que bloquean en DOCKER-USER (por ejemplo, de ufw-docker)"
      hint "sudo iptables -S DOCKER-USER   ·   con ufw-docker:  sudo ufw route allow proto tcp from $(lan_net) to any port 2283"
    fi
  else
    pass "UFW apagado (no bloquea)"
  fi
  echo
  if ((bad)); then echo "Arregla lo marcado con ${R}✘${N} y vuelve a correr:  ./scripts/instalar-immich.sh --diagnostico"
  else echo "${G}Todo bien.${N} Desde otro equipo de la casa abre http://$IP:2283"
       echo "(Si aun así no abre desde el celular: ¿está en la misma Wi-Fi? La red de invitados de la Tenda no ve la Pi.)"; fi
  exit $bad
  ;;

# ------------------------------------------------------------------ estado
status)
  [[ -f "$DIR/.env" ]] || { echo "Immich no está instalado en esta máquina."; exit 0; }
  echo "Versión:  $(running_version 127.0.0.1 || echo '(no responde)')   ·   http://$(my_ip):2283"
  echo "Fotos:    $(envget UPLOAD_LOCATION)  ($(du -sh "$(envget UPLOAD_LOCATION)" 2>/dev/null | cut -f1 || echo ?))"
  echo "Base:     $(envget DB_DATA_LOCATION)"
  if [[ -f "$DIR/homelab.override.yml" ]] && grep -q ml-solo "$DIR/homelab.override.yml"; then
    echo "Modo:     solo reconocimiento de caras (para otro Immich)"
  else
    echo "Reconocimiento local: $("${D[@]}" ps --filter name=immich_machine_learning --format '{{.Status}}' | head -1 || echo apagado)"
  fi
  [[ -f "$DIR/.ml-remoto" ]] && echo "Reconocimiento en:    $(cat "$DIR/.ml-remoto") (primero), el local de respaldo"
  compose ps --format 'table {{.Service}}\t{{.Status}}' 2>/dev/null || true
  ;;

# ------------------------------------------------------------------ PC: solo reconocimiento de caras
mlonly)
  if [[ -f "$DIR/.env" ]] && grep -q '^UPLOAD_LOCATION=/' "$DIR/.env" && ! grep -qs ml-solo "$DIR/homelab.override.yml"; then
    die "Aquí ya hay un Immich completo; --solo-ml es para una PC que ayuda al Immich de la Pi."
  fi
  PI="$ARG"
  ver=""; [[ -n $PI ]] && ver="$(running_version "$PI")"
  [[ -n $PI && -z $ver ]] && warn "No pude preguntar la versión a http://$PI:2283; uso la más reciente (deben coincidir)."
  echo "${B}== Reconocimiento de caras de Immich (para el Immich de ${PI:-la Pi})${N}"
  fetch "$ver"
  cat > "$DIR/homelab.override.yml" <<'EOF'
# Generado por instalar-immich.sh --solo-ml: esta máquina solo hace el reconocimiento de caras
# y la búsqueda inteligente para el Immich de la Pi (marca: ml-solo).
services:
  immich-machine-learning:
    ports: ["3003:3003"]
EOF
  hwaccel
  compose pull -q immich-machine-learning
  compose up -d immich-machine-learning
  if ufw_on; then
    sudo ufw allow from "${PI:-$(lan_net)}" to any port 3003 proto tcp comment "homelab immich ml" >/dev/null
    ok "Firewall: el 3003 solo para ${PI:-la red de la casa}"
  fi
  sleep 5
  curl -fsS -m 5 http://127.0.0.1:3003/ping >/dev/null 2>&1 && ok "Reconocimiento de caras listo en http://$(my_ip):3003" \
    || warn "Todavía arrancando; revisa en un minuto: curl http://127.0.0.1:3003/ping"
  echo
  echo "  Ahora en la Pi:  ./scripts/instalar-immich.sh --ml-remoto http://$(my_ip):3003"
  echo "  Cuando actualices Immich en la Pi, corre aquí otra vez:  ./scripts/instalar-immich.sh --solo-ml ${PI:-IP-de-la-Pi}"
  ;;

# ------------------------------------------------------------------ Pi: usar el de la PC
mlremote)
  [[ -f "$DIR/.env" ]] || die "Primero instala Immich aquí."
  URL="${ARG%/}"
  if curl -fsS -m 5 "$URL/ping" >/dev/null 2>&1; then ok "La PC contesta en $URL"
  else warn "$URL no contesta ahora (¿PC apagada?). Igual lo configuras; Immich usará el local mientras tanto."; fi
  echo "$URL" > "$DIR/.ml-remoto"
  cat <<EOF

  Falta un paso en la web de Immich (se guarda en su base, no en archivos):
    Administración → Configuración → ${B}Aprendizaje automático${N} → URL:
      1. ${B}$URL${N}                              (la PC: rápida)
      2. ${B}http://immich-machine-learning:3003${N}   (la Pi: respaldo si la PC está apagada)
    → Guardar. Luego en Administración → Trabajos puedes lanzar «Detección de caras» y
      «Búsqueda inteligente» sobre todo para que la PC haga el trabajo pesado.

  El reconocimiento local de la Pi se queda encendido como respaldo (casi no usa memoria si
  no trabaja). Si lo quieres apagado:  ${D[*]} stop immich_machine_learning
EOF
  ;;
mllocal)
  rm -f "$DIR/.ml-remoto"
  compose up -d immich-machine-learning
  echo "  En la web: Administración → Configuración → Aprendizaje automático → deja solo"
  echo "  http://immich-machine-learning:3003 → Guardar."
  ;;

# ------------------------------------------------------------------ (en la Pi) preparar la mudanza
export)
  [[ -f "$DIR/.env" ]] || die "Aquí no hay Immich."
  UL="$(envget UPLOAD_LOCATION)"; [[ $UL == /* ]] || UL="$REPO/$DIR/$UL"
  u="$(envget DB_USERNAME)"; u="${u:-postgres}"
  echo "${B}== Preparando Immich para mudarse${N}"
  compose stop immich-server immich-machine-learning >/dev/null 2>&1 || true   # nadie sube mientras copiamos
  "${D[@]}" exec immich_postgres pg_isready -U "$u" >/dev/null 2>&1 || { compose up -d database; wait_db; }
  command -v rsync >/dev/null || sudo apt-get install -y -qq rsync >/dev/null
  # Sin -t: con terminal, los saltos de línea saldrían como \r\n y dañarían los datos
  "${D[@]}" exec immich_postgres pg_dumpall --clean --if-exists --username="$u" | gzip > /tmp/immich-migracion.sql.gz
  sudo install -m 600 /tmp/immich-migracion.sql.gz "$UL/immich-migracion.sql.gz"; rm -f /tmp/immich-migracion.sql.gz
  sudo install -m 600 "$DIR/.env" "$UL/immich-migracion.env"
  # Las fotos son de root (las escribe el contenedor): permiso temporal para copiarlas con rsync
  if [[ -n $ARG ]]; then
    printf '%s ALL=(root) NOPASSWD: /usr/bin/rsync\n' "$ARG" | sudo tee /etc/sudoers.d/90-homelab-immich-mudanza >/dev/null
    sudo chmod 440 /etc/sudoers.d/90-homelab-immich-mudanza
    sudo visudo -cf /etc/sudoers.d/90-homelab-immich-mudanza >/dev/null || { sudo rm -f /etc/sudoers.d/90-homelab-immich-mudanza; die "sudoers inválido"; }
  fi
  ok "Base exportada ($(du -h "$UL/immich-migracion.sql.gz" | cut -f1)). Immich queda en pausa aquí."
  echo "UPLOAD=$UL"
  echo "DBVER=$(grep -E '^IMMICH_VERSION=' "$DIR/.env" | cut -d= -f2-)"
  ;;
exportdone)   # (en la Pi) termina la mudanza: quita el permiso temporal y apunta el botón a la PC
  sudo rm -f /etc/sudoers.d/90-homelab-immich-mudanza
  if [[ -n $ARG ]]; then
    set_link "$ARG"
    compose down >/dev/null 2>&1 || true
    ok "Immich apagado en la Pi; el botón del panel ahora abre $ARG"
    echo "  Las fotos siguen en $(envget UPLOAD_LOCATION) de la Pi: bórralas cuando revises que en la PC está todo."
  else
    compose up -d >/dev/null 2>&1 || true
    warn "Mudanza cancelada: Immich vuelve a funcionar en la Pi."
  fi
  ;;

# ------------------------------------------------------------------ (en la PC) traer Immich de la Pi
pull)
  HL_REPO="$REPO"; . scripts/lib/ssh.sh
  ssh_target "$ARG"
  PI="$SSH_HOST"
  [[ -f "$DIR/.env" && $FORCE -eq 0 ]] && grep -q '^UPLOAD_LOCATION=/' "$DIR/.env" && [[ ! -f "$DIR/homelab.override.yml" ]] && \
    die "Aquí ya hay un Immich con sus propias fotos. Usa --forzar si de verdad quieres reemplazarlo."
  echo "${B}== Mudando Immich de la Pi ($SSH_IP) a esta máquina${N}"
  PIREPO="$("${RSSH[@]}" "$PI" 'for d in ~/MyHomeLab ~/myhomelab; do [ -f "$d/hl" ] && { echo "$d"; break; }; done')"
  [[ -n $PIREPO ]] || die "No encontré el repo del homelab en la Pi."
  ver="$(running_version "$SSH_IP")"
  [[ -n $ver ]] || die "El Immich de la Pi no contesta en http://$SSH_IP:2283 (debe estar prendido para saber su versión)."
  ok "Immich de la Pi: $ver"
  echo "  La Pi pausa Immich y exporta su base (te puede pedir la contraseña de sudo de la Pi)…"
  command -v rsync >/dev/null || die "Falta rsync en esta máquina (sudo apt install rsync)."
  out="$("${RSSH[@]}" -t "$PI" "cd $PIREPO && ./scripts/instalar-immich.sh --exportar $SSH_USER" | tr -d '\r' | tee /dev/stderr)"
  UL_PI="$(sed -n 's/^UPLOAD=//p' <<<"$out" | tail -1)"
  [[ -n $UL_PI ]] || { echo "$out"; die "La Pi no pudo exportar Immich."; }
  # Si algo falla de aquí en adelante, Immich vuelve a funcionar en la Pi como estaba
  cancel() { "${RSSH[@]}" -t "$PI" "cd $PIREPO && ./scripts/instalar-immich.sh --fin-exportar" || true; }
  trap 'rc=$?; if ((rc)); then echo; warn "Algo falló: regreso Immich a la Pi (aquí puedes volver a intentar)."; cancel; fi' EXIT

  # Configuración: misma contraseña de base (va dentro del respaldo) y misma versión
  rm -f "$DIR/homelab.override.yml" "$DIR/.ml-remoto"
  fetch "$ver"
  "${RSSH[@]}" "$PI" "sudo -n rsync --version" >/dev/null 2>&1 || die "La Pi no dio permiso temporal para rsync."
  rsync -q --rsync-path="sudo -n rsync" -e "$RSYNC_SSH" "$PI:$UL_PI/immich-migracion.env" /tmp/immich-pi.env
  for k in DB_PASSWORD DB_USERNAME DB_DATABASE_NAME; do
    v="$(grep -E "^$k=" /tmp/immich-pi.env | cut -d= -f2- || true)"; [[ -n $v ]] && envset "$k" "$v"
  done
  rm -f /tmp/immich-pi.env
  envset IMMICH_VERSION "$ver"
  DEST="$(envget UPLOAD_LOCATION)"
  if [[ -z $DEST || $DEST == ./library ]]; then
    def="$UL_PI"; ((YES)) && DEST="$def" || { read -r -p "  Carpeta para las fotos en esta máquina [$def]: " DEST; DEST="${DEST:-$def}"; }
    KEEP_DB_PASSWORD=1 first_env "$DEST"
  fi
  DBDIR="$(envget DB_DATA_LOCATION)"
  if [[ -n "$(sudo ls -A "$DBDIR" 2>/dev/null)" ]]; then
    ((FORCE)) || die "La carpeta de la base ($DBDIR) no está vacía. Con --forzar la reemplazo."
    compose down >/dev/null 2>&1 || true; sudo rm -rf "$DBDIR"; sudo mkdir -p "$DBDIR"
  fi

  echo "${B}== Copiando fotos y videos (se puede repetir si se corta: sigue donde se quedó)${N}"
  du="$("${RSSH[@]}" "$PI" "sudo -n du -sh '$UL_PI' 2>/dev/null | cut -f1" || true)"; echo "  Tamaño en la Pi: ${du:-?}"
  sudo mkdir -p "$DEST"; sudo chown "$(id -u):$(id -g)" "$DEST"
  # En la Pi lee como root (las fotos son del contenedor); aquí quedan a tu nombre y Immich las lee igual
  rsync -aH --no-owner --no-group --info=progress2 --rsync-path="sudo -n rsync" -e "$RSYNC_SSH" \
    "$PI:$UL_PI/" "$DEST/"
  ok "Fotos copiadas a $DEST"

  echo "${B}== Restaurando la base de Immich${N}"
  compose pull -q
  compose create >/dev/null
  compose up -d database
  wait_db
  u="$(envget DB_USERNAME)"; u="${u:-postgres}"
  # Procedimiento oficial de Immich para restaurar un pg_dumpall
  sudo gunzip --stdout "$DEST/immich-migracion.sql.gz" \
    | sed "s/SELECT pg_catalog.set_config('search_path', '', false);/SELECT pg_catalog.set_config('search_path', 'public, pg_catalog', true);/g" \
    | "${D[@]}" exec -i immich_postgres psql --quiet --dbname=postgres --username="$u" >/tmp/immich-restore.log 2>&1 \
    || { tail -20 /tmp/immich-restore.log; false; }
  hwaccel
  compose up -d
  sleep 10
  new="$(running_version 127.0.0.1)"
  [[ -n $new ]] && ok "Immich $new funcionando aquí" || warn "Immich tarda en arrancar; revisa: ${D[*]} logs immich_server"
  sudo rm -f "$DEST/immich-migracion.sql.gz" "$DEST/immich-migracion.env"
  trap - EXIT
  open_fw
  NEWURL="http://$(my_ip):2283"
  set_link "$NEWURL"
  "${RSSH[@]}" -t "$PI" "cd $PIREPO && ./scripts/instalar-immich.sh --fin-exportar $NEWURL" || true
  cat <<EOF

${G}${B}✅ Immich se mudó:${N} $NEWURL
  • Cuentas, álbumes, caras y fotos son las mismas. Entra con tu usuario de siempre.
  • En cada celular: app Immich → avatar → Cerrar sesión → servidor ${B}$NEWURL${N} → entrar.
    (Las fotos que ya subió no se vuelven a subir.)
  • Ya en esta máquina puedes actualizar a la versión nueva:  ./scripts/instalar-immich.sh --actualizar
  • En la Pi las fotos siguen guardadas en $UL_PI hasta que las borres.
EOF
  ;;
esac
