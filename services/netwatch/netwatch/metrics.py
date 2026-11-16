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
              ap=d["ap"] or "sin asignar"), int(d["online"])) for d in devs])
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
    return "\n".join(out) + "\n"
