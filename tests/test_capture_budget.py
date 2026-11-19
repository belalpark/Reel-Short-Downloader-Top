"""0.6: separate inventory/capture/download, paywall-aware capture budgets and kept files."""
import csv
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.automatic import AutomaticDownloads
from app.bridge import Bridge
from app.downloader import series_folder
from app.library_engine import LibraryEngine
from app.library_store import LibraryStore, clean_title
from app.media_check import SourceCheck
from app.paths import write_json
from app.session_scheduler import SessionScheduler

SID = 'a' * 24
OTHER = 'b' * 24


def page(sid, ep):
    return f'https://www.reelshort.com/ar/episodes/episode-{ep}-fixture-{sid}'


def snapshot(sid, total, *, verified=(), blocked=(), not_played=(), paid=0, tried=None, title='Fixture'):
    rows = {}
    for ep in range(1, total + 1):
        row = {'episode': ep, 'pageUrl': page(sid, ep), 'mediaUrl': '', 'playbackStatus': 'not_checked',
               'status': 'page_only', 'sourceType': '', 'captureId': '', 'observedAt': ''}
        if ep in verified:
            row.update(mediaUrl=f'https://v-mps.crazymaplestudios.com/vod-1/{sid}/{ep}.m3u8',
                       playbackStatus='playback_verified', status='media_found', captureId=f'c{ep}-{time.time()}')
        elif ep in blocked:
            row.update(status='manual_open_required')
        elif ep in not_played:
            row.update(playbackStatus='playback_not_started', status='playback_not_started')
        if tried is not None:
            row['tried'] = ep in tried
        rows[str(ep)] = row
    return {'seriesId': sid, 'title': title, 'total': total, 'rows': rows, 'paidStart': paid}


class Fixture(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.store = LibraryStore(self.root / 'library.sqlite3')
        write_json(self.root / 'connect.json', {'token': 'x' * 48, 'extension_id': 'a' * 32})
        self.bridge = Bridge(self.root / 'connect.json', inbox=self.root / 'Inbox', library=self.store)
        self.auto = AutomaticDownloads(self.root, self.root / 'Inbox', self.root / 'Videos', self.root / 'data', lambda _: None)
        self.open = Mock()
        self.verifier = Mock()
        self.verifier.check.return_value = SourceCheck(True, 'verified_hls', time.time())
        self.engine = LibraryEngine(self.store, self.bridge, self.auto, self.root, self.root / 'data',
                                    self.auto.destination, lambda _: None, verifier=self.verifier,
                                    opener=self.open, session_hub=self.bridge.sessions)
        self.bridge.snapshot_handler = self.engine.on_snapshot
        self.engine.queue = Mock(running=False)

    def seed(self, sid=SID, total=12, **kw):
        self.store.add_series([{'id': sid, 'title': 'Fixture', 'pageUrl': f'https://www.reelshort.com/ar/movie/x-{sid}'}])
        self.store.ingest(snapshot(sid, total, **kw))

    def rows(self, sid=SID):
        return {e['episode']: e for e in self.store.episodes(sid)}


class CaptureBudgetTests(Fixture):
    def test_untried_targets_spend_no_attempt_when_a_run_stops_early(self):
        self.seed(verified=range(1, 7))
        self.store.finish_capture(SID, [7, 8, 9, 10, 11, 12], reason='capture_timeout', tried=[7],
                                  outcomes={7: {'ok': False}})
        rows = self.rows()
        self.assertEqual(rows[7]['attempts'], 1)
        for ep in range(8, 13):
            self.assertEqual((rows[ep]['attempts'], rows[ep]['status'], rows[ep]['error']), (0, 'missing', 'capture_not_reached'))

    def test_paid_episode_the_site_will_not_open_locks_the_paid_range_once(self):
        self.seed(total=20, verified=range(1, 7), paid=7)
        before = self.rows()
        result = self.store.finish_capture(SID, [7, 8, 9, 10, 11, 12], reason='', tried=[7],
                                           outcomes={7: {'ok': False, 'blocked': True}}, locked_from=7)
        rows = self.rows()
        self.assertEqual(result['locked'], 14)
        self.assertTrue(all(rows[ep]['status'] == 'locked' for ep in range(7, 21)))
        self.assertTrue(all(rows[ep]['attempts'] == 0 for ep in range(7, 21)))
        self.assertEqual([rows[ep] for ep in range(1, 7)], [before[ep] for ep in range(1, 7)])
        series = next(s for s in self.store.series() if s['id'] == SID)
        self.assertEqual((series['locked'], series['capture_due'], series['paid_start'], series['locked_from']), (14, 0, 7, 7))
        self.assertEqual(self.store.capture_targets(series, 6), [])
        self.assertEqual(self.store.pending_series(), [])

    def test_one_paid_playback_failure_is_not_enough_but_two_are(self):
        self.seed(total=20, verified=range(1, 7), paid=7)
        self.store.finish_capture(SID, [7], tried=[7], outcomes={7: {'ok': False}})
        self.assertEqual(self.rows()[7]['status'], 'missing')
        self.assertFalse(any(r['status'] == 'locked' for r in self.rows().values()))
        self.store.finish_capture(SID, [8], tried=[8], outcomes={8: {'ok': False}})
        rows = self.rows()
        self.assertEqual(rows[7]['status'], 'locked')
        self.assertEqual(sum(r['status'] == 'locked' for r in rows.values()), 14)

    def test_free_episodes_and_existing_paid_links_are_never_locked(self):
        self.seed(total=12, verified=[1, 2, 3, 9], paid=7)
        self.store.complete(SID, 3, self.root / 'kept.mp4')
        self.store.finish_capture(SID, [4, 7], tried=[4, 7], outcomes={4: {'ok': False}, 7: {'ok': False, 'blocked': True}})
        rows = self.rows()
        self.assertEqual(rows[4]['status'], 'missing')
        self.assertEqual(rows[3]['status'], 'done')
        self.assertEqual(rows[9]['status'], 'captured')
        self.assertEqual([ep for ep, r in rows.items() if r['status'] == 'locked'], [7, 8, 10, 11, 12])

    def test_without_paid_marker_two_consecutive_unopened_episodes_are_required(self):
        self.seed(total=12, verified=range(1, 4))
        self.store.finish_capture(SID, [4, 6], tried=[4, 6], outcomes={4: {'ok': False, 'blocked': True}, 6: {'ok': False, 'blocked': True}})
        self.assertFalse(any(r['status'] == 'locked' for r in self.rows().values()))
        self.store.finish_capture(SID, [7, 8], tried=[7, 8], outcomes={7: {'ok': False, 'blocked': True}, 8: {'ok': False, 'blocked': True}})
        self.assertEqual([ep for ep, r in self.rows().items() if r['status'] == 'locked'], [7, 8, 9, 10, 11, 12])

    def test_a_series_that_never_played_is_not_locked_and_stops_after_two_batches(self):
        self.seed(total=40, paid=7)
        self.store.finish_capture(SID, list(range(1, 7)), tried=range(1, 7), outcomes={})
        self.assertFalse(any(r['status'] in ('locked', 'held') for r in self.rows().values()))
        self.store.finish_capture(SID, list(range(7, 13)), tried=range(7, 13), outcomes={})
        rows = self.rows()
        self.assertFalse(any(r['status'] == 'locked' for r in rows.values()))
        self.assertTrue(all(r['status'] == 'held' for r in rows.values()))
        self.assertEqual(rows[30]['error'], 'series_not_playing')

    def test_rate_limit_spends_no_attempt_and_does_not_lock(self):
        self.seed(total=12, verified=range(1, 7), paid=7)
        self.store.finish_capture(SID, [7, 8], reason='rate_limited', tried=[7, 8], outcomes={7: {'ok': False, 'blocked': True}})
        rows = self.rows()
        self.assertEqual((rows[7]['status'], rows[7]['attempts'], rows[7]['error']), ('missing', 0, 'rate_limited'))
        self.assertGreater(rows[7]['next_try'], time.time() + 30)

    def test_recheck_releases_locked_and_a_new_verified_capture_revives_the_episode(self):
        self.seed(total=10, verified=range(1, 7), paid=7)
        self.store.finish_capture(SID, [7], tried=[7], outcomes={7: {'ok': False, 'blocked': True}})
        self.assertEqual(self.store.retry_locked(), 4)
        rows = self.rows()
        self.assertEqual({rows[ep]['status'] for ep in range(7, 11)}, {'missing'})
        self.store.finish_capture(SID, [7], tried=[7], outcomes={7: {'ok': False, 'blocked': True}})
        self.assertEqual(self.rows()[8]['status'], 'locked')
        self.store.ingest(snapshot(SID, 10, verified=[8], paid=7))
        self.assertEqual(self.rows()[8]['status'], 'captured')

    def test_job_that_never_started_backs_off_the_series_without_touching_episodes(self):
        self.seed(total=12, verified=range(1, 7))
        self.store.finish_capture(SID, [7, 8], reason='capture_timeout', tried=[], outcomes={})
        rows = self.rows()
        self.assertEqual((rows[7]['attempts'], rows[8]['attempts']), (0, 0))
        series = next(s for s in self.store.series() if s['id'] == SID)
        self.assertEqual(series['start_failures'], 1)
        self.assertGreater(series['next_scan'], time.time() + 100)


class SessionTests(Fixture):
    def test_hub_records_reached_episodes_paid_marker_and_boundary(self):
        self.seed(total=12, verified=range(1, 7))
        series = next(s for s in self.store.series() if s['id'] == SID)
        job = self.bridge.sessions.assign(1, series, targets=[7, 8, 9], page_url=page(SID, 7))
        state = snapshot(SID, 12, verified=[], blocked=[7], paid=7, tried=[7])
        state.update(desktopCommandId=job['id'], channel=job['id'], finished=True, lockedFrom=7, current=13)
        self.assertEqual(self.bridge.sessions.receive('/sessions/snapshot', {'sessionId': job['id'], 'seriesId': SID, 'tabId': 4, 'state': state})[0], 200)
        live = self.bridge.sessions.snapshot()[0]
        self.assertEqual((live['tried'], live['paidStart'], live['lockedFrom']), ([7], 7, 7))
        self.assertEqual(live['outcomes'][7], {'ok': False, 'blocked': True, 'status': 'manual_open_required', 'playback': 'not_checked'})
        self.engine.collecting = True
        self.engine.scheduler.tick()
        rows = self.rows()
        self.assertEqual({rows[ep]['status'] for ep in range(7, 13)}, {'locked'})
        self.assertEqual(rows[8]['attempts'], 0)

    def test_older_rows_without_reach_flags_still_count_checked_episodes(self):
        self.seed(total=12, verified=range(1, 7))
        series = next(s for s in self.store.series() if s['id'] == SID)
        job = self.bridge.sessions.assign(1, series, targets=[7, 8], page_url=page(SID, 7))
        state = snapshot(SID, 12, not_played=[7])
        state.update(desktopCommandId=job['id'], channel=job['id'])
        self.bridge.sessions.receive('/sessions/snapshot', {'sessionId': job['id'], 'seriesId': SID, 'tabId': 4, 'state': state})
        self.assertEqual(self.bridge.sessions.snapshot()[0]['tried'], [7])

    def test_timeout_scales_with_batch_and_progress(self):
        scheduler = self.engine.scheduler
        self.assertEqual(scheduler.timeout_for({'targets': [1, 2, 3, 4, 5, 6]}), 90 + 45 * 6)
        self.seed(total=12, verified=range(1, 7))
        self.engine.collecting = True
        now = time.time()
        with patch('time.time', return_value=now):
            scheduler.tick()
        job = self.bridge.sessions.snapshot()[0]
        self.assertEqual(job['targets'], [7, 8, 9, 10, 11, 12])
        with self.bridge.sessions.lock:
            live = self.bridge.sessions.jobs[job['slot']]
            live['seen'] = live['updated'] = now + 200
        with patch('time.time', return_value=now + 220):
            scheduler.tick()
        self.assertEqual(self.bridge.sessions.snapshot()[0]['id'], job['id'], 'a progressing 6-episode run is not cut at 180 s')
        with patch('time.time', return_value=now + 200 + SessionScheduler.PROGRESS_TIMEOUT + 1):
            with self.bridge.sessions.lock:
                live['seen'] = now + 200 + SessionScheduler.PROGRESS_TIMEOUT
            scheduler.tick()
        self.assertNotIn(job['id'], [j['id'] for j in self.bridge.sessions.snapshot()])

    def test_barren_runs_pause_new_tabs_but_not_downloads(self):
        scheduler = self.engine.scheduler
        for _ in range(SessionScheduler.BARREN_LIMIT):
            scheduler._record({'sources': 0}, {'locked': 0})
        self.assertGreater(scheduler.not_before, time.time() + 500)
        self.assertIn('التنزيل مستمر', self.engine.note)
        scheduler._record({'sources': 1}, {})
        self.assertEqual(scheduler.barren, 0)
        self.seed(total=3, verified=[1])
        self.engine.start_downloads()
        self.engine.tick()
        self.engine.queue.start.assert_called_once()

    def test_renewals_are_scheduled_before_new_episodes(self):
        self.seed(SID, total=12, verified=range(1, 7))
        self.seed(OTHER, total=3, verified=[1])
        item = self.rows(OTHER)[1]
        self.store.update_source(item, 'needs_refresh', error='http_forbidden')
        self.engine.collecting = True
        self.engine.scheduler.tick()
        first = next(j for j in self.bridge.sessions.snapshot() if j['slot'] == 1)
        self.assertEqual((first['seriesId'], first['targets'][0]), (OTHER, 1))


class SeparationTests(Fixture):
    def test_download_inventory_and_capture_are_independent(self):
        self.seed(total=12, verified=[1])
        self.engine.start_downloads()
        self.engine.tick()
        self.assertEqual((self.bridge.catalog_job_id, self.bridge.sessions.snapshot()), ('', []))
        self.engine.queue.start.assert_called_once()
        self.engine.start_inventory()
        self.assertTrue(self.bridge.catalog_job_id)
        self.assertFalse(self.engine.collecting)
        self.engine.inventory.cancel()
        catalog_calls = []
        self.bridge.command = Mock(side_effect=lambda kind, *a, **k: catalog_calls.append(kind) or 'id')
        self.engine.start_capture()
        self.assertTrue(self.engine.collecting)
        self.assertNotIn('catalog', catalog_calls)
        self.assertTrue(self.store.setting('resume_collecting'))

    def test_restart_resumes_capture_and_downloads_without_inventory(self):
        self.seed(total=12, verified=[1])
        self.store.save_setting('resume_downloads', True)
        self.store.save_setting('resume_collecting', True)
        self.engine.restore_downloads()
        self.assertTrue(self.engine.downloading and self.engine.collecting)
        self.assertEqual(self.bridge.catalog_job_id, '')
        self.engine.stop()
        self.assertFalse(self.store.setting('resume_collecting'))
        self.assertFalse(self.store.setting('resume_downloads'))


class SavedFileTests(Fixture):
    def test_missing_saved_file_is_flagged_not_downloaded_again(self):
        self.seed(total=3, verified=[1, 2])
        folder = self.auto.destination / f'Fixture_{SID}'
        folder.mkdir(parents=True)
        kept, gone = folder / 'Episode_001.mp4', folder / 'Episode_002.mp4'
        kept.write_bytes(b'\0' * 256)
        for ep, f in ((1, kept), (2, gone)):
            self.store.complete(SID, ep, f)
            self.auto.record_library_completion(SID, ep, f)
        self.engine.start_downloads()
        self.engine.tick()
        rows = self.rows()
        self.assertEqual((rows[2]['status'], rows[2]['error']), ('done', 'saved_file_missing'))
        self.engine.queue.start.assert_not_called()
        self.assertEqual(next(s for s in self.store.series() if s['id'] == SID)['file_missing'], 1)
        gone.write_bytes(b'\0' * 256)
        self.engine.start_downloads()
        self.engine.tick()
        self.assertEqual(self.rows()[2]['error'], '')
        gone.unlink()
        self.engine.start_downloads()
        self.engine.tick()
        self.assertEqual(self.store.requeue_missing_files(), 1)
        self.assertEqual(self.rows()[2]['status'], 'captured')
        self.assertEqual(self.rows()[1]['status'], 'done')

    def test_capture_never_targets_saved_episodes(self):
        self.seed(total=4, verified=[1, 2])
        self.store.complete(SID, 1, self.root / 'one.mp4')
        series = next(s for s in self.store.series() if s['id'] == SID)
        self.assertEqual(self.store.capture_targets(series, 6), [3, 4])
        self.assertIn(1, self.store.skip_sources(SID))

    def test_a_series_keeps_one_folder_after_its_title_changes(self):
        dest = self.root / 'Videos'
        old = dest / f'تشغيل_ Old_{SID}'
        old.mkdir(parents=True)
        (old / 'Episode_001.mp4').write_bytes(b'x')
        self.assertEqual(series_folder(dest, 'New title', SID), old)
        self.assertEqual(series_folder(dest, 'New title', OTHER), dest / f'New title_{OTHER}')


class LibraryOrganisationTests(Fixture):
    def test_catalog_labels_are_not_saved_as_titles(self):
        self.assertEqual(clean_title('تشغيل: مديري الملياردير\xa0يرفض'), 'مديري الملياردير يرفض')
        self.assertEqual(clean_title('الأحدث'), '')
        self.assertEqual(clean_title('Play: The Twins'), 'The Twins')
        self.store.add_series([{'id': SID, 'title': 'الأحدث', 'pageUrl': f'https://www.reelshort.com/ar/movie/x-{SID}'}])
        self.assertEqual(self.store.series()[0]['title'], 'Series')
        self.store.add_series([{'id': SID, 'title': 'تشغيل: Real Name', 'pageUrl': f'https://www.reelshort.com/ar/movie/x-{SID}'}])
        self.assertEqual(self.store.series()[0]['title'], 'Real Name')
        self.store.add_series([{'id': SID, 'title': 'الأحدث', 'pageUrl': f'https://www.reelshort.com/ar/movie/x-{SID}'}])
        self.assertEqual(self.store.series()[0]['title'], 'Real Name')

    def test_existing_polluted_titles_are_repaired_on_open(self):
        self.seed(total=2, verified=[1])
        with self.store.db() as db:
            db.execute("UPDATE series SET title='تشغيل: Old Label' WHERE id=?", (SID,))
        self.assertEqual(LibraryStore(self.store.path).series()[0]['title'], 'Old Label')

    def test_upgrade_clears_only_unreached_timeout_attempts_once(self):
        self.seed(total=4, verified=[1])
        with self.store.db() as db:
            db.execute("UPDATE episodes SET attempts=2,error='capture_timeout',next_try=9e9 WHERE sid=? AND episode=2", (SID,))
            db.execute("UPDATE episodes SET attempts=2,error='source_not_captured' WHERE sid=? AND episode=3", (SID,))
            db.execute("DELETE FROM settings WHERE key='capture_budget_v6'")
        rows = {e['episode']: e for e in LibraryStore(self.store.path).episodes(SID)}
        self.assertEqual((rows[2]['attempts'], rows[2]['error'], rows[2]['next_try']), (0, '', 0))
        self.assertEqual((rows[3]['attempts'], rows[3]['error']), (2, 'source_not_captured'))
        with self.store.db() as db:
            db.execute("UPDATE episodes SET attempts=1,error='capture_timeout' WHERE sid=? AND episode=2", (SID,))
        self.assertEqual(LibraryStore(self.store.path).episodes(SID)[1]['attempts'], 1)

    def test_index_export_lists_every_episode_without_media_urls(self):
        self.seed(total=3, verified=[1, 2])
        self.store.complete(SID, 1, self.root / 'Episode_001.mp4')
        path = self.store.export_index(self.root / 'out' / 'library_index.csv')
        text = path.read_text(encoding='utf-8-sig')
        self.assertNotIn('crazymaplestudios', text)
        rows = list(csv.DictReader(text.splitlines()))
        self.assertEqual([r['Status'] for r in rows], ['محفوظ', 'رابط قيد الفحص', 'بلا رابط'])
        self.assertEqual(rows[0]['File'], str(self.root / 'Episode_001.mp4'))


if __name__ == '__main__':
    unittest.main()
