from __future__ import annotations

import os
from pathlib import Path
import queue
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import VERSION
from .bridge import Bridge
from .catalog import load_catalog
from .downloader import DownloadQueue
from .paths import bundle_root, data_root, downloads_root, write_json
from .runtime import check_runtime, open_edge

NAVY = "#13233F"
BG = "#F3F6FB"
MUTED = "#60718B"
BLUE = "#2563EB"


class DesktopApp:
    def __init__(self, root: tk.Tk, setup=False, smoke=False):
        self.root, self.setup, self.smoke = root, setup, smoke
        self.exit_code = 12 if setup else 0
        self.events = queue.Queue()
        self.entries = []
        self.checks = []
        self.checking = False
        self.finishing = False
        self.bridge = None
        self.destination = downloads_root()
        self.downloads = DownloadQueue(bundle_root(), data_root() / "last_session.json", self.events.put)
        root.title("Reel Short Downloader Top" + (" — الإعداد" if setup else ""))
        left, top, right, bottom = 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            area = wintypes.RECT()
            if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(area), 0):
                left, top, right, bottom = area.left, area.top, area.right, area.bottom
        self.work_area = (left, top, right, bottom)
        width = max(980, min(1160, right - left - 40))
        height = max(600, min(800, bottom - top - 70))
        root.geometry(f"{width}x{height}+{left+16}+{top+10}")
        root.minsize(980, 600)
        root.configure(bg=BG)
        root.protocol("WM_DELETE_WINDOW", self.close)
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=NAVY, font=("Segoe UI", 11))
        style.configure("Card.TFrame", background="white")
        style.configure("TButton", font=("Segoe UI", 10), padding=(12, 8), borderwidth=0, background="#E6EDF7", foreground=NAVY)
        style.map("TButton", background=[("active", "#D8E5F8"), ("disabled", "#EDF1F6")])
        style.configure("Primary.TButton", background=BLUE, foreground="white")
        style.map("Primary.TButton", background=[("disabled", "#CBD5E1"), ("active", "#1D4ED8")])
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(18, 7), font=("Segoe UI", 11), background="#E6EDF7", foreground=NAVY)
        style.map("TNotebook.Tab", background=[("selected", "#FFFFFF"), ("active", "#D8E5F8")])
        style.configure("Treeview", background="white", fieldbackground="white", foreground=NAVY, font=("Segoe UI", 10), rowheight=35, borderwidth=0)
        style.configure("Treeview.Heading", background="#E8EEF7", foreground=NAVY, font=("Segoe UI", 10, "bold"), padding=8)
        style.map("Treeview", background=[("selected", "#DBEAFE")], foreground=[("selected", NAVY)])

        header = tk.Frame(root, bg=NAVY, padx=24, pady=8)
        header.pack(fill="x")
        header.columnconfigure(1, weight=1)
        tk.Label(header, text="REEL SHORT", bg=NAVY, fg="white", font=("Segoe UI", 20, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(header, text="DOWNLOADER TOP", bg=NAVY, fg="#A9C6FF", font=("Segoe UI", 9, "bold")).grid(row=1, column=0, sticky="w")
        tk.Label(header, text="مساحة واحدة للفهرسة والتنزيل وإعداد الإضافة", bg=NAVY, fg="#E0E7F3", font=("Segoe UI", 12)).grid(row=0, column=1, rowspan=2, sticky="e")
        body = ttk.Frame(root, padding=(20, 12))
        body.pack(fill="both", expand=True)
        self.connection = tk.StringVar(value="الإضافة غير متصلة")
        bar = ttk.Frame(body)
        bar.pack(fill="x", pady=(0, 8))
        ttk.Label(bar, text=f"v{VERSION}  •  Windows", foreground=MUTED).pack(side="left")
        self.connection_label = ttk.Label(bar, textvariable=self.connection, foreground=MUTED)
        self.connection_label.pack(side="right")
        self.tabs = ttk.Notebook(body)
        self.tabs.pack(fill="both", expand=True)
        self.episodes_tab, self.setup_tab, self.logs_tab = [ttk.Frame(self.tabs, padding=12) for _ in range(3)]
        self.tabs.add(self.episodes_tab, text="الحلقات والتنزيل")
        self.tabs.add(self.setup_tab, text="إعداد Microsoft Edge")
        self.tabs.add(self.logs_tab, text="سجل العمليات")
        self._episodes_page()
        self._setup_page()
        self._logs_page()
        self.footer = tk.StringVar(value="اختر ملف فهرس الحلقات للبدء.")
        ttk.Label(body, textvariable=self.footer, foreground=MUTED, anchor="e", wraplength=1000).pack(fill="x", pady=(8, 0))
        if not smoke:
            try:
                self.bridge = Bridge(data_root() / "connection.json")
                self.bridge.start()
                self.code.set(self.bridge.enable_pairing())
            except OSError:
                self.bridge = None
                self.code.set("غير متاح")
                self.events.put(("log", "تعذر فتح الاتصال المحلي 127.0.0.1:18765. أغلق أي نسخة أخرى من التطبيق ثم أعد فتحه."))
        self.root.after(250, self._poll)
        if setup:
            self.tabs.select(self.setup_tab)
        self.recheck()

    def _button(self, parent, text, command, primary=False):
        return ttk.Button(parent, text=text, command=command, style="Primary.TButton" if primary else "TButton")

    def _episodes_page(self):
        frame = self.episodes_tab
        ttk.Label(frame, text="مكتبة الحلقات", font=("Segoe UI", 18, "bold"), anchor="e").pack(fill="x")
        self.summary = tk.StringVar(value="لم يُحدد فهرس بعد")
        ttk.Label(frame, textvariable=self.summary, anchor="e", foreground=MUTED, wraplength=980).pack(fill="x", pady=(4, 10))
        actions = ttk.Frame(frame)
        actions.pack(fill="x", pady=(0, 8))
        self._button(actions, "فتح ReelShort في Edge", lambda: self._guard(lambda: open_edge("https://www.reelshort.com/"))).pack(side="left")
        self.import_button = self._button(actions, "اختيار فهرس CSV", self.import_csv, True)
        self.import_button.pack(side="right")
        table_frame = ttk.Frame(frame)
        table_frame.pack(fill="both", expand=True)
        self.table = ttk.Treeview(table_frame, columns=("status", "media", "playback", "episode"), show="headings", selectmode="extended", height=7)
        for col, title, width in (("status", "الحالة", 300), ("media", "رابط الفيديو", 190), ("playback", "تشغيل المتصفح", 210), ("episode", "الحلقة", 90)):
            self.table.heading(col, text=title)
            self.table.column(col, width=width, minwidth=70, anchor="e")
        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        self.table.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        output = ttk.Frame(frame)
        output.pack(fill="x", pady=12)
        self._button(output, "تغيير مجلد الحفظ", self.choose_destination).pack(side="left")
        self.folder_label = ttk.Label(output, text=str(self.destination), foreground=MUTED, anchor="e", wraplength=750)
        self.folder_label.pack(side="right", fill="x", expand=True, padx=12)
        row = ttk.Frame(frame)
        row.pack(fill="x")
        self.download_button = self._button(row, "تنزيل المحدد / جميع الروابط", self.start_download, True)
        self.download_button.pack(side="right")
        self.stop_button = self._button(row, "إيقاف وحفظ التقدم", self.stop_download)
        self.stop_button.pack(side="right", padx=8)
        self._button(row, "فتح مجلد التنزيلات", lambda: self.open_folder(self.destination)).pack(side="left")
        self.progress = ttk.Progressbar(frame, maximum=100)
        self.progress.pack(fill="x", pady=(12, 0))
        # Reserve bottom actions first, allowing only the scrollable table to shrink.
        for widget in (table_frame, output, row, self.progress):
            widget.pack_forget()
        self.progress.pack(side="bottom", fill="x", pady=(8, 0))
        row.pack(side="bottom", fill="x")
        output.pack(side="bottom", fill="x", pady=8)
        table_frame.pack(fill="both", expand=True)

    def _setup_page(self):
        frame = self.setup_tab
        ttk.Label(frame, text="تجهيز المتصفح والتحقق من الاتصال", font=("Segoe UI", 18, "bold"), anchor="e").pack(fill="x")
        ttk.Label(frame, text="1. افتح صفحة الإضافات، ثم فعّل Developer mode واختر Load unpacked.\n2. اختر مجلد الإضافة المنسوخ، ثم افتح أيقونتها وأدخل رمز الربط أدناه.\n3. اضغط «ربط التطبيق» داخل الإضافة واتركها مفتوحة حتى اكتمال التحقق.", justify="right", anchor="e", wraplength=980).pack(fill="x", pady=10)
        row = ttk.Frame(frame)
        row.pack(fill="x")
        self._button(row, "نسخ المسار وفتح إضافات Edge", self.open_extensions, True).pack(side="right")
        self._button(row, "فتح مجلد الإضافة", lambda: self.open_folder(bundle_root() / "extension")).pack(side="right", padx=8)
        self._button(row, "رمز ربط جديد", self.new_code).pack(side="left")
        ttk.Label(frame, text=str(bundle_root() / "extension"), foreground=MUTED, anchor="e", wraplength=980).pack(fill="x", pady=(8, 0))
        code_row = ttk.Frame(frame)
        code_row.pack(fill="x", pady=8)
        self.code = tk.StringVar(value="—")
        ttk.Label(code_row, textvariable=self.code, font=("Consolas", 20, "bold"), foreground=BLUE).pack(side="right")
        ttk.Label(code_row, text="رمز الربط • صالح لخمس دقائق   ", foreground=MUTED).pack(side="right")
        self._button(code_row, "نسخ الرمز", lambda: self.copy(self.code.get())).pack(side="left")
        check_container = ttk.Frame(frame)
        self.check_table = ttk.Treeview(check_container, columns=("detail", "result", "name"), show="headings", height=5, selectmode="none")
        for key, text, width in (("detail", "التفاصيل", 600), ("result", "النتيجة", 100), ("name", "المتطلب", 180)):
            self.check_table.heading(key, text=text)
            self.check_table.column(key, width=width, anchor="e")
        check_scroll = ttk.Scrollbar(check_container, orient="vertical", command=self.check_table.yview)
        self.check_table.configure(yscrollcommand=check_scroll.set)
        self.check_table.pack(side="left", fill="both", expand=True)
        check_scroll.pack(side="right", fill="y")
        end = ttk.Frame(frame)
        self._button(end, "إعادة فحص المتطلبات", self.recheck).pack(side="left")
        if self.setup:
            self.finish_button = self._button(end, "إتمام الإعداد بعد التحقق", self.finish_setup, True)
            self.finish_button.pack(side="right")
            self.finish_button.configure(state="disabled")
        hint = ttk.Label(frame, text="إذا أوقف Edge الإضافة، فعّلها من صفحة الإضافات ثم أعد فتح نافذتها. يلزم الاتصال الحالي للتحقق.", anchor="e", foreground=MUTED, wraplength=920)
        hint.pack(side="bottom", fill="x", pady=(8, 0))
        end.pack(side="bottom", fill="x", pady=(8, 0))
        check_container.pack(fill="both", expand=True, pady=6)

    def _logs_page(self):
        ttk.Label(self.logs_tab, text="سجل محلي دون عرض روابط الوصول", font=("Segoe UI", 18, "bold"), anchor="e").pack(fill="x", pady=(0, 12))
        self.log = tk.Text(self.logs_tab, bg="white", fg=NAVY, font=("Consolas", 10), relief="flat", wrap="word", state="disabled")
        self.log.pack(fill="both", expand=True)
        self._button(self.logs_tab, "فتح مجلد التقارير", lambda: self.open_folder(data_root())).pack(anchor="w", pady=(10, 0))

    def _guard(self, action):
        try:
            return action()
        except (OSError, ValueError, RuntimeError) as e:
            messagebox.showerror("Reel Short Downloader Top", str(e), parent=self.root)

    def copy(self, value):
        self.root.clipboard_clear()
        self.root.clipboard_append(value)

    def open_folder(self, path: Path):
        def action():
            path.mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                os.startfile(path)
            else:
                subprocess.Popen(["xdg-open", str(path)])
        self._guard(action)

    def open_extensions(self):
        self.copy(str(bundle_root() / "extension"))
        self._guard(lambda: open_edge("edge://extensions/"))

    def new_code(self):
        if self.bridge:
            self.code.set(self.bridge.enable_pairing())
        else:
            messagebox.showerror("الاتصال غير متاح", "أغلق أي نسخة أخرى من التطبيق ثم أعد فتحه.", parent=self.root)

    def import_csv(self):
        if self.downloads.running:
            return
        name = filedialog.askopenfilename(parent=self.root, title="اختيار فهرس الحلقات", filetypes=[("Episode index", "*.csv")])
        if not name:
            return
        try:
            entries = load_catalog(Path(name))
        except (OSError, ValueError) as e:
            messagebox.showerror("تعذر استيراد الفهرس", str(e), parent=self.root)
            return
        self.entries = entries
        self.table.delete(*self.table.get_children())
        for item in entries:
            self.table.insert("", "end", iid=str(item.episode), values=("جاهزة" if item.media_url else "صفحة فقط — لا رابط تنزيل", "متاح من الفهرس" if item.media_url else "غير متاح", "موثق" if item.playback == "playback_verified" else "غير موثق", item.episode))
        media = sum(bool(item.media_url) for item in entries)
        self.summary.set(f"{entries[0].title}   •   {len(entries)} حلقة مفهرسة   •   {media} رابط فيديو   •   {len(entries)-media} دون رابط")
        self.footer.set(Path(name).name + " — حدد صفوفًا لتنزيلها، أو اترك التحديد فارغًا لتنزيل جميع الروابط الصريحة.")

    def choose_destination(self):
        if self.downloads.running:
            return
        chosen = filedialog.askdirectory(parent=self.root, title="مجلد حفظ الفيديو", initialdir=str(self.destination) if self.destination.is_dir() else str(Path.home()))
        if chosen:
            self.destination = Path(chosen)
            self.folder_label.configure(text=chosen)
            self.recheck()

    def start_download(self):
        if self.setup or self.downloads.running:
            return
        if self.checking or not self.checks or not all(c["ok"] for c in self.checks):
            self.tabs.select(self.setup_tab)
            messagebox.showerror("المتطلبات غير جاهزة", "أكمل المتطلبات ثم أعد الفحص.", parent=self.root)
            return
        selection = set(self.table.selection())
        entries = [e for e in self.entries if e.media_url and (not selection or str(e.episode) in selection)]
        if not entries:
            messagebox.showinfo("لا توجد روابط", "اختر فهرسًا يحتوي MediaURL. مرشحات HLS لا تُستخدم تلقائيًا.", parent=self.root)
            return
        self.progress["value"] = 0
        self._guard(lambda: self.downloads.start(entries, self.destination))

    def stop_download(self):
        self.footer.set("جارٍ إيقاف التنزيل؛ ستُحفظ الملفات الجزئية للاستئناف.")
        threading.Thread(target=self.downloads.cancel, daemon=True).start()

    def recheck(self):
        if self.checking:
            return
        self.checking = True
        destination = self.destination
        def worker():
            try:
                checks = check_runtime(output=destination)
                self.events.put(("checks", checks))
            except Exception as e:
                self.events.put(("checks", [{"name": "Runtime check", "ok": False, "detail": str(e)}]))
        threading.Thread(target=worker, daemon=True).start()

    def finish_setup(self):
        if self.bridge and self.bridge.status()["connected"] and not self.checking:
            self.finishing = True
            self.recheck()  # Revalidate the actual files immediately before returning success.

    def _poll(self):
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            kind, *args = event
            if kind == "log":
                self.log.configure(state="normal")
                self.log.insert("end", args[0] + "\n")
                if int(self.log.index("end-1c").split(".")[0]) > 500:
                    self.log.delete("1.0", "100.0")
                self.log.see("end")
                self.log.configure(state="disabled")
            elif kind == "checks":
                self.checks = args[0]
                self.checking = False
                self.check_table.delete(*self.check_table.get_children())
                for c in self.checks:
                    self.check_table.insert("", "end", values=(c["detail"], "جاهز" if c["ok"] else "غير جاهز", c["name"]))
                try:
                    write_json(data_root() / "readiness.json", {"checks": self.checks})
                except OSError:
                    pass
                if self.finishing:
                    self.finishing = False
                    if all(c["ok"] for c in self.checks) and self.bridge and self.bridge.status()["connected"]:
                        self.exit_code = 0
                        self.root.destroy()
                        return
            elif kind == "episode":
                ep, value = args
                if self.table.exists(str(ep)):
                    current = list(self.table.item(str(ep), "values"))
                    current[0] = value
                    self.table.item(str(ep), values=current)
                    self.table.see(str(ep))
                self.footer.set(f"الحلقة {ep}: {value}")
            elif kind == "progress":
                ep, percent = args
                self.progress["value"] = percent
                self.footer.set(f"الحلقة {ep}: {percent:.1f}%")
            elif kind == "done":
                results, cancelled = args
                ok = sum(r["status"] == "مكتمل ومفحوص" for r in results)
                self.footer.set(f"{'توقف التنزيل' if cancelled else 'انتهت الدفعة'} • مكتمل ومفحوص: {ok} • فاشل أو متوقف: {len(results)-ok}")
        connected = self.bridge is not None and self.bridge.status()["connected"]
        self.connection.set("إضافة Edge متصلة ومتحقق منها" if connected else "الإضافة غير متصلة — افتح نافذتها للتحقق")
        self.connection_label.configure(foreground="#047857" if connected else MUTED)
        ready = bool(self.checks) and all(c["ok"] for c in self.checks) and not self.checking
        if self.setup:
            self.finish_button.configure(state="normal" if ready and connected else "disabled")
        running = self.downloads.running
        self.download_button.configure(state="disabled" if self.setup or running else "normal")
        self.import_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")
        self.root.after(250, self._poll)

    def close(self):
        if self.downloads.running:
            if not messagebox.askyesno("إيقاف التنزيل", "إيقاف التنزيل وحفظ الملفات الجزئية قبل الإغلاق؟", parent=self.root):
                return
            self.stop_download()
            self._wait_close()
            return
        self.root.destroy()

    def _wait_close(self):
        if self.downloads.running:
            self.root.after(200, self._wait_close)
        else:
            self.root.destroy()

    def cleanup(self):
        if self.bridge:
            self.bridge.close()
