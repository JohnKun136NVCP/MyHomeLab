"""¿Quién reparte IPs en cada red? Si aparece otro router, aquí se ve (y su red, el CIDR).

Un router que alguien conecta a tu red casi siempre trae su DHCP encendido: contesta a los
equipos con OTRA red (p. ej. 192.168.0.x) y les corta el internet o los manda por él. La Pi
pregunta «¿quién me da IP?» (DHCPDISCOVER, con nmap) en cada interfaz y anota quién contesta,
qué red ofrece, su puerta de enlace y su DNS. No pide la IP: solo pregunta.
"""
from __future__ import annotations

import ipaddress
import logging
import re
import shutil
import subprocess
import time

log = logging.getLogger("netwatch.dhcp")

SCHEMA = """
CREATE TABLE IF NOT EXISTS dhcp_servers (
    server  TEXT, iface TEXT, network TEXT, cidr TEXT, router TEXT, dns TEXT, offered TEXT,
    first REAL, last REAL, known INTEGER DEFAULT 0, ack INTEGER DEFAULT 0,
    PRIMARY KEY (server, iface)
);
"""


def parse(out: str) -> list[dict]:
    """Salida de `nmap --script broadcast-dhcp-discover` → una oferta por servidor."""
    offers, cur = [], None
    for ln in out.splitlines():
        ln = ln.strip("|_ \t")
        if re.match(r"Response \d+ of \d+", ln):
            cur = {}
            offers.append(cur)
            continue
        if cur is None or ":" not in ln:
            continue
        k, _, v = ln.partition(":")
        cur[k.strip().lower()] = v.strip()
    res = []
    for o in offers:
        server = o.get("server identifier") or o.get("router", "").split(",")[0].strip()
        if not server:
            continue
        cidr = ""
        try:
            if o.get("ip offered") and o.get("subnet mask"):
                cidr = str(ipaddress.ip_interface(f"{o['ip offered']}/{o['subnet mask']}").network)
        except ValueError:
            pass
        res.append({"server": server, "cidr": cidr, "router": o.get("router", ""),
                    "dns": o.get("domain name server", ""), "offered": o.get("ip offered", ""),
                    "iface": o.get("interface", "")})
    return res


def discover(iface: str, timeout: int = 10) -> list[dict]:
    if not shutil.which("nmap"):
        return []
    try:
        p = subprocess.run(["nmap", "-n", "-e", iface, "--script", "broadcast-dhcp-discover",
                            "--script-args", f"broadcast-dhcp-discover.timeout={timeout}"],
                           capture_output=True, text=True, timeout=timeout + 40)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("DHCP en %s: %s", iface, e)
        return []
    return [{**o, "iface": o["iface"] or iface} for o in parse(p.stdout)]


class Watch:
    def __init__(self, db, mon) -> None:
        self.db, self.mon = db, mon
        with db.lock:
            db.conn.executescript(SCHEMA)
            db.conn.commit()
        self.last = 0.0
        self.running = False

    def known_servers(self, network: str) -> set[str]:
        net = self.mon.net(network)
        out = {net.get("gateway", "")} | set(net.get("dhcp_servers") or [])
        for i in self.mon.infra:
            if (i.get("network") or self.mon.home) == network and i.get("ip"):
                out.add(i["ip"])
        out |= set((self.mon.cfg.get("network") or {}).get("dhcp_servers") or [])
        return {x for x in out if x}

    def run(self) -> None:
        """Pregunta en cada red que la Pi ve ahora. Lo llama el monitor cada 30 min."""
        if self.running:
            return
        self.running, self.last = True, time.time()
        try:
            for name in sorted(self.mon.reachable()):
                n = self.mon.net(name)
                iface = n.get("interface") or self.mon.iface
                for o in discover(iface):
                    self.record(name, o)
        finally:
            self.running = False

    def record(self, network: str, o: dict) -> None:
        now = time.time()
        net_cidr = self.mon.net(network).get("cidr", "")
        known = o["server"] in self.known_servers(network)
        row = self.db.q("SELECT * FROM dhcp_servers WHERE server=? AND iface=?", (o["server"], o["iface"]))
        if row:
            self.db.x("UPDATE dhcp_servers SET network=?, cidr=?, router=?, dns=?, offered=?, last=?, known=? "
                      "WHERE server=? AND iface=?", (network, o["cidr"], o["router"], o["dns"], o["offered"], now,
                                                     int(known), o["server"], o["iface"]))
        else:
            self.db.x("INSERT INTO dhcp_servers (server, iface, network, cidr, router, dns, offered, first, last, "
                      "known, ack) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (o["server"], o["iface"], network, o["cidr"], o["router"], o["dns"], o["offered"], now, now,
                       int(known), int(known)))
        acked = known or (row and row[0]["ack"])
        if acked or self.db.recent_event("rogue_dhcp", o["server"], 7 * 86400):
            return
        dev = next((d for d in self.db.devices() if d.get("ip") == o["server"]), None)
        who = (dev or {}).get("display_name") or (dev or {}).get("vendor") or "un equipo desconocido"
        other_net = o["cidr"] and net_cidr and o["cidr"] != str(ipaddress.ip_network(net_cidr, strict=False))
        level = "critical" if network == self.mon.home or other_net else "warning"
        self.db.add_event(level, "rogue_dhcp", f"Otro router repartiendo IPs en «{network}»: {o['server']}",
                          f"{o['server']} ({who}) ofrece IPs de {o['cidr'] or '¿?'} con puerta de enlace "
                          f"{o['router'] or '¿?'} y DNS {o['dns'] or '¿?'}."
                          + (f" Es OTRA red distinta de la tuya ({net_cidr}): los equipos que le hagan caso se quedan "
                             "sin tu internet o pasan por él. Busca un router conectado por su puerto LAN y apágale "
                             "el DHCP (o conéctalo por WAN)." if other_net else
                             " Si es tuyo, márcalo «es mío» en Red → Wi-Fi."),
                          o["server"])

    def servers(self) -> list[dict]:
        return self.db.q("SELECT * FROM dhcp_servers ORDER BY known, ack, last DESC")

    def ack(self, server: str) -> None:
        self.db.x("UPDATE dhcp_servers SET ack=1 WHERE server=?", (server,))
