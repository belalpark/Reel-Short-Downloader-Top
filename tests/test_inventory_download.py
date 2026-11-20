"""Regression cases for independent inventory, bounded renewal and local completion."""
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.automatic import AutomaticDownloads
from app.bridge import Bridge
from app.library_engine import LibraryEngine
from app.library_store import LibraryStore
from app.media_check import MediaVerifier, SourceCheck
from app.paths import write_json
from app.ui import DesktopApp


def catalog_entry(n):
    sid = f'{n:024x}'
    return {'id': sid, 'title': f'Series {n}', 'pageUrl': f'https://www.reelshort.com/ar/movie/fixture-{sid}'}


def series(n, sources, total=3):
    book = catalog_entry(n)
    return {'seriesId': book['id'], 'title': book['title'], 'total': total, 'rows': {
        str(ep): {'pageUrl': f'https://www.reelshort.com/ar/episodes/episode-{ep}-fixture-{book["id"]}',
                  'mediaUrl': f'https://v-mps.crazymaplestudios.com/{n}/{ep}.mp4' if ep in sources else '',
                  'captureId': f'capture-{ep}', 'playbackStatus': 'playback_verified' if ep in sources else 'not_checked'}
        for ep in range(1, total+1)}}


class InventoryDownloadTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.store = LibraryStore(self.root/'library.sqlite3')
        write_json(self.root/'connect.json', {'token':'x'*48,'extension_id':'a'*32})
        self.bridge = Bridge(self.root/'connect.json', inbox=self.root/'Inbox', library=self.store)
        self.auto = AutomaticDownloads(self.root, self.root/'Inbox', self.root/'Videos', self.root/'data', lambda _:None)
        self.open = Mock()
        self.verifier = Mock()
        self.verifier.check.return_value = SourceCheck(True,'verified_video',time.time())
        self.engine = LibraryEngine(self.store, self.bridge, self.auto, self.root, self.root/'data',
                                    self.auto.destination, lambda _:None, verifier=self.verifier,
                                    opener=self.open, session_hub=self.bridge.sessions)
        self.engine.queue = Mock(running=False)

    def seed(self, n, sources=(), total=3):
        state = series(n,sources,total)
        self.store.ingest(state)
        return state['seriesId']

    def test_gui_download_and_restart_never_issue_catalog_even_with_unknown_series(self):
        self.seed(1,[1])
        self.store.add_series([catalog_entry(2)])
        # 0.7: without an open series, button 3 downloads the library one complete series
        # at a time. Still no catalog pass, and only the current series is opened.
        ui = Mock(library_engine=self.engine, library_store=self.store, automatic=self.auto, preparing=False)
        ui._library_ready.return_value = True
        ui.bridge.status.return_value = {'connected': True, 'page_url': 'https://www.reelshort.com/ar'}
        ui.download_sequence = lambda: DesktopApp.download_sequence(ui)
        DesktopApp.download_library(ui)
        self.engine.tick()
        self.engine.queue.start.assert_called_once()
        self.assertEqual(self.engine.queue.start.call_args.args[0][0].episode,1)
        first = self.engine.current
        self.assertFalse(self.engine.collecting)
        self.assertEqual({j['seriesId'] for j in self.bridge.sessions.snapshot()}, {first})
        self.assertEqual(self.bridge.catalog_job_id,'')
        self.assertTrue(all(first in str(c.args[0]) for c in self.open.call_args_list))
        self.engine.downloading = False
        self.engine.restore_downloads()
        self.assertTrue(self.engine.downloading)
        self.assertEqual(self.bridge.catalog_job_id,'')

    def test_empty_or_never_captured_library_returns_to_idle_without_opening_edge(self):
        for count in (0, 1):
            if count:
                self.seed(1)
            self.engine.start_downloads()
            self.engine.tick()
            self.assertFalse(self.engine.active)
            self.assertFalse(self.store.setting('resume_downloads'))
            self.assertEqual(self.bridge.catalog_job_id,'')
        self.open.assert_not_called()
        self.engine.queue.start.assert_not_called()

    def test_expired_row_refresh_targets_only_its_episode(self):
        state = series(1,[2])
        state['rows']['2']['mediaUrl'] += '?expires=1000000001'
        self.store.ingest(state)
        self.seed(2)
        self.store.add_series([catalog_entry(3)])
        self.engine.start_downloads()
        self.engine.tick()
        jobs=self.bridge.sessions.snapshot()
        self.assertEqual([(j['seriesId'],j['targets'],j['pageUrl']) for j in jobs],
                         [(state['seriesId'],[2],state['rows']['2']['pageUrl'])])
        self.assertEqual(self.bridge.catalog_job_id,'')
        self.verifier.check.assert_not_called()
        self.engine.queue.start.assert_not_called()

    def test_no_progress_uses_persistent_budget_and_manual_retry_only_releases_held(self):
        sid=self.seed(1, [1,2])
        for index in range(3):
            item=self.store.episodes(sid)[0]
            self.store.record_failure(item,'not_direct_vod',next_try=time.time()+30)
            same=series(1,[1,2])
            same['rows']['1']['captureId']=f'capture-again-{index}'
            self.store.ingest(same)
        reopened=LibraryStore(self.store.path)
        before=reopened.episodes(sid)
        self.assertEqual((before[0]['status'],before[0]['attempts']),('held',3))
        self.engine.start_downloads()
        self.assertEqual(reopened.episodes(sid)[0]['status'],'held')
        reopened.retry_held()
        after=reopened.episodes(sid)
        self.assertEqual((after[0]['status'],after[0]['attempts']),('needs_refresh',0))
        self.assertEqual(before[1:],after[1:])

    def test_unknown_series_and_missing_episodes_have_finite_capture_budgets(self):
        missing=self.seed(1)
        item=catalog_entry(2)
        self.store.add_series([item])
        for attempt in range(3):
            for sid in (missing,item['id']):
                self.store.finish_capture(sid,[1,2,3],reason='capture_timeout')
            with self.store.db() as db:
                db.execute('UPDATE episodes SET next_try=0')
                db.execute('UPDATE series SET next_scan=0')
        self.assertFalse(self.store.has_work(download=False))
        self.engine.collecting=True
        self.engine.scheduler.tick()
        self.assertFalse(self.engine.collecting)
        self.assertFalse(self.bridge.sessions.snapshot())
        self.assertEqual(self.store.series()[1]['scan_failures'],3)

    def test_changed_observation_during_transfer_cannot_submit_twice_or_erase_done(self):
        state=series(1,[1],1)
        self.store.ingest(state)
        self.engine.start_downloads()
        self.engine.tick()
        old=self.engine.active_item
        self.assertEqual(old['status'],'downloading')
        state['rows']['1']['mediaUrl']+='?rotated=1'
        state['rows']['1']['captureId']='new'
        self.store.ingest(state)
        self.assertEqual(self.store.episodes(old['sid'])[0]['generation'],old['generation'])
        self.store.complete(old['sid'],1,'saved.webm')
        self.assertFalse(self.store.update_source(old,'ready'))
        self.assertFalse(self.store.record_failure(old,'network_error'))
        self.assertEqual(self.store.episodes(old['sid'])[0]['status'],'done')

    def test_only_sqlite_knows_saved_file_without_ledger_or_browser(self):
        sid=self.seed(1,[1],1)
        self.auto.destination.mkdir()
        saved=self.auto.destination/'old-name.webm'
        saved.write_bytes(b'x'*512)
        self.store.complete(sid,1,str(saved))
        self.engine.start_downloads()
        self.engine.tick()
        self.assertTrue(self.auto._completed_file(sid,1))
        self.assertFalse(self.engine.active)
        self.assertEqual(self.store.episodes(sid)[0]['file'],str(saved))
        self.verifier.check.assert_not_called()
        self.engine.queue.start.assert_not_called()
        self.open.assert_not_called()

    def test_explicit_inventory_can_block_while_the_download_worker_advances(self):
        sid=self.seed(1,[1],1)
        entered,release,queued=threading.Event(),threading.Event(),threading.Event()
        def slow_poll():
            entered.set()
            release.wait(5)
            return {'id':'unused','done':False}
        self.bridge.catalog_status=slow_poll
        self.engine.queue.start.side_effect=lambda *_:queued.set()
        self.engine.start_worker()
        try:
            self.engine.start_collection()
            self.assertTrue(entered.wait(3))
            self.engine.start_downloads()
            self.assertTrue(queued.wait(3))
            self.assertEqual(self.engine.active_item['sid'],sid)
            self.assertFalse(release.is_set())
        finally:
            release.set()
            self.engine.close()

    def test_protection_signal_removes_stale_html_fallback_and_never_requeues(self):
        state=series(1,[1],1)
        self.store.ingest(state)
        state['rows']['1']['status']='hls_drm_protected'
        self.store.ingest(state)
        self.store.retry_held()
        self.engine.start_downloads()
        self.engine.tick()
        self.assertEqual(self.store.episodes(state['seriesId'])[0]['status'],'blocked')
        self.open.assert_not_called()
        self.verifier.check.assert_not_called()
        self.engine.queue.start.assert_not_called()

    def test_long_clear_vod_playlist_checks_end_with_bounded_read_and_no_subrequests(self):
        url='https://v-mps.crazymaplestudios.com/fixture/index.m3u8'
        raw=b'#EXTM3U\n'+b'#EXTINF:1,\nsegment.ts\n'*4100+b'#EXT-X-ENDLIST\n'
        reply=Mock(status=206,url=url)
        reply.read.side_effect=lambda n:raw[:n]
        ctx=Mock()
        ctx.__enter__=Mock(return_value=reply)
        ctx.__exit__=Mock(return_value=False)
        opener=Mock()
        opener.open.return_value=ctx
        with patch('app.media_check.build_opener',return_value=opener):
            self.assertTrue(MediaVerifier().check(url).ok)
            self.assertEqual(opener.open.call_count,1)
            raw=b'#EXTM3U\n#EXT-X-SESSION-KEY:METHOD=SAMPLE-AES,URI="key"\n#EXT-X-STREAM-INF:BANDWIDTH=100\nv.m3u8'
            self.assertEqual(MediaVerifier().check(url).reason,'protected_source')
            self.assertEqual(opener.open.call_count,2)
