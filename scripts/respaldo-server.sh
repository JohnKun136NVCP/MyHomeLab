#!/usr/bin/env bash
# ======================================================================
#  (En la PC) Respaldo cifrado de la PC del homelab → bot de la Pi.
#  Lo programa install-server.sh (domingo 05:00). A mano:  ./scripts/respaldo-server.sh
#  Incluye: .env de la PC, config/frigate (cámaras), configuración de Jellyfin y la base de Immich.
#  NO incluye grabaciones ni películas (son enormes; están en tus discos).
# ======================================================================
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
envget() { grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2-; }
PASS="$(envget HOMELAB_BACKUP_PASSWORD)"; TOKEN="$(envget BACKUP_TOKEN)"; DEV="$(envget BACKUP_DEVICE)"
PI="$(envget HOMELAB_PI)"; PORT="$(envget HOMELAB_PI_PORT)"
[[ -n "$PASS" && -n "$TOKEN" && -n "$PI" ]] || { echo "Faltan HOMELAB_BACKUP_PASSWORD, BACKUP_TOKEN o HOMELAB_PI en .env"; exit 1; }
STAGE="$(mktemp -d)"; trap 'rm -rf "$STAGE"' EXIT
OUT="$STAGE/pc-$(hostname)-$(date +%Y%m%d-%H%M).tar.gz.enc"
mkdir -p "$STAGE/data"
cp -a .env "$STAGE/data/.env"
tar -C config --exclude='*.db*' --exclude=model_cache -cf - frigate | tar -C "$STAGE/data" -xf -
if docker info >/dev/null 2>&1; then D=(docker); else D=(sudo docker); fi
V="$("${D[@]}" volume ls -q | grep -E '(^|_)jellyfin_config$' | head -1 || true)"
if [[ -n "$V" ]]; then
  mp="$(sudo docker volume inspect -f '{{.Mountpoint}}' "$V")"
  sudo tar -C "$mp" --exclude=./cache --exclude=./metadata --exclude=./log --exclude=./transcodes -cf - . \
    | tar -C "$STAGE/data" --one-top-level=jellyfin -xf -
fi
# Immich: la copia más reciente de su base (las fotos ya están en tu disco grande; son enormes)
UL="$(grep -E '^UPLOAD_LOCATION=' hosts/server/immich/.env 2>/dev/null | cut -d= -f2-)"
if [[ -n "$UL" ]]; then
  last="$(ls -1t "$UL"/backups/*.sql.gz 2>/dev/null | head -1)"
  [[ -n "$last" ]] && mkdir -p "$STAGE/data/immich" && cp "$last" "$STAGE/data/immich/" && cp hosts/server/immich/.env "$STAGE/data/immich/immich.env"
fi
tar -C "$STAGE/data" -czf - . | openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass fd:3 3<<<"$PASS" -out "$OUT"
CA=(-k); [[ -f .homelab-ca.crt ]] && CA=(--cacert .homelab-ca.crt)
code="$(printf 'header = "Authorization: Bearer %s"\n' "$TOKEN" | curl -s "${CA[@]}" -K - -o /dev/null -w '%{http_code}' \
  -T "$OUT" "https://$PI:${PORT:-443}/api/backup/${DEV:-pc-server}/$(basename "$OUT")")"
[[ "$code" == 200 ]] && echo "✅ Respaldo enviado ($(du -h "$OUT" | cut -f1))" || { echo "✘ La Pi respondió $code"; exit 1; }
