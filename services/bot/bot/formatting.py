"""Formato de alertas (Alertmanager) y eventos (netwatch) para Telegram."""
from __future__ import annotations

import datetime as dt

from .tg import esc

LEVEL = {"critical": "🔴", "warning": "🟠", "info": "🔵"}
KIND = {"new_device": "🆕", "arp_spoof": "🕵", "ports_changed": "🚪", "ids": "🚨",
        "blocked_seen": "⛔", "quarantine": "🧪", "acl": "🔒", "honeypot": "🍯", "dns_danger": "☣",
        "roam_failed": "📶", "login_failed": "🔑"}


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
