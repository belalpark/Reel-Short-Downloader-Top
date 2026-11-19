"""Durable series/episode identities and replaceable, locally private media sources."""
from contextlib import contextmanager
import json
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import unquote, urlsplit

from .catalog import load_catalog
from .failures import MAX_ATTEMPTS
from .media_check import source_expiry, trusted_source

SID = re.compile(r'^[a-f0-9]{24}$')
# Catalog links carry button/shelf labels ("تشغيل: …", "الأحدث"). They are not series titles.
TITLE_PREFIX = re.compile(r'^\s*(?:تشغيل|شاهد(?:\s+الآن)?|play(?:\s+now)?|watch(?:\s+now)?)\s*[:：\-–]\s*', re.I)
GENERIC_TITLES = {'series', 'عنوان غير معلن', 'الأحدث', 'الأكثر شهرة', 'الأكثر مشاهدة', 'الأكثر رواجًا',
                  'رائج', 'جديد', 'تشغيل', 'شاهد الآن', 'new', 'newest', 'latest', 'popular', 'trending',
                  'hot', 'play', 'play now', 'watch now'}
# Navigation results that mean the site did not open the episode for this account.
ACCESS_BLOCKED = ('manual_open_required', 'access_unavailable')
CAPTURE_OPEN = ('missing', 'needs_refresh')
NEVER_PLAYED_LIMIT = 12


def clean_title(value):
    title = TITLE_PREFIX.sub('', re.sub(r'\s+', ' ', str(value or '').replace('\xa0', ' ')).strip()).strip()[:250]
    return '' if len(title) < 2 or title.casefold() in GENERIC_TITLES else title


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

            if 'retry_status' not in columns:
                db.execute("ALTER TABLE episodes ADD COLUMN retry_status TEXT NOT NULL DEFAULT ''")
            series_columns = {r[1] for r in db.execute('PRAGMA table_info(series)')}
            for name in ('scan_failures', 'paid_start', 'locked_from', 'start_failures'):
                if name not in series_columns:
                    db.execute(f"ALTER TABLE series ADD COLUMN {name} INTEGER NOT NULL DEFAULT 0")
            db.execute('CREATE INDEX IF NOT EXISTS episode_series ON episodes(sid,status)')
            # 0.3-0.5 charged an attempt to every target of a timed-out run, reached or not.
            # Those counts are not evidence about the episode; start them again once.
            if not db.execute("SELECT 1 FROM settings WHERE key='capture_budget_v6'").fetchone():
                db.execute("""UPDATE episodes SET attempts=0,next_try=0,error='' WHERE status IN ('missing','needs_refresh')
                    AND error IN ('capture_timeout','capture_paused')""")
                db.execute("INSERT OR REPLACE INTO settings VALUES('capture_budget_v6','true')")
            # One-time-safe title repair for catalog labels saved by 0.3-0.5.
            for sid, title in db.execute('SELECT id,title FROM series').fetchall():
                fixed = clean_title(title) or 'Series'
                if fixed != title:
                    db.execute('UPDATE series SET title=? WHERE id=?', (fixed, sid))

    def count_series(self):
        with self.db() as db:
            return db.execute('SELECT COUNT(*) FROM series').fetchone()[0]

    def has_renewals(self):
        with self.db() as db:
            return bool(db.execute("""SELECT 1 FROM episodes e JOIN series s ON s.id=e.sid
                WHERE e.status='needs_refresh' AND e.next_try<=? AND e.episode<=s.total LIMIT 1""",
                (time.time(),)).fetchone())

    def expire_due_sources(self):
        with self.db() as db:
            db.execute("""UPDATE episodes SET status='needs_refresh',error='source_expired',verified=0
                WHERE expires>0 AND expires<=? AND status IN ('ready','captured')""", (time.time()+30,))

    def has_work(self, *, download):
        with self.db() as db:
            if download:
                return bool(db.execute("""SELECT 1 FROM episodes e JOIN series s ON s.id=e.sid
                    WHERE e.status IN ('ready','captured','downloading','needs_refresh')
                    AND e.episode<=s.total LIMIT 1""").fetchone())
            return bool(db.execute("""SELECT 1 FROM series WHERE total=0 AND scan_failures<? UNION ALL
                SELECT 1 FROM episodes e JOIN series s ON s.id=e.sid WHERE e.episode<=s.total AND
                e.status IN ('missing','needs_refresh','captured') LIMIT 1""",(MAX_ATTEMPTS,)).fetchone())

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
            title = clean_title(item.get('title', '')) or 'Series'
            checked.append((sid, title, page, time.time()))
        with self.db() as db:
            before = db.total_changes
            db.executemany("INSERT INTO series(id,title,page_url,discovered) VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=CASE WHEN excluded.title<>'Series' AND (series.total=0 OR series.title='Series') THEN excluded.title ELSE series.title END, page_url=CASE WHEN series.page_url LIKE '%/movie/%' AND excluded.page_url NOT LIKE '%/movie/%' THEN series.page_url ELSE excluded.page_url END", checked)
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
            title = clean_title(state.get('title'))
            paid = state.get('paidStart')
            paid = paid if type(paid) is int and 1 <= paid <= total else 0
            db.execute("""UPDATE series SET total=?,scan_failures=0,
                paid_start=CASE WHEN ?>0 THEN ? ELSE paid_start END,
                title=CASE WHEN ?='' THEN title ELSE ? END WHERE id=?""",
                (total,paid,paid,title,title,sid))
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
                if row.get('playbackStatus') == 'hls_drm_protected' or row.get('status') == 'hls_drm_protected':
                    db.execute("""UPDATE episodes SET status='blocked',error='protected_source',media_url='',
                        generation='',verified=0,next_try=0,retry_status=''
                        WHERE sid=? AND episode=? AND status NOT IN ('done','downloading')""", (sid,n))
                    continue
                media = str(row.get('mediaUrl', ''))
                generation = str(row.get('captureId') or row.get('observedAt') or '')[:100]
                if not media or row.get('playbackStatus') != 'playback_verified' or not trusted_source(media) or not generation:
                    continue
                # A new capture ID alone must not restart a verified or in-flight transfer.
                db.execute("""UPDATE episodes SET media_url=?,source_type=?,generation=?,user_agent=?,
                    status='captured',observed=?,verified=0,expires=?,error='',retry_status='',
                    next_try=CASE WHEN media_url<>? THEN 0 ELSE next_try END
                    WHERE sid=? AND episode=? AND status NOT IN ('done','downloading')
                    AND (media_url<>? OR (status='needs_refresh' AND generation<>?))""",
                    (media, str(row.get('sourceType', ''))[:60], generation,
                     str(state.get('browserUserAgent', ''))[:300], now, source_expiry(media),
                     media, sid, n, media, generation))

    def series(self):
        with self.db() as db:
            return [dict(r) for r in db.execute('''SELECT s.*,
              COALESCE(SUM(e.status='done'),0) done,
              COALESCE(SUM(e.status IN ('ready','downloading') AND (e.expires=0 OR e.expires>?)),0) ready,
              COALESCE(SUM(e.status='captured'),0) checking,
              COALESCE(SUM(e.status='needs_refresh' OR (e.status IN ('captured','ready') AND e.expires>0 AND e.expires<=?)),0) refresh,
              COALESCE(SUM(e.status='missing'),0) missing,
              COALESCE(SUM(e.status='held'),0) held,
              COALESCE(SUM(e.status='blocked'),0) blocked,
              COALESCE(SUM(e.status='locked'),0) locked,
              COALESCE(SUM(e.status='done' AND e.error='saved_file_missing'),0) file_missing,
              COALESCE(MIN(CASE WHEN e.status NOT IN ('done','blocked','held','locked') AND e.next_try>? THEN e.next_try END),0) retry_at,
              COALESCE(SUM(e.status='done' OR (e.media_url<>'' AND e.status IN ('captured','ready','downloading') AND (e.expires=0 OR e.expires>?))),0) known_sources,
              COALESCE(SUM(e.status NOT IN ('done','blocked','held','locked','downloading') AND e.next_try<=? AND (e.media_url='' OR e.status='needs_refresh' OR (e.expires<>0 AND e.expires<=?))),0) capture_due
              FROM series s LEFT JOIN episodes e ON e.sid=s.id AND e.episode<=s.total
              GROUP BY s.id ORDER BY s.discovered,s.id''', (time.time()+30,time.time()+30,time.time(),time.time()+30,time.time(),time.time()+30))]

    def episodes(self, sid):
        with self.db() as db:
            return [dict(r) for r in db.execute('SELECT e.* FROM episodes e JOIN series s ON s.id=e.sid WHERE e.sid=? AND e.episode<=s.total ORDER BY e.episode', (sid,))]

    def skip_sources(self, sid):
        with self.db() as db:
            return [r[0] for r in db.execute('''SELECT e.episode FROM episodes e JOIN series s ON s.id=e.sid
                WHERE e.sid=? AND e.episode<=s.total AND (e.status='done' OR
                  (e.media_url<>'' AND e.status IN ('captured','ready','downloading') AND (e.expires=0 OR e.expires>?)))''', (sid, time.time()+30))]

    def capture_targets(self, series, limit=6, *, renewal_only=False):
        if not series['total']:
            if renewal_only or series['scan_failures'] >= MAX_ATTEMPTS:
                return []
            return list(range(1, limit + 1))
        with self.db() as db:
            return [r[0] for r in db.execute("""SELECT episode FROM episodes WHERE sid=? AND episode<=?
                AND next_try<=? AND (status='needs_refresh' OR (status='missing' AND ?=0)
                OR (status IN ('captured','ready') AND expires>0 AND expires<=?))
                ORDER BY CASE WHEN status='missing' THEN 1 ELSE 0 END,episode LIMIT ?""",
                (series['id'], series['total'], time.time(), renewal_only, time.time()+30, limit))]

    def record_failure(self, item, reason, *, status='needs_refresh', next_try=0):
        with self.db() as db:
            return db.execute("""UPDATE episodes SET
                status=CASE WHEN ?!='blocked' AND attempts+1>=? THEN 'held' ELSE ? END,
                retry_status=?,error=?,attempts=attempts+1,next_try=?
                WHERE sid=? AND episode=? AND generation=? AND status=? AND status NOT IN ('done','held','blocked','locked')""",
                (status, MAX_ATTEMPTS, status, status, reason[:100], next_try,
                 item['sid'], item['episode'], item['generation'], item['status'])).rowcount

    def finish_capture(self, sid, targets, *, reason='', next_scan=0, tried=None, outcomes=None,
                       paid_start=0, locked_from=0):
        """Close one capture run. Only episodes the tab actually reached spend an attempt.

        `tried=None` keeps the pre-0.6 contract (every target was attempted). A paid
        episode the account cannot open marks the rest of the paid range as `locked`
        once, instead of spending three tab runs on every locked episode."""
        now = time.time()
        targets = [n for n in targets if type(n) is int]
        reached = set(targets) if tried is None else {n for n in tried if type(n) is int}
        outcomes = outcomes or {}
        locked = 0
        with self.db() as db:
            series = db.execute('SELECT total,paid_start FROM series WHERE id=?', (sid,)).fetchone()
            if not series:
                return {'locked': 0, 'reached': 0}
            total = series['total']
            if type(paid_start) is int and 0 < paid_start <= max(total, 1):
                db.execute('UPDATE series SET paid_start=? WHERE id=?', (paid_start, sid))
            paid = paid_start or series['paid_start']
            playable = db.execute("""SELECT 1 FROM episodes WHERE sid=? AND (status='done' OR
                (media_url<>'' AND status IN ('captured','ready','downloading'))) LIMIT 1""", (sid,)).fetchone()
            boundary = 0
            if reason != 'rate_limited' and playable and total:
                open_rows = {r[0]: r[1] for r in db.execute(
                    f"SELECT episode,attempts FROM episodes WHERE sid=? AND status IN {CAPTURE_OPEN}", (sid,))}
                failed = sorted(n for n in reached if n in open_rows and not outcomes.get(n, {}).get('ok'))
                blocked = [n for n in failed if outcomes.get(n, {}).get('blocked')]
                if paid:
                    paid_failed = [n for n in failed if n >= paid]
                    earlier = [n for n, tries in open_rows.items() if n >= paid and tries > 0 and n not in reached]
                    if type(locked_from) is int and locked_from >= paid:
                        boundary = locked_from
                    elif any(n >= paid for n in blocked):
                        boundary = min(n for n in blocked if n >= paid)
                    elif len(paid_failed) + len(earlier) >= 2 and paid_failed:
                        boundary = min(paid_failed + earlier)
                else:
                    # Without the site's paid marker, require two consecutive episodes whose
                    # selection did not open, after this series already played for the account.
                    boundary = next((a for a, b in zip(blocked, blocked[1:]) if b == a + 1), 0)
            if boundary:
                locked = db.execute(f"""UPDATE episodes SET retry_status=status,status='locked',
                    error='account_access_required',next_try=0
                    WHERE sid=? AND episode>=? AND episode<=? AND status IN {CAPTURE_OPEN}""",
                    (sid, boundary, total)).rowcount
                db.execute('UPDATE series SET locked_from=? WHERE id=?', (boundary, sid))
            for n in targets:
                row = db.execute('SELECT status,attempts,next_try FROM episodes WHERE sid=? AND episode=?', (sid, n)).fetchone()
                if not row or row['status'] not in CAPTURE_OPEN or row['next_try'] > now:
                    continue
                if reason == 'rate_limited':
                    db.execute("UPDATE episodes SET error='rate_limited',next_try=? WHERE sid=? AND episode=?", (now + 60, sid, n))
                elif n in reached:
                    delay = min(300, 30 * 2 ** min(4, row['attempts']))
                    db.execute("""UPDATE episodes SET attempts=attempts+1,error=?,next_try=?,retry_status=status,
                        status=CASE WHEN attempts+1>=? THEN 'held' ELSE status END WHERE sid=? AND episode=?""",
                        (reason or 'source_not_captured', now + delay, MAX_ATTEMPTS, sid, n))
                else:
                    # The tab never got to this episode (slow pages, stop or timeout): no penalty.
                    db.execute("UPDATE episodes SET error='capture_not_reached',next_try=? WHERE sid=? AND episode=?",
                               (now + 60, sid, n))
            if not playable and reached and reason != 'rate_limited':
                failures = db.execute(f"""SELECT COUNT(*) FROM episodes WHERE sid=? AND attempts>0
                    AND status IN {CAPTURE_OPEN + ('held',)}""", (sid,)).fetchone()[0]
                if failures >= NEVER_PLAYED_LIMIT:
                    # Nothing in this series has ever played for the account: stop walking it.
                    db.execute(f"""UPDATE episodes SET retry_status=status,status='held',error='series_not_playing',next_try=0
                        WHERE sid=? AND status IN {CAPTURE_OPEN}""", (sid,))
            started = bool(reached) or reason == 'rate_limited'
            db.execute("""UPDATE series SET scanned=?,
                next_scan=CASE WHEN total=0 THEN MAX(?, ?+60*(scan_failures+1))
                               WHEN ? THEN ? ELSE ?+MIN(1800,120*(1<<MIN(start_failures,4))) END,
                scan_failures=CASE WHEN total=0 THEN scan_failures+1 ELSE 0 END,
                start_failures=CASE WHEN ? THEN 0 ELSE start_failures+1 END,error=? WHERE id=?""",
                (now, next_scan, now, started, next_scan, now, started, reason[:100], sid))
        return {'locked': locked, 'reached': len(reached & set(targets))}

    def retry_held(self):
        with self.db() as db:
            db.execute("""UPDATE episodes SET status=CASE WHEN retry_status='missing' THEN 'needs_refresh' ELSE retry_status END,
                attempts=0,next_try=0,retry_status=''
                WHERE status='held' AND retry_status IN ('missing','needs_refresh','captured')""")

    def retry_locked(self):
        """After the user unlocks episodes on the site, check each series' paid range once more."""
        with self.db() as db:
            count = db.execute("""UPDATE episodes SET status=CASE WHEN retry_status='needs_refresh' THEN 'needs_refresh' ELSE 'missing' END,
                attempts=0,next_try=0,retry_status='',error='' WHERE status='locked'""").rowcount
            db.execute('UPDATE series SET locked_from=0,start_failures=0,next_scan=0')
            return count

    def mark_file_missing(self, item):
        with self.db() as db:
            db.execute("UPDATE episodes SET error='saved_file_missing' WHERE sid=? AND episode=? AND status='done'",
                       (item['sid'], item['episode']))

    def mark_file_present(self, item):
        with self.db() as db:
            db.execute("UPDATE episodes SET error='' WHERE sid=? AND episode=? AND status='done' AND error='saved_file_missing'",
                       (item['sid'], item['episode']))

    def requeue_missing_files(self):
        """Explicit user action only: saved episodes are never downloaded again automatically."""
        with self.db() as db:
            return db.execute("""UPDATE episodes SET status=CASE WHEN media_url<>'' THEN 'captured' ELSE 'needs_refresh' END,
                error='',attempts=0,next_try=0,verified=0 WHERE status='done' AND error='saved_file_missing'""").rowcount

    def export_index(self, path):
        """Write a readable library index without media URLs or session data."""
        import csv
        labels = {'done': 'محفوظ', 'ready': 'رابط جاهز', 'captured': 'رابط قيد الفحص', 'downloading': 'جارٍ التنزيل',
                  'needs_refresh': 'يحتاج تجديد الرابط', 'missing': 'بلا رابط', 'held': 'معلّق بعد تكرار التعذر',
                  'locked': 'مقفلة في الحساب', 'blocked': 'محمي أو غير مدعوم'}
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        with self.db() as db, open(temporary, 'w', encoding='utf-8-sig', newline='') as handle:
            out = csv.writer(handle)
            out.writerow(['SeriesID', 'Title', 'Episode', 'TotalEpisodes', 'Status', 'HasLink', 'LinkVerified',
                          'File', 'EpisodePage', 'Note'])
            for r in db.execute("""SELECT s.id,s.title,s.total,e.episode,e.status,e.media_url<>'' link,e.verified,
                    e.file,COALESCE(NULLIF(e.page_url,''),s.page_url) page,e.error FROM series s
                    JOIN episodes e ON e.sid=s.id AND e.episode<=s.total ORDER BY s.title,s.id,e.episode"""):
                title = r['title']
                if re.match(r'^[\s]*[=+@-]', title):
                    title = "'" + title
                note = 'الملف غير موجود في مجلد الحفظ' if r['error'] == 'saved_file_missing' else ''
                out.writerow([r['id'], title, r['episode'], r['total'], labels.get(r['status'], r['status']),
                              'نعم' if r['link'] else 'لا',
                              time.strftime('%Y-%m-%d %H:%M', time.localtime(r['verified'])) if r['verified'] else '',
                              r['file'] if r['status'] == 'done' else '', r['page'], note])
        temporary.replace(path)
        return path

    def retry_unknown_series(self):
        with self.db() as db:
            db.execute('UPDATE series SET next_scan=0,scan_failures=0 WHERE total=0 AND scan_failures>=?', (MAX_ATTEMPTS,))

    def next_source(self, status):
        with self.db() as db:
            row = db.execute('''SELECT e.*,s.title,s.total,s.page_url series_page FROM episodes e
                JOIN series s ON s.id=e.sid WHERE e.status=? AND e.next_try<=? AND e.episode<=s.total
                ORDER BY e.next_try,s.discovered,e.episode LIMIT 1''', (status, time.time())).fetchone()
        return dict(row) if row else None

    def update_source(self, item, status, *, error='', verified=0, next_try=0):
        with self.db() as db:
            return db.execute("""UPDATE episodes SET status=?,error=?,verified=?,next_try=?
                WHERE sid=? AND episode=? AND generation=? AND status=?""",
                (status,error[:100],verified,next_try,item['sid'],item['episode'],item['generation'],item['status'])).rowcount

    def complete(self, sid, episode, file):
        with self.db() as db:
            db.execute("""UPDATE episodes SET status='done',file=?,error='',retry_status='',next_try=0,attempts=0
                WHERE sid=? AND episode=? AND (status<>'done' OR file<>?)""",(str(file),sid,episode,str(file)))

    def saved_episodes(self):
        with self.db() as db:
            return [dict(r) for r in db.execute("SELECT * FROM episodes WHERE status='done'")]

    def pending_series(self):
        now = time.time()
        return [s for s in self.series() if s['next_scan'] <= now and ((not s['total'] and s['scan_failures']<MAX_ATTEMPTS) or s['capture_due'])]

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
