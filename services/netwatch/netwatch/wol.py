"""Wake-on-LAN: prende la PC (o cualquier equipo con WoL activo en el BIOS) desde el panel o el bot.

Equipos en homelab.yml:
    wake_on_lan:
      - name: PC
        mac: aa:bb:cc:dd:ee:ff
`add-server.sh` agrega la PC sola. netwatch usa la red del host, así que el
«paquete mágico» sale por la red de la casa.
"""
from __future__ import annotations

import ipaddress
import re
import socket

MAC_RE = re.compile(r"^([0-9a-f]{2})([:-]?)([0-9a-f]{2})(\2[0-9a-f]{2}){4}$", re.I)


def norm_mac(mac: str) -> str:
    mac = (mac or "").strip().lower()
    if not MAC_RE.match(mac):
        raise ValueError("MAC inválida (ejemplo: aa:bb:cc:dd:ee:ff)")
    h = re.sub(r"[^0-9a-f]", "", mac)
    return ":".join(h[i:i + 2] for i in range(0, 12, 2))


def packet(mac: str) -> bytes:
    return b"\xff" * 6 + bytes.fromhex(norm_mac(mac).replace(":", "")) * 16


def broadcasts(cidrs: list[str]) -> list[str]:
    out = ["255.255.255.255"]
    for c in cidrs:
        try:
            out.append(str(ipaddress.ip_network(c, strict=False).broadcast_address))
        except ValueError:
            pass
    return list(dict.fromkeys(out))


def send(mac: str, cidrs: list[str] | None = None, port: int = 9) -> list[str]:
    data = packet(mac)
    sent = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for b in broadcasts(cidrs or []):
            for p in (port, 7):
                try:
                    s.sendto(data, (b, p))
                    sent.append(f"{b}:{p}")
                except OSError:
                    pass
    if not sent:
        raise OSError("No pude mandar el paquete por ninguna red")
    return sent


def targets(cfg: dict) -> list[dict]:
    out = []
    for t in cfg.get("wake_on_lan") or []:
        try:
            out.append({"name": str(t.get("name") or t["mac"]), "mac": norm_mac(t["mac"])})
        except (KeyError, ValueError, AttributeError):
            continue
    return out
