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


def test_two_interfaces_no_roaming(tmp_path, monkeypatch):
    """wlan0 al módem y eth0 al Tenda: las dos redes en cada ciclo, sin brincar."""
    from netwatch import monitor as M
    cfg = {"network": {"networks": [{"name": "casa", "interface": "wlan0", "cidr": "192.168.20.0/24"},
                                    {"name": "laboratorio", "interface": "eth0", "cidr": "192.168.200.0/24"}],
                       "roaming": {"enabled": False}},
           "infrastructure": {"access_points": [{"name": "Tenda Lab", "network": "laboratorio",
                                                 "ip": "192.168.200.1", "wan_ip": "192.168.20.2"}]}}
    calls, pings = [], []
    H = scanner.Host
    monkeypatch.setattr(M.scanner, "discover", lambda cidr, iface: calls.append((cidr, iface)) or
                        [H(cidr.replace("0/24", "9"), f"aa:00:00:00:00:{len(calls):02d}")])
    monkeypatch.setattr(M.scanner, "port_scan", lambda ip: [])
    monkeypatch.setattr(M.scanner, "ping", lambda h, timeout=1.5: pings.append(h) or 0.01)
    mon = M.Monitor(cfg, DB(tmp_path / "n.db"), tmp_path)
    mon.scan_all()
    mon.ping_all()
    assert calls == [("192.168.20.0/24", "wlan0"), ("192.168.200.0/24", "eth0")]
    assert pings[0] == "192.168.200.1"                     # el Tenda por su IP del lab, directo


# ------------------------------------------------ nombres, entrantes y honeypot
def test_dns_packets():
    import struct
    from netwatch import names
    q = names.build_query(names.ptr_name("192.168.1.20"), qid=0x1234)
    assert q[:2] == b"\x12\x34" and b"\x0220\x011\x03168\x03192\x07in-addr\x04arpa\x00" in q
    # respuesta con compresión: el nombre de la pregunta y un PTR
    ans = (struct.pack(">HHHHHH", 0x1234, 0x8180, 1, 1, 0, 0) + q[12:]
           + b"\xc0\x0c" + struct.pack(">HHIH", 12, 1, 60, 23) + b"\x0eiPhone-de-Mama\x05local\x00")
    assert names.parse_ptr_answer(ans) == "iPhone-de-Mama.local"
    assert names.clean("iPhone-de-Mama.local.") == "iPhone-de-Mama"
    assert names.clean("desktop-abc.lan") == "desktop-abc"
    qa = names.build_query("tapo-c200", qtype=1, qid=7)
    a = (struct.pack(">HHHHHH", 7, 0x8180, 1, 1, 0, 0) + qa[12:]
         + b"\xc0\x0c" + struct.pack(">HHIH", 1, 1, 60, 4) + bytes([192, 168, 1, 20]))
    assert names.parse_a_answer(a) == "192.168.1.20"
    assert names.parse_ptr_answer(b"\x00" * 5) == ""


def test_parse_conntrack_and_ufw():
    from netwatch import inbound
    mine = {"192.168.1.5", "192.168.100.5"}
    ct = """ipv4     2 tcp      6 431999 ESTABLISHED src=192.168.1.20 dst=192.168.1.5 sport=51234 dport=8443 src=172.18.0.5 dst=192.168.1.20 sport=443 dport=51234 [ASSURED] mark=0 use=1
ipv4     2 tcp      6 100 ESTABLISHED src=192.168.100.10 dst=192.168.100.5 sport=40000 dport=19180 src=192.168.100.5 dst=192.168.100.10 sport=19180 dport=40000 [ASSURED] use=1
ipv4     2 udp      17 29 src=192.168.1.30 dst=192.168.1.5 sport=5353 dport=53 src=192.168.1.5 dst=192.168.1.30 sport=53 dport=5353 use=1
ipv4     2 tcp      6 100 ESTABLISHED src=192.168.1.5 dst=1.1.1.1 sport=40000 dport=443 src=1.1.1.1 dst=192.168.1.5 sport=443 dport=40000 use=1
ipv4     2 tcp      6 100 ESTABLISHED src=172.17.0.2 dst=192.168.1.5 sport=4 dport=9100 src=x use=1"""
    rows = inbound.parse_conntrack(ct, mine)
    assert rows == [("192.168.1.20", 8443, "tcp", "192.168.1.5", 51234),
                    ("192.168.100.10", 19180, "tcp", "192.168.100.5", 40000)]
    ufw = ["Nov 17 10:00:01 pi kernel: [UFW BLOCK] IN=eth0 OUT= MAC=aa SRC=148.113.214.202 DST=192.168.1.5 "
           "LEN=60 TOS=0x00 PROTO=TCP SPT=55555 DPT=22 WINDOW=64240 SYN URGP=0",
           "Nov 17 10:00:02 pi kernel: [UFW BLOCK] IN=eth0 OUT= SRC=192.168.1.30 DST=224.0.0.251 PROTO=UDP SPT=5353 DPT=5353",
           "Nov 17 10:00:03 pi kernel: [UFW AUDIT] IN=eth0 SRC=8.8.8.8 DST=192.168.1.5 PROTO=TCP SPT=1 DPT=80"]
    assert inbound.parse_ufw(ufw, mine) == [("148.113.214.202", 22, "tcp", "eth0", "bloqueada")]
    canary = ['{"logtype": 1001, "src_host": ""}',
              '{"dst_port": 2222, "logtype": 4002, "src_host": "192.168.100.30", "logdata": {"USERNAME": "root", "PASSWORD": "123"}}',
              "basura"]
    assert [e["src_host"] for e in inbound.parse_canary(canary)] == ["192.168.100.30"]
    assert inbound.service_name(19180) == "SSH" and inbound.service_name(12345) == "12345"


def test_inbound_collect_and_summary(tmp_path, monkeypatch):
    from netwatch import inbound
    db = DB(tmp_path / "n.db")
    ct = tmp_path / "ct"
    ufw = tmp_path / "ufw.log"
    can = tmp_path / "canary.log"
    ufw.write_text("")
    monkeypatch.setattr(inbound, "local_ifaces", lambda: {"192.168.1.5": "eth0"})
    ct.write_text("ipv4 2 tcp 6 9 ESTABLISHED src=192.168.1.20 dst=192.168.1.5 sport=1000 dport=8443 src=a\n")
    ib = inbound.Inbound(db, str(ufw), str(can), str(ct))
    ib.collect()
    ib.collect()                                           # la misma conexión no se cuenta dos veces
    ct.write_text("ipv4 2 tcp 6 9 ESTABLISHED src=192.168.1.20 dst=192.168.1.5 sport=1000 dport=8443 src=a\n"
                  "ipv4 2 tcp 6 9 ESTABLISHED src=192.168.1.20 dst=192.168.1.5 sport=1001 dport=8443 src=a\n")
    with open(ufw, "a") as f:
        f.write("k: [UFW BLOCK] IN=eth0 SRC=148.113.214.202 DST=192.168.1.5 PROTO=TCP SPT=5 DPT=22\n")
    can.write_text('{"dst_port": 23, "logtype": 6001, "src_host": "192.168.1.66"}\n')
    hp = ib.collect()
    assert [e["src_host"] for e in hp] == ["192.168.1.66"]
    s = {r["ip"]: r for r in db.inbound_summary(0)}
    assert s["192.168.1.20"]["actions"] == {"permitida": 2} and s["192.168.1.20"]["iface"] == "eth0"
    assert s["148.113.214.202"]["ports"] == {"22": {"bloqueada": 1}}
    assert s["192.168.1.66"]["actions"] == {"honeypot": 1}
    assert sum(r["n"] for r in db.inbound_hourly(0)) == 4


def test_honeypot_and_dns_events(tmp_path, monkeypatch):
    from netwatch import monitor as M
    db = DB(tmp_path / "n.db")
    mon = M.Monitor({"network": {"networks": [{"name": "casa", "cidr": "192.168.1.0/24"}]}}, db, tmp_path)
    monkeypatch.setattr(mon.inbound, "collect", lambda: [{"src_host": "192.168.1.66", "dst_port": 2222,
                                                          "logdata": {"USERNAME": "root"}}])
    mon.collect_inbound()
    mon.collect_inbound()                                  # no repite el aviso en 30 min
    monkeypatch.setattr(mon.adguard, "poll", lambda: [{"client": "192.168.1.66", "domain": "malo.example",
                                                       "reason": "malware/phishing"}])
    mon.poll_dns()
    evs = db.events()
    assert [e["kind"] for e in evs].count("honeypot") == 1 and "username=root" in evs[-1]["detail"]
    assert any(e["kind"] == "dns_danger" and e["level"] == "critical" for e in evs)


def test_resolve_names_and_probe(tmp_path, monkeypatch):
    from netwatch import monitor as M
    db = DB(tmp_path / "n.db")
    cfg = {"network": {"networks": [{"name": "casa", "interface": "eth0", "cidr": "192.168.1.0/24"}]},
           "infrastructure": {"modem": {"name": "Módem", "ip": "192.168.1.254"}}}
    mon = M.Monitor(cfg, db, tmp_path)
    db.upsert_seen("aa:00:00:00:00:20", "192.168.1.20", "", "", True, "casa")
    asked = []
    monkeypatch.setattr(M.names, "resolve", lambda ip, srv: asked.append((ip, srv)) or ("iPhone-de-Mama", "mDNS"))
    mon.resolve_names()
    d = db.device("aa:00:00:00:00:20")
    assert d["hostname"] == "iPhone-de-Mama" and d["hostname_src"] == "mDNS" and d["type"] == "Teléfono"
    assert asked == [("192.168.1.20", ["192.168.1.254"])]
    mon.resolve_names()                                    # ya tiene nombre: no vuelve a preguntar
    assert len(asked) == 1
    monkeypatch.setattr(M.scanner, "ping", lambda h, timeout=1.5: 0.004)
    monkeypatch.setattr(M.scanner, "discover", lambda cidr, iface: [scanner.Host("192.168.1.77", "aa:00:00:00:00:77", "Lenovo")])
    monkeypatch.setattr(M.scanner, "port_scan", lambda ip: ["445/tcp microsoft-ds"])
    monkeypatch.setattr(M.names, "resolve", lambda ip, srv: ("DESKTOP-OFICINA", "NetBIOS"))
    r = mon.probe("192.168.1.77")
    assert r["reachable"] and r["mac"] == "aa:00:00:00:00:77" and r["type"] == "Computadora"
    assert db.device("aa:00:00:00:00:77")["hostname"] == "DESKTOP-OFICINA"


# ------------------------------------------------------------- ajustes
def test_env_roundtrip(tmp_path, monkeypatch):
    from netwatch import settings as st
    monkeypatch.setattr(st, "RUN_DIR", tmp_path)
    env = tmp_path / ".env"
    env.write_text("# comentario\nTELEGRAM_BOT_TOKEN=123:abc\nWEB_PASSWORD_HASH=$$2a$$14$$xyz\n\nHTTPS_PORT=443\n")
    assert st.read_env(env)["WEB_PASSWORD_HASH"] == "$2a$14$xyz"
    ino = env.stat().st_ino
    changed = st.write_env({"HTTPS_PORT": "8443", "WEB_PASSWORD_HASH": "$2b$12$nuevo", "NUEVA": "x y",
                            "TELEGRAM_BOT_TOKEN": "123:abc"}, env)
    assert sorted(changed) == ["HTTPS_PORT", "NUEVA", "WEB_PASSWORD_HASH"]
    txt = env.read_text()
    assert txt.startswith("# comentario\n") and "WEB_PASSWORD_HASH=$$2b$$12$$nuevo" in txt and "HTTPS_PORT=8443" in txt
    assert env.stat().st_ino == ino                       # mismo archivo: Docker sigue viéndolo
    assert len(list(tmp_path.glob(".env.*.bak"))) == 1
    st.write_env({"NUEVA": None}, env)
    assert "NUEVA" not in env.read_text()
    assert st.mask("abcdefghijk") == "••••••hijk" and st.mask("") == ""
    assert st.device_env_key("laptop-win") == "BACKUP_TOKEN_LAPTOP_WIN"
    assert st.restart_for(["HTTPS_PORT", "BACKUP_TOKEN_X"]) == ["bot", "caddy", "grafana", "vaultwarden"]
    assert st.restart_for(["COMPOSE_PROFILES"]) == "all"


def test_yaml_and_apply(tmp_path, monkeypatch):
    from netwatch import settings as st
    monkeypatch.setattr(st, "RUN_DIR", tmp_path)
    y = tmp_path / "homelab.yml"
    y.write_text("infrastructure:\n  modem: {ip: 192.168.1.1}\n")
    data, _ = st.read_yaml(y)
    st.yaml_set(data, "infrastructure.modem.ip", "192.168.1.254")
    st.yaml_set(data, "dns.adguard_url", "")
    st.write_yaml(y, data)
    data, raw = st.read_yaml(y)
    assert st.yaml_get(data, "infrastructure.modem.ip") == "192.168.1.254" and data["dns"]["adguard_url"] == ""
    import pytest as _p
    with _p.raises(Exception):
        st.write_yaml_raw(y, "esto: [no cierra")
    with _p.raises(ValueError):
        st.write_yaml_raw(y, "- una lista")
    assert st.apply_status()["installed"] is False
    st.request_apply(["bot"], ["bot"])
    s = st.apply_status()
    assert s["pending"] is True and json.loads((tmp_path / "apply-request").read_text())["services"] == ["bot"]


def test_adguard_autodetect():
    from netwatch.adguard import AdGuard
    a = AdGuard(None)                       # sin dns: en homelab.yml
    assert a.auto and a.url == "http://127.0.0.1:3002" and not a.enabled
    a.reachable = True
    assert a.enabled
    off = AdGuard("")                       # adguard_url: "" = apagado a propósito
    assert not off.enabled and off.poll() == []
