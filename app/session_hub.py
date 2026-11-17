"""Authenticated per-tab leases. A late/wrong session cannot overwrite another series."""
import re
import secrets
import threading
import time

from .automatic import series_from_page


class SessionHub:
    LIMIT = 3

    def __init__(self, inbox, on_snapshot):
        self.inbox, self.on_snapshot = inbox, on_snapshot
        self.lock = threading.RLock()
        self.jobs = {}
        self.paused = False

    def assign(self, slot, series, *, targets=(), skipped=(), page_url=''):
        if slot not in range(1, self.LIMIT+1) or not re.fullmatch(r'[a-f0-9]{24}', series['id']):
            raise ValueError('Invalid session')
        page = page_url or series['page_url']
        if series_from_page(page) != series['id']:
            raise ValueError('Invalid session page')
        with self.lock:
            if slot in self.jobs:
                raise ValueError('Session slot occupied')
            if any(j['seriesId'] == series['id'] for j in self.jobs.values()):
                raise ValueError('Series already assigned')
            job = {'id': secrets.token_hex(16), 'slot': slot, 'seriesId': series['id'], 'title': series['title'],
                   'pageUrl': page, 'targets': sorted(set(targets)), 'skipped': sorted(set(skipped)),
                   'started': time.time(), 'seen': 0, 'updated': 0, 'tabId': None, 'current': 0,
                   'finished': False, 'paused': False, 'error': '', 'sources': 0}
            self.jobs[slot] = job
            return dict(job)

    def snapshot(self):
        with self.lock:
            return [dict(j) for j in self.jobs.values()]

    def retire(self, slot):
        with self.lock:
            return self.jobs.pop(slot, None)

    def stop(self):
        with self.lock:
            self.jobs.clear()

    def receive(self, path, body):
        if path == '/sessions/poll':
            return 200, {'sessions': [] if self.paused else self.snapshot()}
        with self.lock:
            job = next((j for j in self.jobs.values() if j['id'] == body.get('sessionId')), None)
            tab = body.get('tabId')
            if not job or self.paused or type(tab) is not int or tab < 1 or (job['tabId'] is not None and job['tabId'] != tab):
                return 409, {'error': 'stale_or_wrong_tab_session'}
            if body.get('seriesId') != job['seriesId']:
                return 409, {'error': 'wrong_series_session'}
            job['tabId'], job['seen'] = tab, time.time()
            if path == '/sessions/heartbeat':
                return 200, {'session': dict(job)}
            if path != '/sessions/snapshot':
                return 404, {'error': 'not_found'}
            state = body.get('state')
            if not isinstance(state, dict) or state.get('seriesId') != job['seriesId'] or state.get('desktopCommandId') != job['id'] or state.get('channel') != job['id']:
                return 409, {'error': 'wrong_snapshot_session'}
            try:
                self.inbox.save(state, job['id'])
                self.on_snapshot(state)
            except (ValueError, TypeError):
                job['error'] = 'invalid_episode_index'
                return 422, {'error': job['error']}
            job.update(updated=time.time(), current=state.get('current', 0), total=state.get('total', 0),
                       finished=bool(state.get('finished')), paused=bool(state.get('paused')),
                       sources=sum(bool(r.get('mediaUrl')) and r.get('playbackStatus') == 'playback_verified' for r in state.get('rows', {}).values() if isinstance(r, dict)),
                       error='rate_limited' if state.get('rateLimited') else '')
            return 200, {'accepted': True}
