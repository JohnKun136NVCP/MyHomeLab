"""SQLite del bot: publicaciones programadas, respaldos recibidos y estado."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at   REAL NOT NULL,          -- epoch UTC
    networks TEXT NOT NULL,          -- JSON ["fb","ig","tg"]
    text     TEXT DEFAULT '',
    media    TEXT DEFAULT '',        -- ruta local de la foto/video
    media_kind TEXT DEFAULT '',      -- photo | video
    status   TEXT DEFAULT 'pending', -- pending | done | error | cancelled
    result   TEXT DEFAULT '',
    created  REAL
);
CREATE TABLE IF NOT EXISTS backups (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    device  TEXT, os TEXT, path TEXT, size INTEGER, ts REAL,
    forwarded INTEGER DEFAULT 0, source TEXT
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
"""


class Store:
    def __init__(self, path: Path):
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def q(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def x(self, sql, args=()):
        with self.lock:
            c = self.conn.execute(sql, args)
            self.conn.commit()
            return c.lastrowid

    # --------------------------------------------------------------- kv
    def get(self, k, default=None):
        r = self.q("SELECT v FROM kv WHERE k=?", (k,))
        return json.loads(r[0]["v"]) if r else default

    def set(self, k, v):
        self.x("INSERT OR REPLACE INTO kv (k, v) VALUES (?,?)", (k, json.dumps(v)))

    # ------------------------------------------------------------ posts
    def add_post(self, run_at: float, networks: list[str], text: str, media: str = "",
                 media_kind: str = "") -> int:
        return self.x("INSERT INTO posts (run_at, networks, text, media, media_kind, created) "
                      "VALUES (?,?,?,?,?,?)",
                      (run_at, json.dumps(networks), text, media, media_kind, time.time()))

    def due_posts(self, now: float) -> list[dict]:
        return self._posts("WHERE status='pending' AND run_at<=? ORDER BY run_at", (now,))

    def pending_posts(self) -> list[dict]:
        return self._posts("WHERE status='pending' ORDER BY run_at")

    def post(self, pid: int) -> dict | None:
        r = self._posts("WHERE id=?", (pid,))
        return r[0] if r else None

    def _posts(self, where, args=()):
        rows = self.q(f"SELECT * FROM posts {where}", args)
        for r in rows:
            r["networks"] = json.loads(r["networks"])
        return rows

    def finish_post(self, pid: int, status: str, result: str) -> None:
        self.x("UPDATE posts SET status=?, result=? WHERE id=?", (status, result, pid))

    # --------------------------------------------------------- backups
    def add_backup(self, device, os_, path, size, source, forwarded=0) -> int:
        return self.x("INSERT INTO backups (device, os, path, size, ts, forwarded, source) "
                      "VALUES (?,?,?,?,?,?,?)", (device, os_, path, size, time.time(), forwarded, source))

    def last_backups(self) -> list[dict]:
        return self.q("SELECT device, os, MAX(ts) ts, COUNT(*) n, SUM(size) size FROM backups "
                      "GROUP BY device ORDER BY device")

    def backups_of(self, device: str) -> list[dict]:
        return self.q("SELECT * FROM backups WHERE device=? ORDER BY ts DESC", (device,))

    def delete_backup_row(self, bid: int) -> None:
        self.x("DELETE FROM backups WHERE id=?", (bid,))
