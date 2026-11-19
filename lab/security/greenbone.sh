#!/usr/bin/env bash
# ======================================================================
#  Greenbone / OpenVAS: escáner de vulnerabilidades para TUS equipos (en la PC x86).
#    ./lab/security/greenbone.sh install     baja la versión oficial y la levanta
#    ./lab/security/greenbone.sh password    pone una contraseña nueva al usuario admin
#    ./lab/security/greenbone.sh lan         deja ver la web desde la red de la casa (:9392)
#    ./lab/security/greenbone.sh status | stop | start | remove
#  Web: http://127.0.0.1:9392 (o con «lan», http://IP-de-la-PC:9392). Guía: docs/laboratorio-seguridad.md
#  Escanea SOLO tus equipos: escanear redes ajenas sin permiso es ilegal.
# ======================================================================
set -euo pipefail
. "$(dirname "$(readlink -f "$0")")/../../scripts/lib/flags.sh"; hl_flags "$@"; set -- "${HL_ARGS[@]}"
SELF="$(readlink -f "$0")"; HERE="$(dirname "$SELF")"; REPO="$(cd "$HERE/../.." && pwd)"
DIR="$HERE/greenbone"; URL=https://greenbone.github.io/docs/latest/_static/docker-compose.yml
G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; B=$'\e[1m'; N=$'\e[0m'
ok() { echo "  ${G}✔${N} $*"; }; warn() { echo "  ${Y}⚠${N} $*"; }; die() { echo "${R}✘${N} $*"; exit 1; }
if docker info >/dev/null 2>&1; then D=(docker); else D=(sudo docker); fi
compose() {
  local f=(-f "$DIR/docker-compose.yml"); [[ -f "$DIR/lan.override.yml" ]] && f+=(-f "$DIR/lan.override.yml")
  "${D[@]}" compose -p greenbone-community-edition "${f[@]}" "$@"
}
my_ip() { ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}' || true; }

case "${1:-help}" in
  install)
    [[ "$(uname -m)" == x86_64 ]] || die "Greenbone va en una PC x86_64 (las imágenes oficiales no son para la Raspberry)."
    mem=$(awk '/MemTotal/{print int($2/1048576+0.5)}' /proc/meminfo); ((mem >= 8)) || warn "Greenbone pide ~8 GB de RAM; aquí hay $mem GB."
    mkdir -p "$DIR"
    curl -fsSL "$URL" -o "$DIR/docker-compose.yml.new" && grep -q gvmd "$DIR/docker-compose.yml.new" || die "No pude bajar el compose oficial de Greenbone"
    mv "$DIR/docker-compose.yml.new" "$DIR/docker-compose.yml"
    compose pull -q
    compose up -d
    ok "Greenbone arrancando. La primera vez descarga sus bases de vulnerabilidades: tarda de 30 min a varias horas."
    pw="$(openssl rand -base64 12 | tr -d '/+=' | cut -c1-14)"
    echo "  Esperando a que arranque para poner tu contraseña…"
    for _ in $(seq 1 60); do compose exec -T -u gvmd gvmd gvmd --get-users >/dev/null 2>&1 && break; sleep 10; done
    if compose exec -T -u gvmd gvmd gvmd --user=admin --new-password="$pw" >/dev/null 2>&1; then
      umask 077; echo "admin $pw" > "$DIR/admin.txt"; ok "Usuario admin, contraseña guardada en lab/security/greenbone/admin.txt"
    else warn "Aún no pude poner la contraseña; luego corre: ./lab/security/greenbone.sh password"; fi
    python3 "$REPO/scripts/lib/links.py" add "Vulnerabilidades (Greenbone)" "http://$(my_ip):9392" "🧪" "Escáner de vulnerabilidades de tus equipos" "" || true
    echo "  Web: http://127.0.0.1:9392   (para verla desde otra máquina:  ./lab/security/greenbone.sh lan)" ;;
  password)
    pw="${2:-$(openssl rand -base64 12 | tr -d '/+=' | cut -c1-14)}"
    compose exec -T -u gvmd gvmd gvmd --user=admin --new-password="$pw" && { umask 077; echo "admin $pw" > "$DIR/admin.txt"; ok "Contraseña nueva en lab/security/greenbone/admin.txt"; } ;;
  lan)
    cat > "$DIR/lan.override.yml" <<'YML'
services:
  gsa:
    ports: !override ["9392:80"]
YML
    compose up -d gsa
    if command -v ufw >/dev/null && sudo ufw status | grep -q "Status: active"; then
      for n in $(ip -4 route | awk '/proto kernel/ && $1 !~ /^172\.(1[6-9]|2[0-9]|3[01])\./ {print $1}'); do
        sudo ufw allow from "$n" to any port 9392 proto tcp comment "homelab greenbone" >/dev/null
      done
    fi
    ok "Web en http://$(my_ip):9392 para la red de la casa" ;;
  status|estado) compose ps --format 'table {{.Service}}\t{{.Status}}' ;;
  stop|apagar) compose stop; ok "Greenbone apagado (no pierde nada)" ;;
  start) compose up -d; ok "Greenbone encendido" ;;
  remove|quitar)
    read -r -p "Borra Greenbone, sus bases y reportes. Escribe QUITAR: " a; [[ $a == QUITAR ]] || die "Cancelado"
    compose down -v --remove-orphans; rm -rf "$DIR"
    python3 "$REPO/scripts/lib/links.py" remove "Vulnerabilidades (Greenbone)" || true
    ok "Greenbone quitado" ;;
  *) sed -n 2,10p "$SELF" | sed -E 's/^# ?//' ;;
esac
