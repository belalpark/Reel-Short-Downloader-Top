"""Separate browser collection, source checking and a restartable offline queue."""
from pathlib import Path
import sqlite3
import threading
import time

from .catalog import Episode
from .inventory import CatalogInventory
from .downloader import DownloadQueue, redact
from .media_check import MediaVerifier
from .paths import read_json
from .paths import write_json
from .failures import explanation, retry_delay
from .session_scheduler import SessionScheduler
from .runtime import open_edge


class LibraryEngine:
    def __init__(self, store, bridge, automatic, package, data, destination, emit, *, verifier=None, opener=open_edge, session_hub=None):
        self.store, self.bridge, self.automatic = store, bridge, automatic
        self.package, self.data, self.destination, self.emit = package, data, destination, emit
        self.verifier, self.opener = verifier or MediaVerifier(), opener
        self.queue = DownloadQueue(package, data / 'library_session.json', self._download_event)
        self.shutdown = threading.Event()
        self.worker = None
        self.collecting = self.downloading = False
        self.inventory = CatalogInventory(bridge, opener)
        self.browser_worker = None
        self.browser_wakeup = threading.Event()
        self.migrated = threading.Event()
        self.prepared_downloads = set()
        self.capture = None
        self.active_item = None
        self.note = 'المكتبة جاهزة. اجمع الروابط ثم نزّلها.'
        self.resume_collection = False
        self.source_pause_until = self.store.setting('source_pause_until', 0)
        # Series the user opened in Edge and asked to download: captured without a catalog pass.
        saved_focus = self.store.setting('focus_series', [])
        self.focus = {s for s in saved_focus if isinstance(s, str)} if isinstance(saved_focus, list) else set()
        self.next_expiry_check = 0
        self.next_open = 0
        self.queue_lock = threading.RLock()
        self.saved_recheck = False
        self.suspend_browser = False
        self.wakeup = threading.Event()
        self.scheduler = SessionScheduler(self,session_hub) if session_hub else None
        saved_diagnostics=read_json(data/'download_diagnostics.json',{})
        self.diagnostics = saved_diagnostics.get('events',[])[-100:] if isinstance(saved_diagnostics,dict) and isinstance(saved_diagnostics.get('events'),list) else []

    def diagnostic(self,item,reason,*,http_status=0,detail='',phase='download'):
        self.diagnostics.append({'series_id':item['sid'],'episode':item['episode'],'phase':phase,
            'reason':reason,'http_status':http_status,'detail':redact(detail),'time':time.time()})
        self.diagnostics=self.diagnostics[-100:]
        write_json(self.data/'download_diagnostics.json',{'events':self.diagnostics})

    @property
    def active(self):
        return self.collecting or self.downloading or self.queue.running or bool(self.capture or self.discovery_id or (self.scheduler and self.scheduler.hub.snapshot()))

    @property
    def discovery_id(self):
        return self.inventory.job_id

    def start_worker(self):
        self.store.recover_interrupted()
        self.worker = threading.Thread(target=self._watch_downloads, daemon=True, name='library-downloads')
        self.browser_worker = threading.Thread(target=self._watch_browser, daemon=True, name='library-inventory')
        self.worker.start()
        self.browser_worker.start()

    def restore_downloads(self):
        """Resume only what the user had running. Restarting never starts an inventory."""
        if self.store.setting('resume_downloads', False):
            self.start_downloads()
        if self.store.setting('resume_collecting', False):
            self.start_capture()

    def start_inventory(self):
        """Catalog pass only: adds series. It does not collect links or download."""
        if self.discovery_id or self.shutdown.is_set():
            return
        self.inventory.start()
        self.store.retry_unknown_series()
        self.note = 'جارٍ جرد المسلسلات الجديدة. الجرد لا يوقف التنزيل ولا يجمع الروابط.'
        self.browser_wakeup.set()
        self.changed()

    def start_capture(self):
        """Collect missing episode links of series already in the library (no catalog pass)."""
        if self.shutdown.is_set():
            return
        self.collecting = True
        self.store.save_setting('resume_collecting', True)
        self.note = 'جارٍ جمع روابط الحلقات الناقصة للمسلسلات المحفوظة. المقفلة في الحساب لا تُعاد تلقائيًا.'
        self.browser_wakeup.set()
        self.changed()

    def start_collection(self):
        """Inventory followed by link collection (kept for the fixtures and scripted runs)."""
        if self.collecting and self.discovery_id:
            return
        self.start_inventory()
        self.start_capture()

    def start_series(self, sid, page_url):
        """Download the series open in Edge: add it, collect its missing links, download.
        Only that series is captured; there is no catalog pass."""
        if self.shutdown.is_set():
            return False
        self.store.add_series([{'id': sid, 'title': 'Series', 'pageUrl': page_url}])
        self.focus.add(sid)
        self.store.save_setting('focus_series', sorted(self.focus))
        with self.store.db() as db:
            db.execute('UPDATE series SET next_scan=0,start_failures=0 WHERE id=?', (sid,))
        self.start_downloads()
        self.note = 'جارٍ جمع روابط المسلسل المفتوح وتنزيل المتاح منها؛ المحفوظ لا يُنزَّل مرة أخرى.'
        self.browser_wakeup.set()
        self.changed()
        return True

    def drop_focus(self, sid):
        if sid in self.focus:
            self.focus.discard(sid)
            self.store.save_setting('focus_series', sorted(self.focus))

    def start_downloads(self):
        with self.queue_lock:
            if self.downloading or self.shutdown.is_set():
                return
            self.saved_recheck = True
            self.prepared_downloads.clear()
            self.store.save_setting('resume_downloads', True)
            self.downloading = True
        self.note = 'جارٍ تنزيل الروابط المحفوظة في المكتبة.'
        self.wakeup.set()
        self.browser_wakeup.set()
        self.changed()

    def stop_collection(self):
        self.collecting = False
        if self.scheduler:
            self.scheduler.stop()
        self.inventory.cancel()
        if self.capture:
            self.bridge.command('stop')
        self.capture = None

    def stop(self):
        with self.queue_lock:
            self.downloading = False
        self.stop_collection()
        self.store.save_setting('resume_collecting', False)
        self.focus.clear()
        self.store.save_setting('focus_series', [])
        with self.queue_lock:
            self.queue.cancel()
            self.store.save_setting('resume_downloads', False)
        self.note = 'توقفت المكتبة. الروابط والملفات المحفوظة باقية للمواصلة.'
        self.changed()

    def changed(self):
        self.emit(('library_changed',))

    def on_snapshot(self, state):
        self.store.ingest(state)
        sid = state['seriesId']
        for ep in self.automatic.completed_episodes(sid):
            file = self.automatic.completed.get(sid + '/' + str(ep))
            if file:
                self.store.complete(sid, ep, file)
        self.changed()
        self.wakeup.set()
        self.browser_wakeup.set()

    def _open(self, series, renewal=None):
        if self.shutdown.is_set() or not (self.collecting or (self.downloading and renewal)):
            return
        now = time.time()
        page = series['page_url']
        self.capture = {'series': series, 'renewal': renewal, 'phase': 'opening', 'started': now, 'run': ''}
        state = self.bridge.status()
        if state['connected'] and state['page_url']:
            self.bridge.command('open', page_url=page)
        else:
            self.opener(page)
        self.next_open = now + 3
        self.note = ('تجديد رابط الحلقة ' + str(renewal['episode']) if renewal else 'جمع حلقات') + ' — ' + series['title']
        self.changed()

    def _capture_tick(self):
        if self.suspend_browser or self.shutdown.is_set() or time.time() < self.source_pause_until:
            return
        now = time.time()
        if (self.downloading or self.collecting) and now >= self.next_expiry_check:
            self.store.expire_due_sources()
            self.next_expiry_check = now + 15
        if self.discovery_id:
            note = self.inventory.poll()
            if note:
                self.note = note if self.collecting else note.split('.')[0] + '. اضغط «اجمع روابط الناقص» لجمع روابط الحلقات الجديدة.'
                self.changed()
            if self.discovery_id:
                return
        if self.scheduler:
            self.scheduler.tick()
            return
        if self.capture:
            job = self.capture
            series = job['series']
            state = self.bridge.status()
            if job['phase'] == 'opening':
                from .automatic import series_from_page
                if state['page_url'] and series_from_page(state['page_url']) == series['id'] and not state['pending_kind'] and now - job['started'] >= 1:
                    saved = self.automatic.recover_completed(series['id'], self.shutdown.is_set)
                    if self.capture is not job:
                        return
                    skipped = sorted(set(self.store.skip_sources(series['id'])) | set(saved))
                    target = [job['renewal']['episode']] if job['renewal'] else []
                    if target:
                        skipped = [n for n in skipped if n not in target]
                    job['run'] = self.bridge.command('start', series['id'], collect_only=True, collected_episodes=skipped, target_episodes=target)
                    job['phase'], job['started'] = 'scanning', now
                elif now - job['started'] > 40:
                    self._finish_capture('تعذر الاتصال بصفحة المسلسل؛ سيُعاد لاحقًا')
                return
            metadata = read_json(self.automatic.inbox / (series['id'] + '.json'), {})
            if metadata.get('run_id') == job['run'] and (metadata.get('finished') or metadata.get('paused')):
                if metadata.get('rate_limited'):
                    self.next_open = now + 60
                missing = series['total'] and len(self.store.skip_sources(series['id'])) < series['total']
                self._finish_capture('بعض الحلقات تحتاج إعادة فحص' if metadata.get('paused') or missing else '')
            elif state.get('command_error') or now - job['started'] > 1800:
                self.bridge.command('stop')
                self._finish_capture('تعذر إكمال هذه الجولة؛ سيُعاد الناقص لاحقًا')
            return
        if now < self.next_open:
            return
        if self.downloading:
            item = self.store.next_source('needs_refresh')
            if item:
                series = next((s for s in self.store.series() if s['id'] == item['sid']), None)
                if series:
                    self._open(series, item)
                    return
        if self.collecting:
            pending = self.store.pending_series()
            if pending:
                self._open(pending[0])
            else:
                if not self.store.has_work(download=False):
                    self.collecting = False
                    self.note = 'انتهى جمع الروابط المتاحة لهذه الجولة.'
                    self.changed()

    def _finish_capture(self, error):
        job, self.capture = self.capture, None
        if not job:
            return
        targets = [job['renewal']['episode']] if job['renewal'] else self.store.capture_targets(job['series'],150)
        self.store.finish_capture(job['series']['id'], targets, reason=error, next_scan=time.time()+2)
        self.next_open = max(self.next_open, time.time() + 2)
        self.changed()

    def tick(self):
        """Drive both workers synchronously for embedded callers and deterministic tests."""
        self._capture_tick()
        self._transfer_tick()

    def _reconcile_saved(self):
        """Saved episodes stay saved. A file that is no longer in the save folder is flagged
        for the user; it is downloaded again only through «أعد تنزيل الملفات المفقودة»."""
        for item in self.store.saved_episodes():
            if self.shutdown.is_set() or not self.downloading:
                return
            if self.automatic._completed_file(item['sid'], item['episode']):
                if item['error'] == 'saved_file_missing':
                    self.store.mark_file_present(item)
                continue
            path = Path(item['file'])
            try:
                if path.is_absolute() and path.is_file() and path.stat().st_size >= 128:
                    self.automatic.record_library_completion(item['sid'], item['episode'], path)
                    self.store.mark_file_present(item)
                    continue
                # An unplugged/moved destination is not evidence its files were deleted.
                if not path.is_absolute() or not path.resolve().is_relative_to(self.destination.resolve()) or not self.destination.is_dir():
                    continue
            except OSError:
                continue
            self.store.mark_file_missing(item)

    def _already_saved(self, item):
        sid, ep = item['sid'], item['episode']
        if sid not in self.prepared_downloads:
            for n in self.automatic.recover_completed(sid, self.shutdown.is_set):
                self.store.complete(sid, n, self.automatic.completed[f'{sid}/{n}'])
            self.prepared_downloads.add(sid)
        if self.automatic._completed_file(sid, ep):
            self.store.complete(sid, ep, self.automatic.completed[f'{sid}/{ep}'])
            return True
        return False

    def rate_limited(self):
        self.source_pause_until = max(self.source_pause_until, time.time() + 60)
        self.store.save_setting('source_pause_until', self.source_pause_until)

    def _transfer_tick(self):
        if self.shutdown.is_set() or not (self.downloading or self.collecting):
            return
        if self.saved_recheck:
            self.saved_recheck = False
            self._reconcile_saved()
        if time.time() < self.source_pause_until:
            return
        item = self.store.next_source('captured')
        if item:
            if self._already_saved(item):
                self.changed()
                return
            check = self.verifier.check(item['media_url'], item['page_url'], item['user_agent']) if item.get('user_agent') else self.verifier.check(item['media_url'], item['page_url'])
            if check.ok:
                self.store.update_source(item, 'ready', verified=check.checked_at)
            elif check.reason in ('connection_failed', 'rate_limited'):
                self.store.record_failure(item, check.reason, status='captured', next_try=time.time() + retry_delay(check.reason,item['attempts']+1))
                self.diagnostic(item,check.reason,phase='probe')
                if check.reason == 'rate_limited':
                    self.rate_limited()
                    return
            else:
                self.store.record_failure(item,check.reason,status='blocked' if check.reason in ('unsupported_source','protected_source','playlist_too_large') else 'needs_refresh',next_try=time.time()+retry_delay(check.reason,item['attempts']+1))
                self.note=f"{item['title']} — الحلقة {item['episode']}: {explanation(check.reason)}"
                self.emit(('library_error',self.note))
                self.diagnostic(item,check.reason,http_status={'http_forbidden':403,'http_unauthorized':401,'http_not_found':404}.get(check.reason,0),phase='probe')
            self.changed()
        if self.downloading and not self.queue.running:
            item = self.store.next_source('ready')
            if item:
                if self._already_saved(item):
                    return
                if item['expires'] and item['expires'] <= time.time() + 30 or time.time() - item['verified'] > 3600:
                    self.store.update_source(item, 'captured')
                    return
                with self.queue_lock:
                    if not self.downloading or self.shutdown.is_set():
                        self.active_item = None
                        return
                    if not self.store.update_source(item, 'downloading', verified=item['verified']):
                        return
                    self.active_item = {**item, 'status':'downloading'}
                    self.queue.start([Episode(item['sid'], item['title'], item['episode'], item['total'], item['page_url'], item['media_url'], 'playback_verified', 'media_found', item['source_type'], item['generation'], item.get('user_agent',''))], self.destination)
            elif (not self.collecting and not self.capture and not self.focus
                  and not (self.scheduler and self.scheduler.hub.snapshot()) and not self.store.has_work(download=True)):
                with self.queue_lock:
                    if self.downloading:
                        self.downloading = False
                        self.store.save_setting('resume_downloads', False)
                self.note = 'انتهى طابور الروابط المتاحة. تعرض المكتبة الحلقات المحفوظة والناقصة والمؤجلة.'
                self.changed()

    def _download_event(self, event):
        if event[0] == 'result' and self.active_item:
            item, self.active_item = self.active_item, None
            result = event[1]
            if result['status'] == 'مكتمل ومفحوص' and result.get('file'):
                self.store.complete(item['sid'], item['episode'], result['file'])
                self.automatic.record_library_completion(item['sid'], item['episode'], result['file'])
            elif not result['status'].startswith('متوقف'):
                reason=result.get('reason') or 'download_failed'
                if reason == 'rate_limited':
                    self.rate_limited()
                self.store.record_failure(item,reason,status='blocked' if reason=='protected_source' else 'captured' if reason in ('network_error','download_stalled','download_timeout','disk_full','write_error') else 'needs_refresh',next_try=time.time()+retry_delay(reason,item['attempts']+1))
                self.note=f"{item['title']} — الحلقة {item['episode']}: {explanation(reason)}"
                self.diagnostic(item,reason,http_status=result.get('http_status',0),detail=result.get('detail',''))
                self.emit(('library_error',self.note))
            else:
                self.store.update_source(item, 'ready', verified=item['verified'])
            self.changed()
            self.wakeup.set()
        elif event[0] == 'progress':
            self.emit(('library_progress', event[1], event[2]))
        elif event[0] == 'log':
            self.emit(('log', redact(event[1])))
        elif event[0] == 'done' and self.active_item:
            item, self.active_item = self.active_item, None
            self.store.record_failure(item, 'download_failed', status='captured', next_try=time.time()+retry_delay('download_failed',item['attempts']+1))
            self.changed()

    def _watch_downloads(self):
        try:
            self.store.migrate(self.automatic.inbox)
        except (OSError, ValueError, sqlite3.Error) as error:
            self.emit(('log', redact(str(error))))
        finally:
            self.migrated.set()
        self._run_worker(self._transfer_tick, self.wakeup)

    def _watch_browser(self):
        self.migrated.wait()
        self._run_worker(self._capture_tick, self.browser_wakeup)

    def _run_worker(self, tick, wakeup):
        while not self.shutdown.is_set():
            wakeup.wait(0.6)
            wakeup.clear()
            if self.shutdown.is_set():
                break
            try:
                tick()
            except (OSError, ValueError, sqlite3.Error) as error:
                self.emit(('log', redact(str(error))))

    def close(self):
        # Explicit Stop clears resume intent; closing the window preserves it.
        self.shutdown.set()
        self.wakeup.set()
        self.browser_wakeup.set()
        if self.scheduler:
            self.scheduler.stop()
        self.collecting = False
        with self.queue_lock:
            self.downloading = False
            self.queue.cancel()
        for worker in (self.worker, self.browser_worker):
            if worker:
                worker.join(timeout=8)

    def pause_for_exit(self):
        self.shutdown.set()
        self.wakeup.set()
        self.browser_wakeup.set()
        self.stop_collection()
        with self.queue_lock:
            self.downloading = False
            self.queue.cancel()
