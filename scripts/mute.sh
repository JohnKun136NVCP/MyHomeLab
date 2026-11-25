#!/usr/bin/env bash
# Callar / volver a prender los avisos de «no responde» de un equipo (AP, router, nodo).
#   ./hl mute "Router Oficinas"     ./hl unmute "Router Oficinas"     ./hl mute   (lista)
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."
op="$(basename "$0" .sh)"; [[ ${HL_OP:-} ]] && op="$HL_OP"
python3 - "$op" "${1:-}" <<'PY'
import sys, yaml
op, name = sys.argv[1], sys.argv[2]
p = "config/homelab.yml"; c = yaml.safe_load(open(p)) or {}
al = c.get("alerts") or {}
mute = [str(m) for m in al.get("mute") or []]
if not name:
    print("Callados: " + (", ".join(mute) or "ninguno")); raise SystemExit
mute = [m for m in mute if m.lower() != name.lower()]
if op == "mute":
    mute.append(name)
al["mute"] = mute; c["alerts"] = al
yaml.safe_dump(c, open(p, "w"), sort_keys=False, allow_unicode=True)
print(f"  ✔ {'Ya no te aviso' if op == 'mute' else 'Vuelvo a avisarte'} si «{name}» no responde")
PY
[[ -n ${1:-} ]] && { ./hl restart netwatch >/dev/null 2>&1 || true; curl -fsS -m 5 -X POST http://127.0.0.1:9090/prometheus/-/reload >/dev/null 2>&1 || true; }
