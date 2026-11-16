"""Recepción y organización de respaldos por dispositivo.

Estructura en disco:
    /data/backups/<dispositivo>/<AAAA-MM-DD_HHMMSS>/<archivo>
"""
from __future__ import annotations

import datetime as dt
import hmac
import logging
import os
import re
import shutil
from pathlib import Path

log = logging.getLogger("bot.backups")
SAFE = re.compile(r"[^\w.\-()+ áéíóúñÁÉÍÓÚÑ]")


def safe_name(name: str) -> str:
    name = os.path.basename(name.replace("\\", "/")).strip()
    name = SAFE.sub("_", name)[:180].lstrip(".")
    return name or "archivo"


def env_key(device: str) -> str:
    return "BACKUP_TOKEN_" + re.sub(r"[^A-Z0-9]", "_", device.upper())


class Backups:
    def __init__(self, cfg: dict, store):
        b = cfg.get("backups", {}) or {}
        self.root = Path(b.get("dir", "/data/backups"))
        self.root.mkdir(parents=True, exist_ok=True)
        self.keep = int(b.get("keep_per_device", 5))
        self.forward = bool(b.get("forward_to_telegram", True))
        self.devices = {d["name"]: d for d in (b.get("devices") or [])}
        self.store = store

    def auth(self, device: str, token: str) -> bool:
        """Cada dispositivo tiene su propio token (BACKUP_TOKEN_<NOMBRE> en .env)."""
        expected = os.environ.get(env_key(device), "")
        return bool(device in self.devices and expected and token
                    and hmac.compare_digest(expected, token))

    def new_dir(self, device: str) -> Path:
        stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        d = self.root / safe_name(device) / stamp
        n = 1
        while d.exists():                       # dos respaldos en el mismo segundo
            d = d.with_name(f"{stamp}_{n}")
            n += 1
        d.mkdir(parents=True)
        return d

    def target(self, device: str, filename: str, session: str = "") -> Path:
        """Las partes de un mismo respaldo (.7z.001, .002…) van a la misma carpeta:
        el cliente manda el mismo X-Backup-Session en todas."""
        dev_dir = self.root / safe_name(device)
        if session:
            sess = safe_name(session)
            existing = [p for p in dev_dir.glob("*/.session") if p.read_text().strip() == sess] \
                if dev_dir.exists() else []
            d = existing[0].parent if existing else self.new_dir(device)
            (d / ".session").write_text(sess)
        else:
            d = self.new_dir(device)
        return d / safe_name(filename)

    def prune(self, device: str) -> list[str]:
        """Borra los respaldos más viejos de ese dispositivo (deja `keep_per_device`)."""
        dev_dir = self.root / safe_name(device)
        if not dev_dir.exists():
            return []
        dirs = sorted((p for p in dev_dir.iterdir() if p.is_dir()), reverse=True)
        removed = []
        for old in dirs[self.keep:]:
            shutil.rmtree(old, ignore_errors=True)
            removed.append(old.name)
        if removed:
            for row in self.store.q("SELECT id, path FROM backups WHERE device=?", (device,)):
                if not Path(row["path"]).exists():
                    self.store.delete_backup_row(row["id"])
        return removed

    def summary(self) -> list[dict]:
        out = []
        known = set(self.devices)
        rows = {r["device"]: r for r in self.store.last_backups()}
        for name in sorted(known | set(rows)):
            r = rows.get(name, {})
            out.append({"device": name, "os": self.devices.get(name, {}).get("os") or r.get("os", ""),
                        "last": r.get("ts"), "count": r.get("n", 0), "size": r.get("size") or 0})
        return out
