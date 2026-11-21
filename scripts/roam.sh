#!/usr/bin/env bash
# Que la Pi visite tus otras redes Wi-Fi (otros SSID) por turnos y vea sus equipos.
#
#   ./hl roam                 revisa: redes de homelab.yml, Wi-Fi guardadas, cuáles ve la Pi
#   sudo ./hl roam add SSID   guarda la contraseña de una Wi-Fi SIN conectarse (no te corta nada)
#   ./hl roam now             que visite las otras redes ya, sin esperar su turno
#
# En homelab.yml, cada SSID es una red con su cidr y «interface: wlan0»; las que comparten la
# misma interfaz se visitan por turnos (cada roaming.hop_every s) y la Pi regresa a la primera.
# La red con su propio cable (eth0 → casa) se sigue escaneando siempre.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."
G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; B=$'\e[1m'; N=$'\e[0m'
cmd="${1:-check}"; shift || true
WIFI="$(for i in /sys/class/net/*; do [[ -d $i/wireless ]] && basename "$i"; done | head -1)"; WIFI="${WIFI:-wlan0}"

case "$cmd" in
  check|status|"")
    command -v nmcli >/dev/null || { echo "${R}Falta NetworkManager (nmcli).${N} En Raspberry Pi OS Bookworm ya viene."; exit 1; }
    echo "${B}Wi-Fi de la Pi:${N} $WIFI → $(nmcli -t -f GENERAL.CONNECTION dev show "$WIFI" 2>/dev/null | cut -d: -f2- || echo '¿?')"
    mapfile -t saved < <(nmcli -t -f NAME,TYPE connection show | awk -F: '$2 ~ /wireless/ {print $1}')
    mapfile -t seen < <(nmcli -t -f SSID dev wifi list --rescan auto 2>/dev/null | sed 's/\\:/:/g' | grep -v '^$' | sort -u)
    echo "${B}Redes en homelab.yml:${N}"
    python3 - "$WIFI" <<'PY' | while IFS=$'\t' read -r name iface cidr ssid; do
import sys, yaml
cfg = yaml.safe_load(open("config/homelab.yml")) or {}
net = cfg.get("network") or {}
for n in net.get("networks") or []:
    print("\t".join([n.get("name", ""), n.get("interface") or net.get("interface") or "eth0", n.get("cidr", ""),
                     n.get("connection") or n.get("ssid") or ""]))
PY
      if [[ ! -d /sys/class/net/$iface/wireless ]]; then
        printf "  %-14s %-7s %-18s ${G}cable, se escanea siempre${N}\n" "$name" "$iface" "$cidr"; continue
      fi
      prof="${ssid:-$name}"
      if printf '%s\n' "${saved[@]}" | grep -qxF -- "$prof"; then st="${G}Wi-Fi guardada${N}"; else st="${R}sin guardar → sudo ./hl roam add \"$prof\"${N}"; fi
      if printf '%s\n' "${seen[@]}" | grep -qxF -- "$prof"; then vis="se ve"; else vis="${Y}no se ve desde aquí${N}"; fi
      printf "  %-14s %-7s %-18s SSID «%s»: %b, %b\n" "$name" "$iface" "$cidr" "$prof" "$st" "$vis"
    done
    n_wifi="$(python3 -c 'import yaml; c=yaml.safe_load(open("config/homelab.yml")) or {}; n=c.get("network") or {}; print(sum(1 for x in n.get("networks") or [] if (x.get("interface") or n.get("interface") or "eth0").startswith("wl")))')"
    if ((n_wifi < 2)); then
      echo "  ${Y}Solo hay una red por Wi-Fi:${N} para que brinque, agrega cada SSID como red con «interface: $WIFI»"
      echo "  (docs/redes-multiples.md). Si los SSID son del mismo router y misma red (misma IP 192.168.X), no hace falta:"
      echo "  la Pi ya ve a todos sus equipos."
    fi
    echo "${B}Wi-Fi que la Pi ve ahora:${N} $(printf '%s, ' "${seen[@]}" | sed 's/, $//')" ;;

  add)
    [[ $EUID -eq 0 ]] || exec sudo "$0" add "$@"
    ssid="${1:?Uso: sudo ./hl roam add SSID}"
    if nmcli -t -f NAME connection show | grep -qxF -- "$ssid"; then echo "Ya estaba guardada «$ssid»."; exit 0; fi
    read -rsp "Contraseña de «$ssid» (no se muestra): " pass; echo
    nmcli connection add type wifi ifname "$WIFI" con-name "$ssid" ssid "$ssid" \
      wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$pass" connection.autoconnect no >/dev/null
    echo "${G}✔${N} Guardada (sin conectarse; autoconnect apagado para que no te robe la Wi-Fi principal)."
    echo "  Pruébala:  ./hl roam now   y mira panel → Red en un par de minutos." ;;

  now|ya)
    mkdir -p run && touch run/roam-now
    echo "  Listo: en el siguiente ciclo (1-2 min) la Pi visita las otras redes y regresa." ;;

  -h|--help|help) sed -n 2,11p "$0" ;;
  *) echo "Comando desconocido: $cmd"; sed -n 2,11p "$0"; exit 1 ;;
esac
