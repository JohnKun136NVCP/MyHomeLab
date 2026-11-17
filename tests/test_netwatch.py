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


def test_controld_upstream(tmp_path):
    """Control D como DNS de salida de AdGuard (servidor AdGuard falso)."""
    import base64
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from netwatch.adguard import AdGuard, upstream_urls
    state = {"upstream": ["https://dns10.quad9.net/dns-query"], "posts": []}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _ok(self, obj):
            b = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def _auth(self):
            if self.headers.get("Authorization") != "Basic " + base64.b64encode(b"admin:clave").decode():
                self.send_response(401)
                self.end_headers()
                return False
            return True

        def do_GET(self):
            if self._auth():
                self._ok({"upstream_dns": state["upstream"]} if self.path.startswith("/control/dns_info")
                         else {"data": []} if "querylog" in self.path else {})

        def do_POST(self):
            if self._auth():
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                state["posts"].append(body)
                state["upstream"] = body["upstream_dns"]
                self._ok({})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"

    assert upstream_urls("controld-family") == ["https://freedns.controld.com/family"]
    assert upstream_urls("controld-custom", "abc123xyz") == ["https://dns.controld.com/abc123xyz"]
    assert upstream_urls("controld-custom", "") == [] and upstream_urls("") == []

    bad = AdGuard(url, "admin", "mal")
    ok, msg = bad.set_upstream(["https://freedns.controld.com/family"])
    assert not ok and "usuario" in msg

    ag = AdGuard(url, "admin", "clave")
    assert ag.get_upstream() == ["https://dns10.quad9.net/dns-query"]
    ok, msg = ag.set_upstream(["https://freedns.controld.com/family"])
    assert ok and state["upstream"] == ["https://freedns.controld.com/family"]
    ok, msg = ag.set_upstream(["https://freedns.controld.com/family"])
    assert ok and msg == "ya estaba así" and len(state["posts"]) == 1

    # El monitor lo vuelve a poner si AdGuard se reinstaló
    from netwatch import monitor as M
    mon = M.Monitor({"network": {"networks": [{"name": "casa", "cidr": "192.168.1.0/24"}]},
                     "dns": {"adguard_url": url, "upstream": "controld-p2"}}, DB(tmp_path / "n.db"), tmp_path)
    mon.adguard.user, mon.adguard.password = "admin", "clave"
    mon.poll_dns()
    assert state["upstream"] == ["https://freedns.controld.com/p2"]
    srv.shutdown()


# ------------------------------------------------------------------- VPN
WG_DUMP = (
    "wg-hoshimy\tPRIVADA_NO_DEBE_SALIR=\tPUBPI=\t0\toff\n"
    "wg-hoshimy\tPUBVPS=\t(none)\t203.0.113.7:51820\t10.66.0.0/24\t%d\t1048576\t2097152\t25\n"
    "wg-hoshimy\tPUBTEL=\tPSKSECRETA=\t(none)\t10.66.0.10/32\t0\t0\t0\toff\n")


def test_vpn_parse_and_guess():
    import time as _t

    from netwatch import vpn
    d = vpn.parse_dump(WG_DUMP % int(_t.time()))
    itf = d["wg-hoshimy"]
    assert itf["public_key"] == "PUBPI=" and len(itf["peers"]) == 2
    assert "PRIVADA" not in json.dumps(d) and "PSKSECRETA" not in json.dumps(d)
    h = vpn.guess_hub(itf, ["10.66.0.2/24"])
    assert h == {"network": "10.66.0.0/24", "my_ip": "10.66.0.2", "hub_ip": "10.66.0.1",
                 "hub_endpoint": "203.0.113.7:51820", "hub_key": "PUBVPS=", "warnings": []}
    # Peer del VPS solo con /32 → advierte que los demás equipos no llegarán a la Pi
    itf2 = vpn.parse_dump(WG_DUMP.replace("10.66.0.0/24", "10.66.0.1/32") % int(_t.time()))["wg-hoshimy"]
    h2 = vpn.guess_hub(itf2, ["10.66.0.2/24"])
    assert h2["hub_ip"] == "10.66.0.1" and any("AllowedIPs" in w for w in h2["warnings"])
    old = vpn.guess_hub(vpn.parse_dump(WG_DUMP % (int(_t.time()) - 900))["wg-hoshimy"], ["10.66.0.2/24"])
    assert any("5 minutos" in w for w in old["warnings"])
    used = {"10.66.0.1", "10.66.0.2", "10.66.0.10"}
    assert vpn.next_free_ip("10.66.0.0/24", used) == "10.66.0.11"
    conf = vpn.client_config("PRIV=", "10.66.0.11", h, dns="10.66.0.2")
    assert "Address = 10.66.0.11/32" in conf and "Endpoint = 203.0.113.7:51820" in conf
    assert "AllowedIPs = 10.66.0.0/24" in conf and "DNS = 10.66.0.2" in conf and "PublicKey = PUBVPS=" in conf
    cmds = vpn.vps_commands("wg0", "telefono", "PUBNUEVA=", "10.66.0.11")
    assert "sudo wg set wg0 peer PUBNUEVA= allowed-ips 10.66.0.11/32" in cmds and "PRIV" not in cmds


def test_vpn_web(tmp_path, monkeypatch):
    """API de la VPN con WireGuard simulado (sin wg real)."""
    import time as _t

    from netwatch import vpn
    from netwatch import settings as st
    monkeypatch.setattr(vpn, "available", lambda: True)
    monkeypatch.setattr(vpn, "_run", lambda cmd, inp=None, timeout=10: (0, WG_DUMP % int(_t.time()))
                        if cmd[:2] == ["wg", "show"] else (0, ""))
    monkeypatch.setattr(vpn, "addresses", lambda iface: ["10.66.0.2/24"])
    monkeypatch.setattr(vpn, "genkeys", lambda: ("PRIVNUEVA=", "PUBNUEVA="))
    monkeypatch.setattr(st, "RUN_DIR", tmp_path)
    cfgp = tmp_path / "homelab.yml"
    cfgp.write_text("infrastructure:\n  nodes:\n  - {name: raspberry, target: host.docker.internal:9100}\n")
    monkeypatch.setenv("HOMELAB_CONFIG", str(cfgp))
    import threading
    import urllib.request

    import uvicorn

    from netwatch import monitor as M
    from netwatch.web import build_app
    mon = M.Monitor({"network": {"networks": [{"name": "casa", "cidr": "192.168.1.0/24"}]}},
                    DB(tmp_path / "n.db"), tmp_path)
    server = uvicorn.Server(uvicorn.Config(build_app(mon), host="127.0.0.1", port=0, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        _t.sleep(0.05)
    base = f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"

    def call(path, body=None, method=None):
        req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                     method=method or ("POST" if body is not None else "GET"))
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    code, j = call("/api/vpn")
    itf = j["interfaces"][0]
    assert code == 200 and itf["hub"]["hub_ip"] == "10.66.0.1" and not itf["hub"]["monitored"]
    assert {p["name"] for p in itf["peers"]} == {"VPS", ""}
    code, c = call("/api/vpn/client", {"name": "telefono", "iface": "wg-hoshimy", "dns": True})
    assert code == 200 and c["ip"] == "10.66.0.11" and "PrivateKey = PRIVNUEVA=" in c["config"]
    assert "DNS = 10.66.0.2" in c["config"] and "allowed-ips 10.66.0.11/32" in c["vps_commands"]
    code, c2 = call("/api/vpn/client", {"name": "laptop", "iface": "wg-hoshimy"})
    assert c2["ip"] == "10.66.0.12" and "DNS" not in c2["config"]
    code, _ = call("/api/vpn/client", {"name": "", "iface": "wg-hoshimy"})
    assert code == 400
    code, m = call("/api/vpn/monitor", {"iface": "wg-hoshimy"})
    assert code == 200 and m["ip"] == "10.66.0.1" and m["applied"]
    y, _ = st.read_yaml(cfgp)
    assert {"name": "vps", "target": "10.66.0.1:9100"} in y["infrastructure"]["nodes"]
    assert {"name": "vps", "host": "10.66.0.1"} in y["infrastructure"]["ping_targets"]
    code, j = call("/api/vpn")
    assert j["interfaces"][0]["hub"]["monitored"] and len(j["clients"]) == 2
    code, d = call(f"/api/vpn/client/{j['clients'][0]['id']}", method="DELETE")
    assert code == 200 and "peer PUBNUEVA= remove" in d["vps_commands"]
    from netwatch.metrics import render
    txt = render(mon)
    assert 'homelab_wg_peer_last_handshake_seconds{iface="wg-hoshimy",peer="VPS",endpoint="203.0.113.7:51820"}' in txt
    server.should_exit = True


# ----------------------------------------------------------------- sesión
def test_auth_tokens(tmp_path, monkeypatch):
    from netwatch import auth
    from netwatch import settings as st
    env = tmp_path / ".env"
    env.write_text("WEB_USER=admin\nWEB_PASSWORD_HASH=$$2a$$14$$abc\n")
    monkeypatch.setattr(st, "ENV_PATH", env)
    monkeypatch.setattr(auth, "_secret", b"k" * 32)
    t = auth.make_token("admin", now=1000)
    assert auth.check_token(t, now=2000) == "admin"
    assert auth.check_token(t, now=1000 + auth.TTL + 1) is None           # caducó
    assert auth.check_token(t[:-2] + "xx", now=2000) is None             # firma alterada
    assert auth.check_token("basura", now=2000) is None
    env.write_text("WEB_USER=otro\nWEB_PASSWORD_HASH=x\n")              # cambiaste el usuario
    assert auth.check_token(t, now=2000) is None
    assert auth.credentials() == ("otro", "x")
    assert auth.safe_next("/#ajustes") == "/#ajustes" and auth.safe_next("//malo.com") == "/"
    assert auth.safe_next("https://malo.com") == "/" and auth.safe_next("/\\\\malo") == "/"
    for _ in range(auth.MAX_FAILS):
        assert not auth.too_many("1.2.3.4", now=50)
        auth.record_fail("1.2.3.4", now=50)
    assert auth.too_many("1.2.3.4", now=60) and not auth.too_many("1.2.3.4", now=50 + auth.WINDOW + 1)


def test_login_flow(tmp_path, monkeypatch):
    import http.client
    import threading
    import time as _t
    from urllib.parse import urlencode

    import uvicorn

    from netwatch import auth
    from netwatch import monitor as M
    from netwatch import settings as st
    from netwatch.web import build_app
    env = tmp_path / ".env"
    env.write_text("WEB_USER=admin\nWEB_PASSWORD_HASH=$$2a$$14$$hash\n")
    monkeypatch.setattr(st, "ENV_PATH", env)
    monkeypatch.setattr(auth, "_secret", b"s" * 32)
    monkeypatch.setattr(auth, "_fails", __import__("collections").defaultdict(__import__("collections").deque))
    monkeypatch.setattr(auth, "verify_password", lambda pw, h: pw == "correcta" and h == "$2a$14$hash")
    db = DB(tmp_path / "n.db")
    mon = M.Monitor({"network": {"networks": [{"name": "casa", "cidr": "192.168.1.0/24"}]}}, db, tmp_path)
    server = uvicorn.Server(uvicorn.Config(build_app(mon), host="127.0.0.1", port=0, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        _t.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]

    def req(method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        return r.status, dict(r.getheaders()), r.read().decode()

    # Sin cookie: páginas → /login ; API → 401
    code, h, _ = req("GET", "/auth/check", headers={"X-Forwarded-Uri": "/#vpn"})
    assert code == 302 and h["location"].startswith("/login?next=")
    code, _, b = req("GET", "/auth/check", headers={"X-Forwarded-Uri": "/api/devices"})
    assert code == 401 and "Sesión" in b
    code, _, b = req("GET", "/login?next=/%23vpn")
    assert code == 200 and 'name="password"' in b and 'value="/#vpn"' in b
    form = {"Content-Type": "application/x-www-form-urlencoded", "X-Forwarded-For": "192.168.1.66"}
    code, _, b = req("POST", "/auth/login", urlencode({"user": "admin", "password": "mal", "next": "/"}), form)
    assert code == 401 and "incorrectos" in b
    code, h, _ = req("POST", "/auth/login", urlencode({"user": "admin", "password": "correcta",
                                                       "next": "//malo.com"}), form)
    assert code == 303 and h["location"] == "/"
    cookie = h["set-cookie"].split(";")[0]
    assert "HttpOnly" in h["set-cookie"] and "Secure" in h["set-cookie"]
    code, h, _ = req("GET", "/auth/check", headers={"Cookie": cookie, "X-Forwarded-Uri": "/"})
    assert code == 200 and h["x-homelab-user"] == "admin"
    code, _, b = req("GET", "/api/me", headers={"Cookie": cookie})
    assert json.loads(b)["user"] == "admin"
    code, h, _ = req("POST", "/auth/logout", headers={"Cookie": cookie})
    assert code == 303 and h["location"] == "/login" and 'hl_session=""' in h["set-cookie"]
    # 5 fallos desde la misma IP → bloqueo y aviso al bot
    for _ in range(auth.MAX_FAILS):
        req("POST", "/auth/login", urlencode({"user": "x", "password": "y"}), {**form, "X-Forwarded-For": "10.0.0.9"})
    code, _, b = req("POST", "/auth/login", urlencode({"user": "admin", "password": "correcta"}),
                     {**form, "X-Forwarded-For": "10.0.0.9"})
    assert code == 429 and "Demasiados" in b
    assert any(e["kind"] == "login_failed" and "10.0.0.9" in e["detail"] for e in db.events())
    server.should_exit = True
