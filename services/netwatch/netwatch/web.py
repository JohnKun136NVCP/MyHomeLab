"""API y app web de netwatch (Starlette)."""
from __future__ import annotations

import asyncio
import contextlib
import sqlite3
import json
import os
import re
import threading
import time
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from homelab_common import http_json, prom_query, prom_range

import ipaddress

from . import acl, auth, camprobe, kuma, respaldos, metrics, scanner, settings as st, vpn
from .inbound import service_name
from .classify import ICONS, TYPES
from .monitor import Monitor

STATIC = Path(__file__).resolve().parent / "static"
MAC_RE = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")
RANGES = {"1h": (3600, 30), "6h": (21600, 120), "24h": (86400, 300), "7d": (604800, 1800)}


def build_app(mon: Monitor) -> Starlette:
    db = mon.db
    cfg = mon.cfg
    auth.bind(db)
    prom = os.environ.get("PROMETHEUS_URL", "http://127.0.0.1:9090/prometheus")

    async def index(_):
        # ?v=<fecha del archivo>: tras ./hl update el navegador baja el app.js nuevo (no el de su caché)
        html_ = (STATIC / "index.html").read_text()
        for f in ("app.js", "style.css"):
            v = int((STATIC / f).stat().st_mtime)
            html_ = html_.replace(f'/static/{f}"', f'/static/{f}?v={v}"')
        return HTMLResponse(html_, headers={"Cache-Control": "no-cache"})

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
        ev = db.events(500, day, fp="0")      # sin falsos positivos
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
            "ups": mon.ups.status() if mon.ups.enabled else {"enabled": False},
            "roaming": mon.roaming, "current_network": mon.current, "home_network": mon.home,
            "hop_every": mon.hop_every, "last_hop": mon.last_hop, "roam_on": mon.roam_on,
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
    def _ev_query(request: Request, limit_default: int = 200, limit_max: int = 1000) -> list[dict]:
        qp = request.query_params
        days = float(qp.get("days") or 0)
        return db.events(min(int(qp.get("limit", limit_default)), limit_max),
                         since=time.time() - days * 86400 if days else 0,
                         level=qp.get("level", ""), kind=qp.get("kind", ""),
                         search=qp.get("q", "")[:100], fp=qp.get("fp", ""))

    async def events(request: Request):
        return JSONResponse({"events": _ev_query(request), "kinds": db.event_counts(),
                             "rules": len(db.suppress_rules())})

    async def events_export(request: Request):
        """Bitácora en CSV o JSON (con los mismos filtros de la pantalla)."""
        import csv
        import io
        rows = _ev_query(request, 100000, 100000)
        stamp = time.strftime("%Y%m%d-%H%M")
        if request.query_params.get("format") == "json":
            return Response(json.dumps(rows, ensure_ascii=False, indent=1), media_type="application/json",
                            headers={"Content-Disposition": f'attachment; filename="bitacora-{stamp}.json"'})
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["fecha", "nivel", "tipo", "titulo", "detalle", "mac", "falso_positivo", "nota"])
        for e in rows:
            w.writerow([time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(e["ts"])), e["level"], e["kind"],
                        e["title"], e["detail"], e["mac"], "sí" if e.get("fp") else "", e.get("fp_note") or ""])
        return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",   # BOM: Excel lo abre bien
                        headers={"Content-Disposition": f'attachment; filename="bitacora-{stamp}.csv"'})

    async def export_all(_):
        """Todo en un .zip: bitácora, dispositivos, conexiones bloqueadas, ACL y reglas de silencio."""
        import csv
        import io
        import zipfile

        def to_csv(rows: list[dict]) -> str:
            if not rows:
                return ""
            b = io.StringIO()
            w = csv.DictWriter(b, fieldnames=list(rows[0].keys()), extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v)
                            for k, v in r.items()})
            return "\ufeff" + b.getvalue()
        mem = io.BytesIO()
        with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("bitacora.csv", to_csv(db.events(100000)))
            z.writestr("dispositivos.csv", to_csv(db.devices()))
            z.writestr("conexiones-entrantes.csv", to_csv(db.q("SELECT * FROM inbound ORDER BY last DESC")))
            z.writestr("acl.csv", to_csv(db.acl()))
            z.writestr("falsos-positivos.csv", to_csv(db.suppress_rules()))
            z.writestr("LEEME.txt", "Exportado del panel del homelab el " + time.strftime("%Y-%m-%d %H:%M") +
                       ".\nLas fechas (ts, first, last…) están en segundos desde 1970 (epoch).\n")
        stamp = time.strftime("%Y%m%d-%H%M")
        return Response(mem.getvalue(), media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="homelab-{stamp}.zip"'})

    async def event_fp(request: Request):
        eid = int(request.path_params["eid"])
        if request.method == "DELETE":
            db.unmark_fp(eid)
            return JSONResponse({"ok": True})
        b = await request.json()
        r = db.mark_fp(eid, str(b.get("note", ""))[:300], bool(b.get("suppress")))
        if not r:
            return JSONResponse({"error": "No existe ese evento"}, 404)
        return JSONResponse({"ok": True, **r})

    async def events_clear(request: Request):
        b = await request.json()
        days = b.get("older_than_days")
        n = db.clear_events(float(days) if days not in (None, "") else None,
                            str(b.get("kind", "")), bool(b.get("only_fp")))
        db.add_event("info", "log_cleared", f"Se limpiaron {n} eventos de la bitácora",
                     f"más viejos que {days} días" if days not in (None, "") else "todos" + (" (solo falsos positivos)" if b.get("only_fp") else ""))
        db.mark_notified([e["id"] for e in db.pending_notifications() if e["kind"] == "log_cleared"])
        return JSONResponse({"ok": True, "deleted": n})

    async def suppress_list(request: Request):
        if request.method == "DELETE":
            db.delete_suppress(int(request.path_params["rid"]))
            return JSONResponse({"ok": True})
        return JSONResponse({"rules": db.suppress_rules()})

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

    # ------------------------------------------------- conexiones entrantes
    def origin_of(ip: str) -> str:
        """casa / laboratorio / VPN / Internet según la IP de origen."""
        for i in mon.infra:   # primero: tráfico que llega por el NAT de un Tenda
            if i.get("wan_ip") == ip:
                return f"{i.get('network') or 'otra red'} (vía {i['name']})"
        for n in mon.networks:
            if scanner.in_network(ip, n["cidr"]):
                return n["name"]
        try:
            a = ipaddress.ip_address(ip)
        except ValueError:
            return "?"
        if a in ipaddress.ip_network("10.0.0.0/8"):
            return "VPN"
        return "red privada" if a.is_private else "Internet"

    async def inbound(request: Request):
        hours = max(1, min(int(request.query_params.get("hours", 24)), 24 * 14))
        since = time.time() - hours * 3600
        by_ip = {d["ip"]: d for d in db.devices()}
        rows = db.inbound_summary(since)
        for r in rows:
            d = by_ip.get(r["ip"])
            r["name"] = d["display_name"] if d else ""
            r["mac"] = d["mac"] if d else ""
            r["origin"] = origin_of(r["ip"])
            r["services"] = {p: service_name(int(p)) for p in r["ports"]}
        hourly = db.inbound_hourly(since)
        return JSONResponse({"ips": rows, "hourly": hourly, "hours": hours,
                             "error": mon.inbound.error,
                             "sources": {"conntrack": mon.inbound.conntrack.exists(),
                                         "ufw": mon.inbound.ufw.path.exists(),
                                         "honeypot": mon.inbound.canary.path.exists()}})

    async def inbound_ip(request: Request):
        ip = request.path_params["ip"]
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            return JSONResponse({"error": "IP inválida"}, 400)
        evs = [e for e in db.events(1000) if ip in (e.get("detail") or "") or ip in (e.get("title") or "")
               or (e.get("mac") or "").startswith(ip)]
        hourly = db.q("SELECT hour, action, count n FROM inbound_hourly WHERE ip=? ORDER BY hour", (ip,))
        return JSONResponse({"ip": ip, "origin": origin_of(ip), "events": evs[:50], "hourly": hourly,
                             "ufw": f"sudo ufw insert 1 deny from {ip} comment 'bloqueada desde MyHomeLab'"})

    # ------------------------------------------------- buscar un equipo
    async def lookup(request: Request):
        """Busca por IP, MAC o nombre. Si es IP y no la conocemos, la sondea en la red."""
        q = str((await request.json()).get("q", "")).strip().lower()
        if not q:
            return JSONResponse({"error": "Escribe una IP, MAC o nombre"}, 400)
        devs = db.devices()
        hits = [d for d in devs if q in " ".join(str(d.get(k) or "") for k in
                                                     ("ip", "mac", "name", "hostname", "vendor")).lower()]
        res = {"q": q, "matches": hits[:20], "probe": None}
        ip = q
        if not _is_ip(q) and not hits:      # nombre: pregúntale al DNS de cada red
            from . import names
            for n in mon.networks:
                for srv in mon.dns_servers(n["name"]):
                    a = names.query_a(q, srv)
                    if a:
                        ip = a
                        break
        if _is_ip(ip) and not any(d["ip"] == ip for d in hits):
            import asyncio
            res["probe"] = await asyncio.to_thread(mon.probe, ip)
        return JSONResponse(res)

    def _is_ip(x: str) -> bool:
        try:
            ipaddress.ip_address(x)
            return True
        except ValueError:
            return False

    async def dns(_):
        ag = mon.adguard
        if not ag.enabled:
            return JSONResponse({"enabled": False})
        names_by_ip = {d["ip"]: d["display_name"] for d in db.devices()}
        st = ag.stats
        return JSONResponse({
            "enabled": True, "error": ag.error,
            "queries": st.get("num_dns_queries", 0), "blocked": st.get("num_blocked_filtering", 0),
            "malware": st.get("num_replaced_safebrowsing", 0),
            "top_clients": [{"ip": ip, "name": names_by_ip.get(ip) or ag.names.get(ip, ""), "n": n}
                            for ip, n in ag.top_clients()[:15]],
            "top_blocked": [{"domain": k, "n": v} for item in st.get("top_blocked_domains") or []
                            for k, v in item.items()][:15],
            "url": ag.url, "upstream": ag.upstream or ag.get_upstream()})

    # ------------------------------------------------------ Wake-on-LAN
    async def wol_list(_):
        from . import wol
        online = {d["mac"]: d for d in db.devices()}
        return JSONResponse({"targets": [{**t, "online": bool((online.get(t["mac"]) or {}).get("online")),
                                          "ip": (online.get(t["mac"]) or {}).get("ip")}
                                         for t in wol.targets(mon.cfg)]})

    async def wol_send(request: Request):
        from . import wol
        b = await request.json()
        mac = str(b.get("mac", "")).strip()
        name = str(b.get("name", "")).strip().lower()
        if not mac and name:
            mac = next((t["mac"] for t in wol.targets(mon.cfg) if t["name"].lower() == name), "")
            if not mac:
                return JSONResponse({"error": f"No hay ningún equipo «{name}» en wake_on_lan"}, 404)
        try:
            mac = wol.norm_mac(mac)
            cidrs = [n["cidr"] for n in mon.networks if n.get("cidr")]
            sent = wol.send(mac, cidrs)
        except (ValueError, OSError) as e:
            return JSONResponse({"error": str(e)}, 400)
        db.add_event("info", "wol", f"Encendido remoto (Wake-on-LAN) a {mac}", "Mandado por " + ", ".join(sent), mac)
        return JSONResponse({"ok": True, "mac": mac, "sent": sent})

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

    # ---------------------------------------------------- panel de herramientas
    def tool_list() -> list[dict]:
        env = st.read_env()
        host = env.get("HOMELAB_HOST") or os.environ.get("HOMELAB_HOST", "")
        port = env.get("HTTPS_PORT") or "443"
        profiles = {p.strip() for p in env.get("COMPOSE_PROFILES", "").split(",") if p.strip()}
        base = f"https://{host}:{port}" if host else ""
        tools = [
            {"name": "Grafana", "icon": "📊", "url": "/grafana/", "group": "Homelab",
             "desc": "Tableros de red, nodos, DNS y conexiones", "check": f"{base}/grafana/api/health"},
            {"name": "Prometheus", "icon": "🔥", "url": "/prometheus/targets", "group": "Homelab",
             "desc": "Métricas y estado de cada fuente", "check": "http://127.0.0.1:9090/prometheus/-/ready"},
            {"name": "Alertas activas", "icon": "🔔", "url": "/prometheus/alerts", "group": "Homelab",
             "desc": "Reglas y lo que está sonando", "check": "http://127.0.0.1:9090/prometheus/-/ready"},
        ]
        if "dns" in profiles or mon.adguard.reachable:
            tools.append({"name": "AdGuard Home", "icon": "🛡", "url": f"http://{host}:3002", "group": "Servicios",
                          "desc": "DNS de la casa y bloqueo de anuncios/malware",
                          "check": mon.adguard.url or "http://127.0.0.1:3002"})
        if "servicios" in profiles:
            tools += [
                {"name": "Uptime Kuma", "icon": "📈", "url": f"http://{host}:3001", "group": "Servicios",
                 "desc": "¿Están arriba tu página, el VPS y tus servicios?", "check": "http://127.0.0.1:3001"},
                {"name": "Vaultwarden", "icon": "🔑", "url": "/vault/", "group": "Servicios",
                 "desc": "Tus contraseñas (apps de Bitwarden)", "check": f"{base}/vault/alive"}]
        # --- Herramientas de la casa (./hl home): todas, con encendida/apagada
        home_on = home_profiles()
        for key, name, icon, port, path, desc in HOME_TOOLS:
            on = key in home_on
            if key == "dns-backup" and ("dns" in profiles) and not on:
                continue      # aquí está el AdGuard principal: el de respaldo va en otra máquina
            t = {"name": name, "icon": icon, "group": "En casa", "desc": desc, "home": key, "enabled": on,
                 "url": f"http://{host}:{port}" if port else "#casa"}
            if on and port:
                t["check"] = f"http://127.0.0.1:{port}{path}"
            elif on:
                t["status"] = True          # sin página propia (el túnel): basta con que esté encendido
            else:
                t["status"] = None
            tools.append(t)
        # --- Servicios que agregan los scripts (Immich, Speedtest, Diun, Authelia, no-break, lo de la PC)
        links_by_name = {l.get("name"): l for l in cfg.get("links", []) or []}
        im = links_by_name.get("Fotos (Immich)")
        if im or check_url("http://127.0.0.1:2283/api/server/ping"):
            url = (im or {}).get("url") or f"http://{host}:2283"
            tools.append({"name": "Fotos (Immich)", "icon": "📸", "url": url, "group": "Servicios",
                          "desc": "Fotos y videos de los celulares, cada quien su cuenta",
                          "check": url.rstrip("/") + "/api/server/ping"})
        if check_url("http://127.0.0.1:8085/health"):
            tools.append({"name": "CrowdSec", "icon": "🛑", "url": "#eventos", "group": "Servicios",
                          "desc": "Bloquea en el firewall a quien ataca SSH o el panel (sudo ./hl crowdsec status)",
                          "check": "http://127.0.0.1:8085/health"})
        if "speedtest" in profiles:
            tools.append({"name": "Velocidad del internet", "icon": "🚀", "url": f"http://{host}:8765", "group": "Servicios",
                          "desc": "Speedtest Tracker: mide cada 6 h y guarda el historial",
                          "check": "http://127.0.0.1:8765"})
        if "ntopng" in profiles:
            tools.append({"name": "Tráfico de la red (ntopng)", "icon": "🔎", "url": f"http://{host}:3005", "group": "Servicios",
                          "desc": "Quién usa la red, con qué protocolos y a dónde se conecta",
                          "check": "http://127.0.0.1:3005"})
        if "diun" in profiles:
            tools.append({"name": "Avisos de versiones", "icon": "🆕", "url": "#sistema", "group": "Servicios",
                          "desc": "Diun: te avisa por Telegram si hay imagen nueva (sin página)",
                          "status": None})
        if "sso" in profiles and env.get("SSO_DOMAIN"):
            p_ = "" if port == "443" else f":{port}"
            tools.append({"name": "Inicio de sesión único", "icon": "🔐", "url": f"https://auth.{env['SSO_DOMAIN']}{p_}",
                          "group": "Servicios", "desc": f"Authelia · entra en panel.{env['SSO_DOMAIN']}",
                          "check": f"{base}/" if base else None})
        if mon.ups.enabled:
            u = mon.ups.status()
            tools.append({"name": "No-break", "icon": "🔌", "url": "#resumen", "group": "Servicios",
                          "desc": (f"{u.get('status_text', '')}" + (f" · {round(u['charge'])}%" if u.get("charge") is not None else ""))
                          if u.get("ok") else "sin conexión con NUT", "status": bool(u.get("ok"))})
        for name, icon, desc, path in (("Cine en casa", "🍿", "Películas y series para la tele (en la PC)", "/"),
                                       ("Jellyfin (administrar)", "🎬", "Biblioteca de video (en la PC)", "/health"),
                                       ("Frigate (cámaras)", "🎥", "Grabación y detección de las cámaras (en la PC)", "/api/version")):
            l = links_by_name.get(name)
            if l and l.get("url", "").startswith("http"):
                tools.append({"name": name, "icon": l.get("icon", icon), "url": l["url"], "group": "Servicios",
                              "desc": desc, "check": l["url"].rstrip("/") + path})
        if any("controld.com" in u for u in mon.dns_upstream):
            tools.append({"name": "Control D", "icon": "🧭", "url": "https://controld.com/dashboard",
                          "group": "Servicios", "desc": "DNS de salida de AdGuard: " +
                          ", ".join(u.rsplit("/", 1)[-1] for u in mon.dns_upstream),
                          "check": "https://freedns.controld.com/p0"})
        inf = cfg.get("infrastructure", {}) or {}
        m = inf.get("modem")
        if m and m.get("admin_url"):
            tools.append({"name": m.get("name", "Módem"), "icon": "📶", "url": m["admin_url"], "group": "Red",
                          "desc": m.get("ip", ""), "status": mon.infra_status.get(m.get("name"), {}).get("up")})
        for ap in inf.get("access_points", []) or []:
            if ap.get("admin_url"):
                tools.append({"name": ap["name"], "icon": "📡", "url": ap["admin_url"], "group": "Red",
                              "desc": " · ".join(x for x in (ap.get("model"), ap.get("location"), ap.get("ip")) if x),
                              "status": mon.infra_status.get(ap["name"], {}).get("up")})
        seen = {t["url"] for t in tools}
        names = {t["name"] for t in tools}
        known = {"/grafana/", "/prometheus/", "/vault/"}
        for link in cfg.get("links", []) or []:
            if link.get("url") in seen or link.get("url") in known or link.get("name") in names or \
                    (any(k in link.get("url", "") for k in (":3001", ":3002")) and link.get("group") != "En casa"):
                continue
            # Los que agregan los scripts (home.sh, greenbone…) traen group: Servicios y su check
            tools.append({"name": link["name"], "icon": link.get("icon", "🔗"), "url": link["url"],
                          "group": link.get("group") or "Tus enlaces", "desc": link.get("desc", link["url"]),
                          "check": link.get("check") or (link["url"] if link["url"].startswith("http") else None)})
        return tools

    HOME_TOOLS = [   # clave, nombre, icono, puerto, ruta que responde, descripción (los de scripts/home.sh)
        ("homeassistant", "Home Assistant", "🏠", 8123, "/manifest.json", "Focos, enchufes y cámaras con automatizaciones"),
        ("paperless", "Documentos (Paperless)", "📄", 8010, "/", "Documentos escaneados con búsqueda"),
        ("navidrome", "Música (Navidrome)", "🎵", 4533, "/ping", "Tu música en streaming, con app en el celular"),
        ("mealie", "Recetas y súper (Mealie)", "🍲", 9925, "/api/app/about", "Recetas de la familia y lista del súper compartida"),
        ("jellyseerr", "Pedir películas (Jellyseerr)", "🎟", 5055, "/api/v1/status", "Pide películas o series para Cine en casa"),
        ("syncthing", "Syncthing", "🔄", 8384, "/rest/noauth/health", "Carpetas sincronizadas entre tus equipos"),
        ("umami", "Visitas de la página (Umami)", "📊", 3007, "/api/heartbeat", "Visitas a tu página web, sin Google"),
        ("cloudflared", "Túnel de Cloudflare", "☁", 0, "", "Publica Umami a internet sin abrir puertos"),
        ("dns-backup", "DNS de respaldo", "🛡", 3002, "/", "Segundo AdGuard que copia al principal"),
    ]
    HOME_KEYS = {t[0] for t in HOME_TOOLS}

    def home_profiles() -> set[str]:
        f = Path(os.environ.get("HOMELAB_HOME_ENV", "/homelab/home/.env"))
        try:
            for ln in f.read_text().splitlines():
                if ln.startswith("COMPOSE_PROFILES="):
                    return {p.strip() for p in ln.split("=", 1)[1].strip("\"' ").split(",") if p.strip()}
        except OSError:
            pass
        return set()

    def check_url(url: str) -> bool:
        import ssl
        import urllib.error
        import urllib.request
        ctx = ssl.create_default_context()
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE     # certificados locales
        try:
            with urllib.request.urlopen(urllib.request.Request(url, method="GET"), timeout=4, context=ctx):
                return True
        except urllib.error.HTTPError as e:
            return e.code < 500           # 401/403/404 = el servicio sí contesta
        except (urllib.error.URLError, OSError, ValueError):
            return False

    async def tools(_):
        import asyncio
        items = await asyncio.to_thread(tool_list)

        async def one(t):
            if "status" not in t and t.get("check"):
                t["status"] = await asyncio.to_thread(check_url, t["check"])
            t.pop("check", None)
            return t
        return JSONResponse({"tools": await asyncio.gather(*(one(t) for t in items))})

    # -------------------------------------------------------------- ajustes
    def cfg_path():
        from pathlib import Path
        return Path(os.environ.get("HOMELAB_CONFIG", "/config/homelab.yml"))

    async def settings_get(_):
        env = st.read_env()
        data, raw = st.read_yaml(cfg_path())
        groups: dict[str, list] = {}
        for f in st.FIELDS:
            v = env.get(f["key"], "")
            item = {k: f[k] for k in ("key", "label", "kind", "help", "generate") if k in f}
            item.setdefault("kind", "text")
            if item["kind"] in ("secret", "password"):
                item.update(set=bool(v), hint=st.mask(v) if item["kind"] == "secret" else ("guardada" if v else ""))
            elif item["kind"] == "profiles":
                item.update(value=[p for p in v.split(",") if p], options=st.PROFILES)
            else:
                item["value"] = v
            groups.setdefault(f["group"], []).append(item)
        ygroups: dict[str, list] = {}
        from .adguard import UPSTREAMS
        for f in st.YAML_FIELDS:
            item = {**f, "kind": f.get("kind", "text"), "value": st.yaml_get(data, f["path"])}
            if item["kind"] == "range":
                v = item["value"]
                item["value"] = "-".join(v) if isinstance(v, list) and len(v) == 2 else ""
            if item.get("options") == "UPSTREAMS":
                item["options"] = {k: v[0] for k, v in UPSTREAMS.items()}
                item["value"] = item["value"] or ""
            ygroups.setdefault(f["group"], []).append(item)
        devs = [{"name": d.get("name"), "os": d.get("os", ""),
                 "token_set": bool(env.get(st.device_env_key(d.get("name", ""))))}
                for d in (st.yaml_get(data, "backups.devices") or [])]
        return JSONResponse({"env_groups": groups, "yaml_groups": ygroups, "devices": devs,
                             "yaml_raw": raw, "env_exists": st.ENV_PATH.exists(),
                             "apply": st.apply_status()})

    async def settings_save(request: Request):
        b = await request.json()
        env_up: dict[str, str | None] = {}
        for k, v in (b.get("env") or {}).items():
            f = st.FIELD_BY_KEY.get(k)
            if not f:
                continue
            kind = f.get("kind", "text")
            if kind in ("secret", "password") and (v is None or v == ""):
                continue                      # vacío = no cambiar
            if kind == "password":
                if len(v) < 8:
                    return JSONResponse({"error": "La contraseña de la web debe tener al menos 8 caracteres"}, 400)
                try:
                    v = st.hash_password(v)
                except ImportError:
                    return JSONResponse({"error": "Falta bcrypt en el contenedor (reconstruye netwatch)"}, 400)
            elif kind == "profiles":
                v = ",".join(p for p in (v or []) if p in st.PROFILES)
            elif kind == "bool":
                v = "true" if v in (True, "true", "1", 1) else "false"
            elif kind == "number" and str(v).strip() and not str(v).strip().isdigit():
                return JSONResponse({"error": f"{f['label']}: debe ser un número"}, 400)
            env_up[k] = str(v)
        for k in [f["key"] for f in st.FIELDS if f.get("generate")]:
            if (b.get("generate") or {}).get(k):
                env_up[k] = st.new_token()
        new_tokens = {}
        path = cfg_path()
        data, _ = st.read_yaml(path)
        yaml_changed = False
        for p, v in (b.get("yaml") or {}).items():
            f = next((x for x in st.YAML_FIELDS if x["path"] == p), None)
            if not f:
                continue
            if f.get("kind") == "number":
                v = int(v) if str(v).strip().isdigit() else None
                if v is None:
                    return JSONResponse({"error": f"{f['label']}: debe ser un número"}, 400)
            elif f.get("kind") == "bool":
                v = bool(v)
            elif f.get("kind") == "range":
                txt = str(v or "").replace(" ", "")
                if not txt:
                    v = None
                elif re.match(r"^\d{2}:\d{2}-\d{2}:\d{2}$", txt):
                    v = txt.split("-")
                else:
                    return JSONResponse({"error": f"{f['label']}: escribe por ejemplo 23:00-07:00"}, 400)
            if p == "reports.daily_time" and v and not re.match(r"^\d{2}:\d{2}$", str(v)):
                return JSONResponse({"error": "La hora del reporte va como HH:MM"}, 400)
            old = st.yaml_get(data, p)
            if old != v:
                st.yaml_set(data, p, v)
                yaml_changed = True
                if p == "infrastructure.modem.ip" and \
                        st.yaml_get(data, "infrastructure.modem.admin_url") in (f"http://{old}", f"https://{old}", None):
                    st.yaml_set(data, "infrastructure.modem.admin_url", f"http://{v}")
        if "devices" in b:
            devs = []
            for d in b["devices"]:
                name = re.sub(r"[^a-z0-9-]", "-", str(d.get("name", "")).strip().lower()).strip("-")
                if name and name not in {x["name"] for x in devs}:
                    devs.append({"name": name, "os": d.get("os") or "linux"})
            if devs != (st.yaml_get(data, "backups.devices") or []):
                st.yaml_set(data, "backups.devices", devs)
                yaml_changed = True
            env_now = st.read_env()
            for d in devs:
                key = st.device_env_key(d["name"])
                if not env_now.get(key) and key not in env_up:
                    env_up[key] = new_tokens[d["name"]] = st.new_token()
        dns_msg = ""
        if yaml_changed:
            st.write_yaml(path, data)
            from .adguard import upstream_urls
            urls = upstream_urls(st.yaml_get(data, "dns.upstream") or "", st.yaml_get(data, "dns.controld_resolver") or "")
            if urls != mon.dns_upstream:
                mon.dns_upstream = urls
                if urls and mon.adguard.url:          # se aplica ya, sin reiniciar nada
                    import asyncio
                    ok, dns_msg = await asyncio.to_thread(mon.adguard.set_upstream, urls)
                    dns_msg = ("✅ " if ok else "⚠ ") + dns_msg
        changed = st.write_env(env_up) if env_up else []
        restart = st.restart_for(changed)
        if yaml_changed:
            restart = "all" if restart == "all" else sorted(set(restart) | {"netwatch", "bot"})
        return JSONResponse({"ok": True, "env_changed": changed, "yaml_changed": yaml_changed,
                             "new_tokens": new_tokens, "restart": restart, "dns_msg": dns_msg})

    async def settings_yaml(request: Request):
        raw = (await request.json()).get("raw", "")
        try:
            st.write_yaml_raw(cfg_path(), raw)
        except (ValueError, Exception) as e:  # noqa: BLE001 - errores de YAML al usuario
            return JSONResponse({"error": f"YAML inválido: {e}"}, 400)
        return JSONResponse({"ok": True, "restart": ["netwatch", "bot"]})

    async def settings_token(request: Request):
        name = str((await request.json()).get("device", ""))
        data, _ = st.read_yaml(cfg_path())
        if name not in {d.get("name") for d in st.yaml_get(data, "backups.devices") or []}:
            return JSONResponse({"error": "Ese dispositivo no está en la lista"}, 404)
        tok = st.new_token()
        st.write_env({st.device_env_key(name): tok})
        return JSONResponse({"ok": True, "device": name, "token": tok, "restart": ["bot"]})

    async def apply_post(request: Request):
        b = await request.json() if int(request.headers.get("content-length") or 0) else {}
        try:
            req = st.request_apply(b.get("services", "all"), b.get("force", []))
        except RuntimeError as e:
            return JSONResponse({"error": str(e)}, 400)
        return JSONResponse({"ok": True, "request": req})

    async def apply_get(_):
        return JSONResponse(st.apply_status())

    # --------------------------------------------------------------- sesión
    def client_ip(request: Request) -> str:
        fwd = request.headers.get("x-forwarded-for", "")
        return fwd.split(",")[0].strip() or (request.client.host if request.client else "?")

    async def auth_check(request: Request):
        """Caddy (forward_auth) pregunta aquí antes de cada página del panel."""
        ses = auth.from_request(request)
        uri = request.headers.get("x-forwarded-uri", "/")
        if ses:
            if not auth.allowed(ses["role"], request.headers.get("x-forwarded-method", "GET"), uri):
                return JSONResponse({"error": "Tu cuenta es de solo lectura: pídele al administrador "
                                              "que haga este cambio"}, 403)
            return PlainTextResponse("ok", headers={"X-Homelab-User": ses["user"],
                                                    "X-Homelab-Role": ses["role"]})
        if uri.startswith(("/api/", "/prometheus/api/", "/metrics")):
            return JSONResponse({"error": "Sesión cerrada o caducada: vuelve a entrar"}, 401)
        from urllib.parse import quote
        return RedirectResponse(f"/login?next={quote(auth.safe_next(uri))}", 302)

    async def login_page(request: Request, msg: str = "", status: int = 200):
        import html
        nxt = auth.safe_next(request.query_params.get("next", "/"))
        body = auth.LOGIN_HTML.replace("{msg}", f'<p class="err">{html.escape(msg)}</p>' if msg else "") \
            .replace("{next}", html.escape(nxt))
        return HTMLResponse(body, status, headers={"Cache-Control": "no-store"})

    async def login_get(request: Request):
        return await login_page(request)

    async def login_post(request: Request):
        from urllib.parse import parse_qs
        ip = client_ip(request)
        if auth.too_many(ip):
            return await login_page(request, "Demasiados intentos. Espera 10 minutos.", 429)
        form = {k: v[0] for k, v in parse_qs((await request.body()).decode(errors="replace")).items()}
        main_user, main_hash = auth.credentials()
        has_users = bool(db.q("SELECT 1 FROM users LIMIT 1"))
        if not main_hash and not has_users:
            return await login_page(request, "No hay contraseña configurada: en la Pi corre ./hl password", 503)
        acc = auth.authenticate(form.get("user", "").strip(), form.get("password", ""))
        if acc:
            auth.clear_fails(ip)
            note_login(acc, ip)
            r = RedirectResponse(auth.safe_next(form.get("next", "/")), 303)
            r.set_cookie(auth.COOKIE, auth.make_token(acc["user"]), max_age=auth.TTL, httponly=True,
                         secure=True, samesite="lax", path="/")
            return r
        n = auth.record_fail(ip)
        if n == auth.MAX_FAILS and not db.recent_event("login_failed", ip, 3600):
            db.add_event("warning", "login_failed", f"{n} intentos fallidos de entrar al panel",
                         f"Desde {ip}. Si no fuiste tú, revisa quién es en Conexiones.", ip)
        return await login_page(request, "Usuario o contraseña incorrectos.", 401)

    async def logout(request: Request):
        from . import sso
        if sso.trusted_user(request.headers):
            port = os.environ.get("HTTPS_PORT", "443")
            return RedirectResponse(f"https://auth.{sso.domain()}{'' if port == '443' else ':' + port}/logout", 303)
        r = RedirectResponse("/login", 303)
        r.delete_cookie(auth.COOKIE, path="/")
        return r

    def note_login(acc: dict, ip: str) -> None:
        """Guarda desde dónde entra cada quien y avisa por Telegram si es una IP nueva."""
        now = time.time()
        user = acc["user"]
        seen = db.q("SELECT 1 FROM login_ips WHERE username=? AND ip=?", (user, ip))
        first_ever = not db.q("SELECT 1 FROM login_ips WHERE username=?", (user,))
        db.x("INSERT INTO login_ips (username, ip, first, last) VALUES (?,?,?,?) "
             "ON CONFLICT(username, ip) DO UPDATE SET last=excluded.last", (user, ip, now, now))
        if not acc["main"]:
            db.x("UPDATE users SET last_login=?, last_ip=? WHERE username=?", (now, ip, user))
        if not seen and (cfg.get("anomalies", {}) or {}).get("notify_logins", True):
            dev = db.q("SELECT name, hostname, vendor FROM devices WHERE ip=? ORDER BY last_seen DESC LIMIT 1", (ip,))
            who = (dev[0].get("name") or dev[0].get("hostname") or dev[0].get("vendor")) if dev else ""
            db.add_event("info" if first_ever else "warning", "login_new_ip",
                         f"{user} entró al panel desde {'un equipo nuevo' if not first_ever else 'su primer equipo'}",
                         f"IP {ip}" + (f" ({who})" if who else " (no está en tu lista de dispositivos)")
                         + ". Si no fue esa persona, cambia su contraseña en Ajustes → Cuentas.", ip)

    async def me(request: Request):
        ses = auth.from_request(request)
        if not ses:
            return JSONResponse({"user": None})
        unread = db.q("SELECT COUNT(*) n FROM notices WHERE username=? AND seen=0", (ses["user"],))[0]["n"]
        return JSONResponse({**ses, "role_name": auth.ROLES.get(ses["role"], ses["role"]),
                             "logs": ses["role"] in auth.CAN_SEE_LOGS, "unread": unread})

    async def me_password(request: Request):
        ses = auth.from_request(request)
        if not ses:
            return JSONResponse({"error": "Sesión caducada"}, 401)
        b = await request.json()
        if not auth.authenticate(ses["user"], str(b.get("current", ""))):
            return JSONResponse({"error": "La contraseña actual no es correcta"}, 400)
        new = str(b.get("new", ""))
        if len(new) < 8:
            return JSONResponse({"error": "Usa al menos 8 caracteres"}, 400)
        h = st.hash_password(new)
        if ses["main"]:
            st.write_env({"WEB_PASSWORD_HASH": h})
        else:
            db.x("UPDATE users SET hash=? WHERE username=?", (h, ses["user"]))
        mon.sso.run(force=True)
        r = JSONResponse({"ok": True})        # nueva cookie: la vieja ya no vale
        r.set_cookie(auth.COOKIE, auth.make_token(ses["user"]), max_age=auth.TTL, httponly=True,
                     secure=True, samesite="lax", path="/")
        return r

    # ------------------------------------------------------------ cuentas
    USER_RE = re.compile(r"^[a-zA-Z0-9._-]{2,32}$")

    async def users_list(_):
        main_user, _h = auth.credentials()
        rows = db.q("SELECT username, role, created, last_login, last_ip FROM users ORDER BY username")
        ips = db.q("SELECT username, COUNT(*) n, MAX(last) last FROM login_ips GROUP BY username")
        by = {r["username"]: r for r in ips}
        main = {"username": main_user, "role": "admin", "main": True,
                "last_login": (by.get(main_user) or {}).get("last")}
        return JSONResponse({"users": [main] + [{**r, "main": False, "devices": (by.get(r["username"]) or {}).get("n", 0)}
                                                for r in rows],
                             "roles": auth.ROLES})

    async def users_save(request: Request):
        b = await request.json()
        name = str(b.get("username", "")).strip()
        role = b.get("role", "familia")
        if not USER_RE.match(name):
            return JSONResponse({"error": "Usuario de 2 a 32 letras, números, punto, guion o guion bajo"}, 400)
        if role not in auth.ROLES:
            return JSONResponse({"error": "Rol inválido"}, 400)
        if name == auth.credentials()[0]:
            return JSONResponse({"error": "Ese es el usuario principal: cámbialo en «Mi cuenta»"}, 400)
        pw = str(b.get("password", ""))
        exists = db.q("SELECT 1 FROM users WHERE username=?", (name,))
        if not exists and len(pw) < 8:
            return JSONResponse({"error": "Pon una contraseña de al menos 8 caracteres"}, 400)
        if pw and len(pw) < 8:
            return JSONResponse({"error": "Usa al menos 8 caracteres"}, 400)
        if exists:
            db.x("UPDATE users SET role=? WHERE username=?", (role, name))
            if pw:
                db.x("UPDATE users SET hash=? WHERE username=?", (st.hash_password(pw), name))
        else:
            db.x("INSERT INTO users (username, hash, role, created) VALUES (?,?,?,?)",
                 (name, st.hash_password(pw), role, time.time()))
        mon.sso.run(force=True)
        return JSONResponse({"ok": True, "created": not exists})

    async def users_delete(request: Request):
        name = request.path_params["name"]
        db.x("DELETE FROM users WHERE username=?", (name,))
        db.x("DELETE FROM login_ips WHERE username=?", (name,))
        for t in ("telegram_links", "notices", "link_codes", "backup_devices"):
            db.x(f"DELETE FROM {t} WHERE {'owner' if t == 'backup_devices' else 'username'}=?", (name,))
        mon.sso.run(force=True)
        return JSONResponse({"ok": True})

    async def logins(_):
        return JSONResponse({"logins": db.q("SELECT * FROM login_ips ORDER BY last DESC LIMIT 100")})

    async def camera_probe(request: Request):
        """Busca la ruta RTSP de una cámara genérica (foco 360°, mini cámara WiFi)."""
        import asyncio
        b = await request.json()
        ip = str(b.get("ip", "")).strip()
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            return JSONResponse({"error": "Escribe la IP de la cámara (ej. 192.168.1.40)"}, 400)
        res = await asyncio.to_thread(camprobe.probe, ip, str(b.get("user") or "admin"),
                                      str(b.get("password") or ""), str(b.get("name") or ""))
        if res.get("found"):
            mac = scanner.neigh_mac(ip) or ""
            dev = db.device(mac) if mac else None
            if dev and dev["display_type"] != "Cámara" and not dev.get("type_manual"):
                db.x("UPDATE devices SET type_manual='Cámara' WHERE mac=?", (mac,))
            res["mac"] = mac
        return JSONResponse(res)

    async def services_status(_):
        import asyncio
        sv = cfg.get("services", {}) or {}
        kuma_task = asyncio.to_thread(kuma.status, sv.get("uptime_kuma_url", "http://127.0.0.1:3001"),
                                      sv.get("status_page", "homelab"))
        # Lo que el propio panel revisa (sin depender de Uptime Kuma)
        items = [t for t in await asyncio.to_thread(tool_list)
                 if t.get("group") == "Servicios" or (t.get("group") == "En casa" and t.get("enabled"))]

        async def one(t):
            if "status" not in t and t.get("check"):
                t["status"] = await asyncio.to_thread(check_url, t["check"])
            t.pop("check", None)
            return t
        res, builtin = await asyncio.gather(kuma_task, asyncio.gather(*(one(t) for t in items)))
        res = dict(res)
        res["homelab"] = list(builtin)
        return JSONResponse(res)

    # ------------------------------------------------------------ sistema
    SYSTEM_ACTIONS = {"actualizar": "Actualizar desde GitHub", "actualizar-todo": "Actualizar todo",
                      "escanear-imagenes": "Revisar vulnerabilidades de las imágenes", "revisar": "Revisar el homelab",
                      "respaldo": "Respaldar el homelab"}

    async def system_get(_):
        rev = {}
        f = st.RUN_DIR / "revisar.json"
        if f.exists():
            try:
                import json as _json
                rev = _json.loads(f.read_text())
            except ValueError:
                rev = {}
        bk = {}
        f = st.RUN_DIR / "homelab-backup.json"
        if f.exists():
            try:
                import json as _json
                bk = _json.loads(f.read_text())
            except ValueError:
                bk = {}
        return JSONResponse({"apply": st.apply_status(), "revisar": rev, "backup": bk,
                             "actions": SYSTEM_ACTIONS,
                             "ca": (st.RUN_DIR / "homelab-ca.crt").exists()})

    async def system_action(request: Request):
        act = request.path_params["action"]
        if act not in SYSTEM_ACTIONS:
            return JSONResponse({"error": "acción desconocida"}, 404)
        try:
            req = st.request_apply("all", [], action=act)
        except RuntimeError as e:
            return JSONResponse({"error": str(e)}, 400)
        return JSONResponse({"ok": True, "request": req})

    async def home_toggle(request: Request):
        """Encender/apagar una herramienta de la casa: lo hace la Pi (homelab-apply) con scripts/home.sh."""
        b = await request.json()
        key, on = str(b.get("name", "")), bool(b.get("on"))
        if key not in HOME_KEYS:
            return JSONResponse({"error": "No conozco esa herramienta"}, 404)
        if on and key == "cloudflared" and "cloudflared" not in home_profiles():
            env = Path(os.environ.get("HOMELAB_HOME_ENV", "/homelab/home/.env"))
            if "CLOUDFLARE_TUNNEL_TOKEN=ey" not in (env.read_text() if env.exists() else ""):
                return JSONResponse({"error": "El túnel necesita su token: la primera vez enciéndelo en la Pi con "
                                              "./hl home enable cloudflared"}, 400)
        if on and key == "dns-backup":
            return JSONResponse({"error": "El DNS de respaldo va en OTRA máquina (no la del AdGuard principal): "
                                          "./hl home enable dns-backup ahí, o ./hl roles"}, 400)
        try:
            req = st.request_apply("all", [], action=f"home-{'enable' if on else 'disable'}:{key}")
        except RuntimeError as e:
            return JSONResponse({"error": str(e)}, 400)
        return JSONResponse({"ok": True, "request": req})

    async def ca_cert(_):
        f = st.RUN_DIR / "homelab-ca.crt"
        if not f.exists():
            return PlainTextResponse("Aún no está el certificado: en la Pi corre ./hl ca", 404)
        return FileResponse(f, media_type="application/x-x509-ca-cert", filename="homelab-ca.crt")

    # ------------------------------------------------------------------ VPN
    def vps_monitored(data: dict, hub_ip: str) -> bool:
        nodes = st.yaml_get(data, "infrastructure.nodes") or []
        return any(str(n.get("target", "")).startswith(hub_ip + ":") for n in nodes)

    async def vpn_get(_):
        import asyncio
        s = await asyncio.to_thread(vpn.status)
        clients = db.q("SELECT * FROM vpn_clients ORDER BY created")
        names = {c["public_key"]: c["name"] for c in clients}
        data, _ = st.read_yaml(cfg_path())
        for itf in s["interfaces"]:
            for p in itf["peers"]:
                p["name"] = "VPS" if p["public_key"] == itf["hub"]["hub_key"] else names.get(p["public_key"], "")
            itf["hub"]["monitored"] = bool(itf["hub"]["hub_ip"]) and vps_monitored(data, itf["hub"]["hub_ip"])
        return JSONResponse({**s, "clients": clients, "now": time.time()})

    def find_iface(name: str) -> dict | None:
        return next((i for i in vpn.status()["interfaces"] if i["name"] == name), None)

    async def vpn_client(request: Request):
        import asyncio
        b = await request.json()
        name = re.sub(r"[^\w .-]", "", str(b.get("name", "")).strip())[:40]
        if not name:
            return JSONResponse({"error": "Ponle un nombre (ej. telefono, laptop)"}, 400)
        itf = await asyncio.to_thread(find_iface, str(b.get("iface", "")))
        if not itf:
            return JSONResponse({"error": "No encuentro esa interfaz de WireGuard"}, 404)
        hub = itf["hub"]
        if not (hub["network"] and hub["hub_key"] and hub["hub_endpoint"]):
            return JSONResponse({"error": "No pude deducir la red o el VPS de ese túnel"}, 400)
        used = {hub["my_ip"], hub["hub_ip"]} | {c["ip"] for c in db.q("SELECT ip FROM vpn_clients")}
        for p in itf["peers"]:
            used |= {a.split("/")[0] for a in p["allowed_ips"] if a.endswith("/32")}
        try:
            ip = vpn.next_free_ip(hub["network"], used)
            priv, pub = await asyncio.to_thread(vpn.genkeys)
        except RuntimeError as e:
            return JSONResponse({"error": str(e)}, 400)
        dns = hub["my_ip"] if b.get("dns") else ""      # AdGuard de la Pi por el túnel
        conf = vpn.client_config(priv, ip, hub, dns)
        vps_iface = re.sub(r"[^\w.-]", "", str(b.get("vps_iface") or itf["name"]))
        db.x("INSERT INTO vpn_clients (name, iface, ip, public_key, created) VALUES (?,?,?,?,?)",
             (name, itf["name"], ip, pub, time.time()))
        return JSONResponse({"name": name, "ip": ip, "public_key": pub, "config": conf,
                             "qr": vpn.qr_svg(conf), "vps_commands": vpn.vps_commands(vps_iface, name, pub, ip),
                             "filename": f"{re.sub(r'[^A-Za-z0-9_-]', '_', name)}.conf"})

    async def vpn_client_delete(request: Request):
        c = db.q("SELECT * FROM vpn_clients WHERE id=?", (int(request.path_params["cid"]),))
        if not c:
            return JSONResponse({"error": "no existe"}, 404)
        db.x("DELETE FROM vpn_clients WHERE id=?", (c[0]["id"],))
        return JSONResponse({"ok": True, "vps_commands": vpn.remove_commands(c[0]["iface"], c[0]["name"],
                                                                             c[0]["public_key"])})

    async def vpn_monitor(request: Request):
        """Agrega el VPS (detectado en el túnel) a Prometheus/Grafana y al ping."""
        import asyncio
        itf = await asyncio.to_thread(find_iface, str((await request.json()).get("iface", "")))
        if not itf or not itf["hub"]["hub_ip"]:
            return JSONResponse({"error": "No detecté la IP del VPS en ese túnel"}, 400)
        ip = itf["hub"]["hub_ip"]
        data, _ = st.read_yaml(cfg_path())
        nodes = [n for n in (st.yaml_get(data, "infrastructure.nodes") or []) if n.get("name") != "vps"]
        nodes.append({"name": "vps", "target": f"{ip}:9100"})
        st.yaml_set(data, "infrastructure.nodes", nodes)
        pings = [t for t in (st.yaml_get(data, "infrastructure.ping_targets") or []) if t.get("name") != "vps"]
        pings.append({"name": "vps", "host": ip})
        st.yaml_set(data, "infrastructure.ping_targets", pings)
        st.write_yaml(cfg_path(), data)
        try:
            st.request_apply(["netwatch", "bot"], ["netwatch", "bot"])
            applied = True
        except RuntimeError:
            applied = False
        return JSONResponse({"ok": True, "ip": ip, "applied": applied,
                             "vps_steps": f"En el VPS: VPN_VPS_IP={ip} en hosts/vps/.env y docker compose … up -d "
                                          f"(docs/vpn.md). Firewall: sudo ufw allow in on {itf['name']} from "
                                          f"{itf['hub']['my_ip']} to any port 9100 proto tcp"})

    # ------------------------------------------------ inventario, Wi-Fi y control parental
    async def inventory_get(_):
        try:
            trivy = json.loads((st.RUN_DIR / "trivy.json").read_text())
        except (OSError, ValueError):
            trivy = {}
        return JSONResponse({"trivy": trivy, "devices": await asyncio.to_thread(mon.inventory.devices),
                             "scan": mon.inventory.last_scan(),
                             "every_days": float((mon.cfg.get("inventory") or {}).get("every_days", 7))})

    async def inventory_scan(request: Request):
        b = await request.json() if request.headers.get("content-length", "0") != "0" else {}
        ips = [str(i) for i in (b.get("ips") or [])] or None
        if mon.inventory.running:
            return JSONResponse({"ok": True, "running": True, "msg": "Ya hay un escaneo en curso"})
        threading.Thread(target=mon.inventory.scan, args=(ips,), name="inventario", daemon=True).start()
        return JSONResponse({"ok": True, "running": True,
                             "msg": "Escaneando: tarda unos minutos por equipo, la página se actualiza sola"})

    async def inventory_export(_):
        import csv
        import io
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["equipo", "ip", "mac", "puerto", "proto", "servicio", "producto", "version", "cvss_max", "cves"])
        for d in await asyncio.to_thread(mon.inventory.devices):
            for sv in d["services"]:
                w.writerow([d["name"], d["ip"], d["mac"], sv["port"], sv["proto"], sv["service"], sv["product"],
                            sv["version"], sv["max_cvss"] or "", " ".join(c["id"] for c in sv["cves"])])
        stamp = time.strftime("%Y%m%d-%H%M")
        return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="inventario-{stamp}.csv"'})

    async def wifi_get(request: Request):
        from .wifi import vendor_of
        if request.query_params.get("rescan"):
            mon.wifi._at = 0
        return JSONResponse(await asyncio.to_thread(mon.wifi.summary, vendor_of))

    async def device_wifi(request: Request):
        mac = request.path_params["mac"].lower()
        b = await request.json()
        if not db.device(mac):
            return JSONResponse({"error": "No conozco ese equipo"}, 404)
        db.update_device(mac, wifi=str(b.get("ssid") or "")[:64])
        return JSONResponse({"ok": True})

    async def parental_get(_):
        try:
            services = await asyncio.to_thread(mon.parental.services) if mon.adguard.enabled else []
            err = "" if mon.adguard.enabled else "Necesita AdGuard (perfil dns) con ADGUARD_USER y ADGUARD_PASSWORD en .env"
        except Exception as e:  # noqa: BLE001
            services, err = [], f"No pude leer AdGuard: {e}"
        devs = [{"mac": d["mac"], "ip": d.get("ip"), "online": bool(d.get("online")),
                 "name": d.get("display_name") or d.get("name") or d.get("hostname") or d.get("vendor") or d["mac"]}
                for d in db.devices()]
        return JSONResponse({"rules": mon.parental.rules(), "services": services, "devices": devs, "error": err})

    async def parental_save(request: Request):
        b = await request.json()
        try:
            r = await asyncio.to_thread(mon.parental.save, b)
        except (ValueError, RuntimeError) as e:
            return JSONResponse({"error": str(e)}, 400)
        except Exception as e:  # noqa: BLE001 - AdGuard respondió mal
            return JSONResponse({"error": f"AdGuard no aceptó la regla: {e}"}, 502)
        return JSONResponse({"ok": True, "rule": r})

    async def parental_delete(request: Request):
        await asyncio.to_thread(mon.parental.delete, int(request.path_params["rid"]))
        return JSONResponse({"ok": True})

    # ------------------------------------------------ otros bots de Telegram (./hl bots add)
    from .bots import BotError

    def _bot(request: Request):
        return mon.bots.get(request.path_params["name"])

    async def bots_get(_):
        status = mon.bots.status()
        if time.time() - status.get("ts", 0) > 60 and mon.bots.all():
            with contextlib.suppress(RuntimeError):
                st.request_apply(action="bots-status")          # homelab-apply.sh lee systemd
        out = [await asyncio.to_thread(b.summary, status.get("bots") or {}) for b in mon.bots.all().values()]
        for b in out:
            b["broadcast"] = mon.bots.broadcasts.get(b["name"])
        return JSONResponse({"bots": out, "status_ts": status.get("ts", 0)})

    async def bot_action(request: Request):
        b, act = _bot(request), request.path_params["act"]
        if act not in ("start", "stop", "restart", "logs", "status"):
            return JSONResponse({"error": "Acción desconocida"}, 400)
        if act == "status":
            st.request_apply(action="bots-status")
        else:
            if act != "logs" and not b.unit:
                return JSONResponse({"error": "Este bot no tiene servicio de systemd (./hl bots add … --unit …)"}, 400)
            st.request_apply(action=f"bot-{act}:{b.name}")
            if act != "logs":
                db.add_event("info", "bots", f"Bot «{b.name}»: {dict(start='encender', stop='apagar', restart='reiniciar')[act]}"
                             " pedido desde el panel")
        return JSONResponse({"ok": True})

    async def bot_logs(request: Request):
        return JSONResponse(mon.bots.logs(_bot(request).name))

    async def bot_users(request: Request):
        b = _bot(request)
        try:
            rows = await asyncio.to_thread(b.users, request.query_params.get("q", "").strip()[:64])
        except BotError as e:
            return JSONResponse({"error": str(e)}, 400)
        return JSONResponse({"users": rows})

    async def bot_user_change(request: Request):
        b, uid = _bot(request), int(request.path_params["uid"])
        body = await request.json()
        days = body.get("days")
        try:
            msg = await asyncio.to_thread(b.change_user, uid, str(body.get("op")), str(body.get("type") or "premium"),
                                          int(days) if days not in (None, "", 0, "0") else None)
        except BotError as e:
            return JSONResponse({"error": str(e)}, 400)
        db.add_event("info", "bots", f"Bot «{b.name}»: usuario {uid} → {msg}")
        return JSONResponse({"ok": True, "msg": msg})

    async def bot_payments(request: Request):
        b = _bot(request)
        try:
            return JSONResponse({"payments": await asyncio.to_thread(b.payments)})
        except (BotError, sqlite3.Error) as e:
            return JSONResponse({"error": str(e)}, 400)

    async def bot_refund(request: Request):
        b = _bot(request)
        charge = str((await request.json()).get("charge_id") or "")
        try:
            msg = await asyncio.to_thread(b.refund, charge)
        except BotError as e:
            return JSONResponse({"error": str(e)}, 400)
        db.add_event("info", "bots", f"Bot «{b.name}»: {msg}")
        return JSONResponse({"ok": True, "msg": msg})

    async def bot_songs(request: Request):
        b, qp = _bot(request), request.query_params
        try:
            return JSONResponse(await asyncio.to_thread(b.songs, qp.get("q", "").strip()[:100], qp.get("sort", "recent"),
                                                        min(int(qp.get("limit", 300)), 1000), int(qp.get("offset", 0))))
        except (BotError, sqlite3.Error) as e:
            return JSONResponse({"error": str(e)}, 400)

    async def bot_songs_delete(request: Request):
        b, body = _bot(request), await request.json()
        try:
            r = await asyncio.to_thread(b.delete_songs, body.get("ids") or [], body.get("orphans") or [])
        except (BotError, sqlite3.Error) as e:
            return JSONResponse({"error": str(e)}, 400)
        db.add_event("info", "bots", f"Bot «{b.name}»: {r['removed']} canción(es) borradas, {r['freed_mb']} MB liberados")
        return JSONResponse({"ok": True, **r})

    async def bot_message(request: Request):
        b = _bot(request)
        body = await request.json()
        text = str(body.get("text") or "").strip()
        if not text or len(text) > 4000:
            return JSONResponse({"error": "Escribe el mensaje (máximo 4000 letras)"}, 400)
        to = body.get("to")
        try:
            if to == "all":
                job = mon.bots.broadcast(b.name, text)
                return JSONResponse({"ok": True, "msg": f"Enviando a {job['total']} usuarios…"})
            await asyncio.to_thread(b.api, "sendMessage", chat_id=int(to), text=text)
        except (BotError, ValueError, TypeError) as e:
            return JSONResponse({"error": str(e) or "Destino inválido"}, 400)
        return JSONResponse({"ok": True, "msg": "Enviado"})

    # ------------------------------------------------ contraseñas de servicios (scripts/passwords.sh)
    PW_SERVICES = [
        ("grafana", "Grafana", "📈", "usuario admin", False),
        ("adguard", "AdGuard Home", "🛡", "el usuario de ADGUARD_USER", True),
        ("vaultwarden-admin", "Vaultwarden /admin", "🔐", "solo el token de administración; no toca las bóvedas", False),
        ("speedtest", "Velocidad del internet", "🚀", "Speedtest Tracker: tu correo (si no lo dices, el primero)", True),
        ("uptime-kuma", "Uptime Kuma", "🟢", "el usuario que digas (o el primero)", True),
        ("ntopng", "ntopng", "📶", "vuelve a admin/admin y te pide una nueva al entrar", False),
        ("mealie", "Recetas y súper (Mealie)", "🍲", "correo o usuario (si no lo dices, el administrador)", True),
        ("homeassistant", "Home Assistant", "🏠", "un usuario (si no dices cuál, el primero)", True),
        ("paperless", "Paperless", "📄", "PAPERLESS_ADMIN_USER o el que digas", True),
        ("syncthing", "Syncthing", "🔄", "acceso a su página (usuario admin)", False),
        ("umami", "Umami", "📊", "usuario admin o el que digas", True),
        ("immich", "Immich", "📷", "el administrador; Immich genera la contraseña", False),
        ("linux", "La Raspberry (tu usuario de Linux)", "🍓", "la de sudo y la de la pantalla; tu SSH con llave no cambia", True),
    ]

    def _pw_file(svc: str):
        return st.RUN_DIR / "passwords" / f"{svc}.json"

    async def passwords_list(_):
        return JSONResponse({"services": [dict(key=k, name=n, icon=i, desc=d, user=u) for k, n, i, d, u in PW_SERVICES]})

    async def passwords_reset(request: Request):
        b = await request.json()
        svc, usr = str(b.get("service") or ""), str(b.get("user") or "").strip()
        if svc not in {k for k, *_ in PW_SERVICES}:
            return JSONResponse({"error": "Servicio desconocido"}, 400)
        if usr and not re.fullmatch(r"[A-Za-z0-9@._-]{1,64}", usr):
            return JSONResponse({"error": "Usuario inválido"}, 400)
        with contextlib.suppress(OSError):
            _pw_file(svc).unlink()
        st.request_apply(action=f"password-reset:{svc}" + (f":{usr}" if usr else ""))
        db.add_event("info", "security", f"Contraseña de {svc} restablecida desde el panel")
        return JSONResponse({"ok": True})

    async def passwords_result(request: Request):
        f = _pw_file(request.path_params["svc"])
        if not re.fullmatch(r"[a-z-]{2,30}", request.path_params["svc"]) or not f.exists():
            return JSONResponse({"pending": True})
        try:
            data = json.loads(f.read_text())
        finally:
            with contextlib.suppress(OSError):
                f.unlink()                     # se muestra una sola vez
        return JSONResponse(data, headers={"Cache-Control": "no-store"})

    routes = [
        Route("/", index), Route("/health", health), Route("/metrics", prom_metrics),
        Route("/api/summary", summary), Route("/api/devices", devices),
        Route("/api/devices/known", mark_all_known, methods=["POST"]),
        Route("/api/devices/{mac}", device_update, methods=["PATCH"]),
        Route("/api/devices/{mac}", device_delete, methods=["DELETE"]),
        Route("/api/devices/{mac}/rescan", device_rescan, methods=["POST"]),
        Route("/api/events", events), Route("/api/events/export", events_export),
        Route("/api/export/all", export_all),
        Route("/api/events/clear", events_clear, methods=["POST"]),
        Route("/api/events/{eid:int}/fp", event_fp, methods=["POST", "DELETE"]),
        Route("/api/suppress", suppress_list), Route("/api/suppress/{rid:int}", suppress_list, methods=["DELETE"]),
        Route("/api/acl", acl_list),
        Route("/api/acl", acl_add, methods=["POST"]),
        Route("/api/acl/{rid:int}", acl_delete, methods=["DELETE"]),
        Route("/api/traffic", traffic), Route("/api/totals", totals),
        Route("/api/by-ap", by_ap), Route("/api/topology", topology),
        Route("/api/links", links), Route("/api/cameras", cameras),
        Route("/api/inbound", inbound), Route("/api/inbound/{ip}", inbound_ip),
        Route("/api/lookup", lookup, methods=["POST"]), Route("/api/dns", dns),
        Route("/api/tools", tools), Route("/api/settings", settings_get),
        Route("/api/settings", settings_save, methods=["POST"]),
        Route("/api/settings/yaml", settings_yaml, methods=["POST"]),
        Route("/api/settings/token", settings_token, methods=["POST"]),
        Route("/api/apply", apply_post, methods=["POST"]), Route("/api/apply", apply_get),
        Route("/auth/check", auth_check), Route("/login", login_get),
        Route("/auth/login", login_post, methods=["POST"]),
        Route("/auth/logout", logout, methods=["GET", "POST"]), Route("/api/me", me),
        Route("/api/me/password", me_password, methods=["POST"]),
        Route("/api/users", users_list), Route("/api/users", users_save, methods=["POST"]),
        Route("/api/users/logins", logins),
        Route("/api/users/{name}", users_delete, methods=["DELETE"]),
        Route("/api/services", services_status),
        Route("/api/wol", wol_list), Route("/api/wol", wol_send, methods=["POST"]),
        Route("/api/cameras/probe", camera_probe, methods=["POST"]),
        Route("/api/home/toggle", home_toggle, methods=["POST"]),
        Route("/api/inventory", inventory_get), Route("/api/inventory/scan", inventory_scan, methods=["POST"]),
        Route("/api/inventory/export", inventory_export),
        Route("/api/wifi", wifi_get), Route("/api/devices/{mac}/wifi", device_wifi, methods=["POST"]),
        Route("/api/parental", parental_get), Route("/api/parental", parental_save, methods=["POST"]),
        Route("/api/parental/{rid:int}", parental_delete, methods=["DELETE"]),
        Route("/api/passwords", passwords_list), Route("/api/passwords/reset", passwords_reset, methods=["POST"]),
        Route("/api/passwords/result/{svc}", passwords_result),
        Route("/api/bots", bots_get), Route("/api/bots/{name}/do/{act}", bot_action, methods=["POST"]),
        Route("/api/bots/{name}/logs", bot_logs), Route("/api/bots/{name}/users", bot_users),
        Route("/api/bots/{name}/users/{uid:int}", bot_user_change, methods=["POST"]),
        Route("/api/bots/{name}/payments", bot_payments), Route("/api/bots/{name}/refund", bot_refund, methods=["POST"]),
        Route("/api/bots/{name}/message", bot_message, methods=["POST"]),
        Route("/api/bots/{name}/songs", bot_songs), Route("/api/bots/{name}/songs/delete", bot_songs_delete, methods=["POST"]),
        Route("/api/system", system_get), Route("/api/system/{action}", system_action, methods=["POST"]),
        Route("/ca.crt", ca_cert),
        Route("/api/vpn", vpn_get), Route("/api/vpn/client", vpn_client, methods=["POST"]),
        Route("/api/vpn/client/{cid:int}", vpn_client_delete, methods=["DELETE"]),
        Route("/api/vpn/monitor", vpn_monitor, methods=["POST"]),
        Route("/api/cameras/{name}/snapshot", camera_snapshot),
        Mount("/static", StaticFiles(directory=STATIC), name="static"),
    ]
    async def bad_request(_request, exc):
        return JSONResponse({"error": f"Petición inválida: {exc}"}, 400)

    routes[1:1] = respaldos.routes(db, mon)
    async def bot_error(_request, exc):
        return JSONResponse({"error": str(exc)}, 400)

    async def runtime_error(_request, exc):          # p. ej. falta run/ (servicio de aplicar)
        return JSONResponse({"error": str(exc)}, 503)

    return Starlette(routes=routes, exception_handlers={ValueError: bad_request, BotError: bot_error,
                                                        RuntimeError: runtime_error})
