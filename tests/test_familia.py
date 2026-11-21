"""Respaldos de la familia de punta a punta: dashboard (netwatch) + bot + Telegram falso.

Cada persona agrega su equipo, el instalador sale ya configurado, el respaldo llega a la
Pi y el aviso (y el archivo) le llega SOLO a su dueño; las alertas del homelab solo a quien
tiene permiso de ver registros.
"""
import http.client
import json
import os
import sys
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "services" / "common"), str(ROOT / "services" / "bot"),
                str(ROOT / "services" / "netwatch")]

from test_bot import TG, Fake  # noqa: E402

ADMIN_CHAT, MAMA_CHAT, EXTRANO = 1000, 3000, 4000


def wait(cond, t=8.0):
    end = time.time() + t
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False


def to_chat(chat):
    """Mensajes y documentos que le llegaron a ese chat."""
    out = []
    for m, p, b in list(Fake.calls):
        if p.endswith("/sendMessage"):
            j = json.loads(b)
            if j.get("chat_id") == chat:
                out.append(("msg", j["text"]))
        elif p.endswith("/sendDocument") and f'name="chat_id"\r\n\r\n{chat}\r\n'.encode() in b:
            out.append(("doc", b.decode(errors="replace")))
    return out


@pytest.fixture(scope="module")
def lab(tmp_path_factory):
    import uvicorn
    tmp = tmp_path_factory.mktemp("familia")
    Fake.calls.clear()                     # (comparte la clase con test_bot)
    Fake.updates.clear()
    tg = ThreadingHTTPServer(("127.0.0.1", 0), TG)
    threading.Thread(target=tg.serve_forever, daemon=True).start()
    Fake.local_file = tmp / "tg-file.bin"
    Fake.local_file.write_bytes(b"foto" * 100)
    cfg = tmp / "homelab.yml"
    cfg.write_text(f"backups: {{dir: {tmp}/backups, keep_per_device: 5, forward_to_telegram: true, devices: []}}\n"
                   "network: {networks: [{name: casa, cidr: 192.168.1.0/24}]}\n")
    os.environ.update(HOMELAB_CONFIG=str(cfg), DATA_DIR=str(tmp / "bot"), TELEGRAM_BOT_TOKEN="123:X",
                      TELEGRAM_ALLOWED_IDS=str(ADMIN_CHAT), TELEGRAM_API_URL=f"http://127.0.0.1:{tg.server_address[1]}",
                      INTERNAL_TOKEN="interno", PROMETHEUS_URL="http://127.0.0.1:1",
                      TELEGRAM_BOT_USERNAME="casa_bot")
    # ---- netwatch
    from netwatch import auth, respaldos
    from netwatch import monitor as M
    from netwatch import settings as st
    from netwatch.db import DB
    from netwatch.web import build_app
    env = tmp / ".env"
    env.write_text("WEB_USER=admin\nWEB_PASSWORD_HASH=H:adminpw1\n")
    st.ENV_PATH = env
    auth._secret = b"f" * 32
    auth.verify_password = lambda pw, h: h == "H:" + pw
    st.hash_password = lambda pw: "H:" + pw
    respaldos.CLIENTS = ROOT / "clients"
    db = DB(tmp / "n.db")
    import yaml
    mon = M.Monitor(yaml.safe_load(cfg.read_text()), db, tmp)
    nw = uvicorn.Server(uvicorn.Config(build_app(mon), host="127.0.0.1", port=0, log_level="error"))
    threading.Thread(target=nw.run, daemon=True).start()
    while not nw.started:
        time.sleep(0.05)
    nw_port = nw.servers[0].sockets[0].getsockname()[1]
    os.environ["NETWATCH_URL"] = f"http://127.0.0.1:{nw_port}"
    # ---- bot
    from bot.app import BotApp
    app = BotApp()
    bt = uvicorn.Server(uvicorn.Config(app.asgi(), host="127.0.0.1", port=0, log_level="error"))
    threading.Thread(target=bt.run, daemon=True).start()
    while not bt.started:
        time.sleep(0.05)
    bot_port = bt.servers[0].sockets[0].getsockname()[1]
    os.environ["BOT_URL"] = f"http://127.0.0.1:{bot_port}"
    db.x("INSERT INTO users (username, hash, role, created) VALUES ('mama', 'H:mamapass1', 'familia', 0)")
    yield {"nw": nw_port, "bot": bot_port, "db": db, "app": app, "tmp": tmp}
    nw.should_exit = bt.should_exit = True
    tg.shutdown()


def req(port, method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    c.request(method, path, body=body, headers=headers or {})
    r = c.getresponse()
    return r.status, dict(r.getheaders()), r.read()


def login(lab, user, pw):
    code, h, _ = req(lab["nw"], "POST", "/auth/login", urlencode({"user": user, "password": pw}),
                     {"Content-Type": "application/x-www-form-urlencoded", "X-Forwarded-For": "192.168.1.40"})
    assert code == 303
    return {"Cookie": h["set-cookie"].split(";")[0], "Content-Type": "application/json", "Host": "pi.casa:8443"}


def test_familia_de_punta_a_punta(lab):
    mama = login(lab, "mama", "mamapass1")
    # Familia: no ve registros ni dispositivos; sí sus respaldos
    for uri, code in (("/api/devices", 403), ("/api/events", 403), ("/api/my/backups", 200), ("/#respaldos", 200)):
        c, _, _ = req(lab["nw"], "GET", "/auth/check", headers={**mama, "X-Forwarded-Uri": uri})
        assert c == code, uri

    # 1. Agrega su laptop Linux: el instalador sale configurado
    c, _, b = req(lab["nw"], "POST", "/api/my/devices", json.dumps({"os": "linux", "label": "laptop"}), mama)
    r = json.loads(b)
    assert c == 200 and r["device"] == "mama-laptop"
    assert r["setup"]["unix"].startswith("curl -fsSk https://pi.casa:8443/setup/")
    code = r["setup"]["code"]
    pi = {"Host": "pi.casa:8443"}                  # como lo pide el instalador
    _, _, conf = req(lab["nw"], "GET", f"/setup/{code}/respaldo.conf", headers=pi)
    conf = conf.decode()
    assert "DEVICE=mama-laptop" in conf and "URL=https://pi.casa:8443" in conf
    token = next(x.split("=", 1)[1] for x in conf.splitlines() if x.startswith("TOKEN="))
    _, _, inst = req(lab["nw"], "GET", f"/setup/{code}/instalar.sh", headers=pi)
    assert f"/setup/{code}/respaldo-facil.sh" in inst.decode()
    _, _, script = req(lab["nw"], "GET", f"/setup/{code}/respaldo-facil.sh")
    assert b"respaldo.conf" in script
    c, h, cmd = req(lab["nw"], "GET", f"/setup/{code}/Respaldo-mama-laptop.cmd")
    assert c == 200 and b"-Auto" in cmd and b"\r\n" in cmd
    assert req(lab["nw"], "GET", "/setup/codigo-falso-que-no-existe-xx/respaldo.conf")[0] == 404

    # 2. Vincula su Telegram con el código (botón «Abrir Telegram» = /start CÓDIGO)
    _, _, b = req(lab["nw"], "POST", "/api/my/telegram", "", mama)
    tgcode = json.loads(b)["code"]
    Fake.updates.append({"update_id": 777001, "message": {"message_id": 1, "date": int(time.time()),
                         "chat": {"id": MAMA_CHAT}, "from": {"id": MAMA_CHAT, "first_name": "Mamá"},
                         "text": f"/start {tgcode}"}})
    assert wait(lambda: any("Listo" in t for k, t in to_chat(MAMA_CHAT) if k == "msg"))
    assert lab["db"].q("SELECT chat_id FROM telegram_links WHERE username='mama'")[0]["chat_id"] == MAMA_CHAT

    # 3. Su laptop sube un respaldo: el archivo le llega SOLO a ella; al admin una línea sin archivo
    before_admin = len(to_chat(ADMIN_CHAT))
    c, _, _ = req(lab["bot"], "PUT", "/api/backup/mama-laptop/respaldo.tar.gz.enc", b"x" * 2048,
                  {"Authorization": f"Bearer {token}", "Content-Length": "2048"})
    assert c == 200
    assert wait(lambda: any(k == "doc" for k, _ in to_chat(MAMA_CHAT)))
    assert wait(lambda: any("Respaldo de <b>mama</b>" in t for k, t in to_chat(ADMIN_CHAT)[before_admin:] if k == "msg"))
    assert not any(k == "doc" for k, _ in to_chat(ADMIN_CHAT))
    assert req(lab["bot"], "PUT", "/api/backup/mama-laptop/x.enc", b"x", {"Authorization": "Bearer malo"})[0] == 401
    # Por defecto, ya que le llegó a Telegram, se borra de la Pi
    assert wait(lambda: any("lo borré de la Pi" in t for k, t in to_chat(MAMA_CHAT) if k == "msg"))
    _, _, b = req(lab["nw"], "GET", "/api/my/backups", headers=mama)
    assert json.loads(b)["prefs"] == {"send_telegram": True, "delete_after": True}
    assert next(d for d in json.loads(b)["devices"] if d["name"] == "mama-laptop")["count"] == 0
    # Prefiere conservarlo también en la Pi
    c, _, b = req(lab["nw"], "POST", "/api/my/prefs", json.dumps({"delete_after": False}), mama)
    assert c == 200 and json.loads(b)["prefs"]["delete_after"] is False
    time.sleep(0.1)
    lab["app"]._auth_cache.clear()      # la preferencia se lee al autenticar (caché de 5 min)
    docs_before = sum(1 for k, _ in to_chat(MAMA_CHAT) if k == "doc")
    c, _, _ = req(lab["bot"], "PUT", "/api/backup/mama-laptop/respaldo.tar.gz.enc", b"x" * 2048,
                  {"Authorization": f"Bearer {token}", "Content-Length": "2048"})
    assert c == 200
    assert wait(lambda: sum(1 for k, _ in to_chat(MAMA_CHAT) if k == "doc") > docs_before)

    # 4. Ve su respaldo y su aviso en el dashboard, y lo puede descargar (otro no)
    _, _, b = req(lab["nw"], "GET", "/api/my/backups", headers=mama)
    my = json.loads(b)
    dev = next(d for d in my["devices"] if d["name"] == "mama-laptop")
    assert dev["count"] == 1 and dev["files"][0]["name"] == "respaldo.tar.gz.enc" and dev["clave"]
    assert any("mama-laptop" in n["text"] for n in my["notices"]) and my["telegram"]
    c, _, data = req(lab["nw"], "GET", f"/api/my/devices/mama-laptop/files/{dev['files'][0]['id']}", headers=mama)
    assert c == 200 and data == b"x" * 2048
    lab["db"].x("INSERT INTO users (username, hash, role, created) VALUES ('primo', 'H:primopass1', 'familia', 0)")
    primo = login(lab, "primo", "primopass1")
    assert req(lab["nw"], "GET", f"/api/my/devices/mama-laptop/files/{dev['files'][0]['id']}", headers=primo)[0] == 404
    assert "mama-laptop" not in json.dumps(json.loads(req(lab["nw"], "GET", "/api/my/backups", headers=primo)[2]))

    # 5. Subida desde el celular (navegador): muchos archivos = un solo aviso
    _, _, b = req(lab["nw"], "POST", "/api/my/web-token", "", mama)
    w = json.loads(b)
    n_before = len(to_chat(MAMA_CHAT))
    for i in range(3):
        assert req(lab["bot"], "PUT", f"/api/backup/{w['device']}/foto{i}.jpg", b"img" * 10,
                   {"Authorization": f"Bearer {w['token']}", "X-Backup-Session": "web-1", "X-Backup-Batch": "1"})[0] == 200
    time.sleep(0.5)
    assert len(to_chat(MAMA_CHAT)) == n_before                     # nada por archivo
    req(lab["bot"], "PUT", f"/api/backup/{w['device']}/__fin__", b"",
        {"Authorization": f"Bearer {w['token']}", "X-Backup-Session": "web-1"})
    assert wait(lambda: any("Subiste 3 archivo(s)" in t for k, t in to_chat(MAMA_CHAT) if k == "msg"))

    # 6. Alertas del homelab: solo al admin… hasta que le das permiso de ver registros
    hdr = {"Authorization": "Bearer interno", "Content-Type": "application/json"}
    req(lab["bot"], "POST", "/notify", json.dumps({"text": "alerta-uno"}), hdr)
    assert wait(lambda: any("alerta-uno" in t for k, t in to_chat(ADMIN_CHAT)))
    time.sleep(0.3)
    assert not any("alerta-uno" in t for k, t in to_chat(MAMA_CHAT))
    lab["db"].x("UPDATE users SET role='registros' WHERE username='mama'")
    lab["app"]._rcpt = ([], 0.0)
    req(lab["bot"], "POST", "/notify", json.dumps({"text": "alerta-dos"}), hdr)
    assert wait(lambda: any("alerta-dos" in t for k, t in to_chat(MAMA_CHAT)))
    lab["db"].x("UPDATE users SET role='familia' WHERE username='mama'")
    lab["app"]._rcpt = ([], 0.0)

    # 7. Por Telegram: manda una foto → se guarda en SU respaldo; un desconocido no puede
    Fake.updates.append({"update_id": 777002, "message": {"message_id": 2, "date": int(time.time()),
                         "chat": {"id": MAMA_CHAT}, "from": {"id": MAMA_CHAT, "first_name": "Mamá"},
                         "document": {"file_id": "F1", "file_name": "receta.pdf", "file_size": 400}}})
    assert wait(lambda: any("Guardado en tu respaldo" in t for k, t in to_chat(MAMA_CHAT) if k == "msg"))
    assert list((lab["tmp"] / "backups" / "mama-telegram").rglob("receta.pdf"))
    Fake.updates.append({"update_id": 777003, "message": {"message_id": 3, "date": int(time.time()),
                         "chat": {"id": EXTRANO}, "from": {"id": EXTRANO, "first_name": "X"}, "text": "/misrespaldos"}})
    assert wait(lambda: any("intentó usar tu bot" in t for k, t in to_chat(ADMIN_CHAT)))
    assert not to_chat(EXTRANO)
    # Familia no puede usar comandos de registros
    Fake.updates.append({"update_id": 777004, "message": {"message_id": 4, "date": int(time.time()),
                         "chat": {"id": MAMA_CHAT}, "from": {"id": MAMA_CHAT}, "text": "/eventos"}})
    assert wait(lambda: any("no está disponible" in t for k, t in to_chat(MAMA_CHAT) if k == "msg"))

    # 8. Borrar la cuenta: su laptop ya no puede subir
    # Un respaldo que ya estaba en la Pi: 📲 en «Mis respaldos» se lo manda a su dueño
    _, _, b = req(lab["nw"], "GET", "/api/my/backups", headers=mama)
    dev = next(d for d in json.loads(b)["devices"] if d["name"] == "mama-laptop" and d["files"])
    before = sum(1 for k, _ in to_chat(MAMA_CHAT) if k == "doc")
    c, _, b = req(lab["nw"], "POST", f"/api/my/devices/mama-laptop/files/{dev['files'][0]['id']}/send", "", mama)
    assert c == 200 and json.loads(b)["ok"]
    assert wait(lambda: sum(1 for k, _ in to_chat(MAMA_CHAT) if k == "doc") > before)
    assert req(lab["nw"], "POST", "/api/my/devices/mama-laptop/files/999999/send", "", mama)[0] == 404
    assert req(lab["nw"], "DELETE", "/api/users/mama", headers={"Host": "x"})[0] == 200
    lab["app"]._auth_cache.clear()
    assert req(lab["bot"], "PUT", "/api/backup/mama-laptop/y.enc", b"y", {"Authorization": f"Bearer {token}"})[0] == 401


def test_script_respaldo_facil_linux(lab, tmp_path):
    """clients/unix/respaldo-facil.sh de verdad: comprime, cifra, sube y se puede restaurar."""
    import subprocess
    lab["db"].x("INSERT OR IGNORE INTO users (username, hash, role, created) VALUES ('papa', 'H:papapass1', 'familia', 0)")
    papa = login(lab, "papa", "papapass1")
    r = json.loads(req(lab["nw"], "POST", "/api/my/devices", json.dumps({"os": "linux"}), papa)[2])
    conf = req(lab["nw"], "GET", f"/setup/{r['setup']['code']}/respaldo.conf", headers={"Host": "x"})[2].decode()
    home = tmp_path / "home"
    (home / ".config" / "myhomelab").mkdir(parents=True)
    # En la casa la URL es la de Caddy; aquí apuntamos directo al bot
    conf = "\n".join(f"URL=http://127.0.0.1:{lab['bot']}" if x.startswith("URL=") else x for x in conf.splitlines())
    (home / ".config" / "myhomelab" / "respaldo.conf").write_text(conf + "\n")
    (home / "Documentos").mkdir()
    (home / "Documentos" / "tesis.txt").write_text("entropía de Shannon\n" * 50)
    (home / "Imágenes").mkdir()
    (home / "Imágenes" / "foto.jpg").write_bytes(os.urandom(5000))
    env = {**os.environ, "HOME": str(home), "PATH": f"{tmp_path}/bin:" + os.environ["PATH"]}
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "xdg-user-dir").write_text("#!/bin/sh\nexit 1\n")      # sin xdg: nombres en español
    (tmp_path / "bin" / "xdg-user-dir").chmod(0o755)
    p = subprocess.run(["bash", str(ROOT / "clients" / "unix" / "respaldo-facil.sh")], env=env,
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stdout + p.stderr
    assert (home / ".config" / "myhomelab" / "ultimo-respaldo").exists()
    got = list((lab["tmp"] / "backups" / r["device"]).rglob("*.tar.gz.enc"))
    assert len(got) == 1
    clave = next(x.split("=", 1)[1].strip("'") for x in conf.splitlines() if x.startswith("CLAVE="))
    out = tmp_path / "restaurado"
    out.mkdir()
    dec = subprocess.run(f"openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass pass:{clave} -in '{got[0]}' "
                         f"| tar xz -C '{out}'", shell=True, capture_output=True, text=True)
    assert dec.returncode == 0, dec.stderr
    assert (out / "Documentos" / "tesis.txt").read_text().startswith("entropía")
    assert (out / "Imágenes" / "foto.jpg").stat().st_size == 5000
    # --programado no repite si aún no toca
    p = subprocess.run(["bash", str(ROOT / "clients" / "unix" / "respaldo-facil.sh"), "--programado"], env=env,
                       capture_output=True, text=True, timeout=30)
    assert p.returncode == 0 and len(list((lab["tmp"] / "backups" / r["device"]).rglob("*.enc"))) == 1



def test_bots_solo_admin(lab):
    admin = login(lab, "admin", "adminpw1")
    c, _, body = req(lab["nw"], "GET", "/api/bots", headers=admin)
    assert c == 200 and json.loads(body)["bots"] == []
    c, _, body = req(lab["nw"], "POST", "/api/bots/nadie/do/restart", headers=admin)
    assert c == 400 and "No existe" in json.loads(body)["error"]
    from netwatch import auth
    assert not auth.allowed("registros", "GET", "/api/bots")


def test_contrasenas_de_servicios(lab, monkeypatch):
    from netwatch import settings as st
    run = lab["tmp"] / "run"
    (run / "passwords").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(st, "RUN_DIR", run)
    admin = login(lab, "admin", "adminpw1")
    c, _, b = req(lab["nw"], "POST", "/api/passwords/reset", json.dumps({"service": "grafana"}), admin)
    assert c == 200 and json.loads((run / "apply-request").read_text())["action"] == "password-reset:grafana"
    c, _, b = req(lab["nw"], "POST", "/api/passwords/reset", json.dumps({"service": "paperless", "user": "x;rm -rf"}), admin)
    assert c == 400
    c, _, b = req(lab["nw"], "POST", "/api/passwords/reset", json.dumps({"service": "nope"}), admin)
    assert c == 400
    assert json.loads(req(lab["nw"], "GET", "/api/passwords/result/grafana", headers=admin)[2])["pending"]
    (run / "passwords" / "grafana.json").write_text(json.dumps({"service": "grafana", "user": "admin", "password": "Nueva123"}))
    r = json.loads(req(lab["nw"], "GET", "/api/passwords/result/grafana", headers=admin)[2])
    assert r["password"] == "Nueva123" and not (run / "passwords" / "grafana.json").exists()   # una sola vez
    from netwatch import auth
    assert not auth.allowed("registros", "GET", "/api/passwords")
    # el panel pide app.js con versión para que el navegador no use uno viejo
    assert b"/static/app.js?v=" in req(lab["nw"], "GET", "/", headers=admin)[2]


def test_rutas_nuevas_de_red(lab):
    admin = login(lab, "admin", "adminpw1")
    for path in ("/api/estado", "/api/internet", "/api/syslog", "/api/watch", "/api/capture", "/api/guest",
                 "/api/digest", "/api/system"):
        c, _, b = req(lab["nw"], "GET", path, headers=admin)
        assert c == 200, (path, b[:200])
    c, _, b = req(lab["nw"], "GET", "/estado")
    assert c == 200 and b"Todo bien en casa" in b
    c, _, b = req(lab["nw"], "POST", "/api/guest", json.dumps({"ssid": "Invitados", "password": "visita123"}), admin)
    assert c == 200
    g = json.loads(req(lab["nw"], "GET", "/api/guest", headers=admin)[2])
    assert g["ssid"] == "Invitados" and "<svg" in g["svg"] or g["svg"] == ""      # qrcode puede faltar aquí
    c, _, b = req(lab["nw"], "POST", "/api/capture", json.dumps({"ip": "1.2.3.4; rm -rf /", "iface": "eth0"}), admin)
    assert c == 400
    from netwatch import auth
    assert not auth.allowed("registros", "GET", "/api/capture")
