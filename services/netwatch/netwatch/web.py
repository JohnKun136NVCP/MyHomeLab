"""API y app web de netwatch (Starlette)."""
from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from homelab_common import http_json, prom_query, prom_range

from . import acl, metrics, scanner
from .classify import ICONS, TYPES
from .monitor import Monitor

STATIC = Path(__file__).resolve().parent / "static"
MAC_RE = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")
RANGES = {"1h": (3600, 30), "6h": (21600, 120), "24h": (86400, 300), "7d": (604800, 1800)}


def build_app(mon: Monitor) -> Starlette:
    db = mon.db
    cfg = mon.cfg
    prom = os.environ.get("PROMETHEUS_URL", "http://127.0.0.1:9090/prometheus")

    async def index(_):
        return FileResponse(STATIC / "index.html")

    async def health(_):
        return JSONResponse({"ok": True, "last_scan": mon.last_scan})

    async def prom_metrics(_):
        return PlainTextResponse(metrics.render(mon), media_type="text/plain; version=0.0.4")

    # ------------------------------------------------------------ resumen
    async def summary(_):
        devs = db.devices()
        online = [d for d in devs if d["online"]]
        by_type: dict[str, int] = {}
        for d in online:
            by_type[d["display_type"]] = by_type.get(d["display_type"], 0) + 1
        day = time.time() - 86400
        ev = db.events(500, day)
        return JSONResponse({
            "devices_total": len(devs), "devices_online": len(online),
            "new_today": sum(1 for d in devs if (d["first_seen"] or 0) >= day),
            "unknown": sum(1 for d in devs if not d["known"]),
            "by_type": by_type, "events_24h": len(ev),
            "critical_24h": sum(1 for e in ev if e["level"] == "critical"),
            "infra": mon.infra_status, "targets": mon.target_status,
            "last_scan": mon.last_scan, "scan_error": mon.scan_error,
            "bridge_mode": mon.bridge_mode, "interface": mon.iface,
            "acl_rules": len(db.acl()), "icons": ICONS,
            "roaming": mon.roaming, "current_network": mon.current, "home_network": mon.home,
            "hop_every": mon.hop_every, "last_hop": mon.last_hop,
            "networks": [{"name": n["name"], "cidr": n["cidr"], "ssid": n.get("ssid", ""),
                          **mon.net_status.get(n["name"], {})} for n in mon.networks],
        })

    # -------------------------------------------------------- dispositivos
    async def devices(_):
        out = []
        for d in db.devices():
            c = mon.counters.get(d["mac"], {})
            out.append({**d, "icon": ICONS.get(d["display_type"], "❔"),
                        "bytes_up": c.get("up"), "bytes_down": c.get("down")})
        return JSONResponse({"devices": out, "types": TYPES,
                             "aps": [a["name"] for a in mon.infra if a["role"] == "ap"]})

    async def device_update(request: Request):
        mac = request.path_params["mac"].lower()
        if not db.device(mac):
            return JSONResponse({"error": "no existe"}, 404)
        body = await request.json()
        fields = {k: body[k] for k in ("name", "type_manual", "ap", "known") if k in body}
        if "known" in fields:
            fields["known"] = int(bool(fields["known"]))
        db.update_device(mac, **fields)
        return JSONResponse(db.device(mac))

    async def device_delete(request: Request):
        db.delete_device(request.path_params["mac"].lower())
        return JSONResponse({"ok": True})

    async def device_rescan(request: Request):
        d = db.device(request.path_params["mac"].lower())
        if not d or not d["ip"]:
            return JSONResponse({"error": "no existe"}, 404)

        def work():
            from .classify import classify
            ports = scanner.port_scan(d["ip"])
            db.update_device(d["mac"], ports=ports, last_port_scan=time.time(),
                             type=classify(d["vendor"], d["hostname"], ports, bool(d["random_mac"])))
        threading.Thread(target=work, daemon=True).start()
        return JSONResponse({"ok": True, "msg": "Escaneando puertos… recarga en un minuto"})

    async def mark_all_known(_):
        db.x("UPDATE devices SET known=1")
        return JSONResponse({"ok": True})

    # ------------------------------------------------------------- eventos
    async def events(request: Request):
        limit = int(request.query_params.get("limit", 200))
        return JSONResponse({"events": db.events(min(limit, 1000))})

    # ----------------------------------------------------------------- ACL
    async def acl_list(_):
        rules = db.acl()
        active = acl.active_blocks(rules)
        names = {d["mac"]: d["display_name"] for d in db.devices()}
        return JSONResponse({"rules": [{**r, "name": names.get(r["mac"], ""),
                                        "active_now": r["mac"] in active} for r in rules],
                             "enforced": mon.bridge_mode})

    async def acl_add(request: Request):
        b = await request.json()
        mac = str(b.get("mac", "")).lower().strip()
        if not MAC_RE.match(mac):
            return JSONResponse({"error": "MAC inválida"}, 400)
        start, end = (b.get("start") or "").strip(), (b.get("end") or "").strip()
        if bool(start) != bool(end):
            return JSONResponse({"error": "Pon hora de inicio y de fin, o ninguna (= siempre)"}, 400)
        for k, v in (("inicio", start), ("fin", end)):
            if v:
                try:
                    import datetime as _dt
                    _dt.time.fromisoformat(v)
                    assert re.match(r"^\d{2}:\d{2}$", v)
                except (ValueError, AssertionError):
                    return JSONResponse({"error": f"Hora de {k} inválida (usa HH:MM)"}, 400)
        b["start"], b["end"] = start, end
        rid = db.add_acl(mac, b.get("start", ""), b.get("end", ""), b.get("note", ""))
        db.add_event("info", "acl", f"Regla ACL agregada: bloquear {mac}",
                     (f"de {b['start']} a {b['end']}" if b.get("start") else "siempre"), mac)
        mon._acl_hash = ""  # fuerza re-aplicar
        return JSONResponse({"id": rid})

    async def acl_delete(request: Request):
        db.delete_acl(int(request.path_params["rid"]))
        mon._acl_hash = ""
        return JSONResponse({"ok": True})

    # ------------------------------------------------------------- tráfico
    async def traffic(request: Request):
        rng = request.query_params.get("range", "24h")
        seconds, step = RANGES.get(rng, RANGES["24h"])
        which = request.query_params.get("q", "home")
        exprs = {
            "home": [("Bajada", 'sum(homelab:iface_rx_bps:rate10m{instance="raspberry"})'),
                     ("Subida", 'sum(homelab:iface_tx_bps:rate10m{instance="raspberry"})')],
            "vps": [("Bajada", 'sum(homelab:iface_rx_bps:rate10m{instance="vps"})'),
                    ("Subida", 'sum(homelab:iface_tx_bps:rate10m{instance="vps"})')],
            "devices": [("{name}", "topk(8, sum by (name) (homelab:device_bps:rate10m))")],
            "latency": [("{name}", "homelab_target_latency_seconds")],
        }.get(which)
        if exprs is None:
            return JSONResponse({"error": "q inválido"}, 400)
        series = []
        try:
            for label, e in exprs:
                for r in prom_range(prom, e, seconds, step):
                    name = label.format(**r.get("metric", {})) if "{" in label else label
                    series.append({"name": name, "points": [[float(t), float(v)] for t, v in r["values"]]})
        except RuntimeError as e:
            return JSONResponse({"series": [], "error": str(e)})
        return JSONResponse({"series": series, "unit": "s" if which == "latency" else "bps"})

    async def totals(_):
        """Bytes del día por dispositivo (modo puente) e interfaces."""
        try:
            per_dev = prom_query(prom, "sum by (name, direction) (increase(homelab_device_bytes_total[24h]))")
            sel = '{instance="vps",device!~"lo|veth.*|docker.*|br-.*"}'
            vps = [{"direction": "bajada", "value": r["value"]} for r in
                   prom_query(prom, f"sum(increase(node_network_receive_bytes_total{sel}[24h]))")]
            vps += [{"direction": "subida", "value": r["value"]} for r in
                    prom_query(prom, f"sum(increase(node_network_transmit_bytes_total{sel}[24h]))")]
        except RuntimeError as e:
            return JSONResponse({"error": str(e), "devices": [], "vps": []})
        return JSONResponse({
            "devices": [{**r["metric"], "bytes": float(r["value"][1])} for r in per_dev],
            "vps": [{"direction": r["direction"], "bytes": float(r["value"][1])} for r in vps]})

    # ----------------------------------------------------- AP y topología
    def ap_of(d: dict, aps: list[dict]) -> str | None:
        """AP de un dispositivo: el que le asignaste, o el router de su red si no es la de casa."""
        names = {a["name"] for a in aps}
        if d.get("ap") in names:
            return d["ap"]
        net = d.get("network") or mon.home
        if net != mon.home:
            return next((a["name"] for a in aps if a.get("network") == net), None)
        return None

    def net_label(name: str) -> str:
        n = mon.net(name)
        return f"Wi-Fi {n.get('ssid') or name}"

    async def by_ap(_):
        aps = [a for a in mon.infra if a["role"] == "ap"]
        res: dict[str, dict[str, int]] = {}
        infra_ips = mon.infra_ips()
        for d in db.devices():
            if not d["online"] or d["ip"] in infra_ips:
                continue
            ap = ap_of(d, aps) or net_label(d.get("network") or mon.home)
            res.setdefault(ap, {})
            res[ap][d["display_type"]] = res[ap].get(d["display_type"], 0) + 1
        return JSONResponse({"aps": res, "icons": ICONS})

    async def topology(_):
        inf = cfg.get("infrastructure", {})
        modem = inf.get("modem", {"name": "Módem"})
        aps = [a for a in mon.infra if a["role"] == "ap"]
        infra_ips = mon.infra_ips()
        devs = [d for d in db.devices() if d["ip"] not in infra_ips]
        node = lambda d: {"name": d["display_name"], "mac": d["mac"], "ip": d["ip"],  # noqa: E731
                          "type": d["display_type"], "icon": ICONS.get(d["display_type"], "❔"),
                          "online": bool(d["online"])}
        children = []
        for ap in aps:
            st = mon.infra_status.get(ap["name"], {})
            net = ap.get("network")
            children.append({"name": ap["name"], "ip": ap.get("ip"), "icon": "🧪" if net else "📡",
                             "online": st.get("up", True), "admin_url": ap.get("admin_url"),
                             "type": f"red {net}" if net else None,
                             "children": [node(d) for d in devs if ap_of(d, aps) == ap["name"]]})
        unassigned = [node(d) for d in devs if ap_of(d, aps) is None]
        if unassigned:
            children.append({"name": net_label(mon.home), "icon": "📶", "online": True,
                             "children": unassigned})
        return JSONResponse({"name": "Internet", "icon": "🌐",
                             "online": mon.target_status.get("internet", {}).get("up", True),
                             "children": [{"name": modem.get("name", "Módem"), "ip": modem.get("ip"),
                                           "icon": "📶", "admin_url": modem.get("admin_url"),
                                           "online": mon.infra_status.get(modem.get("name", ""), {}).get("up", True),
                                           "children": children}]})

    # --------------------------------------------------- accesos y cámaras
    async def links(_):
        inf = cfg.get("infrastructure", {})
        out = []
        m = inf.get("modem")
        if m and m.get("admin_url"):
            out.append({"name": m.get("name", "Módem"), "url": m["admin_url"], "icon": "📶",
                        "group": "Red", "up": mon.infra_status.get(m.get("name"), {}).get("up")})
        for ap in inf.get("access_points", []) or []:
            if ap.get("admin_url"):
                out.append({"name": ap["name"], "url": ap["admin_url"], "icon": "📡", "group": "Red",
                            "detail": " · ".join(x for x in (ap.get("model"), ap.get("location")) if x),
                            "up": mon.infra_status.get(ap["name"], {}).get("up")})
        for l in cfg.get("links", []) or []:
            out.append({**l, "group": "Servicios"})
        return JSONResponse({"links": out})

    async def cameras(_):
        c = cfg.get("cameras", {}) or {}
        url = c.get("frigate_url")
        if not url:
            return JSONResponse({"cameras": [], "error": "Configura cameras.frigate_url"})
        try:
            fc = http_json(f"{url.rstrip('/')}/api/config", timeout=5)
            names = list((fc or {}).get("cameras", {}).keys())
        except RuntimeError as e:
            return JSONResponse({"cameras": [], "error": str(e), "frigate_url": url})
        return JSONResponse({"cameras": names, "frigate_url": url, "go2rtc_url": c.get("go2rtc_url")})

    async def camera_snapshot(request: Request):
        """Proxy de la última foto (así el navegador no necesita llegar a la PC)."""
        import urllib.request
        name = request.path_params["name"]
        if not re.match(r"^[\w-]+$", name):
            return PlainTextResponse("nombre inválido", 400)
        url = (cfg.get("cameras", {}) or {}).get("frigate_url", "").rstrip("/")
        try:
            with urllib.request.urlopen(f"{url}/api/{name}/latest.jpg?h=360", timeout=8) as r:
                from starlette.responses import Response
                return Response(r.read(), media_type="image/jpeg", headers={"Cache-Control": "no-store"})
        except OSError as e:
            return PlainTextResponse(f"Sin imagen: {e}", 502)

    routes = [
        Route("/", index), Route("/health", health), Route("/metrics", prom_metrics),
        Route("/api/summary", summary), Route("/api/devices", devices),
        Route("/api/devices/known", mark_all_known, methods=["POST"]),
        Route("/api/devices/{mac}", device_update, methods=["PATCH"]),
        Route("/api/devices/{mac}", device_delete, methods=["DELETE"]),
        Route("/api/devices/{mac}/rescan", device_rescan, methods=["POST"]),
        Route("/api/events", events), Route("/api/acl", acl_list),
        Route("/api/acl", acl_add, methods=["POST"]),
        Route("/api/acl/{rid:int}", acl_delete, methods=["DELETE"]),
        Route("/api/traffic", traffic), Route("/api/totals", totals),
        Route("/api/by-ap", by_ap), Route("/api/topology", topology),
        Route("/api/links", links), Route("/api/cameras", cameras),
        Route("/api/cameras/{name}/snapshot", camera_snapshot),
        Mount("/static", StaticFiles(directory=STATIC), name="static"),
    ]
    async def bad_request(_request, exc):
        return JSONResponse({"error": f"Petición inválida: {exc}"}, 400)

    return Starlette(routes=routes, exception_handlers={ValueError: bad_request})
