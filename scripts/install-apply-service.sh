#!/usr/bin/env bash
# Instala el servicio que aplica los cambios hechos desde la web (Ajustes).
#   sudo ./scripts/install-apply-service.sh
set -euo pipefail
[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$REPO/run"
chmod 700 "$REPO/run"
chmod +x "$REPO/scripts/homelab-apply.sh"
cat > /etc/systemd/system/homelab-apply.service <<UNIT
[Unit]
Description=MyHomeLab: aplica los cambios hechos desde la web
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
WorkingDirectory=$REPO
ExecStart=$REPO/scripts/homelab-apply.sh
UNIT
cat > /etc/systemd/system/homelab-apply.path <<UNIT
[Unit]
Description=MyHomeLab: espera solicitudes de la web en run/apply-request

[Path]
PathExists=$REPO/run/apply-request
Unit=homelab-apply.service

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now homelab-apply.path
date +%s > "$REPO/run/apply-service.installed"
echo "✅ Servicio instalado. La web ya puede aplicar cambios (Ajustes → Guardar y aplicar)."
echo "   Bitácora: $REPO/run/apply.log   ·   estado: systemctl status homelab-apply.path"
