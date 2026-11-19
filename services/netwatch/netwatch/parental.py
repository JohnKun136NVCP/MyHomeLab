"""Control parental con AdGuard Home: por equipo, qué apps/sitios se bloquean y a qué horas sí
se permiten, búsqueda segura y bloqueo de sitios para adultos.

Cada regla del panel es un «cliente persistente» de AdGuard llamado «homelab: NOMBRE» con las
MAC/IP de esos equipos. AdGuard llama a su horario «pausa del bloqueo de servicios»: las horas
que eliges como «permitido» son esa pausa; fuera de ellas, lo elegido queda bloqueado.
Funciona en los equipos que usan la Pi como DNS (y sin «DNS seguro» propio del navegador).
"""
from __future__ import annotations

import json
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS parental (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT UNIQUE NOT NULL,
    macs     TEXT DEFAULT '[]',
    services TEXT DEFAULT '[]',       -- ids de AdGuard: youtube, tiktok, instagram, roblox…
    allow    TEXT DEFAULT '{}',       -- {"mon": ["16:00","20:00"], …} horas en que SÍ se permite
    safe_search INTEGER DEFAULT 1,
    adult    INTEGER DEFAULT 1,       -- «control parental» de AdGuard: sitios para adultos
    enabled  INTEGER DEFAULT 1,
    updated  REAL
);
"""
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
PREFIX = "homelab: "


def hhmm_ms(s: str) -> int:
    h, m = (int(x) for x in s.split(":"))
    if not (0 <= h <= 24 and 0 <= m < 60) or h * 60 + m > 1440:
        raise ValueError(f"Hora inválida: {s}")
    return (h * 60 + m) * 60000


def schedule(allow: dict, tz: str) -> dict:
    """Horas permitidas → horario de pausa de AdGuard (ms desde medianoche)."""
    out: dict = {"time_zone": tz or "Local"}
    for d in DAYS:
        r = allow.get(d)
        if r and r[0] and r[1]:
            a, b = hhmm_ms(r[0]), hhmm_ms(r[1])
            if b <= a:
                raise ValueError(f"En {d} la hora final debe ser después de la inicial")
            out[d] = {"start": a, "end": b}
    return out


def client_body(rule: dict, ids: list[str], tz: str) -> dict:
    return {
        "name": PREFIX + rule["name"], "ids": ids, "tags": [],
        "use_global_settings": False, "filtering_enabled": True, "safebrowsing_enabled": True,
        "parental_enabled": bool(rule["adult"]),
        "safe_search": {"enabled": bool(rule["safe_search"]), "bing": True, "duckduckgo": True, "google": True,
                        "pixabay": True, "yandex": True, "youtube": bool(rule["safe_search"])},
        "use_global_blocked_services": False,
        "blocked_services": rule["services"] if rule["enabled"] else [],
        "blocked_services_schedule": schedule(rule["allow"], tz),
        "upstreams": [], "ignore_querylog": False, "ignore_statistics": False,
    }


class Parental:
    def __init__(self, db, adguard, tz: str = "America/Mexico_City") -> None:
        self.db, self.ag, self.tz = db, adguard, tz
        with db.lock:
            db.conn.executescript(SCHEMA)
            db.conn.commit()

    def rules(self) -> list[dict]:
        out = []
        for r in self.db.q("SELECT * FROM parental ORDER BY name"):
            r["macs"], r["services"], r["allow"] = json.loads(r["macs"]), json.loads(r["services"]), json.loads(r["allow"])
            out.append(r)
        return out

    def services(self) -> list[dict]:
        """Catálogo de AdGuard (YouTube, TikTok, Roblox…)."""
        j = self.ag._get("/control/blocked_services/all") or {}
        return [{"id": s["id"], "name": s["name"], "group": s.get("group_id", "")} for s in j.get("blocked_services", [])]

    def _ids(self, macs: list[str]) -> list[str]:
        ids = []
        for m in macs:
            ids.append(m)
            d = self.db.device(m) or {}
            if d.get("ip"):
                ids.append(d["ip"])          # algunos equipos solo se reconocen por IP
        return list(dict.fromkeys(ids))

    def save(self, rule: dict) -> dict:
        name = str(rule.get("name", "")).strip()[:40]
        if not name:
            raise ValueError("Ponle un nombre (por ejemplo «Tablet de Sofi»)")
        macs = [str(m).lower() for m in rule.get("macs") or []]
        if not macs:
            raise ValueError("Elige al menos un equipo")
        allow = {d: rule.get("allow", {}).get(d) for d in DAYS if rule.get("allow", {}).get(d)}
        r = {"name": name, "macs": macs, "services": [str(s) for s in rule.get("services") or []], "allow": allow,
             "safe_search": int(bool(rule.get("safe_search", True))), "adult": int(bool(rule.get("adult", True))),
             "enabled": int(bool(rule.get("enabled", True)))}
        schedule(allow, self.tz)                     # valida las horas antes de tocar AdGuard
        old = self.db.q("SELECT name FROM parental WHERE id=?", (rule["id"],)) if rule.get("id") else []
        self._push(r, old[0]["name"] if old else None)
        if old:
            self.db.x("UPDATE parental SET name=?, macs=?, services=?, allow=?, safe_search=?, adult=?, enabled=?, updated=? "
                      "WHERE id=?", (name, json.dumps(macs), json.dumps(r["services"]), json.dumps(allow),
                                     r["safe_search"], r["adult"], r["enabled"], time.time(), rule["id"]))
        else:
            self.db.x("INSERT INTO parental (name, macs, services, allow, safe_search, adult, enabled, updated) "
                      "VALUES (?,?,?,?,?,?,?,?)", (name, json.dumps(macs), json.dumps(r["services"]), json.dumps(allow),
                                                   r["safe_search"], r["adult"], r["enabled"], time.time()))
        return r

    def _push(self, r: dict, old_name: str | None) -> None:
        if not self.ag.enabled:
            raise RuntimeError("El control parental usa AdGuard: enciende el perfil dns y pon ADGUARD_USER/PASSWORD")
        body = client_body(r, self._ids(r["macs"]), self.tz)
        existing = {c["name"] for c in (self.ag._get("/control/clients") or {}).get("clients") or []}
        key = PREFIX + (old_name or r["name"])
        if key in existing:
            self.ag._post("/control/clients/update", {"name": key, "data": body})
        else:
            self.ag._post("/control/clients/add", body)

    def delete(self, rid: int) -> None:
        r = self.db.q("SELECT name FROM parental WHERE id=?", (rid,))
        if r and self.ag.enabled:
            try:
                self.ag._post("/control/clients/delete", {"name": PREFIX + r[0]["name"]})
            except Exception:  # noqa: BLE001 - ya no estaba en AdGuard
                pass
        self.db.x("DELETE FROM parental WHERE id=?", (rid,))

    def resync(self) -> None:
        """Si un equipo cambió de IP, actualiza AdGuard (lo llama el monitor de vez en cuando)."""
        for r in self.rules():
            try:
                self._push(r, None)
            except Exception:  # noqa: BLE001
                pass
