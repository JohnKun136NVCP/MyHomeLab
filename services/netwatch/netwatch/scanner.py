"""Descubrimiento de dispositivos: arp-scan (rápido) con nmap como respaldo."""
from __future__ import annotations

import ipaddress
import logging
import re
import shutil
import socket
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("netwatch.scan")
MAC_RE = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")


@dataclass
class Host:
    ip: str
    mac: str
    vendor: str = ""
    hostname: str = ""
    ports: list[str] = field(default_factory=list)

    @property
    def random_mac(self) -> bool:
        """MAC privada/aleatoria (iPhone, Android, Windows): bit «local» encendido."""
        return bool(int(self.mac.split(":")[0], 16) & 0x02)


def is_random_mac(mac: str) -> bool:
    try:
        return bool(int(mac.split(":")[0], 16) & 0x02)
    except ValueError:
        return False


def _run(cmd: list[str], timeout: int = 120) -> str:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log.warning("%s falló: %s", cmd[0], e)
        return ""


def parse_arp_scan(out: str) -> list[Host]:
    hosts = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and MAC_RE.match(parts[1].strip().lower()):
            vendor = parts[2].strip() if len(parts) > 2 else ""
            if vendor.startswith("(Unknown"):
                vendor = ""
            hosts.append(Host(parts[0].strip(), parts[1].strip().lower(), vendor))
    return hosts


def parse_nmap_xml(xml: str) -> list[Host]:
    """Hosts de `nmap -oX -` (sirve para -sn y para escaneo de puertos)."""
    hosts: list[Host] = []
    if not xml.strip():
        return hosts
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return hosts
    for h in root.findall("host"):
        st = h.find("status")
        if st is not None and st.get("state") != "up":
            continue
        ip = mac = vendor = ""
        for a in h.findall("address"):
            if a.get("addrtype") == "ipv4":
                ip = a.get("addr", "")
            elif a.get("addrtype") == "mac":
                mac = a.get("addr", "").lower()
                vendor = a.get("vendor", "")
        hn = h.find("hostnames/hostname")
        ports = []
        for p in h.findall("ports/port"):
            state = p.find("state")
            if state is not None and state.get("state") == "open":
                svc = p.find("service")
                name = svc.get("name", "") if svc is not None else ""
                ports.append(f"{p.get('portid')}/{p.get('protocol')}" + (f" {name}" if name else ""))
        hosts.append(Host(ip, mac, vendor, hn.get("name", "") if hn is not None else "", ports))
    return hosts


def own_host(iface: str) -> Host | None:
    """La propia Raspberry no aparece en su ARP: la agregamos a mano."""
    try:
        mac = Path(f"/sys/class/net/{iface}/address").read_text().strip().lower()
    except OSError:
        return None
    out = _run(["ip", "-4", "-o", "addr", "show", iface], 5)
    m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", out)
    if not m:
        return None
    return Host(m.group(1), mac, "Raspberry Pi (este equipo)", socket.gethostname())


def reverse_dns(ip: str) -> str:
    try:
        socket.setdefaulttimeout(1.5)
        name = socket.gethostbyaddr(ip)[0]
        return "" if name == ip else name.split(".")[0]
    except (OSError, UnicodeError):
        return ""
    finally:
        socket.setdefaulttimeout(None)


def discover(cidr: str, iface: str) -> list[Host]:
    """Escaneo ARP de la red. Regresa una entrada por MAC."""
    hosts: list[Host] = []
    if shutil.which("arp-scan"):
        hosts = parse_arp_scan(_run(["arp-scan", "-I", iface, "--plain", "--retry=2",
                                     "--timeout=500", cidr]))
    if not hosts and shutil.which("nmap"):
        hosts = parse_nmap_xml(_run(["nmap", "-sn", "-n", "-e", iface, "-oX", "-", cidr], 300))
        hosts = [h for h in hosts if h.mac]
    me = own_host(iface)
    if me:
        hosts.append(me)
    by_mac: dict[str, Host] = {}
    for h in hosts:
        if h.mac not in by_mac:
            by_mac[h.mac] = h
    for h in by_mac.values():
        if not h.hostname:
            h.hostname = reverse_dns(h.ip)
    return list(by_mac.values())


# Puertos que delatan qué es cada cosa (incluye los de cámaras genéricas: 8899 ONVIF y
# 34567 de iCSee/XMEye, 10554 de algunas V380, 6668 de Tuya/Smart Life)
SCAN_PORTS = ("21,22,23,25,53,80,81,88,110,111,135,139,143,443,445,515,548,554,631,993,995,1883,1900,"
              "1925,3000,3306,3389,5000,5001,5357,5900,6668,7000,8000,8001,8002,8008,8009,8060,8080,"
              "8081,8443,8554,8888,8899,9000,9090,9100,10554,34567,49152,62078")


def port_scan(ip: str) -> list[str]:
    """Puertos abiertos que ayudan a saber qué es el dispositivo."""
    if not shutil.which("nmap"):
        return []
    res = parse_nmap_xml(_run(["nmap", "-Pn", "-T4", "-p", SCAN_PORTS, "--open", "-oX", "-", ip], 180))
    return res[0].ports if res else []


def neigh_mac(ip: str) -> str:
    """MAC de la tabla ARP del sistema (si hubo tráfico reciente con esa IP)."""
    m = re.search(r"lladdr ([0-9a-f:]{17})", _run(["ip", "neigh", "show", ip], 5))
    return m.group(1) if m else ""


def ping(host: str, timeout: float = 1.5) -> float | None:
    """Latencia en segundos o None si no responde."""
    out = _run(["ping", "-c", "1", "-W", str(int(max(1, timeout))), host], int(timeout) + 3)
    m = re.search(r"time[=<]([\d.]+) ?ms", out)
    return float(m.group(1)) / 1000 if m else None


def in_network(ip: str, cidr: str) -> bool:
    try:
        return ipaddress.ip_address(ip) in ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return False
