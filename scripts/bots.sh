#!/usr/bin/env bash
# Tus otros bots de Telegram (ej. yt-telegrambot) desde el homelab: el panel → Bots los
# muestra, los reinicia y administra sus usuarios; aquí está lo mismo en la terminal.
#
#   sudo ./hl bots add yt ~/yt-telegrambot        registra un bot (detecta tipo y servicio)
#        --unit yt-telegrambot.service             servicio de systemd, si no lo adivina
#        --kind generic                            sin usuarios: solo estado, reinicio y logs
#   ./hl bots list                                  los registrados y si están corriendo
#   sudo ./hl bots start|stop|restart NAME
#   sudo ./hl bots logs NAME [-f]
#   sudo ./hl bots cli NAME ARGS…                  la herramienta del bot, ej. para yt-telegrambot:
#        ./hl bots cli yt grant 123456789 premium --days 30   (también list, info, revoke, stats…)
#   sudo ./hl bots remove NAME                     deja de administrarlo (no toca el bot)
#
# Cómo lo ve el panel: la carpeta del bot se monta (bind, en /etc/fstab) dentro de
# /srv/homelab-bots/NAME, y esa carpeta, en el contenedor del panel. Solo eso: nada más de tu
# home. El bot sigue corriendo igual que siempre, con su propio servicio de systemd.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."
REPO="$PWD"
CONF=config/bots.yml
MNT=/srv/homelab-bots
RUN=run/bots
B=$'\e[1m'; G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; N=$'\e[0m'
cmd="${1:-list}"; shift || true

need_root() { [[ $EUID -eq 0 ]] || exec sudo "$0" "$cmd" "$@"; }
valid() { [[ ${1:-} =~ ^[a-z0-9][a-z0-9_-]{0,30}$ ]] || { echo "${R}Nombre inválido:${N} usa minúsculas, números, - o _"; exit 1; }; }

# config/bots.yml → «nombre<TAB>tipo<TAB>servicio<TAB>carpeta» por bot
rows() {
  [[ -f $CONF ]] || return 0
  python3 - "$CONF" <<'PY'
import sys, yaml
for b in (yaml.safe_load(open(sys.argv[1])) or {}).get("bots") or []:
    print("\t".join(str(b.get(k) or "") for k in ("name", "kind", "unit", "path")))
PY
}
row() { rows | awk -F'\t' -v n="$1" '$1 == n' | head -1; }
get() { local r; r="$(row "$1")"; [[ -n $r ]] || { echo "${R}No hay un bot «$1».${N} Míralos con: ./hl bots list"; exit 1; }
        IFS=$'\t' read -r NAME KIND UNIT DIR <<<"$r"; }

save() {   # save add|remove NOMBRE [tipo servicio carpeta]
  python3 - "$CONF" "$@" <<'PY'
import os, sys, yaml
path, op, name, *rest = sys.argv[1:]
data = (yaml.safe_load(open(path)) if os.path.exists(path) else None) or {}
bots = [b for b in data.get("bots") or [] if b.get("name") != name]
if op == "add":
    kind, unit, folder = rest
    bots.append({"name": name, "kind": kind, "unit": unit, "path": folder})
data["bots"] = bots
tmp = path + ".tmp"
with open(tmp, "w") as f:
    f.write("# Lo escribe ./hl bots add/remove. Panel → Bots.\n")
    yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
os.replace(tmp, path)
PY
}

recreate_panel() {
  echo "  Recargando el panel para que vea la carpeta…"
  docker compose -f hosts/pi/docker-compose.yml --env-file .env up -d --force-recreate netwatch >/dev/null 2>&1 \
    || echo "  ${Y}No pude recrear netwatch:${N} corre  ./hl update"
}

state() { [[ -n ${1:-} ]] && systemctl is-active "$1" 2>/dev/null || echo "-"; }

case "$cmd" in
  add)
    need_root "$@"
    name="${1:?Uso: ./hl bots add NOMBRE CARPETA}"; dir="${2:?Falta la carpeta del bot}"; shift 2
    valid "$name"
    kind=""; unit=""
    while [[ $# -gt 0 ]]; do
      case "$1" in --kind) kind="$2"; shift ;; --unit) unit="$2"; shift ;; *) echo "Opción desconocida: $1"; exit 1 ;; esac; shift
    done
    dir="$(readlink -f "$dir")"
    [[ -d $dir ]] || { echo "${R}No existe la carpeta $dir${N}"; exit 1; }
    case "$dir" in /|/home|/root|/etc|/var|/usr|"$HOME") echo "${R}Esa carpeta es demasiado amplia:${N} pon la del bot."; exit 1 ;; esac
    if [[ -z $kind ]]; then
      if [[ -f $dir/database/databases.py && -f $dir/database/dbcli.py && -f $dir/main.py ]]; then kind=yt-telegrambot; else kind=generic; fi
    fi
    [[ $kind =~ ^(yt-telegrambot|generic)$ ]] || { echo "Tipo desconocido: $kind (yt-telegrambot o generic)"; exit 1; }
    if [[ -z $unit ]]; then   # el servicio cuyo WorkingDirectory es esa carpeta
      for u in $(systemctl list-units --type=service --all --no-legend --plain 2>/dev/null | awk '{print $1}'); do
        [[ "$(systemctl show -p WorkingDirectory --value "$u" 2>/dev/null)" == "$dir" ]] && { unit="$u"; break; }
      done
      [[ -z $unit && $kind == yt-telegrambot ]] && systemctl cat yt-telegrambot.service >/dev/null 2>&1 && unit=yt-telegrambot.service
    fi
    [[ -n $unit ]] || echo "  ${Y}No encontré su servicio de systemd:${N} sin él no podré reiniciarlo (agrégalo con --unit)."
    [[ -z $unit || $unit =~ ^[A-Za-z0-9@._-]+$ ]] || { echo "Servicio inválido: $unit"; exit 1; }
    [[ -f $dir/.env ]] || echo "  ${Y}No hay $dir/.env:${N} sin el TOKEN no podré ver su nombre ni mandar mensajes."
    mkdir -p "$MNT/$name"
    if ! grep -q "# homelab-bot $name\$" /etc/fstab; then
      printf '%s %s none bind,nofail 0 0 # homelab-bot %s\n' "${dir// /\\040}" "$MNT/$name" "$name" >> /etc/fstab
    fi
    mountpoint -q "$MNT/$name" || mount --bind "$dir" "$MNT/$name"
    save add "$name" "$kind" "$unit" "$dir"
    chown "$(stat -c %U .)": "$CONF"
    echo "${G}✔${N} Bot ${B}$name${N} ($kind) · servicio: ${unit:-ninguno} · $dir"
    recreate_panel
    echo "  Ábrelo en el panel → ${B}Bots${N}." ;;

  remove|rm)
    need_root "$@"
    get "${1:?Uso: ./hl bots remove NOMBRE}"
    mountpoint -q "$MNT/$NAME" && umount "$MNT/$NAME"
    sed -i "\|# homelab-bot $NAME\$|d" /etc/fstab
    rmdir "$MNT/$NAME" 2>/dev/null || true
    save remove "$NAME"
    rm -f "$RUN/$NAME.log"
    echo "${G}✔${N} Ya no administro «$NAME». El bot sigue igual (no lo apagué)."
    recreate_panel ;;

  list|ls)
    [[ -n "$(rows)" ]] || { echo "No hay bots registrados. Agrega uno:  sudo ./hl bots add yt ~/yt-telegrambot"; exit 0; }
    printf "${B}%-14s %-15s %-28s %-9s %s${N}\n" NOMBRE TIPO SERVICIO ESTADO CARPETA
    while IFS=$'\t' read -r n k u d; do
      s="$(state "$u")"; c=$Y; [[ $s == active ]] && c=$G; [[ $s == failed ]] && c=$R
      printf "%-14s %-15s %-28s ${c}%-9s${N} %s\n" "$n" "$k" "${u:--}" "$s" "$d"
    done < <(rows) ;;

  start|stop|restart)
    need_root "$@"
    get "${1:?Uso: ./hl bots $cmd NOMBRE}"
    [[ -n $UNIT ]] || { echo "«$NAME» no tiene servicio de systemd (./hl bots add $NAME $DIR --unit …)"; exit 1; }
    systemctl "$cmd" "$UNIT"
    echo "  $UNIT: $(state "$UNIT")" ;;

  logs)
    need_root "$@"
    get "${1:?Uso: ./hl bots logs NOMBRE [-f]}"; shift
    [[ -n $UNIT ]] || { echo "«$NAME» no tiene servicio de systemd."; exit 1; }
    exec journalctl -u "$UNIT" --no-pager -n 200 -o short-iso "$@" ;;

  cli)
    need_root "$@"
    get "${1:?Uso: ./hl bots cli NOMBRE ARGS…}"; shift
    [[ $KIND == yt-telegrambot ]] || { echo "«$NAME» no tiene herramienta de administración."; exit 1; }
    py="$DIR/.venv/bin/python3"; [[ -x $py ]] || py=python3
    cd "$DIR"
    exec sudo -u "$(stat -c %U "$DIR")" "$py" database/dbcli.py "$@" ;;

  # ---- para el panel (lo llama homelab-apply.sh como root) ----
  status-json)
    mkdir -p "$RUN"
    rows | python3 -c '
import json, subprocess, sys, time
out = {}
for line in sys.stdin:
    name, _kind, unit, _path = line.rstrip("\n").split("\t")
    if not unit:
        out[name] = {"state": "none"}
        continue
    props = ("ActiveState", "SubState", "ActiveEnterTimestampMonotonic", "NRestarts", "MemoryCurrent")
    r = subprocess.run(["systemctl", "show", unit] + [f"-p{p}" for p in props], capture_output=True, text=True)
    v = dict(l.split("=", 1) for l in r.stdout.splitlines() if "=" in l)
    num = lambda k: int(v[k]) if v.get(k, "").isdigit() else 0
    mono = num("ActiveEnterTimestampMonotonic") / 1e6
    up = time.clock_gettime(time.CLOCK_MONOTONIC)
    out[name] = {"state": v.get("ActiveState", "unknown"), "sub": v.get("SubState", ""),
                 "since": round(time.time() - (up - mono)) if mono else 0,
                 "restarts": num("NRestarts"), "memory": num("MemoryCurrent")}
json.dump({"ts": time.time(), "bots": out}, sys.stdout)
' > "$RUN/status.json.tmp"
    mv "$RUN/status.json.tmp" "$RUN/status.json"
    echo "   estado de $(rows | wc -l) bot(s) actualizado" ;;

  logs-file)
    get "${1:?}"; mkdir -p "$RUN"
    if [[ -n $UNIT ]]; then journalctl -u "$UNIT" --no-pager -n 300 -o short-iso > "$RUN/$NAME.log.tmp" 2>&1 || true
    else echo "Sin servicio de systemd." > "$RUN/$NAME.log.tmp"; fi
    # el token nunca sale del servidor, aunque el bot lo haya escrito en sus logs
    sed -Ei 's/[0-9]{6,12}:[A-Za-z0-9_-]{30,}/[TOKEN]/g' "$RUN/$NAME.log.tmp"
    mv "$RUN/$NAME.log.tmp" "$RUN/$NAME.log"
    chmod 640 "$RUN/$NAME.log"
    echo "   $(wc -l < "$RUN/$NAME.log") líneas" ;;

  -h|--help|help) sed -n 2,17p "$0" ;;
  *) echo "Comando desconocido: $cmd"; sed -n 2,17p "$0"; exit 1 ;;
esac
