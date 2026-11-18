#!/usr/bin/env bash
# ======================================================================
#  Restaura un respaldo hecho con respaldo-homelab.sh (Pi nueva o microSD/SSD nueva).
#    1. Instala Raspberry Pi OS, clona el repo y corre ./scripts/install-pi.sh
#       (o solo instala Docker).
#    2. ./hl restaurar /ruta/homelab-…tar.gz.enc
#  Te pide la contraseña del respaldo (HOMELAB_BACKUP_PASSWORD). Antes de pisar
#  nada guarda lo actual en run/antes-de-restaurar-FECHA/.
# ======================================================================
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
[[ $EUID -eq 0 ]] || exec sudo --preserve-env=HL_RESTORE_PASSWORD,HL_RESTORE_YES,HL_RESTORE_NO_UP "$0" "$@"
F="${1:-}"
[[ -f "$F" ]] || { echo "Uso: ./hl restaurar ARCHIVO.tar.gz.enc"; exit 1; }
F="$(readlink -f "$F")"

PASS="$(grep -E '^HOMELAB_BACKUP_PASSWORD=' .env 2>/dev/null | cut -d= -f2- | sed 's/\$\$/$/g' || true)"
if [[ -n "${HL_RESTORE_PASSWORD:-}" ]]; then PASS="$HL_RESTORE_PASSWORD"      # lo usa migrar.sh
else
  read -r -s -p "Contraseña del respaldo${PASS:+ (Enter = la de este .env)}: " p; echo
  PASS="${p:-$PASS}"
fi
STAGE="$(mktemp -d /tmp/homelab-restore.XXXXXX)"; trap 'rm -rf "$STAGE"' EXIT
if ! openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass fd:3 3<<<"$PASS" -in "$F" | tar -C "$STAGE" -xzf -; then
  echo "✘ Contraseña incorrecta o archivo dañado."; exit 1
fi
echo; cat "$STAGE/LEEME.txt" 2>/dev/null; echo
echo "Contiene:"; ls -1 "$STAGE/volumes" 2>/dev/null | sed 's/^/  volumen: /'
[[ -d "$STAGE/vaultwarden" ]] && echo "  Vaultwarden"
echo "  .env y config/"
if [[ -z "${HL_RESTORE_YES:-}" ]]; then
  read -r -p "Esto REEMPLAZA la configuración y los datos actuales del homelab. ¿Seguir? [s/N] " r
  [[ "$r" =~ ^[sSyY] ]] || { echo "No cambié nada."; exit 0; }
fi

C=(docker compose -f hosts/pi/docker-compose.yml --env-file .env)
SAVE="run/antes-de-restaurar-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$SAVE"; chmod 700 run "$SAVE"
[[ -f .env ]] && cp -a .env "$SAVE/"
[[ -d config ]] && cp -a config "$SAVE/"
[[ -f .env ]] && docker info >/dev/null 2>&1 && "${C[@]}" --profile dns --profile servicios --profile ids down 2>/dev/null || true

owner="$(stat -c %U .)"
cp -a "$STAGE/homelab/.env" .env; chown "$owner" .env; chmod 600 .env
cp -a "$STAGE/homelab/config/." config/; chown -R "$owner" config
[[ -f "$STAGE/homelab/homelab-ca.crt" ]] && cp -a "$STAGE/homelab/homelab-ca.crt" run/

declare -A VOL=([netwatch]=netwatch_data [bot]=bot_data [grafana]=grafana_data [uptime-kuma]=uptime_kuma
                [adguard-conf]=adguard_conf [adguard-work]=adguard_work [caddy]=caddy_data)
for d in "${!VOL[@]}"; do
  [[ -d "$STAGE/volumes/$d" ]] || continue
  v="homelab-pi_${VOL[$d]}"
  docker volume create "$v" >/dev/null
  mp="$(docker volume inspect -f '{{.Mountpoint}}' "$v")"
  find "$mp" -mindepth 1 -delete
  cp -a "$STAGE/volumes/$d/." "$mp/"
  echo "   ✔ $v"
done
if [[ -d "$STAGE/vaultwarden" ]]; then
  VW="$(grep -E '^VW_DATA=' .env | cut -d= -f2-)"; VW="${VW:-./vaultwarden}"
  [[ "$VW" != /* ]] && VW="$REPO/hosts/pi/$VW"
  [[ -d "$VW" ]] && mv "$VW" "$VW.antes-$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$VW"; cp -a "$STAGE/vaultwarden/." "$VW/"
  echo "   ✔ Vaultwarden → $VW"
fi
if [[ -d "$STAGE/immich" ]]; then     # Immich vivía en la Pi: su configuración y la copia de su base
  mkdir -p run/immich-respaldo; cp -a "$STAGE/immich/." run/immich-respaldo/; chown -R "$owner" run/immich-respaldo
  echo "   ✔ Immich: configuración y copia de su base en run/immich-respaldo/ (las fotos no van en el respaldo)."
  echo "     Para mudar Immich con sus fotos usa:  ./scripts/instalar-immich.sh --traer-de usuario@IP-de-la-Pi"
fi
if [[ -z "${HL_RESTORE_NO_UP:-}" ]]; then "${C[@]}" up -d --build; fi
echo
echo "✅ Restaurado. Lo que había antes quedó en $SAVE"
echo "   Revisa con:  ./hl revisar"
