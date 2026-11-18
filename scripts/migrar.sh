#!/usr/bin/env bash
# ======================================================================
#  Instala el homelab COMPLETO en una PC o servidor Linux más potente (todo en uno):
#  lo de la Raspberry (monitoreo, bot, DNS, panel…) + lo de la PC (cámaras, Cine, fotos).
#
#    git clone https://github.com/JohnKun136NVCP/MyHomeLab && cd MyHomeLab
#    ./scripts/migrar.sh                          te pregunta cómo
#    ./scripts/migrar.sh --desde-cero             instalación nueva, sin nada de la Pi
#    ./scripts/migrar.sh --desde-pi usuario@IP    copia TODO de la Raspberry por SSH (con llave)
#    ./scripts/migrar.sh --desde-pi usuario@IP:2222   …si el SSH de la Pi no está en el 22
#                                                 (también sirve un alias de ~/.ssh/config)
#
#  Opciones: --con-respaldos  (trae también los respaldos de tus equipos; pueden ser GB)
#            --con-tunel      (mueve tu túnel WireGuard al VPS a esta máquina)
#            --sin-pc         (solo lo de la Pi; sin Frigate/Jellyfin/Cine/Immich)
#            --limpiar-pi     (al final deja la Raspberry limpia con limpiar-pi.sh)
#            --puerto-ssh N   (puerto SSH de la Pi; igual que usuario@IP:N)
#            --sin-firewall-pi  (no copiar las reglas propias de UFW de la Pi)
#
#  Desde la Pi trae: .env, config/, Vaultwarden, cuentas y equipos del dashboard, bot y
#  publicaciones, AdGuard, Uptime Kuma, Grafana, el certificado de Caddy (los equipos que ya lo
#  instalaron no ven avisos) y, si quieres, tus respaldos y el túnel. Luego adapta lo que cambia
#  en otra máquina: nombres de las tarjetas de red, IP, rutas y el servidor de la Bot API.
# ======================================================================
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
HL_REPO="$REPO"; . scripts/lib/ssh.sh
MODE=""; PI=""; WITH_BK=0; WITH_WG=0; WITH_PC=1; CLEAN_PI=0; SSH_PORT_OVERRIDE=""; COPY_UFW=1
while (($#)); do
  case "$1" in
    --desde-cero) MODE=cero ;;
    --desde-pi) MODE=pi; PI="${2:?Falta usuario@IP de la Pi}"; shift ;;
    --puerto-ssh) SSH_PORT_OVERRIDE="${2:?Falta el puerto}"; shift ;;
    --sin-firewall-pi) COPY_UFW=0 ;;
    --con-respaldos) WITH_BK=1 ;; --con-tunel) WITH_WG=1 ;; --sin-pc) WITH_PC=0 ;; --limpiar-pi) CLEAN_PI=1 ;;
    -h|--help) sed -n '3,26p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Opción desconocida: $1"; exit 2 ;;
  esac
  shift
done
[[ $EUID -ne 0 ]] || { echo "Córrelo con tu usuario normal (pedirá sudo cuando haga falta)."; exit 1; }
B=$'\e[1m'; G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; N=$'\e[0m'
step() { echo; echo "${B}== $*${N}"; }
ok() { echo "  ${G}✔${N} $*"; }
ask() { local a; read -r -p "  $1${2:+ [$2]}: " a; echo "${a:-${2:-}}"; }
yes_no() { local a; read -r -p "  $1 [s/N] " a; [[ "$a" =~ ^[sSyY] ]]; }
envget() { grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2-; }
envset() {
  local k="$1" v="$2" tmp; tmp="$(mktemp)"; touch .env
  K="$k" V="$v" awk 'BEGIN{k=ENVIRON["K"];v=ENVIRON["V"];d=0} $0 ~ "^"k"=" {if(!d)print k"="v; d=1; next} {print} END{if(!d)print k"="v}' .env > "$tmp"
  cat "$tmp" > .env; rm -f "$tmp"; chmod 600 .env
}

if [[ -z "$MODE" ]]; then
  echo "${B}¿Cómo instalo el homelab en esta máquina?${N}"
  echo "  1) Copiar TODO lo que ya tienes en la Raspberry (por SSH, con llave)"
  echo "  2) Desde cero (sin nada de la Raspberry)"
  case "$(ask "Elige" 1)" in
    2) MODE=cero ;;
    *) MODE=pi; PI="$(ask "Usuario e IP de la Raspberry" "$(id -un)@192.168.1.69")"
       [[ $PI =~ :[0-9]+$ || -n $SSH_PORT_OVERRIDE ]] || SSH_PORT_OVERRIDE="$(ask "Puerto SSH de la Raspberry" 22)" ;;
  esac
fi

# ---------------------------------------------------------------- 1. lo básico
step "1. Docker y herramientas"
. /etc/os-release
case "${ID_LIKE:-$ID} $ID" in
  *debian*|*ubuntu*) PM=apt ;; *fedora*|*rhel*) PM=dnf ;; *arch*) PM=pacman ;; *) PM=unknown ;;
esac
pkgs() {
  case "$PM" in
    apt) sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y "$@" ;;
    dnf) sudo dnf install -y "$@" ;;
    pacman) sudo pacman -S --needed --noconfirm "$@" ;;
    *) echo "  Instala a mano: $*" ;;
  esac
}
case "$PM" in
  apt) pkgs curl openssl rsync ufw wireguard-tools nftables iproute2 python3-yaml pciutils ;;
  dnf) pkgs curl openssl rsync ufw wireguard-tools nftables iproute python3-pyyaml pciutils ;;
  pacman) pkgs curl openssl rsync ufw wireguard-tools nftables iproute2 python-yaml pciutils ;;
esac >/dev/null
if ! command -v docker >/dev/null; then
  if [[ "$PM" == pacman ]]; then pkgs docker docker-compose >/dev/null; else curl -fsSL https://get.docker.com | sudo sh >/dev/null; fi
fi
sudo systemctl enable --now docker >/dev/null
id -nG "$USER" | grep -qw docker || sudo usermod -aG docker "$USER"
D=(sudo docker)
ok "$(docker --version 2>/dev/null || sudo docker --version)"
sudo mkdir -p /var/lib/telegram-bot-api

# ---------------------------------------------------------------- 2. traer de la Pi
PI_IP=""
if [[ "$MODE" == pi ]]; then
  ssh_target "$PI"
  step "2. Conectando con la Raspberry ($SSH_USER@$SSH_IP, puerto SSH $SSH_PORT)"
  PI_IP="$SSH_IP"; PI="$SSH_HOST"
  # ¿El puerto contesta? Si no, quizá el SSH de la Pi está en otro
  if ! timeout 5 bash -c "</dev/tcp/$PI_IP/$SSH_PORT" 2>/dev/null; then
    echo "  ${Y}⚠${N} $PI_IP no contesta en el puerto $SSH_PORT."
    SSH_PORT_OVERRIDE="$(ask "¿En qué puerto escucha el SSH de la Pi?" "$SSH_PORT")"; ssh_target "$PI"
  fi
  if ! "${RSSH[@]}" -o BatchMode=yes -o ConnectTimeout=6 "$PI" true 2>/dev/null; then
    echo "  Todavía no entro con llave. Creo una (si no tienes) y la copio a la Pi:"
    [[ -f ~/.ssh/id_ed25519 ]] || ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519 -C "homelab-migracion@$(hostname)" >/dev/null
    echo "  (te pide UNA vez la contraseña de la Pi; si SSH ya solo acepta llave, cópiala desde un equipo del laboratorio)"
    ssh-copy-id -p "$SSH_PORT" -i ~/.ssh/id_ed25519.pub "$PI" || { echo "${R}✘ No pude copiar la llave.${N}"; exit 1; }
  fi
  ok "Entro a la Pi con llave"
  PIREPO="$("${RSSH[@]}" "$PI" 'for d in ~/MyHomeLab ~/myhomelab; do [ -f "$d/hl" ] && { echo "$d"; break; }; done')"
  [[ -n "$PIREPO" ]] || { echo "${R}✘ No encontré el repo en la Pi (~/MyHomeLab).${N}"; exit 1; }
  echo "  Repo en la Pi: $PIREPO"
  PIUSER="$SSH_USER"
  echo "  Haciendo el respaldo cifrado en la Pi (te pide la contraseña de sudo de la Pi)…"
  "${RSSH[@]}" -t "$PI" "cd $PIREPO && git pull -q --ff-only || true; sudo ./scripts/respaldo-homelab.sh --copia-para $PIUSER; sudo ufw show added > ~/homelab-ufw.txt 2>/dev/null; sudo ufw status verbose 2>/dev/null | grep -E '^(Default|Logging):' >> ~/homelab-ufw.txt; true"
  "${RSCP[@]}" -q "$PI:homelab-migracion.tar.gz.enc" /tmp/homelab-migracion.tar.gz.enc
  BKPASS="$("${RSSH[@]}" "$PI" "grep -E '^HOMELAB_BACKUP_PASSWORD=' $PIREPO/.env | cut -d= -f2-")"
  BKPASS="${BKPASS//\$\$/\$}"
  "${RSSH[@]}" "$PI" 'rm -f ~/homelab-migracion.tar.gz.enc'
  "${RSCP[@]}" -q "$PI:homelab-ufw.txt" /tmp/homelab-ufw-pi.txt 2>/dev/null && "${RSSH[@]}" "$PI" 'rm -f ~/homelab-ufw.txt' || true
  ok "Respaldo traído ($(du -h /tmp/homelab-migracion.tar.gz.enc | cut -f1))"

  step "3. Restaurando aquí"
  HL_RESTORE_PASSWORD="$BKPASS" HL_RESTORE_YES=1 HL_RESTORE_NO_UP=1 ./scripts/restaurar-homelab.sh /tmp/homelab-migracion.tar.gz.enc
  rm -f /tmp/homelab-migracion.tar.gz.enc
  sudo chown "$USER" .env config -R 2>/dev/null || true

  if ((WITH_BK)) || yes_no "¿Traigo también los respaldos de tus equipos y celulares? (pueden ser varios GB)"; then
    OLDBK="$(envget BACKUPS_DIR)"
    NEWBK="$(ask "¿Dónde los guardo aquí? (un disco grande)" "${OLDBK:-/srv/respaldos}")"
    sudo mkdir -p "$NEWBK"
    echo "  Empaquetando en la Pi (te pide sudo de la Pi; necesita espacio libre allá)…"
    "${RSSH[@]}" -t "$PI" "sudo tar -C '$OLDBK' -cf ~/homelab-respaldos.tar . && sudo chown $PIUSER ~/homelab-respaldos.tar"
    rsync -ah --info=progress2 --remove-source-files -e "$RSYNC_SSH" "$PI:homelab-respaldos.tar" /tmp/homelab-respaldos.tar
    sudo tar -C "$NEWBK" -xf /tmp/homelab-respaldos.tar && rm -f /tmp/homelab-respaldos.tar
    envset BACKUPS_DIR "$NEWBK"; ok "Respaldos en $NEWBK"
  fi
  if ((WITH_WG)) || { "${RSSH[@]}" "$PI" 'ls /etc/wireguard/*.conf' >/dev/null 2>&1 && yes_no "¿Muevo tu túnel WireGuard al VPS a esta máquina? (la Pi lo apaga)"; }; then
    WG="$("${RSSH[@]}" "$PI" 'ls /etc/wireguard/ | grep -E "\.conf$" | grep -v "^wg0.conf$" | head -1')"; WG="${WG%.conf}"
    if [[ -n "$WG" ]]; then
      "${RSSH[@]}" -t "$PI" "sudo install -m 600 -o $PIUSER /etc/wireguard/$WG.conf ~/$WG.conf"
      "${RSCP[@]}" -q "$PI:$WG.conf" /tmp/"$WG".conf && "${RSSH[@]}" "$PI" "rm -f ~/$WG.conf"
      sudo install -m 600 -o root /tmp/"$WG".conf "/etc/wireguard/$WG.conf"; rm -f /tmp/"$WG".conf
      "${RSSH[@]}" -t "$PI" "sudo systemctl disable --now wg-quick@$WG" >/dev/null 2>&1 || true
      sudo systemctl enable --now "wg-quick@$WG" && ok "Túnel $WG activo aquí (misma llave: el VPS no cambia nada)"
    fi
  fi
else
  step "2. Configuración nueva"
  [[ -f config/homelab.yml ]] || cp config/homelab.example.yml config/homelab.yml
  ./scripts/gen-secrets.sh
fi

# ---------------------------------------------------------------- 4. adaptar a esta máquina
step "4. Adaptando a esta máquina"
MY_IP="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}')"
WAN_IF="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev") print $(i+1)}')"
echo "  Tarjetas de red:"; ip -br -4 addr | grep -v '^lo\|^docker\|^br-\|^veth' | sed 's/^/    /'
OLD_IP="$(envget HOMELAB_HOST)"
CASA_IF="$(ask "Tarjeta conectada a la red de la casa" "$WAN_IF")"
LAB_IF="$(ask "Tarjeta de la red del laboratorio (vacío = no hay)" "$(ip -br link | awk '$1 ~ /^(wl|wlan)/ {print $1; exit}')")"
envset HOMELAB_HOST "$MY_IP"
envset CAPTURE_INTERFACES "$CASA_IF${LAB_IF:+,$LAB_IF}"
python3 - "$CASA_IF" "$LAB_IF" "$OLD_IP" "$MY_IP" <<'PY'
import sys, yaml
casa, lab, old, new = sys.argv[1:]
p = "config/homelab.yml"
txt = open(p).read()
if old and new and old != new:
    txt = txt.replace(old, new)                     # enlaces, DNS, etc. que apuntaban a la Pi
c = yaml.safe_load(txt) or {}
nets = (c.get("network") or {}).get("networks") or []
for i, n in enumerate(nets):
    if i == 0:
        n["interface"] = casa
    elif lab and n.get("interface", "").startswith(("wl", "wlan")):
        n["interface"] = lab
    elif not lab and n.get("interface", "").startswith(("wl", "wlan")):
        n["enabled"] = False
fw = c.get("firewall") or {}
if fw.get("ssh") and lab:
    fw["ssh"]["interface"] = lab
with open(p, "w") as f:
    f.write("# Adaptado por scripts/migrar.sh\n")
    yaml.safe_dump(c, f, sort_keys=False, allow_unicode=True)
print(f"  ✔ homelab.yml: casa={casa}" + (f", laboratorio={lab}" if lab else "") + (f", IP {old} → {new}" if old and old != new else ""))
PY
# SSH: ¿mismo puerto que la Pi?
LOCAL_SSH="$(ssh_ports | head -1)"
if [[ $MODE == pi && -n ${SSH_PORT:-} && $SSH_PORT != "$LOCAL_SSH" ]] && \
   yes_no "La Pi tiene SSH en el puerto $SSH_PORT y esta máquina en el $LOCAL_SSH. ¿Pongo aquí también el $SSH_PORT?"; then
  NEWP="$SSH_PORT"
  if sudo ufw status 2>/dev/null | grep -q "Status: active"; then   # que no te quedes fuera al cambiarlo
    sudo ufw allow "$NEWP/tcp" comment "SSH (temporal: firewall.sh lo deja solo para el laboratorio)" >/dev/null
  fi
  command -v semanage >/dev/null && sudo semanage port -a -t ssh_port_t -p tcp "$NEWP" 2>/dev/null || true   # SELinux (Fedora)
  grep -qE '^[[:space:]]*Include[[:space:]]+/etc/ssh/sshd_config\.d' /etc/ssh/sshd_config 2>/dev/null || \
    echo "  ${Y}⚠${N} Tu sshd_config no lee sshd_config.d/: agrega «Port $NEWP» a /etc/ssh/sshd_config a mano."
  printf '# MyHomeLab (migrar.sh): mismo puerto que tenía la Raspberry\nPort %s\n' "$NEWP" | \
    sudo tee /etc/ssh/sshd_config.d/05-homelab-puerto.conf >/dev/null
  if sudo sshd -t 2>/dev/null; then
    if systemctl is-active -q ssh.socket 2>/dev/null; then   # Ubuntu 22.10+: el puerto lo abre el socket
      sudo systemctl daemon-reload && sudo systemctl restart ssh.socket
    else
      sudo systemctl restart ssh 2>/dev/null || sudo systemctl restart sshd
    fi
    sleep 1
    if sudo ss -ltnH "sport = :$NEWP" | grep -q .; then ok "SSH de esta máquina ahora en el puerto $NEWP (tu sesión actual no se corta)"
    else echo "  ${Y}⚠${N} No veo SSH escuchando en $NEWP; revisa:  sudo systemctl status ssh"; fi
  else
    sudo rm -f /etc/ssh/sshd_config.d/05-homelab-puerto.conf
    echo "  ${Y}⚠${N} La configuración de SSH no pasó la prueba; dejé el puerto como estaba ($LOCAL_SSH)."
  fi
fi
# Rutas que en la Pi apuntaban a sus discos
for k in BACKUPS_DIR VW_DATA; do
  v="$(envget $k)"; [[ -z "$v" || "$v" != /* ]] && continue
  sudo mkdir -p "$v"
done
# La Bot API local: en la Pi era tuya (fuera de Docker); aquí, el contenedor del homelab
if [[ "$(envget TELEGRAM_API_URL)" == *host.docker.internal:8081* || -z "$(envget TELEGRAM_API_URL)" ]]; then
  echo "  El bot sube archivos de hasta 2 GB con un servidor local de la Bot API."
  echo "  Necesita tu api_id y api_hash (https://my.telegram.org → API development tools)."
  AID="$(ask "api_id (vacío = usar el de Telegram, máx. 50 MB)" "$(envget TELEGRAM_API_ID)")"
  if [[ -n "$AID" ]]; then
    AHASH="$(ask "api_hash" "$(envget TELEGRAM_API_HASH)")"
    envset TELEGRAM_API_ID "$AID"; envset TELEGRAM_API_HASH "$AHASH"
    envset TELEGRAM_API_URL "http://telegram-bot-api:8081"; envset TELEGRAM_API_DATA /var/lib/telegram-bot-api
    prof="$(envget COMPOSE_PROFILES)"; [[ ",$prof," == *,botapi,* ]] || envset COMPOSE_PROFILES "${prof:+$prof,}botapi"
    ok "Bot API local en contenedor"
  else
    envset TELEGRAM_API_URL "https://api.telegram.org"
    echo "  ${Y}⚠${N} Con la API normal de Telegram el límite de archivos es 50 MB."
  fi
fi
# Puertos 80/443: si están libres aquí, el panel queda en https://IP sin puerto
if ! sudo ss -ltnH 'sport = :443' | grep -q .; then envset HTTPS_PORT 443; envset HTTP_PORT 80; fi
ok "Panel: https://$MY_IP$( [[ "$(envget HTTPS_PORT)" != 443 ]] && echo ":$(envget HTTPS_PORT)")"

# ---------------------------------------------------------------- 5. levantar
step "5. Levantando el homelab"
"${D[@]}" compose -f hosts/pi/docker-compose.yml --env-file .env up -d --build
sudo ./scripts/install-apply-service.sh >/dev/null && ok "«Guardar y aplicar» del panel"
sudo ./scripts/respaldo-homelab.sh --instalar >/dev/null && ok "Respaldo semanal programado"
sleep 5; ./hl ca >/dev/null 2>&1 || true

if ((WITH_PC)); then
  step "6. Lo de la PC (Frigate, Jellyfin, Cine en casa, Immich)"
  HL_TODO_EN_UNO=1 ./scripts/install-server.sh --pi "$MY_IP" --puerto "$(envget HTTPS_PORT)"
  if [[ $MODE == pi ]] && "${RSSH[@]}" "$PI" "test -f $PIREPO/hosts/server/immich/.env" 2>/dev/null; then
    if yes_no "La Pi tiene Immich. ¿Lo mudo aquí con todas sus fotos, cuentas y álbumes?"; then
      SSH_PORT_OVERRIDE="$SSH_PORT" ./scripts/instalar-immich.sh --traer-de "$PI" --si || \
        echo "  ${Y}⚠${N} La mudanza de Immich no terminó; Immich sigue en la Pi. Reintenta: ./scripts/instalar-immich.sh --traer-de $PI"
    else
      ./scripts/instalar-immich.sh --solo-ml "$PI_IP" && \
        echo "  En la Pi corre:  ./scripts/instalar-immich.sh --ml-remoto http://$MY_IP:3003" || true
    fi
  else
    ./scripts/instalar-immich.sh --si || true
  fi
fi

step "7. Firewall"
./scripts/firewall.sh || echo "  ${Y}⚠${N} firewall.sh no aplicó las reglas del homelab (revisa firewall.ssh.allow en homelab.yml y corre ./hl firewall)"

# Las reglas de UFW que pusiste tú en la Pi (nginx, 8090, lo que sea): se adaptan a esta máquina.
# Las del homelab y las de SSH no se copian: firewall.sh ya las hizo con el puerto y las tarjetas de aquí.
if [[ $MODE == pi ]] && ((COPY_UFW)) && [[ -s /tmp/homelab-ufw-pi.txt ]]; then
  step "7b. Tus reglas de UFW de la Raspberry"
  grep -E '^(Default|Logging):' /tmp/homelab-ufw-pi.txt | sed 's/^/  En la Pi → /' || true
  mapfile -t PI_RULES < <(python3 - /tmp/homelab-ufw-pi.txt "$PI_IP" "$MY_IP" "$CASA_IF" "$LAB_IF" "${SSH_PORT:-22}" <<'PY'
import re, sys
f, old, new, casa, lab, sshp = sys.argv[1:]
homelab = re.compile(r"comment '?(Dashboard homelab|Dashboard \(redirige a https\)|DNS AdGuard|DNS desde Docker|"
                     r"node-exporter por VPN|homelab|honeypot|NUT|SSH|m[eé]tricas para la Pi)", re.I)
ssh = re.compile(rf"\bOpenSSH\b|\bport ({sshp}|22)\b|^(allow|limit|deny|reject)( in)? ({sshp}|22)(/tcp)?(\s|$)")
seen = set()
for ln in open(f, errors="replace"):
    ln = ln.strip()
    if not ln.startswith("ufw "):
        continue
    r = ln[4:]
    if homelab.search(r):
        continue
    if ssh.search(r):
        print(f"  (no copio la regla de SSH «{r}»: la de aquí usa el puerto y los equipos del laboratorio)", file=sys.stderr)
        continue
    m = re.search(r"\bon (\S+)", r)
    if m:
        ifc = m.group(1)
        to = lab if ifc.startswith(("wl", "wlan")) else casa
        if not to:
            print(f"  (no copio «{r}»: aquí no hay red del laboratorio)", file=sys.stderr)
            continue
        r = r.replace(f"on {ifc}", f"on {to}", 1)
    if old and new and old != new:
        r = re.sub(rf"(?<![\d.]){re.escape(old)}(?![\d.])", new, r)
    if r not in seen:
        seen.add(r)
        print(r)
PY
)
  if ((${#PI_RULES[@]})); then
    printf '    ufw %s\n' "${PI_RULES[@]}"
    if yes_no "¿Agrego estas ${#PI_RULES[@]} regla(s) aquí?"; then
      n=0
      for r in "${PI_RULES[@]}"; do
        if eval "sudo ufw $r" >/dev/null 2>&1; then n=$((n + 1)); else echo "  ${Y}⚠${N} No pude: ufw $r"; fi
      done
      sudo ufw reload >/dev/null 2>&1 || true
      ok "$n regla(s) de la Pi copiadas"
    fi
  else
    ok "La Pi no tenía reglas propias aparte de las del homelab"
  fi
  rm -f /tmp/homelab-ufw-pi.txt
fi

if [[ "$MODE" == pi ]]; then
  echo
  echo "${B}Falta hacer a mano (2 minutos):${N}"
  echo "  • Si el módem o las Tenda usaban la Pi ($PI_IP) como DNS, cámbialo a $MY_IP."
  echo "  • Uptime Kuma: revisa los monitores que apuntaban a $PI_IP."
  echo "  • Los equipos que respaldan siguen funcionando si cambias su dirección a https://$MY_IP:$(envget HTTPS_PORT)"
  echo "    (Mis respaldos → Nuevo enlace de instalación lo hace por ti)."
  [[ -n "$(envget SSO_DOMAIN)" ]] && echo "  • Inicio de sesión único: en AdGuard cambia *.$(envget SSO_DOMAIN) a $MY_IP (o corre ./hl sso de nuevo)."
  [[ -n "$(envget NUT_HOST)" ]] && echo "  • No-break: conecta su USB a esta PC y corre  sudo ./hl nobreak"
  if ((CLEAN_PI)) || yes_no "¿Dejo la Raspberry limpia ahora? (borra el homelab de la Pi)"; then
    "${RSSH[@]}" -t "$PI" "cd $PIREPO && sudo ./scripts/limpiar-pi.sh --sin-respaldo"
  else
    echo "  Cuando quieras:  ssh -p $SSH_PORT -t $PI 'cd $PIREPO && sudo ./scripts/limpiar-pi.sh'"
  fi
fi
echo
echo "${G}${B}✅ Homelab instalado en $(hostname) ($MY_IP).${N}  Revisa todo con:  ./hl revisar"
id -nG "$USER" | grep -qw docker || echo "   (cierra sesión y vuelve a entrar para usar docker sin sudo)"
