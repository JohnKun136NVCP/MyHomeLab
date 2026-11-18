"""Base de datos SQLite de netwatch: dispositivos, eventos y reglas ACL."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    mac         TEXT PRIMARY KEY,
    ip          TEXT,
    name        TEXT,            -- nombre que tú le pones
    hostname    TEXT,
    vendor      TEXT,
    type        TEXT,            -- tipo detectado
    type_manual TEXT,            -- tipo que tú corriges
    ap          TEXT,            -- AP al que lo asignas (para el árbol)
    ports       TEXT DEFAULT '[]',
    known       INTEGER DEFAULT 0,  -- 1 = ya lo revisaste (deja de ser «nuevo»)
    random_mac  INTEGER DEFAULT 0,
    online      INTEGER DEFAULT 0,
    first_seen  REAL,
    last_seen   REAL,
    last_port_scan REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL,
    level   TEXT,      -- info | warning | critical
    kind    TEXT,      -- new_device, arp_spoof, ports_changed, ids, blocked_seen, infra_down…
    title   TEXT,
    detail  TEXT,
    mac     TEXT,
    notified INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
CREATE TABLE IF NOT EXISTS acl (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    mac     TEXT NOT NULL,
    action  TEXT NOT NULL DEFAULT 'block',   -- block
    start   TEXT,     -- "23:00" (opcional: horario)
    end     TEXT,     -- "07:00"
    note    TEXT,
    created REAL
);
CREATE TABLE IF NOT EXISTS inbound (
    ip     TEXT, port INTEGER, proto TEXT, action TEXT,   -- permitida | bloqueada | honeypot
    iface  TEXT DEFAULT '',
    count  INTEGER DEFAULT 0, first REAL, last REAL,
    PRIMARY KEY (ip, port, proto, action)
);
CREATE TABLE IF NOT EXISTS inbound_hourly (
    hour INTEGER, action TEXT, ip TEXT, count INTEGER DEFAULT 0,
    PRIMARY KEY (hour, action, ip)
);
CREATE TABLE IF NOT EXISTS vpn_clients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT, iface TEXT, ip TEXT, public_key TEXT, created REAL
);
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    hash     TEXT NOT NULL,
    role     TEXT NOT NULL DEFAULT 'familia',      -- admin | familia
    created  REAL,
    last_login REAL,
    last_ip  TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS backup_devices (
    name     TEXT PRIMARY KEY,           -- ej. mama-laptop (carpeta en la Pi)
    owner    TEXT NOT NULL,              -- usuario del dashboard
    os       TEXT DEFAULT '',            -- windows | linux | macos | android | web | telegram
    label    TEXT DEFAULT '',
    token    TEXT NOT NULL,
    clave    TEXT DEFAULT '',            -- contraseña de cifrado (para que su dueño restaure)
    created  REAL,
    setup_code TEXT DEFAULT '',
    setup_expires REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS notices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT, ts REAL, text TEXT, level TEXT DEFAULT 'info', seen INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS telegram_links (
    username TEXT PRIMARY KEY, chat_id INTEGER UNIQUE, name TEXT DEFAULT '', linked REAL
);
CREATE TABLE IF NOT EXISTS link_codes (
    code TEXT PRIMARY KEY, username TEXT, expires REAL
);
CREATE TABLE IF NOT EXISTS login_ips (
    username TEXT, ip TEXT, first REAL, last REAL,
    PRIMARY KEY (username, ip)
);
CREATE TABLE IF NOT EXISTS ip_history (
    ip   TEXT,
    mac  TEXT,
    ts   REAL,
    PRIMARY KEY (ip, mac)
);
"""


class DB:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self.lock:
            self.conn.executescript(SCHEMA)
            self.conn.execute("PRAGMA journal_mode=WAL")
            for col in ("network TEXT DEFAULT ''", "hostname_src TEXT DEFAULT ''",
                        "name_tried REAL DEFAULT 0"):
                try:  # bases creadas con versiones anteriores
                    self.conn.execute(f"ALTER TABLE devices ADD COLUMN {col}")
                except sqlite3.OperationalError:
                    pass

    def q(self, sql: str, args=()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def x(self, sql: str, args=()) -> int:
        with self.lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur.lastrowid

    # ---------------------------------------------------------- dispositivos
    def device(self, mac: str) -> dict | None:
        r = self.q("SELECT * FROM devices WHERE mac=?", (mac,))
        return self._dev(r[0]) if r else None

    def devices(self) -> list[dict]:
        return [self._dev(r) for r in self.q("SELECT * FROM devices ORDER BY online DESC, ip")]

    @staticmethod
    def _dev(r: dict) -> dict:
        r["ports"] = json.loads(r.get("ports") or "[]")
        r["display_type"] = r.get("type_manual") or r.get("type") or "Desconocido"
        r["display_name"] = r.get("name") or r.get("hostname") or r.get("vendor") or r["mac"]
        return r

    def upsert_seen(self, mac: str, ip: str, vendor: str, hostname: str, random_mac: bool,
                    network: str = "") -> bool:
        """Marca un dispositivo como visto. Regresa True si es nuevo."""
        now = time.time()
        with self.lock:
            cur = self.conn.execute("SELECT mac, hostname, vendor FROM devices WHERE mac=?", (mac,))
            row = cur.fetchone()
            if row is None:
                self.conn.execute(
                    "INSERT INTO devices (mac, ip, vendor, hostname, random_mac, online, "
                    "first_seen, last_seen, network) VALUES (?,?,?,?,?,1,?,?,?)",
                    (mac, ip, vendor, hostname, int(random_mac), now, now, network))
                new = True
            else:
                self.conn.execute(
                    "UPDATE devices SET ip=?, online=1, last_seen=?, network=?, "
                    "hostname=COALESCE(NULLIF(?, ''), hostname), "
                    "vendor=COALESCE(NULLIF(?, ''), vendor) WHERE mac=?",
                    (ip, now, network, hostname, vendor, mac))
                new = False
            self.conn.execute("INSERT OR REPLACE INTO ip_history (ip, mac, ts) VALUES (?,?,?)",
                              (ip, mac, now))
            self.conn.commit()
        return new

    def mark_offline(self, seen: set[str], network: str | None = None,
                     include_unlabeled: bool = False) -> None:
        """Marca fuera de línea lo que no se vio. Con `network` solo toca esa red
        (los dispositivos de otra red no se apagan solo porque ahora no la vemos)."""
        with self.lock:
            if network is None:
                rows = self.conn.execute("SELECT mac FROM devices WHERE online=1").fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT mac FROM devices WHERE online=1 AND (network=? OR (? AND network=''))",
                    (network, int(include_unlabeled))).fetchall()
            for (mac,) in rows:
                if mac not in seen:
                    self.conn.execute("UPDATE devices SET online=0 WHERE mac=?", (mac,))
            self.conn.commit()

    def update_device(self, mac: str, **fields) -> None:
        allowed = {"name", "type_manual", "ap", "known", "type", "ports", "last_port_scan",
                   "hostname", "hostname_src", "name_tried"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if "ports" in sets:
            sets["ports"] = json.dumps(sets["ports"])
        if not sets:
            return
        cols = ", ".join(f"{k}=?" for k in sets)
        self.x(f"UPDATE devices SET {cols} WHERE mac=?", (*sets.values(), mac))

    def delete_device(self, mac: str) -> None:
        self.x("DELETE FROM devices WHERE mac=?", (mac,))

    def macs_for_ip(self, ip: str, since: float) -> list[str]:
        return [r["mac"] for r in self.q(
            "SELECT mac FROM ip_history WHERE ip=? AND ts>=? ORDER BY ts DESC", (ip, since))]

    # --------------------------------------------------------------- eventos
    def add_event(self, level: str, kind: str, title: str, detail: str = "", mac: str = "") -> int:
        return self.x("INSERT INTO events (ts, level, kind, title, detail, mac) VALUES (?,?,?,?,?,?)",
                      (time.time(), level, kind, title, detail, mac))

    def events(self, limit: int = 200, since: float = 0) -> list[dict]:
        return self.q("SELECT * FROM events WHERE ts>=? ORDER BY ts DESC LIMIT ?", (since, limit))

    def recent_event(self, kind: str, mac: str, within: float) -> bool:
        return bool(self.q("SELECT 1 FROM events WHERE kind=? AND mac=? AND ts>=? LIMIT 1",
                           (kind, mac, time.time() - within)))

    def pending_notifications(self) -> list[dict]:
        return self.q("SELECT * FROM events WHERE notified=0 ORDER BY ts")

    def mark_notified(self, ids: list[int]) -> None:
        if ids:
            self.x(f"UPDATE events SET notified=1 WHERE id IN ({','.join('?' * len(ids))})", ids)

    def event_counts(self) -> dict[str, int]:
        return {r["kind"]: r["n"] for r in self.q("SELECT kind, COUNT(*) n FROM events GROUP BY kind")}

    # ------------------------------------------------------- entrantes
    def add_inbound(self, ip: str, port: int, proto: str, iface: str, action: str) -> None:
        now = time.time()
        hour = int(now // 3600 * 3600)
        with self.lock:
            self.conn.execute(
                "INSERT INTO inbound (ip, port, proto, action, iface, count, first, last) "
                "VALUES (?,?,?,?,?,1,?,?) ON CONFLICT(ip, port, proto, action) DO UPDATE SET "
                "count=count+1, last=excluded.last, iface=COALESCE(NULLIF(excluded.iface,''), iface)",
                (ip, port, proto, action, iface, now, now))
            self.conn.execute(
                "INSERT INTO inbound_hourly (hour, action, ip, count) VALUES (?,?,?,1) "
                "ON CONFLICT(hour, action, ip) DO UPDATE SET count=count+1", (hour, action, ip))
            self.conn.commit()

    def inbound_summary(self, since: float) -> list[dict]:
        """Una fila por IP con sus puertos y acciones desde `since`."""
        rows = self.q("SELECT ip, port, proto, action, iface, count, first, last FROM inbound "
                      "WHERE last>=? ORDER BY last DESC", (since,))
        hourly = {(r["ip"], r["action"]): r["n"] for r in self.q(
            "SELECT ip, action, SUM(count) n FROM inbound_hourly WHERE hour>=? GROUP BY ip, action",
            (int(since // 3600 * 3600),))}
        by_ip: dict[str, dict] = {}
        for r in rows:
            d = by_ip.setdefault(r["ip"], {"ip": r["ip"], "ports": {}, "actions": {}, "first": r["first"],
                                           "last": r["last"], "iface": r["iface"]})
            d["ports"].setdefault(str(r["port"]), {})[r["action"]] = r["count"]
            d["actions"][r["action"]] = hourly.get((r["ip"], r["action"]), 0)
            d["first"] = min(d["first"], r["first"])
            d["last"] = max(d["last"], r["last"])
        for d in by_ip.values():
            d["total"] = sum(d["actions"].values())
        return sorted(by_ip.values(), key=lambda x: -x["total"])

    def inbound_hourly(self, since: float) -> list[dict]:
        return self.q("SELECT hour, action, SUM(count) n FROM inbound_hourly WHERE hour>=? "
                      "GROUP BY hour, action ORDER BY hour", (int(since // 3600 * 3600),))

    def prune_inbound(self, days: int = 14) -> None:
        cut = time.time() - days * 86400
        self.x("DELETE FROM inbound WHERE last<?", (cut,))
        self.x("DELETE FROM inbound_hourly WHERE hour<?", (cut,))

    # ------------------------------------------------------------------- ACL
    def acl(self) -> list[dict]:
        return self.q("SELECT * FROM acl ORDER BY created DESC")

    def add_acl(self, mac: str, start: str = "", end: str = "", note: str = "") -> int:
        return self.x("INSERT INTO acl (mac, action, start, end, note, created) VALUES (?,?,?,?,?,?)",
                      (mac.lower(), "block", start or None, end or None, note, time.time()))

    def delete_acl(self, rule_id: int) -> None:
        self.x("DELETE FROM acl WHERE id=?", (rule_id,))
