"""Panel → Bots: administrar otro bot (yt-telegrambot) con su propia base de datos.

    python -m pytest tests/test_bots.py -q
"""
import json
import shutil
import sqlite3
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "services" / "common"), str(ROOT / "services" / "netwatch")]

from netwatch import bots as B  # noqa: E402
from netwatch.db import DB  # noqa: E402

CALLS: list[tuple[str, dict]] = []


class FakeTG(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        params = dict(urllib.parse.parse_qsl(self.rfile.read(n).decode()))
        method = self.path.rsplit("/", 1)[-1]
        CALLS.append((method, params))
        res = {"getMe": {"id": 1, "is_bot": True, "username": "kawaii_yt_bot"},
               "getWebhookInfo": {"url": "", "pending_update_count": 0},
               "sendMessage": {"message_id": 1}, "refundStarPayment": True}.get(method)
        body = json.dumps({"ok": True, "result": res} if res is not None else {"ok": False, "description": "nope"}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture()
def ytbot(tmp_path, monkeypatch):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeTG)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    bots_dir, cfg, run = tmp_path / "bots", tmp_path / "config", tmp_path / "run" / "bots"
    d = bots_dir / "yt"
    (d / "database").mkdir(parents=True)
    cfg.mkdir()
    run.mkdir(parents=True)
    shutil.copy(ROOT / "tests" / "fixtures" / "ytbot" / "databases.py", d / "database" / "databases.py")
    (d / ".env").write_text(f"TOKEN=123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij\nADMIN_IDS=111\n"
                            f"BOT_API_BASE_URL=http://127.0.0.1:{srv.server_port}\n")
    # la base como la deja el bot de verdad
    sys.path.insert(0, str(d))
    import importlib.util
    spec = importlib.util.spec_from_file_location("ytdb", d / "database" / "databases.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    u = mod.usrdatabase(str(d / "database" / "users.db"))
    for uid, name in ((111, "yo"), (222, "ana"), (333, "beto")):
        u.add_user(uid, name)
    u.ensure_admin(111)
    u.record_payment(222, "ch_1", "month", 100, 30)
    u.set_premium(222, "premium", 30)
    u.close()
    (cfg / "bots.yml").write_text("bots:\n- {name: yt, kind: yt-telegrambot, unit: yt-telegrambot.service, path: /home/john/yt}\n")
    (run / "status.json").write_text(json.dumps({"ts": time.time(), "bots": {"yt": {"state": "active", "since": 1}}}))
    monkeypatch.setattr(B, "BOTS_DIR", bots_dir)
    monkeypatch.setattr(B, "CONF", cfg / "bots.yml")
    monkeypatch.setattr(B, "RUN", run)
    CALLS.clear()
    yield B.Bots(DB(tmp_path / "n.db")), d
    srv.shutdown()


def test_summary_and_users(ytbot):
    bots, _ = ytbot
    s = bots.get("yt").summary(bots.status()["bots"])
    assert s["me"]["username"] == "kawaii_yt_bot" and s["service"]["state"] == "active"
    assert s["stats"]["total_users"] == 3 and s["stats"]["stars_total"] == 100
    users = {u["telegram_id"]: u for u in bots.get("yt").users()}
    assert users[111]["operator"] and users[111]["type_user"] == "admin"
    assert users[222]["active"] and users[222]["payments"] == 1
    assert [u["telegram_id"] for u in bots.get("yt").users("ana")] == [222]
    assert not any(m == "getUpdates" for m, _ in CALLS)        # nunca le roba los mensajes


def test_change_users(ytbot):
    bots, d = ytbot
    bot = bots.get("yt")
    assert "premium hasta" in bot.change_user(333, "grant", "premium", 7)
    assert bot.change_user(333, "revoke") == "regresó a gratis"
    with pytest.raises(B.BotError):
        bot.change_user(111, "revoke")                         # está en ADMIN_IDS
    with pytest.raises(B.BotError):
        bot.change_user(999, "grant", "premium", 7)            # no existe
    bot.change_user(333, "delete")
    with sqlite3.connect(d / "database" / "users.db") as c:
        assert c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 2


def test_refund_and_messages(ytbot):
    bots, d = ytbot
    bot = bots.get("yt")
    assert "100" in bot.refund("ch_1")
    assert ("refundStarPayment", {"user_id": "222", "telegram_payment_charge_id": "ch_1"}) in CALLS
    with pytest.raises(B.BotError):
        bot.refund("ch_1")                                     # ya reembolsado
    u = {x["telegram_id"]: x for x in bot.users()}
    assert u[222]["type_user"] == "unsubscribed"
    with pytest.raises(B.BotError):
        bot.api("getUpdates")
    job = bots.broadcast("yt", "Mantenimiento hoy")
    for _ in range(100):
        if not job["running"]:
            break
        time.sleep(0.05)
    assert job["sent"] == 3 and len([c for c in CALLS if c[0] == "sendMessage"]) == 3


def test_missing_mount(ytbot, tmp_path):
    bots, d = ytbot
    shutil.rmtree(d)
    s = bots.get("yt").summary({})
    assert "./hl bots add yt /home/john/yt" in s["error"]


def test_songs_list_and_delete(ytbot):
    bots, d = ytbot
    import importlib.util
    spec = importlib.util.spec_from_file_location("ytdb2", d / "database" / "databases.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    yt = mod.ytdatabase(str(d / "database" / "idSongs.db"))
    yt.insertData("Lemon", "Kenshi Yonezu", "SX_ViT4Ra7k", 255, "")
    yt.insertData("Plastic Love", "Mariya Takeuchi", "3bNITQR4Uso", 480, "")
    yt.insertData("Vieja", "Alguien", "OLDvid00001", 100, "")
    yt.close()
    songs, thumbs = d / "Songs", d / "thumbimg"
    songs.mkdir()
    thumbs.mkdir()
    (songs / "SX_ViT4Ra7k.flac").write_bytes(b"x" * 2**20)
    (songs / "SX_ViT4Ra7k.mp3").write_bytes(b"x" * 1000)
    (thumbs / "SX_ViT4Ra7k_tg.jpg").write_bytes(b"j")
    (songs / "Vieja.mp3").write_bytes(b"x")                    # nombre de versiones viejas (por título)
    (songs / "3bNITQR4Uso.m4a").write_bytes(b"x")
    (songs / "huerfana.mp3").write_bytes(b"x" * 10)
    with sqlite3.connect(d / "database" / "queue.db") as c:
        c.execute("CREATE TABLE jobs (job_id TEXT PRIMARY KEY, chat_id INTEGER, user_id INTEGER, payload TEXT, status TEXT, created_at TEXT)")
        c.execute("INSERT INTO jobs VALUES ('j1', 1, 1, '{\"url\": \"https://youtu.be/3bNITQR4Uso\"}', 'processing', '')")
    bot = bots.get("yt")
    r = bot.songs()
    by = {s["uri"]: s for s in r["songs"]}
    assert r["total"] == 3 and len(by["SX_ViT4Ra7k"]["files"]) == 2 and by["OLDvid00001"]["files"][0]["name"] == "Vieja.mp3"
    assert by["3bNITQR4Uso"]["busy"] and [o["name"] for o in r["orphans"]] == ["huerfana.mp3"]
    assert [s["name"] for s in bot.songs("kenshi")["songs"]] == ["Lemon"]
    out = bot.delete_songs([by["SX_ViT4Ra7k"]["id"], by["3bNITQR4Uso"]["id"], by["OLDvid00001"]["id"]], ["huerfana.mp3", "../.env"])
    assert out["removed"] == 3 and out["skipped"] == ["Plastic Love"]       # 2 canciones + 1 huérfana; la que se envía no
    assert not (songs / "SX_ViT4Ra7k.flac").exists() and not (thumbs / "SX_ViT4Ra7k_tg.jpg").exists()
    assert not (songs / "Vieja.mp3").exists() and (songs / "3bNITQR4Uso.m4a").exists()
    assert (d / ".env").exists()                                         # nada fuera de Songs/
    assert [s["uri"] for s in bot.songs()["songs"]] == ["3bNITQR4Uso"]
