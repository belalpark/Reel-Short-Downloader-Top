"""Separate browser collection, source checking and a restartable offline queue."""
from pathlib import Path
import sqlite3
import threading
import time

from .catalog import Episode
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
        self.discovery_id = ''
        self.capture = None
        self.active_item = None
        self.note = 'المكتبة جاهزة. اجمع الروابط ثم نزّلها.'
        self.resume_collection = False
        self.next_open = 0
        self.queue_lock = threading.RLock()
        self.discovery_started = 0
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

    def start_all(self):
        if self.active:
            return
        self.store.release_deferred()
        self.start_collection()
        self.start_downloads()
        self.wakeup.set()

    def start_worker(self):
        self.store.recover_interrupted()
        self.worker = threading.Thread(target=self._watch, daemon=True, name='series-library')
        self.worker.start()

    def restore_downloads(self):
        if self.store.setting('resume_downloads', False):
            self.start_downloads()

    def start_collection(self):
        if self.collecting or self.capture or self.discovery_id:
            return
        self.collecting = True
        self.discovery_id = self.bridge.command('catalog', page_url='https://www.reelshort.com/ar')
        self.discovery_started = time.time()
        self.opener('https://www.reelshort.com/ar')
        self.note = 'اترك تبويب Edge مفتوحًا لجمع الفهرس، ثم حلقات المسلسلات الجديدة والناقصة.'
        self.changed()

    def start_downloads(self):
        self.saved_recheck = True
        with self.queue_lock:
            self.downloading = True
        self.store.save_setting('resume_downloads', True)
        series = self.store.series()
        if not series:
            self.start_collection()
        elif any(not s['total'] or s['known_sources'] < s['total'] for s in series):
            self.collecting = True
        self.note = 'تنزيل المكتبة يعمل. يمكن إغلاق Edge ما دامت الروابط صالحة؛ يفتح لتجديد رابط متعذر عند الحاجة.'
        self.changed()

    def stop_collection(self):
        self.collecting = False
        if self.scheduler:
            self.scheduler.stop()
        if self.capture or self.discovery_id:
            self.bridge.command('stop')
        self.capture, self.discovery_id = None, ''

    def stop(self):
        self.stop_collection()
        with self.queue_lock:
            self.downloading = False
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
        if self.suspend_browser:
            return
        now = time.time()
        if self.discovery_id:
            progress = self.bridge.catalog_status()
            if progress.get('id') == self.discovery_id and progress.get('done'):
                self.discovery_id = ''
                self.note = 'حُفظ الجزء المتاح من الفهرس؛ تعذر إكمال بعض الصفحات. جارٍ جمع المحفوظ.' if progress.get('error') else 'حُفظ فهرس المسلسلات. جارٍ جمع روابط الحلقات الجديدة والناقصة.'
                self.changed()
            elif now - max(self.discovery_started, progress.get('updated', 0) if progress.get('id') == self.discovery_id else 0) > 180:
                self.bridge.command('stop')
                self.discovery_id = ''
                self.note = 'تعذر إكمال الفهرس؛ المحفوظ باقٍ. استخدم إعادة الربط والفحص.'
                self.changed()
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
                self.note = 'انتهت الجولة الحالية. المكتمل لا يُعاد جمعه؛ يعاد فحص الناقص بعد المهلة.'
                series = self.store.series()
                if series and all(s['total'] and len(self.store.skip_sources(s['id'])) >= s['total'] for s in series):
                    self.collecting = False
                    self.note = 'حُفظت روابط الجولة. يمكن متابعة تنزيل المكتبة دون إبقاء Edge مفتوحًا ما دامت صالحة.'
                    self.changed()

    def _finish_capture(self, error):
        job, self.capture = self.capture, None
        if not job:
            return
        self.store.scan_finished(job['series']['id'], error)
        renewal = job['renewal']
        if renewal:
            # Conditional generation prevents a late failure from invalidating a fresh capture.
            self.store.update_source(renewal, 'needs_refresh', error='refresh_pending', next_try=time.time() + 300)
        self.next_open = max(self.next_open, time.time() + 2)
        self.changed()

    def tick(self):
        if self.shutdown.is_set():
            return
        if self.saved_recheck:
            self.saved_recheck = False
            for saved in self.store.incomplete_saved():
                if not self.automatic._completed_file(saved['sid'], saved['episode']):
                    self.store.update_source(saved, 'captured' if saved['media_url'] else 'needs_refresh', error='saved_file_missing')
        self._capture_tick()
        item = self.store.next_source('captured')
        if item:
            if self.automatic._completed_file(item['sid'], item['episode']):
                self.store.complete(item['sid'], item['episode'], self.automatic.completed[item['sid'] + '/' + str(item['episode'])])
                self.changed()
                return
            check = self.verifier.check(item['media_url'], item['page_url'], item['user_agent']) if item.get('user_agent') else self.verifier.check(item['media_url'], item['page_url'])
            if check.ok:
                self.store.update_source(item, 'ready', verified=check.checked_at)
            elif check.reason in ('connection_failed', 'rate_limited'):
                self.store.update_source(item, 'captured', error=check.reason, next_try=time.time() + (60 if check.reason == 'rate_limited' else 30))
                self.diagnostic(item,check.reason,phase='probe')
            else:
                self.store.record_failure(item,check.reason,status='blocked' if check.reason in ('unsupported_source','protected_source') else 'needs_refresh',next_try=time.time()+retry_delay(check.reason,item['attempts']+1))
                self.note=f"{item['title']} — الحلقة {item['episode']}: {explanation(check.reason)}"
                self.emit(('library_error',self.note))
                self.diagnostic(item,check.reason,http_status={'http_forbidden':403,'http_unauthorized':401,'http_not_found':404}.get(check.reason,0),phase='probe')
            self.changed()
        if self.downloading and not self.queue.running:
            item = self.store.next_source('ready')
            if item:
                if self.automatic._completed_file(item['sid'], item['episode']):
                    self.store.complete(item['sid'], item['episode'], self.automatic.completed[item['sid'] + '/' + str(item['episode'])])
                    return
                if item['expires'] and item['expires'] <= time.time() + 30 or time.time() - item['verified'] > 3600:
                    self.store.update_source(item, 'captured')
                    return
                self.active_item = item
                with self.queue_lock:
                    if not self.downloading or self.shutdown.is_set():
                        self.active_item = None
                        return
                    self.store.update_source(item, 'downloading', verified=item['verified'])
                    self.queue.start([Episode(item['sid'], item['title'], item['episode'], item['total'], item['page_url'], item['media_url'], 'playback_verified', 'media_found', item['source_type'], item['generation'], item.get('user_agent',''))], self.destination)
            elif not self.collecting and not self.capture and not self.discovery_id:
                series = self.store.series()
                if series and all(s['total'] and s['done'] == s['total'] for s in series):
                    self.downloading = False
                    self.store.save_setting('resume_downloads', False)
                    self.note = 'اكتمل تنزيل المكتبة وفحص ملفاتها.'
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
            self.store.update_source(item, 'captured', error='download_retry', next_try=time.time() + 10)
            self.changed()

    def _watch(self):
        try:
            self.store.migrate(self.automatic.inbox)
            for series in self.store.series():
                for ep in self.automatic.recover_completed(series['id'], self.shutdown.is_set):
                    self.store.complete(series['id'], ep, self.automatic.completed[series['id'] + '/' + str(ep)])
        except (OSError, ValueError, sqlite3.Error) as error:
            self.emit(('log', redact(str(error))))
        while not self.shutdown.is_set():
            self.wakeup.wait(0.6)
            self.wakeup.clear()
            try:
                self.tick()
            except (OSError, ValueError, sqlite3.Error) as error:
                self.emit(('log', redact(str(error))))

    def close(self):
        # Explicit Stop clears resume intent; closing the window preserves it.
        self.shutdown.set()
        self.wakeup.set()
        if self.scheduler:
            self.scheduler.stop()
        self.collecting = False
        with self.queue_lock:
            self.downloading = False
            self.queue.cancel()
        if self.worker:
            self.worker.join(timeout=8)

    def pause_for_exit(self):
        self.stop_collection()
        self.shutdown.set()
        with self.queue_lock:
            self.downloading = False
            self.queue.cancel()
