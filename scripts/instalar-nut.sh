#!/usr/bin/env bash
# No-break (UPS) con NUT: la Pi lo vigila por USB, te avisa por Telegram cuando se va la luz
# y se apaga sola y en orden antes de que se acabe la batería.
#
#   En la Pi (con el no-break conectado por USB):
#     sudo ./scripts/instalar-nut.sh                    detecta el no-break y lo configura
#     sudo ./scripts/instalar-nut.sh --permitir 192.168.1.50   deja que la PC lo lea también
#     sudo ./scripts/instalar-nut.sh --apagar-en 10     apágate tras 10 min sin luz (además de «batería baja»)
#     sudo ./scripts/instalar-nut.sh --probar           manda un aviso de prueba al bot
#
#   En la PC (enchufada al mismo no-break):
#     sudo ./scripts/instalar-nut.sh --cliente 192.168.1.5
#     (en la Pi corre antes:  sudo ./scripts/instalar-nut.sh --permitir IP-de-la-PC)
#
# Guarda los archivos originales de /etc/nut como *.homelab-orig; `limpiar-pi.sh` los regresa.
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$PWD"
[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"

MODE=servidor; PI=""; ALLOW=(); AFTER=0; TEST=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --cliente) MODE=cliente; PI="${2:?falta la IP de la Pi}"; shift ;;
    --permitir) ALLOW+=("${2:?falta la IP}"); shift ;;
    --apagar-en) AFTER="${2:?faltan los minutos}"; shift ;;
    --probar) TEST=1 ;;
    -h|--help) sed -n 2,15p "$0"; exit 0 ;;
    *) echo "Opción desconocida: $1"; exit 1 ;;
  esac; shift
done

B=$'\e[1m'; G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; N=$'\e[0m'
ok() { echo "${G}✔${N} $*"; }; warn() { echo "${Y}⚠${N} $*"; }; die() { echo "${R}✘${N} $*"; exit 1; }
envget() { [[ -f "$REPO/.env" ]] && grep -E "^$1=" "$REPO/.env" | tail -1 | cut -d= -f2- | sed 's/\$\$/$/g' || true; }
putv() {   # igual que gen-secrets: mismo archivo (mismo inodo)
  local k="$1" v="$2" tmp; tmp="$(mktemp)"
  K="$k" V="$v" awk 'BEGIN{k=ENVIRON["K"]; v=ENVIRON["V"]; d=0} $0 ~ "^"k"=" {if(!d) print k"="v; d=1; next} {print} END{if(!d) print k"="v}' \
    "$REPO/.env" > "$tmp"; cat "$tmp" > "$REPO/.env"; rm -f "$tmp"
}

# ------------------------------------------------------------ aviso de prueba
notify_test() {
  [[ -x /etc/nut/homelab-notify.sh ]] || die "Primero instala NUT (sin --probar)."
  NOTIFYTYPE=TEST UPSNAME=ups /etc/nut/homelab-notify.sh "🔌 Prueba del no-break: si lees esto, los avisos funcionan." \
    && ok "Aviso mandado. Revisa Telegram."
}
if ((TEST)); then notify_test; exit 0; fi

# ------------------------------------------------------------ paquetes
if command -v apt-get >/dev/null; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  if [[ $MODE == servidor ]]; then apt-get install -y -qq nut nut-client nut-server curl >/dev/null
  else apt-get install -y -qq nut-client curl >/dev/null; fi
elif command -v dnf >/dev/null; then dnf install -y -q nut nut-client curl
elif command -v pacman >/dev/null; then pacman -S --noconfirm --needed nut curl
else die "No reconozco el gestor de paquetes; instala «nut» a mano."; fi
ok "NUT instalado"

mkdir -p /etc/nut
backup() { [[ -f "$1" && ! -f "$1.homelab-orig" ]] && cp -a "$1" "$1.homelab-orig"; return 0; }
for f in nut.conf ups.conf upsd.conf upsd.users upsmon.conf upssched.conf; do backup "/etc/nut/$f"; done
touch /etc/nut/.homelab
NUTGRP="$(getent group nut >/dev/null && echo nut || echo root)"

PASS_FILE=/etc/nut/.homelab-pass
if [[ $MODE == servidor ]]; then
  [[ -s $PASS_FILE ]] || { openssl rand -hex 12 > "$PASS_FILE"; chmod 600 "$PASS_FILE"; }
  PASS="$(cat "$PASS_FILE")"
else
  echo "Escribe la contraseña de NUT de la Pi (en la Pi:  sudo cat $PASS_FILE ):"
  read -rs PASS; echo; [[ -n $PASS ]] || die "Sin contraseña."
fi

# ------------------------------------------------------------ aviso por Telegram
write_notify() {
  local url="$1" tok="$2"
  umask 027
  cat > /etc/nut/homelab-notify.env <<EOF
BOT_URL=$url
INTERNAL_TOKEN=$tok
HOST_NAME=$(hostname)
EOF
  chgrp "$NUTGRP" /etc/nut/homelab-notify.env; chmod 640 /etc/nut/homelab-notify.env
  cat > /etc/nut/homelab-notify.sh <<'EOF'
#!/bin/sh
# Lo llama upsmon (NOTIFYCMD) en cada cambio del no-break. Manda el aviso al bot del homelab.
. /etc/nut/homelab-notify.env
case "$NOTIFYTYPE" in
  ONBATT)   t="⚡ Se fue la luz en casa: $HOST_NAME está usando el no-break." ;;
  ONLINE)   t="🔌 Regresó la luz: $HOST_NAME ya está conectado a la corriente." ;;
  LOWBATT)  t="🪫 Batería baja del no-break: $HOST_NAME se va a apagar en orden." ;;
  FSD)      t="⏻ Apagado forzado del no-break: $HOST_NAME se está apagando." ;;
  SHUTDOWN) t="⏻ $HOST_NAME se apaga ahora para proteger los discos." ;;
  COMMBAD|NOCOMM) t="⚠ $HOST_NAME perdió contacto con el no-break (¿cable USB?)." ;;
  COMMOK)   t="✅ $HOST_NAME volvió a ver el no-break." ;;
  REPLBATT) t="🔋 El no-break pide cambio de batería." ;;
  *)        t="$*" ;;
esac
logger -t homelab-ups "$t"
# Una sola línea JSON; las comillas del texto no deben romperla
j=$(printf '%s' "$t" | sed 's/\\/\\\\/g; s/"/\\"/g')
silent=false; [ "$NOTIFYTYPE" = COMMOK ] && silent=true
curl -s -m 8 -X POST "$BOT_URL/notify" -H "Authorization: Bearer $INTERNAL_TOKEN" \
  -H 'Content-Type: application/json' -d "{\"text\":\"$j\",\"silent\":$silent}" >/dev/null 2>&1 || true
EOF
  chmod 755 /etc/nut/homelab-notify.sh
}

TOKEN="$(envget INTERNAL_TOKEN)"
if [[ $MODE == servidor ]]; then
  [[ -n $TOKEN ]] || warn "No encontré INTERNAL_TOKEN en .env: los avisos por Telegram no llegarán (corre ./hl secretos)."
  write_notify "http://127.0.0.1:8080" "$TOKEN"
else
  # En la PC el bot vive en la Pi; solo escucha en 127.0.0.1, así que el aviso local se queda en el registro
  # y la Pi (que es la que ve el USB) es la que manda los avisos de luz. Aquí solo avisamos el apagado.
  if [[ -n $TOKEN && "$(envget HL_TODO_EN_UNO)" == 1 ]]; then write_notify "http://127.0.0.1:8080" "$TOKEN"
  else write_notify "http://127.0.0.1:1" ""; fi
fi

# ------------------------------------------------------------ servidor (Pi)
if [[ $MODE == servidor ]]; then
  echo "${B}Buscando el no-break por USB…${N}"
  SCAN="$(nut-scanner -U -q 2>/dev/null || true)"
  if [[ -z $SCAN ]]; then
    warn "nut-scanner no encontró nada. ¿Está el cable USB conectado a la Pi?"
    lsusb 2>/dev/null | grep -viE 'hub|root' || true
    echo "Uso un controlador genérico (usbhid-ups). Si no funciona, prueba driver = nutdrv_qx en /etc/nut/ups.conf."
    SCAN=$'[ups]\n\tdriver = "usbhid-ups"\n\tport = "auto"'
  fi
  # La primera sección que encuentre se llama «ups»
  {
    echo "# Generado por homelab (instalar-nut.sh). El original está en ups.conf.homelab-orig"
    echo "maxretry = 3"
    printf '%s\n' "$SCAN" | awk 'BEGIN{n=0} /^\[/{n++; if(n==1){print "[ups]"; next} else exit} n==1{print}'
    echo '	desc = "No-break del homelab"'
  } > /etc/nut/ups.conf
  grep -E 'driver' /etc/nut/ups.conf | sed 's/^/  /'

  echo "MODE=netserver" > /etc/nut/nut.conf
  {
    echo "LISTEN 127.0.0.1 3493"
    echo "LISTEN ::1 3493"
    if ((${#ALLOW[@]})); then echo "LISTEN 0.0.0.0 3493"; fi
  } > /etc/nut/upsd.conf
  cat > /etc/nut/upsd.users <<EOF
[upsmon]
	password = $PASS
	upsmon primary
EOF
  MON_LINE="MONITOR ups@localhost 1 upsmon $PASS primary"
else
# ------------------------------------------------------------ cliente (PC)
  echo "MODE=netclient" > /etc/nut/nut.conf
  MON_LINE="MONITOR ups@$PI 1 upsmon $PASS secondary"
  if command -v upsc >/dev/null && ! upsc "ups@$PI" ups.status >/dev/null 2>&1; then
    warn "No pude leer ups@$PI. En la Pi corre:  sudo ./scripts/instalar-nut.sh --permitir $(hostname -I | awk '{print $1}')"
  fi
fi

if ((AFTER > 0)); then
  UPSSCHED="$(command -v upssched || echo /usr/sbin/upssched)"
  cat > /etc/nut/upssched.conf <<EOF
CMDSCRIPT /etc/nut/homelab-upssched.sh
PIPEFN /run/nut/upssched.pipe
LOCKFN /run/nut/upssched.lock
AT ONBATT * START-TIMER apagar $((AFTER * 60))
AT ONLINE * CANCEL-TIMER apagar
EOF
  cat > /etc/nut/homelab-upssched.sh <<'EOF'
#!/bin/sh
[ "$1" = apagar ] && /usr/sbin/upsmon -c fsd
EOF
  chmod 755 /etc/nut/homelab-upssched.sh
  # upssched también avisa por Telegram: lo envolvemos
  cat > /etc/nut/homelab-notify-both.sh <<EOF
#!/bin/sh
/etc/nut/homelab-notify.sh "\$@"
$UPSSCHED "\$@"
EOF
  chmod 755 /etc/nut/homelab-notify-both.sh
  NOTIFY=/etc/nut/homelab-notify-both.sh
else
  NOTIFY=/etc/nut/homelab-notify.sh
fi

cat > /etc/nut/upsmon.conf <<EOF
# Generado por homelab (instalar-nut.sh). El original está en upsmon.conf.homelab-orig
$MON_LINE
MINSUPPLIES 1
SHUTDOWNCMD "/sbin/shutdown -h +0"
NOTIFYCMD $NOTIFY
POLLFREQ 5
POLLFREQALERT 5
HOSTSYNC 15
DEADTIME 25
FINALDELAY 5
EOF
for ev in ONLINE ONBATT LOWBATT FSD COMMOK COMMBAD SHUTDOWN REPLBATT NOCOMM; do
  echo "NOTIFYFLAG $ev SYSLOG+EXEC" >> /etc/nut/upsmon.conf
done
chgrp "$NUTGRP" /etc/nut/*.conf /etc/nut/upsd.users 2>/dev/null || true
chmod 640 /etc/nut/upsd.users /etc/nut/upsmon.conf

if [[ $MODE == servidor ]]; then
  systemctl enable nut-driver-enumerator.path >/dev/null 2>&1 || true
  upsdrvctl -u root start >/dev/null 2>&1 || systemctl restart 'nut-driver@ups' 2>/dev/null || true
  systemctl enable --now nut-server >/dev/null 2>&1 || true
  systemctl restart nut-server >/dev/null 2>&1 || true
fi
systemctl enable --now nut-monitor >/dev/null 2>&1 || systemctl enable --now nut-client >/dev/null 2>&1 || true
systemctl restart nut-monitor >/dev/null 2>&1 || systemctl restart nut-client >/dev/null 2>&1 || true
sleep 3

if [[ $MODE == servidor ]]; then
  if upsc ups@localhost >/tmp/ups.$$ 2>/dev/null; then
    ok "No-break leído: $(grep -E '^(device.model|ups.model):' /tmp/ups.$$ | head -1 | cut -d' ' -f2-) · $(grep '^ups.status:' /tmp/ups.$$ | cut -d' ' -f2-) · batería $(grep '^battery.charge:' /tmp/ups.$$ | cut -d' ' -f2-)%"
  else
    warn "Todavía no puedo leer el no-break. Revisa:  sudo journalctl -u 'nut-driver*' -n 30"
  fi
  rm -f /tmp/ups.$$
  # El panel lo muestra en Inicio
  if [[ -f "$REPO/.env" ]]; then
    putv NUT_HOST 127.0.0.1; putv NUT_UPS ups
    ok "Panel: la tarjeta «No-break» aparece en Inicio (reiniciando netwatch…)"
    (cd "$REPO" && ./hl up -d netwatch >/dev/null 2>&1) || warn "Reinicia el panel:  ./hl up -d netwatch"
  fi
  if command -v ufw >/dev/null && ((${#ALLOW[@]})); then
    for ip in "${ALLOW[@]}"; do ufw allow from "$ip" to any port 3493 proto tcp comment 'NUT' >/dev/null; done
    ok "La(s) PC(s) ${ALLOW[*]} pueden leer el no-break (puerto 3493)"
  fi
  echo
  echo "${B}Listo.${N} Qué pasa cuando se va la luz:"
  echo "  1. Te llega «⚡ Se fue la luz» por Telegram."
  ((AFTER > 0)) && echo "  2. Si en $AFTER min no regresa, todo se apaga en orden."
  echo "  $((AFTER > 0 ? 3 : 2)). Si la batería llega a «baja», primero se apagan las PCs cliente y luego la Pi."
  echo "  Cuando regrese la luz, configura en el BIOS/no-break «encender al volver la corriente»."
  echo "  Prueba los avisos:  sudo ./scripts/instalar-nut.sh --probar"
  echo "  Contraseña de NUT para la PC:  sudo cat $PASS_FILE"
else
  upsc "ups@$PI" ups.status >/dev/null 2>&1 && ok "La PC ve el no-break de la Pi; se apagará sola cuando la Pi lo ordene." \
    || warn "Aún no veo el no-break de la Pi; revisa la contraseña y --permitir en la Pi."
fi
