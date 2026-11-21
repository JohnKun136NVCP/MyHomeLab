#!/usr/bin/env bash
# Salud de los discos (SMART): avisa ANTES de que se muera el disco de la Pi o de la PC.
#
#   sudo ./hl disks              revisa ahora y muestra el resultado
#   sudo ./hl disks --install    instala smartmontools y lo revisa solo cada día (07:10)
#   sudo ./hl disks --remove     quita la revisión diaria
#
# Lo que importa: sectores reasignados/pendientes, errores, temperatura, desgaste de SSD/NVMe
# y el «PASSED/FAILED» del propio disco. También cuánto espacio queda. Si algo está mal te llega
# por Telegram y queda en el panel (Sistema).
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."
REPO="$PWD"
G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; B=$'\e[1m'; N=$'\e[0m'
cmd="${1:-check}"

case "$cmd" in
  --install)
    [[ $EUID -eq 0 ]] || exec sudo "$0" --install
    command -v smartctl >/dev/null || { apt-get update -qq && apt-get install -y -qq smartmontools; }
    cat > /etc/systemd/system/homelab-disks.service <<UNIT
[Unit]
Description=Homelab: salud de los discos (SMART)
[Service]
Type=oneshot
ExecStart=$REPO/scripts/disks.sh --quiet
UNIT
    cat > /etc/systemd/system/homelab-disks.timer <<'UNIT'
[Unit]
Description=Homelab: revisar los discos cada día
[Timer]
OnCalendar=*-*-* 07:10
Persistent=true
[Install]
WantedBy=timers.target
UNIT
    systemctl daemon-reload
    systemctl enable --now homelab-disks.timer
    echo "${G}✔${N} Revisión diaria de discos activada."
    exec "$0" check ;;
  --remove)
    [[ $EUID -eq 0 ]] || exec sudo "$0" --remove
    systemctl disable --now homelab-disks.timer 2>/dev/null || true
    rm -f /etc/systemd/system/homelab-disks.{service,timer}; systemctl daemon-reload
    echo "Revisión diaria quitada." ; exit 0 ;;
esac

[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"
command -v smartctl >/dev/null || { echo "${Y}Falta smartmontools:${N} sudo ./hl disks --install"; exit 1; }
mkdir -p run
QUIET=0; [[ $cmd == --quiet ]] && QUIET=1

python3 - "$QUIET" <<'PY'
import json, os, shutil, subprocess, sys, time

quiet = sys.argv[1] == "1"

def sh(*cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.stdout

devs = []
for line in sh("smartctl", "--scan-open").splitlines():
    if not line.strip() or line.startswith("#"):
        continue
    parts = line.split("#")[0].split()
    if parts:
        devs.append(parts)                       # ej. ['/dev/sda', '-d', 'sat']

disks, problems = [], []
for d in devs:
    try:
        j = json.loads(sh("smartctl", "-j", "-a", *d) or "{}")
    except ValueError:
        continue
    name = j.get("model_name") or j.get("model_family") or d[0]
    passed = (j.get("smart_status") or {}).get("passed")
    attrs = {a.get("name"): (a.get("raw") or {}).get("value", 0)
             for a in (j.get("ata_smart_attributes") or {}).get("table", [])}
    nv = j.get("nvme_smart_health_information_log") or {}
    temp = (j.get("temperature") or {}).get("current")
    realloc = attrs.get("Reallocated_Sector_Ct", 0)
    pending = attrs.get("Current_Pending_Sector", 0)
    uncorr = attrs.get("Offline_Uncorrectable", 0) + attrs.get("Reported_Uncorrect", 0)
    wear = nv.get("percentage_used")
    if wear is None:
        for k in ("Wear_Leveling_Count", "Percent_Lifetime_Remain", "SSD_Life_Left"):
            if k in attrs:
                wear = None if k == "Wear_Leveling_Count" else max(0, 100 - int(attrs[k]))
    hours = (j.get("power_on_time") or {}).get("hours")
    issues = []
    if passed is False:
        issues.append("el propio disco dice FAILED: cámbialo ya")
    if realloc:
        issues.append(f"{realloc} sectores reasignados")
    if pending:
        issues.append(f"{pending} sectores pendientes")
    if uncorr:
        issues.append(f"{uncorr} errores que no se pudieron corregir")
    if nv.get("media_errors"):
        issues.append(f"{nv['media_errors']} errores de medio (NVMe)")
    if temp and temp >= 60:
        issues.append(f"caliente: {temp} °C")
    if wear is not None and wear >= 80:
        issues.append(f"desgaste {wear} %")
    smart_off = not j.get("smart_status") and not attrs and not nv
    disks.append({"dev": d[0], "model": name, "passed": passed, "temp": temp, "hours": hours, "wear": wear,
                  "realloc": realloc, "pending": pending, "issues": issues,
                  "note": "este adaptador USB no deja leer SMART" if smart_off else ""})
    if issues:
        problems.append(f"💽 {name} ({d[0]}): " + ", ".join(issues))

space = []
for mnt in ("/", "/srv", "/mnt", "/media"):
    if os.path.ismount(mnt) or mnt == "/":
        u = shutil.disk_usage(mnt)
        pct = round(100 * u.used / u.total)
        space.append({"mount": mnt, "pct": pct, "free_gb": round(u.free / 2**30, 1)})
        if pct >= 90:
            problems.append(f"💾 {mnt} está al {pct} % (quedan {round(u.free / 2**30, 1)} GB)")

out = {"ts": time.time(), "disks": disks, "space": space, "problems": problems}
json.dump(out, open("run/disks.json", "w"), indent=1)

if not quiet:
    for d in disks:
        ok = "✅" if not d["issues"] else "⚠️"
        extra = f"{d['temp']} °C" if d["temp"] else ""
        if d["hours"]:
            extra += f" · {d['hours']} h encendido"
        if d["wear"] is not None:
            extra += f" · desgaste {d['wear']} %"
        print(f"  {ok} {d['model']} ({d['dev']}) {extra}  {d['note']}")
        for i in d["issues"]:
            print(f"       → {i}")
    for s in space:
        print(f"  {'✅' if s['pct'] < 90 else '⚠️'} {s['mount']}: {s['pct']} % usado, {s['free_gb']} GB libres")
    if not disks:
        print("  No encontré discos con SMART (¿adaptador USB sin soporte?).")
PY

# aviso por Telegram solo si hay problemas
probs="$(python3 -c 'import json; print("\n".join(json.load(open("run/disks.json"))["problems"]))')"
tok="$(grep -E '^INTERNAL_TOKEN=' .env 2>/dev/null | cut -d= -f2- || true)"
if [[ -n $probs && -n $tok ]]; then
  python3 -c 'import json,sys; print(json.dumps({"text": "💽 <b>Discos</b>\n" + sys.stdin.read() + "\n\nRespalda lo importante y revisa: sudo ./hl disks"}))' <<<"$probs" \
    | curl -s -m 10 -X POST http://127.0.0.1:8080/notify -H "Authorization: Bearer $tok" \
        -H "Content-Type: application/json" --data-binary @- >/dev/null || true
fi
chown "$(stat -c %U "$REPO")": run/disks.json 2>/dev/null || true
