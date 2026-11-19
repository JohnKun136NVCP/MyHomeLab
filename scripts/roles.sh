#!/usr/bin/env bash
# ======================================================================
#  Reparte los servicios entre la Pi y tus PCs con Ansible.
#    ./hl roles               muestra qué va en cada máquina (de homelab.yml → machines)
#    ./hl roles ping          ¿puedo entrar por SSH a todas?
#    ./hl roles check         simula: qué cambiaría, sin cambiar nada
#    ./hl roles apply         lo aplica (te pide tu contraseña de sudo una vez)
#    ./hl roles apply NOMBRE  solo en esa máquina
#  Guía: docs/redistribucion.md
# ======================================================================
set -euo pipefail
. "$(dirname "$(readlink -f "$0")")/lib/flags.sh"; hl_flags "$@"; set -- "${HL_ARGS[@]}"
REPO="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
cd "$REPO"
G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; B=$'\e[1m'; N=$'\e[0m'
need_ansible() {
  command -v ansible-playbook >/dev/null && return 0
  echo "${Y}Ansible no está instalado. Lo instalo (solo en esta máquina, la que manda).${N}"
  if command -v apt-get >/dev/null; then sudo apt-get update -qq && sudo apt-get install -y -qq ansible-core >/dev/null
  elif command -v dnf >/dev/null; then sudo dnf install -y -q ansible-core
  elif command -v pacman >/dev/null; then sudo pacman -S --noconfirm --needed ansible-core
  else echo "${R}Instala ansible-core a mano.${N}"; exit 1; fi
}
cmd="${1:-plan}"; shift || true
case "$cmd" in
  plan|plan-only|"")
    echo "${B}Máquinas y lo que lleva cada una${N} (config/homelab.yml → machines)"
    python3 scripts/lib/inventory.py plan || { echo; sed -n '/^machines:/,/^[a-z]/p' config/homelab.example.yml | head -30; exit 1; }
    echo; echo "  ./hl roles ping   ·   ./hl roles check   ·   ./hl roles apply" ;;
  ping|check|apply)
    need_ansible
    python3 scripts/lib/inventory.py >/dev/null
    cd ansible
    lim=(); [[ -n "${1:-}" ]] && lim=(--limit "$1")
    case "$cmd" in
      ping) ANSIBLE_CONFIG=ansible.cfg ansible all "${lim[@]}" -m ansible.builtin.ping ;;
      check) ANSIBLE_CONFIG=ansible.cfg ansible-playbook site.yml "${lim[@]}" --check --diff -K ;;
      apply) ANSIBLE_CONFIG=ansible.cfg ansible-playbook site.yml "${lim[@]}" -K
             echo; echo "${G}Listo.${N} Revisa:  ./hl check   y el panel (Inicio → Servicios)." ;;
    esac ;;
  *) sed -n 2,9p "$(readlink -f "$0")" | sed -E 's/^# ?//' ;;
esac
