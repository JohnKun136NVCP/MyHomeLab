"""Lee el no-break (UPS) desde NUT (upsd, puerto 3493) sin dependencias.

`./scripts/ups.sh` instala NUT en la Pi y escribe en .env:
    NUT_HOST=127.0.0.1   NUT_UPS=ups
netwatch usa la red del host, así que 127.0.0.1:3493 es el upsd de la Pi.
"""
from __future__ import annotations

import os
import socket
import time

FIELDS = {
    "ups.status": "status", "battery.charge": "charge", "battery.runtime": "runtime",
    "ups.load": "load", "input.voltage": "input_voltage", "battery.voltage": "battery_voltage",
    "ups.model": "model", "ups.mfr": "mfr", "device.model": "model",
}

STATUS_TEXT = {
    "OL": "conectado a la luz", "OB": "usando batería", "LB": "batería baja",
    "CHRG": "cargando", "DISCHRG": "descargando", "RB": "cambia la batería",
    "OVER": "sobrecarga", "BYPASS": "en bypass", "OFF": "apagado", "FSD": "apagado forzado",
    "TRIM": "bajando voltaje", "BOOST": "subiendo voltaje", "CAL": "calibrando",
}


def parse(lines: list[str]) -> dict:
    """Convierte la salida de `LIST VAR` en un dict con nombres sencillos."""
    raw: dict[str, str] = {}
    for ln in lines:
        if not ln.startswith("VAR "):
            continue
        try:
            _, _ups, rest = ln.split(" ", 2)
            key, val = rest.split(" ", 1)
        except ValueError:
            continue
        raw[key] = val.strip().strip('"')
    out: dict = {"raw": raw}
    for k, name in FIELDS.items():
        if k in raw and name not in out:
            out[name] = raw[k]
    for k in ("charge", "runtime", "load", "input_voltage", "battery_voltage"):
        if k in out:
            try:
                out[k] = float(out[k])
            except ValueError:
                out.pop(k)
    flags = (out.get("status") or "").split()
    out["flags"] = flags
    out["on_battery"] = "OB" in flags
    out["low_battery"] = "LB" in flags
    out["status_text"] = ", ".join(STATUS_TEXT.get(f, f) for f in flags) or "sin datos"
    return out


def query(host: str, ups: str, port: int = 3493, timeout: float = 3.0) -> dict:
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.sendall(f"LIST VAR {ups}\n".encode())
        buf = b""
        deadline = time.time() + timeout
        while b"END LIST VAR" not in buf and b"ERR" not in buf[:20] and time.time() < deadline:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        try:
            s.sendall(b"LOGOUT\n")
        except OSError:
            pass
    text = buf.decode(errors="replace")
    if text.startswith("ERR"):
        raise RuntimeError(text.strip())
    return parse(text.splitlines())


class Ups:
    """Estado del no-break, con caché corta para no saturar upsd."""

    def __init__(self) -> None:
        self.host = os.environ.get("NUT_HOST", "").strip()
        self.name = os.environ.get("NUT_UPS", "ups").strip() or "ups"
        self._last: dict = {}
        self._at = 0.0

    @property
    def enabled(self) -> bool:
        return bool(self.host)

    def status(self, max_age: float = 20) -> dict:
        if not self.enabled:
            return {"enabled": False}
        if time.time() - self._at < max_age and self._last:
            return self._last
        try:
            st = query(self.host, self.name)
            st.update(enabled=True, ok=True, name=self.name)
        except Exception as e:  # noqa: BLE001
            st = {"enabled": True, "ok": False, "name": self.name, "error": str(e)}
        self._last, self._at = st, time.time()
        return st
