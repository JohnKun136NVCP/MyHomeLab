#!/usr/bin/env bash
# ======================================================================
#  Quita el honeypot de la Raspberry: contenedor, imagen, volumen,
#  reglas de UFW y variables de .env. No toca nada más.
#    ./scripts/quitar-honeypot.sh
# ======================================================================
set -uo pipefail
cd "$(dirname "$0")/.."
say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }

say "Contenedor e imagen"
for c in $(sudo docker ps -aq --filter "name=homelab-pi-honeypot"); do
  sudo docker rm -f "$c" >/dev/null && echo "   contenedor $c eliminado"
done
for i in $(sudo docker images -q "homelab-pi-honeypot" 2>/dev/null | sort -u); do
  sudo docker rmi -f "$i" >/dev/null && echo "   imagen $i eliminada"
done
sudo docker volume rm homelab-pi_honeypot_logs >/dev/null 2>&1 && echo "   volumen homelab-pi_honeypot_logs eliminado"

say "Reglas de UFW con el comentario «honeypot»"
if command -v ufw >/dev/null; then
  # Se borran de la más alta a la más baja (al borrar una, las demás se recorren)
  nums=$(sudo ufw status numbered | grep -i "# honeypot" | sed -E 's/^\[ *([0-9]+)\].*/\1/' | sort -rn)
  if [[ -z "$nums" ]]; then
    echo "   no hay reglas del honeypot"
  else
    for n in $nums; do sudo ufw --force delete "$n" >/dev/null && echo "   regla [$n] eliminada"; done
  fi
fi

say "Variables de .env"
if [[ -f .env ]] && grep -qE '^(HONEYPOT_IP|HONEYPOT_NODE)=' .env; then
  cp .env .env.antes-de-quitar-honeypot
  sed -i -E '/^(# Honeypot: IP de la Pi en el laboratorio.*|HONEYPOT_IP=.*|HONEYPOT_NODE=.*)$/d' .env
  echo "   quitadas (copia en .env.antes-de-quitar-honeypot)"
else
  echo "   no había"
fi
if [[ -f .env ]] && grep -q 'honeypot' .env; then
  sed -i -E 's/(COMPOSE_PROFILES=.*)honeypot,?/\1/; s/,$//' .env
fi

echo
say "Listo. El honeypot ya no existe en la Pi."
sudo ufw status numbered 2>/dev/null | head -20
