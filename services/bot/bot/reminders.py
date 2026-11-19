"""Recordatorios de la casa: pagos, citas y cumpleaños, por Telegram.

    /recordar mañana 9:00 pagar la luz
    /recordar 15/12 18:00 cita con el dentista       (sin hora: 9:00)
    /recordar en 2h sacar la ropa                     (m, min, h, d)
    /recordar cada día 8:00 tomar agua
    /recordar cada lunes 7:30 sacar la basura
    /recordar cada mes 5 9:00 pagar el internet
    /recordar cada año 15/03 cumpleaños de mamá
Cada chat ve y borra solo los suyos.
"""
from __future__ import annotations

import calendar
import datetime as dt
import re
import time
from zoneinfo import ZoneInfo

SCHEMA = """
CREATE TABLE IF NOT EXISTS reminders (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    chat    INTEGER NOT NULL,
    text    TEXT NOT NULL,
    run_at  REAL NOT NULL,            -- epoch del próximo aviso
    rep     TEXT DEFAULT '',          -- '' | daily | weekly:0 | monthly:5 | yearly:03-15
    hhmm    TEXT DEFAULT '09:00',
    created REAL
);
"""
DEFAULT_TIME = "09:00"
WEEKDAYS = {"lunes": 0, "martes": 1, "miercoles": 2, "miércoles": 2, "jueves": 3, "viernes": 4,
            "sabado": 5, "sábado": 5, "domingo": 6}
WD_NAME = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
UNITS = {"m": 60, "min": 60, "mins": 60, "minuto": 60, "minutos": 60, "h": 3600, "hr": 3600, "hora": 3600,
         "horas": 3600, "d": 86400, "dia": 86400, "día": 86400, "dias": 86400, "días": 86400}
TIME_RE = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", re.I)
USAGE = ("Ejemplos:\n<code>/recordar mañana 9:00 pagar la luz</code>\n<code>/recordar 15/12 18:00 dentista</code>\n"
         "<code>/recordar en 2h sacar la ropa</code>\n<code>/recordar cada mes 5 pagar el internet</code>\n"
         "<code>/recordar cada año 15/03 cumpleaños de mamá</code>")


def _hhmm(tok: str) -> str | None:
    m = TIME_RE.match(tok)
    if not m or (":" not in tok and not m.group(3)):
        return None                     # «5» solo no es hora (podría ser el día del mes)
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    if m.group(3):
        h = h % 12 + (12 if m.group(3).lower() == "pm" else 0)
    if h > 23 or mi > 59:
        return None
    return f"{h:02d}:{mi:02d}"


def _date(tok: str, now: dt.datetime) -> dt.date | None:
    t = tok.lower()
    if t == "hoy":
        return now.date()
    if t in ("mañana", "manana"):
        return now.date() + dt.timedelta(days=1)
    if t in ("pasado", "pasadomañana"):
        return now.date() + dt.timedelta(days=2)
    if re.match(r"^\d{4}-\d{2}-\d{2}$", t):
        return dt.date.fromisoformat(t)
    m = re.match(r"^(\d{1,2})/(\d{1,2})(?:/(\d{4}))?$", t)
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        y = int(m.group(3)) if m.group(3) else now.year
        date = dt.date(y, mo, d)
        if not m.group(3) and date < now.date():
            date = dt.date(y + 1, mo, d)          # «15/01» en diciembre es el año que viene
        return date
    if t in WEEKDAYS:
        ahead = (WEEKDAYS[t] - now.weekday()) % 7 or 7
        return now.date() + dt.timedelta(days=ahead)
    return None


def _at(date: dt.date, hhmm: str, tz: ZoneInfo) -> dt.datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return dt.datetime.combine(date, dt.time(h, m), tzinfo=tz)


def next_run(rep: str, hhmm: str, after: dt.datetime, tz: ZoneInfo) -> dt.datetime:
    """Siguiente aviso de un recordatorio que se repite, estrictamente después de «after»."""
    kind, _, arg = rep.partition(":")
    d = after.date()
    for _ in range(800):
        if kind == "daily":
            ok = True
        elif kind == "weekly":
            ok = d.weekday() == int(arg)
        elif kind == "monthly":
            ok = d.day == min(int(arg), calendar.monthrange(d.year, d.month)[1])   # 31 → último del mes
        elif kind == "yearly":
            mo, dd = (int(x) for x in arg.split("-"))
            ok = d.month == mo and d.day == (dd if not (mo == 2 and dd == 29 and not calendar.isleap(d.year)) else 28)
        else:
            raise ValueError(rep)
        if ok:
            cand = _at(d, hhmm, tz)
            if cand > after:
                return cand
        d += dt.timedelta(days=1)
    raise ValueError("No encontré la próxima fecha")


def parse(text: str, tz: ZoneInfo, now: dt.datetime | None = None) -> tuple[dt.datetime, str, str, str]:
    """'/recordar …' → (cuándo, repetición, hora, texto)."""
    now = now or dt.datetime.now(tz)
    body = re.sub(r"^/recordar(@\w+)?\s*", "", text.strip(), flags=re.I)
    toks = body.split()
    if not toks:
        raise ValueError(USAGE)
    low = [t.lower() for t in toks]

    def need_text(rest: list[str]) -> str:
        s = " ".join(rest).strip(" ,:-")
        if not s:
            raise ValueError("¿Qué te recuerdo? " + USAGE)
        return s[:500]

    # en 2h / en 30 min / en 3 días
    if low[0] == "en" and len(toks) >= 2:
        m = re.match(r"^(\d+)\s*([a-zá]+)?$", low[1])
        unit, skip = (m.group(2), 2) if m and m.group(2) else ((low[2] if len(low) > 2 else ""), 3)
        if m and unit in UNITS:
            when = now + dt.timedelta(seconds=int(m.group(1)) * UNITS[unit])
            return when.replace(second=0, microsecond=0), "", when.strftime("%H:%M"), need_text(toks[skip:])

    # cada …
    if low[0] == "cada" and len(toks) >= 2:
        what, rest = low[1], toks[2:]
        if what in ("dia", "día"):
            rep = "daily"
        elif what in WEEKDAYS:
            rep = f"weekly:{WEEKDAYS[what]}"
        elif what == "mes":
            if not rest or not rest[0].isdigit() or not 1 <= int(rest[0]) <= 31:
                raise ValueError("Di el día del mes: <code>/recordar cada mes 5 pagar el internet</code>")
            rep, rest = f"monthly:{int(rest[0])}", rest[1:]
        elif what in ("año", "ano"):
            m = re.match(r"^(\d{1,2})[/-](\d{1,2})$", rest[0]) if rest else None
            if not m:
                raise ValueError("Di la fecha: <code>/recordar cada año 15/03 cumpleaños de mamá</code>")
            a, b = int(m.group(1)), int(m.group(2))
            mo, dd = (a, b) if "-" in rest[0] else (b, a)       # 03-15 (mes-día) o 15/03 (día/mes)
            dt.date(2024, mo, dd)                               # valida (2024 admite 29/02)
            rep, rest = f"yearly:{mo:02d}-{dd:02d}", rest[1:]
        else:
            raise ValueError("Repite con «cada día», «cada lunes», «cada mes 5» o «cada año 15/03»")
        hhmm = DEFAULT_TIME
        if rest and _hhmm(rest[0]):
            hhmm, rest = _hhmm(rest[0]), rest[1:]
        return next_run(rep, hhmm, now, tz), rep, hhmm, need_text(rest)

    # fecha [hora] texto · hora texto (hoy, o mañana si ya pasó)
    date = _date(toks[0], now)
    rest = toks[1:] if date else toks
    hhmm = _hhmm(rest[0]) if rest else None
    if hhmm:
        rest = rest[1:]
    if not date and not hhmm:
        raise ValueError("No entiendo cuándo. " + USAGE)
    hhmm = hhmm or DEFAULT_TIME
    when = _at(date or now.date(), hhmm, tz)
    if not date and when <= now:
        when += dt.timedelta(days=1)
    if when <= now - dt.timedelta(minutes=1):
        raise ValueError("Esa fecha ya pasó")
    return when, "", hhmm, need_text(rest)


def describe(rep: str, hhmm: str) -> str:
    kind, _, arg = rep.partition(":")
    if kind == "daily":
        return f"cada día {hhmm}"
    if kind == "weekly":
        return f"cada {WD_NAME[int(arg)]} {hhmm}"
    if kind == "monthly":
        return f"cada mes el {arg} a las {hhmm}"
    if kind == "yearly":
        mo, dd = arg.split("-")
        return f"cada año el {int(dd)}/{int(mo)} a las {hhmm}"
    return ""


class Reminders:
    def __init__(self, store) -> None:
        self.s = store
        with store.lock:
            store.conn.executescript(SCHEMA)
            store.conn.commit()

    def add(self, chat: int, when: dt.datetime, rep: str, hhmm: str, text: str) -> int:
        if len(self.of(chat)) >= 100:
            raise ValueError("Ya tienes 100 recordatorios; borra alguno con /olvidar N")
        return self.s.x("INSERT INTO reminders (chat, text, run_at, rep, hhmm, created) VALUES (?,?,?,?,?,?)",
                        (chat, text, when.timestamp(), rep, hhmm, time.time()))

    def of(self, chat: int) -> list[dict]:
        return self.s.q("SELECT * FROM reminders WHERE chat=? ORDER BY run_at", (chat,))

    def delete(self, chat: int, rid: int) -> bool:
        if not self.s.q("SELECT id FROM reminders WHERE id=? AND chat=?", (rid, chat)):
            return False                 # solo los tuyos
        self.s.x("DELETE FROM reminders WHERE id=? AND chat=?", (rid, chat))
        return True

    def due(self, now: float) -> list[dict]:
        return self.s.q("SELECT * FROM reminders WHERE run_at <= ? ORDER BY run_at", (now,))

    def done(self, r: dict, tz: ZoneInfo) -> None:
        """Después de avisar: el que se repite pasa al siguiente; el de una vez se borra."""
        if r["rep"]:
            after = max(dt.datetime.fromtimestamp(r["run_at"], tz), dt.datetime.now(tz))
            nxt = next_run(r["rep"], r["hhmm"], after, tz)
            self.s.x("UPDATE reminders SET run_at=? WHERE id=?", (nxt.timestamp(), r["id"]))
        else:
            self.s.x("DELETE FROM reminders WHERE id=?", (r["id"],))
