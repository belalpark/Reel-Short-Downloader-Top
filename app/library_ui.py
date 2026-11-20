"""A compact library view; media URLs stay private and are never presented as permanent."""
from datetime import datetime
import time
import tkinter as tk
from tkinter import ttk
from .failures import MAX_ATTEMPTS, explanation


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
        ttk.Label(body, text='يُنزَّل مسلسل واحد في كل مرة: تُفحص حلقاته في تبويب Edge الظاهر وتُنزَّل كاملة، ثم ينتقل التطبيق إلى التالي. المحفوظ لا يُنزَّل مرة أخرى. الجرد يضيف مسلسلات من الموقع فقط.', anchor='e', wraplength=990, justify='right').pack(fill='x', pady=(6, 10))
        self.summary = tk.StringVar()
        ttk.Label(body, textvariable=self.summary, anchor='e', font=('Segoe UI', 11, 'bold'), wraplength=1000, justify='right').pack(fill='x', pady=(0, 8))
        actions = ttk.Frame(body)
        actions.pack(fill='x', pady=(0, 10))
        ttk.Button(actions, text='نزّل المسلسلات بالترتيب', command=app.download_sequence, style='Primary.TButton').pack(side='right', padx=6)
        ttk.Button(actions, text='نزّل المسلسل المحدد', command=lambda: app.download_selected((self.table.selection() or [''])[0])).pack(side='right', padx=4)
        ttk.Button(actions, text='جرد مسلسلات جديدة', command=app.inventory_library).pack(side='right', padx=4)
        ttk.Button(actions, text='إيقاف المكتبة', command=app.stop_library).pack(side='right')
        upkeep = ttk.Frame(body)
        upkeep.pack(fill='x', pady=(0, 10))
        ttk.Button(upkeep, text='أعد فحص المعلّق والمقفل', command=app.retry_library).pack(side='right', padx=(6, 4))
        ttk.Button(upkeep, text='أعد تنزيل الملفات المفقودة', command=app.redownload_missing).pack(side='right', padx=4)
        ttk.Button(upkeep, text='صدّر فهرس المكتبة', command=app.export_library).pack(side='right', padx=4)
        ttk.Button(upkeep, text='فحص / إعادة الربط', command=app.reconnect).pack(side='left')
        filters = ttk.Frame(body)
        filters.pack(fill='x', pady=(0, 6))
        ttk.Label(filters, text='بحث عن مسلسل').pack(side='right')
        self.search = tk.StringVar()
        ttk.Entry(filters, textvariable=self.search, justify='right', width=32).pack(side='right', padx=6)
        self.category = tk.StringVar(value='الكل')
        ttk.Combobox(filters, textvariable=self.category, state='readonly', width=16,
                     values=('الكل','جاهز','محفوظ','بلا رابط','يحتاج تجديدًا','معلّق','مقفلة في الحساب','ملف مفقود','محمي أو غير مدعوم')).pack(side='right')
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
        ttk.Label(body, text='انقر المسلسل مرتين لعرض حلقاته وسبب تعذّر أي حلقة. حدّد مسلسلًا ثم «نزّل المسلسل المحدد» لتنزيله كاملًا.', anchor='e').pack(fill='x')
        self.refresh()
        self.window.after(1000,self.tick)

    def tick(self):
        if self.window.winfo_exists():
            self.refresh()
            self.window.after(1000,self.tick)

    def refresh(self):
        if not self.window.winfo_exists():
            return
        series = self.app.library_store.series()
        self.summary.set(f"المسلسلات: {len(series)}   |   الحلقات: {sum(s['total'] for s in series)}   |   روابط موثقة: {sum(s['ready'] for s in series)}   |   محفوظ: {sum(s['done'] for s in series)}   |   بلا رابط: {sum(s['missing'] for s in series)}   |   مقفلة في الحساب: {sum(s['locked'] for s in series)}   |   معلّق: {sum(s['held'] for s in series)}" + (f"   |   ملفات مفقودة: {missing_files}" if (missing_files := sum(s['file_missing'] for s in series)) else ''))
        existing = set(self.table.get_children())
        engine=self.app.library_engine
        jobs={j['seriesId']:j for j in engine.scheduler.hub.snapshot()} if engine and engine.scheduler else {}
        query = self.search.get().strip().casefold()
        for item in series:
            if query and query not in (item['title']+' '+item['id']).casefold():
                continue
            categories = {'جاهز':item['ready'],'محفوظ':item['done'],'بلا رابط':item['missing'] or not item['total'],
                          'يحتاج تجديدًا':item['refresh'],'معلّق':item['held'] or item['scan_failures']>=MAX_ATTEMPTS,
                          'مقفلة في الحساب':item['locked'],'ملف مفقود':item['file_missing'],
                          'محمي أو غير مدعوم':item['blocked']}
            if self.category.get() != 'الكل' and not categories[self.category.get()]:
                continue
            status = 'محفوظ بالكامل' if item['total'] and item['done'] == item['total'] else 'يحتاج تجديد روابط' if item['refresh'] else 'روابط جاهزة' if item['ready'] else 'فحص الروابط جارٍ' if item['checking'] else 'الحلقات لم تجمع بعد' if not item['total'] else 'بانتظار الروابط الناقصة'
            if item['id'] in jobs:
                job=jobs[item['id']];status=f"الحلقة {job['current'] or '…'}: {job.get('note') or 'جارٍ الفحص'}"[:90]
            elif item['held'] or item['scan_failures']>=MAX_ATTEMPTS:
                status = 'معلّق بعد تكرار التعذر'
            elif item['blocked'] and not item['ready']:
                status = 'مصدر محمي أو غير مدعوم'
            elif item['locked'] and not (item['ready'] or item['checking'] or item['refresh'] or item['capture_due']):
                status = f"المتاح محفوظ — {item['locked']} حلقة مقفلة في الحساب" if item['done'] + item['locked'] >= item['total'] else f"{item['locked']} حلقة مقفلة في الحساب"
            elif item['done']!=item['total']:
                due=item['retry_at']
                if due:status=f'محاولة تلقائية بعد {max(1,int(due-time.time()))} ثانية'
            if engine and engine.current == item['id']:
                status = '◀ الحالي — ' + status
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
        labels = {'done':'محفوظ على الجهاز','ready':'رابط موثق قابل لإعادة الفحص','captured':'رابط ملتقط — لم يجتز الفحص بعد','needs_refresh':'يحتاج تجديدًا من الموقع','downloading':'جارٍ التنزيل','held':'معلّق بعد تكرار التعذر','missing':'لم يُجمع رابط الحلقة','blocked':'مصدر غير مدعوم أو محمي','locked':'مقفلة في الحساب — افتحها في الموقع ثم أعد الفحص'}
        for row in self.app.library_store.episodes(chosen[0]):
            stamp = lambda t: datetime.fromtimestamp(t).strftime('%Y-%m-%d %H:%M') if t else 'غير معلن'
            status = labels.get(row['status'], row['status'])
            if row['error']:
                status+=' — '+explanation(row['error']).split('؛')[0]
            if row['status'] == 'ready' and row['expires'] and row['expires'] <= time.time()+30:
                status = 'انتهت الصلاحية المعلنة — يحتاج تجديدًا'
            table.insert('','end',values=(stamp(row['expires']) if row['expires'] else ('لم يحدّد المصدر موعدًا' if row['media_url'] else '—'),stamp(row['verified']) if row['verified'] else '—',status,row['episode']))
