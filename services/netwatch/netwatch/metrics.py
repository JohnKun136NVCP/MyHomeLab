"""Métricas en formato de texto de Prometheus (sin dependencias)."""
from __future__ import annotations

from .monitor import Monitor


def _esc(v) -> str:
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def _labels(**kw) -> str:
    return "{" + ",".join(f'{k}="{_esc(v)}"' for k, v in kw.items()) + "}"


def render(mon: Monitor) -> str:
    db = mon.db
    devs = db.devices()
    out: list[str] = []

    def metric(name: str, typ: str, help_: str, rows):
        out.append(f"# HELP {name} {help_}")
        out.append(f"# TYPE {name} {typ}")
        for labels, val in rows:
            out.append(f"{name}{_labels(**labels) if labels else ''} {val}")

    metric("homelab_devices_total", "gauge", "Dispositivos conocidos", [({}, len(devs))])
    metric("homelab_devices_online", "gauge", "Dispositivos en línea",
           [({}, sum(1 for d in devs if d["online"]))])
    metric("homelab_device_up", "gauge", "1 si el dispositivo está en línea", [
        (dict(mac=d["mac"], ip=d["ip"] or "", name=d["display_name"], type=d["display_type"],
              ap=d["ap"] or "sin asignar", network=d.get("network") or mon.home), int(d["online"]))
        for d in devs])
    metric("homelab_network_last_scan_timestamp_seconds", "gauge", "Último escaneo de cada red", [
        (dict(network=n), st.get("last_scan", 0)) for n, st in mon.net_status.items()])
    metric("homelab_network_devices", "gauge", "Dispositivos vistos en el último escaneo de cada red", [
        (dict(network=n), st.get("devices", 0)) for n, st in mon.net_status.items()])
    metric("homelab_device_bytes_total", "counter", "Bytes por dispositivo (modo puente)", [
        (dict(mac=mac, name=next((d["display_name"] for d in devs if d["mac"] == mac), mac),
              direction=direction), c[direction])
        for mac, c in mon.counters.items() for direction in ("up", "down")])
    metric("homelab_infra_up", "gauge", "1 si el módem/AP responde ping", [
        (dict(name=n, ip=s["ip"], role=s["role"]), int(s["up"])) for n, s in mon.infra_status.items()])
    metric("homelab_infra_latency_seconds", "gauge", "Latencia a módem/AP", [
        (dict(name=n, ip=s["ip"], role=s["role"]), s["latency"])
        for n, s in mon.infra_status.items() if s["latency"] is not None])
    metric("homelab_target_up", "gauge", "1 si el destino responde ping", [
        (dict(name=n, host=s["host"]), int(s["up"])) for n, s in mon.target_status.items()])
    metric("homelab_target_latency_seconds", "gauge", "Latencia a destinos externos", [
        (dict(name=n, host=s["host"]), s["latency"])
        for n, s in mon.target_status.items() if s["latency"] is not None])
    metric("homelab_events_total", "counter", "Eventos anómalos registrados por tipo", [
        (dict(kind=k), n) for k, n in db.event_counts().items()])
    metric("homelab_acl_blocked", "gauge", "Reglas de bloqueo", [({}, len(db.acl()))])
    metric("homelab_ids_alerts_total", "counter", "Alertas de Suricata por severidad", [
        (dict(severity=s), n) for s, n in mon.ids_alerts.items()])
    metric("homelab_last_scan_timestamp_seconds", "gauge", "Último escaneo", [({}, mon.last_scan)])
    ups = mon.ups.status() if mon.ups.enabled else {}
    if ups.get("ok"):
        metric("homelab_ups_on_battery", "gauge", "1 si el no-break está usando batería", [({}, int(ups["on_battery"]))])
        for k, help_ in (("charge", "Carga de la batería (%)"), ("runtime", "Segundos de batería que quedan"),
                         ("load", "Carga conectada (%)"), ("input_voltage", "Voltaje de entrada")):
            if k in ups:
                metric(f"homelab_ups_{k}", "gauge", help_, [({}, ups[k])])
    from . import vpn
    s = vpn.status()
    rows_up, rows_hs, rows_rx, rows_tx = [], [], [], []
    names = {c["public_key"]: c["name"] for c in db.q("SELECT name, public_key FROM vpn_clients")}
    for itf in s["interfaces"]:
        rows_up.append((dict(iface=itf["name"]), 1))
        for p in itf["peers"]:
            peer = "VPS" if p["public_key"] == itf["hub"]["hub_key"] else names.get(p["public_key"], p["public_key"][:8])
            lab = dict(iface=itf["name"], peer=peer, endpoint=p["endpoint"])
            if p["handshake"]:
                rows_hs.append((lab, p["handshake"]))
            rows_rx.append((lab, p["rx"]))
            rows_tx.append((lab, p["tx"]))
    # alerts.mute de homelab.yml: nodos/peers cuyas alertas de «caído» no se mandan (ej. el VPS)
    mute = [str(m).lower() for m in ((mon.cfg.get("alerts") or {}).get("mute") or []) if str(m).strip()]
    nodes = [n.get("name", "") for n in (mon.cfg.get("infrastructure", {}) or {}).get("nodes") or []]
    cands = {*nodes, *(r[0]["peer"] for r in rows_hs), *mon.infra_status, *mute}
    muted = sorted({c for c in cands if c and any(m in c.lower() for m in mute)})
    metric("homelab_alert_muted", "gauge", "1 = no avisar si este nodo/peer se cae (alerts.mute)",
           [(dict(name=m), 1) for m in muted])
    metric("homelab_wg_interface_up", "gauge", "Interfaces WireGuard activas", rows_up)
    metric("homelab_wg_peer_last_handshake_seconds", "gauge", "Último contacto con cada peer (epoch)", rows_hs)
    metric("homelab_wg_peer_receive_bytes_total", "counter", "Bytes recibidos por peer", rows_rx)
    metric("homelab_wg_peer_transmit_bytes_total", "counter", "Bytes enviados por peer", rows_tx)
    day = db.inbound_hourly(__import__("time").time() - 86400)
    tot: dict[str, int] = {}
    for r in day:
        tot[r["action"]] = tot.get(r["action"], 0) + r["n"]
    metric("homelab_inbound_24h", "gauge", "Conexiones entrantes a la Pi en 24 h por acción", [
        (dict(action=a), n) for a, n in tot.items()])
    ag = mon.adguard
    if ag.enabled and ag.stats:
        metric("homelab_dns_queries", "gauge", "Consultas DNS (periodo de AdGuard)",
               [({}, ag.stats.get("num_dns_queries", 0))])
        metric("homelab_dns_blocked", "gauge", "Consultas bloqueadas por filtros",
               [({}, ag.stats.get("num_blocked_filtering", 0))])
        metric("homelab_dns_malware", "gauge", "Bloqueos de malware/phishing",
               [({}, ag.stats.get("num_replaced_safebrowsing", 0))])
        names_by_ip = {d["ip"]: d["display_name"] for d in devs}
        metric("homelab_dns_client_queries", "gauge", "Consultas por cliente (top de AdGuard)", [
            (dict(ip=ip, name=names_by_ip.get(ip) or ag.names.get(ip, ip)), n) for ip, n in ag.top_clients()])
    return "\n".join(out) + "\n"
