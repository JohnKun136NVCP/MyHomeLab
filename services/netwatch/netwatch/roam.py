"""Cambiar la Wi-Fi de la Pi entre SSIDs (NetworkManager / nmcli).

La Pi vive en su red «casa» y cada `hop_every` segundos visita las demás redes,
las escanea y regresa. Cada red necesita un perfil guardado en NetworkManager
(conéctate una vez a mano:  sudo nmcli dev wifi connect "SSID" password "clave").
"""
from __future__ import annotations

import ipaddress
import logging
import os
import re
import shutil
import subprocess
import time

log = logging.getLogger("netwatch.roam")


def _run(cmd: list[str], timeout: int = 60) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def available() -> bool:
    return shutil.which("nmcli") is not None


def current_ip(iface: str) -> str:
    _, out = _run(["ip", "-4", "-o", "addr", "show", iface], 5)
    m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", out)
    return m.group(1) if m else ""


def in_cidr(ip: str, cidr: str) -> bool:
    try:
        return bool(ip) and ipaddress.ip_address(ip) in ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return False


def is_wifi(iface: str) -> bool:
    return os.path.isdir(f"/sys/class/net/{iface}/wireless")


def wifi_iface() -> str:
    """La primera interfaz Wi-Fi (wlan0 casi siempre)."""
    try:
        return next(i for i in sorted(os.listdir("/sys/class/net")) if is_wifi(i))
    except (OSError, StopIteration):
        return "wlan0"


def connect(profile: str, iface: str, cidr: str, timeout: int = 45) -> tuple[bool, str]:
    """Activa el perfil y espera a tener IP dentro de `cidr`."""
    if not is_wifi(iface) and os.path.isdir("/sys/class/net"):
        iface = wifi_iface()            # «interface: eth0» por omisión, pero los SSID van por la Wi-Fi
    if in_cidr(current_ip(iface), cidr):
        return True, "ya conectado"
    code, out = _run(["nmcli", "--wait", str(timeout), "connection", "up", "id", profile,
                      "ifname", iface], timeout + 10)
    if code != 0:
        return False, out[-300:]
    end = time.time() + 20
    while time.time() < end:
        if in_cidr(current_ip(iface), cidr):
            return True, "ok"
        time.sleep(1)
    return False, f"conectó a {profile} pero no recibió IP en {cidr}"
