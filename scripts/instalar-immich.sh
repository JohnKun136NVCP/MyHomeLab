#!/usr/bin/env bash
# ======================================================================
#  Immich en la PC: las fotos y videos de los celulares se respaldan SOLOS.
#    ./scripts/instalar-immich.sh            pregunta dónde guardar las fotos
#    ./scripts/instalar-immich.sh --si       con los valores sugeridos
#    ./scripts/instalar-immich.sh --actualizar   baja la versión nueva de Immich y reinicia
#
#  Usa el docker-compose.yml y .env OFICIALES de la última versión de Immich (así lo pide el
#  proyecto: la base de datos va fijada por versión). Quedan en hosts/server/immich/.
#  Fotos: http://IP-de-la-PC:2283   App: «Immich» en Play Store / App Store.
# ======================================================================
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
YES=0; UPDATE=0
for a in "$@"; do case "$a" in --si|-y) YES=1 ;; --actualizar) UPDATE=1 ;; *) echo "Opción desconocida: $a"; exit 2 ;; esac; done
DIR=hosts/server/immich
REL=https://github.com/immich-app/immich/releases/latest/download
G=$'\e[32m'; B=$'\e[1m'; N=$'\e[0m'
if docker info >/dev/null 2>&1; then D=(docker); else D=(sudo docker); fi
mkdir -p "$DIR"
envget() { grep -E "^$1=" "$DIR/.env" 2>/dev/null | tail -1 | cut -d= -f2-; }
envset() {
  local k="$1" v="$2"
  if grep -qE "^$k=" "$DIR/.env"; then sed -i "s|^$k=.*|$k=$v|" "$DIR/.env"; else echo "$k=$v" >> "$DIR/.env"; fi
}

echo "${B}== Bajando Immich (versión oficial más reciente)${N}"
curl -fsSL "$REL/docker-compose.yml" -o "$DIR/docker-compose.yml.new"
grep -q "immich-server" "$DIR/docker-compose.yml.new" || { echo "✘ No pude bajar el compose de Immich"; exit 1; }
mv "$DIR/docker-compose.yml.new" "$DIR/docker-compose.yml"
if [[ ! -f "$DIR/.env" ]]; then
  curl -fsSL "$REL/example.env" -o "$DIR/.env"
  chmod 600 "$DIR/.env"
  def="/srv/fotos"
  if ((YES)); then FOTOS="$def"; else read -r -p "  Carpeta para las fotos y videos (un disco grande) [$def]: " FOTOS; FOTOS="${FOTOS:-$def}"; fi
  sudo mkdir -p "$FOTOS"; sudo chown "$(id -u):$(id -g)" "$FOTOS"
  envset UPLOAD_LOCATION "$FOTOS"
  envset DB_DATA_LOCATION "$REPO/$DIR/postgres"          # en el disco local de la PC (no en red)
  envset DB_PASSWORD "$(openssl rand -hex 16)"           # solo letras y números, como pide Immich
  envset TZ "$(timedatectl show -p Timezone --value 2>/dev/null || echo America/Mexico_City)"
  sed -i 's/^# *TZ=/TZ=/' "$DIR/.env"
  echo "  ${G}✔${N} Fotos en $FOTOS"
fi

# Aceleración del reconocimiento de caras/búsqueda: Intel → OpenVINO
if lspci 2>/dev/null | grep -Ei 'vga|display' | grep -qi intel && [[ -e /dev/dri/renderD128 ]]; then
  curl -fsSL "$REL/hwaccel.ml.yml" -o "$DIR/hwaccel.ml.yml" && \
  python3 - "$DIR/docker-compose.yml" <<'PY' && echo "  ${G}✔${N} Reconocimiento con la GPU Intel (OpenVINO)"
import re, sys, yaml
p = sys.argv[1]; orig = open(p).read(); out = []; inml = False; done = 0
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
assert done == 4 and ml["extends"]["service"] == "openvino", "no pude activar OpenVINO; sigue en CPU"
open(p, "w").write(new)
PY
fi

echo "${B}== Levantando Immich${N}"
"${D[@]}" compose -f "$DIR/docker-compose.yml" --env-file "$DIR/.env" pull -q
"${D[@]}" compose -f "$DIR/docker-compose.yml" --env-file "$DIR/.env" up -d
((UPDATE)) && { echo "  ${G}✔${N} Immich actualizado"; exit 0; }

command -v ufw >/dev/null && sudo ufw status | grep -q "Status: active" && {
  LAN="$(ip -4 route | awk '/proto kernel/ && $1 ~ /\// {print $1; exit}')"
  sudo ufw allow from "${LAN:-192.168.1.0/24}" to any port 2283 proto tcp comment "homelab immich" >/dev/null
}
IP="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}')"
# Enlace en el dashboard (si la «Pi» es esta misma máquina, o por SSH desde install-server)
if [[ -f config/homelab.yml ]] && grep -q '^links:' config/homelab.yml && ! grep -q 'Fotos (Immich)' config/homelab.yml; then
  python3 - "$IP" <<'PY'
import sys, yaml
p = "config/homelab.yml"; c = yaml.safe_load(open(p)) or {}
c.setdefault("links", []).append({"name": "Fotos (Immich)", "url": f"http://{sys.argv[1]}:2283", "icon": "📸"})
with open(p, "w") as f:
    yaml.safe_dump(c, f, sort_keys=False, allow_unicode=True)
PY
fi
cat <<EOF

${G}${B}✅ Immich listo:${N} http://$IP:2283
  1. Ábrelo y crea tu cuenta: la primera es la de administrador.
  2. Administración → Usuarios → crea una cuenta para cada quien (mamá, papá…).
  3. Administración → Configuración → Copia de seguridad: deja activa la copia diaria de la base.
  4. En cada celular: app «Immich» → servidor http://$IP:2283 → entra → ☁ Respaldo → elige
     «Cámara» (y WhatsApp si quieres) → activa respaldo en segundo plano.
  Guía para la familia: docs/fotos.md
EOF
