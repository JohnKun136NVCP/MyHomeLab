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
  echo "   Te agregué al grupo docker. Este script usa sudo; para usar 'docker' sin sudo"
  echo "   cierra sesión y vuelve a entrar."
fi
sudo systemctl enable --now docker >/dev/null 2>&1 || true

say "Herramientas del host (WireGuard para la VPN, utilidades de red)"
sudo apt-get update -qq
sudo apt-get install -y wireguard-tools nftables iproute2 python3-yaml openssl

say "Secretos y configuración"
./scripts/gen-secrets.sh
grep -qE '^WEB_PASSWORD_HASH=.+' .env || { echo "✘ Falta WEB_PASSWORD_HASH en .env"; exit 1; }

say "Docker usa docker0 = 172.17.0.1 (netwatch escucha ahí para Caddy y Prometheus)"
ip -4 addr show docker0 2>/dev/null | grep -q 172.17.0.1 || \
  echo "   ⚠ docker0 no es 172.17.0.1: cambia LISTEN de netwatch en hosts/pi/docker-compose.yml"

say "Limpieza de restos de versiones anteriores (honeypot)"
./scripts/quitar-honeypot.sh >/dev/null 2>&1 || true

say "Servicio para aplicar cambios desde la web (Ajustes)"
mkdir -p run
sudo ./scripts/install-apply-service.sh

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
  • VPS:  pon VPN_VPS_IP (IP del VPS en tu túnel) y el nodo «vps» en homelab.yml (docs/vpn.md)
  • Ver todo el tráfico y bloquear (ACL): docs/modo-puente.md
EOF
