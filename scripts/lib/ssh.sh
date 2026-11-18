# shellcheck shell=bash
# Funciones de SSH compartidas por los scripts del homelab (se cargan con «source»).
#
#   ssh_ports                 puertos en los que escucha el SSH de ESTA máquina (uno por línea)
#   ssh_target DESTINO        prepara la conexión a otra máquina. DESTINO puede ser
#                             usuario@IP, usuario@IP:2222 o un alias de ~/.ssh/config.
#                             Deja: SSH_HOST, SSH_USER, SSH_PORT, SSH_IP y los arreglos
#                             RSSH (ssh), RSCP (scp) y RSYNC_SSH (para rsync -e).

# Puerto(s) del SSH local. Orden: homelab.yml (firewall.ssh.port) → sshd -T → ssh.socket
# (Ubuntu 22.10+ arranca SSH por socket) → lo que está escuchando → sshd_config → 22.
ssh_ports() {
  local p="" cfg="${HL_REPO:-.}/config/homelab.yml"
  if [[ -f $cfg ]]; then
    p="$(python3 - "$cfg" <<'PY' 2>/dev/null || true
import sys, yaml
s = ((yaml.safe_load(open(sys.argv[1])) or {}).get("firewall") or {}).get("ssh") or {}
v = s.get("port") or s.get("puerto")
print("\n".join(str(x) for x in (v if isinstance(v, list) else [v])) if v else "")
PY
)"
  fi
  if [[ -z $p ]] && systemctl is-active -q ssh.socket 2>/dev/null; then
    p="$(systemctl show ssh.socket -p Listen --value 2>/dev/null | grep -oE '[0-9]+ \(Stream\)' | awk '{print $1}')"
  fi
  if [[ -z $p ]]; then
    p="$( { sshd -T 2>/dev/null || sudo -n sshd -T 2>/dev/null; } | awk '$1=="port"{print $2}')"
  fi
  if [[ -z $p ]]; then
    p="$( { ss -Htlnp 2>/dev/null || sudo -n ss -Htlnp 2>/dev/null; } | awk '/"sshd"/ {n=split($4,a,":"); print a[n]}')"
  fi
  if [[ -z $p ]]; then
    p="$(grep -hiE '^[[:space:]]*Port[[:space:]]+[0-9]+' /etc/ssh/sshd_config /etc/ssh/sshd_config.d/*.conf 2>/dev/null | awk '{print $2}')"
  fi
  printf '%s\n' "${p:-22}" | grep -E '^[0-9]+$' | sort -un
}

ssh_target() {
  local dest="$1" port=""
  # usuario@IP:2222 (sin confundirlo con una IPv6)
  if [[ $dest =~ ^(.+):([0-9]{1,5})$ && ${BASH_REMATCH[1]} != *:* ]]; then
    dest="${BASH_REMATCH[1]}"; port="${BASH_REMATCH[2]}"
  fi
  port="${port:-${SSH_PORT_OVERRIDE:-}}"
  # ssh -G resuelve todo como lo haría ssh (alias de ~/.ssh/config incluidos)
  local g; g="$(ssh -G ${port:+-p "$port"} "$dest" 2>/dev/null)"
  SSH_HOST="$dest"
  SSH_USER="$(awk '$1=="user"{print $2; exit}' <<<"$g")"
  SSH_PORT="$(awk '$1=="port"{print $2; exit}' <<<"$g")"; SSH_PORT="${SSH_PORT:-${port:-22}}"
  SSH_IP="$(awk '$1=="hostname"{print $2; exit}' <<<"$g")"
  # Sin ssh -G (cliente muy viejo): lo sacamos del texto
  [[ -n $SSH_USER ]] || { [[ $dest == *@* ]] && SSH_USER="${dest%@*}" || SSH_USER="$(id -un)"; }
  [[ -n $SSH_IP ]] || SSH_IP="${dest#*@}"
  if [[ -n $SSH_IP && ! $SSH_IP =~ ^[0-9.]+$ ]]; then
    SSH_IP="$(getent ahostsv4 "$SSH_IP" | awk '{print $1; exit}')"
  fi
  # Una sola conexión para todo (no pide la llave/contraseña en cada paso)
  local cm=(-o ControlMaster=auto -o "ControlPath=$HOME/.ssh/cm-homelab-%r@%h:%p" -o ControlPersist=15m)
  RSSH=(ssh -p "$SSH_PORT" "${cm[@]}")
  RSCP=(scp -P "$SSH_PORT" "${cm[@]}")
  RSYNC_SSH="ssh -p $SSH_PORT ${cm[*]}"
}
