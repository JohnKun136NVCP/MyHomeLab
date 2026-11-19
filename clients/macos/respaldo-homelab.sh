#!/usr/bin/env bash
# ======================================================================
#  Respaldo de macOS hacia MyHomeLab
#    ./respaldo-homelab.sh --configurar   primera vez (contraseñas al Llavero)
#    ./respaldo-homelab.sh                hace el respaldo y lo sube
#    ./respaldo-homelab.sh --programar    revisión mensual con launchd (respalda si pasaron 90 días)
#    ./respaldo-homelab.sh --solo-local   no sube nada
#    ./respaldo-homelab.sh --install     comando «respaldo-homelab» + icono en el Escritorio
#  Requiere: brew install sevenzip   (comando 7zz)
# ======================================================================
set -euo pipefail
CONF="$HOME/.config/myhomelab/backup.conf"
STATE="$HOME/.config/myhomelab/last-backup"
say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }

keychain_get() { security find-generic-password -a "$USER" -s "$1" -w 2>/dev/null; }
keychain_set() { security add-generic-password -U -a "$USER" -s "$1" -w "$2"; }

if [[ "${1:-}" == "--install" || "${1:-}" == "--instalar" ]]; then
  mkdir -p "$HOME/.local/bin"
  install -m 755 "$0" "$HOME/.local/bin/respaldo-homelab"
  grep -q '.local/bin' "$HOME/.zshrc" 2>/dev/null || \
    printf '\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$HOME/.zshrc"
  CMDF="$HOME/Desktop/Respaldo MyHomeLab.command"     # doble clic = abre Terminal y respalda
  printf '#!/bin/bash\n"$HOME/.local/bin/respaldo-homelab"\necho\nread -r -p "Enter para cerrar…"\n' > "$CMDF"
  chmod +x "$CMDF"
  say "Instalado: comando «respaldo-homelab» (terminal nueva) e icono «Respaldo MyHomeLab» en el Escritorio."
  exit 0
fi

if [[ "${1:-}" == "--configurar" || ! -f "$CONF" ]]; then
  mkdir -p "$(dirname "$CONF")"
  read -r -p "URL del homelab (ej. https://192.168.0.5): " URL
  read -r -p "Dispositivo en homelab.yml (ej. macbook): " DEVICE
  read -r -s -p "Token del dispositivo: " TOK; echo
  read -r -s -p "Contraseña para cifrar el .7z: " PW; echo
  keychain_set myhomelab-token "$TOK"; keychain_set myhomelab-7z "$PW"
  cat > "$CONF" <<EOF
URL="$URL"
DEVICE="$DEVICE"
CA=""                       # ruta al root.crt de Caddy (vacío = no verificar)
DEST="\$HOME/Respaldos"
# Qué respaldar (rutas relativas a tu home)
ITEMS=(.ssh .gitconfig .zshrc .zprofile .p10k.zsh .oh-my-zsh/custom .config/kitty
       "Library/Application Support/Code/User/settings.json"
       "Library/Application Support/Code/User/keybindings.json"
       "Library/Application Support/Code/User/snippets"
       Library/Fonts Documents Desktop)
EOF
  chmod 600 "$CONF"
  say "Listo. Edita $CONF para elegir qué respaldar."
  [[ "${1:-}" == "--configurar" ]] && exit 0
fi
# shellcheck source=/dev/null
source "$CONF"

if [[ "${1:-}" == "--programar" ]]; then
  PL="$HOME/Library/LaunchAgents/com.myhomelab.respaldo.plist"
  cat > "$PL" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.myhomelab.respaldo</string>
  <key>ProgramArguments</key><array><string>$(cd "$(dirname "$0")" && pwd)/$(basename "$0")</string><string>--si-toca</string></array>
  <key>StartCalendarInterval</key><dict><key>Day</key><integer>1</integer><key>Hour</key><integer>11</integer></dict>
  <key>StandardOutPath</key><string>/tmp/myhomelab-respaldo.log</string>
  <key>StandardErrorPath</key><string>/tmp/myhomelab-respaldo.log</string>
</dict></plist>
EOF
  launchctl unload "$PL" 2>/dev/null || true; launchctl load "$PL"
  say "Programado: cada día 1 revisa y respalda si pasaron 90 días."; exit 0
fi
if [[ "${1:-}" == "--si-toca" && -f "$STATE" ]]; then
  (( $(date +%s) - $(cat "$STATE") < 90 * 86400 )) && exit 0
fi

SEVEN="$(command -v 7zz || command -v 7z || true)"
[[ -n "$SEVEN" ]] || { echo "Instala 7-Zip: brew install sevenzip"; exit 1; }
STAMP="$(date +%Y-%m-%d_%H%M)"; NAME="respaldo_${DEVICE}_${STAMP}"
WORK="$(mktemp -d)/$NAME"; mkdir -p "$WORK/home" "$DEST"
trap 'rm -rf "$(dirname "$WORK")"' EXIT

say "Lista de apps (Homebrew, App Store, /Applications)…"
command -v brew >/dev/null && brew bundle dump --file="$WORK/Brewfile" --force --describe 2>/dev/null || true
command -v mas >/dev/null && mas list > "$WORK/appstore.txt" 2>/dev/null || true
ls /Applications > "$WORK/aplicaciones.txt"
defaults read com.apple.dock persistent-apps > "$WORK/dock.txt" 2>/dev/null || true
cat > "$WORK/LEEME.txt" <<EOF
Restaurar en otra Mac:
  7zz x $NAME.7z.001
  brew bundle --file=Brewfile          # reinstala apps de Homebrew y de la App Store (si usas mas)
  rsync -a home/ ~/                    # tus configuraciones
EOF

say "Copiando archivos…"
for item in "${ITEMS[@]}"; do
  [[ -e "$HOME/$item" ]] || continue
  mkdir -p "$WORK/home/$(dirname "$item")"
  rsync -a --exclude node_modules --exclude .cache --exclude Caches "$HOME/$item" "$WORK/home/$(dirname "$item")/"
  echo "   ~/$item"
done

say "Comprimiendo (7z -mx=9, AES-256, partes de 1.9 GB)…"
PW="$(keychain_get myhomelab-7z)"
"$SEVEN" a -t7z -mx=9 -mhe=on "-p$PW" -v1900m "$DEST/$NAME.7z" "$WORK" -bsp1
unset PW
date +%s > "$STATE"
[[ "${1:-}" == "--solo-local" ]] && { say "Guardado en $DEST"; exit 0; }

TOK="$(keychain_get myhomelab-token)"; SESSION="$(date +%Y%m%d%H%M%S)"
for part in "$DEST/$NAME".7z.*; do
  say "Subiendo $(basename "$part")…"
  code=$( { echo "header = \"Authorization: Bearer $TOK\""; echo "header = \"X-Backup-Session: $SESSION\"";
            if [[ -n "$CA" ]]; then echo "cacert = \"$CA\""; else echo "insecure"; fi; } |
          curl -sS -K - -T "$part" -o /dev/null -w '%{http_code}' \
            "${URL%/}/api/backup/$DEVICE/$(basename "$part")")
  [[ "$code" == "200" ]] || { echo "El homelab respondió $code"; exit 1; }
done
say "Respaldo enviado a MyHomeLab ✅"
