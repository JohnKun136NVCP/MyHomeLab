#!/usr/bin/env python3
"""Genera los tableros de Grafana (config/grafana/dashboards/*.json).

Edita aquí y vuelve a correrlo:  python3 scripts/gen_dashboards.py
"""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "config" / "grafana" / "dashboards"
DS = {"type": "prometheus", "uid": "prometheus"}
_id = 0


def nid():
    global _id
    _id += 1
    return _id


def stat(title, expr, x, y, w=4, h=4, unit="none", legend="", thresholds=None):
    return {"id": nid(), "type": "stat", "title": title, "datasource": DS,
            "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "fieldConfig": {"defaults": {"unit": unit, "thresholds": thresholds or {
                "mode": "absolute", "steps": [{"color": "green", "value": None}]}},
                "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"]}, "colorMode": "background",
                        "graphMode": "area"},
            "targets": [{"refId": "A", "expr": expr, "legendFormat": legend, "datasource": DS}]}


def ts(title, targets, x, y, w=12, h=8, unit="bps", stack=False):
    return {"id": nid(), "type": "timeseries", "title": title, "datasource": DS,
            "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "fieldConfig": {"defaults": {"unit": unit, "custom": {
                "fillOpacity": 15, "lineWidth": 2, "showPoints": "never",
                "stacking": {"mode": "normal" if stack else "none"}}}, "overrides": []},
            "options": {"legend": {"displayMode": "table", "placement": "bottom",
                                   "calcs": ["mean", "max"]}, "tooltip": {"mode": "multi"}},
            "targets": [{"refId": chr(65 + i), "expr": e, "legendFormat": l, "datasource": DS}
                        for i, (e, l) in enumerate(targets)]}


def table(title, expr, x, y, w=12, h=8):
    return {"id": nid(), "type": "table", "title": title, "datasource": DS,
            "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "targets": [{"refId": "A", "expr": expr, "format": "table", "instant": True,
                         "datasource": DS}],
            "transformations": [{"id": "organize", "options": {
                "excludeByName": {"Time": True, "__name__": True, "job": True, "instance": True}}}]}


def dashboard(uid, title, panels, variables=()):
    return {"uid": uid, "title": title, "tags": ["homelab"], "timezone": "browser",
            "schemaVersion": 39, "refresh": "1m", "time": {"from": "now-24h", "to": "now"},
            "templating": {"list": list(variables)}, "panels": panels}


RED = {"mode": "absolute", "steps": [{"color": "red", "value": None}, {"color": "green", "value": 1}]}

# ------------------------------------------------------------------ Red de casa
red = dashboard("homelab-red", "Red de casa", [
    stat("Dispositivos en línea", "homelab_devices_online", 0, 0),
    stat("Dispositivos conocidos", "homelab_devices_total", 4, 0),
    stat("Internet", 'homelab_target_up{name="internet"}', 8, 0, thresholds=RED),
    stat("Latencia internet", 'homelab_target_latency_seconds{name="internet"}', 12, 0, unit="s"),
    stat("Eventos anómalos (24 h)", "sum(increase(homelab_events_total[24h]))", 16, 0,
         thresholds={"mode": "absolute", "steps": [{"color": "green", "value": None},
                                                   {"color": "orange", "value": 1},
                                                   {"color": "red", "value": 10}]}),
    stat("Bloqueados (ACL)", "homelab_acl_blocked", 20, 0),
    ts("Tráfico de la casa (interfaz de la Pi)", [
        ('homelab:iface_rx_bps:rate10m{instance="raspberry"}', "bajada {{device}}"),
        ('homelab:iface_tx_bps:rate10m{instance="raspberry"}', "subida {{device}}")], 0, 4, 24),
    ts("Top 10 dispositivos por tráfico (modo puente)", [
        ('topk(10, sum by (name) (homelab:device_bps:rate10m))', "{{name}}")], 0, 12, 12, stack=True),
    ts("Dispositivos en línea por AP", [
        ("sum by (ap) (homelab_device_up)", "{{ap}}")], 12, 12, 12, unit="none"),
    ts("Latencia (ping)", [("homelab_target_latency_seconds", "{{name}}"),
                           ("homelab_infra_latency_seconds", "{{name}}")], 0, 20, 12, unit="s"),
    ts("Dispositivos por tipo", [("sum by (type) (homelab_device_up)", "{{type}}")],
       12, 20, 12, unit="none", stack=True),
    table("Infraestructura (1 = responde)", "homelab_infra_up", 0, 28, 12),
    ts("Alertas IDS (Suricata)", [("sum by (severity) (increase(homelab_ids_alerts_total[10m]))",
                                   "severidad {{severity}}")], 12, 28, 12, unit="none"),
    ts("Conexiones entrantes a la Pi (24 h)", [("homelab_inbound_24h", "{{action}}")], 0, 36, 12, unit="none"),
    ts("DNS: consultas y bloqueos (AdGuard)", [("homelab_dns_queries", "consultas"),
                                               ("homelab_dns_blocked", "bloqueadas"),
                                               ("homelab_dns_malware", "malware/phishing")], 12, 36, 12, unit="none"),
    table("DNS: equipos que más consultan", "topk(10, homelab_dns_client_queries)", 0, 44, 12),
    ts("CrowdSec (VPS): IPs bloqueadas", [("sum by (instance) (cs_active_decisions)", "{{instance}}")],
       12, 44, 12, unit="none"),
])

# ------------------------------------------------------------------ Nodos
inst = {"name": "instance", "type": "query", "datasource": DS, "refresh": 1,
        "query": "label_values(node_uname_info, instance)", "multi": True, "includeAll": True,
        "current": {"text": "All", "value": "$__all"}}
F = 'instance=~"$instance"'
nodos = dashboard("homelab-nodos", "Nodos: Raspberry, PC y VPS", [
    stat("Arriba", f'up{{job="node",{F}}}', 0, 0, 6, legend="{{instance}}", thresholds=RED),
    stat("Uptime", f'time() - node_boot_time_seconds{{{F}}}', 6, 0, 6, unit="s", legend="{{instance}}"),
    stat("Temp. Raspberry", 'node_thermal_zone_temp{instance="raspberry"}', 12, 0, 6, unit="celsius"),
    stat("RAM usada", f'100 * (1 - node_memory_MemAvailable_bytes{{{F}}} / node_memory_MemTotal_bytes{{{F}}})',
         18, 0, 6, unit="percent", legend="{{instance}}"),
    ts("CPU", [(f'100 * (1 - avg by (instance) (rate(node_cpu_seconds_total{{mode="idle",{F}}}[5m])))',
                "{{instance}}")], 0, 4, 12, unit="percent"),
    ts("Disco usado", [(f'100 * (1 - node_filesystem_avail_bytes{{fstype!~"tmpfs|overlay|squashfs",{F}}} '
                        f'/ node_filesystem_size_bytes{{fstype!~"tmpfs|overlay|squashfs",{F}}})',
                        "{{instance}} {{mountpoint}}")], 12, 4, 12, unit="percent"),
    ts("Tráfico de red (bajada)", [(f'homelab:iface_rx_bps:rate10m{{{F}}}', "{{instance}} {{device}}")],
       0, 12, 12),
    ts("Tráfico de red (subida)", [(f'homelab:iface_tx_bps:rate10m{{{F}}}', "{{instance}} {{device}}")],
       12, 12, 12),
    ts("Tráfico del VPS (total del día)", [
        ('increase(node_network_receive_bytes_total{instance="vps",device!~"lo|veth.*|docker.*|br-.*"}[24h])', "bajada {{device}}"),
        ('increase(node_network_transmit_bytes_total{instance="vps",device!~"lo|veth.*|docker.*|br-.*"}[24h])', "subida {{device}}")],
       0, 20, 24, unit="bytes"),
], variables=[inst])

OUT.mkdir(parents=True, exist_ok=True)
for d in (red, nodos):
    (OUT / f"{d['uid']}.json").write_text(json.dumps(d, indent=2, ensure_ascii=False))
    print("escrito", d["uid"])
