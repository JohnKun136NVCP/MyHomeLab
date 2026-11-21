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
        rate = f[7] if len(f) > 7 else ""
        try:
            mhz = int(freq.split()[0])
        except (ValueError, IndexError):
            continue
        aps.append({"ssid": ssid or "(oculta)", "bssid": bssid.lower(), "channel": int(chan or 0), "freq": mhz,
                    "band": band(mhz), "signal": int(signal or 0), "security": sec or "abierta",
                    "connected": inuse.strip() == "*", "rate": rate.strip()})
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

    def scan(self, max_age: float = 300, force: bool = False) -> list[dict]:
        if self._cache and time.time() - self._at < max_age and not force:
            return self._cache["aps"]
        if not shutil.which("nmcli"):
            self.error = "Falta nmcli en el panel"
            return []
        try:
            p = subprocess.run(["nmcli", "-t", "-f", "IN-USE,SSID,BSSID,CHAN,FREQ,SIGNAL,SECURITY,RATE",
                                "dev", "wifi", "list", "--rescan", "yes" if force or not self._cache else "auto"],
                               capture_output=True, text=True, timeout=40)
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

    def my_ssids(self) -> set[str]:
        mine = set((self.cfg.get("wifi") or {}).get("ssids") or [])
        for n in (self.cfg.get("network") or {}).get("networks") or []:
            if n.get("ssid"):
                mine.add(n["ssid"])
        return mine

    def summary(self, vendor_of=None, force: bool = False) -> dict:
        aps = self.scan(force=force)
        mine = self.my_ssids()
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
                "error": self.error, "ts": self._at, "mine_configured": bool(self.my_ssids()),
                "optimize": recommend(aps, mine), "seen": self.seen_list(vendor_of)}

    # ------------------------------------------------ routers/AP nuevos que aparecen en el aire
    def track(self, notify: bool = True) -> list[dict]:
        """Guarda cada BSSID que se ve. La primera vez solo aprende (no avisa).

        * Tu SSID desde un aparato de otro fabricante → posible «gemelo malvado» (crítico).
        * Una red nueva con señal fuerte (≥ 60 %) → casi seguro está dentro de la casa: alguien
          conectó un router o AP (aviso).
        """
        aps = self.scan()
        if not aps:
            return []
        mine = self.my_ssids()
        if not mine:
            con = [a for a in aps if a["connected"]]
            pref = {a["bssid"][:8] for a in con}
            mine = {a["ssid"] for a in aps if a["bssid"][:8] in pref}
        with self.db.lock:
            self.db.conn.execute(SEEN_SCHEMA)
            self.db.conn.commit()
        first_time = not self.db.q("SELECT 1 FROM wifi_seen LIMIT 1")
        known = {r["bssid"]: r for r in self.db.q("SELECT * FROM wifi_seen")}
        my_ouis = {b[:8] for b, r in known.items() if r["ssid"] in mine} | \
                  {a["bssid"][:8] for a in aps if a["ssid"] in mine and a["bssid"] in known}
        if first_time:
            my_ouis |= {a["bssid"][:8] for a in aps if a["ssid"] in mine}
        new = []
        now = time.time()
        for a in aps:
            r = known.get(a["bssid"])
            if r:
                self.db.x("UPDATE wifi_seen SET ssid=?, last=?, channel=?, max_signal=MAX(max_signal, ?) WHERE bssid=?",
                          (a["ssid"], now, a["channel"], a["signal"], a["bssid"]))
                continue
            status = ""
            if not first_time:
                if a["ssid"] in mine and a["bssid"][:8] not in my_ouis:
                    status = "twin"
                elif a["ssid"] not in mine and a["signal"] >= 60:
                    status = "new"
            self.db.x("INSERT INTO wifi_seen (bssid, ssid, channel, band, first, last, max_signal, status, ack) "
                      "VALUES (?,?,?,?,?,?,?,?,?)", (a["bssid"], a["ssid"], a["channel"], a["band"], now, now,
                                                     a["signal"], status, 1 if first_time or not status else 0))
            if status:
                new.append({**a, "status": status})
        if notify:
            for a in new:
                if a["status"] == "twin":
                    self.db.add_event("critical", "wifi_twin", f"¿Gemelo malvado? Alguien transmite tu red «{a['ssid']}»",
                                      f"BSSID {a['bssid']} ({vendor_of(a['bssid']) or 'fabricante desconocido'}), canal "
                                      f"{a['channel']}, señal {a['signal']} %. Tus AP de esa red son de otro fabricante. "
                                      "Si compraste un AP nuevo, márcalo «es mío» en Red → Wi-Fi.", a["bssid"])
                else:
                    # ¿Es un aparato conectado a tu red? (su MAC de cable suele diferir solo en el último byte)
                    dev = next((d for d in self.db.devices() if d["mac"][:14] == a["bssid"][:14]), None)
                    where = (f" Está conectado a tu red como {dev.get('ip')} "
                             f"({dev.get('display_name') or dev.get('vendor') or dev['mac']}).") if dev else ""
                    self.db.add_event("warning", "wifi_new", f"Nuevo router/AP inalámbrico cerca: «{a['ssid']}»",
                                      f"BSSID {a['bssid']} ({vendor_of(a['bssid']) or '¿?'}), canal {a['channel']} "
                                      f"({a['band']}), señal {a['signal']} %: con esa señal casi seguro está dentro de "
                                      f"la casa.{where} Si es tuyo o de un vecino, márcalo en Red → Wi-Fi.", a["bssid"])
        return new

    def seen_list(self, vendor_of=None) -> list[dict]:
        try:
            rows = self.db.q("SELECT * FROM wifi_seen WHERE status != '' ORDER BY ack, last DESC LIMIT 100")
        except Exception:  # noqa: BLE001 - aún no hay tabla
            return []
        return [{**r, "vendor": vendor_of(r["bssid"]) if vendor_of else ""} for r in rows]

    def ack(self, bssid: str, verdict: str) -> None:
        """verdict: mine (es mío) · neighbor (de un vecino) — deja de marcarlo."""
        self.db.x("UPDATE wifi_seen SET ack=1, status=? WHERE bssid=?",
                  ("mine" if verdict == "mine" else "neighbor", bssid.lower()))


SEEN_SCHEMA = """
CREATE TABLE IF NOT EXISTS wifi_seen (
    bssid TEXT PRIMARY KEY, ssid TEXT, channel INTEGER, band TEXT,
    first REAL, last REAL, max_signal INTEGER,
    status TEXT DEFAULT '',     -- '' normal · new · twin · mine · neighbor
    ack INTEGER DEFAULT 0
);
"""

# ----------------------------------------------------------------- optimizar canales
CH24 = (1, 6, 11)
CH5 = (36, 40, 44, 48, 149, 153, 157, 161)        # sin DFS: los entiende cualquier equipo


def _w(signal: int) -> float:
    """Cuánto estorba una red: más si se oye fuerte."""
    return (max(signal, 1) / 100) ** 2


def _cost(band_: str, ch: int, others: list[dict]) -> float:
    c = 0.0
    for o in others:
        if band_ == "2.4 GHz":
            d = abs(o["channel"] - ch)
            if d < 5:                                     # en 2.4 GHz se enciman hasta 4 canales
                c += _w(o["signal"]) * (1.0 if d == 0 else 0.6 * (5 - d) / 5)
        elif band_ == "5 GHz":
            same80 = (o["channel"] - 36) // 16 == (ch - 36) // 16 if o["channel"] < 149 and ch < 149 \
                else (o["channel"] >= 149 and ch >= 149)
            if o["channel"] == ch:
                c += _w(o["signal"])
            elif same80:                                  # con 80 MHz se comparten 4 canales
                c += 0.5 * _w(o["signal"])
    return c


def recommend(aps: list[dict], mine: set[str]) -> dict:
    """Canal sugerido para cada AP tuyo + revisiones que hacen lenta una Wi-Fi."""
    my = sorted([a for a in aps if a["ssid"] in mine], key=lambda a: -a["signal"])
    out, tips = [], []
    taken: list[dict] = []
    for a in my:
        if a["band"] not in ("2.4 GHz", "5 GHz"):
            continue
        cands = CH24 if a["band"] == "2.4 GHz" else CH5
        others = [o for o in aps if o["band"] == a["band"] and o["bssid"] != a["bssid"]
                  and o["ssid"] not in mine] + [t for t in taken if t["band"] == a["band"]]
        now = _cost(a["band"], a["channel"], others)
        best = min(cands, key=lambda c: (_cost(a["band"], c, others), c != a["channel"]))
        bcost = _cost(a["band"], best, others)
        rec = {"ssid": a["ssid"], "bssid": a["bssid"], "band": a["band"], "channel": a["channel"],
               "signal": a["signal"], "rate": a.get("rate", ""), "best": a["channel"], "busy_now": round(now, 2),
               "busy_best": round(bcost, 2)}
        if best != a["channel"] and now - bcost > 0.15:
            rec["best"] = best
            rec["why"] = (f"El canal {a['channel']} está ocupado por "
                          f"{sum(1 for o in others if o['channel'] == a['channel'])} red(es) en el mismo canal "
                          f"o encimadas; el {best} está más libre.")
        if a["band"] == "2.4 GHz" and a["channel"] not in CH24:
            rec["best"] = best
            rec["why"] = f"En 2.4 GHz usa 1, 6 u 11: el {a['channel']} se encima con los dos lados."
        taken.append({**a, "channel": rec["best"]})
        out.append(rec)
        if a["connected"] and a.get("rate"):
            try:
                mbps = float(a["rate"].split()[0])
            except (ValueError, IndexError):
                mbps = 0
            if 0 < mbps <= 72:
                tips.append(f"La Pi se conecta a «{a['ssid']}» a solo {a['rate']}: está lejos, hay paredes o el AP "
                            "usa 20 MHz en 2.4 GHz. Más cerca o en 5 GHz va mucho más rápido.")
        if a["signal"] < 40:
            tips.append(f"«{a['ssid']}» ({a['band']}, {a['bssid']}) llega débil a la Pi ({a['signal']} %): un AP "
                        "con poca señal hace que los equipos bajen de velocidad; acércalo o súbele potencia.")
        if (a["security"] or "").strip() in ("", "abierta", "--") or "WEP" in a["security"]:
            tips.append(f"«{a['ssid']}» no tiene contraseña segura ({a['security'] or 'abierta'}): usa WPA2/WPA3.")
        elif "WPA1" in a["security"] or a["security"].strip() == "WPA":
            tips.append(f"«{a['ssid']}» usa WPA viejo: ponlo en WPA2-PSK (AES) o WPA2/WPA3; TKIP limita a 54 Mbit/s.")
    bands = {}
    for a in my:
        bands.setdefault(a["ssid"], set()).add(a["band"])
    for ssid, bs in bands.items():
        if bs == {"2.4 GHz"}:
            tips.append(f"«{ssid}» solo está en 2.4 GHz: si el aparato tiene 5 GHz, actívalo (mismo nombre con «band "
                        "steering» o «{ssid}-5G»). 5 GHz es varias veces más rápido de cerca.")
    same = {}
    for a in my:
        same.setdefault((a["band"], a["channel"]), []).append(a)
    for (b, ch), lst in same.items():
        if len({x["bssid"][:14] for x in lst}) > 1:
            tips.append(f"Dos de tus AP transmiten en el mismo canal {ch} ({b}): {', '.join(x['ssid'] for x in lst)}. "
                        "Sepáralos (ver «canal sugerido»).")
    return {"aps": out, "tips": list(dict.fromkeys(tips))}


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
