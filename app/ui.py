from __future__ import annotations

import os
import json
from pathlib import Path
import queue
import threading
import time
import tempfile
import tkinter as tk
from tkinter import filedialog, ttk

from . import VERSION, EXTENSION_VERSION
from .automatic import AutomaticDownloads, provision_extension, series_from_page
from .bridge import Bridge
from .downloader import redact
from .edge_install import install_unpacked
from .paths import bundle_root, data_root, downloads_root, read_json, write_json
from .presentation import catalog_view
from .runtime import check_runtime, open_edge
from .library_store import LibraryStore
from .library_engine import LibraryEngine
from .library_ui import LibraryWindow

NAVY, BLUE, BG, MUTED = "#152540", "#2563EB", "#F2F5FA", "#60718B"


class DesktopApp:
    def __init__(self, root: tk.Tk, setup=False, smoke=False):
        self.root, self.smoke = root, smoke
        self.exit_code = 0
        self.events, self.logs = queue.Queue(), []
        self.checks, self.checking, self.installing = [], False, False
        self.return_after_install = False
        self.preparing, self.prepare_generation = False, 0
        self.entries, self.scan_meta = [], {}
        self.row_status = {}
        self.bridge = None
        self.library_engine = self.library_window = None
        self.library_restore_checked = self.resume_single = False
        self.library_dirty = False
        self.last_library_refresh = 0
        self.library_temporary = tempfile.TemporaryDirectory() if smoke else None
        self.library_store = LibraryStore((Path(self.library_temporary.name) if smoke else data_root()) / 'library.sqlite3')
        self.extension_folder = data_root() / "Extension"
        settings = read_json(data_root() / "settings.json", {})
        saved_folder = settings.get("download_folder") if isinstance(settings, dict) else None
        self.destination = Path(saved_folder) if isinstance(saved_folder, str) and Path(saved_folder).is_absolute() else downloads_root()
        self.automatic = AutomaticDownloads(bundle_root(), data_root() / "Inbox", self.destination, data_root(), self.events.put)
        self.closed = False
        root.title("Reel Short Downloader Top")
        self.work_area = (0, 0, root.winfo_screenwidth(), root.winfo_screenheight())
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            area = wintypes.RECT()
            if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(area), 0):
                self.work_area = (area.left, area.top, area.right, area.bottom)
        left, top, right, bottom = self.work_area
        root.geometry(f"{max(880,min(1100,right-left-40))}x{max(600,min(760,bottom-top-70))}+{left+16}+{top+10}")
        root.minsize(880, 600)
        root.configure(bg=BG)
        root.protocol("WM_DELETE_WINDOW", self.close)
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=NAVY, font=("Segoe UI", 10))
        style.configure("TButton", background="#E5EDF9", foreground=NAVY, padding=(12, 9), font=("Segoe UI", 11), borderwidth=0)
        style.map("TButton", background=[("active", "#D8E6FA"), ("disabled", "#E8EDF4")])
        style.configure("Primary.TButton", background=BLUE, foreground="white", font=("Segoe UI", 12, "bold"))
        style.map("Primary.TButton", background=[("active", "#1D4ED8"), ("disabled", "#AFC5EF")])
        style.configure("Treeview", background="white", fieldbackground="white", foreground=NAVY, rowheight=30, font=("Segoe UI", 10), borderwidth=0)
        style.configure("Treeview.Heading", background="#E7EEF8", foreground=NAVY, font=("Segoe UI", 10, "bold"), padding=6)
        header = tk.Frame(root, bg=NAVY, padx=22, pady=15)
        header.pack(fill="x")
        tk.Label(header, text="REEL SHORT DOWNLOADER TOP", bg=NAVY, fg="white", font=("Segoe UI", 17, "bold")).pack(side="left")
        tk.Label(header, text="افتح · أضف الملحق · حمّل", bg=NAVY, fg="#C8D9FC", font=("Segoe UI", 12)).pack(side="right")
        body = ttk.Frame(root, padding=(18, 12))
        body.pack(fill="both", expand=True)
        cards = ttk.Frame(body)
        cards.pack(fill="x")
        self.step_buttons = []
        specs = [("١", "افتح موقع المسلسل", "سجّل دخولك، ثم افتح المسلسل المطلوب.", self.open_site),
                 ("٢", "أضف الملحق", "إضافة وربط تلقائيان، دون رمز أو كلمة مرور.", self.add_extension),
                 ("٣", "نزّل من المكتبة", "ينزّل المسلسل المفتوح في Edge وروابط المكتبة المحفوظة.", self.download_library)]
        for index, (number, title, description, command) in enumerate(specs):
            cards.columnconfigure(2-index, weight=1, uniform="steps")
            card = tk.Frame(cards, bg="white", padx=12, pady=10, highlightbackground="#DAE3F0", highlightthickness=1)
            card.grid(row=0, column=2-index, sticky="nsew", padx=(5, 5))
            tk.Label(card, text=number, bg="white", fg=BLUE, font=("Segoe UI", 28, "bold")).pack(anchor="e")
            tk.Label(card, text=description, bg="white", fg=MUTED, font=("Segoe UI", 10), wraplength=265, justify="right", height=2).pack(fill="x", pady=(0, 10))
            button = ttk.Button(card, text=title, command=command, style="Primary.TButton" if index == 2 else "TButton")
            button.pack(fill="x")
            self.step_buttons.append(button)
        self.status = tk.StringVar(value="جارٍ التجهيز…")
        status_row = ttk.Frame(body)
        status_row.pack(fill="x", pady=(12, 5))
        self.stop_button = ttk.Button(status_row, text="إيقاف", command=self.stop_download)
        self.stop_button.pack(side="left")
        ttk.Label(status_row, textvariable=self.status, anchor="e", foreground=BLUE, wraplength=740).pack(side="right", fill="x", expand=True)
        self.connection = tk.StringVar(value="الملحق غير متصل")
        ttk.Label(body, textvariable=self.connection, foreground=MUTED, anchor="e").pack(fill="x", pady=(0, 5))
        self.fallback = ttk.Frame(body)
        ttk.Button(self.fallback, text="فتح صفحة الإضافات", command=self.open_extensions).pack(side="right")
        ttk.Button(self.fallback, text="نسخ مسار الملحق", command=self.copy_extension_path).pack(side="right", padx=6)
        self.counts = tk.StringVar(value="تم التنزيل: ٠   |   جاهز للتنزيل: ٠   |   متبقٍ: ٠")
        ttk.Label(body, textvariable=self.counts, anchor="e", font=("Segoe UI", 12, "bold")).pack(fill="x", pady=(2, 7))
        bottom = ttk.Frame(body)
        bottom.pack(side="bottom", fill="x", pady=(9, 0))
        ttk.Button(bottom, text="فتح مجلد الحفظ", command=lambda: self.open_folder(self.destination)).pack(side="left")
        ttk.Button(bottom, text="تغيير المجلد", command=self.choose_destination).pack(side="left", padx=6)
        ttk.Button(bottom, text="التفاصيل", command=self.show_details).pack(side="left")
        ttk.Button(bottom, text='مكتبة المسلسلات', command=self.open_library).pack(side='right')
        ttk.Button(bottom, text='إعادة الربط والفحص', command=self.reconnect).pack(side='right', padx=6)
        self.path_label = ttk.Label(body, text=str(self.destination), anchor="e", foreground=MUTED, wraplength=970)
        self.path_label.pack(side="bottom", fill="x", pady=(8, 0))
        self.progress = ttk.Progressbar(body, maximum=100)
        self.progress.pack(side="bottom", fill="x", pady=(6, 0))
        table_frame = ttk.Frame(body)
        table_frame.pack(fill="both", expand=True)
        self.table = ttk.Treeview(table_frame, columns=("status", "available", "episode"), show="headings", height=4, selectmode="none")
        for key, title, width in (("status", "حالة التنزيل", 440), ("available", "رابط الفيديو", 220), ("episode", "الحلقة", 100)):
            self.table.heading(key, text=title)
            self.table.column(key, width=width, anchor="e", minwidth=70)
        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        self.table.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        if not smoke:
            try:
                credentials = data_root() / "connection.json"
                provision_extension(bundle_root() / "extension", self.extension_folder, credentials)
                self.bridge = Bridge(credentials, inbox=data_root() / "Inbox", progress=self.automatic.progress, library=self.library_store)
                self.bridge.start()
                self.automatic.start_worker()
                self.library_engine = LibraryEngine(self.library_store, self.bridge, self.automatic, bundle_root(), data_root(), self.destination, self.events.put, session_hub=self.bridge.sessions)
                self.bridge.snapshot_handler = self.library_engine.on_snapshot
                self.library_engine.start_worker()
            except OSError as error:
                self.events.put(("error", "تعذر فتح البرنامج؛ أغلق أي نسخة أخرى ثم حاول مجددًا.", str(error)))
        self.recheck()
        root.after(250, self._poll)

    def _ready(self):
        return not self.checking and bool(self.checks) and all(c["ok"] for c in self.checks)

    def open_library(self):
        if self.library_window and self.library_window.window.winfo_exists():
            self.library_window.window.lift()
        else:
            self.library_window = LibraryWindow(self)

    def _library_ready(self, action):
        if self.installing:
            self.status.set(f'انتظر اكتمال إعادة ربط الملحق، ثم {action}.')
            return False
        if not self._ready() or not self.library_engine:
            self.status.set('لم يكتمل التجهيز؛ استخدم إعادة الربط والفحص.')
            return False
        if self.automatic.enabled or self.automatic.queue.running or self.preparing:
            self.status.set(f'أوقف تنزيل المسلسل الحالي أولًا، ثم {action}.')
            return False
        return True

    def inventory_library(self):
        """Catalog pass only. It never starts link collection or downloads."""
        if not self._library_ready('ابدأ الجرد'):
            return
        try:
            self.library_engine.start_inventory()
        except OSError as error:
            self.status.set('تعذر فتح Edge للجرد. ' + redact(str(error)))
            return
        self.open_library()

    def collect_library(self):
        """Collect missing links of series already in the library; no catalog pass."""
        if not self._library_ready('اجمع الروابط'):
            return
        self.library_engine.start_capture()
        self.open_library()

    def download_library(self):
        """Button 3: the series open in Edge, plus every saved link. Never a catalog pass."""
        if not self._library_ready('نزّل المكتبة'):
            return
        page = self.bridge.status().get('page_url', '') if self.bridge else ''
        sid = series_from_page(page)
        if sid:
            try:
                self.library_engine.start_series(sid, page)
            except ValueError:
                sid = ''
            else:
                self.status.set('أُضيف المسلسل المفتوح في Edge إلى المكتبة؛ تُجمع روابطه وينزَّل المتاح منها، مع بقية روابط المكتبة.')
        if not sid and not self.library_store.count_series():
            self.status.set('المكتبة فارغة. افتح صفحة مسلسل في Edge ثم اضغط ٣، أو افتح المكتبة واضغط «جرد مسلسلات جديدة».')
            self.open_library()
            return
        if not sid:
            self.library_engine.start_downloads()
            self.status.set('جارٍ تنزيل روابط المكتبة المحفوظة. لإضافة مسلسل افتحه في Edge ثم اضغط ٣.')
        self.open_library()

    def retry_library(self):
        if not self.library_engine or not self._ready():
            return
        self.library_store.retry_held()
        released = self.library_store.retry_locked()
        self.library_engine.start_downloads()
        if released:
            self.library_engine.start_capture()
        self.library_engine.browser_wakeup.set()
        self.library_engine.wakeup.set()

    def redownload_missing(self):
        if not self.library_engine or not self._ready():
            return
        count = self.library_store.requeue_missing_files()
        self.status.set(f'أعيدت {count} حلقة مفقودة الملف إلى طابور التنزيل.' if count else 'لا توجد ملفات محفوظة مفقودة.')
        if count:
            self.library_engine.start_downloads()

    def export_library(self):
        try:
            path = self.library_store.export_index(self.destination / 'library_index.csv')
        except OSError as error:
            self.status.set('تعذر حفظ فهرس المكتبة. ' + redact(str(error)))
            return
        self.status.set(f'حُفظ فهرس المكتبة في: {path}')
        if self.library_engine:
            self.library_engine.note = f'حُفظ فهرس المكتبة (دون روابط الفيديو) في: {path}'

    def stop_library(self):
        if self.library_engine:
            self.library_engine.stop()

    def reconnect(self):
        if self.installing or self.smoke or not self.bridge or not self.bridge.server:
            return
        self.resume_single = self.automatic.enabled
        if self.library_engine:
            self.library_engine.resume_collection = self.library_engine.collecting
            self.library_engine.suspend_browser = True
            self.library_engine.stop_collection()
        self.stop_download(single_only=True)
        self.recheck()
        self.installing = self.return_after_install = True
        self.status.set('جارٍ فحص المتطلبات وتحديث ربط الملحق؛ تقدم التنزيل محفوظ.')
        def repair():
            try:
                provision_extension(bundle_root() / 'extension', self.extension_folder, data_root() / 'connection.json')
                install_unpacked(self.extension_folder, lambda message: self.events.put(('status',message)))
                self.events.put(('extension_done',))
            except Exception as error:
                self.events.put(('extension_error',str(error)))
        threading.Thread(target=repair,daemon=True,name='reconnect-extension').start()

    def recheck(self):
        if self.checking:
            return
        self.checking = True
        destination = self.destination
        def run():
            try:
                self.events.put(("checks", check_runtime(output=destination)))
            except Exception as error:
                self.events.put(("checks", [{"name": "Runtime", "ok": False, "detail": str(error)}]))
        threading.Thread(target=run, daemon=True).start()

    def open_site(self):
        try:
            open_edge("https://www.reelshort.com/")
            self.status.set("افتح المسلسل المطلوب في الموقع، ثم تابع من الزر ٢.")
        except OSError as error:
            self.status.set(str(error))

    def add_extension(self):
        if self.installing or not self._ready():
            return
        if self.bridge and self.bridge.status()["connected"]:
            self.status.set("الملحق جاهز. افتح المسلسل في Edge ثم اضغط ٣ — نزّل من المكتبة.")
            return
        self.installing = True
        self.return_after_install = True
        self.status.set("جارٍ إضافة الملحق إلى Edge…")
        def worker():
            try:
                install_unpacked(self.extension_folder, lambda message: self.events.put(("status", message)))
                self.events.put(("extension_done",))
            except Exception as error:
                self.events.put(("extension_error", str(error)))
        threading.Thread(target=worker, daemon=True, name="edge-extension-setup").start()

    def open_extensions(self):
        self.copy_extension_path()
        try:
            open_edge("edge://extensions/")
        except OSError as error:
            self.status.set(str(error))

    def copy_extension_path(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(str(self.extension_folder))
        self.status.set("المسار منسوخ: فعّل Developer mode ثم Load unpacked والصق المسار مرة واحدة.")

    def start_download(self):
        if self.library_engine and self.library_engine.active:
            self.status.set('المكتبة تعمل؛ أوقفها أولًا لتنزيل مسلسل منفرد.')
            return
        if not self._ready() or not self.bridge or self.preparing or self.automatic.enabled or self.automatic.queue.running:
            return
        state = self.bridge.status()
        sid = series_from_page(state["page_url"])
        if not state["connected"]:
            self.status.set("اضغط الزر ٢ لإضافة الملحق أولًا.")
            return
        if not sid:
            self.status.set("افتح صفحة المسلسل المطلوب في Edge ثم اضغط بدء التنزيل.")
            return
        self.preparing = True
        self.prepare_generation += 1
        generation = self.prepare_generation
        self.status.set("جارٍ التحقق من الملفات المحفوظة لتجاوز المكتمل واستئناف الناقص فقط…")
        def prepare():
            try:
                completed = self.automatic.recover_completed(sid, lambda: generation != self.prepare_generation or self.closed)
                self.events.put(("resume_ready", sid, completed, generation))
            except (OSError, ValueError) as error:
                self.events.put(("resume_failed", str(error), generation))
        threading.Thread(target=prepare, daemon=True, name="recover-saved-files").start()

    def _begin_download(self, sid, completed):
        if not self.bridge or series_from_page(self.bridge.status()["page_url"]) != sid:
            self.status.set("افتح نفس المسلسل في Edge ثم اضغط مواصلة التنزيل.")
            return
        try:
            run_id = self.bridge.command("start", sid, patient=True, completed_episodes=completed)
            self.automatic.begin(sid, run_id, patient=True)
            self.entries, self.scan_meta, self.row_status = list(self.automatic.entries), {}, {}
            self.table.delete(*self.table.get_children())
            self.progress["value"] = 0
            self._update_table()
            self.status.set(f"جارٍ استئناف الناقص فقط؛ {len(completed)} حلقة محفوظة. لن ينتقل إلى التالية قبل اكتمال تنزيل الحالية.")
        except ValueError as error:
            self.status.set(str(error))

    def stop_download(self, single_only=False):
        self.preparing = False
        self.prepare_generation += 1
        if self.bridge:
            self.bridge.command("stop", self.automatic.series_id)
        threading.Thread(target=self.automatic.stop, daemon=True).start()
        if self.library_engine and not single_only:
            self.library_engine.stop()
        self.status.set("تم طلب الإيقاف؛ يُحفظ تقدم التنزيل للاستئناف.")

    def choose_destination(self):
        if self.preparing or self.automatic.enabled or self.automatic.queue.running or (self.library_engine and self.library_engine.active):
            self.status.set("أوقف التنزيل قبل تغيير المجلد.")
            return
        selected = filedialog.askdirectory(parent=self.root, title="مجلد حفظ المقاطع")
        if selected:
            self.destination = self.automatic.destination = Path(selected)
            if self.library_engine:
                self.library_engine.destination = self.destination
            write_json(data_root() / "settings.json", {"download_folder": selected})
            self.path_label.configure(text=selected)
            self.recheck()

    def open_folder(self, path):
        try:
            path.mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                os.startfile(path)
        except OSError as error:
            self.status.set(str(error))

    def show_details(self):
        window = tk.Toplevel(self.root)
        window.title("التفاصيل")
        ttk.Button(window,text='نسخ تقرير المشكلة',command=self.copy_diagnostics).pack(anchor='e',padx=8,pady=6)
        text = tk.Text(window, wrap="word", width=100, height=24, font=("Consolas", 10))
        text.pack(fill="both", expand=True)
        text.insert("end", "\n".join(self.logs[-300:]))
        text.configure(state="disabled")

    def copy_diagnostics(self):
        books=self.library_store.series()
        report={'app_version':VERSION,'extension_version':EXTENSION_VERSION,
            'checks':[{'name':c['name'],'ok':c['ok']} for c in self.checks],
            'library':[{'id':b['id'],'total':b['total'],'done':b['done'],'ready':b['ready'],'error':b['error']} for b in books],
            'events':self.library_engine.diagnostics if self.library_engine else []}
        self.root.clipboard_clear();self.root.clipboard_append(json.dumps(report,ensure_ascii=False,indent=2))
        self.status.set('نُسخ تقرير المشكلة دون روابط الفيديو أو بيانات الدخول.')

    def _update_table(self):
        rows, done, available, remaining = catalog_view(self.entries, self.automatic._is_complete, self.row_status)
        for fields in rows:
            episode = str(fields[2])
            if self.table.exists(episode):
                self.table.item(episode, values=fields)
            else:
                self.table.insert("", "end", iid=episode, values=fields)
        self.counts.set(f"تم التنزيل: {done}   |   جاهز للتنزيل: {available}   |   متبقٍ: {remaining}")
        return done

    def _poll(self):
        if self.closed:
            return
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            kind, *args = event
            if kind == 'library_changed':
                self.library_dirty=True
            elif kind == 'library_error':
                self.status.set(args[0])
            elif kind == 'library_progress':
                self.progress['value'] = args[1]
                self.status.set(f'المكتبة — تنزيل الحلقة {args[0]} — {args[1]:.1f}%')
            elif kind == "resume_ready":
                if self.preparing and args[2] == self.prepare_generation:
                    self.preparing = False
                    self._begin_download(args[0], args[1])
            elif kind == "resume_failed":
                if args[1] == self.prepare_generation:
                    self.preparing = False
                    self.status.set("تعذر فحص مجلد الحفظ؛ تحقق من توفره ثم أعد المواصلة.")
                    self.logs.append(redact(args[0]))
            elif kind == "checks":
                self.checks, self.checking = args[0], False
                failures = [c for c in self.checks if not c["ok"]]
                self.logs.extend(c["name"] + ": " + c["detail"] for c in self.checks)
                self.status.set("ابدأ بالزر ١ لفتح الموقع." if not failures else "لم يكتمل التجهيز: " + ", ".join(c["name"] for c in failures))
                if not failures and self.library_engine and not self.library_restore_checked:
                    self.library_restore_checked = True
                    self.library_engine.restore_downloads()
            elif kind == "catalog":
                self.entries, self.scan_meta = args[0], args[1]
                self._update_table()
                self.status.set(self.scan_meta.get("note", "جارٍ الفحص والتنزيل…"))
            elif kind == "episode":
                self.row_status[args[0]] = args[1]
                self._update_table()
            elif kind == "progress":
                self.progress["value"] = args[1]
                self.status.set(f"تنزيل الحلقة {args[0]} — {args[1]:.1f}%")
            elif kind == "done":
                self._update_table()
            elif kind == "extension_done":
                self.installing = False
                if self.bridge:
                    self.bridge.require_fresh_heartbeat()
                self.status.set("جارٍ التحقق من الملحق؛ سيظهر جاهز تلقائيًا.")
            elif kind == "extension_error":
                self.installing = False
                self.return_after_install = False
                self.logs.append(redact(args[0]))
                self.status.set(args[0][:190])
                self.fallback.pack(fill="x", before=self.table.master, pady=(3, 7))
            elif kind == 'result':
                result = args[0]
                if result['status']=='مكتمل ومفحوص' and result.get('file'):
                    self.library_store.complete(result['series_id'], result['episode'], result['file'])
                    if self.library_window and self.library_window.window.winfo_exists():
                        self.library_window.refresh()
            elif kind == "status":
                self.status.set(args[0])
            elif kind in ("log", "error"):
                self.logs.append(redact(str(args[-1])))
                if kind == "error":
                    self.status.set(args[0])
        if self.library_dirty and time.monotonic()-self.last_library_refresh>=0.8:
            self.library_dirty=False;self.last_library_refresh=time.monotonic()
            books=self.library_store.series()
            self.counts.set(f"المكتبة — محفوظ: {sum(b['done'] for b in books)}   |   جاهز: {sum(b['ready'] for b in books)}   |   المسلسلات: {len(books)}")
        state = self.bridge.status() if self.bridge else {"connected": False, "command_error": ""}
        connected = state["connected"]
        self.connection.set("الملحق جاهز ومتصل تلقائيًا" if connected else "أضف الملحق من الزر ٢ — دون رمز ربط")
        if connected:
            self.fallback.pack_forget()
            if self.return_after_install and not self.installing:
                self.return_after_install = False
                if self.library_engine:
                    self.library_engine.suspend_browser = False
                self.root.lift()
                self.root.focus_force()
                if self.library_engine and self.library_engine.resume_collection:
                    self.library_engine.resume_collection = False
                    self.library_engine.collecting = True
                    self.library_engine.browser_wakeup.set()
        if connected and self.resume_single and not self.installing and series_from_page(state.get('page_url','')):
            self.resume_single = False
            self.start_download()
        if state.get("command_error"):
            self.status.set(state["command_error"])
            if self.automatic.enabled:
                threading.Thread(target=self.automatic.stop, daemon=True).start()
        active = self.preparing or self.automatic.enabled or self.automatic.queue.running or bool(self.library_engine and self.library_engine.active)
        if self.automatic.enabled and self.scan_meta.get("paused") and not self.automatic.queue.running:
            self.automatic.enabled = False
            self.status.set(self.scan_meta.get("note", "توقف الفحص؛ افتح الحلقة المطلوبة ثم اضغط بدء التنزيل."))
        elif self.automatic.enabled and self.scan_meta.get("finished") and not self.automatic.queue.running:
            with self.automatic.lock:
                pending = self.automatic.pending()
                if not pending:
                    self.automatic.enabled = False
                    done = self._update_table()
                    self.status.set(f"انتهت المحاولة: تم تنزيل {done} من {len(self.entries)}. " + ("يمكنك إعادة المحاولة لاحقًا." if done < len(self.entries) else "جميع المقاطع محفوظة."))
        self.step_buttons[1].configure(state="disabled" if self.installing or not self._ready() else "normal")
        self.step_buttons[2].configure(state="disabled" if self.preparing or self.automatic.enabled or bool(self.library_engine and self.library_engine.downloading) or not self._ready() else "normal")
        self.stop_button.configure(state="normal" if active else "disabled")
        self.root.after(250, self._poll)

    def close(self):
        if self.closed:
            return
        self.stop_download(single_only=True)
        if self.library_engine:
            self.library_engine.pause_for_exit()
        deadline = time.monotonic() + 3
        def finish():
            waiting = self.automatic.queue.running or bool(self.library_engine and self.library_engine.queue.running) or bool(self.bridge and self.bridge.status().get('pending_kind')=='stop')
            if waiting and time.monotonic() < deadline:
                self.root.after(150, finish)
            else:
                self.closed = True
                self.root.destroy()
        self.root.after(300, finish)

    def cleanup(self):
        self.automatic.close()
        if self.library_engine:
            self.library_engine.close()
        if self.bridge:
            self.bridge.close()
        if self.library_temporary:
            self.library_temporary.cleanup()
