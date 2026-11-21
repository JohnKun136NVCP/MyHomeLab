"""Inventario, mapa de Wi-Fi, control parental y recordatorios del bot.

    python -m pytest tests/test_casa_red.py -q
"""
import datetime as dt
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "services" / "common"), str(ROOT / "services" / "netwatch"), str(ROOT / "services" / "bot")]

from netwatch import inventory, parental, wifi  # noqa: E402
from netwatch.db import DB  # noqa: E402

TZ = ZoneInfo("America/Mexico_City")

NMAP = """<?xml version="1.0"?><nmaprun><host><status state="up"/>
<address addr="192.168.1.50" addrtype="ipv4"/><address addr="AA:BB:CC:00:11:22" addrtype="mac"/>
<ports>
<port protocol="tcp" portid="22"><state state="open"/><service name="ssh" product="OpenSSH" version="7.4" extrainfo="protocol 2.0">
<cpe>cpe:/a:openbsd:openssh:7.4</cpe></service>
<script id="vulners" output="&#xa;  cpe:/a:openbsd:openssh:7.4: &#xa;    	CVE-2023-38408	9.8	https://vulners.com/cve/CVE-2023-38408&#xa;    	CVE-2016-10012	7.8	https://vulners.com/cve/CVE-2016-10012&#xa;    	CVE-2018-15473	5.3	https://vulners.com/cve/CVE-2018-15473"/></port>
<port protocol="tcp" portid="80"><state state="closed"/><service name="http"/></port>
<port protocol="tcp" portid="8080"><state state="open"/><service name="http" product="lighttpd"/></port>
</ports></host></nmaprun>"""


def test_inventory_parse():
    svcs = inventory.parse(NMAP)
    assert [s["port"] for s in svcs] == [22, 8080]
    ssh = svcs[0]
    assert ssh["mac"] == "aa:bb:cc:00:11:22" and ssh["product"] == "OpenSSH" and "7.4" in ssh["version"]
    assert ssh["cves"][0] == {"id": "CVE-2023-38408", "cvss": 9.8}
    assert len(ssh["cves"]) == 3 and svcs[1]["cves"] == []


def test_inventory_save_and_event(tmp_path):
    db = DB(tmp_path / "n.db")
    db.upsert_seen("aa:bb:cc:00:11:22", "192.168.1.50", "Tenda", "router", False)
    inv = inventory.Inventory(db, {})
    for s in inventory.parse(NMAP):
        inv._save("aa:bb:cc:00:11:22", "192.168.1.50", s)
    devs = inv.devices()
    assert devs[0]["max_cvss"] == 9.8 and devs[0]["cves"] == 3
    evs = [e for e in db.events() if e["kind"] == "vulnerable"]
    assert len(evs) == 1 and evs[0]["level"] == "critical"
    for s in inventory.parse(NMAP):          # mismas CVE: no se vuelve a avisar
        inv._save("aa:bb:cc:00:11:22", "192.168.1.50", s)
    assert len([e for e in db.events() if e["kind"] == "vulnerable"]) == 1


NMCLI = r"""*:Casa:AA\:BB\:CC\:00\:00\:01:6:2437 MHz:80:WPA2
 :Casa:AA\:BB\:CC\:00\:00\:02:44:5220 MHz:62:WPA2
 :Casa-Arriba:AA\:BB\:CC\:00\:00\:11:3:2422 MHz:40:WPA2
 :Vecino:11\:22\:33\:44\:55\:66:6:2437 MHz:70:WPA2 WPA3
 :Vecino2:11\:22\:33\:44\:55\:67:6:2437 MHz:55:WPA2
 :Vecino3:11\:22\:33\:44\:55\:68:6:2437 MHz:45:WPA2
 :Mi\:Red:22\:22\:33\:44\:55\:69:11:2462 MHz:30:
"""


def test_wifi_parse_and_summary(tmp_path):
    aps = wifi.parse(NMCLI)
    assert aps[0]["connected"] and aps[0]["bssid"] == "aa:bb:cc:00:00:01" and aps[0]["band"] == "2.4 GHz"
    assert aps[1]["band"] == "5 GHz" and aps[1]["channel"] == 44
    assert aps[-1]["ssid"] == "Mi:Red"
    db = DB(tmp_path / "n.db")
    sv = wifi.Survey(db, {})
    sv._cache, sv._at = {"aps": aps}, 9e18      # sin nmcli: usa lo de arriba
    db.upsert_seen("de:ad:be:ef:00:01", "192.168.1.20", "", "tele", False)
    db.update_device("de:ad:be:ef:00:01", wifi="Casa-Arriba")
    s = sv.summary()
    names = {n["ssid"]: n for n in s["networks"]}
    assert set(names) == {"Casa", "Casa-Arriba"}            # adivinadas por los mismos AP
    assert names["Casa"]["bands"] == ["2.4 GHz", "5 GHz"]
    assert names["Casa-Arriba"]["devices"][0]["mac"] == "de:ad:be:ef:00:01"
    assert any("canal 3" in t for t in s["tips"])            # 2.4 GHz fuera de 1/6/11
    assert any("canal 6" in t and "vecinas" in t for t in s["tips"])
    assert s["others"] == 4


def test_parental_schedule_and_body():
    sch = parental.schedule({"mon": ["16:00", "20:00"], "sat": ["10:00", "22:30"]}, "America/Mexico_City")
    assert sch["mon"] == {"start": 16 * 3600000, "end": 20 * 3600000}
    assert sch["sat"]["end"] == (22 * 60 + 30) * 60000 and "tue" not in sch
    with pytest.raises(ValueError):
        parental.schedule({"mon": ["20:00", "16:00"]}, "")
    body = parental.client_body({"name": "Tablet", "services": ["youtube", "tiktok"], "allow": {}, "adult": 1,
                                 "safe_search": 1, "enabled": 1}, ["aa:bb:cc:dd:ee:ff", "192.168.1.9"], "UTC")
    assert body["name"] == "homelab: Tablet" and body["blocked_services"] == ["youtube", "tiktok"]
    assert body["use_global_blocked_services"] is False and body["parental_enabled"] is True


class FakeAG:
    enabled = True

    def __init__(self):
        self.clients, self.calls = {}, []

    def _get(self, path):
        if path == "/control/clients":
            return {"clients": [{"name": n} for n in self.clients]}
        return {"blocked_services": [{"id": "youtube", "name": "YouTube"}]}

    def _post(self, path, body):
        self.calls.append(path)
        if path.endswith("/add"):
            self.clients[body["name"]] = body
        elif path.endswith("/update"):
            self.clients.pop(body["name"])
            self.clients[body["data"]["name"]] = body["data"]
        elif path.endswith("/delete"):
            self.clients.pop(body["name"])


def test_parental_flow(tmp_path):
    db = DB(tmp_path / "n.db")
    db.upsert_seen("aa:bb:cc:dd:ee:ff", "192.168.1.9", "", "tablet", False)
    ag = FakeAG()
    p = parental.Parental(db, ag, "UTC")
    with pytest.raises(ValueError):
        p.save({"name": "x", "macs": []})
    p.save({"name": "Tablet", "macs": ["AA:BB:CC:DD:EE:FF"], "services": ["youtube"], "allow": {"mon": ["16:00", "18:00"]}})
    assert ag.clients["homelab: Tablet"]["ids"] == ["aa:bb:cc:dd:ee:ff", "192.168.1.9"]
    rid = p.rules()[0]["id"]
    p.save({"id": rid, "name": "Tablet de Sofi", "macs": ["aa:bb:cc:dd:ee:ff"], "services": []})
    assert list(ag.clients) == ["homelab: Tablet de Sofi"] and len(p.rules()) == 1
    p.delete(rid)
    assert not ag.clients and not p.rules()


# ------------------------------------------------------------------ bot
def test_reminder_parse():
    from bot import reminders as R
    now = dt.datetime(2026, 11, 19, 10, 0, tzinfo=TZ)        # jueves
    w, rep, _, t = R.parse("/recordar mañana 9:00 pagar la luz", TZ, now)
    assert (w.day, w.hour, rep, t) == (20, 9, "", "pagar la luz")
    w, *_ = R.parse("/recordar 15/01 dentista", TZ, now)
    assert (w.year, w.month, w.hour) == (2027, 1, 9)           # ya pasó este año → el que viene
    w, *_, t = R.parse("/recordar en 2h sacar la ropa", TZ, now)
    assert w.hour == 12 and t == "sacar la ropa"
    w, *_ = R.parse("/recordar en 30 min café", TZ, now)
    assert w.minute == 30
    w, rep, *_ = R.parse("/recordar cada mes 5 pagar el internet", TZ, now)
    assert rep == "monthly:5" and (w.month, w.day) == (12, 5)
    w, rep, *_ = R.parse("/recordar cada año 15/03 cumpleaños de mamá", TZ, now)
    assert rep == "yearly:03-15" and (w.year, w.month, w.day) == (2027, 3, 15)
    w, rep, *_ = R.parse("/recordar cada lunes 7:30 basura", TZ, now)
    assert rep == "weekly:0" and w.weekday() == 0 and w.hour == 7
    w, *_ = R.parse("/recordar 8:00 tomar pastilla", TZ, now)   # ya pasó hoy → mañana
    assert w.day == 20
    for bad in ("/recordar", "/recordar mañana", "/recordar cuando sea algo", "/recordar cada mes pagar"):
        with pytest.raises(ValueError):
            R.parse(bad, TZ, now)


def test_reminder_next_run_month_end():
    from bot import reminders as R
    after = dt.datetime(2027, 2, 1, 0, 0, tzinfo=TZ)
    assert R.next_run("monthly:31", "09:00", after, TZ).day == 28
    assert R.next_run("yearly:02-29", "09:00", dt.datetime(2027, 1, 1, tzinfo=TZ), TZ).day == 28


def test_reminder_store(tmp_path):
    from bot import reminders as R
    from bot.store import Store
    rs = R.Reminders(Store(tmp_path / "b.db"))
    now = dt.datetime.now(TZ)
    a = rs.add(1, now - dt.timedelta(minutes=1), "", "09:00", "una vez")
    b = rs.add(1, now - dt.timedelta(minutes=1), "daily", "09:00", "diario")
    rs.add(2, now + dt.timedelta(days=1), "", "09:00", "de otro")
    assert not rs.delete(2, a)                                 # no puedes borrar los de otro
    due = rs.due(now.timestamp())
    assert {r["id"] for r in due} == {a, b}
    for r in due:
        rs.done(r, TZ)
    left = rs.of(1)
    assert [r["id"] for r in left] == [b] and left[0]["run_at"] > now.timestamp()


def test_inventory_vulners_tables_and_bad_scores():
    xml = """<?xml version="1.0"?><nmaprun><host><address addr="192.168.1.254" addrtype="ipv4"/>
<ports><port protocol="tcp" portid="53"><state state="open"/><service name="domain" product="dnsmasq" version="2.78"/>
<script id="vulners" output="&#xa;  cpe:/a:thekelleys:dnsmasq:2.78: &#xa;    	CVE-2020-25682	8.1	https://vulners.com/cve/CVE-2020-25682&#xa;    	CVE-2026-12969	15485	https://x&#xa;">
<table key="cpe:/a:thekelleys:dnsmasq:2.78">
<table><elem key="id">CVE-2020-25682</elem><elem key="cvss">8.1</elem><elem key="type">cve</elem></table>
<table><elem key="id">PACKETSTORM:15485</elem><elem key="cvss">7.5</elem><elem key="type">packetstorm</elem></table>
<table><elem key="id">CVE-2026-4892</elem><elem key="cvss">274</elem><elem key="type">cve</elem></table>
<table><elem key="id">PRION:CVE-2020-25681</elem><elem key="cvss">8.1</elem><elem key="type">prion</elem></table>
</table></script></port></ports></host></nmaprun>"""
    s = inventory.parse(xml)[0]
    assert s["cves"] == [{"id": "CVE-2020-25681", "cvss": 8.1}, {"id": "CVE-2020-25682", "cvss": 8.1}]
    # sin tablas: el texto, y los puntajes imposibles fuera
    s = inventory.parse(xml.split("<table key")[0] + "</script></port></ports></host></nmaprun>")[0]
    assert s["cves"] == [{"id": "CVE-2020-25682", "cvss": 8.1}]
