"""Durable series/episode identities and replaceable, locally private media sources."""
from contextlib import contextmanager
import json
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import unquote, urlsplit

from .catalog import Episode, load_catalog
from .media_check import source_expiry, trusted_source

SID = re.compile(r'^[a-f0-9]{24}$')


def series_page(url, sid=''):
    try:
        u = urlsplit(url)
        if u.scheme != 'https' or u.netloc != 'www.reelshort.com':
            return ''
        path = unquote(u.path)
        found = re.search(r'(?:^|[-/])([a-f0-9]{24})(?:[-/]|$)', path, re.I)
        if not found or (sid and found[1].lower() != sid):
            return ''
        if not re.search(r'/(?:movie|full-episodes)/|/episodes/episode-\d+(?:[-/]|$)', path):
            return ''
        return 'https://www.reelshort.com' + u.path
    except (ValueError, TypeError):
        return ''


class LibraryStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS series(
                id TEXT PRIMARY KEY, title TEXT NOT NULL, page_url TEXT NOT NULL,
                total INTEGER NOT NULL DEFAULT 0, discovered REAL NOT NULL,
                scanned REAL NOT NULL DEFAULT 0, next_scan REAL NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT '');
              CREATE TABLE IF NOT EXISTS episodes(
                sid TEXT NOT NULL, episode INTEGER NOT NULL, page_url TEXT NOT NULL DEFAULT '',
                media_url TEXT NOT NULL DEFAULT '', source_type TEXT NOT NULL DEFAULT '',
                generation TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'missing',
                observed REAL NOT NULL DEFAULT 0, verified REAL NOT NULL DEFAULT 0,
                expires REAL NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
                next_try REAL NOT NULL DEFAULT 0, file TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '', PRIMARY KEY(sid,episode),
                FOREIGN KEY(sid) REFERENCES series(id));
              CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
              CREATE INDEX IF NOT EXISTS episode_work ON episodes(status,next_try);
            ''')
            columns = {r[1] for r in db.execute('PRAGMA table_info(episodes)')}
            if 'user_agent' not in columns:
                db.execute("ALTER TABLE episodes ADD COLUMN user_agent TEXT NOT NULL DEFAULT ''")

    def recover_interrupted(self):
        with self.db() as db:
            db.execute("UPDATE episodes SET status='captured' WHERE status='downloading'")

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.path, timeout=8)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA journal_mode=WAL')
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def setting(self, key, default=None):
        with self.db() as db:
            row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def save_setting(self, key, value):
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (key, json.dumps(value)))

    def add_series(self, items):
        if not isinstance(items, list) or len(items) > 200:
            raise ValueError('Invalid library batch')
        checked = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError('Invalid library item')
            sid = str(item.get('id', '')).lower()
            page = series_page(str(item.get('pageUrl', '')), sid)
            if not SID.fullmatch(sid) or not page:
                raise ValueError('Invalid library series')
            title = str(item.get('title', '')).strip()[:250] or 'Series'
            checked.append((sid, title, page, time.time()))
        with self.db() as db:
            before = db.total_changes
            db.executemany("INSERT INTO series(id,title,page_url,discovered) VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=CASE WHEN series.total=0 OR series.title='Series' THEN excluded.title ELSE series.title END, page_url=CASE WHEN series.page_url LIKE '%/movie/%' AND excluded.page_url NOT LIKE '%/movie/%' THEN series.page_url ELSE excluded.page_url END", checked)
            return db.total_changes - before

    def ingest(self, state):
        sid, total = str(state.get('seriesId', '')), state.get('total')
        rows = state.get('rows', {})
        if not SID.fullmatch(sid) or type(total) is not int or not 1 <= total <= 150 or not isinstance(rows, dict):
            raise ValueError('Invalid library snapshot')
        with self.db() as db:
            existing = db.execute('SELECT id FROM series WHERE id=?', (sid,)).fetchone()
        if not existing:
            first = next((series_page(str(r.get('pageUrl', '')), sid) for r in rows.values() if isinstance(r, dict) and series_page(str(r.get('pageUrl', '')), sid)), '')
            if not first:
                return
            self.add_series([{'id': sid, 'title': state.get('title', 'Series'), 'pageUrl': first}])
        now = time.time()
        with self.db() as db:
            db.execute('UPDATE series SET total=?, title=? WHERE id=?', (total, str(state.get('title', 'Series'))[:250], sid))
            landing = series_page(str(state.get('seriesPageUrl', '')), sid)
            if landing:
                db.execute('UPDATE series SET page_url=? WHERE id=?', (landing, sid))
            for n in range(1, total + 1):
                row = rows.get(str(n), {})
                if not isinstance(row, dict):
                    continue
                page = series_page(str(row.get('pageUrl', '')), sid)
                if page and not re.search(rf'/episodes/episode-{n}(?:[-/]|$)', urlsplit(page).path):
                    page = ''
                db.execute('INSERT OR IGNORE INTO episodes(sid,episode,page_url) VALUES(?,?,?)', (sid, n, page))
                if page:
                    db.execute('UPDATE episodes SET page_url=? WHERE sid=? AND episode=?', (page, sid, n))
                media = str(row.get('mediaUrl', ''))
                generation = str(row.get('captureId') or row.get('observedAt') or '')[:100]
                if not media or row.get('playbackStatus') != 'playback_verified' or not trusted_source(media) or not generation:
                    continue
                db.execute('''UPDATE episodes SET media_url=?,source_type=?,generation=?,user_agent=?,
                    status=CASE WHEN status='done' THEN status ELSE 'captured' END,
                    observed=?,verified=0,expires=?,error='',next_try=CASE WHEN media_url<>? THEN 0 ELSE next_try END
                    WHERE sid=? AND episode=? AND (generation<>? OR media_url<>?)''',
                    (media, str(row.get('sourceType', ''))[:60], generation, str(state.get('browserUserAgent', ''))[:300], now, source_expiry(media), media, sid, n, generation, media))

    def series(self):
        with self.db() as db:
            return [dict(r) for r in db.execute('''SELECT s.*,
              COALESCE(SUM(e.status='done'),0) done,
              COALESCE(SUM(e.status IN ('ready','downloading') AND (e.expires=0 OR e.expires>?)),0) ready,
              COALESCE(SUM(e.status='captured'),0) checking,
              COALESCE(SUM(e.status='needs_refresh'),0) refresh,
              COALESCE(MIN(CASE WHEN e.status NOT IN ('done','blocked') AND e.next_try>? THEN e.next_try END),0) retry_at,
              COALESCE(SUM(e.status='done' OR (e.media_url<>'' AND e.status IN ('captured','ready','downloading') AND (e.expires=0 OR e.expires>?))),0) known_sources,
              COALESCE(SUM(e.status NOT IN ('done','blocked','downloading') AND e.next_try<=? AND (e.media_url='' OR e.status='needs_refresh' OR (e.expires<>0 AND e.expires<=?))),0) capture_due
              FROM series s LEFT JOIN episodes e ON e.sid=s.id AND e.episode<=s.total
              GROUP BY s.id ORDER BY s.discovered,s.id''', (time.time()+30,time.time(),time.time()+30,time.time(),time.time()+30))]

    def episodes(self, sid):
        with self.db() as db:
            return [dict(r) for r in db.execute('SELECT * FROM episodes WHERE sid=? ORDER BY episode', (sid,))]

    def skip_sources(self, sid):
        with self.db() as db:
            return [r[0] for r in db.execute('''SELECT e.episode FROM episodes e JOIN series s ON s.id=e.sid
                WHERE e.sid=? AND e.episode<=s.total AND (e.status='done' OR
                  (e.media_url<>'' AND e.status IN ('captured','ready','downloading') AND (e.expires=0 OR e.expires>?)))''', (sid, time.time()+30))]

    def capture_targets(self, series, limit=6):
        if not series['total']:
            return list(range(1, limit+1))
        skipped = set(self.skip_sources(series['id']))
        return [e['episode'] for e in self.episodes(series['id']) if e['episode']<=series['total'] and
                e['episode'] not in skipped and e['status']!='blocked' and e['next_try']<=time.time()][:limit]

    def record_failure(self, item, reason, *, status='needs_refresh', next_try=0):
        with self.db() as db:
            db.execute("UPDATE episodes SET status=?,error=?,attempts=attempts+1,next_try=? WHERE sid=? AND episode=? AND generation=? AND status<>'done'",
                       (status,reason[:100],next_try,item['sid'],item['episode'],item['generation']))

    def finish_capture(self, sid, targets, *, reason='', next_scan=0):
        skipped = set(self.skip_sources(sid))
        with self.db() as db:
            for n in targets:
                if n in skipped:
                    continue
                row = db.execute('SELECT * FROM episodes WHERE sid=? AND episode=?',(sid,n)).fetchone()
                if row and row['status'] not in ('done','blocked'):
                    delay = 60 if reason=='rate_limited' else min(300,30*2**min(4,row['attempts']))
                    db.execute("UPDATE episodes SET attempts=attempts+1,error=?,next_try=MAX(next_try,?) WHERE sid=? AND episode=?",
                               (reason or 'source_not_captured',time.time()+delay,sid,n))
            db.execute('UPDATE series SET scanned=?,next_scan=?,error=? WHERE id=?',
                       (time.time(),next_scan,reason[:100],sid))

    def release_deferred(self):
        with self.db() as db:
            db.execute("UPDATE episodes SET next_try=0 WHERE status NOT IN ('done','blocked','downloading') AND error<>'rate_limited'")
            db.execute('UPDATE series SET next_scan=0')

    def next_source(self, status):
        with self.db() as db:
            row = db.execute('''SELECT e.*,s.title,s.total,s.page_url series_page FROM episodes e
                JOIN series s ON s.id=e.sid WHERE e.status=? AND e.next_try<=? AND e.episode<=s.total
                ORDER BY e.next_try,s.discovered,e.episode LIMIT 1''', (status, time.time())).fetchone()
        return dict(row) if row else None

    def update_source(self, item, status, *, error='', verified=0, next_try=0):
        with self.db() as db:
            db.execute('UPDATE episodes SET status=?,error=?,verified=?,next_try=? WHERE sid=? AND episode=? AND generation=?',
                (status, error[:100], verified, next_try, item['sid'], item['episode'], item['generation']))

    def complete(self, sid, episode, file):
        with self.db() as db:
            db.execute("UPDATE episodes SET status='done',file=?,error='' WHERE sid=? AND episode=?", (str(file), sid, episode))

    def incomplete_saved(self):
        with self.db() as db:
            return [dict(r) for r in db.execute("SELECT * FROM episodes WHERE status='done'")]

    def scan_finished(self, sid, error=''):
        with self.db() as db:
            db.execute('UPDATE series SET scanned=?,next_scan=?,error=? WHERE id=?', (time.time(), time.time() + (300 if error else 60), error[:150], sid))

    def pending_series(self):
        now = time.time()
        return [s for s in self.series() if s['next_scan'] <= now and (not s['total'] or len(self.skip_sources(s['id'])) < s['total'])]

    def migrate(self, inbox):
        if self.setting('inbox_imported', False):
            return
        for path in Path(inbox).glob('*.csv'):
            try:
                entries = load_catalog(path)
                self.ingest({'seriesId': entries[0].series_id, 'title': entries[0].title, 'total': entries[0].total,
                    'rows': {str(e.episode): {'pageUrl': e.page_url, 'mediaUrl': e.media_url,
                    'playbackStatus': e.playback, 'sourceType': e.source_type,
                    'captureId': e.capture_id or 'legacy-' + str(path.stat().st_mtime_ns)} for e in entries}})
            except (OSError, ValueError):
                continue
        self.save_setting('inbox_imported', True)
