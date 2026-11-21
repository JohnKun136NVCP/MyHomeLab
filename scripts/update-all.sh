#!/usr/bin/env bash
# Actualizar TODO de una vez y revisar vulnerabilidades de las imágenes.
#
#   ./hl update --all            git pull + imágenes nuevas (Pi, casa, Immich) + reconstruir el
#                                panel y el bot desde cero + limpiar imágenes viejas + escaneo
#   ./hl update --all --no-scan  lo mismo sin el escaneo (más rápido)
#   ./hl scan                    solo el escaneo de las imágenes que están corriendo
#
# El escaneo usa Trivy (en un contenedor, no se instala nada) y cuenta solo vulnerabilidades
# ALTAS y CRÍTICAS que YA tienen arreglo: esas se quitan actualizando. El resumen te llega por
# Telegram y queda en panel → Inventario.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."
if docker info >/dev/null 2>&1; then D=(docker); else D=(sudo docker); fi
C=("${D[@]}" compose -f hosts/pi/docker-compose.yml --env-file .env)
H=("${D[@]}" compose -f hosts/home/docker-compose.yml --env-file hosts/home/.env)
I=("${D[@]}" compose -f hosts/server/immich/docker-compose.yml --env-file hosts/server/immich/.env)
B=$'\e[1m'; G=$'\e[32m'; Y=$'\e[33m'; N=$'\e[0m'
step() { echo; echo "${B}== $*${N}"; }
SCAN=1; ONLY_SCAN=0
for a in "$@"; do case "$a" in --no-scan) SCAN=0 ;; --scan-only) ONLY_SCAN=1 ;; esac; done

scan() {
  step "Revisando vulnerabilidades de las imágenes (Trivy; la primera vez baja su base, ~1-3 min)"
  mkdir -p run
  mapfile -t imgs < <("${D[@]}" ps --format '{{.Image}}' | sort -u)
  local out=run/trivy.json tmp; tmp="$(mktemp -d)"
  for img in "${imgs[@]}"; do
    printf "  %-55s " "$img"
    if "${D[@]}" run --rm -v /var/run/docker.sock:/var/run/docker.sock:ro -v homelab-trivy-cache:/root/.cache/ \
         aquasec/trivy:latest image --quiet --scanners vuln --severity HIGH,CRITICAL --ignore-unfixed \
         --format json "$img" > "$tmp/$(tr '/:@' '___' <<<"$img").json" 2>/dev/null; then echo "ok"; else echo "${Y}no se pudo${N}"; fi
  done
  python3 - "$tmp" "$out" <<'PY'
import json, pathlib, sys, time
src, out = pathlib.Path(sys.argv[1]), sys.argv[2]
images = []
for f in sorted(src.glob("*.json")):
    try:
        d = json.loads(f.read_text() or "{}")
    except ValueError:
        continue
    vulns = [v for r in d.get("Results") or [] for v in r.get("Vulnerabilities") or []]
    crit = sorted({v["VulnerabilityID"] for v in vulns if v.get("Severity") == "CRITICAL"})
    high = sorted({v["VulnerabilityID"] for v in vulns if v.get("Severity") == "HIGH"})
    pkgs = sorted({f'{v.get("PkgName")} → {v.get("FixedVersion")}' for v in vulns})[:15]
    images.append({"image": d.get("ArtifactName") or f.stem, "critical": len(crit), "high": len(high),
                   "ids": (crit + high)[:20], "fixes": pkgs})
images.sort(key=lambda i: (-i["critical"], -i["high"], i["image"]))
json.dump({"ts": time.time(), "images": images}, open(out, "w"), indent=1)
bad = [i for i in images if i["critical"] or i["high"]]
print()
for i in images:
    mark = "✅" if not (i["critical"] or i["high"]) else ("🔴" if i["critical"] else "🟠")
    print(f"  {mark} {i['image']}: {i['critical']} críticas, {i['high']} altas")
PY
  rm -rf "$tmp"
  # resumen por Telegram (sin sonido si todo está bien)
  local tok; tok="$(grep -E '^INTERNAL_TOKEN=' .env | cut -d= -f2-)"
  if [[ -n $tok ]]; then
    python3 - "$out" <<'PY' | curl -s -m 10 -X POST http://127.0.0.1:8080/notify -H "Authorization: Bearer $tok" \
        -H "Content-Type: application/json" --data-binary @- >/dev/null || true
import json, sys
d = json.load(open(sys.argv[1]))
bad = [i for i in d["images"] if i["critical"] or i["high"]]
if not bad:
    text = f"🛡 Imágenes revisadas ({len(d['images'])}): sin vulnerabilidades altas o críticas con arreglo pendiente."
else:
    lines = [f"{'🔴' if i['critical'] else '🟠'} {i['image']}: {i['critical']} críticas, {i['high']} altas" for i in bad[:12]]
    text = ("🛡 <b>Vulnerabilidades con arreglo disponible</b>\n" + "\n".join(lines) +
            "\n\nSe quitan actualizando: ./hl update --all (las que siguen es que el proyecto aún no saca versión nueva).")
print(json.dumps({"text": text, "silent": not bad}))
PY
  fi
}

if ((ONLY_SCAN)); then scan; exit 0; fi

step "Código del homelab (git pull)"
owner="$(stat -c %U .)"
if [[ "$(id -un)" == "$owner" ]]; then git pull --ff-only; else sudo -u "$owner" git pull --ff-only; fi

step "Imágenes nuevas de la Pi"
"${C[@]}" pull --ignore-buildable 2>/dev/null || "${C[@]}" pull --ignore-pull-failures 2>&1 | grep -v "^ *$" | tail -5 || true
step "Reconstruyendo panel y bot desde cero (Python y paquetes al día)"
"${C[@]}" build --pull --no-cache netwatch bot
"${C[@]}" up -d --remove-orphans

if [[ -f hosts/home/.env ]]; then
  step "Herramientas de la casa"
  "${H[@]}" pull --ignore-pull-failures -q || true
  "${H[@]}" up -d
fi
if [[ -f hosts/server/immich/docker-compose.yml && -f hosts/server/immich/.env ]]; then
  step "Immich"
  "${I[@]}" pull -q || true
  "${I[@]}" up -d
fi

step "Limpiando imágenes viejas"
"${D[@]}" image prune -f | tail -1

((SCAN)) && scan
echo
echo "${G}✅ Todo actualizado.${N} Recarga el panel. Sistema operativo: sudo apt update && sudo apt full-upgrade"
