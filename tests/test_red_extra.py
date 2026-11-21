"""Routers nuevos, optimizar Wi-Fi, caídas del internet, syslog, invitados y resúmenes.

    python -m pytest tests/test_red_extra.py -q
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "services" / "common"), str(ROOT / "services" / "netwatch"), str(ROOT / "services" / "bot")]

from netwatch import dhcp, extras, wifi  # noqa: E402
from netwatch.db import DB  # noqa: E402

NM = r"""*:Casa:AA\:BB\:CC\:00\:00\:01:3:2422 MHz:80:WPA2:65 Mbit/s
 :Casa:AA\:BB\:CC\:00\:00\:02:44:5220 MHz:62:WPA2:540 Mbit/s
 :Vecino1:11\:22\:33\:00\:00\:01:1:2412 MHz:70:WPA2:130 Mbit/s
 :Vecino2:11\:22\:33\:00\:00\:02:1:2412 MHz:65:WPA2:130 Mbit/s
 :Vecino3:11\:22\:33\:00\:00\:03:6:2437 MHz:60:WPA2:130 Mbit/s
 :Vecino4:11\:22\:33\:00\:00\:04:44:5220 MHz:70:WPA2:540 Mbit/s
"""


def test_wifi_rate_and_recommend():
    aps = wifi.parse(NM)
    assert aps[0]["rate"] == "65 Mbit/s" and aps[0]["connected"]
    r = wifi.recommend(aps, {"Casa"})
    by = {a["bssid"]: a for a in r["aps"]}
    assert by["aa:bb:cc:00:00:01"]["best"] == 11                # 3 no vale; 1 y 6 ocupados → 11
    assert by["aa:bb:cc:00:00:02"]["best"] != 44                # 44 lo comparte un vecino fuerte
    assert any("65 Mbit/s" in t for t in r["tips"])              # la Pi va lenta


def test_wifi_track_new_router_and_evil_twin(tmp_path):
    db = DB(tmp_path / "n.db")
    sv = wifi.Survey(db, {"wifi": {"ssids": ["Casa"]}})
    sv._cache, sv._at = {"aps": wifi.parse(NM)}, 9e18
    assert sv.track() == []                                     # primera vez: solo aprende
    extra = (r" :Casa:DE\:AD\:BE\:EF\:00\:01:6:2437 MHz:75:WPA2:130 Mbit/s" + "\n"
             r" :TP-Link_9F2A:50\:C7\:BF\:00\:00\:09:11:2462 MHz:72:WPA2:130 Mbit/s" + "\n"
             r" :Lejano:50\:C7\:BF\:00\:00\:10:11:2462 MHz:20:WPA2:130 Mbit/s")
    db.upsert_seen("50:c7:bf:00:00:0a", "192.168.1.88", "TP-Link", "", False)
    sv._cache = {"aps": wifi.parse(NM + extra)}
    new = {a["ssid"]: a["status"] for a in sv.track()}
    assert new == {"Casa": "twin", "TP-Link_9F2A": "new"}       # el lejano (20 %) es de un vecino
    ev = {e["kind"]: e for e in db.events()}
    assert ev["wifi_twin"]["level"] == "critical"
    assert "192.168.1.88" in ev["wifi_new"]["detail"]            # el router está conectado a tu red
    sv.ack("de:ad:be:ef:00:01", "mine")
    assert [x["status"] for x in sv.seen_list() if x["bssid"] == "de:ad:be:ef:00:01"] == ["mine"]


NMAP_DHCP = """Starting Nmap
Pre-scan script results:
| broadcast-dhcp-discover:
|   Response 1 of 2:
|     Interface: eth0
|     IP Offered: 192.168.1.77
|     DHCP Message Type: DHCPOFFER
|     Server Identifier: 192.168.1.254
|     Subnet Mask: 255.255.255.0
|     Router: 192.168.1.254
|     Domain Name Server: 192.168.1.69
|   Response 2 of 2:
|     Interface: eth0
|     IP Offered: 192.168.0.100
|     Server Identifier: 192.168.0.1
|     Subnet Mask: 255.255.255.0
|     Router: 192.168.0.1
|_    Domain Name Server: 192.168.0.1
"""


def test_dhcp_rogue_router_with_cidr(tmp_path):
    from netwatch import monitor as M
    offers = dhcp.parse(NMAP_DHCP)
    assert [(o["server"], o["cidr"]) for o in offers] == [("192.168.1.254", "192.168.1.0/24"),
                                                          ("192.168.0.1", "192.168.0.0/24")]
    db = DB(tmp_path / "n.db")
    mon = M.Monitor({"network": {"networks": [{"name": "casa", "interface": "eth0", "cidr": "192.168.1.0/24"}]},
                     "infrastructure": {"modem": {"name": "Módem", "ip": "192.168.1.254"}}}, db, tmp_path)
    for o in offers:
        mon.dhcp.record("casa", o)
    ev = [e for e in db.events() if e["kind"] == "rogue_dhcp"]
    assert len(ev) == 1 and ev[0]["level"] == "critical" and "192.168.0.0/24" in ev[0]["detail"]
    s = {r["server"]: r for r in mon.dhcp.servers()}
    assert s["192.168.1.254"]["known"] and not s["192.168.0.1"]["ack"]
    mon.dhcp.ack("192.168.0.1")
    mon.dhcp.record("casa", offers[1])                          # ya es tuyo: no vuelve a avisar
    assert len([e for e in db.events() if e["kind"] == "rogue_dhcp"]) == 1


def test_outages_and_digest(tmp_path):
    from netwatch import monitor as M
    db = DB(tmp_path / "n.db")
    mon = M.Monitor({"network": {"networks": [{"name": "casa", "cidr": "192.168.1.0/24"}]}}, db, tmp_path)
    o = mon.outages
    o.update("internet", False, 1000)
    o.update("internet", True, 1030)                            # 30 s: no cuenta
    o.update("internet", False, 2000)
    o.update("internet", True, 2000 + 600)
    rows = o.list(0)
    assert len(rows) == 1 and rows[0]["end"] - rows[0]["start"] == 600
    assert o.stats(0)["minutes"] == 10
    d = extras.digest(db, mon, 0)
    assert d["outages"]["count"] == 1 and "honeypot" in d
    from bot import reports
    text = reports.weekly_digest({**d, "new_devices": ["Dispositivo nuevo en casa: tele"], "honeypot": 2})
    assert "1 caída" in text and "trampa 2 vez" in text and "tele" in text
    m = reports.monthly_internet({"month": o.stats(0), "outages": rows})
    assert "10 min" in m


def test_syslog_parse_and_alert(tmp_path):
    r = extras.parse_syslog(b"<38>Nov 22 07:01:02 tenda-sala httpd[123]: login failed for admin from 192.168.1.50", "192.168.1.2")
    assert r["severity"] == 6 and r["facility"] == 4 and r["tag"] == "httpd" and r["msg"].startswith("login failed")
    db = DB(tmp_path / "n.db")
    sl = extras.Syslog(db, 0, lambda ip: "Tenda Sala")
    sl.handle(b"<30>dnsmasq: DHCPACK(br0) 192.168.1.40", "192.168.1.2")
    sl.handle(b"<38>httpd: login failed for admin", "192.168.1.2")
    assert len(sl.query()) == 2 and sl.hosts()[0]["n"] == 2
    assert [e["kind"] for e in db.events()] == ["syslog_alert"]


def test_guest_qr_and_capture_names():
    assert extras.wifi_qr_text("Casa;Invitados", "a:b,c") == r"WIFI:T:WPA;S:Casa\;Invitados;P:a\:b\,c;;"
    assert extras.wifi_qr_text("Libre", "") == "WIFI:T:nopass;S:Libre;;"
    assert extras.Capture.path("../../etc/passwd") is None
    assert extras.Capture.path("captura-x.pcap") is None        # no existe
