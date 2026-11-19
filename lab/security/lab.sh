#!/usr/bin/env bash
# Laboratorio de seguridad en la PC: Wazuh (SIEM) + máquinas vulnerables aisladas.
#
#   ./lab/security/lab.sh wazuh           instala y levanta Wazuh (manager, indexer, dashboard)
#   ./lab/security/lab.sh targets       DVWA + Juice Shop (+ --metasploitable)
#   ./lab/security/lab.sh attack          abre una terminal de Kali dentro de la red aislada
#   ./lab/security/lab.sh status          qué está prendido y dónde entrar
#   ./lab/security/lab.sh stop          apaga todo el laboratorio (no borra nada)
#   ./lab/security/lab.sh remove          borra contenedores, volúmenes y la copia de wazuh-docker
#
# Agentes de Wazuh en la Pi / el VPS:  ./lab/security/wazuh-agent.sh IP-de-la-PC   (en ese equipo)
# Guía de prácticas: docs/laboratorio-seguridad.md
set -euo pipefail
. "$(dirname "$(readlink -f "$0")")/../../scripts/lib/flags.sh"; hl_flags "$@"; set -- "${HL_ARGS[@]}"   # opciones en inglés (acepta las de antes en español)
SELF="$(readlink -f "$0")"
HERE="$(dirname "$SELF")"
cd "$HERE"
WAZUH_VERSION="${WAZUH_VERSION:-v4.14.6}"
WZ="$HERE/wazuh-docker/single-node"
B=$'\e[1m'; G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; N=$'\e[0m'
ok() { echo "${G}✔${N} $*"; }; warn() { echo "${Y}⚠${N} $*"; }; die() { echo "${R}✘${N} $*"; exit 1; }
if docker info >/dev/null 2>&1; then D=(docker); else D=(sudo docker); fi
LAB=("${D[@]}" compose -f "$HERE/docker-compose.yml")
WZC=("${D[@]}" compose -f "$WZ/docker-compose.yml" -p homelab-wazuh)

cmd="${1:-help}"; shift || true
case "$cmd" in
wazuh)
  [[ "$(uname -m)" == x86_64 ]] || die "Wazuh en Docker necesita una PC x86_64 (en la Pi instala solo el agente)."
  cores="$(nproc)"; mem="$(awk '/MemTotal/{print int($2/1048576)}' /proc/meminfo)"
  ((cores >= 4)) || warn "Wazuh pide 4 núcleos; tienes $cores."
  ((mem >= 8)) || warn "Wazuh pide ~8 GB de RAM; tienes ${mem} GB. Puede ir lento."
  # El indexer (OpenSearch) lo necesita; queda guardado para cada arranque
  if [[ "$(sysctl -n vm.max_map_count)" -lt 262144 ]]; then
    echo "vm.max_map_count=262144" | sudo tee /etc/sysctl.d/90-wazuh.conf >/dev/null
    sudo sysctl -q -w vm.max_map_count=262144
    ok "vm.max_map_count=262144"
  fi
  if [[ ! -d "$HERE/wazuh-docker/.git" ]]; then
    git clone -q --depth 1 -b "$WAZUH_VERSION" https://github.com/wazuh/wazuh-docker.git "$HERE/wazuh-docker"
    ok "wazuh-docker $WAZUH_VERSION descargado"
  fi
  cd "$WZ"
  # Puertos: el dashboard NO va en 443 (choca con Caddy si todo vive en la PC) y, mientras tenga la
  # contraseña de fábrica, solo se abre desde la propia PC. Agentes (1514/1515) sí, para la Pi y el VPS.
  sed -i -E 's/"443:5601"/"127.0.0.1:5602:5601"/; s/"9200:9200"/"127.0.0.1:9200:9200"/; s/"55000:55000"/"127.0.0.1:55000:55000"/' docker-compose.yml
  if [[ ! -f config/wazuh_indexer_ssl_certs/root-ca.pem ]]; then
    "${D[@]}" compose -f generate-indexer-certs.yml run --rm generator
    ok "Certificados internos generados"
  fi
  "${WZC[@]}" up -d
  if command -v ufw >/dev/null && sudo ufw status | grep -q "Status: active"; then
    for p in 1514 1515; do sudo ufw allow "$p/tcp" comment 'homelab wazuh agentes' >/dev/null; done
    ok "Firewall: 1514/1515 abiertos para los agentes"
  fi
  echo
  ok "Wazuh arrancando (tarda 1–3 min la primera vez)."
  echo "  Dashboard: ${B}https://127.0.0.1:5602${N}  usuario admin / SecretPassword (cámbiala: docs/laboratorio-seguridad.md)"
  echo "  Agentes:   en la Pi o el VPS corre  ./lab/security/wazuh-agent.sh $(hostname -I | awk '{print $1}')"
  ;;
targets|objetivos)
  prof=()
  [[ "${1:-}" == --metasploitable ]] && prof=(--profile metasploitable)
  mkdir -p trabajo
  "${LAB[@]}" "${prof[@]}" up -d
  ok "Objetivos arriba en la red aislada 10.250.66.0/24 (sin internet ni acceso a tu casa)"
  echo "  DVWA:        ${B}http://127.0.0.1:8181${N}  (admin / password → «Create / Reset Database»)"
  echo "  Juice Shop:  ${B}http://127.0.0.1:8182${N}"
  [[ ${#prof[@]} -gt 0 ]] && echo "  Metasploitable 2: 10.250.66.12 (desde el atacante)"
  echo "  Atacante (Kali): ./lab/security/lab.sh attack"
  ;;
attack|atacar)
  "${LAB[@]}" up -d attacker >/dev/null
  if ! "${LAB[@]}" exec attacker test -x /usr/bin/nmap 2>/dev/null; then
    warn "La red aislada no tiene internet: conecto el atacante un momento para instalar herramientas…"
    net="$("${D[@]}" network ls --format '{{.Name}}' | grep -E '^homelab-seclab_egress$')"
    "${D[@]}" network connect "$net" homelab-seclab-attacker-1
    "${LAB[@]}" exec attacker bash -c 'apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nmap sqlmap nikto hydra curl netcat-traditional metasploit-framework >/dev/null' \
      || warn "No se instalaron todas las herramientas"
    "${D[@]}" network disconnect "$net" homelab-seclab-attacker-1
    ok "Herramientas instaladas; el atacante vuelve a estar aislado"
  fi
  echo "Objetivos: dvwa 10.250.66.10 · juice-shop 10.250.66.11:3000 · metasploitable 10.250.66.12.  Archivos en lab/security/trabajo/"
  exec "${LAB[@]}" exec attacker bash
  ;;
status|estado)
  "${LAB[@]}" --profile metasploitable ps --format 'table {{.Service}}\t{{.Status}}' 2>/dev/null || true
  [[ -d "$WZ" ]] && "${WZC[@]}" ps --format 'table {{.Service}}\t{{.Status}}' 2>/dev/null || echo "Wazuh: no instalado"
  ;;
stop|apagar)
  "${LAB[@]}" --profile metasploitable stop
  [[ -d "$WZ" ]] && "${WZC[@]}" stop
  ok "Laboratorio apagado (los datos se quedan)"
  ;;
remove|quitar)
  read -rp "Esto borra Wazuh (alertas incluidas) y los objetivos. Escribe QUITAR: " a
  [[ $a == QUITAR ]] || die "Cancelado"
  "${LAB[@]}" --profile metasploitable down -v --remove-orphans
  [[ -d "$WZ" ]] && "${WZC[@]}" down -v --remove-orphans
  rm -rf "$HERE/wazuh-docker"
  sudo rm -f /etc/sysctl.d/90-wazuh.conf
  command -v ufw >/dev/null && sudo ufw status numbered | grep -q 'homelab wazuh' && \
    while n="$(sudo ufw status numbered | grep 'homelab wazuh' | head -1 | sed -E 's/^\[ *([0-9]+)\].*/\1/')"; [[ -n $n ]]; do
      yes | sudo ufw delete "$n" >/dev/null; done
  ok "Laboratorio quitado"
  ;;
*) sed -n 2,13p "$SELF" | sed 's/^# \{0,1\}//' ;;
esac
