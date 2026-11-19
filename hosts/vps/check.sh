#!/usr/bin/env bash
# ======================================================================
#  Córrelo EN EL VPS cuando Prometheus de la Pi diga que el VPS está abajo («down»).
#    ./hosts/vps/check.sh                 revisa y te dice qué falla
#    ./hosts/vps/check.sh --fix      además lo corrige (te pregunta antes)
#
#  Cómo funciona: node-exporter (las métricas del VPS) escucha SOLO en la IP del VPS dentro del
#  túnel WireGuard, en el puerto 9100. La Prometheus de la Pi entra por el túnel a esa IP.
#  Si falla cualquiera de estas cuatro cosas, la Pi lo ve «abajo»:
#    1. VPN_VPS_IP en hosts/vps/.env es la IP real del VPS en el túnel
#    2. el contenedor node-exporter está corriendo y escuchando en esa IP
#    3. el firewall del VPS deja entrar a la Pi al 9100 por el túnel
#    4. la Pi apunta a esa misma IP (homelab.yml → nodes → vps → target)
# ======================================================================
set -uo pipefail
. "$(dirname "$(readlink -f "$0")")/../../scripts/lib/flags.sh"; hl_flags "$@"; set -- "${HL_ARGS[@]}"   # opciones en inglés (acepta las de antes en español)
cd "$(dirname "$0")/../.."
REPO="$PWD"
FIX=0; [[ "${1:-}" == --fix ]] && FIX=1
G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; B=$'\e[1m'; N=$'\e[0m'
ok() { echo "  ${G}✔${N} $*"; }; bad() { echo "  ${R}✘${N} $*"; PROBLEMS=$((PROBLEMS + 1)); }; tip() { echo "      → $*"; }
yes_no() { local a; read -r -p "      ¿Lo arreglo? [s/N] " a; [[ "$a" =~ ^[sSyY] ]]; }
PROBLEMS=0
ENVF=hosts/vps/.env
if docker info >/dev/null 2>&1; then D=(docker); else D=(sudo docker); fi
C=("${D[@]}" compose -f hosts/vps/docker-compose.yml --env-file "$ENVF")
putv() {
  local k="$1" v="$2" tmp; tmp="$(mktemp)"
  K="$k" V="$v" awk 'BEGIN{k=ENVIRON["K"]; v=ENVIRON["V"]; d=0} $0 ~ "^"k"=" {if(!d) print k"="v; d=1; next} {print} END{if(!d) print k"="v}' \
    "$ENVF" > "$tmp"; cat "$tmp" > "$ENVF"; rm -f "$tmp"
}

echo "${B}== 1. ¿Qué IP tiene el VPS dentro del túnel?${N}"
# Interfaces de WireGuard y sus IPs
mapfile -t WGIF < <(sudo wg show interfaces 2>/dev/null | tr ' ' '\n' | grep -v '^$')
if ((${#WGIF[@]} == 0)); then
  bad "No hay ningún túnel WireGuard activo en el VPS"
  tip "sudo systemctl status wg-quick@wg-hoshimy   (o el nombre de tu túnel)"
  echo; echo "Sin túnel la Pi no puede leer el VPS. Arréglalo primero."; exit 1
fi
declare -A IFIP=()
for i in "${WGIF[@]}"; do
  ipa="$(ip -4 -o addr show dev "$i" 2>/dev/null | awk '{print $4}' | head -1)"
  IFIP[$i]="${ipa%/*}"
  ok "Túnel $i: ${ipa:-sin IP}"
done
[[ -f $ENVF ]] || { cp hosts/vps/.env.example "$ENVF"; echo "  (creé $ENVF a partir del ejemplo)"; }
WANT="$(grep -E '^VPN_VPS_IP=' "$ENVF" | cut -d= -f2- | tr -d "\"' ")"
TUN=""
for i in "${!IFIP[@]}"; do [[ ${IFIP[$i]} == "$WANT" ]] && TUN="$i"; done
if [[ -n $TUN ]]; then
  ok "VPN_VPS_IP=$WANT coincide con el túnel $TUN"
else
  bad "VPN_VPS_IP=$WANT en $ENVF, pero ningún túnel tiene esa IP"
  # Si solo hay un túnel, esa es la buena
  if ((${#WGIF[@]} == 1)); then
    TUN="${WGIF[0]}"; NEWIP="${IFIP[$TUN]}"
    tip "Debe ser $NEWIP (la del túnel $TUN)"
    if ((FIX)) && yes_no; then putv VPN_VPS_IP "$NEWIP"; WANT="$NEWIP"; ok "VPN_VPS_IP=$NEWIP"; RECREATE=1; fi
  else
    tip "Pon en $ENVF la IP del túnel que usa la Pi (VPN_VPS_IP=…)"
  fi
fi

echo "${B}== 2. ¿Corre node-exporter y escucha en $WANT:9100?${N}"
[[ ${RECREATE:-0} == 1 ]] && "${C[@]}" up -d --force-recreate node-exporter >/dev/null 2>&1 && sleep 3
st="$("${D[@]}" ps -a --filter "label=com.docker.compose.project=homelab-vps" --filter "label=com.docker.compose.service=node-exporter" --format '{{.Status}}' | head -1)"
if [[ -z $st ]]; then
  bad "node-exporter no existe en este VPS"
  tip "${C[*]} up -d"
  if ((FIX)) && yes_no; then "${C[@]}" up -d && sleep 3 && st="$("${D[@]}" ps --filter "label=com.docker.compose.service=node-exporter" --format '{{.Status}}' | head -1)"; fi
fi
if [[ $st == Up* ]]; then
  ok "node-exporter: $st"
elif [[ -n $st ]]; then
  bad "node-exporter: $st (se está reiniciando)"
  logs="$("${D[@]}" logs --tail 20 "$("${D[@]}" ps -aq --filter "label=com.docker.compose.service=node-exporter" | head -1)" 2>&1)"
  grep -iE 'error|fatal' <<<"$logs" | tail -3 | sed 's/^/        /'
  if grep -q "cannot assign requested address" <<<"$logs"; then
    tip "VPN_VPS_IP no es la IP del túnel (paso 1)"
  elif grep -q "address already in use" <<<"$logs"; then
    # Otro programa ya usa el 9100: casi siempre el node-exporter instalado con apt
    own="$(sudo ss -ltnpH 'sport = :9100' | grep -oE 'users:\(\("[^"]+",pid=[0-9]+' | head -1)"
    prog="$(sed -E 's/users:\(\("([^"]+)".*/\1/' <<<"$own")"; pid="$(grep -oE '[0-9]+$' <<<"$own")"
    unit="$( [[ -n $pid ]] && ps -o unit= -p "$pid" 2>/dev/null | tr -d ' ' )"
    echo "      El puerto 9100 ya lo usa: ${B}${prog:-?}${N} (pid ${pid:-?}${unit:+, servicio $unit})"
    cid="$(sed -nE 's/^docker-([0-9a-f]{12,})\.scope$/\1/p' <<<"$unit")"
    if [[ -n $cid ]]; then           # otro contenedor con red del host (otro node-exporter)
      cname="$("${D[@]}" ps --no-trunc --filter "id=$cid" --format '{{.Names}} ({{.Image}}, proyecto {{.Label "com.docker.compose.project"}})')"
      echo "      Es OTRO contenedor: ${B}${cname:-$cid}${N}"
      tip "Quítalo:  ${D[*]} rm -f ${cid:0:12}   (si es de otro compose, bórralo de ese archivo para que no vuelva)"
      if ((FIX)) && yes_no; then
        "${D[@]}" update --restart=no "$cid" >/dev/null 2>&1; "${D[@]}" rm -f "$cid" >/dev/null && \
          "${C[@]}" up -d --force-recreate node-exporter >/dev/null 2>&1 && sleep 3 && ok "Quitado; node-exporter del homelab arrancando en $WANT:9100"
      fi
    elif [[ $prog == docker-proxy ]]; then
      tip "Es otro contenedor: ${D[*]} ps --filter publish=9100   y apágalo o cámbiale el puerto"
    elif [[ -n $unit && $unit == *.service ]]; then
      echo "      Ese escucha en TODAS las IPs (también la pública; solo te protege UFW)."
      echo "      El del homelab escucha solo en el túnel ($WANT), que es más seguro."
      tip "Apaga el otro:  sudo systemctl disable --now $unit"
      if ((FIX)) && yes_no; then
        sudo systemctl disable --now "$unit" && "${C[@]}" up -d --force-recreate node-exporter >/dev/null 2>&1 && sleep 3 \
          && ok "Apagué $unit; node-exporter del homelab arrancando en $WANT:9100"
      fi
    else
      tip "Detén ese programa (sudo kill $pid) o cambia su puerto; luego: ${C[*]} up -d --force-recreate node-exporter"
    fi
  fi
fi
listen="$(sudo ss -ltnH 'sport = :9100' | awk '{print $4}' | paste -sd' ' -)"
if [[ -z $listen ]]; then bad "Nadie escucha en el puerto 9100"
elif [[ " $listen " == *" $WANT:9100 "* ]]; then ok "Escucha en $WANT:9100"
else bad "Escucha en $listen, no en $WANT:9100"; tip "${C[*]} up -d --force-recreate node-exporter"; fi
if curl -fsS -m 4 "http://$WANT:9100/metrics" 2>/dev/null | grep -q '^node_'; then ok "Responde métricas en http://$WANT:9100/metrics"
else bad "No responde en http://$WANT:9100/metrics"; fi

echo "${B}== 3. ¿El firewall del VPS deja entrar a la Pi?${N}"
# La Pi es el peer del túnel (o uno de ellos)
mapfile -t PEERS < <(sudo wg show "${TUN:-${WGIF[0]}}" allowed-ips 2>/dev/null | awk '{for(i=2;i<=NF;i++) print $i}' | grep -v '^(none)$' | sed 's#/32$##')
PI="${PI_IP:-}"
if [[ -z $PI ]]; then
  if ((${#PEERS[@]} == 1)); then PI="${PEERS[0]}"
  else echo "  Peers del túnel: ${PEERS[*]:-ninguno}"; read -r -p "  ¿Cuál es la IP de la Pi dentro del túnel? " PI; fi
fi
PI="${PI%/*}"
if command -v ufw >/dev/null && sudo ufw status | grep -q "Status: active"; then
  rule="$(sudo ufw status | grep -E '^9100' || true)"
  if [[ -n $rule ]] && grep -qE "$PI|${PI%.*}\.0/|Anywhere" <<<"$rule"; then
    ok "UFW deja pasar al 9100:"; sed 's/^/        /' <<<"$rule"
  else
    bad "UFW está activo y no deja entrar a la Pi ($PI) al 9100"
    tip "sudo ufw allow in on ${TUN:-wg-hoshimy} from $PI to any port 9100 proto tcp comment 'homelab prometheus'"
    if ((FIX)) && yes_no; then
      sudo ufw allow in on "${TUN:-wg-hoshimy}" from "$PI" to any port 9100 proto tcp comment 'homelab prometheus' >/dev/null && ok "Regla agregada"
    fi
  fi
elif command -v firewall-cmd >/dev/null && sudo firewall-cmd --state 2>/dev/null | grep -q running; then
  echo "  ${Y}⚠${N} Usas firewalld: permite el 9100 en la zona del túnel:"
  tip "sudo firewall-cmd --permanent --zone=trusted --add-interface=${TUN:-wg-hoshimy} && sudo firewall-cmd --reload"
else
  ok "Sin UFW/firewalld activo (si tu proveedor tiene firewall en su web, el túnel no pasa por él)"
fi
# Reglas sueltas de iptables que tiren el 9100 (CrowdSec no bloquea IPs privadas del túnel, pero revisamos)
sudo iptables -S INPUT 2>/dev/null | grep -E -- '--dport 9100 .*-j (DROP|REJECT)' | sed 's/^/        iptables: /'

echo "${B}== 4. En la Pi${N}"
echo "  En config/homelab.yml de la Pi debe estar:"
echo "      infrastructure:"
echo "        nodes:"
echo "          - name: vps"
echo "            target: ${WANT}:9100"
echo "  y en la Pi:  ./hl vps   (lo comprueba desde allá y te dice el error exacto de Prometheus)"
echo
if ((PROBLEMS)); then
  echo "${Y}$PROBLEMS cosa(s) por arreglar.${N} $([[ $FIX == 0 ]] && echo "Corre con --fix para que lo haga por ti.")"
else
  echo "${G}El VPS está bien.${N} Si la Pi aún lo ve abajo, el problema está en el paso 4 o en el túnel: corre ./hl vps en la Pi."
fi
