#!/usr/bin/env bash
# ======================================================================
#  Instala MyHomeLab en la Raspberry Pi (Raspberry Pi OS 64 bits / Ubuntu)
#    ./scripts/install-pi.sh
# ======================================================================
set -euo pipefail
cd "$(dirname "$0")/.."
say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }

if [[ "$(uname -m)" != "aarch64" && "$(uname -m)" != "x86_64" ]]; then
  echo "Usa un sistema de 64 bits (Raspberry Pi OS 64-bit). Imágenes de Grafana/Prometheus no traen armv7 en todas sus versiones."
  exit 1
fi

say "Docker"
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
  sudo usermod -aG docker "$USER"
  echo "   Cierra sesión y vuelve a entrar para usar docker sin sudo (o usa 'newgrp docker')."
fi

say "Herramientas del host (WireGuard para la VPN, utilidades de red)"
sudo apt-get update -qq
sudo apt-get install -y wireguard-tools nftables iproute2 python3-yaml openssl

say "Secretos y configuración"
./scripts/gen-secrets.sh

say "Docker usa docker0 = 172.17.0.1 (netwatch escucha ahí para Caddy y Prometheus)"
ip -4 addr show docker0 2>/dev/null | grep -q 172.17.0.1 || \
  echo "   ⚠ docker0 no es 172.17.0.1: cambia LISTEN de netwatch en hosts/pi/docker-compose.yml"

say "Levantando servicios"
sudo docker compose -f hosts/pi/docker-compose.yml --env-file .env up -d --build

HOST="$(grep '^HOMELAB_HOST=' .env | cut -d= -f2)"
cat <<EOF

✅ MyHomeLab está arriba.
   Web:      https://$HOST        (usuario y contraseña de .env)
   Grafana:  https://$HOST/grafana/  (admin / GRAFANA_ADMIN_PASSWORD)

Siguientes pasos (ver README):
  • Certificado: instala la CA de Caddy en tus equipos para quitar el aviso:
      sudo docker compose -f hosts/pi/docker-compose.yml cp caddy:/data/caddy/pki/authorities/local/root.crt .
  • IDS:  sudo docker compose -f hosts/pi/docker-compose.yml --env-file .env --profile ids up -d
  • VPN:  ./scripts/pi-vpn.sh wg0.conf   (el archivo que descargas de wg-easy en el VPS)
  • Ver todo el tráfico y bloquear (ACL): docs/modo-puente.md
EOF
