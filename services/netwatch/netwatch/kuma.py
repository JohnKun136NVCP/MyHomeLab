"""Estado de servicios desde la página de estado pública de Uptime Kuma (sin API key).

En Uptime Kuma: Páginas de estado → Nueva página → slug «homelab» → agrega tus monitores.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

STATUS = {0: "caído", 1: "arriba", 2: "pendiente", 3: "mantenimiento"}


def _get(url: str, timeout: float = 5):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/json"}),
                                timeout=timeout) as r:
        return json.loads(r.read().decode())


def parse(page: dict, beats: dict) -> list[dict]:
    hb = beats.get("heartbeatList") or {}
    up = beats.get("uptimeList") or {}
    out = []
    for g in page.get("publicGroupList") or []:
        for m in g.get("monitorList") or []:
            mid = str(m.get("id"))
            hist = hb.get(mid) or []
            last = hist[-1] if hist else {}
            st = last.get("status")
            out.append({
                "id": m.get("id"), "name": m.get("name", "?"), "group": g.get("name", ""),
                "type": m.get("type", ""), "status": st, "status_name": STATUS.get(st, "sin datos"),
                "msg": last.get("msg", ""), "ping": last.get("ping"), "time": last.get("time", ""),
                "uptime_24h": up.get(f"{mid}_24"),
                "history": [h.get("status") for h in hist[-30:]],
            })
    return out


def status(base: str, slug: str) -> dict:
    base = (base or "http://127.0.0.1:3001").rstrip("/")
    if not slug:
        return {"ok": False, "error": "Falta el slug de la página de estado (Ajustes → Avisos)", "monitors": []}
    try:
        page = _get(f"{base}/api/status-page/{slug}")
        beats = _get(f"{base}/api/status-page/heartbeat/{slug}")
    except urllib.error.HTTPError as e:
        msg = (f"No existe la página de estado «{slug}» en Uptime Kuma: créala (Páginas de estado → Nueva)"
               if e.code == 404 else f"Uptime Kuma respondió {e.code}")
        return {"ok": False, "error": msg, "monitors": []}
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {"ok": False, "error": f"Uptime Kuma no responde en {base} ({e})", "monitors": []}
    mons = parse(page, beats)
    return {"ok": True, "monitors": mons, "url": base, "slug": slug,
            "down": sum(1 for m in mons if m["status"] == 0),
            "up": sum(1 for m in mons if m["status"] == 1)}
