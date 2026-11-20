"""0.7: button 3 downloads the open series completely, one series at a time."""
import time
import unittest
from unittest.mock import Mock, patch

from app.library_engine import LibraryEngine
from app.library_store import LibraryStore
from app.ui import DesktopApp
from tests.test_capture_budget import Fixture, OTHER, SID, page, snapshot

THIRD = 'c' * 24


class SeriesSequenceTests(Fixture):
    def setUp(self):
        super().setUp()
        self.events = []
        self.engine.emit = self.events.append

    def press_three(self, page_url):
        ui = Mock(library_engine=self.engine, library_store=self.store, bridge=Mock(), automatic=self.auto)
        ui._library_ready.return_value = True
        ui.download_sequence = lambda: DesktopApp.download_sequence(ui)
        ui.bridge.status.return_value = {'connected': True, 'page_url': page_url, 'pending_kind': ''}
        DesktopApp.download_library(ui)
        return ui

    def deliver(self, sid, total, **kw):
        job = self.bridge.sessions.snapshot()[0]
        state = snapshot(sid, total, **kw)
        state.update(desktopCommandId=job['id'], channel=job['id'], finished=True)
        self.bridge.sessions.receive('/sessions/heartbeat', {'sessionId': job['id'], 'seriesId': sid, 'tabId': 7})
        code, _ = self.bridge.sessions.receive('/sessions/snapshot', {'sessionId': job['id'], 'seriesId': sid, 'tabId': 7, 'state': state})
        self.assertEqual(code, 200)
        return job

    def download_all(self):
        """Run ticks; every download request succeeds with a real small file."""
        def start(entries, destination):
            for item in entries:
                folder = destination / f'S_{item.series_id}'
                folder.mkdir(parents=True, exist_ok=True)
                f = folder / f'Episode_{item.episode:03d}.mp4'
                f.write_bytes(b'\0' * 256)
                self.engine._download_event(('result', {'series_id': item.series_id, 'episode': item.episode,
                                                        'status': 'مكتمل ومفحوص', 'file': str(f)}))
        self.engine.queue.start.side_effect = start
        for _ in range(60):
            self.engine.tick()

    def test_open_series_is_captured_in_one_visible_tab_without_inventory(self):
        ui = self.press_three(page(SID, 1))
        self.assertEqual((self.engine.current, self.engine.sequence), (SID, 'single'))
        self.engine.tick()
        jobs = self.bridge.sessions.snapshot()
        self.assertEqual([(j['slot'], j['seriesId'], j['foreground']) for j in jobs], [(1, SID, True)])
        self.assertEqual(jobs[0]['targets'], list(range(1, 11)))
        self.assertEqual(self.bridge.catalog_job_id, '')
        self.assertIn('كاملًا', ui.status.set.call_args.args[0])

    def test_paid_episodes_that_play_for_a_subscriber_are_downloaded_not_locked(self):
        self.press_three(page(SID, 1))
        self.engine.tick()
        self.deliver(SID, 10, verified=range(1, 11), paid=7, tried=range(1, 11))
        self.download_all()
        rows = self.rows()
        self.assertEqual({r['status'] for r in rows.values()}, {'done'})
        self.assertEqual(len(rows), 10)

    def test_series_completion_stops_and_reports_once_in_single_mode(self):
        self.press_three(page(SID, 1))
        self.engine.tick()
        self.deliver(SID, 3, verified=[1, 2, 3], tried=[1, 2, 3])
        self.download_all()
        done = [e for e in self.events if e[0] == 'series_complete']
        self.assertEqual(len(done), 1)
        _, sid, summary, mode = done[0]
        self.assertEqual((sid, mode, summary['done'], summary['total']), (SID, 'single', 3, 3))
        self.assertEqual((self.engine.current, self.engine.downloading), (None, False))
        self.assertIsNone(self.store.setting('current_series'))

    def test_other_series_are_not_downloaded_while_the_current_one_is_incomplete(self):
        self.seed(OTHER, total=2, verified=[1, 2])
        self.press_three(page(SID, 1))
        self.engine.tick()
        started = []
        self.engine.queue.start.side_effect = lambda entries, destination: started.extend(e.series_id for e in entries)
        for _ in range(10):
            self.engine.tick()
        self.assertEqual(started, [])
        self.assertEqual({j['seriesId'] for j in self.bridge.sessions.snapshot()}, {SID})

    def test_library_sequence_finishes_each_series_before_the_next(self):
        self.seed(SID, total=2, verified=[1, 2])
        self.seed(OTHER, total=2, verified=[1, 2])
        order = []
        def start(entries, destination):
            for item in entries:
                order.append(item.series_id)
                folder = destination / f'S_{item.series_id}'
                folder.mkdir(parents=True, exist_ok=True)
                f = folder / f'Episode_{item.episode:03d}.mp4'
                f.write_bytes(b'\0' * 256)
                self.engine._download_event(('result', {'series_id': item.series_id, 'episode': item.episode,
                                                        'status': 'مكتمل ومفحوص', 'file': str(f)}))
        self.engine.queue.start.side_effect = start
        self.assertTrue(self.engine.start_library_sequence())
        self.assertEqual(self.engine.current, SID)
        for _ in range(40):
            self.engine.tick()
        self.assertEqual(order, [SID, SID, OTHER, OTHER])
        kinds = [e[0] for e in self.events if e[0] in ('series_complete', 'library_complete')]
        self.assertEqual(kinds, ['series_complete', 'series_complete', 'library_complete'])
        self.assertIsNone(self.engine.current)

    def test_current_series_survives_restart_and_stop_clears_it(self):
        self.press_three(page(SID, 1))
        again = LibraryEngine(self.store, self.bridge, self.auto, self.root, self.root / 'data', self.auto.destination,
                              lambda _: None, verifier=self.verifier, opener=self.open, session_hub=self.bridge.sessions)
        self.assertEqual((again.current, again.sequence), (SID, 'single'))
        again.restore_downloads()
        self.assertTrue(again.downloading)
        again.stop()
        self.assertEqual((again.current, self.store.setting('current_series')), (None, None))

    def test_hidden_tab_failure_spends_no_attempt(self):
        self.press_three(page(SID, 1))
        self.engine.tick()
        job = self.bridge.sessions.snapshot()[0]
        state = snapshot(SID, 10, verified=[1], not_played=[2], tried=[1])
        state['rows']['2']['status'] = 'tab_hidden'
        state.update(desktopCommandId=job['id'], channel=job['id'], finished=True)
        self.bridge.sessions.receive('/sessions/snapshot', {'sessionId': job['id'], 'seriesId': SID, 'tabId': 7, 'state': state})
        self.engine.scheduler.tick()
        row = self.rows()[2]
        self.assertEqual((row['attempts'], row['error']), (0, 'capture_not_reached'))

    def test_episode_failure_reason_is_recorded(self):
        self.press_three(page(SID, 1))
        self.engine.tick()
        self.deliver(SID, 10, verified=[1], not_played=[2], tried=[1, 2])
        self.engine.scheduler.tick()
        self.assertEqual(self.rows()[2]['error'], 'playback_not_started')

    def test_empty_library_without_an_open_series_explains_and_starts_nothing(self):
        ui = self.press_three('https://www.reelshort.com/ar')
        self.assertFalse(self.engine.downloading)
        self.assertIn('المكتبة فارغة', ui.status.set.call_args.args[0])

    def test_completion_prompt_continues_with_other_series_or_inventory(self):
        ui = Mock(library_engine=self.engine, library_store=self.store, smoke=False, logs=[], root=None)
        self.seed(OTHER, total=2, verified=[1])
        with patch('app.ui.messagebox.askyesno', return_value=True) as ask:
            DesktopApp._series_complete(ui, SID, {'title': 'A', 'total': 3, 'done': 3, 'locked': 0, 'held': 0, 'blocked': 0}, 'single')
        self.assertEqual(ask.call_count, 1)
        self.assertEqual((self.engine.current, self.engine.sequence), (OTHER, 'library'))
        self.engine.stop()
        self.store.complete(OTHER, 1, self.root / 'a.mp4')
        self.store.complete(OTHER, 2, self.root / 'b.mp4')
        with patch('app.ui.messagebox.askyesno', return_value=True) as ask:
            DesktopApp._series_complete(ui, SID, {'title': 'A', 'total': 3, 'done': 3}, 'single')
        self.assertEqual(ask.call_count, 2)
        self.assertTrue(self.bridge.catalog_job_id)
        self.assertTrue(self.engine.continue_after_inventory)
        with patch('app.ui.messagebox.askyesno', return_value=False) as ask:
            self.engine.stop()
            DesktopApp._series_complete(ui, SID, {'title': 'A', 'total': 3, 'done': 3}, 'single')
        self.assertIsNone(self.engine.current)

    def test_inventory_requested_from_the_prompt_continues_the_sequence(self):
        self.engine.continue_with_inventory()
        self.store.add_series([{'id': THIRD, 'title': 'New', 'pageUrl': f'https://www.reelshort.com/ar/movie/x-{THIRD}'}])
        self.bridge.catalog_progress = {'id': self.bridge.catalog_job_id, 'done': True, 'updated': time.time()}
        self.engine._capture_tick()
        self.assertEqual((self.engine.current, self.engine.sequence), (THIRD, 'library'))


if __name__ == '__main__':
    unittest.main()
