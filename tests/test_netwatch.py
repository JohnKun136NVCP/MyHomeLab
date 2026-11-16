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
