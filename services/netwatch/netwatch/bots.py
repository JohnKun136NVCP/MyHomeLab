"""Tus otros bots de Telegram, administrados desde el panel (Bots).

Cada bot se registra con `./hl bots add NOMBRE CARPETA`: su carpeta queda montada en
/homelab/bots/NOMBRE y su servicio de systemd lo maneja homelab-apply.sh (el panel no toca
systemd). Tipos:

* ``yt-telegrambot`` (github.com/JohnKun136NVCP/yt-telegrambot): usuarios, premium, cuota,
  pagos con Stars y reembolsos, usando el propio ``database/databases.py`` del bot para no
  duplicar sus reglas.
* ``generic``: estado, reinicio, logs y su @usuario.

Con el TOKEN de su .env solo se llaman métodos que no le quitan nada al bot: nunca
``getUpdates`` (eso le robaría los mensajes) ni webhooks.
"""
from __future__ import annotations

import contextlib
import importlib.util
import json
import logging
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

log = logging.getLogger("netwatch.bots")

BOTS_DIR = Path(os.environ.get("HOMELAB_BOTS", "/homelab/bots"))
CONF = Path(os.environ.get("HOMELAB_CONFIG", "/homelab/config/homelab.yml")).with_name("bots.yml")
RUN = Path(os.environ.get("HOMELAB_RUN", "/homelab/run")) / "bots"
SAFE_METHODS = {"getMe", "getWebhookInfo", "sendMessage", "refundStarPayment", "getChat"}
GRANT_TYPES = ("premium", "subscribed", "admin")


def read_env(path: Path) -> dict:
    out = {}
    with contextlib.suppress(OSError):
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                v = v[1:-1]
            out[k.strip().removeprefix("export ").strip()] = v
    return out


class BotError(RuntimeError):
    pass


class Bot:
    def __init__(self, cfg: dict) -> None:
        self.name = str(cfg["name"])
        self.kind = str(cfg.get("kind") or "generic")
        self.unit = str(cfg.get("unit") or "")
        self.host_path = str(cfg.get("path") or "")
        self.dir = BOTS_DIR / self.name
        self._me: tuple[float, dict] | None = None
        self._mod = None

    # ------------------------------------------------------------ Telegram
    @property
    def env(self) -> dict:
        return read_env(self.dir / ".env")

    def api(self, method: str, **params) -> dict:
        if method not in SAFE_METHODS:
            raise BotError(f"Método no permitido: {method}")
        env = self.env
        token = env.get("TOKEN") or env.get("BOT_TOKEN") or env.get("TELEGRAM_TOKEN")
        if not token:
            raise BotError("No encontré el TOKEN en el .env del bot")
        base = (env.get("BOT_API_BASE_URL") or "https://api.telegram.org").rstrip("/")
        data = urllib.parse.urlencode({k: (json.dumps(v) if isinstance(v, (dict, list)) else v)
                                       for k, v in params.items()}).encode()
        req = urllib.request.Request(f"{base}/bot{token}/{method}", data=data)
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                res = json.load(r)
        except urllib.error.HTTPError as e:
            try:
                res = json.load(e)
            except ValueError:
                raise BotError(f"Telegram respondió {e.code}") from None
        except (OSError, ValueError) as e:
            raise BotError(f"No pude hablar con Telegram ({base.split('//')[-1]}): {e}") from None
        if not res.get("ok"):
            raise BotError(res.get("description") or "Telegram dijo que no")
        return res["result"]

    def me(self) -> dict:
        if self._me and time.time() - self._me[0] < 600:
            return self._me[1]
        try:
            info = self.api("getMe")
            info["pending"] = self.api("getWebhookInfo").get("pending_update_count", 0)
        except BotError as e:
            info = {"error": str(e)}
        self._me = (time.time(), info)
        return info

    # ------------------------------------------------------- yt-telegrambot
    @property
    def users_db(self) -> Path:
        return self.dir / "database" / "users.db"

    def _module(self):
        """El database/databases.py del propio bot (solo usa sqlite3, sin efectos al importar)."""
        src = self.dir / "database" / "databases.py"
        if not src.exists():
            raise BotError("Este bot no tiene database/databases.py")
        mtime = src.stat().st_mtime
        if self._mod and self._mod[0] == mtime:
            return self._mod[1]
        spec = importlib.util.spec_from_file_location(f"hlbot_{self.name}_databases", src)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self._mod = (mtime, mod)
        return mod

    @contextlib.contextmanager
    def userdb(self):
        if not self.users_db.exists():
            raise BotError("Todavía no hay database/users.db (¿el bot ya arrancó alguna vez?)")
        db = self._module().usrdatabase(str(self.users_db))
        try:
            yield db
        finally:
            db.close()
            self._fix_owner()

    def _fix_owner(self) -> None:
        """El panel corre como root: que el WAL/SHM de SQLite sigan siendo del usuario del bot."""
        try:
            st = self.users_db.stat()
        except OSError:
            return
        for p in (self.users_db, Path(f"{self.users_db}-wal"), Path(f"{self.users_db}-shm")):
            with contextlib.suppress(OSError):
                if p.exists() and (p.stat().st_uid, p.stat().st_gid) != (st.st_uid, st.st_gid):
                    os.chown(p, st.st_uid, st.st_gid)

    def _ro(self, path: Path) -> sqlite3.Connection:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
        c.row_factory = sqlite3.Row
        return c

    def users(self, q: str = "", limit: int = 500) -> list[dict]:
        admins = {s.strip() for s in self.env.get("ADMIN_IDS", "").split(",") if s.strip()}
        with self.userdb() as db:          # crea columnas nuevas si el bot se actualizó
            pass
        with contextlib.closing(self._ro(self.users_db)) as c:
            sql = ("SELECT u.telegram_id, u.username, u.songs_by_day, u.premium, u.type_user, u.last_request_time,"
                   " u.premium_until, (SELECT COUNT(*) FROM payments p WHERE p.telegram_id=u.telegram_id) AS payments,"
                   " (SELECT COALESCE(SUM(stars),0) FROM payments p WHERE p.telegram_id=u.telegram_id AND refunded=0)"
                   " AS stars FROM users u")
            args: tuple = ()
            if q:
                sql += " WHERE CAST(u.telegram_id AS TEXT) LIKE ? OR u.username LIKE ?"
                args = (f"%{q}%", f"%{q.lstrip('@')}%")
            sql += " ORDER BY COALESCE(u.last_request_time, '') DESC LIMIT ?"
            rows = [dict(r) for r in c.execute(sql, (*args, limit))]
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        for r in rows:
            t = (r["type_user"] or "unsubscribed").lower()
            exp = r["premium_until"]
            r["active"] = t == "admin" or ((bool(r["premium"]) or t in ("premium", "subscribed"))
                                          and not (t in ("premium", "subscribed") and exp and exp <= now))
            r["operator"] = str(r["telegram_id"]) in admins     # ADMIN_IDS del .env (no se le quita en el panel)
        return rows

    def change_user(self, uid: int, op: str, type_: str = "premium", days: int | None = None) -> str:
        if op == "grant":
            if type_ not in GRANT_TYPES:
                raise BotError("Tipo inválido")
            with self.userdb() as db:
                if not db.set_premium(uid, type_user=type_, days=None if type_ == "admin" else (days or None)):
                    raise BotError("Ese usuario no existe en el bot (tiene que haberle escrito antes)")
                u = db.get_user(uid)
            return f"{type_} hasta {u['premium_until'][:16].replace('T', ' ')}" if u.get("premium_until") else f"{type_} sin vencimiento"
        if op in ("revoke", "reset", "delete") and str(uid) in self.env.get("ADMIN_IDS", "").split(",") and op != "reset":
            raise BotError("Es administrador en ADMIN_IDS del .env del bot: quítalo de ahí primero")
        with self.userdb() as db:
            if op == "revoke":
                ok = db.revoke_premium(uid)
            elif op == "reset":
                db.cursor.execute("UPDATE users SET songs_by_day = 0 WHERE telegram_id = ?", (uid,))
                ok = db.cursor.rowcount > 0
                db.connect.commit()
            elif op == "delete":
                db.cursor.execute("DELETE FROM users WHERE telegram_id = ?", (uid,))
                ok = db.cursor.rowcount > 0
                db.connect.commit()
            else:
                raise BotError("Operación desconocida")
        if not ok:
            raise BotError("Ese usuario no existe en el bot")
        return {"revoke": "regresó a gratis", "reset": "cuota de hoy en 0", "delete": "borrado"}[op]

    def payments(self, uid: int | None = None) -> list[dict]:
        with contextlib.closing(self._ro(self.users_db)) as c:
            sql = ("SELECT p.*, u.username FROM payments p LEFT JOIN users u ON u.telegram_id=p.telegram_id"
                   + (" WHERE p.telegram_id=?" if uid else "") + " ORDER BY p.id DESC LIMIT 300")
            return [dict(r) for r in c.execute(sql, (uid,) if uid else ())]

    def refund(self, charge_id: str) -> str:
        """Igual que el /refund del bot: Telegram regresa las Stars, luego se marca y se quita el premium."""
        with self.userdb() as db:
            p = db.get_payment(charge_id)
        if not p:
            raise BotError("No existe ese pago")
        if p["refunded"]:
            raise BotError("Ese pago ya se había reembolsado")
        self.api("refundStarPayment", user_id=p["telegram_id"], telegram_payment_charge_id=charge_id)
        with self.userdb() as db:
            db.mark_refunded(charge_id)
            db.revoke_premium(p["telegram_id"])
        return f"Reembolsadas {p['stars']} ⭐ a {p['telegram_id']}"

    def stats(self) -> dict:
        out: dict = {}
        try:
            with self.userdb() as db:
                out = db.stats()
        except BotError as e:
            return {"error": str(e)}
        except Exception as e:  # noqa: BLE001 - una versión distinta del bot
            return {"error": f"No pude leer la base del bot: {e}"}
        with contextlib.suppress(sqlite3.Error):
            with contextlib.closing(self._ro(self.users_db)) as c:
                out["by_type"] = dict(c.execute("SELECT type_user, COUNT(*) FROM users GROUP BY type_user").fetchall())
                day = time.strftime("%Y-%m-%d")
                out["active_today"] = c.execute("SELECT COUNT(*) FROM users WHERE last_request_time >= ?",
                                                (day,)).fetchone()[0]
        songs = self.dir / "database" / "idSongs.db"
        if songs.exists():
            with contextlib.suppress(sqlite3.Error), contextlib.closing(self._ro(songs)) as c:
                out["songs"] = c.execute("SELECT COUNT(*) FROM songs").fetchone()[0]
        queue = self.dir / "database" / "queue.db"
        if queue.exists():
            with contextlib.suppress(sqlite3.Error), contextlib.closing(self._ro(queue)) as c:
                out["queue"] = dict(c.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall())
        folder = self.dir / "Songs"
        if folder.is_dir():
            out["songs_mb"] = round(sum(f.stat().st_size for f in folder.iterdir() if f.is_file()) / 2**20)
        return out

    # ------------------------------------------------ canciones (caché del bot)
    AUDIO = (".flac", ".m4a", ".mp3")

    def _song_files(self) -> dict[str, list[Path]]:
        """Archivos de Songs/ agrupados por nombre (sin extensión), igual que los busca el bot."""
        out: dict[str, list[Path]] = {}
        folder = self.dir / "Songs"
        if folder.is_dir():
            for f in folder.rglob("*"):
                if f.is_file() and f.suffix.lower() in self.AUDIO:
                    out.setdefault(f.stem.strip().lower(), []).append(f)
        return out

    def _files_for(self, song: dict, index: dict[str, list[Path]]) -> list[Path]:
        files = list(index.get(str(song["uri"]).lower(), []))
        if not files and song.get("name"):                      # versiones viejas: por título
            files = list(index.get(str(song["name"]).strip().lower(), []))
        return files

    def _busy_uris(self) -> set[str]:
        """Videos que la cola está bajando o mandando ahora mismo: esos no se borran."""
        q = self.dir / "database" / "queue.db"
        if not q.exists():
            return set()
        with contextlib.suppress(sqlite3.Error), contextlib.closing(self._ro(q)) as c:
            return {r[0] for r in c.execute("SELECT payload FROM jobs")}
        return set()

    def songs(self, q: str = "", sort: str = "recent", limit: int = 300, offset: int = 0) -> dict:
        db = self.dir / "database" / "idSongs.db"
        if not db.exists():
            raise BotError("Todavía no hay database/idSongs.db")
        index = self._song_files()
        order = {"recent": "id DESC", "name": "name COLLATE NOCASE", "channel": "channel COLLATE NOCASE, name",
                 "duration": "duration DESC"}.get(sort, "id DESC")
        where, args = "", ()
        if q:
            where, args = " WHERE name LIKE ? OR channel LIKE ? OR uri = ?", (f"%{q}%", f"%{q}%", q)
        with contextlib.closing(self._ro(db)) as c:
            total = c.execute("SELECT COUNT(*) FROM songs" + where, args).fetchone()[0]
            rows = [dict(r) for r in c.execute(f"SELECT id, name, channel, uri, duration FROM songs{where} "
                                              f"ORDER BY {order} LIMIT ? OFFSET ?", (*args, limit, offset))]
            known = {str(r[0]).lower() for r in c.execute("SELECT uri FROM songs")} | \
                    {str(r[0]).strip().lower() for r in c.execute("SELECT name FROM songs WHERE name IS NOT NULL")}
        busy = " ".join(self._busy_uris())
        for r in rows:
            files = self._files_for(r, index)
            r["files"] = [{"name": f.name, "mb": round(f.stat().st_size / 2**20, 1)} for f in files]
            r["mb"] = round(sum(x["mb"] for x in r["files"]), 1)
            r["busy"] = bool(r["uri"]) and r["uri"] in busy
        orphans = [f for stem, fs in index.items() if stem not in known for f in fs]
        return {"songs": rows, "total": total,
                "orphans": [{"name": str(f.relative_to(self.dir / "Songs")), "mb": round(f.stat().st_size / 2**20, 1)}
                            for f in sorted(orphans)[:200]],
                "orphans_mb": round(sum(f.stat().st_size for f in orphans) / 2**20, 1)}

    def delete_songs(self, ids: list[int], orphans: list[str] | None = None) -> dict:
        """Borra canciones de idSongs.db y sus archivos (audio en Songs/ y miniatura en thumbimg/).

        La próxima vez que alguien la pida, el bot la vuelve a bajar como si fuera nueva.
        """
        db = self.dir / "database" / "idSongs.db"
        songs_dir, thumbs = (self.dir / "Songs").resolve(), self.dir / "thumbimg"
        index = self._song_files()
        busy = " ".join(self._busy_uris())
        removed, freed, skipped = 0, 0, []
        ids = [int(i) for i in ids][:1000]
        if ids:
            c = sqlite3.connect(str(db), timeout=30)
            try:
                c.row_factory = sqlite3.Row
                rows = [dict(r) for r in c.execute(
                    f"SELECT id, name, uri FROM songs WHERE id IN ({','.join('?' * len(ids))})", ids)]
                for r in rows:
                    if r["uri"] and r["uri"] in busy:
                        skipped.append(r["name"] or r["uri"])
                        continue
                    for f in self._files_for(r, index):
                        with contextlib.suppress(OSError):
                            freed += f.stat().st_size
                            f.unlink()
                    with contextlib.suppress(OSError):
                        (thumbs / f"{r['uri']}_tg.jpg").unlink()
                    c.execute("DELETE FROM songs WHERE id = ?", (r["id"],))
                    removed += 1
                c.commit()
            finally:
                c.close()
                self._fix_owner_of(db)
        for name in orphans or []:
            f = (songs_dir / name).resolve()
            if f.parent != songs_dir and songs_dir not in f.parents:
                continue                                         # nada fuera de Songs/
            if f.is_file() and f.suffix.lower() in self.AUDIO:
                freed += f.stat().st_size
                f.unlink()
                removed += 1
        return {"removed": removed, "freed_mb": round(freed / 2**20, 1), "skipped": skipped}

    def _fix_owner_of(self, db: Path) -> None:
        try:
            st = db.stat()
        except OSError:
            return
        for p in (db, Path(f"{db}-wal"), Path(f"{db}-shm")):
            with contextlib.suppress(OSError):
                if p.exists() and (p.stat().st_uid, p.stat().st_gid) != (st.st_uid, st.st_gid):
                    os.chown(p, st.st_uid, st.st_gid)

    def recipients(self) -> list[int]:
        with contextlib.closing(self._ro(self.users_db)) as c:
            return [r[0] for r in c.execute("SELECT telegram_id FROM users")]

    def summary(self, status: dict) -> dict:
        st = status.get(self.name) or {}
        d = {"name": self.name, "kind": self.kind, "unit": self.unit, "path": self.host_path,
             "mounted": self.dir.is_dir() and any(self.dir.iterdir()), "service": st, "me": {}}
        if not d["mounted"]:
            d["error"] = "El panel no ve la carpeta: corre  sudo ./hl bots add " + self.name + " " + (self.host_path or "CARPETA")
            return d
        d["me"] = self.me()
        if self.kind == "yt-telegrambot":
            d["stats"] = self.stats()
        return d


class Bots:
    def __init__(self, db=None) -> None:
        self.db = db
        self._bots: dict[str, Bot] = {}
        self._mtime = -1.0
        self.broadcasts: dict[str, dict] = {}
        self._pending_since: dict[str, float] = {}

    def all(self) -> dict[str, Bot]:
        try:
            m = CONF.stat().st_mtime
        except OSError:
            self._bots, self._mtime = {}, -1.0
            return self._bots
        if m != self._mtime:
            try:
                cfg = (yaml.safe_load(CONF.read_text()) or {}).get("bots") or []
            except (OSError, yaml.YAMLError) as e:
                log.warning("bots.yml: %s", e)
                cfg = []
            old = self._bots
            self._bots = {str(b["name"]): old.get(str(b["name"])) or Bot(b) for b in cfg if b.get("name")}
            for b in cfg:                       # si cambió tipo o servicio
                if b.get("name") in self._bots:
                    bot = self._bots[b["name"]]
                    bot.kind, bot.unit, bot.host_path = b.get("kind") or "generic", b.get("unit") or "", b.get("path") or ""
            self._mtime = m
        return self._bots

    def get(self, name: str) -> Bot:
        b = self.all().get(name)
        if not b:
            raise BotError("No existe ese bot")
        return b

    @staticmethod
    def status() -> dict:
        try:
            s = json.loads((RUN / "status.json").read_text())
        except (OSError, ValueError):
            return {"ts": 0, "bots": {}}
        return s

    def logs(self, name: str) -> dict:
        f = RUN / f"{name}.log"
        try:
            return {"ts": f.stat().st_mtime, "text": f.read_text(errors="replace")[-200_000:]}
        except OSError:
            return {"ts": 0, "text": ""}

    def broadcast(self, name: str, text: str, to: list[int] | None = None) -> dict:
        bot = self.get(name)
        if self.broadcasts.get(name, {}).get("running"):
            raise BotError("Ya hay un envío en curso")
        ids = to or bot.recipients()
        job = {"running": True, "total": len(ids), "sent": 0, "failed": 0, "started": time.time()}
        self.broadcasts[name] = job

        def run() -> None:
            for uid in ids:
                try:
                    bot.api("sendMessage", chat_id=uid, text=text, disable_web_page_preview="true")
                    job["sent"] += 1
                except BotError as e:
                    job["failed"] += 1
                    if "retry after" in str(e).lower():
                        time.sleep(5)
                time.sleep(0.05)               # Telegram: ~30 mensajes por segundo
            job["running"] = False
            job["ended"] = time.time()
            if self.db and len(ids) > 1:
                self.db.add_event("info", "bots", f"Bot «{name}»: aviso enviado a {job['sent']} de {job['total']}")
        threading.Thread(target=run, name=f"bot-{name}-envio", daemon=True).start()
        return job

    def watch(self) -> None:
        """Si el bot deja de leer sus mensajes (se juntan sin responder), seguro está caído."""
        for name, bot in self.all().items():
            if not bot.dir.is_dir():
                continue
            bot._me = None
            info = bot.me()
            pending = info.get("pending", 0) if "error" not in info else 0
            if pending >= 3:
                since = self._pending_since.setdefault(name, time.time())
                if time.time() - since > 900 and since > 0:
                    if self.db:
                        self.db.add_event("warning", "bot_down", f"🤖 Bot «{name}» no está contestando",
                                          f"{pending} mensajes esperando desde hace 15 min. "
                                          f"Revisa:  sudo ./hl bots logs {name}")
                    self._pending_since[name] = -1e18      # una vez hasta que se recupere
            else:
                self._pending_since.pop(name, None)
