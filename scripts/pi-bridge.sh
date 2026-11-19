#!/usr/bin/env bash
# ======================================================================
#  Modo puente: la Raspberry queda ENTRE el módem y tus Access Points.
#
#      Módem ──cable── [eth0  Raspberry  eth1] ──cable── switch / AP Tenda
#                              br0 (con la IP de la Pi)
#
#  Así todo lo que sale a internet cruza la Pi: tráfico por dispositivo,
#  Suricata ve todo y las reglas ACL cortan de verdad.
#  Necesitas un adaptador USB 3.0 → Gigabit Ethernet (eth1).
#
#  ⚠ Si la Pi se apaga, la casa se queda sin internet hasta que conectes el
#    módem directo al AP. Deja un cable a la mano. Lee docs/modo-puente.md.
#
#    sudo ./scripts/pi-bridge.sh activar eth0 eth1
#    sudo ./scripts/pi-bridge.sh desactivar
#    sudo ./scripts/pi-bridge.sh estado
# ======================================================================
set -euo pipefail
. "$(dirname "$(readlink -f "$0")")/lib/flags.sh"; hl_flags "$@"; set -- "${HL_ARGS[@]}"   # opciones en inglés (acepta las de antes en español)
ACTION="${1:-estado}"; UP="${2:-eth0}"; DOWN="${3:-eth1}"
[[ $EUID -eq 0 ]] || { echo "Córrelo con sudo"; exit 1; }
command -v nmcli >/dev/null || { echo "Este script usa NetworkManager (Raspberry Pi OS Bookworm lo trae)."; exit 1; }

case "$ACTION" in
  activar)
    ip link show "$DOWN" >/dev/null 2>&1 || { echo "No veo $DOWN. ¿Conectaste el adaptador USB?"; exit 1; }
    echo "==> Creando br0 con $UP + $DOWN (IP por DHCP del módem)"
    nmcli con delete br0 bridge-br0-up bridge-br0-down >/dev/null 2>&1 || true
    nmcli con add type bridge ifname br0 con-name br0 ipv4.method auto ipv6.method auto \
          bridge.stp no bridge.forward-delay 0
    nmcli con add type bridge-slave ifname "$UP" master br0 con-name bridge-br0-up
    nmcli con add type bridge-slave ifname "$DOWN" master br0 con-name bridge-br0-down
    # Las conexiones viejas de eth0/eth1 ya no se usan
    for c in $(nmcli -t -f NAME,DEVICE con show | awk -F: -v a="$UP" -v b="$DOWN" '$2==a||$2==b{print $1}'); do
      [[ "$c" == bridge-br0-* ]] || nmcli con modify "$c" connection.autoconnect no
    done
    # Docker pone la cadena FORWARD de iptables en DROP y, con br_netfilter, eso también
    # aplica al tráfico que cruza br0: sin esta regla la casa se quedaría sin internet.
    cat > /etc/systemd/system/homelab-bridge-fw.service <<'UNIT'
[Unit]
Description=MyHomeLab: deja pasar el trafico del puente br0 aunque Docker este activo
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh -c 'iptables -N DOCKER-USER 2>/dev/null; iptables -C DOCKER-USER -i br0 -o br0 -j ACCEPT 2>/dev/null || iptables -I DOCKER-USER -i br0 -o br0 -j ACCEPT'

[Install]
WantedBy=multi-user.target
UNIT
    systemctl daemon-reload
    systemctl enable --now homelab-bridge-fw.service
    nmcli con up br0
    echo
    echo "✅ Puente activo. Ahora:"
    echo "   1) config/homelab.yml → network.bridge_mode: true, interface: br0"
    echo "   2) .env → CAPTURE_INTERFACE=br0  (para Suricata)"
    echo "   3) docker compose -f hosts/pi/docker-compose.yml --env-file .env up -d"
    echo "   La IP de la Pi pudo cambiar: revisa con 'ip -4 addr show br0' y ajusta HOMELAB_HOST."
    ;;
  desactivar)
    nmcli con delete br0 bridge-br0-up bridge-br0-down 2>/dev/null || true
    for c in $(nmcli -t -f NAME,TYPE con show | awk -F: '$2=="802-3-ethernet"{print $1}'); do
      nmcli con modify "$c" connection.autoconnect yes
    done
    nmcli con up "$(nmcli -t -f NAME,DEVICE con show | awk -F: -v a="$UP" '$2==a||$2==""{print $1; exit}')" || true
    systemctl disable --now homelab-bridge-fw.service 2>/dev/null || true
    rm -f /etc/systemd/system/homelab-bridge-fw.service
    echo "Puente eliminado. Pon bridge_mode: false en homelab.yml."
    ;;
  estado)
    ip -br link show type bridge || true
    bridge link 2>/dev/null || true
    ip -4 -br addr show br0 2>/dev/null || echo "br0 no existe (modo pasivo)"
    nft list table bridge homelab 2>/dev/null | head -20 || true
    ;;
  *) echo "Uso: sudo $0 activar|desactivar|estado [eth_al_modem] [eth_a_los_AP]"; exit 1 ;;
esac
