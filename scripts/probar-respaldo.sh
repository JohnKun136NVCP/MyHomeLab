#!/usr/bin/env bash
# Prueba de restauración: un respaldo que nunca se probó no es un respaldo.
# Descifra el respaldo más reciente del homelab en una carpeta temporal, revisa que trae
# lo necesario para restaurar (y que las bases SQLite abren) y te manda el resultado al bot.
# No toca nada de lo que está corriendo.
#
#   sudo ./scripts/probar-respaldo.sh               prueba el más reciente ahora
#   sudo ./scripts/probar-respaldo.sh ARCHIVO.enc   prueba ese
#   sudo ./scripts/probar-respaldo.sh --instalar    cada mes (día 1, 05:30)
#   sudo ./scripts/probar-respaldo.sh --quitar
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$PWD"
[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"

if [[ "${1:-}" == --instalar ]]; then
  cat > /etc/systemd/system/homelab-restore-test.service <<UNIT
[Unit]
Description=MyHomeLab: prueba mensual de restauración del respaldo
After=docker.service
[Service]
Type=oneshot
Nice=10
ExecStart=$REPO/scripts/probar-respaldo.sh
UNIT
  cat > /etc/systemd/system/homelab-restore-test.timer <<UNIT
[Unit]
Description=MyHomeLab: prueba de restauración cada mes
[Timer]
OnCalendar=*-*-01 05:30:00
Persistent=true
RandomizedDelaySec=15min
[Install]
WantedBy=timers.target
UNIT
  systemctl daemon-reload
  systemctl enable --now homelab-restore-test.timer
  echo "✅ Programado el día 1 de cada mes a las 05:30."
  exit 0
fi
if [[ "${1:-}" == --quitar ]]; then
  systemctl disable --now homelab-restore-test.timer 2>/dev/null || true
  rm -f /etc/systemd/system/homelab-restore-test.{service,timer}
  systemctl daemon-reload; echo "Listo, ya no se programa."; exit 0
fi

envv() { grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2- | sed 's/^"\(.*\)"$/\1/; s/\$\$/$/g' || true; }
say() {   # manda el resultado al bot (sin sonido si salió bien)
  local text="$1" silent="$2" tok; tok="$(envv INTERNAL_TOKEN)"
  echo "$text"
  [[ -n $tok ]] || return 0
  python3 -c 'import json,sys; print(json.dumps({"text": sys.argv[1], "silent": sys.argv[2] == "1"}))' "$text" "$silent" |
    curl -s -m 10 -X POST http://127.0.0.1:8080/notify -H "Authorization: Bearer $tok" \
      -H 'Content-Type: application/json' --data-binary @- >/dev/null || true
}

FILE="${1:-}"
if [[ -z $FILE ]]; then
  CFG_DIR="$(python3 -c 'import yaml; print(((yaml.safe_load(open("config/homelab.yml")) or {}).get("homelab_backup") or {}).get("dir") or "")' 2>/dev/null || true)"
  BK="$(envv BACKUPS_DIR)"
  DIR="${CFG_DIR:-${BK:-$REPO/backups}/homelab}"
  FILE="$(ls -1t "$DIR"/homelab-*.tar.gz.enc 2>/dev/null | head -1 || true)"
  [[ -n $FILE ]] || { say "❌ Prueba de respaldo: no encontré ningún respaldo en $DIR. ¿Corre el respaldo semanal? (./hl respaldo)" 0; exit 1; }
fi
PASS="$(envv HOMELAB_BACKUP_PASSWORD)"
[[ -n $PASS ]] || { say "❌ Prueba de respaldo: falta HOMELAB_BACKUP_PASSWORD en .env" 0; exit 1; }

NAME="$(basename "$FILE")"
AGE_D=$(( ($(date +%s) - $(stat -c %Y "$FILE")) / 86400 ))
T="$(mktemp -d /tmp/homelab-prueba.XXXXXX)"; trap 'rm -rf "$T"' EXIT
chmod 700 "$T"

if ! openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass fd:3 3<<<"$PASS" -in "$FILE" 2>/dev/null | tar -C "$T" -xzf - 2>"$T.err"; then
  say "❌ Prueba de respaldo FALLÓ: $NAME no se pudo descifrar/abrir ($(head -c 200 "$T.err" | tr "\n" " ")). Revisa la contraseña y el disco." 0
  rm -f "$T.err"; exit 1
fi
rm -f "$T.err"

problems=(); found=()
for f in homelab/.env homelab/config/homelab.yml; do
  [[ -s "$T/$f" ]] && found+=("${f#homelab/}") || problems+=("falta $f")
done
grep -qE '^WEB_PASSWORD_HASH=.+' "$T/homelab/.env" 2>/dev/null || problems+=(".env sin contraseña del panel")
# Las bases SQLite deben abrir y pasar su revisión de integridad
while IFS= read -r db; do
  rel="${db#"$T"/}"
  if python3 - "$db" <<'PY'
import sqlite3, sys
c = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
sys.exit(0 if c.execute("PRAGMA integrity_check").fetchone()[0] == "ok" else 1)
PY
  then found+=("$rel"); else problems+=("base dañada: $rel"); fi
done < <(find "$T" -type f \( -name '*.db' -o -name '*.sqlite' -o -name '*.sqlite3' \) -size +0 2>/dev/null)
for d in volumes/netwatch volumes/bot; do [[ -d "$T/$d" ]] || problems+=("falta $d"); done
((AGE_D > 9)) && problems+=("el respaldo más nuevo tiene $AGE_D días")

size="$(du -sh "$T" | cut -f1)"
nfiles="$(find "$T" -type f | wc -l)"
mkdir -p run
if ((${#problems[@]})); then
  say "⚠ Prueba de respaldo con problemas ($NAME): $(IFS='; '; echo "${problems[*]}"). Se pudo abrir: ${#found[@]} piezas, $nfiles archivos, $size." 0
  printf '{"ts":%s,"ok":false,"file":"%s"}\n' "$(date +%s)" "$NAME" > run/homelab-restore-test.json
  exit 1
fi
say "✅ Prueba de respaldo OK: $NAME (hace $AGE_D días) se descifra y trae todo para restaurar — $nfiles archivos, $size, ${#found[@]} piezas revisadas (bases SQLite íntegras)." 1
printf '{"ts":%s,"ok":true,"file":"%s"}\n' "$(date +%s)" "$NAME" > run/homelab-restore-test.json
