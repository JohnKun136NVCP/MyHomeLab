#!/usr/bin/env bash
# Crea .env a partir de .env.example con contraseñas y tokens aleatorios.
# No toca valores que ya tengas puestos. Uso:  ./scripts/gen-secrets.sh
set -euo pipefail
cd "$(dirname "$0")/.."
[[ -f .env ]] || cp .env.example .env
[[ -f config/homelab.yml ]] || cp config/homelab.example.yml config/homelab.yml
chmod 600 .env

rand() { openssl rand -base64 33 | tr -d '/+=\n' | cut -c1-40; }
setv() {  # setv CLAVE valor  (solo si está vacía o con el valor de ejemplo)
  local k="$1" v="$2" cur
  cur="$(grep -E "^$k=" .env | cut -d= -f2- || true)"
  if [[ -z "$cur" || "$cur" == "cambia-esto" ]]; then
    if grep -qE "^$k=" .env; then sed -i "s|^$k=.*|$k=$v|" .env; else echo "$k=$v" >> .env; fi
    echo "  $k generado"
  fi
}

setv INTERNAL_TOKEN "$(rand)"
setv GRAFANA_ADMIN_PASSWORD "$(rand | cut -c1-20)"
grep -q '^VW_ADMIN_TOKEN=' .env || echo 'VW_ADMIN_TOKEN=' >> .env
setv VW_ADMIN_TOKEN "$(rand)$(rand)"
# Un token por cada dispositivo de config/homelab.yml
python3 - <<'EOF' | while read -r key; do setv "$key" "$(rand)"; done
import re, yaml
cfg = yaml.safe_load(open("config/homelab.yml"))
for d in (cfg.get("backups", {}) or {}).get("devices", []) or []:
    print("BACKUP_TOKEN_" + re.sub(r"[^A-Z0-9]", "_", d["name"].upper()))
EOF

# Docker sin sudo solo funciona si tu sesión ya tiene el grupo «docker»
# (recién instalado hay que cerrar sesión). Si no, usamos sudo.
if docker info >/dev/null 2>&1; then DOCKER=(docker); else DOCKER=(sudo docker); fi

if ! grep -qE '^WEB_PASSWORD_HASH=.+' .env; then
  echo
  if ! command -v docker >/dev/null; then
    echo "  Docker aún no está instalado: la contraseña de la web se pedirá al final de install-pi.sh"
  else
    while :; do
      read -r -s -p "Contraseña para entrar a la web del homelab: " pw; echo
      read -r -s -p "Repítela: " pw2; echo
      [[ -n "$pw" && "$pw" == "$pw2" ]] && break
      echo "  No coinciden o está vacía; otra vez."
    done
    if h="$("${DOCKER[@]}" run --rm caddy:2-alpine caddy hash-password --plaintext "$pw")" && [[ "$h" == \$2* ]]; then
      sed -i "s|^WEB_PASSWORD_HASH=.*|WEB_PASSWORD_HASH=${h//\$/\$\$}|" .env
      echo "  WEB_PASSWORD_HASH generado"
    else
      echo "  ✘ No pude generar el hash con Docker. Revisa 'sudo docker info' y vuelve a correr este script."
      exit 1
    fi
  fi
fi
echo
echo "Listo. Revisa .env (token del bot, TELEGRAM_ALLOWED_IDS, HOMELAB_HOST) y config/homelab.yml"
echo "Tokens de respaldo para tus dispositivos:"
grep '^BACKUP_TOKEN_' .env
