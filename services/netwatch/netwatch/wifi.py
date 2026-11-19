"""Mapa Wi‑Fi: tus redes (SSID), en qué banda y canal transmite cada AP, qué tan llenos están
los canales y qué equipos van en cada red.

Lo que se ve en el aire lo lee el Wi‑Fi de la Pi con NetworkManager (nmcli por D‑Bus); no hace
falta unirse a ninguna red para verla. Qué equipo va en qué SSID no se puede saber desde la red
(solo el AP lo sabe): lo eliges tú en el panel y se guarda.
"""
from __future__ import annotations

import shutil
import subprocess
import time

SCHEMA_COL = "ALTER TABLE devices ADD COLUMN wifi TEXT DEFAULT ''"


def split_nmcli(line: str) -> list[str]:
    """nmcli -t separa con «:» y escapa los «:» de adentro (BSSID) con «\\»."""
    out, cur, esc = [], "", False
    for ch in line:
        if esc:
            cur += ch
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return out


def band(freq_mhz: int) -> str:
    if freq_mhz >= 5925:
        return "6 GHz"
    if freq_mhz >= 4900:
        return "5 GHz"
    return "2.4 GHz"


def parse(out: str) -> list[dict]:
    aps = []
    for ln in out.splitlines():
        f = split_nmcli(ln)
        if len(f) < 7:
            continue
        inuse, ssid, bssid, chan, freq, signal, sec = f[:7]
        try:
            mhz = int(freq.split()[0])
        except (ValueError, IndexError):
            continue
        aps.append({"ssid": ssid or "(oculta)", "bssid": bssid.lower(), "channel": int(chan or 0), "freq": mhz,
                    "band": band(mhz), "signal": int(signal or 0), "security": sec or "abierta",
                    "connected": inuse.strip() == "*"})
    return aps


class Survey:
    def __init__(self, db, cfg: dict) -> None:
        self.db = db
        self.cfg = cfg
        try:
            with db.lock:
                db.conn.execute(SCHEMA_COL)
                db.conn.commit()
        except Exception:  # noqa: BLE001 - ya existe
            pass
        self._cache: dict = {}
        self._at = 0.0
        self.error = ""

    def scan(self, max_age: float = 300) -> list[dict]:
        if self._cache and time.time() - self._at < max_age:
            return self._cache["aps"]
        if not shutil.which("nmcli"):
            self.error = "Falta nmcli en el panel"
            return []
        try:
            p = subprocess.run(["nmcli", "-t", "-f", "IN-USE,SSID,BSSID,CHAN,FREQ,SIGNAL,SECURITY",
                                "dev", "wifi", "list", "--rescan", "auto"], capture_output=True, text=True, timeout=40)
        except subprocess.TimeoutExpired:
            self.error = "La búsqueda de redes tardó demasiado"
            return self._cache.get("aps", [])
        if p.returncode != 0:
            self.error = (p.stderr or "nmcli falló").strip()[:200]
            return self._cache.get("aps", [])
        self.error = ""
        aps = parse(p.stdout)
        self._cache, self._at = {"aps": aps}, time.time()
        return aps

    def summary(self, vendor_of=None) -> dict:
        aps = self.scan()
        mine = set((self.cfg.get("wifi") or {}).get("ssids") or [])
        # Si no dijiste cuáles son las tuyas: las que vienen de los mismos aparatos que la red a la que está la Pi
        if not mine:
            connected = [a for a in aps if a["connected"]]
            if connected:
                pref = {a["bssid"][:8] for a in connected}
                mine = {a["ssid"] for a in aps if a["bssid"][:8] in pref}
        nets: dict[str, dict] = {}
        for a in aps:
            if a["ssid"] not in mine:
                continue
            n = nets.setdefault(a["ssid"], {"ssid": a["ssid"], "bands": set(), "aps": [], "devices": []})
            n["bands"].add(a["band"])
            n["aps"].append({**a, "vendor": vendor_of(a["bssid"]) if vendor_of else ""})
        for d in self.db.devices():
            w = d.get("wifi") or ""
            if w in nets:
                nets[w]["devices"].append({"mac": d["mac"], "name": d.get("name") or d.get("hostname") or d.get("vendor")
                                           or d["mac"], "ip": d.get("ip"), "online": bool(d.get("online"))})
        # Canales: cuántas redes (tuyas y de vecinos) hay en cada uno
        chans: dict[str, dict[int, list]] = {"2.4 GHz": {}, "5 GHz": {}, "6 GHz": {}}
        for a in aps:
            chans[a["band"]].setdefault(a["channel"], []).append({"ssid": a["ssid"], "signal": a["signal"],
                                                                  "mine": a["ssid"] in mine})
        tips = []
        for n in nets.values():
            for ap in n["aps"]:
                if ap["band"] == "2.4 GHz" and ap["channel"] not in (1, 6, 11):
                    tips.append(f"«{n['ssid']}» en 2.4 GHz usa el canal {ap['channel']}: usa 1, 6 u 11 "
                                "para no pisarte con los vecinos.")
                crowd = [x for x in chans[ap["band"]].get(ap["channel"], []) if not x["mine"] and x["signal"] >= 40]
                if len(crowd) >= 3:
                    tips.append(f"El canal {ap['channel']} de «{n['ssid']}» ({ap['band']}) lo comparten "
                                f"{len(crowd)} redes vecinas fuertes: prueba otro.")
        unassigned = [d for d in self.db.devices() if d.get("online") and not d.get("wifi")]
        return {"networks": [{**n, "bands": sorted(n["bands"])} for n in nets.values()],
                "channels": {b: {str(c): v for c, v in sorted(cs.items())} for b, cs in chans.items() if cs},
                "others": len({a["ssid"] for a in aps if a["ssid"] not in mine}),
                "tips": list(dict.fromkeys(tips)), "unassigned": len(unassigned),
                "error": self.error, "ts": self._at, "mine_configured": bool((self.cfg.get("wifi") or {}).get("ssids"))}


_OUI: dict[str, str] = {}


def vendor_of(mac: str) -> str:
    """Fabricante del AP por su BSSID (ieee-data, ya instalado en el panel)."""
    if not _OUI:
        for path in ("/usr/share/ieee-data/oui.txt",):
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    for ln in f:
                        if "(hex)" in ln:
                            k, _, v = ln.partition("(hex)")
                            _OUI[k.strip().replace("-", ":").lower()] = v.strip()
            except OSError:
                pass
        _OUI.setdefault("_", "")
    return _OUI.get(mac[:8].lower(), "")
