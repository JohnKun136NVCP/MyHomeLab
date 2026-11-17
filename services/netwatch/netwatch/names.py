"""Nombres de los equipos sin depender del DNS del sistema.

Prueba, en orden:
  1. PTR al DNS del módem (casi todos los módems resuelven los nombres que dio su DHCP);
  2. mDNS (iPhone, Mac, Linux con avahi, impresoras, Chromecast…);
  3. NetBIOS (Windows), con nmblookup si está instalado.
Todo con la biblioteca estándar (paquetes DNS armados a mano).
"""
from __future__ import annotations

import ipaddress
import random
import re
import shutil
import socket
import struct
import subprocess

MDNS = ("224.0.0.251", 5353)


def _qname(name: str) -> bytes:
    out = b""
    for part in name.rstrip(".").split("."):
        out += bytes([len(part)]) + part.encode()
    return out + b"\0"


def ptr_name(ip: str) -> str:
    return ".".join(reversed(ip.split("."))) + ".in-addr.arpa"


def build_query(name: str, qtype: int = 12, qid: int | None = None, unicast_response: bool = False) -> bytes:
    qid = random.randint(0, 0xFFFF) if qid is None else qid
    header = struct.pack(">HHHHHH", qid, 0x0100 if qid else 0, 1, 0, 0, 0)
    qclass = 0x8001 if unicast_response else 1        # bit QU de mDNS: «respóndeme a mí»
    return header + _qname(name) + struct.pack(">HH", qtype, qclass)


def _read_name(buf: bytes, off: int, depth: int = 0) -> tuple[str, int]:
    labels, jumped, end = [], False, off
    while depth < 20:
        if off >= len(buf):
            raise ValueError("paquete truncado")
        n = buf[off]
        if n == 0:
            off += 1
            break
        if n & 0xC0 == 0xC0:                       # puntero de compresión
            ptr = struct.unpack(">H", buf[off:off + 2])[0] & 0x3FFF
            if not jumped:
                end = off + 2
            jumped, off, depth = True, ptr, depth + 1
            continue
        labels.append(buf[off + 1:off + 1 + n].decode(errors="replace"))
        off += 1 + n
    return ".".join(labels), (end if jumped else off)


def parse_ptr_answer(buf: bytes) -> str:
    """Primer registro PTR de una respuesta DNS (o '')."""
    if len(buf) < 12:
        return ""
    _, _, qd, an, _, _ = struct.unpack(">HHHHHH", buf[:12])
    off = 12
    for _ in range(qd):
        _, off = _read_name(buf, off)
        off += 4
    for _ in range(an):
        _, off = _read_name(buf, off)
        rtype, _, _, rdlen = struct.unpack(">HHIH", buf[off:off + 10])
        off += 10
        if rtype == 12:
            name, _ = _read_name(buf, off)
            return name
        off += rdlen
    return ""


def clean(name: str) -> str:
    """'iPhone-de-Juan.local.' → 'iPhone-de-Juan';  'desktop-abc.lan' → 'desktop-abc'."""
    name = name.rstrip(".")
    for suf in (".local", ".lan", ".home", ".localdomain", ".domain", ".gateway", ".home.arpa"):
        if name.lower().endswith(suf):
            name = name[: -len(suf)]
    return name.split(".")[0] if re.search(r"\.\d+$", name) is None else name


def query_ptr(ip: str, server: str, port: int = 53, timeout: float = 1.2) -> str:
    q = build_query(ptr_name(ip))
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            s.sendto(q, (server, port))
            data, _ = s.recvfrom(2048)
        return clean(parse_ptr_answer(data))
    except (OSError, ValueError, struct.error):
        return ""


def parse_a_answer(buf: bytes) -> str:
    """Primer registro A de una respuesta DNS (o '')."""
    if len(buf) < 12:
        return ""
    _, _, qd, an, _, _ = struct.unpack(">HHHHHH", buf[:12])
    off = 12
    for _ in range(qd):
        _, off = _read_name(buf, off)
        off += 4
    for _ in range(an):
        _, off = _read_name(buf, off)
        rtype, _, _, rdlen = struct.unpack(">HHIH", buf[off:off + 10])
        off += 10
        if rtype == 1 and rdlen == 4:
            return socket.inet_ntoa(buf[off:off + 4])
        off += rdlen
    return ""


def query_a(name: str, server: str, port: int = 53, timeout: float = 1.2) -> str:
    """IP de un nombre según el DNS del módem/Tenda (prueba también .lan y .local)."""
    if not re.match(r"^[\w.-]+$", name):
        return ""
    for candidate in (name, f"{name}.lan", f"{name}.home"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(timeout)
                s.sendto(build_query(candidate, qtype=1), (server, port))
                data, _ = s.recvfrom(2048)
            ip = parse_a_answer(data)
            if ip:
                return ip
        except (OSError, ValueError, struct.error):
            continue
    return ""


def query_mdns(ip: str, timeout: float = 1.2) -> str:
    """Pregunta por mDNS «¿quién es esta IP?» y pide respuesta directa (QU)."""
    q = build_query(ptr_name(ip), qid=0, unicast_response=True)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
            # Directo al equipo y a la dirección multicast (algunos solo contestan a una)
            for dst in ((ip, 5353), MDNS):
                s.sendto(q, dst)
            while True:
                data, _ = s.recvfrom(4096)
                name = parse_ptr_answer(data)
                if name:
                    return clean(name)
    except (OSError, ValueError, struct.error):
        return ""


def query_netbios(ip: str, timeout: int = 3) -> str:
    if not shutil.which("nmblookup"):
        return ""
    try:
        out = subprocess.run(["nmblookup", "-A", ip], capture_output=True, text=True,
                             timeout=timeout).stdout
    except (subprocess.TimeoutExpired, OSError):
        return ""
    m = re.search(r"^\s+(\S+)\s+<00>\s+-\s+(?!<GROUP>)", out, re.M)
    return m.group(1) if m else ""


def resolve(ip: str, dns_servers: list[str]) -> tuple[str, str]:
    """(nombre, método) usando todos los métodos; ('', '') si nadie contesta."""
    try:
        if not ipaddress.ip_address(ip).is_private:
            return "", ""
    except ValueError:
        return "", ""
    for srv in dns_servers:
        n = query_ptr(ip, srv)
        if n and n != ip:
            return n, f"DNS {srv}"
    n = query_mdns(ip)
    if n:
        return n, "mDNS"
    n = query_netbios(ip)
    if n:
        return n, "NetBIOS"
    try:
        n = socket.gethostbyaddr(ip)[0]
        if n and n != ip:
            return clean(n), "DNS del sistema"
    except (OSError, UnicodeError):
        pass
    return "", ""
