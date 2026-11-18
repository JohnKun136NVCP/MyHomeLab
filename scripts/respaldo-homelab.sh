#!/usr/bin/env bash
# ======================================================================
#  Respaldo cifrado del PROPIO homelab (no de tus computadoras):
#    .env, config/, Vaultwarden, cuentas y dispositivos del dashboard, bot,
#    AdGuard, Uptime Kuma, Grafana y el certificado de Caddy.
#
#    ./hl respaldo                              respaldar ahora
#    sudo ./scripts/respaldo-homelab.sh --instalar   cada domingo 04:30 (systemd)
#    sudo ./scripts/respaldo-homelab.sh --quitar     deja de programarlo
#
#  Cifrado AES-256 con HOMELAB_BACKUP_PASSWORD (.env). ¡Guárdala FUERA de la Pi
#  (Vaultwarden + papel)! Sin ella el respaldo no se puede abrir.
#  Destinos (config/homelab.yml → homelab_backup): tu SSD, Telegram y opcional el VPS.
#  Restaurar: ./hl restaurar ARCHIVO.tar.gz.enc
# ======================================================================
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"

if [[ "${1:-}" == "--instalar" ]]; then
  cat > /etc/systemd/system/homelab-backup.service <<UNIT
[Unit]
Description=MyHomeLab: respaldo cifrado del homelab
After=docker.service
[Service]
Type=oneshot
ExecStart=$REPO/scripts/respaldo-homelab.sh
UNIT
  cat > /etc/systemd/system/homelab-backup.timer <<UNIT
[Unit]
Description=MyHomeLab: respaldo semanal del homelab
[Timer]
OnCalendar=Sun *-*-* 04:30:00
Persistent=true
RandomizedDelaySec=10min
[Install]
WantedBy=timers.target
UNIT
  systemctl daemon-reload
  systemctl enable --now homelab-backup.timer
  echo "✅ Programado cada domingo a las 04:30.  Próximo: $(systemctl list-timers homelab-backup.timer --no-legend | awk '{print $1, $2, $3}')"
  exit 0
fi
if [[ "${1:-}" == "--quitar" ]]; then
  systemctl disable --now homelab-backup.timer 2>/dev/null || true
  rm -f /etc/systemd/system/homelab-backup.{service,timer}
  systemctl daemon-reload
  echo "Listo, ya no se programa."; exit 0
fi

envv() { grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2- | sed 's/^"\(.*\)"$/\1/; s/\$\$/$/g'; }
PASS="$(envv HOMELAB_BACKUP_PASSWORD)"
if [[ -z "$PASS" ]]; then
  echo "✘ Falta HOMELAB_BACKUP_PASSWORD en .env: corre ./scripts/gen-secrets.sh"; exit 1
fi
eval "$(python3 - <<'PY'
import shlex, yaml
c = (yaml.safe_load(open("config/homelab.yml")) or {}).get("homelab_backup") or {}
print(f"CFG_DIR={shlex.quote(str(c.get('dir') or ''))}")
print(f"KEEP={int(c.get('keep') or 8)}")
print(f"TG={'1' if c.get('telegram', True) else '0'}")
print(f"VPS={shlex.quote(str(c.get('vps') or ''))}")
PY
)"
BK="$(envv BACKUPS_DIR)"
DEST="${CFG_DIR:-${BK:-$REPO/backups}/homelab}"
mkdir -p "$DEST"; chmod 700 "$DEST"
STAMP="$(date +%Y%m%d-%H%M)"
OUT="$DEST/homelab-$(hostname)-$STAMP.tar.gz.enc"
C=(docker compose -f hosts/pi/docker-compose.yml --env-file .env)
PROJECT="homelab-pi"
STAGE="$(mktemp -d /tmp/homelab-bk.XXXXXX)"
STATUS="$REPO/run/homelab-backup.json"
PAUSED=()
cleanup() {
  ((${#PAUSED[@]})) && "${C[@]}" unpause "${PAUSED[@]}" >/dev/null 2>&1 || true
  rm -rf "$STAGE"
}
trap cleanup EXIT
fail() {
  mkdir -p run
  printf '{"ts":%s,"ok":false,"error":%s}\n' "$(date +%s)" "$(python3 -c 'import json,sys;print(json.dumps(sys.argv[1]))' "$1")" > "$STATUS"
  echo "✘ $1"; exit 1
}

echo "== Respaldo del homelab → $OUT"
mkdir -p "$STAGE/homelab"
cp -a .env "$STAGE/homelab/.env"
cp -a config "$STAGE/homelab/config"
[[ -d run ]] && find run -maxdepth 1 -name 'homelab-ca.crt' -exec cp -a {} "$STAGE/homelab/" \;

# Pausa unos segundos los servicios con bases SQLite para copiarlas sin corromperlas
for s in vaultwarden uptime-kuma netwatch bot grafana; do
  if "${C[@]}" ps --status running --services 2>/dev/null | grep -qx "$s"; then PAUSED+=("$s"); fi
done
((${#PAUSED[@]})) && "${C[@]}" pause "${PAUSED[@]}" >/dev/null

vol() {  # vol nombre-de-volumen carpeta-destino [exclusiones…]
  local v="${PROJECT}_$1" dst="$STAGE/volumes/$2" mp; shift 2
  mp="$(docker volume inspect -f '{{.Mountpoint}}' "$v" 2>/dev/null)" || return 0
  mkdir -p "$dst"
  tar -C "$mp" "${@/#/--exclude=}" -cf - . | tar -C "$dst" -xf -
  echo "   + $v"
}
vol netwatch_data netwatch
vol bot_data bot social frigate.jpg
vol grafana_data grafana
vol uptime_kuma uptime-kuma
vol adguard_conf adguard-conf
vol adguard_work adguard-work data/querylog.json 'data/querylog.json.*' data/stats.db
vol caddy_data caddy
VW="$(envv VW_DATA)"; VW="${VW:-./vaultwarden}"
[[ "$VW" != /* ]] && VW="$REPO/hosts/pi/$VW"
if [[ -d "$VW" ]]; then
  mkdir -p "$STAGE/vaultwarden"
  tar -C "$VW" --exclude=icon_cache -cf - . | tar -C "$STAGE/vaultwarden" -xf -
  echo "   + vaultwarden ($VW)"
fi
((${#PAUSED[@]})) && "${C[@]}" unpause "${PAUSED[@]}" >/dev/null; PAUSED=()

cat > "$STAGE/LEEME.txt" <<EOF
Respaldo de MyHomeLab · $(hostname) · $(date '+%F %T')
Versión del repo: $(sudo -u "$(stat -c %U .)" git log -1 --format='%h %s' 2>/dev/null || echo '?')
VW_DATA original: $VW
Restaurar: git clone del repo y  ./hl restaurar $(basename "$OUT")
EOF

tar -C "$STAGE" -czf - . | openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt \
  -pass fd:3 3<<<"$PASS" -out "$OUT" || fail "No pude cifrar el respaldo"
chmod 600 "$OUT"
SIZE=$(stat -c %s "$OUT")
echo "✅ $(numfmt --to=iec "$SIZE") cifrado"

# Borra los viejos (se quedan los KEEP más recientes)
ls -1t "$DEST"/homelab-*.tar.gz.enc 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f

SENT=""
if ((TG)); then
  tok="$(envv TELEGRAM_BOT_TOKEN)"; chat="$(envv TELEGRAM_ALLOWED_IDS | cut -d, -f1 | tr -d ' ')"
  api="$(envv TELEGRAM_API_URL)"; api="${api:-https://api.telegram.org}"; api="${api/host.docker.internal/127.0.0.1}"
  limit=$((1900 * 1024 * 1024)); [[ "$api" == https://api.telegram.org* ]] && limit=$((49 * 1024 * 1024))
  if [[ -n "$tok" && -n "$chat" ]] && ((SIZE < limit)); then
    cap="🗄 Respaldo del homelab ($(hostname)) · $(numfmt --to=iec "$SIZE") · cifrado con HOMELAB_BACKUP_PASSWORD"
    if printf 'url = "%s/bot%s/sendDocument"\n' "$api" "$tok" | curl -s --max-time 3600 -K - \
        -F "chat_id=$chat" -F "caption=$cap" -F "document=@$OUT" | grep -q '"ok":true'; then
      SENT="telegram"; echo "   → enviado por Telegram"
    else echo "   ⚠ No pude mandarlo por Telegram (queda en $DEST)"; fi
  elif ((SIZE >= limit)); then echo "   ⚠ Pesa más de lo que acepta Telegram; queda en $DEST"; fi
fi
if [[ -n "$VPS" ]]; then
  owner="$(stat -c %U .)"
  if sudo -u "$owner" scp -q -o BatchMode=yes "$OUT" "$VPS/" ; then SENT="${SENT:+$SENT,}vps"; echo "   → copiado al VPS ($VPS)"
  else echo "   ⚠ No pude copiarlo al VPS ($VPS)"; fi
fi

mkdir -p run
python3 - "$STATUS" "$OUT" "$SIZE" "$SENT" <<'PY'
import json, sys, time
json.dump({"ts": time.time(), "ok": True, "file": sys.argv[2], "size": int(sys.argv[3]),
           "sent": [s for s in sys.argv[4].split(",") if s]}, open(sys.argv[1], "w"))
PY
