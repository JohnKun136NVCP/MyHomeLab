"""Formato de alertas (Alertmanager) y eventos (netwatch) para Telegram."""
from __future__ import annotations

import datetime as dt

from .tg import esc

LEVEL = {"critical": "🔴", "warning": "🟠", "info": "🔵"}
KIND = {"new_device": "🆕", "arp_spoof": "🕵", "ports_changed": "🚪", "ids": "🚨",
        "blocked_seen": "⛔", "quarantine": "🧪", "acl": "🔒", "honeypot": "🍯", "dns_danger": "☣",
        "roam_failed": "📶", "login_failed": "🔑", "login_new_ip": "👤", "camera_found": "📷"}


def alertmanager(payload: dict) -> str:
    lines = []
    for a in payload.get("alerts", []):
        lab, ann = a.get("labels", {}), a.get("annotations", {})
        resolved = a.get("status") == "resolved"
        icon = "✅" if resolved else LEVEL.get(lab.get("severity", "warning"), "🟠")
        title = ann.get("summary") or lab.get("alertname", "Alerta")
        lines.append(f"{icon} <b>{'Resuelto: ' if resolved else ''}{esc(title)}</b>")
        if ann.get("description") and not resolved:
            lines.append(f"   {esc(ann['description'])}")
        meta = " · ".join(esc(v) for k, v in lab.items()
                          if k in ("instance", "device", "name", "mac") and v)
        if meta:
            lines.append(f"   <i>{meta}</i>")
    return "\n".join(lines)


def diun(body: dict) -> str:
    """Webhook de Diun: hay una versión nueva de una imagen que usas."""
    img = str(body.get("image") or "?")
    status = body.get("status")
    what = "versión nueva" if status == "update" else "imagen nueva" if status == "new" else str(status or "cambio")
    meta = body.get("metadata") or {}
    where = meta.get("ctn_names") or meta.get("ctn_name") or ""
    link = body.get("hub_link") or ""
    return (f"🆕 Hay {what} de <code>{esc(img)}</code>"
            + (f" (lo usa {esc(where)})" if where else "")
            + ".\nCuando quieras: <code>./hl pull && ./hl up -d</code>"
            + (f"\n{esc(link)}" if link else ""))


def uptime_kuma(body: dict) -> str:
    """Webhook de Uptime Kuma (tipo «Webhook», cuerpo application/json)."""
    hb = body.get("heartbeat") or {}
    mon = body.get("monitor") or {}
    status = hb.get("status")
    icon = {0: "🔴", 1: "✅", 2: "🟡", 3: "🔧"}.get(status, "📈")
    if not hb and not mon:                       # botón «Probar» de Uptime Kuma
        return f"📈 <b>Uptime Kuma</b>: {esc(body.get('msg') or 'prueba')}"
    name = mon.get("name") or "monitor"
    target = mon.get("url") or mon.get("hostname") or ""
    state = {0: "CAÍDO", 1: "arriba de nuevo", 2: "pendiente", 3: "en mantenimiento"}.get(status, "")
    lines = [f"{icon} <b>{esc(name)}</b> {state}"]
    if target and target != "https://":
        lines.append(f"   <code>{esc(target)}</code>")
    if hb.get("msg"):
        lines.append(f"   {esc(hb['msg'])}")
    if hb.get("ping") and status == 1:
        lines.append(f"   respuesta en {hb['ping']} ms")
    return "\n".join(lines)


def netwatch_events(events: list[dict]) -> str:
    if not events:
        return ""
    lines = [f"🛡 <b>Red de casa</b> · {len(events)} evento(s)"]
    for e in events[:15]:
        t = dt.datetime.fromtimestamp(e["ts"]).strftime("%H:%M")
        lines.append(f"{LEVEL.get(e['level'], '•')}{KIND.get(e['kind'], '')} <b>{esc(e['title'])}</b> "
                     f"<i>{t}</i>\n   {esc(e['detail'])}")
    if len(events) > 15:
        lines.append(f"… y {len(events) - 15} más (ver la web → Eventos)")
    return "\n".join(lines)
