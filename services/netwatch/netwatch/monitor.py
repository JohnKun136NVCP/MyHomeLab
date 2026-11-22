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
        # Redes que comparten interfaz (varios SSID en la misma Wi-Fi) se visitan por turnos;
        # las que tienen interfaz propia (eth0 a casa) se escanean siempre, sin moverse.
        groups: dict[str, list[dict]] = {}
        for n in self.networks:
            groups.setdefault(n["interface"], []).append(n)
        self.roam_groups = {i: g for i, g in groups.items() if len(g) > 1}
        self.roaming = (bool(r.get("enabled")) or bool(self.roam_groups)) and len(self.networks) > 1 \
            and not self.bridge_mode
        if self.roaming and not self.roam_groups:          # roaming «clásico»: todas por la misma Wi-Fi
            self.roam_groups = {self.networks[0]["interface"]: self.networks}
        # en cada interfaz que brinca: su red «base» (a la que regresa) y en la que está ahora
        self.roam_base = {i: next((n["name"] for n in g if n["name"] == self.home), g[0]["name"])
                          for i, g in self.roam_groups.items()}
        self.roam_on = dict(self.roam_base)
        self.hop_every = int(r.get("hop_every", 900))
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
        from .inventory import Inventory
        from .parental import Parental
        from .wifi import Survey
        self.inventory = Inventory(db, cfg)
        self.wifi = Survey(db, cfg)
        self.parental = Parental(db, self.adguard, os.environ.get("TZ") or "America/Mexico_City")
        self._parental_synced = 0.0
        from .bots import Bots
        from .dhcp import Watch
        self.bots = Bots(db)
        self.dhcp = Watch(db, self)
        self._wifi_tracked = time.time() - 540            # primera revisión del aire al minuto
        from . import extras
        self.outages = extras.Outages(db)
        self.watch = extras.Watch(db, cfg)
        self.capture = extras.Capture()
        sl = cfg.get("syslog") or {}
        self.syslog = extras.Syslog(db, int(sl.get("port", 514)), self._name_of_ip)
        if sl.get("enabled", True) and not (os.environ.get("NETWATCH_NO_SYSLOG") or os.environ.get("PYTEST_CURRENT_TEST")):
            self.syslog.start()
        self._bots_watched = 0.0
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
        if not self.roaming:
            return {n["name"] for n in self.networks}
        fixed = {n["name"] for n in self.networks if n["interface"] not in self.roam_groups}
        return fixed | set(self.roam_on.values())

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
                         self.sync_sso, self.maintenance, self.maybe_roam):
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
        o si una sola MAC responde por varias IPs de infraestructura.

        Sin falsas alarmas por lo normal de una casa:
        * MAC conocida (``mac``/``macs`` del equipo en homelab.yml, o marcada «es normal» en
          Eventos) → se acepta en silencio.
        * El equipo de siempre sigue en la red con OTRA IP → el DHCP le cambió la IP
          (aviso de «reserva su IP»), no es spoofing.
        * MAC nueva privada/aleatoria → aviso, no crítico.
        * Un repetidor (AP) que contesta por los equipos que tiene detrás (MAC-NAT) → normal.
        """
        infra = self.infra_ips(self.current) if infra is None else infra
        by_ip: dict[str, list[str]] = {}
        ip_of: dict[str, str] = {}
        for h in hosts:
            by_ip.setdefault(h.ip, []).append(h.mac)
            ip_of[h.mac] = h.ip

        def known(item: dict, mac: str) -> bool:
            cfg = {str(m).lower() for m in ([item.get("mac")] if item.get("mac") else []) + list(item.get("macs") or [])}
            return mac.lower() in cfg or bool(self.db.suppressed("arp_spoof", mac, ""))

        for ip, item in infra.items():
            macs = sorted(set(by_ip.get(ip, [])))
            if len(macs) > 1 and not all(known(item, m) for m in macs):
                lvl = "critical" if item.get("role") == "modem" else "warning"
                self._spoof_event(ip, item["name"], f"Varias MAC responden por {ip}: {', '.join(macs)}", level=lvl)
            if not macs:
                continue
            new = by_ip[ip][0]
            prev = self._infra_mac.get(ip)
            if prev is None:  # primera vez desde que arrancó: lo último que guardó la base
                hist = [m for m in self.db.macs_for_ip(ip, time.time() - 30 * 86400) if m != new]
                prev = new if not hist else None
            nat = item.get("role") != "modem" and any(
                o != ip and new in by_ip.get(o, []) and infra[o].get("role") == "ap" for o in infra)
            if prev and prev != new and not known(item, new) and not nat:
                if prev in ip_of and ip_of[prev] != ip:
                    if not self.db.recent_event("infra_ip_changed", prev, 6 * 3600):
                        self.db.add_event("warning", "infra_ip_changed", f"{item['name']} cambió de IP",
                                          f"{item['name']} ({prev}) ahora está en {ip_of[prev]}; la IP {ip} la tiene "
                                          f"{new}. No es un ataque: el DHCP le dio otra IP. Resérvale su IP en el "
                                          f"router o pon la nueva en homelab.yml.", prev)
                elif scanner.is_random_mac(new):
                    self._spoof_event(ip, item["name"],
                                      f"{item['name']} ({ip}) contesta ahora con una MAC privada/aleatoria {new} "
                                      f"(antes {prev}). Si es un equipo tuyo que cambia de MAC, márcalo «es normal».",
                                      new, level="warning")
                else:
                    self._spoof_event(ip, item["name"],
                                      f"{item['name']} ({ip}) cambió de MAC: antes {prev}, ahora {new}. Si no cambiaste "
                                      f"el equipo puede ser ARP spoofing; si sí, márcalo «es normal».", new,
                                      level="critical" if item.get("role") == "modem" else "warning")
            self._infra_mac[ip] = new
        mac_ips: dict[str, set] = {}
        for h in hosts:
            if h.ip in infra:
                mac_ips.setdefault(h.mac, set()).add(h.ip)
        for mac, ips in mac_ips.items():
            if len(ips) < 2:
                continue
            roles = {infra[i].get("role") for i in ips}
            if "modem" not in roles and "ap" in roles:
                continue            # repetidor con MAC-NAT: contesta por lo que tiene detrás
            if all(known(infra[i], mac) for i in ips):
                continue
            self._spoof_event(",".join(sorted(ips)), "infraestructura",
                              f"La MAC {mac} responde por varias IPs de infraestructura: "
                              f"{', '.join(sorted(ips))}", mac)

    def _spoof_event(self, ip: str, name: str, detail: str, mac: str = "", level: str = "critical") -> None:
        if not self.db.recent_event("arp_spoof", mac or ip, 6 * 3600):
            title = f"Posible ARP spoofing en {name}" if level == "critical" else f"{name}: cambió la MAC que contesta"
            self.db.add_event(level, "arp_spoof", title, detail, mac or ip)

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
            self.outages.update(t["name"], lat is not None)

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

    def _name_of_ip(self, ip: str) -> str:
        for i in self.infra:
            if i.get("ip") == ip:
                return i["name"]
        d = next((d for d in self.db.devices() if d.get("ip") == ip), None)
        return (d or {}).get("display_name") or ip

    def maintenance(self) -> None:
        """Inventario semanal (de madrugada) y control parental al día si un equipo cambia de IP."""
        if self.inventory.due():
            threading.Thread(target=self.inventory.scan, name="inventario", daemon=True).start()
        if self.adguard.enabled and time.time() - self._parental_synced > 1800:
            self._parental_synced = time.time()
            self.parental.resync()
        if time.time() - self._wifi_tracked > 600:          # routers/AP nuevos en el aire
            self._wifi_tracked = time.time()
            threading.Thread(target=self.wifi.track, name="wifi", daemon=True).start()
        if time.time() - self.dhcp.last > 1800 and not self.bridge_mode:   # ¿otro router repartiendo IPs?
            threading.Thread(target=self.dhcp.run, name="dhcp", daemon=True).start()
        if (self.cfg.get("watch") or {}) and time.time() - self.watch.last > 6 * 3600:   # certificados y DNS
            self.watch.last = time.time()
            threading.Thread(target=self.watch.run, name="watch", daemon=True).start()
        if self.bots.all() and time.time() - self._bots_watched > 300:
            self._bots_watched = time.time()
            threading.Thread(target=self.bots.watch, name="bots", daemon=True).start()

    # ---------------------------------------------------------- roaming
    def maybe_roam(self) -> None:
        """Visita las otras redes (otros SSID) de cada Wi-Fi que comparte, las escanea y regresa."""
        force = Path(os.environ.get("HOMELAB_RUN", "/homelab/run")) / "roam-now"   # ./hl roam now
        forced = force.exists()
        if not self.roaming or (time.time() - self.last_hop < self.hop_every and not forced):
            return
        force.unlink(missing_ok=True)
        self.last_hop = time.time()
        if not roam.available():
            self.scan_error = "Roaming activado pero falta nmcli/NetworkManager en el contenedor"
            return
        home_iface = self.net(self.home)["interface"]
        for iface, group in self.roam_groups.items():
            base = self.net(self.roam_base[iface])
            try:
                for n in group:
                    if n["name"] == base["name"]:
                        continue
                    ok, msg = roam.connect(n.get("connection") or n.get("ssid") or n["name"], iface, n["cidr"])
                    if not ok:
                        self.net_status[n["name"]]["error"] = f"No pude conectarme: {msg}"
                        if not self.db.recent_event("roam_failed", n["name"], 6 * 3600):
                            self.db.add_event("warning", "roam_failed", f"No pude entrar a la red {n['name']}",
                                              msg + "\n¿Guardaste su Wi-Fi en la Pi?  ./hl roam add", n["name"])
                        continue
                    self.roam_on[iface] = n["name"]
                    self.net_status[n["name"]]["last_visit"] = time.time()
                    if iface == home_iface:
                        self.current = n["name"]
                    self.net_status[n["name"]]["error"] = ""
                    self.stop.wait(min(self.dwell, 10))     # que el ARP de la red se llene un poco
                    self.scan(n["name"])
                    self.ping_all()
                    self.daily_port_scan()
                    log.info("Red %s escaneada (%d dispositivos)", n["name"], self.net_status[n["name"]]["devices"])
            finally:
                ok, msg = roam.connect(base.get("connection") or base.get("ssid") or base["name"], iface, base["cidr"], 60)
                self.roam_on[iface] = base["name"]
                if iface == home_iface:
                    self.current = self.home
                if not ok:
                    log.error("¡No pude regresar a %s!: %s", base["name"], msg)
                    self.db.add_event("critical", "roam_failed", f"No pude regresar a la red {base['name']}",
                                      msg, base["name"])

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
