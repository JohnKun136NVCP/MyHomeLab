#!/usr/bin/env bash
# Crea .env a partir de .env.example con contraseñas y tokens aleatorios.
# No toca valores que ya tengas puestos. Uso:  ./scripts/gen-secrets.sh
set -euo pipefail
. "$(dirname "$(readlink -f "$0")")/lib/flags.sh"; hl_flags "$@"; set -- "${HL_ARGS[@]}"   # opciones en inglés (acepta las de antes en español)
cd "$(dirname "$0")/.."
[[ -f .env ]] || cp .env.example .env
[[ -f config/homelab.yml ]] || cp config/homelab.example.yml config/homelab.yml
chmod 600 .env

# Cambia o agrega KEY=valor escribiendo en el MISMO archivo (mismo inodo):
# el contenedor del panel monta .env y no vería un archivo nuevo creado por «sed -i».
putv() {
  local k="$1" v="$2" tmp
  tmp="$(mktemp)"
  K="$k" V="$v" awk 'BEGIN{k=ENVIRON["K"]; v=ENVIRON["V"]; done=0}
    $0 ~ "^"k"=" { if (!done) print k"="v; done=1; next } { print }
    END{ if (!done) print k"="v }' .env > "$tmp"
  cat "$tmp" > .env && rm -f "$tmp"
}

# --web-password: vuelve a pedir la contraseña de la web aunque ya exista
if [[ "${1:-}" == "--web-password" ]]; then putv WEB_PASSWORD_HASH ""; fi

rand() { openssl rand -base64 33 | tr -d '/+=\n' | cut -c1-40; }
setv() {  # setv CLAVE valor  (solo si está vacía o con el valor de ejemplo)
  local k="$1" v="$2" cur
  cur="$(grep -E "^$k=" .env | cut -d= -f2- || true)"
  if [[ -z "$cur" || "$cur" == "cambia-esto" ]]; then
    putv "$k" "$v"
    echo "  $k generado"
  fi
}

setv INTERNAL_TOKEN "$(rand)"
setv GRAFANA_ADMIN_PASSWORD "$(rand | cut -c1-20)"
grep -q '^HOMELAB_BACKUP_PASSWORD=' .env || NEW_BK_PASS=1
setv HOMELAB_BACKUP_PASSWORD "$(rand | cut -c1-32)"
grep -q '^VW_ADMIN_TOKEN=' .env || echo 'VW_ADMIN_TOKEN=' >> .env
setv VW_ADMIN_TOKEN "$(rand)$(rand)"
grep -q '^SPEEDTEST_APP_KEY=' .env || echo 'SPEEDTEST_APP_KEY=' >> .env
setv SPEEDTEST_APP_KEY "base64:$(openssl rand -base64 32)"
# Inicio de sesión único (Authelia): solo si ya lo instalaste (sso.sh agrega SSO_DOMAIN)
if grep -qE '^SSO_DOMAIN=.+' .env; then
  for k in SSO_SECRET AUTHELIA_JWT_SECRET AUTHELIA_SESSION_SECRET AUTHELIA_STORAGE_KEY; do
    grep -q "^$k=" .env || echo "$k=" >> .env
    setv "$k" "$(rand)$(rand)"
  done
fi
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
      read -r -s -p "Contraseña para entrar a la web del homelab: " pw || { echo; echo "  (sin terminal: corre ./hl password después)"; break; }; echo
      read -r -s -p "Repítela: " pw2 || { pw=""; break; }; echo
      [[ -n "$pw" && "$pw" == "$pw2" ]] && break
      echo "  No coinciden o está vacía; otra vez."
    done
    if [[ -z "${pw:-}" ]]; then :
    elif h="$("${DOCKER[@]}" run --rm caddy:2-alpine caddy hash-password --plaintext "$pw")" && [[ "$h" == \$2* ]]; then
      putv WEB_PASSWORD_HASH "${h//\$/\$\$}"
      if grep -qE '^WEB_PASSWORD_HASH=\$\$2' .env; then
        echo "  WEB_PASSWORD_HASH guardado (usuario: $(grep -E '^WEB_USER=' .env | cut -d= -f2- || true))"
      else
        echo "  ✘ No se guardó WEB_PASSWORD_HASH en .env"; exit 1
      fi
    else
      echo "  ✘ No pude generar el hash con Docker. Revisa 'sudo docker info' y vuelve a correr este script."
      exit 1
    fi
  fi
fi
grep -qE '^WEB_USER=.+' .env || putv WEB_USER admin
echo
echo "Listo. Revisa .env (token del bot, TELEGRAM_ALLOWED_IDS, HOMELAB_HOST) y config/homelab.yml"
if [[ -n "${NEW_BK_PASS:-}" ]]; then
  echo
  echo "🔐 Contraseña de los respaldos del homelab (guárdala en Vaultwarden Y en papel; sin ella"
  echo "   no podrás restaurar si la Pi se muere):"
  echo "     $(grep -E '^HOMELAB_BACKUP_PASSWORD=' .env | cut -d= -f2-)"
fi
echo "Tokens de respaldo para tus dispositivos:"
grep '^BACKUP_TOKEN_' .env
