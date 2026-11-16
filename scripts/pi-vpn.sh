#!/usr/bin/env bash
# Conecta la Raspberry (o la PC) a la VPN del VPS como cliente WireGuard.
#   1) En la web de wg-easy (VPS) crea un cliente "raspberry" y descarga su .conf
#   2) sudo ./scripts/pi-vpn.sh raspberry.conf
set -euo pipefail
CONF="${1:?Uso: sudo $0 archivo.conf}"
[[ $EUID -eq 0 ]] || { echo "Córrelo con sudo"; exit 1; }
command -v wg-quick >/dev/null || apt-get install -y wireguard-tools
install -m 600 "$CONF" /etc/wireguard/wg0.conf
# La Pi siempre conectada y alcanzable desde la VPN aunque haya CGNAT
grep -q PersistentKeepalive /etc/wireguard/wg0.conf || \
  sed -i '/^\[Peer\]/a PersistentKeepalive = 25' /etc/wireguard/wg0.conf
# Solo la red de la VPN por el túnel (el internet de la casa sigue normal)
sed -i 's|^AllowedIPs.*|AllowedIPs = 10.8.0.0/24|' /etc/wireguard/wg0.conf
# Sin DNS del túnel (la Pi usa el de casa)
sed -i '/^DNS/d' /etc/wireguard/wg0.conf
systemctl enable --now wg-quick@wg0
sleep 2
wg show wg0 | head -12
ping -c 2 -W 2 10.8.0.1 && echo "✅ La Pi ya ve al VPS por la VPN (10.8.0.1)"
