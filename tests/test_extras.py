"""No-break (NUT), inicio de sesión único (Authelia), Wake-on-LAN y avisos de Diun."""
import json
import socket
import sys
import threading
import time
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "services" / "common"), str(ROOT / "services" / "bot"),
                str(ROOT / "services" / "netwatch")]

from netwatch import nut, sso, wol  # noqa: E402


# ------------------------------------------------------------------ NUT
def fake_upsd(lines):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def run():
        c, _ = srv.accept()
        c.recv(100)
        c.sendall(("BEGIN LIST VAR ups\n" + "".join(f"VAR ups {l}\n" for l in lines)
                   + "END LIST VAR ups\n").encode())
        c.recv(100)
        c.close()
    threading.Thread(target=run, daemon=True).start()
    return srv.getsockname()[1]


def test_nut_lee_el_no_break():
    port = fake_upsd(['ups.status "OB DISCHRG"', 'battery.charge "64"', 'battery.runtime "900"',
                      'ups.load "22"', 'device.model "Back-UPS 600"'])
    st = nut.query("127.0.0.1", "ups", port)
    assert st["on_battery"] and not st["low_battery"]
    assert st["charge"] == 64 and st["runtime"] == 900 and st["model"] == "Back-UPS 600"
    assert "usando batería" in st["status_text"]


def test_nut_sin_conexion_no_truena(monkeypatch):
    monkeypatch.setenv("NUT_HOST", "127.0.0.1")
    monkeypatch.setattr(nut, "query", lambda *a, **k: (_ for _ in ()).throw(ConnectionRefusedError("no")))
    st = nut.Ups().status()
    assert st["enabled"] and not st["ok"]
    monkeypatch.delenv("NUT_HOST")
    assert nut.Ups().status() == {"enabled": False}


# ------------------------------------------------------------------ SSO
def test_users_yml_para_authelia():
    txt = sso.render_users([
        {"user": "john", "hash": "$2b$12$abc", "role": "admin"},
        {"user": "mama", "hash": "$2b$12$def", "role": "familia"},
        {"user": "raro", "hash": "plano", "role": "familia"},           # sin bcrypt: no se copia
    ], "casa.lan")
    d = yaml.safe_load(txt)["users"]
    assert set(d) == {"john", "mama"}
    assert d["john"]["groups"] == ["admin"] and d["mama"]["groups"] == ["familia"]
    assert d["mama"]["email"] == "mama@casa.lan" and d["john"]["password"] == "$2b$12$abc"
    assert yaml.safe_load(sso.render_users([], "casa.lan")) == {"users": {}}


def test_solo_se_confia_en_remote_user_con_el_secreto(monkeypatch):
    monkeypatch.setenv("SSO_SECRET", "s3cr3t")
    monkeypatch.setenv("SSO_DOMAIN", "casa.lan")
    assert sso.trusted_user({"x-homelab-sso": "s3cr3t", "remote-user": "mama"}) == "mama"
    assert sso.trusted_user({"x-homelab-sso": "otro", "remote-user": "mama"}) == ""
    assert sso.trusted_user({"remote-user": "john"}) == ""
    monkeypatch.setenv("SSO_SECRET", "")
    assert sso.trusted_user({"x-homelab-sso": "", "remote-user": "john"}) == ""


def test_sync_escribe_y_reenvia_codigo(tmp_path, monkeypatch):
    monkeypatch.setenv("SSO_SECRET", "x")
    monkeypatch.setenv("SSO_DOMAIN", "casa.lan")
    (tmp_path / "authelia").mkdir()

    class DB:
        def q(self, *_):
            return [{"username": "mama", "hash": "$2b$12$zz", "role": "familia"}]

    from netwatch import auth
    monkeypatch.setattr(auth, "credentials", lambda: ("john", "$2b$12$yy"))
    sent = []
    s = sso.Sync(DB(), tmp_path, lambda text, owner=False: sent.append((text, owner)))
    s.run(force=True)
    users = yaml.safe_load((tmp_path / "authelia" / "users.yml").read_text())["users"]
    assert users["john"]["groups"] == ["admin"] and users["mama"]["groups"] == ["familia"]
    (tmp_path / "authelia" / "notification.txt").write_text("Código: 123456\n")
    s.run(force=True)
    assert sent and "123456" in sent[-1][0] and sent[-1][1] is True


# ------------------------------------------------------------------ WoL
def test_paquete_magico():
    p = wol.packet("AA-BB-CC-DD-EE-FF")
    assert len(p) == 102 and p[:6] == b"\xff" * 6 and p[6:12] == bytes.fromhex("aabbccddeeff")
    assert wol.norm_mac("aabb.ccdd.eeff".replace(".", "")) == "aa:bb:cc:dd:ee:ff"
    with pytest.raises(ValueError):
        wol.norm_mac("hola")
    assert "192.168.1.255" in wol.broadcasts(["192.168.1.0/24"])
    assert wol.targets({"wake_on_lan": [{"name": "PC", "mac": "AA:BB:CC:DD:EE:01"}, {"name": "malo"}]}) == \
        [{"name": "PC", "mac": "aa:bb:cc:dd:ee:01"}]


def test_wol_sale_por_udp():
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", 0))
    port = rx.getsockname()[1]
    rx.settimeout(2)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.sendto(wol.packet("aa:bb:cc:dd:ee:ff"), ("127.0.0.1", port))
    data, _ = rx.recvfrom(200)
    assert data[6:12] == bytes.fromhex("aabbccddeeff")


# ------------------------------------------------------------------ bot
def test_aviso_de_diun():
    from bot import formatting
    t = formatting.diun({"diun_version": "4.28", "status": "update", "image": "docker.io/louislam/uptime-kuma:2-slim",
                         "hub_link": "https://hub.docker.com/r/louislam/uptime-kuma",
                         "metadata": {"ctn_names": "homelab-pi-uptime-kuma-1"}})
    assert "versión nueva" in t and "uptime-kuma:2-slim" in t and "homelab-pi-uptime-kuma-1" in t


def test_panel_con_inicio_de_sesion_unico(tmp_path, monkeypatch):
    """Caddy+Authelia mandan Remote-User + secreto: el panel respeta el rol de esa cuenta."""
    import http.client
    import uvicorn
    from netwatch import auth
    from netwatch import monitor as M
    from netwatch import settings as st
    from netwatch.db import DB
    from netwatch.web import build_app
    env = tmp_path / ".env"
    env.write_text("WEB_USER=john\nWEB_PASSWORD_HASH=$$2b$$12$$hash\nHOMELAB_HOST=127.0.0.1\n"
                   "COMPOSE_PROFILES=speedtest,diun\n")
    monkeypatch.setattr(st, "ENV_PATH", env)
    monkeypatch.setattr(auth, "_secret", b"s" * 32)
    monkeypatch.setenv("SSO_SECRET", "s3cr3t")
    monkeypatch.setenv("SSO_DOMAIN", "casa.lan")
    monkeypatch.setenv("HTTPS_PORT", "8443")
    db = DB(tmp_path / "n.db")
    db.x("INSERT INTO users (username, hash, role, created) VALUES ('mama', '$2b$12$m', 'familia', 0)")
    mon = M.Monitor({"network": {"networks": [{"name": "casa", "cidr": "192.168.1.0/24"}]},
                     "wake_on_lan": [{"name": "PC", "mac": "aa:bb:cc:dd:ee:01"}],
                     "links": [{"name": "Fotos (Immich)", "url": "http://127.0.0.1:9/", "icon": "📸"},
                               {"name": "Cine en casa", "url": "http://127.0.0.1:9", "icon": "🍿"}]}, db, tmp_path)
    server = uvicorn.Server(uvicorn.Config(build_app(mon), host="127.0.0.1", port=0, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]

    def req(method, path, headers=None, body=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        return r.status, dict(r.getheaders()), r.read().decode()

    sso_h = lambda u: {"X-Homelab-Sso": "s3cr3t", "Remote-User": u}
    code, h, _ = req("GET", "/auth/check", {**sso_h("john"), "X-Forwarded-Uri": "/api/settings"})
    assert code == 200 and h["x-homelab-role"] == "admin"
    code, _, _ = req("GET", "/auth/check", {**sso_h("mama"), "X-Forwarded-Uri": "/api/settings"})
    assert code == 403
    code, _, b = req("GET", "/api/me", sso_h("mama"))
    assert json.loads(b)["user"] == "mama"
    # Remote-User sin el secreto (alguien que lo inventa) no sirve
    code, h, _ = req("GET", "/auth/check", {"Remote-User": "john", "X-Forwarded-Uri": "/"})
    assert code == 302
    code, _, _ = req("GET", "/auth/check", {"X-Homelab-Sso": "mal", "Remote-User": "john",
                                           "X-Forwarded-Uri": "/api/devices"})
    assert code == 401
    # Cerrar sesión por SSO lleva al portal de Authelia
    code, h, _ = req("GET", "/auth/logout", sso_h("john"))
    assert code == 303 and h["location"] == "https://auth.casa.lan:8443/logout"
    # Wake-on-LAN: la lista y el envío
    code, _, b = req("GET", "/api/wol")
    assert json.loads(b)["targets"][0]["name"] == "PC"
    code, _, b = req("POST", "/api/wol", {"Content-Type": "application/json"}, json.dumps({"mac": "nada"}))
    assert code == 400
    # Immich, Speedtest, Diun y lo de la PC aparecen en el panel (Inicio y Servicios)
    code, _, b = req("GET", "/api/tools")
    tools = {t["name"]: t for t in json.loads(b)["tools"]}
    assert tools["Fotos (Immich)"]["group"] == "Servicios" and tools["Fotos (Immich)"]["status"] is False
    assert {"Velocidad del internet", "Avisos de versiones", "Cine en casa"} <= set(tools)
    assert not any(t["group"] == "Tus enlaces" and t["name"] == "Fotos (Immich)" for t in tools.values())
    code, _, b = req("GET", "/api/services")
    names = [t["name"] for t in json.loads(b)["homelab"]]
    assert "Fotos (Immich)" in names and "Velocidad del internet" in names
    server.should_exit = True


# ------------------------------------------------------- SSH en otro puerto y UFW de la Pi
def _bash(code, **env):
    import os
    import subprocess
    return subprocess.run(["bash", "-c", code], capture_output=True, text=True,
                          env={**os.environ, **env}, cwd=ROOT).stdout


def test_puerto_ssh_de_la_pi():
    out = _bash('. scripts/lib/ssh.sh; ssh_target pi@192.168.1.69:2222; echo "$SSH_USER|$SSH_IP|$SSH_PORT|${RSSH[1]} ${RSSH[2]}"')
    assert out.strip() == "pi|192.168.1.69|2222|-p 2222"
    out = _bash('. scripts/lib/ssh.sh; SSH_PORT_OVERRIDE=2200; ssh_target pi@10.0.0.2; echo "$SSH_PORT|$RSYNC_SSH"')
    assert out.startswith("2200|ssh -p 2200")


def test_puerto_ssh_fijo_en_homelab_yml(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "homelab.yml").write_text("firewall:\n  ssh:\n    port: [2222, 22]\n")
    assert _bash(". scripts/lib/ssh.sh; ssh_ports", HL_REPO=str(tmp_path)).split() == ["22", "2222"]


def test_reglas_ufw_de_la_pi_se_adaptan(tmp_path):
    import re
    import subprocess
    src = (ROOT / "scripts" / "migrate.sh").read_text()
    py = re.search(r"<<'PY'\n(import re, sys\nf, old, new, casa, lab, sshp.*?)\nPY\n", src, re.S).group(1)
    rules = tmp_path / "ufw.txt"
    rules.write_text("Added user rules:\n"
                     "ufw allow 2222/tcp\n"
                     "ufw allow from 192.168.100.10 to any port 2222 proto tcp comment 'SSH laptop'\n"
                     "ufw allow from 192.168.1.0/24 to any port 8443 proto tcp comment 'Dashboard homelab'\n"
                     "ufw allow from 192.168.1.0/24 to any port 8090 proto tcp comment 'mi app'\n"
                     "ufw allow in on wlan0 to any port 5201 proto tcp\n"
                     "ufw allow in on eth0 to 192.168.1.69 port 51820 proto udp\n")
    out = subprocess.run([sys.executable, "-c", py, str(rules), "192.168.1.69", "192.168.1.50", "enp3s0", "", "2222"],
                         capture_output=True, text=True)
    assert out.stdout.splitlines() == ["allow from 192.168.1.0/24 to any port 8090 proto tcp comment 'mi app'",
                                       "allow in on enp3s0 to 192.168.1.50 port 51820 proto udp"]
    assert "SSH" in out.stderr and "laboratorio" in out.stderr      # wlan0 sin red de lab: no se copia


# ------------------------------------------------------- reparto (Ansible) y herramientas de la casa
def test_inventario_de_ansible(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts" / "lib"))
    import inventory
    cfg = tmp_path / "homelab.yml"
    cfg.write_text(yaml.safe_dump({"machines": [
        {"name": "pi", "host": "192.168.1.69", "user": "john", "local": True, "roles": ["core"]},
        {"name": "minipc", "host": "192.168.1.50", "user": "john", "ssh_port": 2222,
         "roles": ["media", "photos", "home", "dns_backup"], "home": ["paperless"]},
        {"name": "laptop", "host": "192.168.1.60", "laptop": True, "roles": ["lab"], "lab": ["wazuh"]}]}))
    inv = inventory.inventory(inventory.load(cfg), "https://example/repo.git")["all"]
    assert inv["vars"]["core_ip"] == "192.168.1.69" and inv["vars"]["core_name"] == "pi"
    assert inv["hosts"]["pi"]["ansible_connection"] == "local"
    assert inv["hosts"]["minipc"]["ansible_port"] == 2222 and inv["hosts"]["minipc"]["home_services"] == ["paperless"]
    assert set(inv["children"]) == {"core", "media", "photos", "home", "dns_backup", "lab", "laptops"}
    assert "laptop" in inv["children"]["laptops"]["hosts"]
    cfg.write_text(yaml.safe_dump({"machines": [{"name": "a", "host": "x", "roles": ["core", "dns_backup", "nada"]}]}))
    with pytest.raises(SystemExit) as e:
        inventory.load(cfg)
    assert "nada" in str(e.value) and "dns_backup" in str(e.value)


def test_botones_del_panel_y_casa(tmp_path):
    import shutil
    import subprocess
    repo = tmp_path / "r"
    (repo / "scripts" / "lib").mkdir(parents=True)
    (repo / "config").mkdir()
    shutil.copy(ROOT / "scripts" / "lib" / "links.py", repo / "scripts" / "lib")
    (repo / "config" / "homelab.yml").write_text("links:\n- {name: Grafana, url: /grafana/}\n")
    run = lambda *a: subprocess.run([sys.executable, str(repo / "scripts/lib/links.py"), *a], check=True)
    run("add", "Música (Navidrome)", "http://10.0.0.5:4533", "🎵", "tu música", "http://10.0.0.5:4533/ping")
    run("add", "Música (Navidrome)", "http://10.0.0.6:4533", "🎵", "tu música", "")
    links = yaml.safe_load((repo / "config/homelab.yml").read_text())["links"]
    assert [l["name"] for l in links] == ["Grafana", "Música (Navidrome)"]
    assert links[1]["url"].endswith("10.0.0.6:4533") and links[1]["group"] == "Servicios"
    run("remove", "Música (Navidrome)")
    assert len(yaml.safe_load((repo / "config/homelab.yml").read_text())["links"]) == 1
    # home.sh: la lista y los botones de lo encendido, sin Docker
    shutil.copytree(ROOT / "scripts", repo / "scripts", dirs_exist_ok=True)
    (repo / "hosts" / "home").mkdir(parents=True)
    (repo / "hosts" / "home" / ".env").write_text("HOME_HOST=10.0.0.5\nCOMPOSE_PROFILES=navidrome,umami\nHOME_DATA=/tmp/x\n")
    out = subprocess.run(["bash", str(repo / "scripts/home.sh"), "links"], capture_output=True, text=True, cwd=repo).stdout
    assert '"Música (Navidrome)" "http://10.0.0.5:4533"' in out and "3007/api/heartbeat" in out
    lst = subprocess.run(["bash", str(repo / "scripts/home.sh"), "list"], capture_output=True, text=True, cwd=repo).stdout
    assert "navidrome" in lst and "paperless" in lst
