#!/usr/bin/env bash
# Instala Linux Guardian en ~/.local/share/linux-guardian con su propio entorno virtual.
#   ./install.sh              instala (Pillow + Telethon dentro del venv)
#   ./install.sh --sin-telethon   no instala Telethon (solo usarás el bot)
#   ./install.sh --desinstalar    borra el programa, el venv y el acceso del menú
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$HOME/.local/share/linux-guardian"
VENV="$DEST/venv"
BIN="$HOME/.local/bin/linux-guardian"
DESKTOP="$HOME/.local/share/applications/linux-guardian.desktop"

if [[ "${1:-}" == "--desinstalar" ]]; then
  "$VENV/bin/python" "$DEST/guardian.py" --remove-reminder 2>/dev/null || true
  rm -rf "$DEST" "$BIN" "$DESKTOP"
  echo "✔ Desinstalado. Tu configuración sigue en ~/.config/linux-guardian (bórrala si quieres)."
  exit 0
fi

echo "==> Paquetes del sistema (no se pueden meter en un venv)"
echo "    python3-venv, python3-tk (Tkinter), 7-Zip, curl, notify-send, rsync, poppler-utils"
sudo apt-get update -qq
sudo apt-get install -y python3-venv python3-tk curl libnotify-bin rsync file poppler-utils
sudo apt-get install -y 7zip 2>/dev/null || sudo apt-get install -y p7zip-full

echo "==> Copiando programa a $DEST"
mkdir -p "$DEST"
cp "$SRC"/guardian.py "$SRC"/core.py "$SRC"/restore.sh "$SRC"/requirements.txt "$DEST"/
chmod +x "$DEST"/restore.sh

echo "==> Creando entorno virtual en $VENV"
[[ -x "$VENV/bin/python" ]] || python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --upgrade pip -q
if [[ "${1:-}" == "--sin-telethon" ]]; then
  grep -iv telethon "$DEST/requirements.txt" > "$DEST/req.tmp"
  "$VENV/bin/python" -m pip install -q -r "$DEST/req.tmp"; rm -f "$DEST/req.tmp"
else
  "$VENV/bin/python" -m pip install -q -r "$DEST/requirements.txt"
fi
"$VENV/bin/python" -c "import tkinter" 2>/dev/null \
  || { echo "✘ El venv no ve Tkinter: revisa que python3-tk esté instalado."; exit 1; }

echo "==> Creando lanzadores"
mkdir -p "$HOME/.local/bin" "$HOME/.local/share/applications"
cat > "$BIN" <<EOF
#!/usr/bin/env bash
exec "$VENV/bin/python" "$DEST/guardian.py" "\$@"
EOF
chmod +x "$BIN"
cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=Linux Guardian
Comment=Respaldo y máquina del tiempo para tu Linux
Exec=$VENV/bin/python $DEST/guardian.py
Icon=drive-harddisk
Terminal=false
Categories=System;Utility;
EOF

# Si ya tenías el recordatorio activo, se reescribe para que use el venv
if [[ -f "$HOME/.config/systemd/user/linux-guardian-reminder.timer" ]]; then
  "$VENV/bin/python" "$DEST/guardian.py" --install-reminder >/dev/null && echo "    recordatorio actualizado al venv"
fi

echo
echo "✔ Listo. Ábrelo desde el menú (Linux Guardian) o con:  linux-guardian"
echo "  Todo lo de Python vive en: $VENV"
