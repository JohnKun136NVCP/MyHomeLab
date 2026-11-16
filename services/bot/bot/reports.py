"""Textos de estado y reporte diario (Prometheus + netwatch + Frigate + respaldos)."""
from __future__ import annotations

import os
import time

from homelab_common import http_json, human_bps, human_bytes, prom_query

from .tg import esc

IFACE = 'device!~"lo|veth.*|docker.*|br-.*|wg.*"'


def _prom() -> str:
    return os.environ.get("PROMETHEUS_URL", "http://prometheus:9090/prometheus")


def _netwatch(path: str):
    base = os.environ.get("NETWATCH_URL", "http://host.docker.internal:8000").rstrip("/")
    return http_json(f"{base}{path}", timeout=10)


def _val(expr: str, default=None):
    try:
        r = prom_query(_prom(), expr)
        return float(r[0]["value"][1]) if r else default
    except (RuntimeError, ValueError, KeyError, IndexError):
        return default


def _by(expr: str, label: str) -> dict[str, float]:
    try:
        return {r["metric"].get(label, "?"): float(r["value"][1]) for r in prom_query(_prom(), expr)}
    except (RuntimeError, ValueError):
        return {}


def ago(ts: float | None) -> str:
    if not ts:
        return "nunca"
    d = time.time() - ts
    if d < 3600:
        return f"hace {int(d // 60)} min"
    if d < 86400:
        return f"hace {int(d // 3600)} h"
    return f"hace {int(d // 86400)} días"


def nodes_block() -> str:
    up = _by('up{job="node"}', "instance")
    if not up:
        return "🖥 <b>Nodos</b>: Prometheus no responde\n"
    lines = ["🖥 <b>Nodos</b>"]
    cpu = _by('100 * (1 - avg by (instance) (rate(node_cpu_seconds_total{mode="idle"}[10m])))', "instance")
    mem = _by("100 * (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)", "instance")
    disk = _by('max by (instance) (100 * (1 - node_filesystem_avail_bytes{mountpoint="/"} '
               '/ node_filesystem_size_bytes{mountpoint="/"}))', "instance")
    for inst, u in sorted(up.items()):
        if u < 1:
            lines.append(f"  ❌ {esc(inst)}: no responde")
            continue
        lines.append(f"  ✅ {esc(inst)}: CPU {cpu.get(inst, 0):.0f}% · RAM {mem.get(inst, 0):.0f}% · "
                     f"disco {disk.get(inst, 0):.0f}%")
    temp = _val('max(node_thermal_zone_temp{instance="raspberry"})')
    if temp:
        lines.append(f"  🌡 Raspberry: {temp:.0f} °C")
    return "\n".join(lines) + "\n"


def vps_block() -> str:
    up = _val('up{job="node",instance="vps"}')
    if up is None:
        return "☁ <b>VPS</b>: sin datos (¿está en la VPN y en homelab.yml?)\n"
    if up < 1:
        return "☁ <b>VPS</b>: ❌ no responde\n"
    rx = _val(f'sum(increase(node_network_receive_bytes_total{{instance="vps",{IFACE}}}[24h]))', 0)
    tx = _val(f'sum(increase(node_network_transmit_bytes_total{{instance="vps",{IFACE}}}[24h]))', 0)
    now_rx = _val(f'sum(homelab:iface_rx_bps:rate10m{{instance="vps",{IFACE}}})', 0)
    now_tx = _val(f'sum(homelab:iface_tx_bps:rate10m{{instance="vps",{IFACE}}})', 0)
    cpu = _val('100 * (1 - avg(rate(node_cpu_seconds_total{instance="vps",mode="idle"}[10m])))', 0)
    mem = _val('100 * (1 - node_memory_MemAvailable_bytes{instance="vps"} / node_memory_MemTotal_bytes{instance="vps"})', 0)
    disk = _val('max(100 * (1 - node_filesystem_avail_bytes{instance="vps",mountpoint="/"} '
                '/ node_filesystem_size_bytes{instance="vps",mountpoint="/"}))', 0)
    up_s = _val('time() - node_boot_time_seconds{instance="vps"}', 0)
    return (f"☁ <b>VPS</b> ✅ (encendido {up_s / 86400:.0f} días)\n"
            f"  CPU {cpu:.0f}% · RAM {mem:.0f}% · disco {disk:.0f}%\n"
            f"  Ahora: ↓ {human_bps(now_rx)} · ↑ {human_bps(now_tx)}\n"
            f"  Últimas 24 h: ↓ {human_bytes(rx)} · ↑ {human_bytes(tx)}\n")


def home_block() -> str:
    try:
        s = _netwatch("/api/summary")
    except RuntimeError as e:
        return f"🏠 <b>Casa</b>: netwatch no responde ({esc(e)})\n"
    inet = s.get("targets", {}).get("internet", {})
    lines = [f"🏠 <b>Casa</b> · {s['devices_online']} dispositivos en línea "
             f"({s['new_today']} nuevos hoy, {s['unknown']} sin revisar)",
             f"  Internet: {'✅' if inet.get('up') else '❌'}"
             + (f" {inet['latency'] * 1000:.0f} ms" if inet.get("latency") else "")]
    for name, st in s.get("infra", {}).items():
        lines.append(f"  {'✅' if st['up'] else '❌'} {esc(name)} ({st['ip']})")
    lines.append(f"  Eventos 24 h: {s['events_24h']} ({s['critical_24h']} críticos)")
    rx = _val(f'sum(increase(node_network_receive_bytes_total{{instance="raspberry",{IFACE}}}[24h]))')
    tx = _val(f'sum(increase(node_network_transmit_bytes_total{{instance="raspberry",{IFACE}}}[24h]))')
    if rx is not None:
        lines.append(f"  Tráfico Pi 24 h: ↓ {human_bytes(rx)} · ↑ {human_bytes(tx or 0)}"
                     + ("" if s.get("bridge_mode") else " (solo la Pi; toda la casa en modo puente)"))
    top = _by("topk(5, sum by (name) (increase(homelab_device_bytes_total[24h])))", "name")
    if top:
        lines.append("  Top consumo: " + ", ".join(f"{esc(n)} {human_bytes(v)}"
                                                   for n, v in sorted(top.items(), key=lambda x: -x[1])))
    return "\n".join(lines) + "\n"


def backups_block(backups) -> str:
    lines = ["📦 <b>Respaldos</b>"]
    for b in backups.summary():
        warn = " ⚠" if not b["last"] or time.time() - b["last"] > 90 * 86400 else ""
        lines.append(f"  {esc(b['device'])} ({b['os']}): {ago(b['last'])}{warn}"
                     + (f" · {b['count']} guardados, {human_bytes(b['size'])}" if b["count"] else ""))
    return "\n".join(lines) + "\n"


def cameras_block(cfg: dict) -> str:
    url = (cfg.get("cameras", {}) or {}).get("frigate_url")
    if not url:
        return ""
    try:
        evs = http_json(f"{url.rstrip('/')}/api/events?after={time.time() - 86400:.0f}&limit=500", timeout=8)
    except RuntimeError:
        return "📷 <b>Cámaras</b>: Frigate no responde\n"
    counts: dict[str, int] = {}
    for e in evs or []:
        k = f"{e.get('camera')}: {e.get('label')}"
        counts[k] = counts.get(k, 0) + 1
    body = ", ".join(f"{esc(k)} ×{n}" for k, n in sorted(counts.items())) or "sin detecciones"
    return f"📷 <b>Cámaras (24 h)</b>: {body}\n"


def alerts_block() -> str:
    firing = _by('ALERTS{alertstate="firing"}', "alertname")
    if not firing:
        return "🔔 Sin alertas activas\n"
    return "🔔 <b>Alertas activas</b>: " + ", ".join(esc(a) for a in firing) + "\n"


def daily_report(cfg: dict, backups) -> str:
    return "\n".join([f"📋 <b>Reporte del homelab</b> · {time.strftime('%d/%m/%Y %H:%M')}\n",
                      home_block(), vps_block(), nodes_block(), alerts_block(),
                      cameras_block(cfg), backups_block(backups)])
