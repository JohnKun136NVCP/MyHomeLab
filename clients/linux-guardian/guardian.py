#!/usr/bin/env python3
"""
Linux Guardian — respalda y restaura tu Linux con interfaz gráfica.

  python3 guardian.py               abre la interfaz
  python3 guardian.py --remind      (lo usa el temporizador) avisa si toca respaldo
  python3 guardian.py --telethon-login   inicia sesión de Telegram en terminal
"""
from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
import threading
import datetime as dt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import core  # noqa: E402

if "--remind" in sys.argv:
    core.remind_cli(force="--force" in sys.argv)
    sys.exit(0)
if "--install-reminder" in sys.argv:
    print(core.reminder_install())
    sys.exit(0)
if "--remove-reminder" in sys.argv:
    print(core.reminder_remove())
    sys.exit(0)
if "--telethon-login" in sys.argv:
    core.telethon_login_cli()
    sys.exit(0)

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
except ImportError:
    print("Falta Tkinter. Instálalo con:  sudo apt install python3-tk\n"
          "(Tkinter viene del sistema; el venv lo usa automáticamente.)")
    sys.exit(1)

try:
    from PIL import Image, ImageTk  # opcional: vista previa de JPG/WEBP/etc.
except ImportError:
    Image = ImageTk = None

TEXT_EXT = {".txt", ".md", ".py", ".sh", ".c", ".h", ".cpp", ".js", ".ts", ".json",
            ".yml", ".yaml", ".toml", ".ini", ".conf", ".cfg", ".csv", ".log",
            ".html", ".css", ".xml", ".zsh", ".bash", ".desktop", ".service", ".rs",
            ".go", ".java", ".lua", ".vim", ".tex", ".sql"}
PDF_EXT = {".pdf"}
IMG_EXT = {".png", ".gif", ".jpg", ".jpeg", ".webp", ".bmp", ".ico", ".svg"}


def open_terminal(cmd: list[str]) -> bool:
    """Abre una terminal (kitty primero) ejecutando cmd."""
    import shlex
    inner = " ".join(shlex.quote(c) for c in cmd) + "; echo; read -p 'Enter para cerrar…'"
    for term, args in (("kitty", ["bash", "-c"]),
                       ("gnome-terminal", ["--", "bash", "-c"]),
                       ("konsole", ["-e", "bash", "-c"]),
                       ("xfce4-terminal", ["-x", "bash", "-c"]),
                       ("x-terminal-emulator", ["-e", "bash", "-c"])):
        if shutil.which(term):
            subprocess.Popen([term, *args, inner])
            return True
    return False


class CheckTree(ttk.Frame):
    """Tabla con casilla ☑/☐ por fila. Varias filas pueden compartir la misma clave."""

    def __init__(self, master, columns, widths, on_change=None):
        super().__init__(master)
        cols = ("✔",) + tuple(columns)
        self.tree = ttk.Treeview(self, columns=cols, show="headings", selectmode="extended")
        for c, w in zip(cols, (40,) + tuple(widths)):
            self.tree.heading(c, text=c, command=lambda c=c: self._sort(c))
            self.tree.column(c, width=w, anchor="center" if c == "✔" else "w",
                             stretch=c != "✔")
        sb = ttk.Scrollbar(self, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.tree.tag_configure("off", foreground="#999")
        self.rows: dict[str, dict] = {}     # row_id -> {"key", "values"}
        self.checked: dict[str, bool] = {}  # key -> marcado
        self.on_change = on_change or (lambda: None)
        self._sort_col, self._sort_rev = None, False
        self.tree.bind("<Button-1>", self._click)
        self.tree.bind("<space>", lambda _e: self.toggle_selected())
        self.tree.bind("<Double-1>", lambda _e: self.toggle_selected())

    def set_rows(self, rows, checked: dict[str, bool]):
        """rows: [(row_id, key, values)]"""
        self.rows = {r: {"key": k, "values": v} for r, k, v in rows}
        self.checked = checked

    def show(self, pred=lambda row_id, r: True):
        self.tree.delete(*self.tree.get_children())
        for rid, r in self.rows.items():
            if pred(rid, r):
                on = self.checked.get(r["key"], True)
                self.tree.insert("", "end", iid=rid, values=("☑" if on else "☐",) + tuple(r["values"]),
                                 tags=() if on else ("off",))

    def _paint(self, rid):
        r = self.rows[rid]
        on = self.checked.get(r["key"], True)
        self.tree.set(rid, "✔", "☑" if on else "☐")
        self.tree.item(rid, tags=() if on else ("off",))

    def _repaint_key(self, key):
        for rid in self.tree.get_children():
            if self.rows[rid]["key"] == key:
                self._paint(rid)

    def _click(self, e):
        if self.tree.identify_region(e.x, e.y) == "cell" and self.tree.identify_column(e.x) == "#1":
            rid = self.tree.identify_row(e.y)
            if rid:
                self.set_keys([self.rows[rid]["key"]], None)
                return "break"

    def set_keys(self, keys, value):
        """value None = alternar."""
        for k in set(keys):
            self.checked[k] = (not self.checked.get(k, True)) if value is None else value
            self._repaint_key(k)
        self.on_change()

    def toggle_selected(self):
        self.set_keys([self.rows[r]["key"] for r in self.tree.selection()], None)
        return "break"

    def visible_keys(self):
        return [self.rows[r]["key"] for r in self.tree.get_children()]

    def _sort(self, col):
        self._sort_rev = not self._sort_rev if self._sort_col == col else False
        self._sort_col = col
        items = [(self.tree.set(i, col).lower(), i) for i in self.tree.get_children()]
        for idx, (_, i) in enumerate(sorted(items, reverse=self._sort_rev)):
            self.tree.move(i, "", idx)


class Guardian(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Linux Guardian 🛡  — respaldo y máquina del tiempo")
        self.geometry("1150x760")
        self.minsize(900, 600)
        self.cfg = core.load_config()
        self.q: queue.Queue = queue.Queue()
        self.busy = False

        style = ttk.Style(self)
        for theme in ("clam", "alt", "default"):
            if theme in style.theme_names():
                style.theme_use(theme)
                break
        style.configure("Title.TLabel", font=("TkDefaultFont", 14, "bold"))
        style.configure("Hint.TLabel", foreground="#666")
        style.configure("Big.TButton", font=("TkDefaultFont", 11, "bold"), padding=8)
        style.configure("Treeview", rowheight=22)

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=6, pady=6)
        self._tab_backup()
        self._tab_explorer()
        self._tab_apps()
        self._tab_themes()
        self._tab_system()
        self._tab_passwords()
        self._tab_restore()
        self._tab_telegram()
        self._tab_reminder()

        self.status = ttk.Label(self, text="", anchor="w")
        self.status.pack(fill="x", padx=8, pady=(0, 4))
        self._refresh_status()
        self.after(100, self._poll)
        self.protocol("WM_DELETE_WINDOW", self._close)

    # ------------------------------------------------------------------ util
    def _poll(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self.log.insert("end", payload + "\n")
                    self.log.see("end")
                elif kind == "progress":
                    self.pbar["value"] = payload * 100
                elif kind == "call":
                    payload()
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def ui(self, fn):
        """Ejecuta fn en el hilo de la interfaz."""
        self.q.put(("call", fn))

    def bg(self, fn, *args):
        threading.Thread(target=fn, args=args, daemon=True).start()

    def _refresh_status(self):
        d = core.days_since_backup(self.cfg)
        last = "nunca" if d is None else f"hace {d} días ({self.cfg['last_backup'][:10]})"
        self.status.config(text=f"  {core.distro_name()}   ·   Último respaldo: {last}   ·   "
                                f"Recordatorio: {core.reminder_status()}")

    def _save(self):
        core.save_config(self.cfg)

    def _close(self):
        if self.busy and not messagebox.askyesno(
                "Respaldo en curso", "Hay un respaldo en curso. ¿Salir de todos modos?"):
            return
        self.destroy()

    # ================================================================ RESPALDO
    def _tab_backup(self):
        f = ttk.Frame(self.nb, padding=10)
        self.nb.add(f, text="🗄  Respaldo")
        left = ttk.Frame(f, width=480)
        left.pack(side="left", fill="y", padx=(0, 10))

        # --- Arriba y siempre visible: destino, contraseña, opciones y botón
        g = ttk.LabelFrame(left, text="Respaldo", padding=8)
        g.pack(fill="x")
        ttk.Label(g, text="Guardar en:").grid(row=0, column=0, sticky="w")
        self.dest_var = tk.StringVar(value=self.cfg["dest_dir"])
        ttk.Entry(g, textvariable=self.dest_var, width=34).grid(row=0, column=1, sticky="ew")
        ttk.Button(g, text="…", width=3, command=self._pick_dest).grid(row=0, column=2)
        ttk.Label(g, text="Contraseña del .7z:").grid(row=1, column=0, sticky="w", pady=2)
        self.pw1 = ttk.Entry(g, show="•", width=34)
        self.pw1.grid(row=1, column=1, sticky="ew")
        ttk.Label(g, text="Repetir:").grid(row=2, column=0, sticky="w")
        self.pw2 = ttk.Entry(g, show="•", width=34)
        self.pw2.grid(row=2, column=1, sticky="ew")
        self.show_pw = tk.BooleanVar(value=False)
        ttk.Checkbutton(g, text="👁", variable=self.show_pw, command=lambda: [
            e.config(show="" if self.show_pw.get() else "•") for e in (self.pw1, self.pw2)]
                        ).grid(row=1, column=2, rowspan=2)
        ttk.Label(g, text="Sin esta contraseña no se puede abrir el respaldo. ¡No la olvides!",
                  style="Hint.TLabel").grid(row=3, column=0, columnspan=3, sticky="w")
        self.send_tg = tk.BooleanVar(value=bool(self.cfg.get("bot_token") or
                                                 self.cfg.get("telegram_mode") == "user"))
        ttk.Checkbutton(g, text="Enviar a mi Telegram al terminar",
                        variable=self.send_tg).grid(row=4, column=0, columnspan=3, sticky="w", pady=(4, 0))
        self.send_hl = tk.BooleanVar(value=bool(self.cfg.get("homelab_url")))
        ttk.Checkbutton(g, text="Enviar a mi homelab (la Pi lo guarda y lo reenvía al bot)",
                        variable=self.send_hl).grid(row=5, column=0, columnspan=3, sticky="w")
        self.ts_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(g, text="Crear también una instantánea de Timeshift (sistema)",
                        variable=self.ts_var,
                        state="normal" if core.timeshift_available() else "disabled"
                        ).grid(row=6, column=0, columnspan=3, sticky="w")
        g.columnconfigure(1, weight=1)
        self.btn_backup = ttk.Button(left, text="▶  Crear respaldo", style="Big.TButton",
                                     command=self._do_backup)
        self.btn_backup.pack(fill="x", pady=8)

        # --- Abajo: qué respaldar, con barra de desplazamiento
        ttk.Label(left, text="¿Qué quieres respaldar?", style="Title.TLabel").pack(anchor="w")
        box = ttk.Frame(left)
        box.pack(fill="both", expand=True)
        canvas = tk.Canvas(box, highlightthickness=0, width=460)
        sb = ttk.Scrollbar(box, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")

        def wheel(e):  # rueda del ratón (X11 usa Button-4/5)
            canvas.yview_scroll(-1 if (getattr(e, "num", 0) == 4 or e.delta > 0) else 1, "units")
        for w in (canvas, inner):
            w.bind("<Enter>", lambda _e: (canvas.bind_all("<MouseWheel>", wheel),
                                          canvas.bind_all("<Button-4>", wheel),
                                          canvas.bind_all("<Button-5>", wheel)))
            w.bind("<Leave>", lambda _e: (canvas.unbind_all("<MouseWheel>"),
                                          canvas.unbind_all("<Button-4>"),
                                          canvas.unbind_all("<Button-5>")))

        self.cat_vars: dict[str, tk.BooleanVar] = {}
        for cat in core.CATEGORIES:
            v = tk.BooleanVar(value=cat.default)
            self.cat_vars[cat.key] = v
            row = ttk.Frame(inner)
            row.pack(anchor="w", fill="x", pady=1)
            ttk.Checkbutton(row, text=("🔒 " if cat.sensitive else "") + cat.label,
                            variable=v).pack(anchor="w")
            if cat.note:
                ttk.Label(row, text="     " + cat.note, style="Hint.TLabel",
                          wraplength=420).pack(anchor="w")

        right = ttk.Frame(f)
        right.pack(side="left", fill="both", expand=True)
        ttk.Label(right, text="Bitácora", style="Title.TLabel").pack(anchor="w")
        self.pbar = ttk.Progressbar(right, maximum=100)
        self.pbar.pack(fill="x", pady=4)
        self.log = tk.Text(right, wrap="word", font=("Monospace", 9), bg="#1e1e2e",
                           fg="#cdd6f4", insertbackground="#cdd6f4")
        self.log.pack(fill="both", expand=True)
        self.log.insert("end", "Listo. Elige qué respaldar y presiona «Crear respaldo».\n")

    def _pick_dest(self):
        d = filedialog.askdirectory(initialdir=self.dest_var.get() or str(Path.home()))
        if d:
            self.dest_var.set(d)

    def _do_backup(self):
        if self.busy:
            return
        p1, p2 = self.pw1.get(), self.pw2.get()
        if not p1:
            messagebox.showwarning("Contraseña", "Escribe una contraseña para cifrar el respaldo.")
            return
        if p1 != p2:
            messagebox.showwarning("Contraseña", "Las contraseñas no coinciden.")
            return
        if len(p1) < 8 and not messagebox.askyesno(
                "Contraseña corta", "La contraseña tiene menos de 8 caracteres. ¿Continuar?"):
            return
        selected = [k for k, v in self.cat_vars.items() if v.get()]
        if not selected:
            messagebox.showinfo("Nada", "No marcaste ninguna sección.")
            return
        self.cfg["dest_dir"] = self.dest_var.get()
        self._save()
        self.busy = True
        self.btn_backup.state(["disabled"])
        self.log.delete("1.0", "end")
        send = self.send_tg.get()
        homelab = self.send_hl.get()
        limit = core.telegram_limit(self.cfg) if send else core.LIMIT_2GB
        self.bg(self._backup_worker, selected, p1, limit, send, self.ts_var.get(), homelab)

    def _backup_worker(self, selected, password, limit, send, timeshift, homelab=False):
        log = lambda m: self.q.put(("log", m))
        prog = lambda x: self.q.put(("progress", x))
        try:
            if timeshift:
                log("• Creando instantánea de Timeshift (pedirá tu contraseña)…")
                code, out = core.run(core.timeshift_snapshot_cmd("Linux Guardian"), timeout=3600)
                log(("✔ " if code == 0 else "⚠ ") + out.strip().splitlines()[-1] if out.strip()
                    else ("✔ Timeshift listo" if code == 0 else "⚠ Timeshift falló"))
            log(f"Límite por archivo: {core.human(limit)}"
                + (" (Bot API pública de Telegram)" if limit == core.LIMIT_BOT_PUBLIC else ""))
            files = core.create_backup(selected, self.cfg["dest_dir"], password, limit,
                                       self.cfg.get("custom_paths", []),
                                       self.cfg.get("password_files", []), log, prog,
                                       apps_excluded=self.cfg.get("apps_excluded", []),
                                       themes_selected=self.cfg.get("themes_selected"))
            self.cfg["last_backup"] = dt.datetime.now().isoformat(timespec="minutes")
            self._save()
            log("")
            for p in files:
                log(f"📦 {p}  ({core.human(p.stat().st_size)})")
            if homelab:
                log("\n• Subiendo a tu homelab…")
                core.homelab_upload(self.cfg, files, log)
            if send:
                log("\n• Enviando a Telegram…")
                core.telegram_send_files(self.cfg, files, log)
            log("\n✅ Respaldo terminado.")
            core.notify("Respaldo terminado", f"{len(files)} archivo(s) en {self.cfg['dest_dir']}")
        except Exception as e:  # noqa: BLE001
            log(f"\n✘ Error: {e}")
        finally:
            def done():
                self.busy = False
                self.btn_backup.state(["!disabled"])
                self._refresh_status()
            self.ui(done)

    # =============================================================== EXPLORADOR
    def _tab_explorer(self):
        f = ttk.Frame(self.nb, padding=6)
        self.nb.add(f, text="📁  Explorador")
        bar = ttk.Frame(f)
        bar.pack(fill="x")
        self.path_var = tk.StringVar(value=str(Path.home()))
        ttk.Button(bar, text="⌂", width=3, command=lambda: self._exp_go(Path.home())).pack(side="left")
        ttk.Button(bar, text="↑", width=3,
                   command=lambda: self._exp_go(Path(self.path_var.get()).parent)).pack(side="left")
        e = ttk.Entry(bar, textvariable=self.path_var)
        e.pack(side="left", fill="x", expand=True, padx=4)
        e.bind("<Return>", lambda _e: self._exp_go(Path(self.path_var.get()).expanduser()))
        self.hidden_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="Ocultos", variable=self.hidden_var,
                        command=lambda: self._exp_go(Path(self.path_var.get()))).pack(side="left")
        ttk.Button(bar, text="＋ Agregar al respaldo", command=self._exp_add).pack(side="left", padx=4)
        ttk.Button(bar, text="Abrir", command=self._exp_open).pack(side="left")

        pw = ttk.PanedWindow(f, orient="horizontal")
        pw.pack(fill="both", expand=True, pady=6)
        lf = ttk.Frame(pw)
        self.tree = ttk.Treeview(lf, columns=("size", "mod"), selectmode="extended")
        self.tree.heading("#0", text="Nombre")
        self.tree.heading("size", text="Tamaño")
        self.tree.heading("mod", text="Modificado")
        self.tree.column("#0", width=320)
        self.tree.column("size", width=80, anchor="e")
        self.tree.column("mod", width=120)
        sb = ttk.Scrollbar(lf, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.tree.bind("<<TreeviewOpen>>", self._exp_expand)
        self.tree.bind("<<TreeviewSelect>>", self._exp_select)
        self.tree.bind("<Double-1>", self._exp_dbl)
        pw.add(lf, weight=1)

        rf = ttk.Frame(pw)
        self.prev_title = ttk.Label(rf, text="Vista previa", style="Title.TLabel")
        self.prev_title.pack(anchor="w")
        # Barra de páginas (solo visible con PDFs)
        self.pdf_bar = ttk.Frame(rf)
        ttk.Button(self.pdf_bar, text="◀", width=3, command=lambda: self._pdf_page(-1)).pack(side="left")
        self.pdf_lbl = ttk.Label(self.pdf_bar, text="")
        self.pdf_lbl.pack(side="left", padx=6)
        ttk.Button(self.pdf_bar, text="▶", width=3, command=lambda: self._pdf_page(1)).pack(side="left")
        ttk.Button(self.pdf_bar, text="Abrir PDF", command=self._exp_open).pack(side="left", padx=8)
        self._pdf = None  # (ruta, página, total)
        self._prev_token = 0
        self.prev_img = ttk.Label(rf)
        self.prev_img.pack(anchor="w")
        self.prev = tk.Text(rf, wrap="word", font=("Monospace", 9))
        psb = ttk.Scrollbar(rf, command=self.prev.yview)
        self.prev.configure(yscrollcommand=psb.set)
        self.prev.pack(side="left", fill="both", expand=True)
        psb.pack(side="left", fill="y")
        pw.add(rf, weight=1)

        ttk.Label(f, text="Rutas personalizadas en el respaldo:").pack(anchor="w")
        cl = ttk.Frame(f)
        cl.pack(fill="x")
        self.custom_list = tk.Listbox(cl, height=4)
        self.custom_list.pack(side="left", fill="x", expand=True)
        ttk.Button(cl, text="Quitar", command=self._exp_remove).pack(side="left", padx=4)
        for p in self.cfg.get("custom_paths", []):
            self.custom_list.insert("end", p)
        self._img_ref = None
        self._exp_go(Path.home())

    def _exp_go(self, path: Path):
        if not path.is_dir():
            return
        self.path_var.set(str(path))
        self.tree.delete(*self.tree.get_children())
        self._exp_fill("", path)

    def _exp_fill(self, parent, path: Path):
        try:
            entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError as e:
            self.tree.insert(parent, "end", text=f"⚠ {e.strerror}")
            return
        for p in entries:
            if not self.hidden_var.get() and p.name.startswith("."):
                continue
            try:
                st = p.lstat()
                mod = dt.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
                isdir = p.is_dir() and not p.is_symlink()
                size = "" if isdir else core.human(st.st_size)
            except OSError:
                continue
            icon = "📁 " if isdir else ("🔗 " if p.is_symlink() else self._icon(p))
            iid = self.tree.insert(parent, "end", text=icon + p.name, values=(size, mod),
                                   open=False, tags=(str(p),))
            if isdir:
                self.tree.insert(iid, "end", text="…", tags=("__dummy__",))

    @staticmethod
    def _icon(p: Path) -> str:
        s = p.suffix.lower()
        if s in IMG_EXT:
            return "🖼 "
        if s == ".deb":
            return "📦 "
        if s == ".pdf":
            return "📕 "
        if s in (".7z", ".zip", ".gz", ".xz", ".tar", ".zst"):
            return "🗜 "
        if os.access(p, os.X_OK):
            return "⚙ "
        return "📄 "

    def _path_of(self, iid) -> Path | None:
        tags = self.tree.item(iid, "tags")
        if not tags or tags[0] == "__dummy__":
            return None
        return Path(tags[0])

    def _exp_expand(self, _e):
        iid = self.tree.focus()
        kids = self.tree.get_children(iid)
        if len(kids) == 1 and self.tree.item(kids[0], "tags") == ("__dummy__",):
            self.tree.delete(kids[0])
            p = self._path_of(iid)
            if p:
                self._exp_fill(iid, p)

    def _exp_dbl(self, _e):
        p = self._path_of(self.tree.focus())
        if p and p.is_dir():
            self._exp_go(p)

    def _exp_open(self):
        p = self._path_of(self.tree.focus())
        if p:
            subprocess.Popen(["xdg-open", str(p)])

    def _exp_add(self):
        added = 0
        for iid in self.tree.selection():
            p = self._path_of(iid)
            if p and str(p) not in self.cfg["custom_paths"]:
                self.cfg["custom_paths"].append(str(p))
                self.custom_list.insert("end", str(p))
                added += 1
        if added:
            self.cat_vars["custom"].set(True)
            self._save()

    def _exp_remove(self):
        for i in reversed(self.custom_list.curselection()):
            val = self.custom_list.get(i)
            self.custom_list.delete(i)
            if val in self.cfg["custom_paths"]:
                self.cfg["custom_paths"].remove(val)
        self._save()

    def _set_preview(self, title: str, text: str):
        self.prev_title.config(text=title)
        self.prev.delete("1.0", "end")
        self.prev.insert("end", text)

    def _exp_select(self, _e):
        p = self._path_of(self.tree.focus())
        if not p:
            return
        self.prev_img.config(image="")
        self._img_ref = None
        self._set_preview(p.name, "Cargando…")
        self._prev_token += 1
        if p.suffix.lower() == ".pdf" and p.is_file():
            self._pdf = (p, 0, 0)
            self.pdf_bar.pack(anchor="w", pady=2, before=self.prev_img)
            self._pdf_render()
            return
        self._pdf = None
        self.pdf_bar.pack_forget()
        self.bg(self._preview_worker, p, self._prev_token)

    # ---- PDF
    def _pdf_page(self, delta):
        if not self._pdf:
            return
        p, page, total = self._pdf
        new = max(0, min(page + delta, max(total - 1, 0)))
        if new != page:
            self._pdf = (p, new, total)
            self._prev_token += 1
            self._pdf_render()

    def _pdf_render(self):
        p, page, _ = self._pdf
        token = self._prev_token
        self.pdf_lbl.config(text=f"página {page + 1} / …")

        def work():
            png, total, text = core.render_pdf_page(str(p), page)

            def show():
                if token != self._prev_token:
                    return
                self._pdf = (p, page, total)
                self.pdf_lbl.config(text=f"página {page + 1} / {total}" if total else "")
                st = p.stat()
                self._set_preview(p.name, f"Ruta: {p}\nTamaño: {core.human(st.st_size)}\n{text}")
                if png:
                    import base64
                    try:
                        self._img_ref = tk.PhotoImage(data=base64.b64encode(png))
                        self.prev_img.config(image=self._img_ref)
                    except tk.TclError:
                        pass
            self.ui(show)
        self.bg(work)

    def _preview_worker(self, p: Path, token: int = 0):
        title, text, img = p.name, "", None
        try:
            st = p.lstat()
            head = (f"Ruta: {p}\nTamaño: {core.human(st.st_size)}   Permisos: "
                    f"{oct(st.st_mode & 0o7777)}\n")
            if p.is_symlink():
                head += f"Enlace → {os.readlink(p)}\n"
            if p.is_dir():
                try:
                    n = sum(1 for _ in p.iterdir())
                except OSError:
                    n = "?"
                code, out = core.run(["du", "-sh", str(p)], timeout=15)
                size = out.split()[0] if code == 0 and out else "(muy grande, no calculado)"
                text = head + f"Elementos: {n}\nTamaño total: {size}\n"
            elif p.suffix.lower() == ".deb":
                info, deps = core.deb_file_depends(str(p))
                text = head + "\n".join(f"{k}: {v}" for k, v in info.items() if k != "Depends")
                text += "\n\nDependencias:\n" + "\n".join(
                    f"  {'✔' if ok else '✘ FALTA'}  {d}" for d, ok in deps)
                miss = sum(1 for _, ok in deps if not ok)
                text += (f"\n\n{miss} dependencias faltan; restore.sh las instala antes del .deb."
                         if miss else "\n\nTodas las dependencias están instaladas.")
            elif p.suffix.lower() in IMG_EXT and p.suffix.lower() != ".svg":
                text = head
                img = p
            else:
                with open(p, "rb") as fh:
                    chunk = fh.read(65536)
                if b"\0" not in chunk[:8192] and (p.suffix.lower() in TEXT_EXT or chunk):
                    try:
                        body = chunk.decode("utf-8")
                    except UnicodeDecodeError:
                        body = chunk.decode("latin-1")
                    text = head + "─" * 60 + "\n" + body
                    if st.st_size > len(chunk):
                        text += "\n… (archivo truncado en la vista previa)"
                else:
                    _, ftype = core.run(["file", "-b", str(p)], timeout=10)
                    text = head + f"Tipo: {ftype.strip() or 'binario'}\n"
                    if "ELF" in ftype:
                        _, ldd = core.run(["ldd", str(p)], timeout=10)
                        missing = [l for l in ldd.splitlines() if "not found" in l]
                        text += f"\nBibliotecas enlazadas: {len(ldd.splitlines())}\n"
                        text += ("Faltan:\n" + "\n".join(missing)) if missing else "Ninguna falta ✔\n"
                        text += "\n" + ldd[:4000]
                    code, owner = core.run(["dpkg", "-S", str(p)], timeout=10)
                    if code == 0:
                        text += f"\nPertenece al paquete: {owner.split(':')[0]}\n"
        except OSError as e:
            text = f"No se puede leer: {e}"

        def show():
            if token != self._prev_token:
                return  # el usuario ya eligió otro archivo
            self._set_preview(title, text)
            if img is not None:
                self._show_image(img)
        self.ui(show)

    def _show_image(self, p: Path):
        self._img_ref = self._load_thumb(p, (480, 320))
        if self._img_ref:
            self.prev_img.config(image=self._img_ref)

    # ============================================================ APLICACIONES
    APP_FILTERS = {
        "Todas": lambda r: True,
        "Con ventana": lambda r: r["kind"] in ("apt", "flatpak", "snap", "manual")
                                  and not r["row"].startswith(("flatpak:", "snap:")),
        "apt": lambda r: r["kind"] in ("apt", "apt-cli"),
        "Flatpak": lambda r: r["kind"] == "flatpak",
        "Snap": lambda r: r["kind"] == "snap",
        "Manual (AppImage, /opt…)": lambda r: r["kind"] == "manual",
        "apt sin ventana": lambda r: r["kind"] == "apt-cli",
        "Solo desmarcadas": None,
    }

    def _tab_apps(self):
        f = ttk.Frame(self.nb, padding=6)
        self.nb.add(f, text="📦  Aplicaciones")
        bar = ttk.Frame(f)
        bar.pack(fill="x")
        ttk.Button(bar, text="⟳ Analizar", command=self._apps_scan).pack(side="left")
        ttk.Label(bar, text="  Buscar:").pack(side="left")
        self.app_q = tk.StringVar()
        self.app_q.trace_add("write", lambda *_: self._apps_filter())
        ttk.Entry(bar, textvariable=self.app_q, width=24).pack(side="left")
        self.app_kind = tk.StringVar(value="Todas")
        cb = ttk.Combobox(bar, textvariable=self.app_kind, values=list(self.APP_FILTERS),
                          state="readonly", width=24)
        cb.pack(side="left", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda _e: self._apps_filter())
        self.app_hidden = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Incluir ocultas", variable=self.app_hidden,
                        command=self._apps_scan).pack(side="left")
        ttk.Button(bar, text="☑ Marcar visibles",
                   command=lambda: self.app_tree.set_keys(self.app_tree.visible_keys(), True)
                   ).pack(side="left", padx=(12, 2))
        ttk.Button(bar, text="☐ Desmarcar visibles",
                   command=lambda: self.app_tree.set_keys(self.app_tree.visible_keys(), False)
                   ).pack(side="left")
        ttk.Label(f, style="Hint.TLabel", text=(
            "Haz clic en la casilla ☑/☐ (o selecciona varias y presiona Espacio) para decidir qué se "
            "reinstala. apt/Flatpak/Snap solo guardan el nombre; las «Manual» copian sus archivos "
            "(AppImage, carpeta en /opt…). Tu selección se guarda sola.")).pack(anchor="w", pady=4)
        self.app_tree = CheckTree(f, ("Aplicación", "Tipo", "Paquete / ID", "Ubicación"),
                                  (260, 150, 230, 380), on_change=self._apps_changed)
        self.app_tree.pack(fill="both", expand=True)
        self.app_count = ttk.Label(f, text="Presiona «Analizar» para listar todo lo instalado.")
        self.app_count.pack(anchor="w", pady=4)
        self._app_data: dict[str, dict] = {}

    def _apps_scan(self):
        self.app_count.config(text="Analizando aplicaciones… (unos segundos)")
        hidden = self.app_hidden.get()

        def work():
            rows = core.list_applications(include_hidden=hidden)
            self.ui(lambda: self._apps_loaded(rows))
        self.bg(work)

    def _apps_loaded(self, rows):
        self._app_data = {r["row"]: r for r in rows}
        excl = set(self.cfg.get("apps_excluded", []))
        checked = {r["key"]: r["key"] not in excl for r in rows}
        self.app_tree.set_rows(
            [(r["row"], r["key"], (r["name"], core.KIND_LABEL[r["kind"]], r["id"], r["where"]))
             for r in rows], checked)
        self._apps_filter()

    def _apps_filter(self):
        q = self.app_q.get().lower().strip()
        kind = self.app_kind.get()
        pred_kind = self.APP_FILTERS.get(kind)

        def pred(rid, r):
            d = self._app_data[rid]
            if kind == "Solo desmarcadas":
                if self.app_tree.checked.get(r["key"], True):
                    return False
            elif pred_kind and not pred_kind(d):
                return False
            return not q or q in " ".join(map(str, r["values"])).lower()
        self.app_tree.show(pred)
        self._apps_count()

    def _apps_changed(self):
        self.cfg["apps_excluded"] = sorted(k for k, on in self.app_tree.checked.items() if not on)
        self._save()
        self._apps_count()

    def _apps_count(self):
        keys = {r["key"] for r in self._app_data.values()}
        on = sum(1 for k in keys if self.app_tree.checked.get(k, True))
        by = {}
        for r in self._app_data.values():
            by[r["kind"]] = by.get(r["kind"], 0) + 1
        self.app_count.config(text=f"{on} de {len(keys)} marcadas para el respaldo   ·   " +
                              "  ".join(f"{core.KIND_LABEL[k]}: {n}" for k, n in sorted(by.items())))

    # =================================================================== TEMAS
    def _tab_themes(self):
        f = ttk.Frame(self.nb, padding=6)
        self.nb.add(f, text="🎨  Temas")
        bar = ttk.Frame(f)
        bar.pack(fill="x")
        ttk.Button(bar, text="⟳ Analizar", command=self._th_scan).pack(side="left")
        self.th_kind = tk.StringVar(value="Todos")
        kinds = ["Todos", "GTK / Shell", "Íconos", "Cursores", "Íconos + cursores", "Fuentes",
                 "SDDM", "Plasma", "Plasma Look&Feel", "Esquema de color",
                 "Decoración ventanas", "Kvantum", "Fondos", "Solo en uso", "Solo del usuario"]
        cb = ttk.Combobox(bar, textvariable=self.th_kind, values=kinds, state="readonly", width=20)
        cb.pack(side="left", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda _e: self._th_filter())
        ttk.Button(bar, text="☑ Marcar visibles",
                   command=lambda: self.th_tree.set_keys(self.th_tree.visible_keys(), True)
                   ).pack(side="left", padx=(12, 2))
        ttk.Button(bar, text="☐ Desmarcar visibles",
                   command=lambda: self.th_tree.set_keys(self.th_tree.visible_keys(), False)
                   ).pack(side="left")
        ttk.Button(bar, text="↺ Selección recomendada", command=self._th_default).pack(side="left", padx=6)
        ttk.Label(f, style="Hint.TLabel", wraplength=1100, text=(
            "Los que dicen «paquete» vienen de apt: el respaldo solo guarda el nombre y restore.sh los "
            "reinstala. Los tuyos y los del sistema sin paquete (por ejemplo un tema SDDM que bajaste) se "
            "copian completos; los de /usr/share se restauran con sudo. También se guarda qué tema de SDDM "
            "usas.")).pack(anchor="w", pady=4)
        pw = ttk.PanedWindow(f, orient="horizontal")
        pw.pack(fill="both", expand=True)
        self.th_tree = CheckTree(pw, ("Tipo", "Nombre", "Dónde", "Paquete", "En uso"),
                                 (140, 260, 80, 200, 60), on_change=self._th_changed)
        self.th_tree.tree.bind("<<TreeviewSelect>>", self._th_select, add="+")
        pw.add(self.th_tree, weight=3)
        rf = ttk.Frame(pw)
        self.th_img = ttk.Label(rf)
        self.th_img.pack(anchor="w")
        self.th_info = tk.Text(rf, wrap="word", font=("Monospace", 9), width=40)
        self.th_info.pack(fill="both", expand=True)
        pw.add(rf, weight=2)
        self.th_count = ttk.Label(f, text="Presiona «Analizar».")
        self.th_count.pack(anchor="w", pady=4)
        self._th_data: dict[str, dict] = {}
        self._th_img_ref = None

    def _th_scan(self):
        self.th_count.config(text="Analizando temas, íconos, cursores, fuentes y SDDM…")

        def work():
            items = core.list_themes()
            self.ui(lambda: self._th_loaded(items))
        self.bg(work)

    def _th_loaded(self, items):
        self._th_data = {i["row"]: i for i in items}
        sel = self.cfg.get("themes_selected")
        sel = set(sel if sel is not None else core.default_theme_selection(items))
        self.th_tree.set_rows(
            [(i["row"], i["path"], (i["kind"], i["name"], i["scope"],
                                    i["package"] or "—", "★" if i["active"] else ""))
             for i in items], {i["path"]: i["path"] in sel for i in items})
        self._th_filter()

    def _th_default(self):
        if not self._th_data:
            return
        sel = set(core.default_theme_selection(list(self._th_data.values())))
        for k in self.th_tree.checked:
            self.th_tree.checked[k] = k in sel
        self._th_filter()
        self._th_changed()

    def _th_filter(self):
        kind = self.th_kind.get()

        def pred(rid, _r):
            i = self._th_data[rid]
            if kind == "Todos":
                return True
            if kind == "Solo en uso":
                return i["active"]
            if kind == "Solo del usuario":
                return i["scope"] == "usuario"
            return i["kind"] == kind
        self.th_tree.show(pred)
        self._th_count_upd()

    def _th_changed(self):
        self.cfg["themes_selected"] = sorted(k for k, on in self.th_tree.checked.items() if on)
        self._save()
        self._th_count_upd()

    def _th_count_upd(self):
        on = [self._th_data[k] for k, v in self.th_tree.checked.items() if v and k in self._th_data]
        copy = sum(1 for i in on if not (i["scope"] == "sistema" and i["package"]))
        self.th_count.config(text=f"{len(on)} marcados de {len(self._th_data)}  ·  "
                                  f"{copy} se copian  ·  {len(on) - copy} se reinstalan con apt")

    def _th_select(self, _e):
        sel = self.th_tree.tree.selection()
        if not sel:
            return
        path = self._th_data[sel[0]]["path"]

        def work():
            info = core.theme_info(path)
            img = core.theme_preview_image(path)
            self.ui(lambda: self._th_show(info, img))
        self.bg(work)

    def _th_show(self, info, img):
        self.th_info.delete("1.0", "end")
        self.th_info.insert("end", info)
        self.th_img.config(image="")
        self._th_img_ref = None
        if img is not None:
            self._th_img_ref = self._load_thumb(img, (380, 240))
            if self._th_img_ref:
                self.th_img.config(image=self._th_img_ref)

    def _load_thumb(self, p: Path, box):
        try:
            if Image is not None:
                im = Image.open(p)
                im.thumbnail(box)
                return ImageTk.PhotoImage(im)
            if p.suffix.lower() in (".png", ".gif"):
                ph = tk.PhotoImage(file=str(p))
                k = max(1, -(-ph.width() // box[0]), -(-ph.height() // box[1]))
                return ph.subsample(k, k)
        except Exception:  # noqa: BLE001
            pass
        return None

    # ================================================================= SISTEMA
    def _tab_system(self):
        f = ttk.Frame(self.nb, padding=6)
        self.nb.add(f, text="🧩  Sistema")
        top = ttk.Frame(f)
        top.pack(fill="x")
        ttk.Button(top, text="⟳ Analizar sistema", command=self._scan).pack(side="left")
        self.scan_lbl = ttk.Label(top, text="  Presiona «Analizar» para ver apps, paquetes y temas.",
                                  style="Hint.TLabel")
        self.scan_lbl.pack(side="left")
        sub = ttk.Notebook(f)
        sub.pack(fill="both", expand=True, pady=6)

        def table(parent, cols, widths):
            fr = ttk.Frame(parent)
            t = ttk.Treeview(fr, columns=cols, show="headings")
            for c, w in zip(cols, widths):
                t.heading(c, text=c)
                t.column(c, width=w)
            s = ttk.Scrollbar(fr, command=t.yview)
            t.configure(yscrollcommand=s.set)
            t.pack(side="left", fill="both", expand=True)
            s.pack(side="left", fill="y")
            return fr, t

        fr, self.t_apps = table(sub, ("App", "Estado", "Origen", "ID", "Versión"),
                                (160, 100, 90, 260, 160))
        sub.add(fr, text="Apps importantes")
        fr, self.t_pkgs = table(sub, ("Tipo", "Nombre", "Detalle"), (90, 360, 300))
        sub.add(fr, text="Instaladas")
        fr, self.t_theme = table(sub, ("Elemento", "Valor"), (300, 500))
        sub.add(fr, text="Temas")

        dep = ttk.Frame(sub, padding=6)
        sub.add(dep, text="Dependencias")
        row = ttk.Frame(dep)
        row.pack(fill="x")
        ttk.Label(row, text="Paquete:").pack(side="left")
        self.dep_var = tk.StringVar(value="zoom")
        de = ttk.Entry(row, textvariable=self.dep_var, width=30)
        de.pack(side="left", padx=4)
        de.bind("<Return>", lambda _e: self._deps())
        ttk.Button(row, text="Ver dependencias", command=self._deps).pack(side="left")
        ttk.Button(row, text="Revisar un .deb…", command=self._deps_deb).pack(side="left", padx=4)
        self.dep_txt = tk.Text(dep, font=("Monospace", 9))
        self.dep_txt.pack(fill="both", expand=True, pady=6)
        self.dep_txt.insert("end", "Escribe el nombre de un paquete instalado (ej. zoom, code, kitty) "
                                   "o elige un .deb para ver qué dependencias tiene y cuáles faltan.\n")

    def _scan(self):
        self.scan_lbl.config(text="  Analizando… (puede tardar unos segundos)")
        self.bg(self._scan_worker)

    def _scan_worker(self):
        fl, sn = core.flatpak_apps(), core.snap_apps()
        apps = core.detect_apps(fl, sn)
        apt = core.apt_manual_packages()
        ext = core.vscode_extensions()
        themes, folders = core.theme_settings(), core.theme_folders()

        def show():
            for t in (self.t_apps, self.t_pkgs, self.t_theme):
                t.delete(*t.get_children())
            for a in apps:
                self.t_apps.insert("", "end", values=(
                    a["name"], "✔ instalada" if a["installed"] else "— no", a["source"],
                    a["id"], a["version"]))
            for a, o in fl:
                self.t_pkgs.insert("", "end", values=("Flatpak", a, o))
            for n, c, cl in sn:
                self.t_pkgs.insert("", "end", values=("Snap", n, c + (" classic" if cl else "")))
            for e in ext:
                self.t_pkgs.insert("", "end", values=("VS Code", e, "extensión"))
            for p in apt:
                self.t_pkgs.insert("", "end", values=("apt", p, "manual"))
            for s, k, v in themes:
                self.t_theme.insert("", "end", values=(f"{s.split('.')[-1]} › {k}", v))
            for rel, items in folders:
                self.t_theme.insert("", "end", values=(f"~/{rel}", ", ".join(items) or "(vacío)"))
            self.scan_lbl.config(text=f"  {sum(a['installed'] for a in apps)} apps clave · "
                                      f"{len(apt)} apt · {len(fl)} Flatpak · {len(sn)} Snap · "
                                      f"{len(ext)} extensiones VS Code")
        self.ui(show)

    def _deps(self):
        pkg = self.dep_var.get().strip()
        if not pkg:
            return
        self.dep_txt.delete("1.0", "end")
        self.dep_txt.insert("end", f"Revisando {pkg}…\n")

        def work():
            deps, rdeps = core.package_depends(pkg)
            out = f"Dependencias de {pkg}:\n"
            out += "\n".join(f"  {'✔' if ok else '✘'}  {d}" for d, ok in deps) or "  (ninguna o no instalado)"
            out += f"\n\nPaquetes instalados que dependen de {pkg}:\n  " + (", ".join(rdeps) or "(ninguno)")
            self.ui(lambda: (self.dep_txt.delete("1.0", "end"), self.dep_txt.insert("end", out)))
        self.bg(work)

    def _deps_deb(self):
        f = filedialog.askopenfilename(filetypes=[("Paquetes .deb", "*.deb")],
                                       initialdir=str(Path.home() / "Descargas"))
        if not f:
            return

        def work():
            info, deps = core.deb_file_depends(f)
            out = "\n".join(f"{k}: {v}" for k, v in info.items() if k != "Depends")
            out += "\n\nDependencias:\n" + "\n".join(
                f"  {'✔' if ok else '✘ FALTA'}  {d}" for d, ok in deps)
            out += ("\n\nPara instalarlo resolviendo dependencias:\n  sudo apt install " + repr(f)
                    + "\n\nO agrégalo al respaldo desde el Explorador: restore.sh lo instalará "
                      "revisando dependencias primero.")
            self.ui(lambda: (self.dep_txt.delete("1.0", "end"), self.dep_txt.insert("end", out)))
        self.bg(work)

    # ============================================================= CONTRASEÑAS
    def _tab_passwords(self):
        f = ttk.Frame(self.nb, padding=12)
        self.nb.add(f, text="🔑  Contraseñas")
        ttk.Label(f, text="Exporta tus contraseñas tú mismo", style="Title.TLabel").pack(anchor="w")
        steps = (
            "Linux Guardian no lee ni descifra las contraseñas de tus navegadores: tú las exportas "
            "y aquí solo eliges el archivo para meterlo al respaldo cifrado.\n\n"
            "Brave:   Menú ☰ › Contraseñas y autocompletar › Administrador de contraseñas › "
            "Configuración › Exportar contraseñas  (o usa el botón de abajo).\n"
            "Firefox: about:logins › ⋯ › Exportar inicios de sesión.\n"
            "KeePassXC / Bitwarden: exporta su base de datos o JSON cifrado (lo más seguro).\n\n"
            "⚠ El CSV exportado está en TEXTO PLANO. Dentro del .7z queda cifrado con AES-256, "
            "pero el archivo original en tu disco no: bórralo con el botón «Borrar de forma segura» "
            "cuando ya hayas hecho el respaldo.")
        ttk.Label(f, text=steps, wraplength=900, justify="left").pack(anchor="w", pady=8)
        row = ttk.Frame(f)
        row.pack(anchor="w", pady=4)
        ttk.Button(row, text="Abrir administrador de Brave",
                   command=lambda: self._launch(["brave-browser", "brave://password-manager/settings"],
                                                ["brave", "brave://password-manager/settings"],
                                                ["flatpak", "run", "com.brave.Browser",
                                                 "brave://password-manager/settings"])).pack(side="left")
        ttk.Button(row, text="Abrir Firefox (about:logins)",
                   command=lambda: self._launch(["firefox", "about:logins"])).pack(side="left", padx=6)
        ttk.Label(f, text="Archivos de contraseñas incluidos en el respaldo:").pack(anchor="w", pady=(10, 2))
        lr = ttk.Frame(f)
        lr.pack(fill="x")
        self.pw_list = tk.Listbox(lr, height=6)
        self.pw_list.pack(side="left", fill="x", expand=True)
        bcol = ttk.Frame(lr)
        bcol.pack(side="left", padx=6)
        ttk.Button(bcol, text="＋ Agregar archivo…", command=self._pw_add).pack(fill="x")
        ttk.Button(bcol, text="Quitar de la lista", command=self._pw_remove).pack(fill="x", pady=4)
        ttk.Button(bcol, text="🗑 Borrar de forma segura", command=self._pw_shred).pack(fill="x")
        for p in self.cfg.get("password_files", []):
            self.pw_list.insert("end", p)

    def _launch(self, *cmds):
        for c in cmds:
            if shutil.which(c[0]):
                subprocess.Popen(c)
                return
        messagebox.showinfo("No encontrado", "No encontré ese navegador instalado.")

    def _pw_add(self):
        for f in filedialog.askopenfilenames(initialdir=str(Path.home())):
            if f not in self.cfg["password_files"]:
                self.cfg["password_files"].append(f)
                self.pw_list.insert("end", f)
        self.cat_vars["passwords"].set(bool(self.cfg["password_files"]))
        self._save()

    def _pw_remove(self):
        for i in reversed(self.pw_list.curselection()):
            v = self.pw_list.get(i)
            self.pw_list.delete(i)
            if v in self.cfg["password_files"]:
                self.cfg["password_files"].remove(v)
        self._save()

    def _pw_shred(self):
        sel = [self.pw_list.get(i) for i in self.pw_list.curselection()]
        if not sel:
            messagebox.showinfo("Selecciona", "Selecciona primero el archivo en la lista.")
            return
        if not messagebox.askyesno("Borrar", "Se sobrescribirá y borrará:\n\n" + "\n".join(sel)
                                   + "\n\nNo se puede deshacer. ¿Ya hiciste el respaldo?"):
            return
        for f in sel:
            core.run(["shred", "-u", "-z", f]) if shutil.which("shred") else Path(f).unlink(missing_ok=True)
        self._pw_remove()

    # =============================================================== RESTAURAR
    def _tab_restore(self):
        f = ttk.Frame(self.nb, padding=12)
        self.nb.add(f, text="⏳  Máquina del tiempo")
        ttk.Label(f, text="Restaurar en un sistema nuevo o roto", style="Title.TLabel").pack(anchor="w")
        ttk.Label(f, wraplength=950, justify="left", text=(
            "1) Elige el .7z (o el .7z.001 si viene en partes) y escribe su contraseña.\n"
            "2) Se extrae y se abre una terminal con restore.sh, que pregunta sección por sección: "
            "repos de Brave y VS Code, paquetes apt, Zoom (.deb con revisión de dependencias), Flathub, "
            "Snaps, tus configuraciones y ~/.ssh, zsh + Oh My Zsh + Powerlevel10k, extensiones de "
            "VS Code y tu tema.\n"
            "Si tu sistema no arranca bien, primero usa Timeshift (abajo) para volver a un punto anterior."
        )).pack(anchor="w", pady=6)
        g = ttk.Frame(f)
        g.pack(fill="x", pady=6)
        self.rs_file = tk.StringVar()
        ttk.Label(g, text="Respaldo:").grid(row=0, column=0, sticky="w")
        ttk.Entry(g, textvariable=self.rs_file, width=70).grid(row=0, column=1, sticky="ew")
        ttk.Button(g, text="…", width=3, command=self._rs_pick).grid(row=0, column=2)
        ttk.Label(g, text="Contraseña:").grid(row=1, column=0, sticky="w")
        self.rs_pw = ttk.Entry(g, show="•", width=40)
        self.rs_pw.grid(row=1, column=1, sticky="w")
        self.rs_yes = tk.BooleanVar(value=False)
        ttk.Checkbutton(g, text="Instalar todo sin preguntar (--yes)",
                        variable=self.rs_yes).grid(row=2, column=1, sticky="w")
        self.rs_dry = tk.BooleanVar(value=False)
        ttk.Checkbutton(g, text="Solo simular (--dry-run)",
                        variable=self.rs_dry).grid(row=3, column=1, sticky="w")
        g.columnconfigure(1, weight=1)
        ttk.Button(f, text="▶  Extraer y restaurar", style="Big.TButton",
                   command=self._rs_go).pack(anchor="w", pady=8)
        ttk.Button(f, text="📂 Ya lo extraje: elegir carpeta y correr restore.sh",
                   command=self._rs_folder).pack(anchor="w")

        ttk.Separator(f).pack(fill="x", pady=14)
        ttk.Label(f, text="Timeshift: instantáneas del sistema", style="Title.TLabel").pack(anchor="w")
        ttk.Label(f, wraplength=950, style="Hint.TLabel", text=(
            "El respaldo .7z reinstala tus apps y configuraciones; Timeshift guarda una foto completa del "
            "sistema (/) para regresar en minutos si una actualización lo rompe. Se complementan.")).pack(anchor="w")
        row = ttk.Frame(f)
        row.pack(anchor="w", pady=6)
        st = "normal" if core.timeshift_available() else "disabled"
        ttk.Button(row, text="📸 Crear instantánea ahora", state=st,
                   command=lambda: open_terminal(core.timeshift_snapshot_cmd("Linux Guardian manual"))
                   ).pack(side="left")
        ttk.Button(row, text="Abrir Timeshift", state=st,
                   command=lambda: subprocess.Popen(["timeshift-launcher"]
                                                    if shutil.which("timeshift-launcher")
                                                    else ["pkexec", "timeshift-gtk"])).pack(side="left", padx=6)
        if not core.timeshift_available():
            ttk.Label(row, text="  Timeshift no está instalado:  sudo apt install timeshift",
                      style="Hint.TLabel").pack(side="left")

    def _rs_pick(self):
        f = filedialog.askopenfilename(initialdir=self.cfg["dest_dir"],
                                       filetypes=[("Respaldos 7z", "*.7z *.7z.001"), ("Todos", "*")])
        if f:
            self.rs_file.set(f)

    def _restore_cmd(self, folder: Path) -> list[str]:
        cmd = [str(folder / "restore.sh")]
        if self.rs_yes.get():
            cmd.append("--yes")
        if self.rs_dry.get():
            cmd.append("--dry-run")
        return cmd

    def _rs_go(self):
        f, pw = self.rs_file.get(), self.rs_pw.get()
        seven = core.find_7z()
        if not (f and Path(f).exists()):
            messagebox.showwarning("Archivo", "Elige el archivo de respaldo.")
            return
        if not seven:
            messagebox.showerror("7-Zip", "Instala 7-Zip:  sudo apt install 7zip")
            return
        out = Path(f).parent / "restaurar"
        out.mkdir(exist_ok=True)
        self.nb.select(0)
        self.log.delete("1.0", "end")
        self.q.put(("log", f"• Extrayendo {Path(f).name} en {out}…"))

        def work():
            code, msg = core.run([seven, "x", "-y", f"-p{pw}", f"-o{out}", f], timeout=7200)
            if code != 0:
                self.q.put(("log", "✘ No se pudo extraer (¿contraseña incorrecta?)\n" + msg[-800:]))
                return
            dirs = sorted(out.glob("respaldo_*/restore.sh"))
            if not dirs:
                self.q.put(("log", "✘ No encontré restore.sh dentro del respaldo."))
                return
            folder = dirs[-1].parent
            self.q.put(("log", f"✔ Extraído en {folder}\n• Abriendo terminal con restore.sh…"))
            if not open_terminal(self._restore_cmd(folder)):
                self.q.put(("log", f"Abre una terminal y corre:\n  {folder}/restore.sh"))
        self.bg(work)

    def _rs_folder(self):
        d = filedialog.askdirectory(initialdir=self.cfg["dest_dir"])
        if d and (Path(d) / "restore.sh").exists():
            open_terminal(self._restore_cmd(Path(d)))
        elif d:
            messagebox.showwarning("restore.sh", "Esa carpeta no tiene restore.sh.")

    # ================================================================ TELEGRAM
    def _tab_telegram(self):
        f = ttk.Frame(self.nb, padding=12)
        self.nb.add(f, text="✈  Telegram")
        ttk.Label(f, text="Enviar respaldos a tu Telegram", style="Title.TLabel").pack(anchor="w")
        self.tg_mode = tk.StringVar(value=self.cfg.get("telegram_mode", "bot"))
        m = ttk.Frame(f)
        m.pack(anchor="w", pady=6)
        ttk.Radiobutton(m, text="Bot de Telegram (fácil; 50 MB por archivo con la API pública, "
                                "2 GB con servidor Bot API local)",
                        variable=self.tg_mode, value="bot").pack(anchor="w")
        ttk.Radiobutton(m, text="Mi cuenta personal → «Mensajes guardados» (2 GB por archivo; "
                                "requiere Telethon)",
                        variable=self.tg_mode, value="user").pack(anchor="w")

        g = ttk.LabelFrame(f, text="Bot", padding=8)
        g.pack(fill="x", pady=4)
        self.tg_vars = {}

        def field(parent, r, label, key, show=None, hint=""):
            ttk.Label(parent, text=label).grid(row=r, column=0, sticky="w", pady=2)
            v = tk.StringVar(value=str(self.cfg.get(key, "")))
            ttk.Entry(parent, textvariable=v, width=60, show=show).grid(row=r, column=1, sticky="ew")
            if hint:
                ttk.Label(parent, text=hint, style="Hint.TLabel").grid(row=r, column=2, sticky="w", padx=6)
            self.tg_vars[key] = v

        field(g, 0, "Token del bot:", "bot_token", "•", "Créalo con @BotFather → /newbot")
        field(g, 1, "Tu chat_id:", "chat_id", None, "Escríbele a tu bot y presiona «Detectar»")
        field(g, 2, "Servidor API:", "api_base", None, "Deja el oficial, o tu Bot API local para 2 GB")
        g.columnconfigure(1, weight=1)

        u = ttk.LabelFrame(f, text="Cuenta personal (Telethon)", padding=8)
        u.pack(fill="x", pady=4)
        field(u, 0, "api_id:", "api_id", None, "De https://my.telegram.org › API development tools")
        field(u, 1, "api_hash:", "api_hash", "•")
        ttk.Label(u, style="Hint.TLabel", text="Telethon ya viene en el entorno virtual si usaste ./install.sh. "
                  "El chat_id vacío = Mensajes guardados."
                  ).grid(row=2, column=0, columnspan=3, sticky="w")
        u.columnconfigure(1, weight=1)

        h = ttk.LabelFrame(f, text="Mi homelab (Raspberry) — recomendado", padding=8)
        h.pack(fill="x", pady=4)
        field(h, 0, "URL del homelab:", "homelab_url", None, "ej. https://192.168.0.5")
        field(h, 1, "Dispositivo:", "homelab_device", None, "como está en homelab.yml, ej. laptop-linux")
        field(h, 2, "Token:", "homelab_token", "•", "BACKUP_TOKEN_… del .env de la Pi")
        field(h, 3, "Certificado CA:", "homelab_ca", None, "root.crt de Caddy (vacío = no verificar)")
        ttk.Label(h, style="Hint.TLabel", text="La Pi guarda el respaldo, borra los viejos y te lo reenvía "
                  "por tu bot (hasta 2 GB por parte). Con esto no hace falta configurar el bot aquí."
                  ).grid(row=4, column=0, columnspan=3, sticky="w")
        h.columnconfigure(1, weight=1)

        row = ttk.Frame(f)
        row.pack(anchor="w", pady=8)
        ttk.Button(row, text="💾 Guardar", command=self._tg_save).pack(side="left")
        ttk.Button(row, text="🏠 Probar homelab", command=self._hl_test).pack(side="left", padx=6)
        ttk.Button(row, text="🔍 Detectar chat_id", command=self._tg_detect).pack(side="left", padx=6)
        ttk.Button(row, text="📨 Mensaje de prueba", command=self._tg_test).pack(side="left")
        ttk.Button(row, text="🔐 Iniciar sesión en Telegram (cuenta)",
                   command=self._tg_login).pack(side="left", padx=6)
        self.tg_out = ttk.Label(f, text="", wraplength=950)
        self.tg_out.pack(anchor="w")
        ttk.Label(f, style="Hint.TLabel", wraplength=950, text=(
            "Si el respaldo pesa más que el límite del modo elegido, se parte automáticamente en "
            "volúmenes .7z.001, .7z.002… y se envían todos. Para unirlos solo extrae el .001 con 7-Zip. "
            "El token se guarda en ~/.config/linux-guardian/config.json con permisos 600.")).pack(anchor="w", pady=10)

    def _tg_save(self):
        for k, v in self.tg_vars.items():
            self.cfg[k] = v.get().strip()
        self.cfg["telegram_mode"] = self.tg_mode.get()
        if not self.cfg["api_base"]:
            self.cfg["api_base"] = "https://api.telegram.org"
        self._save()
        self.tg_out.config(text=f"Guardado. Límite por archivo en este modo: "
                                f"{core.human(core.telegram_limit(self.cfg))}")

    def _hl_test(self):
        self._tg_save()

        def work():
            ok, msg = core.homelab_check(self.cfg)
            self.ui(lambda: self.tg_out.config(text=("✔ " if ok else "✘ ") + msg))
        self.bg(work)

    def _tg_detect(self):
        self._tg_save()
        if not self.cfg["bot_token"]:
            self.tg_out.config(text="Pon primero el token del bot.")
            return

        def work():
            import json
            ok, out = core._curl_bot(self.cfg, "getUpdates", {}, 20)
            msg = "No encontré mensajes. Escríbele algo a tu bot en Telegram y vuelve a intentar."
            if ok:
                try:
                    res = json.loads(out)["result"]
                    for upd in reversed(res):
                        chat = (upd.get("message") or upd.get("channel_post") or {}).get("chat")
                        if chat:
                            cid = str(chat["id"])
                            self.cfg["chat_id"] = cid
                            self._save()
                            self.ui(lambda: self.tg_vars["chat_id"].set(cid))
                            msg = f"✔ chat_id detectado: {cid} ({chat.get('first_name') or chat.get('title', '')})"
                            break
                except (ValueError, KeyError):
                    pass
            else:
                msg = "✘ " + out[:300]
            self.ui(lambda: self.tg_out.config(text=msg))
        self.bg(work)

    def _tg_test(self):
        self._tg_save()

        def work():
            ok, out = core.telegram_send_message(self.cfg, "🛡 Prueba de Linux Guardian: todo listo.")
            self.ui(lambda: self.tg_out.config(text="✔ Mensaje enviado." if ok else "✘ " + out[:300]))
        self.bg(work)

    def _tg_login(self):
        self._tg_save()
        if not (self.cfg["api_id"] and self.cfg["api_hash"]):
            self.tg_out.config(text="Pon api_id y api_hash primero.")
            return
        if not open_terminal([sys.executable, str(Path(__file__).resolve()), "--telethon-login"]):
            self.tg_out.config(text="Corre en una terminal:  python3 guardian.py --telethon-login")

    # ============================================================ RECORDATORIO
    def _tab_reminder(self):
        f = ttk.Frame(self.nb, padding=12)
        self.nb.add(f, text="⏰  Recordatorio")
        ttk.Label(f, text="Recordatorio cada 3 meses", style="Title.TLabel").pack(anchor="w")
        ttk.Label(f, wraplength=950, justify="left", text=(
            "Se instala un temporizador de systemd (de tu usuario, sin root) que revisa una vez al día. "
            "Si ya pasaron los días indicados desde tu último respaldo, te manda una notificación de "
            "escritorio y, si configuraste Telegram, también un mensaje. Si la computadora estaba "
            "apagada, revisa al encender.")).pack(anchor="w", pady=6)
        row = ttk.Frame(f)
        row.pack(anchor="w", pady=4)
        ttk.Label(row, text="Avisar cada").pack(side="left")
        self.days_var = tk.IntVar(value=int(self.cfg.get("remind_days", 90)))
        ttk.Spinbox(row, from_=7, to=365, textvariable=self.days_var, width=5).pack(side="left", padx=4)
        ttk.Label(row, text="días (90 ≈ 3 meses)").pack(side="left")
        b = ttk.Frame(f)
        b.pack(anchor="w", pady=8)
        ttk.Button(b, text="✔ Activar", command=self._rem_on).pack(side="left")
        ttk.Button(b, text="✘ Desactivar", command=self._rem_off).pack(side="left", padx=6)
        ttk.Button(b, text="🔔 Probar notificación",
                   command=lambda: self.bg(core.remind_cli, True)).pack(side="left")
        self.rem_lbl = ttk.Label(f, text="Estado: " + core.reminder_status())
        self.rem_lbl.pack(anchor="w", pady=6)

    def _rem_on(self):
        self.cfg["remind_days"] = int(self.days_var.get())
        self._save()
        msg = core.reminder_install()
        self.rem_lbl.config(text=msg + "\nEstado: " + core.reminder_status())
        self._refresh_status()

    def _rem_off(self):
        msg = core.reminder_remove()
        self.rem_lbl.config(text=msg + "\nEstado: " + core.reminder_status())
        self._refresh_status()


if __name__ == "__main__":
    Guardian().mainloop()
