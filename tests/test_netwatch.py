"""Pruebas de netwatch:  python -m pytest tests/"""
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "services" / "common"), str(ROOT / "services" / "netwatch")]

from netwatch import acl, scanner  # noqa: E402
from netwatch.classify import classify  # noqa: E402
from netwatch.db import DB  # noqa: E402

NMAP_XML = """<?xml version="1.0"?><nmaprun>
<host><status state="up"/><address addr="192.168.0.20" addrtype="ipv4"/>
<address addr="3C:84:6A:11:22:33" addrtype="mac" vendor="TP-Link Systems"/>
<hostnames><hostname name="tapo-c200" type="PTR"/></hostnames>
<ports><port protocol="tcp" portid="554"><state state="open"/><service name="rtsp"/></port>
<port protocol="tcp" portid="2020"><state state="open"/></port>
<port protocol="tcp" portid="80"><state state="closed"/></port></ports></host>
<host><status state="down"/><address addr="192.168.0.99" addrtype="ipv4"/></host>
</nmaprun>"""


def test_parse_arp_scan():
    out = "192.168.0.1\tc8:3a:35:00:00:01\tTenda Technology Co.,Ltd.\n" \
          "192.168.0.50\t5a:11:22:33:44:55\t(Unknown: locally administered)\nbasura\n"
    hs = scanner.parse_arp_scan(out)
    assert [h.ip for h in hs] == ["192.168.0.1", "192.168.0.50"]
    assert hs[0].vendor.startswith("Tenda") and hs[1].vendor == ""
    assert hs[1].random_mac and not hs[0].random_mac


def test_parse_nmap_xml():
    hs = scanner.parse_nmap_xml(NMAP_XML)
    assert len(hs) == 1
    h = hs[0]
    assert h.mac == "3c:84:6a:11:22:33" and h.hostname == "tapo-c200"
    assert h.ports == ["554/tcp rtsp", "2020/tcp"]


def test_classify():
    assert classify("TP-Link Systems", "tapo-c200", ["554/tcp rtsp"]) == "Cámara"
    assert classify("TP-Link Systems", "", []) == "IoT / Domótica"
    assert classify("Apple, Inc.", "iPhone-de-Juan", []) == "Teléfono"
    assert classify("", "", [], random_mac=True) == "Teléfono"
    assert classify("Intel Corporate", "DESKTOP-ABC", ["445/tcp"]) == "Computadora"
    assert classify("Seiko Epson", "", ["631/tcp ipp"]) == "Impresora"
    assert classify("Tenda Technology", "", [], is_infra=True) == "Access Point / Router"
    assert classify("Sony Interactive Entertainment", "", []) == "Consola"
    assert classify("Espressif Inc.", "", []) == "IoT / Domótica"
    assert classify("Raspberry Pi Trading", "", ["22/tcp ssh", "8096/tcp"]) == "Servidor"


def test_acl_windows():
    rules = [{"mac": "AA:BB:CC:DD:EE:01", "start": "23:00", "end": "07:00"},
             {"mac": "aa:bb:cc:dd:ee:02", "start": None, "end": None},
             {"mac": "malo", "start": None, "end": None}]
    assert acl.active_blocks(rules, dt.time(23, 30)) == {"aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:02"}
    assert acl.active_blocks(rules, dt.time(6, 59)) == {"aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:02"}
    assert acl.active_blocks(rules, dt.time(12, 0)) == {"aa:bb:cc:dd:ee:02"}


def test_ruleset_and_counters():
    rs = acl.build_ruleset(["aa:bb:cc:dd:ee:01", "AA:BB:CC:DD:EE:02", "x"], {"aa:bb:cc:dd:ee:02"})
    assert "delete table bridge homelab" in rs
    assert 'counter name "up_aabbccddee01"' in rs and "elements = { aa:bb:cc:dd:ee:02 }" in rs
    assert " x" not in rs
    js = json.dumps({"nftables": [{"metainfo": {}},
                                  {"counter": {"name": "up_aabbccddee01", "packets": 3, "bytes": 1500}},
                                  {"counter": {"name": "down_aabbccddee01", "packets": 9, "bytes": 9000}}]})
    assert acl.parse_counters(js) == {"aa:bb:cc:dd:ee:01": {"up": 1500, "down": 9000}}


def test_db(tmp_path):
    db = DB(tmp_path / "t.db")
    assert db.upsert_seen("aa:bb:cc:dd:ee:01", "192.168.0.5", "Apple", "iphone", True) is True
    assert db.upsert_seen("aa:bb:cc:dd:ee:01", "192.168.0.6", "", "", True) is False
    d = db.device("aa:bb:cc:dd:ee:01")
    assert d["ip"] == "192.168.0.6" and d["vendor"] == "Apple" and d["display_name"] == "iphone"
    db.update_device(d["mac"], name="Cel Juan", ap="AP Sala", ports=["22/tcp"])
    d = db.device(d["mac"])
    assert d["display_name"] == "Cel Juan" and d["ports"] == ["22/tcp"]
    db.mark_offline(set())
    assert db.device(d["mac"])["online"] == 0
    eid = db.add_event("warning", "new_device", "x", "y", d["mac"])
    assert db.recent_event("new_device", d["mac"], 60)
    assert [e["id"] for e in db.pending_notifications()] == [eid]
    db.mark_notified([eid])
    assert db.pending_notifications() == []


def test_roaming_two_networks(tmp_path, monkeypatch):
    """La Pi vive en casa, brinca al laboratorio, lo escanea y regresa."""
    from netwatch import monitor as M
    cfg = {"network": {"interface": "wlan0",
                       "networks": [{"name": "casa", "ssid": "MODEM", "cidr": "192.168.20.0/24"},
                                    {"name": "laboratorio", "ssid": "LAB", "cidr": "192.168.200.0/24",
                                     "notify_new_devices": False}],
                       "roaming": {"enabled": True, "home": "casa", "hop_every": 0, "dwell": 0}},
           "infrastructure": {"modem": {"name": "Módem", "ip": "192.168.20.1"},
                              "access_points": [{"name": "Tenda Lab", "network": "laboratorio",
                                                 "ip": "192.168.200.1", "wan_ip": "192.168.20.2"}]}}
    H = scanner.Host
    scans = {"192.168.20.0/24": [H("192.168.20.1", "aa:00:00:00:00:01", "ZTE"),
                                 H("192.168.20.2", "c8:3a:35:00:00:99", "Tenda"),
                                 H("192.168.20.50", "aa:00:00:00:00:50", "Apple", "iphone-mama")],
             "192.168.200.0/24": [H("192.168.200.1", "c8:3a:35:00:00:01", "Tenda"),
                                  H("192.168.200.30", "aa:00:00:00:02:30", "Cisco", "sw-lab")]}
    hops, pings = [], []
    monkeypatch.setattr(M.scanner, "discover", lambda cidr, iface: scans[cidr])
    monkeypatch.setattr(M.scanner, "port_scan", lambda ip: [])
    monkeypatch.setattr(M.scanner, "ping", lambda h, timeout=1.5: pings.append(h) or 0.01)
    monkeypatch.setattr(M.roam, "available", lambda: True)
    monkeypatch.setattr(M.roam, "connect", lambda prof, iface, cidr, timeout=45: hops.append(prof) or (True, "ok"))
    db = DB(tmp_path / "n.db")
    mon = M.Monitor(cfg, db, tmp_path)
    mon.scan()
    mon.ping_all()
    assert "192.168.20.2" in pings and "192.168.200.1" not in pings   # Tenda por su WAN desde casa
    mon.maybe_roam()
    assert hops == ["LAB", "MODEM"] and mon.current == "casa"
    devs = {d["mac"]: d for d in db.devices()}
    assert devs["aa:00:00:00:02:30"]["network"] == "laboratorio"
    assert devs["aa:00:00:00:00:50"]["online"] == 1          # los de casa no se apagan al visitar el lab
    assert devs["c8:3a:35:00:00:99"]["name"] == "Tenda Lab (WAN)"
    titles = [e["title"] for e in db.events()]
    assert any("iphone-mama" in t for t in titles) and not any("sw-lab" in t for t in titles)
    scans["192.168.20.0/24"] = scans["192.168.20.0/24"][:2]
    mon.scan()                                                # el iPhone se fue; el lab no se toca
    devs = {d["mac"]: d for d in db.devices()}
    assert devs["aa:00:00:00:00:50"]["online"] == 0 and devs["aa:00:00:00:02:30"]["online"] == 1
    assert mon.net_status["laboratorio"]["devices"] == 2


def test_roaming_returns_home_on_failure(tmp_path, monkeypatch):
    from netwatch import monitor as M
    cfg = {"network": {"networks": [{"name": "casa", "cidr": "10.0.0.0/24"},
                                    {"name": "lab", "cidr": "10.1.0.0/24"}],
                       "roaming": {"enabled": True, "hop_every": 0}}}
    hops = []
    monkeypatch.setattr(M.roam, "available", lambda: True)
    monkeypatch.setattr(M.roam, "connect",
                        lambda p, i, c, timeout=45: hops.append(p) or ((False, "sin señal") if p == "lab" else (True, "")))
    db = DB(tmp_path / "n.db")
    mon = M.Monitor(cfg, db, tmp_path)
    mon.maybe_roam()
    assert hops == ["lab", "casa"] and mon.current == "casa"
    assert any(e["kind"] == "roam_failed" for e in db.events())
