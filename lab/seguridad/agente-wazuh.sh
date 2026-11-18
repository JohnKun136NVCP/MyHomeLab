#!/usr/bin/env bash
# Agente de Wazuh para la Pi o el VPS: manda sus registros (SSH, sudo, UFW, Suricata…) a la PC.
#   sudo ./lab/seguridad/agente-wazuh.sh 192.168.1.50          (IP de la PC; por VPN usa la IP del túnel)
#   sudo ./lab/seguridad/agente-wazuh.sh --quitar
# Debe ser la misma versión (o menor) que el manager de la PC.
set -euo pipefail
[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"
VER="${WAZUH_AGENT_VERSION:-4.14.6-1}"
G=$'\e[32m'; Y=$'\e[33m'; N=$'\e[0m'

if [[ "${1:-}" == --quitar ]]; then
  systemctl disable --now wazuh-agent 2>/dev/null || true
  apt-get purge -y -qq wazuh-agent >/dev/null 2>&1 || true
  rm -f /etc/apt/sources.list.d/wazuh.list /usr/share/keyrings/wazuh.gpg
  rm -rf /var/ossec
  echo "${G}✔${N} Agente de Wazuh quitado"; exit 0
fi
MGR="${1:?Uso: $0 IP-de-la-PC-con-Wazuh}"
command -v apt-get >/dev/null || { echo "Este script es para Debian/Raspberry Pi OS/Ubuntu."; exit 1; }

apt-get install -y -qq curl gnupg >/dev/null
curl -fsSL https://packages.wazuh.com/key/GPG-KEY-WAZUH | gpg --dearmor --yes -o /usr/share/keyrings/wazuh.gpg
chmod 644 /usr/share/keyrings/wazuh.gpg
echo "deb [signed-by=/usr/share/keyrings/wazuh.gpg] https://packages.wazuh.com/4.x/apt/ stable main" \
  > /etc/apt/sources.list.d/wazuh.list
apt-get update -qq
WAZUH_MANAGER="$MGR" WAZUH_AGENT_NAME="$(hostname)" apt-get install -y -qq "wazuh-agent=$VER" >/dev/null
apt-mark hold wazuh-agent >/dev/null      # que un apt upgrade no lo deje más nuevo que el manager
touch /var/ossec/.homelab

CONF=/var/ossec/etc/ossec.conf
add_log() {   # add_log formato ruta
  grep -q "<location>$2</location>" "$CONF" && return 0
  sed -i "0,/<\/ossec_config>/s##  <localfile>\n    <log_format>$1</log_format>\n    <location>$2</location>\n  </localfile>\n</ossec_config>#" "$CONF"
  echo "${G}✔${N} También lee $2"
}
# Suricata del homelab (perfil ids) y el log de UFW
if command -v docker >/dev/null; then
  mp="$(docker volume inspect homelab-pi_suricata_logs -f '{{.Mountpoint}}' 2>/dev/null || true)"
  [[ -n $mp ]] && add_log json "$mp/eve.json"
fi
[[ -f /var/log/ufw.log ]] && add_log syslog /var/log/ufw.log

systemctl daemon-reload
systemctl enable --now wazuh-agent >/dev/null
sleep 3
if systemctl is-active -q wazuh-agent; then
  echo "${G}✔${N} Agente «$(hostname)» conectado a $MGR. En el dashboard: Agents."
else
  echo "${Y}⚠${N} El agente no arrancó: journalctl -u wazuh-agent -n 30 (¿abriste 1514/1515 en la PC?)"
fi
