#!/usr/bin/env bash
# ======================================================================
#  Diagnóstico del homelab: revisa todo y dice cómo arreglar lo que falle.
#    ./hl revisar          (o ./scripts/revisar.sh)
#  Guarda el resultado en run/revisar.json (lo muestra el dashboard en Inicio).
# ======================================================================
set -uo pipefail
cd "$(dirname "$0")/.."
if docker info >/dev/null 2>&1; then D=(docker); else D=(sudo docker); fi
C=("${D[@]}" compose -f hosts/pi/docker-compose.yml --env-file .env)
OK=0; WARN=0; BAD=0
RES=()
G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; B=$'\e[1m'; N=$'\e[0m'
[[ -t 1 ]] || { G=; Y=; R=; B=; N=; }

add() {  # add estado(ok|warn|bad) "qué" "cómo arreglar"
  local st="$1" what="$2" fix="${3:-}"
  case "$st" in
    ok)   ((OK++));   echo "  ${G}✔${N} $what" ;;
    warn) ((WARN++)); echo "  ${Y}⚠${N} $what"; [[ -n "$fix" ]] && echo "      → $fix" ;;
    bad)  ((BAD++));  echo "  ${R}✘${N} $what"; [[ -n "$fix" ]] && echo "      → $fix" ;;
  esac
  RES+=("$(python3 -c 'import json,sys; print(json.dumps({"st":sys.argv[1],"what":sys.argv[2],"fix":sys.argv[3]}))' "$st" "$what" "$fix")")
}
envv() { grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2- | sed 's/^"\(.*\)"$/\1/'; }
section() { echo; echo "${B}$1${N}"; }

# ---------------------------------------------------------------- .env
section "Configuración (.env)"
if [[ ! -f .env ]]; then
  add bad "No existe .env" "./scripts/gen-secrets.sh"
else
  [[ "$(stat -c %a .env)" == 600 ]] && add ok ".env protegido (600)" || add warn ".env lo pueden leer otros usuarios" "chmod 600 .env"
  for k in TELEGRAM_BOT_TOKEN TELEGRAM_ALLOWED_IDS HOMELAB_HOST INTERNAL_TOKEN; do
    v="$(envv $k)"
    if [[ -z "$v" || "$v" == "cambia-esto" ]]; then add bad "$k vacío" "ponlo en el dashboard → Ajustes, o en .env"
    else add ok "$k configurado"; fi
  done
  if [[ "$(envv WEB_PASSWORD_HASH)" == \$\$2* || "$(envv WEB_PASSWORD_HASH)" == \$2* ]]; then
    add ok "Contraseña de la web configurada (usuario: $(envv WEB_USER || echo admin))"
  else
    add bad "No hay contraseña para la web" "./hl contraseña"
  fi
  [[ -f config/homelab.yml ]] && add ok "config/homelab.yml existe" || add bad "Falta config/homelab.yml" "cp config/homelab.example.yml config/homelab.yml"
fi

# -------------------------------------------------------------- Docker
section "Contenedores"
if ! command -v docker >/dev/null; then
  add bad "Docker no está instalado" "./scripts/install-pi.sh"
else
  mapfile -t want < <("${C[@]}" config --services 2>/dev/null)
  running="$("${D[@]}" ps --format '{{.Names}} {{.Status}}' 2>/dev/null)"
  for s in "${want[@]}"; do
    line="$(grep -E "^homelab-pi-$s-[0-9]+ " <<<"$running" | head -1)"
    if [[ -z "$line" ]]; then add bad "$s no está corriendo" "./hl up -d $s   y luego   ./hl logs $s"
    elif [[ "$line" == *"(unhealthy)"* ]]; then add bad "$s corre pero está enfermo" "./hl logs $s"
    elif [[ "$line" == *Restarting* ]]; then add bad "$s se reinicia una y otra vez" "./hl logs $s"
    else add ok "$s arriba"; fi
  done
fi

# ------------------------------------------------------------ servicios
section "Servicios"
HP="$(envv HTTPS_PORT)"; HP="${HP:-443}"
code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 5 "https://127.0.0.1:$HP/login")"
[[ "$code" == 200 ]] && add ok "Dashboard responde en el puerto $HP" || add bad "El dashboard no responde (código $code)" "./hl logs caddy netwatch"

tok="$(envv TELEGRAM_BOT_TOKEN)"; api="$(envv TELEGRAM_API_URL)"; api="${api:-https://api.telegram.org}"; api="${api/host.docker.internal/127.0.0.1}"
if [[ -n "$tok" ]]; then
  r="$(printf 'url = "%s/bot%s/getMe"\n' "$api" "$tok" | curl -s --max-time 10 -K - 2>/dev/null)"
  if [[ "$r" == *'"ok":true'* ]]; then add ok "Token del bot válido (@$(sed -n 's/.*"username":"\([^"]*\)".*/\1/p' <<<"$r"))"
  elif [[ "$r" == *401* || "$r" == *Unauthorized* ]]; then add bad "Telegram rechaza el token del bot" "copia el token de @BotFather en Ajustes"
  else add warn "No pude preguntarle a Telegram por el bot" "revisa internet o el servidor Bot API local"; fi
fi

if [[ ",$(envv COMPOSE_PROFILES)," == *,dns,* ]]; then
  ip="$(hostname -I | awk '{print $1}')"
  if command -v dig >/dev/null; then ans="$(dig +short +time=3 +tries=1 @"$ip" google.com 2>/dev/null | head -1)"
  else ans="$(nslookup google.com "$ip" 2>/dev/null | awk '/^Address: /{print $2; exit}')"; fi
  [[ -n "$ans" ]] && add ok "AdGuard resuelve nombres en $ip" || add bad "AdGuard no contesta en $ip:53" "./hl logs adguard"
fi

if systemctl is-enabled homelab-apply.path >/dev/null 2>&1; then add ok "Servicio de «Guardar y aplicar» instalado"
else add warn "La web no puede aplicar cambios por sí sola" "sudo ./scripts/install-apply-service.sh"; fi

# ------------------------------------------------------------ firewall
section "Firewall (UFW)"
if ! command -v ufw >/dev/null; then
  add warn "UFW no está instalado" "sudo apt install ufw && ./hl firewall"
else
  st="$(sudo -n ufw status 2>/dev/null || true)"
  if [[ -z "$st" ]]; then add warn "No pude leer UFW sin contraseña" "corre: sudo ./hl revisar"
  elif [[ "$st" != *"Status: active"* ]]; then add warn "UFW está apagado" "./hl firewall"
  else
    add ok "UFW activo"
    grep -qE '^53.*ALLOW.*172\.16\.0\.0/12' <<<"$st" && add ok "DNS permitido desde Docker" || add warn "Uptime Kuma no puede consultar el DNS" "./hl firewall"
    if grep -qE '^22(/tcp)? .*ALLOW +Anywhere' <<<"$st"; then add warn "SSH abierto a cualquiera" "./hl firewall (solo tus equipos del laboratorio)"; else add ok "SSH limitado"; fi
  fi
  if sudo -n sshd -T 2>/dev/null | grep -q '^passwordauthentication no'; then add ok "SSH solo con llave"
  else add warn "SSH acepta contraseña" "./hl firewall --solo-llave"; fi
fi

if [[ ",$(envv COMPOSE_PROFILES)," == *,servicios,* ]]; then
  if [[ "$(envv VW_SIGNUPS_ALLOWED)" == false ]]; then add ok "Vaultwarden: registros cerrados (familia por invitación)"
  else add warn "Vaultwarden deja que cualquiera de la red se registre" "cuando tengas tu cuenta: Ajustes → Vaultwarden → desmarca «Permitir crear cuentas»"; fi
fi

# ------------------------------------------------------------ sistema
section "Sistema"
rootdev="$(findmnt -no SOURCE /)"
if [[ "$rootdev" == /dev/mmcblk* ]]; then add warn "El sistema arranca de la microSD ($rootdev)" "mueve BACKUPS_DIR y VW_DATA a tu SSD (docs/seguridad.md)"
else add ok "El sistema está en $rootdev (SSD)"; fi
for d in / "$(envv BACKUPS_DIR)"; do
  [[ -z "$d" || ! -d "$d" ]] && continue
  pct="$(df --output=pcent "$d" | tail -1 | tr -dc 0-9)"; free="$(df -h --output=avail "$d" | tail -1 | tr -d ' ')"
  if ((pct >= 90)); then add bad "Disco de $d al $pct% (quedan $free)" "borra respaldos viejos o baja backups.max_age_days"
  elif ((pct >= 80)); then add warn "Disco de $d al $pct% (quedan $free)"
  else add ok "Disco de $d al $pct% (quedan $free)"; fi
done
if [[ -r /sys/class/thermal/thermal_zone0/temp ]]; then
  t=$(( $(cat /sys/class/thermal/thermal_zone0/temp) / 1000 ))
  if ((t >= 75)); then add bad "Temperatura ${t} °C" "revisa ventilador y disipador"
  elif ((t >= 65)); then add warn "Temperatura ${t} °C"
  else add ok "Temperatura ${t} °C"; fi
fi
if command -v wg >/dev/null && ifs="$(sudo -n wg show interfaces 2>/dev/null)" && [[ -n "$ifs" ]]; then
  add ok "WireGuard activo: $ifs"
fi

# ------------------------------------------------------------ respaldos
section "Respaldo del homelab"
last="$(ls -1t "$(envv HOMELAB_BACKUP_DIR || true)"/homelab-*.tar.gz.enc backups/homelab/homelab-*.tar.gz.enc 2>/dev/null | head -1)"
if [[ -z "$last" ]]; then add warn "Nunca se ha respaldado el homelab" "./hl respaldo   y   sudo ./scripts/respaldo-homelab.sh --instalar"
else
  age=$(( ( $(date +%s) - $(stat -c %Y "$last") ) / 86400 ))
  ((age > 8)) && add warn "Último respaldo del homelab hace $age días" "./hl respaldo" || add ok "Último respaldo del homelab hace $age días"
fi
systemctl is-enabled homelab-backup.timer >/dev/null 2>&1 && add ok "Respaldo semanal programado" || add warn "El respaldo semanal no está programado" "sudo ./scripts/respaldo-homelab.sh --instalar"

# ------------------------------------------------------------ versión
section "Versión"
GIT=(git); [[ $EUID -eq 0 ]] && GIT=(sudo -u "$(stat -c %U .)" git)
if "${GIT[@]}" fetch -q 2>/dev/null; then
  behind="$("${GIT[@]}" rev-list --count HEAD..@{u} 2>/dev/null || echo 0)"
  ((behind > 0)) && add warn "Hay $behind cambio(s) nuevos en GitHub" "./hl actualizar   (o botón Actualizar del dashboard)" || add ok "Al día con GitHub"
fi

echo
echo "${B}Resumen:${N} ${G}$OK bien${N} · ${Y}$WARN avisos${N} · ${R}$BAD problemas${N}"
mkdir -p run
{ printf '{"ts":%s,"ok":%s,"warn":%s,"bad":%s,"items":[' "$(date +%s)" "$OK" "$WARN" "$BAD"
  (IFS=,; printf '%s' "${RES[*]}"); printf ']}\n'; } > run/revisar.json 2>/dev/null || true
((BAD == 0))
