#!/usr/bin/env bash
# ======================================================================
#  Aplica lo que pidió la web (Ajustes → «Guardar y aplicar»).
#  Lo corre systemd (homelab-apply.path) cuando aparece run/apply-request.
#  También se puede correr a mano:  sudo ./scripts/homelab-apply.sh
# ======================================================================
set -uo pipefail
cd "$(dirname "$0")/.."
REQ=run/apply-request
OUT=run/apply-result.json
LOG=run/apply.log
C=(docker compose -f hosts/pi/docker-compose.yml --env-file .env)

services="all"; force=""
if [[ -f "$REQ" ]]; then
  read -r services force < <(python3 - "$REQ" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
s = r.get("services", "all")
print("all" if s == "all" else ",".join(s) or "all", ",".join(r.get("force", [])) or "-")
PY
)
  [[ "$force" == "-" ]] && force=""
fi
start=$(date +%s)
(
  echo "== $(date '+%F %T') aplicar: servicios=${services} recrear=${force:-ninguno}"
  profiles="$(grep -E '^COMPOSE_PROFILES=' .env | tail -1 | cut -d= -f2- | tr -d '"'"'"' ')"
  echo "   perfiles activos: ${profiles:-ninguno}"
  # Apaga los servicios de los perfiles que desmarcaste
  base="$("${C[@]}" config --services 2>/dev/null | sort)"
  for p in dns servicios ids; do
    if [[ ",$profiles," != *",$p,"* ]]; then
      extra="$(comm -13 <(echo "$base") <("${C[@]}" --profile "$p" config --services 2>/dev/null | sort))"
      for s in $extra; do
        if docker ps -a --format '{{.Names}}' | grep -q "^homelab-pi-$s-"; then
          echo "   apagando $s (perfil $p desactivado)"
          "${C[@]}" --profile "$p" rm -sf "$s"
        fi
      done
    fi
  done
  args=(up -d --build --remove-orphans)
  if [[ "$services" == "all" ]]; then
    "${C[@]}" "${args[@]}"; rc=$?
  else
    IFS=, read -ra list <<< "$services"
    # solo los que existen con los perfiles actuales (vaultwarden puede estar apagado)
    mapfile -t ok < <(printf '%s\n' "${list[@]}" | grep -Fxf <("${C[@]}" config --services))
    rc=0
    if ((${#ok[@]})); then "${C[@]}" "${args[@]}" "${ok[@]}"; rc=$?; fi
  fi
  if [[ -n "$force" && $rc -eq 0 ]]; then
    IFS=, read -ra fl <<< "$force"
    mapfile -t okf < <(printf '%s\n' "${fl[@]}" | grep -Fxf <("${C[@]}" config --services))
    if ((${#okf[@]})); then "${C[@]}" up -d --force-recreate "${okf[@]}"; rc=$?; fi
  fi
  echo "== terminó con código $rc en $(( $(date +%s) - start )) s"
  exit $rc
) > "$LOG" 2>&1
rc=$?
python3 - "$rc" "$LOG" "$OUT" <<'PY'
import json, sys, time
rc, log, out = int(sys.argv[1]), sys.argv[2], sys.argv[3]
lines = open(log, encoding="utf-8", errors="replace").read().splitlines()
json.dump({"ts": time.time(), "ok": rc == 0, "code": rc, "log": lines[-80:]}, open(out, "w"))
PY
rm -f "$REQ"
exit $rc
