"""Fair, bounded capture batches; blocked episodes cannot monopolize every tab."""
import time


class SessionScheduler:
    # A tab needs a page load, playback proof and HLS matching per episode.
    PER_TARGET = 45
    BASE = 90
    PROGRESS_TIMEOUT = 150
    BARREN_LIMIT = 6
    FOCUS_BATCH = 10

    def __init__(self, engine, hub, *, slots=3, batch=6, job_timeout=None, idle_timeout=45):
        self.engine, self.hub = engine, hub
        self.slots, self.batch = min(3,max(1,slots)), batch
        self.job_timeout, self.idle_timeout = job_timeout, idle_timeout
        self.not_before = self.last_open = 0
        self.prepared = set()
        self.order = {}
        self.barren = 0
        self.pause_level = 0

    def stop(self):
        self.hub.stop()
        self.order.clear()

    def timeout_for(self, job):
        return self.job_timeout or self.BASE + self.PER_TARGET * max(1, len(job['targets']))

    def finish(self, job, reason=''):
        self.hub.retire(job['slot'])
        targets = job['targets'] or list(range(1,self.batch+1))
        result = self.engine.store.finish_capture(job['seriesId'], targets, reason=reason, next_scan=time.time()+1,
            tried=job.get('tried', targets), outcomes=job.get('outcomes'),
            paid_start=job.get('paidStart', 0), locked_from=job.get('lockedFrom', 0)) or {}
        if reason != 'rate_limited':
            self._record(job, result)
        self.engine.changed()

    def _record(self, job, result):
        """Stop opening tabs when several runs in a row produce nothing at all."""
        if job.get('sources') or result.get('locked'):
            self.barren = self.pause_level = 0
            return
        self.barren += 1
        if self.barren >= self.BARREN_LIMIT:
            self.barren = 0
            pause = min(3600, 600 * 2 ** self.pause_level)
            self.pause_level = min(self.pause_level + 1, 3)
            self.not_before = max(self.not_before, time.time() + pause)
            self.engine.note = (f'توقف فتح التبويبات {pause // 60} دقيقة: {self.BARREN_LIMIT} جولات متتالية لم يبدأ فيها تشغيل أي حلقة. '
                                'تأكد أن Edge مسجّل الدخول ويعرض الحلقات، ثم استخدم «فحص / إعادة الربط». التنزيل مستمر.')

    def _capture_current(self, sid, jobs, now, generation):
        """One series at a time, in one visible tab, until every episode is handled."""
        engine = self.engine
        if jobs:
            return
        series = next((s for s in engine.store.series() if s['id'] == sid), None)
        if not series or series['next_scan'] > now:
            return
        if sid not in self.prepared:
            for ep in engine.automatic.recover_completed(sid, lambda: engine.shutdown.is_set() or engine.current != sid):
                engine.store.complete(sid, ep, engine.automatic.completed[sid + '/' + str(ep)])
            self.prepared.add(sid)
        targets = engine.store.capture_targets(series, self.FOCUS_BATCH)
        if not targets or engine.shutdown.is_set() or engine.suspend_browser or engine.current != sid:
            return
        rows = {e['episode']: e for e in engine.store.episodes(sid)}
        page = rows.get(targets[0], {}).get('page_url') or series['page_url']
        if not self.hub.assign(1, series, targets=targets, skipped=engine.store.skip_sources(sid), page_url=page,
                               expected_generation=generation, foreground=True):
            return
        self.order[sid] = now
        engine.note = f"جارٍ فحص حلقات «{series['title']}» من الحلقة {targets[0]} في تبويب Edge الظاهر؛ يبدأ التنزيل مع وصول كل رابط مؤكد."
        if not engine.bridge.status()['connected'] and now - self.last_open > 20:
            self.last_open = now
            engine.opener(page)
        engine.changed()

    def tick(self):
        engine, now = self.engine, time.time()
        generation=self.hub.generation
        if engine.suspend_browser or engine.shutdown.is_set():
            return
        for job in self.hub.snapshot():
            if job['error']=='rate_limited':
                self.not_before = now+60
                engine.rate_limited()
                for active in self.hub.snapshot():
                    self.finish(active,'rate_limited')
                engine.note = 'الموقع طلب مهلة. ستُستأنف الطلبات تلقائيًا بعد دقيقة.'
                return
            if job['finished'] or job['paused'] or job['error']:
                self.finish(job,job['error'] or ('capture_paused' if job['paused'] else ''))
            elif (now-job['started']>self.timeout_for(job) or now-(job['seen'] or job['started'])>self.idle_timeout
                  or now-(job['updated'] or job['started'])>self.PROGRESS_TIMEOUT):
                self.finish(job,'capture_timeout')
        if not (engine.collecting or engine.downloading) or now<self.not_before:
            return
        jobs = self.hub.snapshot()
        active_series = {j['seriesId'] for j in jobs}
        occupied = {j['slot'] for j in jobs}
        if len(occupied) >= self.slots:
            return
        current = getattr(engine, 'current', None)
        if current:
            self._capture_current(current, jobs, now, generation)
            return
        renewal_only = not engine.collecting
        if renewal_only and not engine.store.has_renewals():
            return
        # Renewals of links that already failed come before new episodes.
        candidates = sorted(engine.store.series(),key=lambda s:(0 if s['refresh'] else 1,self.order.get(s['id'],0),s['discovered'],s['id']))
        for slot in range(1,self.slots+1):
            if slot in occupied:
                continue
            chosen = None
            for series in candidates:
                if series['id'] in active_series or series['next_scan']>now:
                    continue
                only_renew = renewal_only
                if only_renew and not series['refresh']:
                    continue
                if series['total'] and not series['capture_due']:
                    continue
                targets = engine.store.capture_targets(series, self.batch, renewal_only=only_renew)
                if targets:
                    chosen = series,targets
                    break
            if not chosen:
                break
            series,targets = chosen
            if series['id'] not in self.prepared:
                for ep in engine.automatic.recover_completed(series['id'],lambda:engine.shutdown.is_set() or engine.suspend_browser or not (engine.collecting or engine.downloading)):
                    engine.store.complete(series['id'],ep,engine.automatic.completed[series['id']+'/'+str(ep)])
                self.prepared.add(series['id'])
                targets = engine.store.capture_targets(series, self.batch, renewal_only=only_renew)
                if not targets:
                    continue
            rows = {e['episode']:e for e in engine.store.episodes(series['id'])}
            page = rows.get(targets[0],{}).get('page_url') or series['page_url']
            if engine.shutdown.is_set() or engine.suspend_browser or not (engine.collecting or engine.downloading):
                return
            assigned=self.hub.assign(slot,series,targets=targets,skipped=engine.store.skip_sources(series['id']),page_url=page,expected_generation=generation)
            if not assigned:
                return
            self.order[series['id']]=now
            active_series.add(series['id'])
            engine.note = f'جمع الروابط: {len(self.hub.snapshot())} تبويبات مستقلة؛ التنزيل يعمل بشكل منفصل.'
            if not engine.bridge.status()['connected'] and now-self.last_open>20:
                self.last_open=now
                engine.opener(page)
            engine.changed()
        if engine.collecting and not self.hub.snapshot() and not engine.store.has_work(download=False):
            engine.collecting=False
            engine.store.save_setting('resume_collecting', False)
