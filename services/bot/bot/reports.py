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
    cs = _val('sum(cs_active_decisions{instance="vps"})')
    return (f"☁ <b>VPS</b> ✅ (encendido {up_s / 86400:.0f} días)\n"
            f"  CPU {cpu:.0f}% · RAM {mem:.0f}% · disco {disk:.0f}%\n"
            f"  Ahora: ↓ {human_bps(now_rx)} · ↑ {human_bps(now_tx)}\n"
            f"  Últimas 24 h: ↓ {human_bytes(rx)} · ↑ {human_bytes(tx)}\n"
            + (f"  🛡 CrowdSec: {cs:.0f} IPs bloqueadas ahora\n" if cs is not None else ""))


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


def services_block() -> str:
    try:
        s = _netwatch("/api/services")
    except RuntimeError:
        return ""
    if not s.get("ok"):
        return f"🧩 <b>Servicios</b>: {esc(s.get('error', 'sin datos'))}\n"
    mons = s.get("monitors", [])
    if not mons:
        return ""
    down = [m for m in mons if m.get("status") == 0]
    low = [m for m in mons if m.get("status") == 1 and (m.get("uptime_24h") or 1) < 0.99]
    lines = [f"🧩 <b>Servicios</b>: {s.get('up', 0)}/{len(mons)} arriba"]
    for m in down:
        lines.append(f"  🔴 {esc(m['name'])}: {esc(m.get('msg') or 'caído')}")
    for m in low:
        lines.append(f"  🟡 {esc(m['name'])}: {m['uptime_24h'] * 100:.1f}% arriba en 24 h")
    return "\n".join(lines) + "\n"


def dns_block() -> str:
    try:
        d = _netwatch("/api/dns")
    except RuntimeError:
        return ""
    if not d.get("enabled"):
        return ""
    q, b = d.get("queries"), d.get("blocked")
    if not q:
        return ""
    return (f"🧱 <b>DNS (24 h)</b>: {q:,} consultas · {b or 0:,} bloqueadas "
            f"({(b or 0) / q * 100:.0f}%)\n").replace(",", " ")


def new_devices_block() -> str:
    try:
        devs = _netwatch("/api/devices")
    except RuntimeError:
        return ""
    devs = devs.get("devices", devs) if isinstance(devs, dict) else devs
    day = time.time() - 86400
    new = [d for d in devs or [] if (d.get("first_seen") or 0) >= day]
    if not new:
        return ""
    names = ", ".join(esc(d.get("display_name") or d.get("hostname") or d.get("vendor") or d.get("ip") or "?")
                      for d in new[:10])
    return f"🆕 <b>Nuevos en la red</b>: {names}" + (f" y {len(new) - 10} más" if len(new) > 10 else "") + "\n"


def alerts_block() -> str:
    firing = _by('ALERTS{alertstate="firing"}', "alertname")
    if not firing:
        return "🔔 Sin alertas activas\n"
    return "🔔 <b>Alertas activas</b>: " + ", ".join(esc(a) for a in firing) + "\n"


def daily_report(cfg: dict, backups) -> str:
    return "\n".join([f"📋 <b>Reporte del homelab</b> · {time.strftime('%d/%m/%Y %H:%M')}\n",
                      home_block(), new_devices_block(), services_block(), dns_block(),
                      vps_block(), nodes_block(), alerts_block(),
                      cameras_block(cfg), backups_block(backups)])


# ------------------------------------------------------------------ semanal y mensual
def weekly_digest(d: dict | None = None) -> str:
    """Resumen de seguridad de la semana (domingo)."""
    try:
        d = d if d is not None else _netwatch("/api/digest?days=7")
    except Exception as e:  # noqa: BLE001
        return f"🛡 <b>Resumen semanal</b>\nNo pude leer el panel: {esc(str(e))}"
    lines = ["🛡 <b>Resumen de la semana</b>"]
    o = d.get("outages") or {}
    ok = []
    lines.append(f"🌐 Internet: {o.get('count', 0)} caída(s), {o.get('minutes', 0)} min · "
                 f"{o.get('uptime_pct', 100)} % arriba" if o else "🌐 Internet: sin datos")
    nd = d.get("new_devices") or []
    lines.append(f"🆕 Equipos nuevos: {len(nd)}" + (": " + ", ".join(esc(x.split(': ', 1)[-1]) for x in nd[:6]) if nd else ""))
    if d.get("honeypot"):
        lines.append(f"🍯 Alguien tocó la trampa {d['honeypot']} vez(es): revisa Eventos")
    else:
        ok.append("trampa sin visitas")
    if d.get("arp"):
        lines.append(f"🕵️ Avisos de ARP: {d['arp']}")
    for r in d.get("rogue") or []:
        lines.append(f"🛰 {esc(r)}")
    if d.get("dns_danger"):
        lines.append(f"☠️ Sitios peligrosos bloqueados: {d['dns_danger']}")
    for v in d.get("vulnerable") or []:
        lines.append(f"🧬 {esc(v)}")
    iv = d.get("images_vuln") or []
    if iv:
        lines.append("🐳 Imágenes con vulnerabilidades con arreglo: " + ", ".join(
            f"{esc(i['image'].split('/')[-1])} ({i['critical']}C/{i['high']}A)" for i in iv[:5]) + " → ./hl update --all")
    for c in d.get("certs") or []:
        lines.append(f"🔏 Certificado de {esc(c['target'])}: vence en {int(c['days'])} días")
    for p in (d.get("disks") or {}).get("problems") or []:
        lines.append(esc(p))
    if d.get("syslog_alerts"):
        lines.append(f"📜 Avisos de los registros de tus equipos: {d['syslog_alerts']}")
    lines.append(f"🚨 Eventos críticos: {d.get('critical', 0)}")
    if ok and len(lines) <= 5:
        lines.append("✅ Semana tranquila.")
    return "\n".join(lines)


def monthly_internet(j: dict | None = None) -> str:
    """El 1 de cada mes: las caídas del internet del mes, para reclamarle al proveedor."""
    try:
        j = j if j is not None else _netwatch("/api/internet?days=31")
    except Exception as e:  # noqa: BLE001
        return f"🌐 No pude leer las caídas del internet: {esc(str(e))}"
    m = j.get("month") or {}
    outs = [o for o in j.get("outages") or [] if o.get("end")]
    lines = [f"🌐 <b>Internet del último mes</b>",
             f"Caídas: {m.get('count', 0)} · total {m.get('minutes', 0)} min · la más larga {m.get('longest_min', 0)} min",
             f"Disponibilidad: {m.get('uptime_pct', 100)} %"]
    for o in sorted(outs, key=lambda o: o["start"] - o["end"])[:5]:
        lines.append(f"• {time.strftime('%d/%m %H:%M', time.localtime(o['start']))} — {round((o['end'] - o['start']) / 60)} min")
    if outs:
        lines.append("La lista completa (para el proveedor): panel → Internet → Exportar CSV.")
    return "\n".join(lines)
