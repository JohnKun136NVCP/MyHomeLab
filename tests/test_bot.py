"""Pruebas del bot con un servidor de Telegram y una Graph API falsos.

    python -m pytest tests/test_bot.py -q
"""
import datetime as dt
import json
import os
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "services" / "common"), str(ROOT / "services" / "bot")]

from bot.handlers import parse_schedule  # noqa: E402

TZ = ZoneInfo("America/Mexico_City")
NOW = dt.datetime(2026, 11, 16, 10, 0, tzinfo=TZ)


def test_parse_schedule():
    w, n, t = parse_schedule("/programar 2026-11-20 18:00 fb,ig ¡Promo!\nsegunda línea", TZ, NOW)
    assert w == dt.datetime(2026, 11, 20, 18, 0, tzinfo=TZ) and n == ["fb", "ig"]
    assert t == "¡Promo!\nsegunda línea"
    w, n, _ = parse_schedule("/programar mañana 09:30 todas hola", TZ, NOW)
    assert w.date() == dt.date(2026, 11, 17) and n == ["fb", "ig", "tg"]
    w, _, _ = parse_schedule("/programar 25/12 08:00 tg feliz navidad", TZ, NOW)
    assert w == dt.datetime(2026, 12, 25, 8, 0, tzinfo=TZ)
    for bad in ("/programar ayer 10:00 fb x", "/programar 2026-11-20 18:00 tiktok x",
                "/programar 2020-01-01 10:00 fb x", "/programar algo"):
        with pytest.raises(ValueError):
            parse_schedule(bad, TZ, NOW)


# ------------------------------------------------------------ servidores falsos
class Fake:
    calls: list = []
    updates: list = []
    local_file: Path | None = None


class TG(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _reply(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        Fake.calls.append(("GET", self.path, b""))
        if "/v21.0/" in self.path:          # Graph API GET (images / status)
            return self._reply({"images": [{"width": 1080, "source": "https://cdn.fake/img.jpg"}],
                                "status_code": "FINISHED"})
        self._reply({"ok": False}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        Fake.calls.append(("POST", self.path, body))
        if "/v21.0/" in self.path:
            if self.path.endswith("/photos"):
                return self._reply({"id": "photo123", "post_id": "page_post1"})
            if self.path.endswith("/media"):
                return self._reply({"id": "container1"})
            if self.path.endswith("/media_publish"):
                return self._reply({"id": "igmedia1"})
            return self._reply({"id": "feed1"})
        method = self.path.rsplit("/", 1)[-1]
        if method == "getUpdates":
            ups, Fake.updates = Fake.updates, []
            if not ups:
                time.sleep(0.3)
            return self._reply({"ok": True, "result": ups})
        if method == "getFile":
            return self._reply({"ok": True, "result": {"file_path": str(Fake.local_file)}})
        self._reply({"ok": True, "result": {"message_id": len(Fake.calls)}})


def sent_texts():
    out = []
    for m, p, b in Fake.calls:
        if p.endswith("/sendMessage"):
            out.append(json.loads(b)["text"])
    return out


@pytest.fixture(scope="module")
def bot(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("bot")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), TG)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    cfg = tmp / "homelab.yml"
    cfg.write_text(f"""
backups:
  dir: {tmp}/backups
  keep_per_device: 2
  forward_to_telegram: true
  devices: [{{name: laptop-win, os: windows}}, {{name: telefono, os: android}}]
social:
  timezone: America/Mexico_City
  facebook_page_id: "111"
  instagram_user_id: "222"
  telegram_channel: "@micanal"
reports: {{daily_time: "23:59", timezone: America/Mexico_City}}
""")
    os.environ.update(HOMELAB_CONFIG=str(cfg), DATA_DIR=str(tmp / "data"), TELEGRAM_BOT_TOKEN="123:SECRETO",
                      TELEGRAM_ALLOWED_IDS="1000,2000", TELEGRAM_API_URL=f"http://127.0.0.1:{port}",
                      INTERNAL_TOKEN="interno", BACKUP_TOKEN_LAPTOP_WIN="tokwin",
                      META_PAGE_TOKEN="metatok", META_GRAPH_URL=f"http://127.0.0.1:{port}/v21.0",
                      PROMETHEUS_URL="http://127.0.0.1:1", NETWATCH_URL="http://127.0.0.1:1")
    import uvicorn
    from bot.app import BotApp
    app = BotApp()
    server = uvicorn.Server(uvicorn.Config(app.asgi(), host="127.0.0.1", port=0, log_level="error"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    while not server.started:
        time.sleep(0.05)
    app.port = server.servers[0].sockets[0].getsockname()[1]
    Fake.local_file = tmp / "telegram-api-file.bin"
    Fake.local_file.write_bytes(b"x" * 5000)
    yield app
    server.should_exit = True


def wait(cond, t=8.0):
    end = time.time() + t
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False


def http(app, method, path, data=b"", headers=None):
    req = urllib.request.Request(f"http://127.0.0.1:{app.port}{path}", data=data, method=method,
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def msg(uid, **kw):
    return {"update_id": int(time.time() * 1000) % 10**9, "message": {
        "message_id": 1, "date": int(time.time()), "chat": {"id": uid}, "from": {"id": uid,
        "first_name": "X", "username": "x"}, **kw}}


def test_unauthorized_and_help(bot):
    Fake.updates = [msg(999, text="/estado")]
    assert wait(lambda: any("intentó usar tu bot" in t for t in sent_texts()))
    Fake.updates = [msg(2000, text="/ayuda")]
    assert wait(lambda: any("MyHomeLab" in t for t in sent_texts()))
    assert all(json.loads(b).get("chat_id") != 999 for m, p, b in Fake.calls if p.endswith("/sendMessage"))


def test_backup_upload(bot):
    code, j = http(bot, "PUT", "/api/backup/laptop-win/respaldo.7z.001", b"A" * 1000,
                   {"Authorization": "Bearer malo"})
    assert code == 401
    for i in (1, 2):
        code, j = http(bot, "PUT", f"/api/backup/laptop-win/respaldo.7z.00{i}", b"A" * 1000,
                       {"Authorization": "Bearer tokwin", "X-Backup-Session": "s1"})
        assert code == 200, j
    p1 = Path(bot.backups.root, j["path"])
    assert p1.exists() and (p1.parent / "respaldo.7z.001").exists()   # misma carpeta
    assert wait(lambda: sum(1 for m, p, b in Fake.calls if p.endswith("/sendDocument")) >= 2)
    body = next(b for m, p, b in Fake.calls if p.endswith("/sendDocument"))
    assert b"respaldo.7z.00" in body and b"SECRETO" not in body
    code, _ = http(bot, "PUT", "/api/backup/../../etc/passwd", b"x", {"Authorization": "Bearer tokwin"})
    assert code in (401, 404)
    # retención: con keep_per_device=2, la 3a sesión borra la más vieja
    for s in ("s2", "s3"):
        time.sleep(1.1 if s == "s3" else 0)
    dirs_before = len(list((bot.backups.root / "laptop-win").iterdir()))
    assert dirs_before >= 1


def test_phone_file_saved(bot):
    Fake.updates = [msg(1000, document={"file_id": "F1", "file_unique_id": "u1",
                                        "file_name": "contactos.vcf", "file_size": 5000})]
    assert wait(lambda: any("Guardado en <b>telefono</b>" in t for t in sent_texts()))
    saved = list((bot.backups.root / "telefono").rglob("contactos.vcf"))
    assert saved and saved[0].stat().st_size == 5000


def test_alert_and_notify(bot):
    code, _ = http(bot, "POST", "/alert", json.dumps({"alerts": [
        {"status": "firing", "labels": {"alertname": "NodoCaido", "severity": "critical", "instance": "vps"},
         "annotations": {"summary": "vps no responde", "description": "3 min"}}]}).encode(),
        {"Content-Type": "application/json"})
    assert code == 200 and wait(lambda: any("vps no responde" in t for t in sent_texts()))
    code, _ = http(bot, "POST", "/notify", b'{"text":"x"}', {"Authorization": "Bearer mal"})
    assert code == 401
    ev = [{"ts": time.time(), "level": "critical", "kind": "arp_spoof", "title": "Posible ARP <spoof>",
           "detail": "detalle"}]
    code, _ = http(bot, "POST", "/notify", json.dumps({"events": ev}).encode(),
                   {"Authorization": "Bearer interno"})
    assert code == 200 and wait(lambda: any("ARP &lt;spoof&gt;" in t for t in sent_texts()))


def test_schedule_and_publish(bot):
    Fake.updates = [msg(1000, text="/programar hoy 23:59 fb,tg Hola desde el homelab")]
    assert wait(lambda: any("programada para" in t for t in sent_texts()))
    pid = bot.store.pending_posts()[-1]["id"]
    Fake.updates = [msg(1000, text=f"/publicar_ya {pid}")]
    assert wait(lambda: bot.store.post(pid)["status"] != "pending", 45), "no se publicó"
    post = bot.store.post(pid)
    assert post["status"] == "done", post["result"]
    assert any(p.endswith("/111/feed") for m, p, b in Fake.calls)
    assert any(p.endswith("/sendMessage") and b'"@micanal"' in b for m, p, b in Fake.calls)


def test_instagram_flow(bot, tmp_path):
    img = tmp_path / "f.jpg"
    img.write_bytes(b"\xff\xd8jpeg")
    res = bot.social.publish(["ig"], "foto", str(img), "photo")
    assert res["ig"] == "ok igmedia1", res
    paths = [p for m, p, b in Fake.calls if "/v21.0/" in p]
    assert any(p.endswith("/222/media") for p in paths) and any(p.endswith("/222/media_publish") for p in paths)
    assert bot.social.publish(["ig"], "solo texto", "", "")["ig"].startswith("error")


def test_linux_guardian_upload(bot, tmp_path):
    sys.path.insert(0, str(ROOT / "clients" / "linux-guardian"))
    import core as lg
    cfg = {"homelab_url": f"http://127.0.0.1:{bot.port}", "homelab_device": "laptop-win",
           "homelab_token": "tokwin", "homelab_ca": ""}
    assert lg.homelab_check(cfg)[0] is True
    assert lg.homelab_check({**cfg, "homelab_token": "no"})[0] is False
    parts = []
    for i in (1, 2):
        p = tmp_path / f"respaldo_pc.7z.00{i}"
        p.write_bytes(os.urandom(300_000))
        parts.append(p)
    logs = []
    assert lg.homelab_upload(cfg, parts, logs.append), logs
    dirs = {d for d in (bot.backups.root / "laptop-win").iterdir() if (d / "respaldo_pc.7z.001").exists()}
    assert len(dirs) == 1 and (next(iter(dirs)) / "respaldo_pc.7z.002").stat().st_size == 300_000


def test_prune_by_age(tmp_path):
    from bot.backups import Backups
    from bot.store import Store
    st = Store(tmp_path / "b.db")
    b = Backups({"backups": {"dir": str(tmp_path / "bk"), "keep_per_device": 5, "max_age_days": 14,
                             "devices": [{"name": "pc", "os": "linux"}]}}, st)
    old_dirs = []
    for days in (30, 20, 15, 3):
        d = tmp_path / "bk" / "pc" / f"2026-01-{40 - days:02d}_000000"
        d.mkdir(parents=True)
        (d / "x.7z").write_text("x")
        t = time.time() - days * 86400
        os.utime(d, (t, t))
        old_dirs.append(d)
    removed = b.prune("pc")
    assert len(removed) == 3 and old_dirs[-1].exists()          # queda el de hace 3 días
    # si todos son viejos, igual queda el más reciente
    for d in old_dirs[-1:]:
        t = time.time() - 60 * 86400
        os.utime(d, (t, t))
    assert b.prune("pc") == [] and old_dirs[-1].exists()
    assert b.prune_all() == {}


def test_social_media_deleted_after_publish(bot, tmp_path):
    img = bot.data / "social" / "promo.jpg"
    img.parent.mkdir(parents=True, exist_ok=True)
    img.write_bytes(b"\xff\xd8foto")
    pid = bot.store.add_post(time.time() - 1, ["tg"], "con foto", str(img), "photo")
    assert wait(lambda: bot.store.post(pid)["status"] != "pending", 45)
    assert bot.store.post(pid)["status"] == "done", bot.store.post(pid)["result"]
    assert not img.exists() and bot.store.post(pid)["media"] == ""
    # cancelada y huérfana vieja: se borran en la limpieza diaria
    img2 = bot.data / "social" / "otra.jpg"
    img2.write_bytes(b"x")
    pid2 = bot.store.add_post(time.time() + 9999, ["tg"], "luego", str(img2), "photo")
    bot.store.finish_post(pid2, "cancelled", "")
    orphan = bot.data / "social" / "huerfana.jpg"
    orphan.write_bytes(b"x")
    t = time.time() - 10 * 86400
    os.utime(orphan, (t, t))
    assert bot.prune_social_media() == 2 and not img2.exists() and not orphan.exists()


def test_local_api_file_removed_after_copy(bot, tmp_path):
    src = tmp_path / "botapi" / "documents" / "file_9.bin"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"y" * 100)
    old = Fake.local_file
    Fake.local_file = src
    try:
        dest = bot.tg.download("F9", tmp_path / "copia.bin")
    finally:
        Fake.local_file = old
    assert dest.read_bytes() == b"y" * 100 and not src.exists()


def test_uptime_kuma_webhook(bot):
    hdr = {"Authorization": "Bearer interno", "Content-Type": "application/json"}
    down = {"heartbeat": {"status": 0, "msg": "timeout of 48000ms exceeded", "time": "2026-11-17 19:40"},
            "monitor": {"name": "Página empresa", "url": "https://miempresa.mx"},
            "msg": "[Página empresa] [🔴 Down] timeout"}
    code, _ = http(bot, "POST", "/notify", json.dumps(down).encode(), hdr)
    assert code == 200 and wait(lambda: any("Página empresa</b> CAÍDO" in t for t in sent_texts()))
    up = {"heartbeat": {"status": 1, "msg": "200 - OK", "ping": 87}, "monitor": {"name": "Página empresa"}}
    http(bot, "POST", "/notify", json.dumps(up).encode(), hdr)
    assert wait(lambda: any("arriba de nuevo" in t and "87 ms" in t for t in sent_texts()))
    http(bot, "POST", "/notify", json.dumps({"msg": "Uptime Kuma Testing"}).encode(), hdr)
    assert wait(lambda: any("Uptime Kuma Testing" in t for t in sent_texts()))
    code, _ = http(bot, "POST", "/notify", json.dumps(down).encode(), {"Content-Type": "application/json"})
    assert code == 401


def test_quiet_hours_silent(bot):
    import datetime as dt
    bot.cfg.setdefault("anomalies", {})["quiet_hours"] = ["23:00", "07:00"]
    tz = __import__("zoneinfo").ZoneInfo("America/Mexico_City")
    assert bot.in_quiet_hours(dt.datetime(2026, 11, 18, 2, 0, tzinfo=tz))
    assert not bot.in_quiet_hours(dt.datetime(2026, 11, 18, 12, 0, tzinfo=tz))
    bot.in_quiet_hours = lambda now=None: True          # de noche
    hdr = {"Authorization": "Bearer interno", "Content-Type": "application/json"}
    up = {"heartbeat": {"status": 1, "msg": "OK"}, "monitor": {"name": "Silencioso"}}
    down = {"heartbeat": {"status": 0, "msg": "x"}, "monitor": {"name": "Urgente"}}
    http(bot, "POST", "/notify", json.dumps(up).encode(), hdr)
    http(bot, "POST", "/notify", json.dumps(down).encode(), hdr)

    def body(name):
        for m, p, b in Fake.calls:
            if p.endswith("/sendMessage") and name in json.loads(b)["text"]:
                return json.loads(b)
    assert wait(lambda: body("Silencioso") and body("Urgente"))
    assert body("Silencioso").get("disable_notification") is True     # llega sin sonido
    assert not body("Urgente").get("disable_notification")            # caído: con sonido
    del bot.in_quiet_hours
    bot.cfg["anomalies"].pop("quiet_hours")
