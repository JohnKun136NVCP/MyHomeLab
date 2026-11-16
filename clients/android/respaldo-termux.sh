#!/data/data/com.termux/files/usr/bin/bash
# ======================================================================
#  Respaldo del teléfono Android hacia MyHomeLab (desde Termux)
#  Instala Termux y Termux:API desde F-Droid, luego:
#    pkg install p7zip curl termux-api
#    termux-setup-storage
#    bash respaldo-termux.sh --configurar
#    bash respaldo-termux.sh              # respaldo ahora
#    bash respaldo-termux.sh --programar  # cada semana revisa y respalda si pasaron 90 días
#  También puedes mandarle archivos al bot directamente desde Telegram.
# ======================================================================
set -euo pipefail
CONF="$HOME/.config/myhomelab/backup.conf"
STATE="$HOME/.config/myhomelab/last-backup"
say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
notify() { command -v termux-notification >/dev/null && termux-notification -t "MyHomeLab" -c "$1" || true; }

if [[ "${1:-}" == "--configurar" || ! -f "$CONF" ]]; then
  mkdir -p "$(dirname "$CONF")"
  read -r -p "URL del homelab (ej. https://192.168.0.5): " URL
  read -r -p "Dispositivo (ej. telefono): " DEVICE
  read -r -s -p "Token del dispositivo: " TOK; echo
  read -r -s -p "Contraseña del .7z: " PW; echo
  cat > "$CONF" <<EOF
URL="$URL"
DEVICE="$DEVICE"
TOKEN="$TOK"
PASSWORD="$PW"
CA=""
# Carpetas del almacenamiento compartido (~/storage/shared = memoria interna)
ITEMS=(DCIM/Camera Pictures Documents Download Recordings
       "Android/media/com.whatsapp/WhatsApp/Media" "Android/media/com.whatsapp/WhatsApp/Backups")
ONLY_WIFI=1
EOF
  chmod 600 "$CONF"
  say "Guardado en $CONF"; [[ "${1:-}" == "--configurar" ]] && exit 0
fi
# shellcheck source=/dev/null
source "$CONF"

if [[ "${1:-}" == "--programar" ]]; then
  JOB="$HOME/.config/myhomelab/job.sh"   # termux-job-scheduler no pasa argumentos
  printf '#!/data/data/com.termux/files/usr/bin/bash\nexec bash "%s" --si-toca\n' "$(realpath "$0")" > "$JOB"
  chmod 700 "$JOB"
  termux-job-scheduler --job-id 4242 --period-ms $((7 * 86400 * 1000)) --persisted true \
    --network unmetered --charging true --script "$JOB"
  say "Programado: semanal, con Wi-Fi y cargando; respalda si pasaron 90 días."; exit 0
fi
if [[ "${1:-}" == "--si-toca" && -f "$STATE" ]]; then
  (( $(date +%s) - $(cat "$STATE") < 90 * 86400 )) && exit 0
fi
if [[ "${ONLY_WIFI:-1}" == "1" ]] && command -v termux-wifi-connectioninfo >/dev/null; then
  termux-wifi-connectioninfo | grep -q '"supplicant_state": "COMPLETED"' || { echo "Sin Wi-Fi; no respaldo."; exit 0; }
fi

SHARED="$HOME/storage/shared"
[[ -d "$SHARED" ]] || { echo "Corre primero: termux-setup-storage"; exit 1; }
STAMP="$(date +%Y-%m-%d_%H%M)"; NAME="respaldo_${DEVICE}_${STAMP}"
OUT="$SHARED/Respaldos"; mkdir -p "$OUT"
LIST="$(mktemp)"
for item in "${ITEMS[@]}"; do [[ -e "$SHARED/$item" ]] && echo "$item" >> "$LIST"; done
# Lista de apps instaladas (si Android lo permite desde Termux)
(cmd package list packages -3 2>/dev/null || pm list packages -3 2>/dev/null || true) \
  | sed 's/^package://' | sort > "$SHARED/.apps-instaladas.txt"
echo ".apps-instaladas.txt" >> "$LIST"

say "Comprimiendo… (fotos y videos ya vienen comprimidos: -mx=3 para no calentar el teléfono)"
notify "Respaldando el teléfono…"
cd "$SHARED"
7z a -t7z -mx=3 -mhe=on "-p$PASSWORD" -v1900m "$OUT/$NAME.7z" "@$LIST" -bsp1
rm -f "$LIST" "$SHARED/.apps-instaladas.txt"
date +%s > "$STATE"
[[ "${1:-}" == "--solo-local" ]] && exit 0

SESSION="$(date +%Y%m%d%H%M%S)"
for part in "$OUT/$NAME".7z.*; do
  say "Subiendo $(basename "$part")…"
  code=$( { echo "header = \"Authorization: Bearer $TOKEN\""; echo "header = \"X-Backup-Session: $SESSION\"";
            if [[ -n "$CA" ]]; then echo "cacert = \"$CA\""; else echo "insecure"; fi; } |
          curl -sS -K - -T "$part" -o /dev/null -w '%{http_code}' "${URL%/}/api/backup/$DEVICE/$(basename "$part")")
  [[ "$code" == "200" ]] || { notify "Falló la subida ($code)"; exit 1; }
  rm -f "$part"   # ya está en la Pi: libera espacio del teléfono
done
notify "Respaldo enviado ✅"; say "Listo ✅"
