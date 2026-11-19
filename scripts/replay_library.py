"""Developer tool: replay a copy of a real library DB through the checked-out engine with a simulated
browser, clock and downloader. Usage: python -I scripts/replay_library.py REPO COPY_OF_library.sqlite3 MODE [DAYS] [new|old]
(new = 5.7 agent: reports reached episodes and stops at the first unopened paid episode)
MODE = download  (press "download from library" only)
       collect   (press "inventory & collect" then download)
Prints counters as JSON. Never touches the network."""
import json, shutil, sys, tempfile, time as _time
from pathlib import Path
from unittest.mock import Mock, patch

repo, db_src, mode = sys.argv[1], sys.argv[2], sys.argv[3]
days = float(sys.argv[4]) if len(sys.argv) > 4 else 7
new_agent = len(sys.argv) > 5 and sys.argv[5] == 'new'
sys.path.insert(0, repo)

clock = [1795000000.0]
def now():
    return clock[0]

with patch('time.time', now):
    import sqlite3
    from app.automatic import AutomaticDownloads
    from app.bridge import Bridge
    from app.library_engine import LibraryEngine
    from app.library_store import LibraryStore
    from app.media_check import SourceCheck
    from app.paths import write_json

    tmp = Path(tempfile.mkdtemp())
    db = tmp / 'library.sqlite3'
    shutil.copy(db_src, db)
    # Site model derived from the user's data: series whose later episodes failed are
    # treated as paywalled after the last captured episode (account not entitled).
    raw = sqlite3.connect(db)
    paid = {}
    for sid, total in raw.execute('SELECT id,total FROM series'):
        got = [r[0] for r in raw.execute("SELECT episode FROM episodes WHERE sid=? AND (media_url<>'' OR status='done')", (sid,))]
        bad = [r[0] for r in raw.execute("SELECT episode FROM episodes WHERE sid=? AND error IN ('capture_timeout','source_not_captured')", (sid,))]
        paid[sid] = (max(got) + 1) if got and bad else (1 if bad and not got else (total or 60) + 1)
    raw.close()
    store = LibraryStore(db)
    write_json(tmp / 'connect.json', {'token': 'x' * 48, 'extension_id': 'a' * 32})
    bridge = Bridge(tmp / 'connect.json', inbox=tmp / 'Inbox', library=store)
    dest = tmp / 'Videos'
    auto = AutomaticDownloads(tmp, tmp / 'Inbox', dest, tmp / 'data', lambda _: None)
    opener = Mock()
    verifier = Mock()
    verifier.check.side_effect = lambda *a, **k: SourceCheck(True, 'verified_hls', now())
    engine = LibraryEngine(store, bridge, auto, tmp, tmp / 'data', dest, lambda _: None,
                           verifier=verifier, opener=opener, session_hub=bridge.sessions)
    bridge.snapshot_handler = engine.on_snapshot
    bridge.status = lambda: {'connected': True, 'page_url': 'https://www.reelshort.com/ar', 'pending_kind': '', 'command_error': ''}
    bridge.catalog_status = lambda: {'id': bridge.catalog_job_id, 'done': True, 'updated': now()}
    stats = {'catalog_commands': 0, 'tab_sessions': 0, 'targets_sent': 0, 'locked_attempts': 0,
             'free_captured': 0, 'downloads': 0, 'redownloads_of_done': 0}
    real_command = bridge.command
    def command(kind, *a, **k):
        if kind == 'catalog':
            stats['catalog_commands'] += 1
        return real_command(kind, *a, **k)
    bridge.command = command

    done_before = {(r['sid'], r['episode']) for r in store.saved_episodes()}
    # Make the user's saved episodes exist on disk, as they do on the user's PC.
    for r in store.saved_episodes():
        f = dest / f"S_{r['sid']}" / f"Episode_{r['episode']:03d}.mp4"
        f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b'\0' * 256)
        auto.record_library_completion(r['sid'], r['episode'], f)
        with store.db() as c:
            c.execute('UPDATE episodes SET file=? WHERE sid=? AND episode=?', (str(f), r['sid'], r['episode']))

    queue = Mock(running=False)
    def start(entries, destination):
        for item in entries:
            key = (item.series_id, item.episode)
            stats['downloads'] += 1
            if key in done_before:
                stats['redownloads_of_done'] += 1
            f = destination / f'S_{item.series_id}' / f'Episode_{item.episode:03d}.mp4'
            f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b'\0' * 256)
            engine._download_event(('result', {'series_id': item.series_id, 'episode': item.episode,
                                    'status': 'مكتمل ومفحوص', 'file': str(f), 'reason': ''}))
    queue.start.side_effect = start
    engine.queue = queue

    handled = {}
    def browser():
        """Answer each leased tab after a realistic 15 s per target."""
        for job in bridge.sessions.snapshot():
            if job['id'] in handled:
                bridge.sessions.receive('/sessions/heartbeat', {'sessionId': job['id'], 'seriesId': job['seriesId'], 'tabId': job['slot']})
                if now() >= handled[job['id']]['at'] and not handled[job['id']]['sent']:
                    handled[job['id']]['sent'] = True
                    sid = job['seriesId']; p = paid.get(sid, 999)
                    total = next((s['total'] for s in store.series() if s['id'] == sid), 0) or 60
                    rows = {}
                    locked_from = 0
                    for ep in job['targets']:
                        if new_agent and locked_from:
                            break
                        page = f'https://www.reelshort.com/ar/episodes/episode-{ep}-x-{sid}'
                        if ep < p:
                            stats['free_captured'] += 1
                            rows[str(ep)] = {'episode': ep, 'pageUrl': page, 'playbackStatus': 'playback_verified',
                                             'status': 'media_found', 'sourceType': 'playback_hls',
                                             'mediaUrl': f'https://v-mps.crazymaplestudios.com/vod-1/{sid}/{ep}/{job["id"][:6]}.m3u8',
                                             'captureId': job['id'] + str(ep), 'observedAt': 'x'}
                        else:
                            stats['locked_attempts'] += 1
                            rows[str(ep)] = {'episode': ep, 'pageUrl': page, 'playbackStatus': 'not_checked',
                                             'status': 'manual_open_required', 'sourceType': '', 'mediaUrl': '',
                                             'captureId': '', 'observedAt': 'x'}
                            if new_agent and p <= total:
                                locked_from = ep
                        rows[str(ep)]['tried'] = True
                    state = {'seriesId': sid, 'title': job['title'], 'total': total, 'current': total + 1,
                             'rows': rows, 'desktopCommandId': job['id'], 'channel': job['id'],
                             'running': False, 'finished': True, 'paused': False, 'rateLimited': False,
                             'paidStart': p if p <= total else 0, 'browserUserAgent': 'UA', 'lockedFrom': locked_from}
                    bridge.sessions.receive('/sessions/heartbeat', {'sessionId': job['id'], 'seriesId': sid, 'tabId': job['slot']})
                    code, body = bridge.sessions.receive('/sessions/snapshot', {'sessionId': job['id'], 'seriesId': sid, 'tabId': job['slot'], 'state': state})
                    if code != 200:
                        stats.setdefault('snapshot_errors', []).append(body)
                continue
            stats['tab_sessions'] += 1
            stats['targets_sent'] += len(job['targets'])
            handled[job['id']] = {'at': now() + 15 * len(job['targets']), 'sent': False}
            bridge.sessions.receive('/sessions/heartbeat', {'sessionId': job['id'], 'seriesId': job['seriesId'], 'tabId': job['slot']})

    engine.store.recover_interrupted()
    if hasattr(engine, '_watch_downloads'):
        engine.store.migrate(auto.inbox)
    if mode == 'collect':
        engine.start_collection()
    engine.start_downloads()
    end = now() + days * 86400
    steps = 0
    while now() < end:
        steps += 1
        before = (stats['free_captured'], stats['downloads'])
        engine.tick()
        browser()
        if (stats['free_captured'], stats['downloads']) != before:
            stats['last_useful_hour'] = round((now() - 1795000000) / 3600, 1)
        if getattr(engine.scheduler, 'not_before', 0) > now() + 300:
            stats['breaker_pause_steps'] = stats.get('breaker_pause_steps', 0) + 1
        if not engine.active:
            break
        clock[0] += 5
    counts = dict(store_status=dict(sqlite3.connect(db).execute('SELECT status,COUNT(*) FROM episodes GROUP BY status').fetchall()))
    stats.update(counts, simulated_hours=round((now() - 1795000000) / 3600, 1), still_active=engine.active,
                 opener_calls=opener.call_count)
    stats["held_rows"]=[dict(r) for r in sqlite3.connect(db).execute("SELECT sid,episode,status,retry_status,attempts,error,media_url<>'' FROM episodes WHERE status='held' LIMIT 5").fetchall()] if False else [list(r) for r in sqlite3.connect(db).execute("SELECT sid,episode,status,retry_status,attempts,error,media_url<>'' FROM episodes WHERE status='held' LIMIT 5").fetchall()]
    print(json.dumps(stats, ensure_ascii=False, indent=1))
