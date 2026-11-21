"""Cosas chicas del homelab que viven en el panel:

* Outages     — bitácora de caídas del internet (cuándo y cuánto), para reclamarle al proveedor.
* Syslog      — recibe los registros del módem, los Tenda y lo que mande syslog (UDP 514).
* Watch       — certificados por vencer y cambios en el DNS de tus dominios.
* Capture     — graba unos segundos del tráfico de un equipo en .pcap (para Wireshark).
* guest_qr    — el QR de la Wi-Fi de invitados.
* digest      — resumen semanal de seguridad (lo manda el bot el domingo).
"""
from __future__ import annotations

import contextlib
import datetime as dt
import ipaddress
import json
import logging
import os
import re
import shutil
import socket
import ssl
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger("netwatch.extras")
RUN = Path(os.environ.get("HOMELAB_RUN", "/homelab/run"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS outages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, target TEXT, start REAL, end REAL
);
CREATE TABLE IF NOT EXISTS syslog (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, host TEXT, severity INTEGER, facility INTEGER,
    tag TEXT, msg TEXT
);
CREATE INDEX IF NOT EXISTS syslog_ts ON syslog(ts);
CREATE TABLE IF NOT EXISTS watch_state (
    key TEXT PRIMARY KEY, value TEXT, ts REAL
);
"""


def ensure(db) -> None:
    with db.lock:
        db.conn.executescript(SCHEMA)
        db.conn.commit()


# ===================================================================== caídas del internet
class Outages:
    """Se alimenta del ping a «internet» (ping_targets): abre una caída cuando deja de
    responder y la cierra cuando vuelve. Caídas de menos de 1 minuto no cuentan (un ping perdido)."""

    MIN = 60

    def __init__(self, db) -> None:
        self.db = db
        ensure(db)
        self.down_since: dict[str, float] = {}

    def update(self, target: str, up: bool, now: float | None = None) -> None:
        now = now or time.time()
        if not up:
            self.down_since.setdefault(target, now)
            return
        start = self.down_since.pop(target, None)
        if start and now - start >= self.MIN:
            self.db.x("INSERT INTO outages (target, start, end) VALUES (?,?,?)", (target, start, now))
            mins = round((now - start) / 60)
            self.db.add_event("warning", "outage", f"Se fue el internet {mins} min",
                              f"Sin respuesta de «{target}» de {time.strftime('%H:%M', time.localtime(start))} "
                              f"a {time.strftime('%H:%M', time.localtime(now))}. Queda en Internet → Caídas.")

    def list(self, since: float = 0) -> list[dict]:
        rows = self.db.q("SELECT * FROM outages WHERE start >= ? ORDER BY start DESC", (since,))
        cur = [{"id": 0, "target": t, "start": s, "end": None} for t, s in self.down_since.items()
               if time.time() - s >= self.MIN]
        return cur + rows

    def stats(self, since: float) -> dict:
        rows = [r for r in self.list(since) if r["end"]]
        total = sum(r["end"] - r["start"] for r in rows)
        longest = max((r["end"] - r["start"] for r in rows), default=0)
        span = max(time.time() - since, 1)
        return {"count": len(rows), "minutes": round(total / 60), "longest_min": round(longest / 60),
                "uptime_pct": round(100 * (1 - total / span), 3)}


# ===================================================================== syslog
SYSLOG_RE = re.compile(r"^<(\d{1,3})>(?:1 )?(?:(\w{3}\s+\d+\s[\d:]{8})\s)?(?:(\S+)\s)?(.*)$", re.S)
ALERT_WORDS = re.compile(r"(login fail|failed login|authentication fail|invalid (?:user|password)|wrong password|"
                         r"brute|attack|intrus|dos attack|flood|port ?scan|contraseña incorrecta)", re.I)


def parse_syslog(data: bytes, src: str) -> dict:
    text = data.decode("utf-8", "replace").strip()
    m = SYSLOG_RE.match(text)
    pri, rest = (int(m.group(1)), m.group(4)) if m else (13, text)
    tag = ""
    t = re.match(r"([\w./-]{1,32})(?:\[\d+\])?:\s*(.*)$", rest, re.S)
    if t:
        tag, rest = t.group(1), t.group(2)
    return {"ts": time.time(), "host": src, "severity": pri & 7, "facility": pri >> 3, "tag": tag,
            "msg": rest[:2000]}


class Syslog:
    """Servidor syslog (UDP 514). Guarda lo último (50 000 líneas) y avisa de intentos de entrar."""

    KEEP = 50_000

    def __init__(self, db, port: int = 514, names=None) -> None:
        self.db, self.port, self.names = db, port, names or (lambda ip: ip)
        ensure(db)
        self.error = ""
        self.count = 0

    def start(self) -> None:
        threading.Thread(target=self._serve, name="syslog", daemon=True).start()

    def _serve(self) -> None:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("0.0.0.0", self.port))
        except OSError as e:
            self.error = f"No pude abrir el puerto {self.port}/udp: {e}"
            log.warning(self.error)
            return
        while True:
            try:
                data, (ip, _) = s.recvfrom(8192)
                self.handle(data, ip)
            except Exception:  # noqa: BLE001 - un paquete raro no tumba el servidor
                log.exception("syslog")

    def handle(self, data: bytes, ip: str) -> None:
        r = parse_syslog(data, ip)
        self.db.x("INSERT INTO syslog (ts, host, severity, facility, tag, msg) VALUES (?,?,?,?,?,?)",
                  (r["ts"], r["host"], r["severity"], r["facility"], r["tag"], r["msg"]))
        self.count += 1
        if self.count % 1000 == 0:
            self.db.x("DELETE FROM syslog WHERE id <= (SELECT MAX(id) FROM syslog) - ?", (self.KEEP,))
        if ALERT_WORDS.search(r["msg"]) and not self.db.recent_event("syslog_alert", ip, 1800):
            self.db.add_event("warning", "syslog_alert", f"{self.names(ip)} reporta: {r['msg'][:80]}",
                              f"Registro de {ip}: {r['msg'][:500]}", ip)

    def query(self, host: str = "", q: str = "", limit: int = 500) -> list[dict]:
        sql, args = "SELECT * FROM syslog WHERE 1=1", []
        if host:
            sql += " AND host=?"
            args.append(host)
        if q:
            sql += " AND (msg LIKE ? OR tag LIKE ?)"
            args += [f"%{q}%", f"%{q}%"]
        return self.db.q(sql + " ORDER BY id DESC LIMIT ?", (*args, limit))

    def hosts(self) -> list[dict]:
        return self.db.q("SELECT host, COUNT(*) n, MAX(ts) last FROM syslog GROUP BY host ORDER BY last DESC")


# ===================================================================== certificados y DNS
def cert_days(host: str, port: int = 443, timeout: float = 8) -> tuple[float | None, str]:
    """Días que le quedan al certificado de host:port (y quién lo emitió)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE           # queremos verlo aunque esté vencido
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock, \
                ctx.wrap_socket(sock, server_hostname=host) as s:
            der = s.getpeercert(binary_form=True)
    except OSError as e:
        return None, str(e)
    try:
        import ssl as _ssl
        pem = _ssl.DER_cert_to_PEM_cert(der)
        info = _decode_cert(pem)
        end = dt.datetime.strptime(info["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=dt.timezone.utc)
        issuer = dict(x[0] for x in info.get("issuer", ())).get("organizationName", "")
        return round((end - dt.datetime.now(dt.timezone.utc)).total_seconds() / 86400, 1), issuer
    except Exception as e:  # noqa: BLE001
        return None, f"no pude leer el certificado: {e}"


def _decode_cert(pem: str) -> dict:
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False) as f:
        f.write(pem)
        path = f.name
    try:
        return ssl._ssl._test_decode_cert(path)        # noqa: SLF001 - parte estable de CPython
    finally:
        os.unlink(path)


def resolve(name: str) -> list[str]:
    try:
        return sorted({a[4][0] for a in socket.getaddrinfo(name, None)})
    except OSError:
        return []


class Watch:
    """homelab.yml → watch: {domains: [miempresa.mx], certs: [miempresa.mx, panel.casa.lan:8443]}"""

    def __init__(self, db, cfg: dict) -> None:
        self.db, self.cfg = db, cfg
        ensure(db)
        self.last = 0.0
        self.results: dict = {"certs": [], "dns": []}

    def _get(self, key: str):
        r = self.db.q("SELECT value FROM watch_state WHERE key=?", (key,))
        return json.loads(r[0]["value"]) if r else None

    def _set(self, key: str, value) -> None:
        self.db.x("INSERT INTO watch_state (key, value, ts) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET "
                  "value=excluded.value, ts=excluded.ts", (key, json.dumps(value), time.time()))

    def run(self) -> dict:
        w = self.cfg.get("watch") or {}
        self.last = time.time()
        certs, dns = [], []
        for item in w.get("certs") or w.get("domains") or []:
            host, _, port = str(item).partition(":")
            days, issuer = cert_days(host, int(port or 443))
            certs.append({"target": str(item), "days": days, "issuer": issuer if days is not None else "",
                          "error": "" if days is not None else issuer})
            if days is not None and days < 21:
                lvl = "critical" if days < 7 else "warning"
                if not self.db.recent_event("cert_expiring", str(item), 86400 * (1 if days < 7 else 7)):
                    self.db.add_event(lvl, "cert_expiring", f"El certificado de {item} vence en {int(days)} días",
                                      f"Emisor: {issuer}. Renuévalo (certbot renew / Caddy lo hace solo si está bien).",
                                      str(item))
        for name in w.get("domains") or []:
            now_ips = resolve(str(name))
            prev = self._get(f"dns:{name}")
            dns.append({"domain": name, "ips": now_ips, "prev": prev or []})
            if now_ips and prev and sorted(prev) != now_ips:
                self.db.add_event("critical" if not set(prev) & set(now_ips) else "warning", "dns_changed",
                                  f"Cambió a dónde apunta {name}",
                                  f"Antes: {', '.join(prev)} · ahora: {', '.join(now_ips)}. Si no lo cambiaste tú "
                                  "(o tu proveedor), revisa tu cuenta del dominio: puede ser un secuestro de DNS.",
                                  str(name))
            if now_ips:
                self._set(f"dns:{name}", now_ips)
        self.results = {"certs": certs, "dns": dns, "ts": self.last}
        return self.results


# ===================================================================== captura de tráfico
CAP_DIR = RUN / "captures"


class Capture:
    def __init__(self) -> None:
        self.running: dict | None = None

    @staticmethod
    def available() -> bool:
        return shutil.which("tcpdump") is not None

    def start(self, iface: str, ip: str = "", seconds: int = 60, port: int = 0) -> dict:
        if self.running:
            raise RuntimeError("Ya hay una captura en curso")
        if not self.available():
            raise RuntimeError("Falta tcpdump en el panel: ./hl update --all")
        if ip:
            ipaddress.ip_address(ip)
        seconds = max(5, min(int(seconds), 300))
        CAP_DIR.mkdir(parents=True, exist_ok=True)
        name = f"captura-{time.strftime('%Y%m%d-%H%M%S')}{'-' + ip if ip else ''}.pcap"
        flt = []
        if ip:
            flt += ["host", ip]
        if port:
            flt += (["and"] if flt else []) + ["port", str(int(port))]
        cmd = ["timeout", str(seconds), "tcpdump", "-i", iface, "-n", "-s", "0", "-c", "200000",
               "-w", str(CAP_DIR / name), *flt]
        self.running = {"file": name, "ip": ip, "iface": iface, "started": time.time(), "seconds": seconds}

        def run() -> None:
            try:
                subprocess.run(cmd, capture_output=True, timeout=seconds + 30)
            except (OSError, subprocess.TimeoutExpired) as e:
                log.warning("captura: %s", e)
            finally:
                self.running = None
                self.prune()
        threading.Thread(target=run, name="captura", daemon=True).start()
        return self.running

    @staticmethod
    def files() -> list[dict]:
        if not CAP_DIR.is_dir():
            return []
        return [{"name": f.name, "size": f.stat().st_size, "ts": f.stat().st_mtime}
                for f in sorted(CAP_DIR.glob("*.pcap"), key=lambda f: -f.stat().st_mtime)]

    @staticmethod
    def path(name: str) -> Path | None:
        if not re.fullmatch(r"captura-[\w.-]+\.pcap", name):
            return None
        p = CAP_DIR / name
        return p if p.is_file() else None

    @staticmethod
    def prune(keep: int = 20) -> None:
        for f in Capture.files()[keep:]:
            with contextlib.suppress(OSError):
                (CAP_DIR / f["name"]).unlink()


# ===================================================================== Wi-Fi de invitados
def wifi_qr_text(ssid: str, password: str, security: str = "WPA", hidden: bool = False) -> str:
    def esc(s: str) -> str:
        return re.sub(r'([\\;,:"])', r"\\\1", s)
    sec = "nopass" if not password else security
    return f"WIFI:T:{sec};S:{esc(ssid)};" + (f"P:{esc(password)};" if password else "") + \
        ("H:true;" if hidden else "") + ";"


# ===================================================================== resumen semanal
def digest(db, mon, since: float) -> dict:
    """Lo importante de la semana, para el bot (y para la página de Seguridad)."""
    ev = db.q("SELECT * FROM events WHERE ts >= ? AND COALESCE(fp,0)=0", (since,))
    by = lambda k: [e for e in ev if e["kind"] == k]  # noqa: E731
    out = {
        "since": since,
        "critical": sum(1 for e in ev if e["level"] == "critical"),
        "new_devices": [e["title"] for e in by("new_device")][:15],
        "honeypot": len(by("honeypot")),
        "arp": len(by("arp_spoof")),
        "rogue": [e["title"] for e in ev if e["kind"] in ("rogue_dhcp", "wifi_twin", "wifi_new")][:10],
        "dns_danger": len(by("dns_danger")),
        "vulnerable": [e["title"] for e in by("vulnerable")][:10],
        "outages": mon.outages.stats(since) if hasattr(mon, "outages") else {},
        "syslog_alerts": len(by("syslog_alert")),
        "certs": [c for c in (mon.watch.results.get("certs") or []) if c.get("days") is not None and c["days"] < 30]
        if hasattr(mon, "watch") else [],
    }
    try:
        tv = json.loads((RUN / "trivy.json").read_text())
        out["images_vuln"] = [i for i in tv.get("images", []) if i["critical"] or i["high"]][:10]
    except (OSError, ValueError):
        out["images_vuln"] = []
    try:
        out["disks"] = json.loads((RUN / "disks.json").read_text())
    except (OSError, ValueError):
        out["disks"] = {}
    return out
