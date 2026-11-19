#!/usr/bin/env bash
# Inicio de sesión único con Authelia: entras una vez y ya quedas dentro del panel,
# Grafana, Uptime Kuma y AdGuard. Los administradores usan además un código (2FA).
#
#   ./scripts/sso.sh                 dominio casa.lan (te lo pregunta)
#   ./scripts/sso.sh --domain casa.lan --no-2fa
#   ./scripts/sso.sh --remove        lo apaga y regresa al inicio de sesión de siempre
#   ./scripts/sso.sh --check         revisa todo (DNS, Authelia, Caddy) si no te deja entrar
#
# Las cuentas son las mismas del panel (Ajustes → Cuentas): el panel las copia a Authelia.
# Por IP (https://192.168.1.5:8443) todo sigue igual que antes.
set -euo pipefail
. "$(dirname "$(readlink -f "$0")")/lib/flags.sh"; hl_flags "$@"; set -- "${HL_ARGS[@]}"   # opciones en inglés (acepta las de antes en español)
cd "$(dirname "$0")/.."
REPO="$PWD"
B=$'\e[1m'; G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; N=$'\e[0m'
ok() { echo "${G}✔${N} $*"; }; warn() { echo "${Y}⚠${N} $*"; }; die() { echo "${R}✘${N} $*"; exit 1; }
[[ -f .env ]] || die "Falta .env: corre ./scripts/gen-secrets.sh"

DOM=""; TWOFA=1; REMOVE=0; CHECK=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain) DOM="${2:?}"; shift ;;
    --no-2fa) TWOFA=0 ;;
    --remove) REMOVE=1 ;;
    --check|--revisar) CHECK=1 ;;
    -h|--help) sed -n 2,10p "$0"; exit 0 ;;
    *) die "Opción desconocida: $1" ;;
  esac; shift
done

envget() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- || true; }
putv() {
  local k="$1" v="$2" tmp; tmp="$(mktemp)"
  K="$k" V="$v" awk 'BEGIN{k=ENVIRON["K"]; v=ENVIRON["V"]; d=0} $0 ~ "^"k"=" {if(!d) print k"="v; d=1; next} {print} END{if(!d) print k"="v}' \
    .env > "$tmp"; cat "$tmp" > .env; rm -f "$tmp"
}
profiles_set() {   # profiles_set add|del sso
  local cur new; cur="$(envget COMPOSE_PROFILES | tr -d "\"' ")"
  new="$(printf '%s' "$cur" | tr ',' '\n' | grep -vxE "sso|" || true)"
  [[ $1 == add ]] && new="$(printf '%s\nsso' "$new")"
  putv COMPOSE_PROFILES "$(printf '%s' "$new" | grep -v '^$' | paste -sd, -)"
}

if ((CHECK)); then
  DOM="$(envget SSO_DOMAIN)"; HOST="$(envget HOMELAB_HOST)"; PORT="$(envget HTTPS_PORT)"; PORT="${PORT:-443}"
  [[ $PORT == 443 ]] && P="" || P=":$PORT"
  bad=0; f() { echo "  ${R}✘${N} $*"; bad=1; }
  echo "${B}== Inicio de sesión único${N}"
  [[ -n $DOM ]] || { f "No está instalado (SSO_DOMAIN vacío en .env). Instálalo: ./hl sso"; exit 1; }
  ok "Dominio: $DOM  ·  entra en https://panel.$DOM$P"
  [[ ",$(envget COMPOSE_PROFILES)," == *,sso,* ]] && ok "Perfil sso encendido" || f "Falta sso en COMPOSE_PROFILES (corre ./hl sso de nuevo)"
  st="$(./hl ps authelia --format '{{.Status}}' 2>/dev/null | tail -1)"
  [[ $st == Up* ]] && ok "Authelia: $st" || { f "Authelia no está corriendo (${st:-no existe})"; ./hl logs --tail 15 --no-log-prefix authelia 2>/dev/null | sed 's/^/      /' | tail -8; }
  [[ -s config/authelia/users.yml ]] && ok "Cuentas en Authelia: $(grep -c 'password:' config/authelia/users.yml)" || f "config/authelia/users.yml vacío"
  [[ -f config/caddy/sso/sso.caddy ]] && ok "Caddy tiene la configuración de $DOM" || f "Falta config/caddy/sso/sso.caddy (./hl sso)"
  for n in auth panel; do
    r="$(getent hosts "$n.$DOM" | awk '{print $1}' | head -1)"
    if [[ $r == "$HOST" ]]; then ok "$n.$DOM → $r (la Pi)"; else f "$n.$DOM resuelve a «${r:-nada}», no a la Pi ($HOST)"; fi
  done
  code="$(curl -sk -o /dev/null -w '%{http_code}' --resolve "auth.$DOM:$PORT:$HOST" "https://auth.$DOM:$PORT/api/health" || true)"
  [[ $code == 200 ]] && ok "El portal contesta (https://auth.$DOM$P)" || f "El portal no contesta bien (HTTP $code)"
  loc="$(curl -sk -o /dev/null -w '%{redirect_url}' --resolve "panel.$DOM:$PORT:$HOST" "https://panel.$DOM:$PORT/" || true)"
  [[ $loc == "https://auth.$DOM$P/"* || $loc == "https://auth.$DOM:$PORT/"* ]] && ok "Sin sesión, el panel manda al portal: ${loc%%\?*}" \
    || f "Sin sesión, el panel debería mandar a https://auth.$DOM$P/ y manda a «${loc:-ningún lado}»"
  echo
  echo "  Si en el navegador ves ${B}about:blank#blocked${N}:"
  echo "   • Abre la dirección escribiéndola tú en una pestaña nueva: https://panel.$DOM$P"
  echo "     (el navegador bloquea abrir un dominio .lan desde un enlace si no confía en él todavía)"
  echo "   • Ese equipo debe usar la Pi como DNS: en Chrome/Edge apaga «DNS seguro» (Configuración →"
  echo "     Privacidad → Seguridad), o no verá $DOM."
  echo "   • Instala el certificado de la casa (./hl ca) en ese equipo, y entra una vez a https://auth.$DOM$P"
  echo "   • Prueba en una ventana de incógnito: si ahí funciona, es una extensión (bloqueador)."
  exit $bad
fi
if ((REMOVE)); then
  rm -f config/caddy/sso/sso.caddy
  putv SSO_DOMAIN ""; putv SSO_GRAFANA false
  profiles_set del
  ./hl stop authelia >/dev/null 2>&1 || true
  ./hl rm -f authelia >/dev/null 2>&1 || true
  ./hl up -d netwatch caddy grafana
  ok "Inicio de sesión único apagado. Entra por https://$(envget HOMELAB_HOST):$(envget HTTPS_PORT) como siempre."
  exit 0
fi

HOST="$(envget HOMELAB_HOST)"; PORT="$(envget HTTPS_PORT)"; PORT="${PORT:-443}"
[[ -n $HOST ]] || die "Falta HOMELAB_HOST en .env"
[[ $PORT == 443 ]] && P="" || P=":$PORT"
if [[ -z $DOM ]]; then
  read -rp "Dominio para la casa [casa.lan]: " DOM; DOM="${DOM:-casa.lan}"
fi
[[ $DOM =~ ^[a-z0-9-]+(\.[a-z0-9-]+)+$ ]] || die "Dominio inválido: $DOM (ejemplo: casa.lan)"
[[ $DOM == *.local ]] && die "No uses .local (choca con mDNS). Usa casa.lan o casa.home.arpa"

# ----------------------------------------------------------- secretos
putv SSO_DOMAIN "$DOM"
./scripts/gen-secrets.sh >/dev/null
for k in SSO_SECRET AUTHELIA_JWT_SECRET AUTHELIA_SESSION_SECRET AUTHELIA_STORAGE_KEY; do
  [[ -n "$(envget $k)" ]] || die "No se generó $k"
done
putv SSO_GRAFANA true; putv SSO_GRAFANA_ROLE Admin
ok "Secretos listos"

ADMIN_POLICY=two_factor; ((TWOFA)) || ADMIN_POLICY=one_factor
mkdir -p config/authelia config/caddy/sso

# ----------------------------------------------------------- Authelia
cat > config/authelia/configuration.yml <<EOF
# Lo genera scripts/sso.sh (vuelve a correrlo para cambiarlo).
theme: auto
default_2fa_method: totp
server:
  address: 'tcp://0.0.0.0:9091/'
log:
  level: info
totp:
  issuer: MyHomeLab
authentication_backend:
  password_reset:
    disable: true         # la contraseña se cambia en el panel (Mi cuenta)
  file:
    path: /config/users.yml
    watch: true           # el panel la reescribe al crear/borrar cuentas
access_control:
  default_policy: deny
  rules:
    # Respaldos e instaladores llevan su propio token; el certificado es público
    - domain: 'panel.$DOM'
      resources: ['^/api/backup/', '^/setup/', '^/ca\.crt$', '^/static/']
      policy: bypass
    # Vaultwarden tiene su propio inicio de sesión (y las apps de Bitwarden no saben de Authelia)
    - domain: 'panel.$DOM'
      resources: ['^/vault/admin']
      subject: 'group:admin'
      policy: $ADMIN_POLICY
    - domain: 'panel.$DOM'
      resources: ['^/vault']
      policy: bypass
    - domain: ['grafana.$DOM', 'kuma.$DOM', 'adguard.$DOM']
      subject: 'group:admin'
      policy: $ADMIN_POLICY
    - domain: 'panel.$DOM'
      resources: ['^/grafana', '^/prometheus']
      subject: 'group:admin'
      policy: $ADMIN_POLICY
    - domain: 'panel.$DOM'
      subject: 'group:admin'
      policy: $ADMIN_POLICY
    # Familia y «ve registros»: solo contraseña. Qué ven adentro lo decide el panel.
    - domain: 'panel.$DOM'
      subject: ['group:familia', 'group:registros']
      policy: one_factor
session:
  cookies:
    - domain: '$DOM'
      authelia_url: 'https://auth.$DOM$P'
      default_redirection_url: 'https://panel.$DOM$P'
  expiration: 12h
  inactivity: 2h
  remember_me: 1M
regulation:
  max_retries: 5
  find_time: 10m
  ban_time: 15m
storage:
  local:
    path: /config/db.sqlite3
# Sin correo: el código para registrar el 2FA queda en notification.txt y el panel te lo manda por Telegram
notifier:
  filesystem:
    filename: /config/notification.txt
EOF
chmod 600 config/authelia/configuration.yml
ok "config/authelia/configuration.yml"

# ----------------------------------------------------------- Caddy
cat > config/caddy/sso/sso.caddy <<EOF
# Lo genera scripts/sso.sh. Dominio: $DOM
(homelab_sso) {
	forward_auth authelia:9091 {
		uri /api/authz/forward-auth
		copy_headers Remote-User Remote-Groups Remote-Email Remote-Name
	}
}

https://auth.$DOM:443 {
	tls internal
	reverse_proxy authelia:9091
}

https://panel.$DOM:443 {
	tls internal
	encode zstd gzip

	handle /api/backup/* {
		request_body {
			max_size 4GB
		}
		reverse_proxy bot:8080 {
			transport http {
				read_timeout 2h
				write_timeout 2h
			}
		}
	}
	handle /internal/* {
		respond 404
	}
	@public path /setup/* /ca.crt /static/*
	handle @public {
		reverse_proxy host.docker.internal:8000
	}
	handle /vault/admin* {
		route {
			request_header -Remote-User
			import homelab_sso
			reverse_proxy vaultwarden:80
		}
	}
	handle /vault* {
		reverse_proxy vaultwarden:80
	}
	handle /grafana* {
		route {
			request_header -X-Webauth-User
			import homelab_sso
			request_header X-Webauth-User {http.request.header.Remote-User}
			reverse_proxy grafana:3000
		}
	}
	handle {
		route {
			# Nadie de fuera puede mandar estas cabeceras: las pone Caddy tras validar con Authelia
			request_header -Remote-User
			request_header -X-Homelab-Sso
			import homelab_sso
			request_header X-Homelab-Sso {\$SSO_SECRET}
			# Lo que puede ver cada rol lo sigue decidiendo el panel
			forward_auth host.docker.internal:8000 {
				uri /auth/check
				copy_headers X-Homelab-User X-Homelab-Role
			}
			handle /prometheus* {
				reverse_proxy prometheus:9090
			}
			reverse_proxy host.docker.internal:8000
		}
	}
}

https://grafana.$DOM:443 {
	tls internal
	redir https://panel.$DOM$P/grafana/
}

https://kuma.$DOM:443 {
	tls internal
	route {
		import homelab_sso
		reverse_proxy uptime-kuma:3001
	}
}

https://adguard.$DOM:443 {
	tls internal
	route {
		import homelab_sso
		reverse_proxy adguard:3000
	}
}
EOF
ok "config/caddy/sso/sso.caddy"

# ----------------------------------------------------------- levantar
profiles_set add
echo "${B}Reiniciando el panel para que copie las cuentas a Authelia…${N}"
./hl up -d netwatch >/dev/null
for _ in $(seq 1 30); do [[ -s config/authelia/users.yml ]] && break; sleep 2; done
[[ -s config/authelia/users.yml ]] && ok "Cuentas copiadas: $(grep -c 'password:' config/authelia/users.yml)" \
  || warn "Todavía no aparece config/authelia/users.yml (el panel lo escribe en menos de un minuto)"
[[ -s config/authelia/users.yml ]] || printf 'users: {}\n' > config/authelia/users.yml
./hl up -d authelia caddy grafana
sleep 5
if ./hl ps authelia 2>/dev/null | grep -q -i "up\|running"; then ok "Authelia arriba"
else warn "Authelia no arrancó; mira:  ./hl logs authelia"; fi

# ----------------------------------------------------------- DNS
NAMES=(auth panel grafana kuma adguard)
AGU="$(envget ADGUARD_USER)"; AGP="$(envget ADGUARD_PASSWORD)"
dns_ok=0
if [[ ",$(envget COMPOSE_PROFILES)," == *,dns,* && -n $AGU ]]; then
  cur="$(curl -s -m 5 -u "$AGU:$AGP" http://127.0.0.1:3002/control/rewrite/list || true)"
  if [[ $cur == *"\"*.$DOM\""* ]]; then dns_ok=1
  elif curl -s -m 5 -u "$AGU:$AGP" -H 'Content-Type: application/json' \
      -d "{\"domain\":\"*.$DOM\",\"answer\":\"$HOST\"}" http://127.0.0.1:3002/control/rewrite/add -o /dev/null -w '%{http_code}' | grep -q 200; then
    dns_ok=1
  fi
fi
if ((dns_ok)); then
  ok "AdGuard: *.$DOM → $HOST (todos los equipos que usan la Pi como DNS ya lo resuelven)"
else
  warn "No pude agregar el DNS en AdGuard. Hazlo una vez:"
  echo "   AdGuard → Filtros → Reescrituras DNS → Agregar:  *.$DOM  →  $HOST"
  echo "   o, en un equipo sin AdGuard, agrega al archivo hosts:"
  echo "   $HOST ${NAMES[*]/%/.$DOM}"
fi

echo
echo "${B}Listo.${N}"
echo "  Entra en:  ${B}https://panel.$DOM$P${N}   (inicio de sesión en https://auth.$DOM$P)"
echo "  Usuario y contraseña: los mismos del panel."
((TWOFA)) && echo "  Administradores: la primera vez te pide registrar un código (Google Authenticator, Aegis…)." \
  && echo "  El código de verificación para registrarlo te llega por Telegram."
echo "  Grafana:   https://grafana.$DOM$P    Uptime Kuma: https://kuma.$DOM$P    AdGuard: https://adguard.$DOM$P"
echo "  En Uptime Kuma puedes quitar su propio inicio de sesión (Ajustes → Seguridad → Desactivar autenticación)"
echo "  porque ahora lo protege Authelia; por IP:3001 quedaría abierto, así que hazlo solo si cierras ese puerto."
echo "  El certificado es el mismo de siempre (./hl ca)."
