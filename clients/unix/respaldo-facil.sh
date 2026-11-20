#!/usr/bin/env bash
# ======================================================================
#  Respaldo fácil a MyHomeLab para Linux y Mac (lo instala «Mis respaldos»).
#    respaldo-facil.sh                respalda ahora (Documentos, Escritorio e Imágenes)
#    respaldo-facil.sh --install URL  configura con el enlace del dashboard y programa
#    respaldo-facil.sh --programado    lo usa el temporizador (solo si ya toca)
#    respaldo-facil.sh --remove        deja de respaldar automáticamente
#    respaldo-facil.sh --desinstalar   lo borra todo de esta computadora (tus respaldos en el homelab se quedan)
#  Cifrado con tu contraseña de respaldo (la ves en el dashboard → Mis respaldos).
#  Restaurar:  openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in ARCHIVO | tar xz
#  (si son varias partes: cat ARCHIVO.part-* | openssl enc -d … | tar xz)
# ======================================================================
set -euo pipefail
CONF_DIR="$HOME/.config/myhomelab"; CONF="$CONF_DIR/respaldo.conf"
ME="$HOME/.local/share/myhomelab/respaldo-facil.sh"
STAMP="$CONF_DIR/ultimo-respaldo"
MAC=0; [[ "$(uname)" == Darwin ]] && MAC=1

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
avisar() {  # notificación de escritorio (si hay)
  if ((MAC)); then osascript -e "display notification \"$2\" with title \"$1\"" 2>/dev/null || true
  else command -v notify-send >/dev/null && notify-send -i drive-harddisk "$1" "$2" 2>/dev/null || true; fi
}

instalar() {
  local setup="$1"
  say "Bajando tu configuración del homelab…"
  mkdir -p "$CONF_DIR"; chmod 700 "$CONF_DIR"
  ( umask 077; curl -fsSk "$setup/respaldo.conf" -o "$CONF.tmp" ) \
    || { echo "No pude conectarme (¿el enlace caducó o no estás en casa?)"; exit 1; }
  grep -q '^TOKEN=' "$CONF.tmp" || { echo "Respuesta rara del homelab"; rm -f "$CONF.tmp"; exit 1; }
  mv "$CONF.tmp" "$CONF"; chmod 600 "$CONF"
  if ((MAC)); then
    local plist="$HOME/Library/LaunchAgents/mx.myhomelab.respaldo.plist"
    mkdir -p "$(dirname "$plist")"
    cat > "$plist" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>mx.myhomelab.respaldo</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$ME</string><string>--programado</string></array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>13</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>$CONF_DIR/respaldo.log</string>
  <key>StandardErrorPath</key><string>$CONF_DIR/respaldo.log</string>
</dict></plist>
PL
    launchctl unload "$plist" 2>/dev/null || true; launchctl load "$plist"
  elif command -v systemctl >/dev/null && systemctl --user show-environment >/dev/null 2>&1; then
    local u="$HOME/.config/systemd/user"; mkdir -p "$u"
    printf '[Unit]\nDescription=Respaldo MyHomeLab\n[Service]\nType=oneshot\nExecStart=%s --programado\n' "$ME" > "$u/myhomelab-respaldo.service"
    printf '[Unit]\nDescription=Respaldo MyHomeLab (revisa cada día si ya toca)\n[Timer]\nOnCalendar=*-*-* 13:00\nPersistent=true\n[Install]\nWantedBy=timers.target\n' > "$u/myhomelab-respaldo.timer"
    systemctl --user daemon-reload; systemctl --user enable --now myhomelab-respaldo.timer >/dev/null
  else
    (crontab -l 2>/dev/null | grep -v myhomelab; echo "0 13 * * * $ME --programado # myhomelab") | crontab -
  fi
  if ((!MAC)); then                                    # acceso «Respaldar ahora»
    local app="$HOME/.local/share/applications/myhomelab-respaldo.desktop"; mkdir -p "$(dirname "$app")"
    printf '[Desktop Entry]\nType=Application\nName=Respaldar ahora (MyHomeLab)\nExec=%s\nIcon=drive-harddisk\nTerminal=true\n' "$ME" > "$app"
    local desk; desk="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
    [[ -d "$desk" ]] && cp "$app" "$desk/" && chmod +x "$desk/myhomelab-respaldo.desktop" 2>/dev/null || true
  fi
  # shellcheck disable=SC1090
  . "$CONF"
  say "Listo: se respalda solo cada $SEMANAS semanas (y si la computadora estaba apagada, en cuanto la prendas)."
  say "Haciendo tu primer respaldo…"
  respaldar
}

carpeta() {  # DOCUMENTS / DESKTOP / PICTURES → ruta real (en español o inglés)
  if ((MAC)); then
    case "$1" in DOCUMENTS) echo "$HOME/Documents";; DESKTOP) echo "$HOME/Desktop";; PICTURES) echo "$HOME/Pictures";; *) echo "$1";; esac
  else
    xdg-user-dir "$1" 2>/dev/null || case "$1" in
      DOCUMENTS) echo "$HOME/Documentos";; DESKTOP) echo "$HOME/Escritorio";; PICTURES) echo "$HOME/Imágenes";; *) echo "$1";; esac
  fi
}

respaldar() {
  [[ -f "$CONF" ]] || { echo "Primero instálalo con el enlace de «Mis respaldos»."; exit 1; }
  # shellcheck disable=SC1090
  . "$CONF"
  local name dirs=() d
  for c in $CARPETAS; do d="$(carpeta "$c")"; [[ -d "$d" && "$d" != "$HOME" ]] && dirs+=("${d#"$HOME"/}"); done
  ((${#dirs[@]})) || { echo "No encontré carpetas para respaldar"; exit 1; }
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  name="respaldo_${DEVICE}_$(date +%Y-%m-%d_%H%M).tar.gz.enc"
  say "Comprimiendo y cifrando: ${dirs[*]}"
  tar -C "$HOME" --exclude='.cache' --exclude='node_modules' --exclude='*.tmp' --exclude='.Trash*' \
      -czf - "${dirs[@]}" 2>/dev/null \
    | openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass fd:3 3<<<"$CLAVE" \
    | split -b 1900m - "$tmp/$name.part-"
  local parts=("$tmp"/*.part-*)
  if ((${#parts[@]} == 1)); then mv "${parts[0]}" "$tmp/$name"; parts=("$tmp/$name"); fi
  local session; session="$(date +%Y%m%d%H%M%S)"
  for p in "${parts[@]}"; do
    say "Subiendo $(basename "$p") ($(du -h "$p" | cut -f1))…"
    local code
    code="$(printf 'header = "Authorization: Bearer %s"\nheader = "X-Backup-Session: %s"\n' "$TOKEN" "$session" \
      | curl -sS -k -K - -T "$p" -o /dev/null -w '%{http_code}' "$URL/api/backup/$DEVICE/$(basename "$p")")" || code=000
    if [[ "$code" != 200 ]]; then
      avisar "Respaldo MyHomeLab" "No se pudo subir (código $code). ¿Estás en la red de la casa?"
      echo "✘ El homelab respondió $code"; exit 1
    fi
  done
  date +%s > "$STAMP"
  avisar "Respaldo MyHomeLab" "✅ Tu respaldo llegó al homelab"
  say "✅ Respaldo enviado. El aviso te llega a «Mis respaldos» (y a Telegram si lo vinculaste)."
}

case "${1:-}" in
  --install|--instalar) instalar "${2:?Falta el enlace de Mis respaldos}" ;;
  --programado)
    # shellcheck disable=SC1090
    . "$CONF"; last="$(cat "$STAMP" 2>/dev/null || echo 0)"
    (( $(date +%s) - last >= SEMANAS * 7 * 86400 - 43200 )) && respaldar || true ;;
  --remove|--quitar)
    if ((MAC)); then launchctl unload "$HOME/Library/LaunchAgents/mx.myhomelab.respaldo.plist" 2>/dev/null; rm -f "$HOME/Library/LaunchAgents/mx.myhomelab.respaldo.plist"
    else systemctl --user disable --now myhomelab-respaldo.timer 2>/dev/null || true; (crontab -l 2>/dev/null | grep -v myhomelab) | crontab - 2>/dev/null || true; fi
    echo "Ya no se respalda automáticamente." ;;
  --desinstalar|--uninstall)
    "$0" --remove >/dev/null 2>&1 || true
    if ((MAC)); then :; else
      rm -f "$HOME/.config/systemd/user/myhomelab-respaldo.service" "$HOME/.config/systemd/user/myhomelab-respaldo.timer"
      systemctl --user daemon-reload 2>/dev/null || true
    fi
    desk="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
    rm -f "$HOME/.local/share/applications/myhomelab-respaldo.desktop" "$desk/myhomelab-respaldo.desktop"
    rm -rf "$CONF_DIR"                   # configuración y enlace con el homelab
    rm -f "$ME"; rmdir "$(dirname "$ME")" 2>/dev/null || true
    echo "✅ Respaldo fácil desinstalado. Lo que ya subiste sigue en el panel → Mis respaldos." ;;
  *) respaldar ;;
esac
