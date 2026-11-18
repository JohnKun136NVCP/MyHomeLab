"""Ciclo principal: escaneo, detección de anomalías, ACL, pings y avisos."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path

from homelab_common import http_json

from . import acl, names, roam, scanner
from .adguard import AdGuard, upstream_urls
from .inbound import Inbound, service_name
from .classify import classify
from .db import DB

log = logging.getLogger("netwatch.monitor")


class Monitor:
    def __init__(self, cfg: dict, db: DB, data: Path):
        self.cfg = cfg
        self.db = db
        self.data = data
        net = cfg.get("network", {})
        self.bridge_mode = bool(net.get("bridge_mode"))
        self.iface = net.get("bridge_interface", "br0") if self.bridge_mode else net.get("interface", "eth0")
        self.interval = int(net.get("scan_interval", 120))
        # Varias redes (SSIDs). Sin lista = una sola red con cidr/interface de siempre.
        nets = net.get("networks") or [{"name": "casa", "cidr": net.get("cidr", "192.168.0.0/24")}]
        self.networks = [{"interface": self.iface, **n} for n in nets]
        r = net.get("roaming", {}) or {}
        self.home = r.get("home") or self.networks[0]["name"]
        self.roaming = bool(r.get("enabled")) and len(self.networks) > 1 and not self.bridge_mode
        self.hop_every = int(r.get("hop_every", 1800))
        self.dwell = int(r.get("dwell", 20))
        self.current = self.home
        self.last_hop = 0.0
        self.net_status: dict[str, dict] = {n["name"]: {"last_scan": 0, "error": "", "devices": 0}
                                            for n in self.networks}
        self.infra = self._infra_list()
        self.infra_status: dict[str, dict] = {}      # nombre -> {up, latency, ip, role}
        self.target_status: dict[str, dict] = {}
        self.counters: dict[str, dict[str, int]] = {}
        self.ids_alerts: dict[str, int] = {}
        self.last_scan = 0.0
        self.scan_error = ""
        self._acl_hash = ""
        self._eve_pos = 0
        from .nut import Ups
        self.ups = Ups()
        from .sso import Sync
        cfg_file = Path(os.environ.get("HOMELAB_CONFIG", "/homelab/config/homelab.yml"))
        self.sso = Sync(db, cfg_file.parent, self.send_text)
        self._last_daily_scan = ""
        self._infra_mac: dict[str, str] = {}
        self.stop = threading.Event()
        self.inbound = Inbound(db, os.environ.get("UFW_LOG", "/hostlog/ufw.log"),
                               os.environ.get("CANARY_LOG", "/honeypot/opencanary.log"))
        dns = cfg.get("dns", {}) or {}
        self.adguard = AdGuard(dns.get("adguard_url") if "adguard_url" in dns else None)
        self.dns_upstream = upstream_urls(dns.get("upstream", ""), dns.get("controld_resolver", ""))
        self._upstream_checked = 0.0
        self.upstream_msg = ""
        self._name_budget = int(net.get("names_per_cycle", 8))
        self.write_prom_targets()

    # ------------------------------------------------------------ config
    def _infra_list(self) -> list[dict]:
        inf = self.cfg.get("infrastructure", {})
        out = []
        m = inf.get("modem")
        if m:
            out.append({**m, "role": "modem"})
        for ap in inf.get("access_points", []) or []:
            out.append({**ap, "role": "ap"})
        return out

    def reachable(self) -> set[str]:
        """Redes que la Pi ve ahora: todas si cada una tiene su interfaz (sin roaming),
        o solo la red donde está la Wi-Fi si va brincando entre SSIDs."""
        return {self.current} if self.roaming else {n["name"] for n in self.networks}

    def scan_all(self) -> None:
        for name in sorted(self.reachable(), key=lambda n: n != self.home):
            self.scan(name)

    def net(self, name: str | None = None) -> dict:
        name = name or self.current
        return next((n for n in self.networks if n["name"] == name), self.networks[0])

    @property
    def cidr(self) -> str:
        return self.net()["cidr"]

    def infra_ips(self, network: str | None = None) -> dict[str, dict]:
        """IP → equipo de infraestructura. Con `network` solo los alcanzables desde esa red
        (su IP en esa red, o su IP WAN si el equipo es router de otra red)."""
        out = {}
        for i in self.infra:
            inet = i.get("network") or self.home
            if i.get("ip") and (network is None or inet == network):
                out[i["ip"]] = i
            if i.get("wan_ip") and (network is None or network == self.home):
                out[i["wan_ip"]] = {**i, "name": f"{i['name']} (WAN)"}
        return out

    def write_prom_targets(self) -> None:
        """Lista de nodos para Prometheus (file_sd) a partir de homelab.yml."""
        inf = self.cfg.get("infrastructure", {}) or {}
        d = self.data / "prom-targets"
        d.mkdir(parents=True, exist_ok=True)
        for fname, key in (("nodes.json", "nodes"), ("crowdsec.json", "crowdsec")):
            sd = [{"targets": [n["target"]], "labels": {"instance": n["name"]}} for n in inf.get(key) or []]
            tmp = d / (fname + ".tmp")
            tmp.write_text(json.dumps(sd, indent=2))
            tmp.replace(d / fname)

    # ------------------------------------------------------------- ciclo
    def run_forever(self) -> None:
        log.info("netwatch: redes %s por %s (modo puente: %s, roaming: %s)",
                 [n["name"] for n in self.networks], self.iface, self.bridge_mode, self.roaming)
        while not self.stop.is_set():
            t0 = time.time()
            for step in (self.scan_all, self.ping_all, self.sync_acl, self.read_ids, self.collect_inbound,
                         self.poll_dns, self.resolve_names, self.daily_port_scan, self.notify,
                         self.sync_sso, self.maybe_roam):
                try:
                    step()
                except Exception:  # noqa: BLE001 - que un paso no tumbe a los demás
                    log.exception("Error en %s", step.__name__)
            self.stop.wait(max(5, self.interval - (time.time() - t0)))

    # ---------------------------------------------------------- escaneo
    def scan(self, network: str | None = None) -> None:
        net = self.net(network)
        hosts = scanner.discover(net["cidr"], net.get("interface", self.iface))
        if not hosts:
            self.scan_error = f"El escaneo de «{net['name']}» no encontró nada (¿arp-scan/nmap? ¿interfaz?)"
            self.net_status[net["name"]]["error"] = self.scan_error
            log.warning(self.scan_error)
            return
        self.scan_error = ""
        infra = self.infra_ips(net["name"])
        seen = set()
        anomalies = {**self.cfg.get("anomalies", {}), **{k: net[k] for k in
                                                         ("notify_new_devices",) if k in net}}
        blocked = {r["mac"] for r in self.db.acl()}
        for h in hosts:
            if not scanner.in_network(h.ip, net["cidr"]):
                continue
            seen.add(h.mac)
            is_new = self.db.upsert_seen(h.mac, h.ip, h.vendor, h.hostname, h.random_mac, net["name"])
            dev = self.db.device(h.mac)
            is_infra = h.ip in infra
            if is_infra and not dev.get("name") and \
                    not any(d["name"] == infra[h.ip]["name"] for d in self.db.devices()):
                self.db.update_device(h.mac, name=infra[h.ip]["name"], known=1)
            if is_new:
                ports = []
                if self.cfg.get("network", {}).get("port_scan_new_devices", True) and not is_infra:
                    ports = scanner.port_scan(h.ip)
                t = classify(h.vendor, h.hostname, ports, h.random_mac, is_infra)
                self.db.update_device(h.mac, type=t, ports=ports, last_port_scan=time.time())
                if not is_infra and anomalies.get("notify_new_devices", True):
                    self.db.add_event(
                        "warning", "new_device",
                        f"Dispositivo nuevo en {net['name']}: {h.hostname or h.vendor or h.mac}",
                        f"IP {h.ip} · MAC {h.mac} · {h.vendor or 'fabricante desconocido'} · tipo: {t}"
                        + (" · MAC privada" if h.random_mac else "")
                        + (f" · puertos: {', '.join(ports)}" if ports else ""), h.mac)
                if anomalies.get("quarantine_unknown") and self.bridge_mode and not is_infra:
                    self.db.add_acl(h.mac, note="cuarentena automática (dispositivo nuevo)")
                    self.db.add_event("warning", "quarantine", f"En cuarentena: {h.mac}",
                                      "Se bloqueó en automático. Desbloquéalo en la web (ACL).", h.mac)
            elif not dev.get("type"):
                self.db.update_device(h.mac, type=classify(h.vendor, h.hostname, dev["ports"],
                                                           h.random_mac, is_infra))
            if h.mac in blocked and not self.db.recent_event("blocked_seen", h.mac, 3600):
                self.db.add_event("warning", "blocked_seen",
                                  f"Dispositivo bloqueado conectado: {dev.get('display_name') or h.mac}",
                                  f"IP {h.ip}" + ("" if self.bridge_mode else
                                                  " (sin modo puente no se puede cortar su tráfico)"),
                                  h.mac)
        self.detect_arp_spoof(hosts, infra)
        self.db.mark_offline(seen, net["name"], include_unlabeled=net["name"] == self.home)
        self.last_scan = time.time()
        self.net_status[net["name"]] = {"last_scan": self.last_scan, "error": "", "devices": len(seen)}

    def detect_arp_spoof(self, hosts: list[scanner.Host], infra: dict | None = None) -> None:
        """Alerta si la IP del módem (u otro equipo de infraestructura) cambia de MAC
        o si una sola MAC responde por varias IPs de infraestructura."""
        infra = self.infra_ips(self.current) if infra is None else infra
        by_ip: dict[str, list[str]] = {}
        for h in hosts:
            by_ip.setdefault(h.ip, []).append(h.mac)
        for ip, item in infra.items():
            macs = by_ip.get(ip, [])
            if len(set(macs)) > 1:
                self._spoof_event(ip, item["name"], f"Varias MAC responden por {ip}: {', '.join(macs)}")
            if not macs:
                continue
            prev = self._infra_mac.get(ip)
            if prev is None:  # primera vez desde que arrancó: lo último que guardó la base
                hist = [m for m in self.db.macs_for_ip(ip, time.time() - 30 * 86400) if m != macs[0]]
                prev = macs[0] if not hist else None
            if prev and prev != macs[0]:
                self._spoof_event(ip, item["name"],
                                  f"{item['name']} ({ip}) cambió de MAC: antes {prev}, "
                                  f"ahora {macs[0]}. Si no cambiaste el equipo puede ser ARP spoofing.",
                                  macs[0])
            self._infra_mac[ip] = macs[0]
        mac_ips: dict[str, set] = {}
        for h in hosts:
            if h.ip in infra:
                mac_ips.setdefault(h.mac, set()).add(h.ip)
        for mac, ips in mac_ips.items():
            if len(ips) > 1:
                self._spoof_event(",".join(sorted(ips)), "infraestructura",
                                  f"La MAC {mac} responde por varias IPs de infraestructura: "
                                  f"{', '.join(sorted(ips))}", mac)

    def _spoof_event(self, ip: str, name: str, detail: str, mac: str = "") -> None:
        if not self.db.recent_event("arp_spoof", mac or ip, 6 * 3600):
            self.db.add_event("critical", "arp_spoof", f"Posible ARP spoofing en {name}", detail, mac or ip)

    def daily_port_scan(self) -> None:
        when = self.cfg.get("network", {}).get("daily_port_scan")
        today = dt.date.today().isoformat()
        if not when or self._last_daily_scan == today or dt.datetime.now().strftime("%H:%M") < when:
            return
        self._last_daily_scan = today
        for d in self.db.devices():
            if not d["online"] or d["ip"] in self.infra_ips() or \
                    (d.get("network") or self.home) not in self.reachable():
                continue
            ports = scanner.port_scan(d["ip"])
            old, new = set(d["ports"]), set(ports)
            if d["last_port_scan"] and new - old:
                self.db.add_event("warning", "ports_changed",
                                  f"{d['display_name']} abrió puertos nuevos",
                                  f"Nuevos: {', '.join(sorted(new - old))}. Antes: "
                                  f"{', '.join(sorted(old)) or 'ninguno'}", d["mac"])
            self.db.update_device(d["mac"], ports=ports, last_port_scan=time.time())

    # ------------------------------------------------------------ pings
    def ping_all(self) -> None:
        for i in self.infra:
            # Desde otra red solo se le puede hacer ping por su IP WAN (si la tiene)
            ip = i["ip"] if (i.get("network") or self.home) in self.reachable() else i.get("wan_ip")
            if not ip:
                continue
            lat = scanner.ping(ip)
            prev = self.infra_status.get(i["name"], {}).get("up", True)
            self.infra_status[i["name"]] = {"up": lat is not None, "latency": lat, "ip": i["ip"],
                                            "role": i["role"], "via": ip}
            if prev and lat is None:
                log.info("%s no responde", i["name"])
        for t in self.cfg.get("infrastructure", {}).get("ping_targets", []) or []:
            lat = scanner.ping(t["host"])
            self.target_status[t["name"]] = {"up": lat is not None, "latency": lat, "host": t["host"]}

    # ------------------------------------------------- nombres de equipos
    def dns_servers(self, network: str) -> list[str]:
        """DNS que conocen los nombres de esa red: su gateway (módem o Tenda)."""
        n = self.net(network)
        out = [n["gateway"]] if n.get("gateway") else []
        for i in self.infra:
            if (i.get("network") or self.home) == network and i.get("ip") and \
                    (i["role"] == "modem" or i.get("network")):
                out.append(i["ip"])
        return list(dict.fromkeys(out))

    def resolve_names(self) -> None:
        """Busca el nombre de los equipos que no lo tienen (pocos por ciclo, cada 6 h)."""
        budget = self._name_budget
        for d in self.db.devices():
            if budget <= 0:
                break
            net = d.get("network") or self.home
            if not d["online"] or d.get("hostname") or net not in self.reachable() or \
                    time.time() - (d.get("name_tried") or 0) < 6 * 3600:
                continue
            budget -= 1
            name, how = self.adguard.names.get(d["ip"], ""), "AdGuard"
            if not name:
                name, how = names.resolve(d["ip"], self.dns_servers(net))
            fields = {"name_tried": time.time()}
            if name:
                fields.update(hostname=name, hostname_src=how)
                if not d.get("type") or d.get("type") == "Desconocido":
                    fields["type"] = classify(d.get("vendor", ""), name, d["ports"], bool(d["random_mac"]))
            self.db.update_device(d["mac"], **fields)

    def probe(self, ip: str) -> dict:
        """Busca a fondo una IP que no aparece en la lista (botón «Buscar en la red»)."""
        net = next((n for n in self.networks if scanner.in_network(ip, n["cidr"])), None)
        res = {"ip": ip, "network": net["name"] if net else "", "reachable": False}
        if net and net["name"] not in self.reachable():
            res["note"] = f"La Pi no está ahora en la red {net['name']} (roaming)"
        lat = scanner.ping(ip)
        res["ping_ms"] = round(lat * 1000, 1) if lat is not None else None
        host = None
        if net and net["name"] in self.reachable():
            hs = scanner.discover(ip + "/32", net.get("interface", self.iface))
            host = next((h for h in hs if h.ip == ip), None)
        if host is None:
            mac = scanner.neigh_mac(ip)
            host = scanner.Host(ip, mac) if mac else None
        if host:
            res.update(mac=host.mac, vendor=host.vendor, random_mac=host.random_mac)
        res["reachable"] = lat is not None or host is not None
        res["ports"] = scanner.port_scan(ip) if res["reachable"] else []
        res["hostname"], res["hostname_src"] = (self.adguard.names.get(ip), "AdGuard") \
            if self.adguard.names.get(ip) else names.resolve(ip, self.dns_servers(net["name"]) if net else [])
        res["type"] = classify(res.get("vendor", ""), res["hostname"], res["ports"], res.get("random_mac", False))
        if host and net:   # lo agregamos/actualizamos para que ya aparezca
            self.db.upsert_seen(host.mac, ip, host.vendor, res["hostname"], host.random_mac, net["name"])
            self.db.update_device(host.mac, ports=res["ports"], type=res["type"],
                                  hostname_src=res["hostname_src"], last_port_scan=time.time())
            res["device"] = self.db.device(host.mac)
        return res

    # ------------------------------------------------- entrantes y DNS
    def collect_inbound(self) -> None:
        for ev in self.inbound.collect():
            src, port = ev["src_host"], int(ev.get("dst_port") or 0)
            data = ev.get("logdata") or {}
            extra = ", ".join(f"{k.lower()}={v}" for k, v in data.items()
                              if k in ("USERNAME", "PASSWORD", "PATH", "HOSTNAME") and v)[:200]
            dev = next((d for d in self.db.devices() if d["ip"] == src), None)
            who = f"{dev['display_name']} ({src})" if dev else src
            if not self.db.recent_event("honeypot", f"{src}:{port}", 1800):
                self.db.add_event("critical", "honeypot", f"Honeypot: {who} tocó {service_name(port)}",
                                  f"Puerto {port}. Nadie debería conectarse aquí: revisa ese equipo."
                                  + (f" Datos: {extra}" if extra else ""), f"{src}:{port}")
        if int(time.time()) % 3600 < self.interval:
            self.db.prune_inbound()

    def poll_dns(self) -> None:
        dangers = self.adguard.poll()
        # Mantén el DNS de salida elegido en Ajustes (por si reinstalas AdGuard), cada 30 min
        if self.dns_upstream and self.adguard.reachable and time.time() - self._upstream_checked > 1800:
            self._upstream_checked = time.time()
            ok, self.upstream_msg = self.adguard.set_upstream(self.dns_upstream)
            if not ok:
                log.warning("DNS de salida: %s", self.upstream_msg)
        for b in dangers:
            dev = next((d for d in self.db.devices() if d["ip"] == b["client"]), None)
            who = f"{dev['display_name']} ({b['client']})" if dev else b["client"]
            if not self.db.recent_event("dns_danger", f"{b['client']}|{b['domain']}", 6 * 3600):
                self.db.add_event("critical" if b["reason"].startswith("malware") else "warning",
                                  "dns_danger", f"DNS bloqueó {b['reason']}: {b['domain']}",
                                  f"Lo pidió {who}. Si no lo abriste tú, revisa ese equipo.",
                                  f"{b['client']}|{b['domain']}")

    # ---------------------------------------------------------- roaming
    def maybe_roam(self) -> None:
        """Visita las otras redes (otros SSID), las escanea y regresa a casa."""
        if not self.roaming or time.time() - self.last_hop < self.hop_every:
            return
        self.last_hop = time.time()
        if not roam.available():
            self.scan_error = "Roaming activado pero falta nmcli/NetworkManager en el contenedor"
            return
        home = self.net(self.home)
        try:
            for n in self.networks:
                if n["name"] == self.home:
                    continue
                ok, msg = roam.connect(n.get("connection") or n.get("ssid") or n["name"],
                                       n.get("interface", self.iface), n["cidr"])
                if not ok:
                    self.net_status[n["name"]]["error"] = f"No pude conectarme: {msg}"
                    if not self.db.recent_event("roam_failed", n["name"], 6 * 3600):
                        self.db.add_event("warning", "roam_failed", f"No pude entrar a la red {n['name']}",
                                          msg, n["name"])
                    continue
                self.current = n["name"]
                self.stop.wait(min(self.dwell, 5))     # que el ARP de la red se llene un poco
                self.scan(n["name"])
                self.ping_all()
                self.daily_port_scan()
                log.info("Red %s escaneada (%d dispositivos)", n["name"], self.net_status[n["name"]]["devices"])
        finally:
            ok, msg = roam.connect(home.get("connection") or home.get("ssid") or home["name"],
                                   home.get("interface", self.iface), home["cidr"], 60)
            self.current = self.home
            if not ok:
                log.error("¡No pude regresar a %s!: %s", self.home, msg)
                self.db.add_event("critical", "roam_failed", f"No pude regresar a la red {self.home}",
                                  msg, self.home)

    # -------------------------------------------------------------- ACL
    def sync_acl(self) -> None:
        if not self.bridge_mode:
            return
        if not acl.bridge_exists(self.iface):
            self.scan_error = f"Modo puente activado pero no existe {self.iface}"
            return
        rules = self.db.acl()
        blocked = acl.active_blocks(rules)
        macs = [d["mac"] for d in self.db.devices()]
        h = hashlib.sha1(json.dumps([sorted(macs), sorted(blocked)]).encode()).hexdigest()
        if h != self._acl_hash:  # solo se re-aplica si algo cambió (si no, se reiniciarían los contadores)
            ok, err = acl.apply(acl.build_ruleset(macs, blocked))
            if ok:
                self._acl_hash = h
                log.info("nftables: %d dispositivos, %d bloqueados", len(macs), len(blocked))
            else:
                log.error("nftables falló: %s", err)
        self.counters = acl.read_counters()

    # -------------------------------------------------------------- IDS
    def read_ids(self) -> None:
        eve = Path(os.environ.get("SURICATA_EVE", "/suricata/eve.json"))
        if not eve.exists():
            return
        size = eve.stat().st_size
        if size < self._eve_pos:      # rotó el archivo
            self._eve_pos = 0
        if self._eve_pos == 0 and size > 5_000_000:
            self._eve_pos = size       # la primera vez no reprocesamos todo el historial
        grouped: dict[str, dict] = {}
        with open(eve, encoding="utf-8", errors="replace") as f:
            f.seek(self._eve_pos)
            for line in f:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("event_type") != "alert":
                    continue
                a = ev.get("alert", {})
                sev = str(a.get("severity", 3))
                self.ids_alerts[sev] = self.ids_alerts.get(sev, 0) + 1
                if sev in ("1", "2"):
                    key = f"{a.get('signature')}|{ev.get('src_ip')}"
                    g = grouped.setdefault(key, {"n": 0, "ev": ev})
                    g["n"] += 1
            self._eve_pos = f.tell()
        for g in grouped.values():
            ev, a = g["ev"], g["ev"].get("alert", {})
            mac = ev.get("ether", {}).get("src_mac", "") or ev.get("src_ip", "")
            if self.db.recent_event("ids", mac + a.get("signature", ""), 3600):
                continue
            self.db.add_event("critical" if str(a.get("severity")) == "1" else "warning", "ids",
                              f"IDS: {a.get('signature', 'alerta')}",
                              f"{ev.get('src_ip')}:{ev.get('src_port', '')} → {ev.get('dest_ip')}:"
                              f"{ev.get('dest_port', '')} ({ev.get('proto', '')}) · categoría: "
                              f"{a.get('category', '?')} · {g['n']} veces", mac + a.get("signature", ""))

    # ---------------------------------------------------------- avisos
    def in_quiet_hours(self) -> bool:
        q = self.cfg.get("anomalies", {}).get("quiet_hours")
        if not q:
            return False
        return acl._in_window(q[0], q[1], dt.datetime.now().time())

    def sync_sso(self) -> None:
        self.sso.run()

    def send_text(self, text: str, owner: bool = False, silent: bool = False) -> None:
        bot = os.environ.get("BOT_URL")
        if not bot:
            return
        try:
            http_json(f"{bot.rstrip('/')}/notify", "POST",
                      {"text": text, "silent": silent, **({"to": "owner"} if owner else {})},
                      {"Authorization": f"Bearer {os.environ.get('INTERNAL_TOKEN', '')}"})
        except RuntimeError as e:
            log.warning("No pude avisar al bot: %s", e)

    def notify(self) -> None:
        """Manda al bot los eventos pendientes. En horas de silencio solo los críticos."""
        bot = os.environ.get("BOT_URL")
        if not bot:
            return
        pending = self.db.pending_notifications()
        if self.in_quiet_hours():
            pending = [e for e in pending if e["level"] == "critical"]
        if not pending:
            return
        try:
            http_json(f"{bot.rstrip('/')}/notify", "POST",
                      {"source": "netwatch", "events": pending},
                      {"Authorization": f"Bearer {os.environ.get('INTERNAL_TOKEN', '')}"})
            self.db.mark_notified([e["id"] for e in pending])
        except RuntimeError as e:
            log.warning("No pude avisar al bot: %s", e)
