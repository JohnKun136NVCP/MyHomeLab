"""ACL y contadores por dispositivo con nftables (solo en modo puente).

En modo puente la Pi está entre el módem y tus AP, así que TODO lo que sale a
internet cruza su bridge. Una tabla `bridge homelab` hace dos cosas:
  * cuenta los bytes de subida/bajada de cada MAC (tráfico por dispositivo);
  * descarta el tráfico de las MAC bloqueadas (siempre o en un horario).
Fuera del modo puente las reglas se guardan igual y netwatch avisa si un
dispositivo bloqueado aparece en la red, pero no puede cortarlo.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
import shutil
import subprocess

log = logging.getLogger("netwatch.acl")
TABLE = "homelab"
MAC_RE = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")


def counter_name(mac: str, direction: str) -> str:
    return f"{direction}_{mac.replace(':', '')}"


def _in_window(start: str | None, end: str | None, now: dt.time) -> bool:
    if not start or not end:
        return True  # sin horario = siempre
    s = dt.time.fromisoformat(start)
    e = dt.time.fromisoformat(end)
    return s <= now < e if s <= e else (now >= s or now < e)  # cruza medianoche


def active_blocks(rules: list[dict], now: dt.time | None = None) -> set[str]:
    now = now or dt.datetime.now().time()
    return {r["mac"].lower() for r in rules
            if MAC_RE.match(r["mac"].lower()) and _in_window(r.get("start"), r.get("end"), now)}


def build_ruleset(macs: list[str], blocked: set[str]) -> str:
    """Texto para `nft -f -`. Reemplaza la tabla completa de forma atómica."""
    macs = sorted({m.lower() for m in macs if MAC_RE.match(m.lower())})
    lines = [f"table bridge {TABLE}", f"delete table bridge {TABLE}", f"table bridge {TABLE} {{"]
    for m in macs:
        lines.append(f"  counter {counter_name(m, 'up')} {{ }}")
        lines.append(f"  counter {counter_name(m, 'down')} {{ }}")
    elems = ", ".join(sorted(blocked))
    lines.append("  set blocked { type ether_addr;" + (f" elements = {{ {elems} }};" if elems else "") + " }")
    lines.append("  chain forward {")
    lines.append("    type filter hook forward priority 0; policy accept;")
    lines.append("    ether saddr @blocked counter drop")
    lines.append("    ether daddr @blocked counter drop")
    for m in macs:
        lines.append(f'    ether saddr {m} counter name "{counter_name(m, "up")}"')
        lines.append(f'    ether daddr {m} counter name "{counter_name(m, "down")}"')
    lines.append("  }")
    lines.append("}")
    return "\n".join(lines) + "\n"


def apply(ruleset: str) -> tuple[bool, str]:
    if not shutil.which("nft"):
        return False, "nft no está instalado"
    p = subprocess.run(["nft", "-f", "-"], input=ruleset, capture_output=True, text=True)
    return p.returncode == 0, p.stderr.strip()


def read_counters() -> dict[str, dict[str, int]]:
    """{mac: {"up": bytes, "down": bytes}} desde nftables."""
    if not shutil.which("nft"):
        return {}
    p = subprocess.run(["nft", "-j", "list", "counters", "table", "bridge", TABLE],
                       capture_output=True, text=True)
    if p.returncode != 0:
        return {}
    return parse_counters(p.stdout)


def parse_counters(js: str) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    try:
        data = json.loads(js)
    except ValueError:
        return out
    for item in data.get("nftables", []):
        c = item.get("counter")
        if not c:
            continue
        m = re.match(r"^(up|down)_([0-9a-f]{12})$", c.get("name", ""))
        if not m:
            continue
        raw = m.group(2)
        mac = ":".join(raw[i:i + 2] for i in range(0, 12, 2))
        out.setdefault(mac, {"up": 0, "down": 0})[m.group(1)] = int(c.get("bytes", 0))
    return out


def bridge_exists(iface: str) -> bool:
    return shutil.which("ip") is not None and subprocess.run(
        ["ip", "link", "show", iface], capture_output=True).returncode == 0
