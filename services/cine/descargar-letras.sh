#!/usr/bin/env bash
# Guarda las letras del portal en la PC para que se vean igual sin internet.
# Lo corre install-server.sh; a mano:  ./services/cine/descargar-letras.sh
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p fonts
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
css="$(curl -fsSL -A "$UA" 'https://fonts.googleapis.com/css2?family=Big+Shoulders+Display:wght@600;800;900&family=Figtree:wght@400;500;600;700&display=swap')"
n=0
out="$css"
for u in $(grep -oE 'https://fonts.gstatic.com/[^)]+\.woff2' <<<"$css" | sort -u); do
  f="fonts/$(basename "$u")"
  [[ -f "$f" ]] || curl -fsSL "$u" -o "$f"
  out="${out//$u/$(basename "$u")}"
  n=$((n + 1))
done
printf '%s\n' "$out" > fonts/fonts.css
echo "✅ $n archivos de letras en services/cine/fonts"
