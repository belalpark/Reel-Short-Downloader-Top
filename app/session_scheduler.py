"""Fair, bounded capture batches; blocked episodes cannot monopolize every tab."""
import time


class SessionScheduler:
    def __init__(self, engine, hub, *, slots=3, batch=6, job_timeout=180, idle_timeout=45):
        self.engine, self.hub = engine, hub
        self.slots, self.batch = min(3,max(1,slots)), batch
        self.job_timeout, self.idle_timeout = job_timeout, idle_timeout
        self.not_before = self.last_open = 0
        self.prepared = set()
        self.order = {}

    def stop(self):
        self.hub.stop()
        self.order.clear()

    def finish(self, job, reason=''):
        self.hub.retire(job['slot'])
        targets = job['targets'] or list(range(1,self.batch+1))
        self.engine.store.finish_capture(job['seriesId'],targets,reason=reason,next_scan=time.time()+1)
        self.engine.changed()

    def tick(self):
        engine, now = self.engine, time.time()
        generation=self.hub.generation
        if engine.suspend_browser or engine.shutdown.is_set():
            return
        for job in self.hub.snapshot():
            if job['error']=='rate_limited':
                self.not_before = now+60
                for active in self.hub.snapshot():
                    self.finish(active,'rate_limited')
                engine.note = 'الموقع طلب مهلة دقيقة؛ تنزيل الروابط الجاهزة مستمر.'
                return
            if job['finished'] or job['paused'] or job['error']:
                self.finish(job,job['error'] or ('capture_paused' if job['paused'] else ''))
            elif now-job['started']>self.job_timeout or now-(job['seen'] or job['started'])>self.idle_timeout:
                self.finish(job,'capture_timeout')
        if not (engine.collecting or engine.downloading) or now<self.not_before:
            return
        jobs = self.hub.snapshot()
        active_series = {j['seriesId'] for j in jobs}
        occupied = {j['slot'] for j in jobs}
        for slot in range(1,self.slots+1):
            if slot in occupied:
                continue
            candidates = sorted(engine.store.series(),key=lambda s:(self.order.get(s['id'],0),s['discovered'],s['id']))
            chosen = None
            for series in candidates:
                if series['id'] in active_series or series['next_scan']>now:
                    continue
                if series['total'] and not series['capture_due']:
                    continue
                targets = engine.store.capture_targets(series,self.batch)
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
                targets = engine.store.capture_targets(series,self.batch)
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
            engine.note = f'السحب التلقائي: {len(self.hub.snapshot())} تبويبات مستقلة؛ التنزيل يبدأ عند وصول كل رابط صالح.'
            if not engine.bridge.status()['connected'] and now-self.last_open>20:
                self.last_open=now
                engine.opener(page)
            engine.changed()
        if not self.hub.snapshot():
            series = engine.store.series()
            if series and all(s['total'] and s['known_sources']>=s['total'] for s in series):
                engine.collecting=False
