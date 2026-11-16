"""A compact library view; media URLs stay private and are never presented as permanent."""
from datetime import datetime
import time
import tkinter as tk
from tkinter import ttk


class LibraryWindow:
    def __init__(self, app):
        self.app = app
        self.window = tk.Toplevel(app.root)
        self.window.title('مكتبة المسلسلات — Reel Short Downloader Top')
        left,top,right,bottom=app.work_area
        self.window.geometry(f'{min(1060,right-left-40)}x{min(620,bottom-top-70)}+{left+20}+{top+20}')
        self.window.minsize(880, 540)
        body = ttk.Frame(self.window, padding=16)
        body.pack(fill='both', expand=True)
        ttk.Label(body, text='مكتبة المسلسلات', anchor='e', font=('Segoe UI', 19, 'bold')).pack(fill='x')
        ttk.Label(body, text='اجمع الروابط من Edge، ثم نزّل المكتبة. عند تعذر رابط يفتح Edge لتجديده؛ لا ضمان لرابط دائم.', anchor='e', wraplength=990).pack(fill='x', pady=(6, 10))
        self.summary = tk.StringVar()
        ttk.Label(body, textvariable=self.summary, anchor='e', font=('Segoe UI', 11, 'bold')).pack(fill='x', pady=(0, 8))
        actions = ttk.Frame(body)
        actions.pack(fill='x', pady=(0, 10))
        ttk.Button(actions, text='جمع المسلسلات', command=app.collect_library).pack(side='right')
        ttk.Button(actions, text='تنزيل / مواصلة المكتبة', command=app.download_library, style='Primary.TButton').pack(side='right', padx=6)
        ttk.Button(actions, text='إيقاف المكتبة', command=app.stop_library).pack(side='right')
        ttk.Button(actions, text='فحص / إعادة الربط', command=app.reconnect).pack(side='left')
        frame = ttk.Frame(body)
        frame.pack(fill='both', expand=True)
        self.table = ttk.Treeview(frame, columns=('state', 'done', 'ready', 'total', 'title'), show='headings', selectmode='browse')
        for key, label, width in [('state','الحالة',210),('done','محفوظ',80),('ready','روابط موثقة',100),('total','الحلقات',80),('title','المسلسل',420)]:
            self.table.heading(key, text=label)
            self.table.column(key, width=width, anchor='e', minwidth=60)
        scroll = ttk.Scrollbar(frame, orient='vertical', command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        self.table.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        self.table.bind('<Double-1>', self.details)
        self.note = tk.StringVar()
        ttk.Label(body, textvariable=self.note, anchor='e', wraplength=990).pack(fill='x', pady=(10, 5))
        ttk.Label(body, text='انقر المسلسل مرتين لعرض الحلقات وآخر فحص للرابط.', anchor='e').pack(fill='x')
        self.refresh()

    def refresh(self):
        if not self.window.winfo_exists():
            return
        series = self.app.library_store.series()
        self.summary.set(f"المسلسلات: {len(series)}   |   الحلقات: {sum(s['total'] for s in series)}   |   روابط موثقة: {sum(s['ready'] for s in series)}   |   محفوظ: {sum(s['done'] for s in series)}")
        existing = set(self.table.get_children())
        for item in series:
            status = 'محفوظ بالكامل' if item['total'] and item['done'] == item['total'] else 'يحتاج تجديد روابط' if item['refresh'] else 'روابط جاهزة' if item['ready'] else 'فحص الروابط جارٍ' if item['checking'] else 'الحلقات لم تجمع بعد' if not item['total'] else 'بانتظار الروابط الناقصة'
            fields = (status,item['done'],item['ready'],item['total'] or '—',item['title'])
            if self.table.exists(item['id']):
                self.table.item(item['id'], values=fields)
            else:
                self.table.insert('', 'end', iid=item['id'], values=fields)
            existing.discard(item['id'])
        for sid in existing:
            self.table.delete(sid)
        self.note.set(self.app.library_engine.note if self.app.library_engine else 'المكتبة محفوظة محليًا.')

    def details(self, _event=None):
        chosen = self.table.selection()
        if not chosen:
            return
        window = tk.Toplevel(self.window)
        window.title('حلقات المسلسل')
        window.geometry('860x520')
        table = ttk.Treeview(window, columns=('expiry','checked','state','episode'), show='headings')
        for key,label,width in [('expiry','صلاحية معلنة',200),('checked','آخر تحقق',200),('state','الحالة',330),('episode','الحلقة',70)]:
            table.heading(key,text=label)
            table.column(key,width=width,anchor='e')
        table.pack(fill='both',expand=True)
        labels = {'done':'محفوظ على الجهاز','ready':'رابط موثق قابل لإعادة الفحص','captured':'رابط ملتقط — لم يجتز الفحص بعد','needs_refresh':'يحتاج تجديدًا من الموقع','downloading':'جارٍ التنزيل','missing':'بانتظار التقاط الفيديو','blocked':'مصدر غير مدعوم أو محمي'}
        for row in self.app.library_store.episodes(chosen[0]):
            stamp = lambda t: datetime.fromtimestamp(t).strftime('%Y-%m-%d %H:%M') if t else 'غير معلن'
            status = labels.get(row['status'], row['status'])
            if row['status'] == 'ready' and row['expires'] and row['expires'] <= time.time()+30:
                status = 'انتهت الصلاحية المعلنة — يحتاج تجديدًا'
            table.insert('','end',values=(stamp(row['expires']),stamp(row['verified']),status,row['episode']))
