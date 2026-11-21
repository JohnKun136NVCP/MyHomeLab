"""Conexiones entrantes a la Pi: permitidas (conntrack), bloqueadas (UFW) y honeypot.

* conntrack (/proc/net/nf_conntrack): ve también las que Docker redirige a un
  contenedor (web, Grafana…), que `ss` no muestra.
* Log de UFW ([UFW BLOCK]): intentos rechazados por el firewall.
* Log de OpenCanary: alguien tocó un servicio falso del honeypot.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import re
import subprocess
from pathlib import Path

log = logging.getLogger("netwatch.inbound")

CT_RE = re.compile(r"^ipv4\s+\d+\s+(tcp|udp)\s+\d+\s+\d+\s+(?:(\w+)\s+)?"
                   r"src=(\S+) dst=(\S+) sport=(\d+) dport=(\d+)")
UFW_RE = re.compile(r"\[UFW (BLOCK|AUDIT|ALLOW)\].*?IN=(\S*).*?SRC=(\S+) DST=(\S+).*?PROTO=(\w+)"
                    r"(?:.*?SPT=(\d+))?(?:.*?DPT=(\d+))?")
IGNORE_NETS = [ipaddress.ip_network(n) for n in ("127.0.0.0/8", "172.16.0.0/12", "169.254.0.0/16",
                                                 "224.0.0.0/4", "255.255.255.255/32", "0.0.0.0/32")]
# Ruido que no interesa contar como «entrante»
IGNORE_PORTS = {53, 67, 68, 123, 137, 138, 1900, 5353, 5355}

SERVICES = {21: "FTP", 22: "SSH", 23: "Telnet", 53: "DNS", 80: "HTTP", 443: "HTTPS",
            445: "SMB", 1433: "MSSQL", 2222: "SSH (señuelo)", 3000: "Grafana", 3001: "Uptime Kuma",
            3306: "MySQL", 3389: "RDP", 5900: "VNC", 6379: "Redis", 8000: "netwatch",
            8081: "Bot API", 8088: "Web homelab (http)", 8090: "nginx 8090", 8443: "Web homelab",
            9090: "Prometheus", 9100: "node-exporter", 19180: "SSH", 51820: "WireGuard"}


def service_name(port: int) -> str:
    return SERVICES.get(int(port), str(port))


def ignored(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return True
    return a.version != 4 or any(a in n for n in IGNORE_NETS)


def local_ifaces() -> dict[str, str]:
    """{ip_de_la_pi: interfaz}"""
    try:
        out = subprocess.run(["ip", "-4", "-o", "addr"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return {}
    return {m.group(2): m.group(1) for m in re.finditer(r"^\d+:\s+(\S+)\s+inet (\d+\.\d+\.\d+\.\d+)", out, re.M)}


def local_ips() -> set[str]:
    return set(local_ifaces())


def parse_conntrack(text: str, mine: set[str]) -> list[tuple[str, int, str, str, int]]:
    """[(ip_origen, puerto_destino, proto, ip_destino, puerto_origen)] de lo que llega a la Pi."""
    out = []
    for line in text.splitlines():
        m = CT_RE.match(line)
        if not m:
            continue
        proto, state, src, dst, sport, dport = m.groups()
        if dst not in mine or src in mine or ignored(src) or int(dport) in IGNORE_PORTS:
            continue
        if proto == "tcp" and state not in (None, "ESTABLISHED", "SYN_RECV", "TIME_WAIT", "CLOSE_WAIT",
                                             "FIN_WAIT", "LAST_ACK", "CLOSE"):
            continue
        out.append((src, int(dport), proto, dst, int(sport)))
    return out


def parse_ufw(lines: list[str], mine: set[str] | None = None) -> list[tuple[str, int, str, str, str]]:
    """[(ip_origen, puerto, proto, interfaz, acción)] de los bloqueos de UFW."""
    out = []
    for line in lines:
        m = UFW_RE.search(line)
        if not m or m.group(1) != "BLOCK":
            continue
        _, iface, src, dst, proto, _spt, dpt = m.groups()
        if ignored(src) or (mine and dst not in mine) or not dpt or int(dpt) in IGNORE_PORTS:
            continue
        out.append((src, int(dpt), proto.lower(), iface, "bloqueada"))
    return out


def parse_canary(lines: list[str]) -> list[dict]:
    """Eventos de OpenCanary (logtype ≥ 2000 = interacción real, no arranque)."""
    out = []
    for line in lines:
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if int(ev.get("logtype", 0)) < 2000 or not ev.get("src_host"):
            continue
        out.append(ev)
    return out


class Tail:
    """Lee solo lo nuevo de un archivo de log (sobrevive a la rotación)."""

    def __init__(self, path: str, start_at_end: bool = True):
        self.path = Path(path)
        self.pos = -1 if start_at_end else 0

    def read(self) -> list[str]:
        if not self.path.exists():
            return []
        size = self.path.stat().st_size
        if self.pos < 0 or size < self.pos:
            self.pos = size if self.pos < 0 else 0
            return []
        with open(self.path, encoding="utf-8", errors="replace") as f:
            f.seek(self.pos)
            lines = f.readlines()
            self.pos = f.tell()
        return lines


class Inbound:
    """Acumula las conexiones entrantes en la base cada ciclo."""

    def __init__(self, db, ufw_log: str, canary_log: str, conntrack: str = "/proc/net/nf_conntrack"):
        self.db = db
        self.conntrack = Path(conntrack)
        self.ufw = Tail(ufw_log)
        self.canary = Tail(canary_log, start_at_end=False)
        self._seen: set[tuple] = set()        # conexiones ya contadas (siguen en conntrack)
        self.mine: set[str] = set()
        self.error = ""

    def collect(self) -> list[dict]:
        """Lee las tres fuentes. Regresa eventos del honeypot para avisar."""
        ifaces = local_ifaces()
        self.mine = set(ifaces) or self.mine
        iface_of = ifaces.get
        rows: list[tuple] = []
        # 1) conntrack: cuenta cada conexión una sola vez
        if self.conntrack.exists():
            try:
                cur = set(parse_conntrack(self.conntrack.read_text(errors="replace"), self.mine))
                for src, port, proto, dst, _sport in cur - self._seen:
                    rows.append((src, port, proto, iface_of(dst, ""), "permitida"))
                self._seen = cur
                self.error = ""
            except OSError as e:
                self.error = f"No puedo leer conntrack: {e}"
        else:
            self.error = "No existe /proc/net/nf_conntrack (¿módulo nf_conntrack cargado?)"
        # 2) UFW
        rows += parse_ufw(self.ufw.read(), self.mine)
        # 3) honeypot
        # lo que hace la propia Pi (inventario, escaneos) no es un intruso
        canary = [e for e in parse_canary(self.canary.read()) if e["src_host"] not in self.mine
                  and not e["src_host"].startswith("127.")]
        for ev in canary:
            rows.append((ev["src_host"], int(ev.get("dst_port") or 0), "tcp", "", "honeypot"))
        for r in rows:
            self.db.add_inbound(*r)
        return canary
