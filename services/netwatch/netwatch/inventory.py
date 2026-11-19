"""Inventario: qué servicios y versiones tiene cada equipo de la casa, y si alguna tiene
vulnerabilidades conocidas (CVE).

Una vez por semana (o al pedirlo en el panel) corre `nmap -sV --script vulners` sobre los
equipos encendidos. «vulners» compara la versión de cada servicio con la base pública de
vulners.com (necesita internet; manda solo el nombre y la versión del programa, no tus datos).
Para un análisis a fondo de un equipo usa Greenbone (lab/security/greenbone.sh).
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import threading
import time
import xml.etree.ElementTree as ET

log = logging.getLogger("netwatch.inventory")

SCHEMA = """
CREATE TABLE IF NOT EXISTS inventory (
    mac      TEXT NOT NULL,
    ip       TEXT,
    port     INTEGER NOT NULL,
    proto    TEXT DEFAULT 'tcp',
    service  TEXT DEFAULT '',
    product  TEXT DEFAULT '',
    version  TEXT DEFAULT '',
    cpe      TEXT DEFAULT '',
    cves     TEXT DEFAULT '[]',     -- [{"id": "CVE-…", "cvss": 7.5}]
    max_cvss REAL DEFAULT 0,
    first    REAL,
    last     REAL,
    PRIMARY KEY (mac, port, proto)
);
CREATE TABLE IF NOT EXISTS inventory_scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, hosts INTEGER, services INTEGER, vulnerable INTEGER, secs REAL
);
"""
CVE_RE = re.compile(r"((?:CVE|PRION|GHSA|EDB-ID)[-:][\w-]+)\s+(\d+(?:\.\d+)?)")
PORTS = "21,22,23,25,53,80,81,110,139,143,443,445,554,631,993,995,1883,3000,3306,3389,5000,5001,5432,5900," \
        "6379,8000,8008,8080,8081,8443,8554,8888,9000,9090,9100,10000,32400,34567,49152,62078"


def parse(xml: str) -> list[dict]:
    """Servicios de `nmap -sV --script vulners -oX -`."""
    out: list[dict] = []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return out
    for h in root.iter("host"):
        ip = mac = ""
        for a in h.iter("address"):
            if a.get("addrtype") == "ipv4":
                ip = a.get("addr", "")
            elif a.get("addrtype") == "mac":
                mac = a.get("addr", "").lower()
        for p in h.iter("port"):
            st = p.find("state")
            if st is None or st.get("state") != "open":
                continue
            sv = p.find("service")
            sv = sv if sv is not None else ET.Element("service")
            cves: dict[str, float] = {}
            for sc in p.iter("script"):
                if sc.get("id") != "vulners":
                    continue
                for m in CVE_RE.finditer(sc.get("output", "")):
                    cid = m.group(1).replace("CVE:", "CVE-")
                    if cid.startswith("CVE"):
                        cves[cid] = max(cves.get(cid, 0), float(m.group(2)))
            out.append({"ip": ip, "mac": mac, "port": int(p.get("portid", 0)), "proto": p.get("protocol", "tcp"),
                        "service": sv.get("name", ""), "product": sv.get("product", ""),
                        "version": " ".join(x for x in (sv.get("version", ""), sv.get("extrainfo", "")) if x),
                        "cpe": ",".join(c.text or "" for c in sv.iter("cpe")),
                        "cves": sorted(({"id": k, "cvss": v} for k, v in cves.items()), key=lambda c: -c["cvss"])[:50]})
    return out


class Inventory:
    def __init__(self, db, cfg: dict) -> None:
        self.db = db
        self.cfg = cfg
        with db.lock:
            db.conn.executescript(SCHEMA)
            db.conn.commit()
        self.running = False
        self.progress = ""
        self._lock = threading.Lock()

    # ------------------------------------------------------------ consultas
    def devices(self) -> list[dict]:
        rows = self.db.q("SELECT * FROM inventory ORDER BY mac, port")
        devs = {d["mac"]: d for d in self.db.devices()}
        by: dict[str, dict] = {}
        for r in rows:
            r["cves"] = json.loads(r["cves"] or "[]")
            d = devs.get(r["mac"], {})
            e = by.setdefault(r["mac"], {"mac": r["mac"], "ip": d.get("ip") or r["ip"],
                                         "name": d.get("name") or d.get("hostname") or d.get("vendor") or r["mac"],
                                         "type": d.get("type", ""), "vendor": d.get("vendor", ""),
                                         "online": bool(d.get("online")), "services": [], "max_cvss": 0, "cves": 0})
            e["services"].append(r)
            e["max_cvss"] = max(e["max_cvss"], r["max_cvss"] or 0)
            e["cves"] += len(r["cves"])
        return sorted(by.values(), key=lambda e: (-e["max_cvss"], e["name"]))

    def last_scan(self) -> dict:
        r = self.db.q("SELECT * FROM inventory_scans ORDER BY ts DESC LIMIT 1")
        return {**(r[0] if r else {}), "running": self.running, "progress": self.progress}

    # ------------------------------------------------------------ escaneo
    def due(self) -> bool:
        inv = self.cfg.get("inventory") or {}
        if inv.get("enabled", True) is False or self.running:
            return False
        days = float(inv.get("every_days", 7))
        last = self.last_scan().get("ts") or 0
        return time.time() - last > days * 86400 and 2 <= time.localtime().tm_hour <= 5   # de madrugada

    def scan(self, ips: list[str] | None = None) -> None:
        if not shutil.which("nmap") or not self._lock.acquire(blocking=False):
            return
        self.running, t0 = True, time.time()
        try:
            devs = {d["ip"]: d for d in self.db.devices() if d.get("online") and d.get("ip")}
            targets = ips or list(devs)
            found, vuln = 0, 0
            for i, ip in enumerate(targets, 1):
                self.progress = f"{i}/{len(targets)} · {ip}"
                try:
                    p = subprocess.run(["nmap", "-Pn", "-sV", "--version-light", "-T4", "-p", PORTS, "--open",
                                        "--script", "vulners", "--script-args", "mincvss=4.0", "-oX", "-", ip],
                                       capture_output=True, text=True, timeout=600)
                except subprocess.TimeoutExpired:
                    continue
                svcs = parse(p.stdout)
                mac = (devs.get(ip) or {}).get("mac") or next((s["mac"] for s in svcs if s["mac"]), "")
                if not mac:
                    continue
                for s in svcs:
                    found += 1
                    vuln += self._save(mac, ip, s)
                # lo que ya no está abierto se va del inventario
                ports = {(s["port"], s["proto"]) for s in svcs}
                for r in self.db.q("SELECT port, proto FROM inventory WHERE mac=?", (mac,)):
                    if (r["port"], r["proto"]) not in ports:
                        self.db.x("DELETE FROM inventory WHERE mac=? AND port=? AND proto=?", (mac, r["port"], r["proto"]))
            self.db.x("INSERT INTO inventory_scans (ts, hosts, services, vulnerable, secs) VALUES (?,?,?,?,?)",
                      (time.time(), len(targets), found, vuln, time.time() - t0))
            log.info("Inventario: %d equipos, %d servicios, %d con CVE altas", len(targets), found, vuln)
        finally:
            self.running, self.progress = False, ""
            self._lock.release()

    def _save(self, mac: str, ip: str, s: dict) -> int:
        now = time.time()
        old = self.db.q("SELECT cves FROM inventory WHERE mac=? AND port=? AND proto=?", (mac, s["port"], s["proto"]))
        old_ids = {c["id"] for c in json.loads(old[0]["cves"])} if old else set()
        mx = max((c["cvss"] for c in s["cves"]), default=0)
        self.db.x("""INSERT INTO inventory (mac, ip, port, proto, service, product, version, cpe, cves, max_cvss, first, last)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                     ON CONFLICT(mac, port, proto) DO UPDATE SET ip=excluded.ip, service=excluded.service,
                       product=excluded.product, version=excluded.version, cpe=excluded.cpe, cves=excluded.cves,
                       max_cvss=excluded.max_cvss, last=excluded.last""",
                  (mac, ip, s["port"], s["proto"], s["service"], s["product"], s["version"], s["cpe"],
                   json.dumps(s["cves"]), mx, now, now))
        new_high = [c for c in s["cves"] if c["cvss"] >= 7 and c["id"] not in old_ids]
        if new_high:
            d = self.db.device(mac) or {}
            who = d.get("name") or d.get("hostname") or d.get("vendor") or ip
            what = " ".join(x for x in (s["product"] or s["service"], s["version"]) if x)
            self.db.add_event("critical" if mx >= 9 else "warning", "vulnerable",
                              f"Versión vulnerable en {who}: {what} (puerto {s['port']})",
                              f"{len(new_high)} CVE nuevas con CVSS ≥ 7, la peor {new_high[0]['id']} ({new_high[0]['cvss']}). "
                              "Actualiza ese equipo o servicio. Detalle en Inventario.", mac)
        return 1 if mx >= 7 else 0
