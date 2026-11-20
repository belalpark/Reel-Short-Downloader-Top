"""0.6.1: button 3 downloads the series open in Edge without a catalog pass."""
import time
import unittest
from unittest.mock import Mock

from app.library_store import LibraryStore
from app.ui import DesktopApp
from tests.test_capture_budget import Fixture, OTHER, SID, page, snapshot


class OpenSeriesTests(Fixture):
    def press_three(self, page_url):
        ui = Mock(library_engine=self.engine, library_store=self.store, bridge=Mock(), automatic=self.auto)
        ui._library_ready.return_value = True
        ui.bridge.status.return_value = {'connected': True, 'page_url': page_url, 'pending_kind': ''}
        DesktopApp.download_library(ui)
        return ui

    def test_open_series_in_an_empty_library_is_added_captured_and_downloaded(self):
        ui = self.press_three(page(SID, 1))
        self.assertEqual([s['id'] for s in self.store.series()], [SID])
        self.assertEqual(self.engine.focus, {SID})
        self.assertTrue(self.engine.downloading)
        self.engine.tick()
        jobs = self.bridge.sessions.snapshot()
        self.assertEqual([(j['seriesId'], j['targets']) for j in jobs], [(SID, [1, 2, 3, 4, 5, 6])])
        self.assertEqual(self.bridge.catalog_job_id, '')
        self.assertIn('المسلسل المفتوح', ui.status.set.call_args.args[0])
        state = snapshot(SID, 8, verified=[1, 2], tried=[1, 2, 3])
        state.update(desktopCommandId=jobs[0]['id'], channel=jobs[0]['id'], finished=True)
        self.bridge.sessions.receive('/sessions/snapshot', {'sessionId': jobs[0]['id'], 'seriesId': SID, 'tabId': 3, 'state': state})
        for _ in range(4):
            self.engine.tick()
        self.engine.queue.start.assert_called()
        self.assertEqual(self.engine.queue.start.call_args.args[0][0].series_id, SID)

    def test_only_the_open_series_gets_new_episodes_not_the_rest_of_the_library(self):
        self.seed(OTHER, total=6, verified=[1])
        self.press_three(page(SID, 4))
        self.engine.tick()
        self.assertEqual({j['seriesId'] for j in self.bridge.sessions.snapshot()}, {SID})

    def test_empty_library_without_an_open_series_explains_and_starts_nothing(self):
        ui = self.press_three('https://www.reelshort.com/ar')
        self.assertFalse(self.engine.downloading)
        self.assertEqual(self.store.count_series(), 0)
        self.assertIn('المكتبة فارغة', ui.status.set.call_args.args[0])
        ui.open_library.assert_called_once()

    def test_focus_survives_restart_until_stop_and_ends_when_capture_is_done(self):
        self.press_three(page(SID, 1))
        restarted = LibraryStore(self.store.path)
        self.assertEqual(restarted.setting('focus_series'), [SID])
        self.store.ingest(snapshot(SID, 2, verified=[1, 2]))
        self.engine.scheduler.tick()
        self.assertEqual(self.engine.focus, set())
        self.press_three(page(SID, 1))
        self.engine.stop()
        self.assertEqual((self.engine.focus, self.store.setting('focus_series')), (set(), []))

    def test_saved_episodes_of_the_open_series_are_not_captured_again(self):
        self.seed(SID, total=4, verified=[1, 2])
        self.store.complete(SID, 1, self.root / 'Episode_001.mp4')
        self.store.complete(SID, 2, self.root / 'Episode_002.mp4')
        self.press_three(page(SID, 1))
        self.engine.scheduler.tick()
        self.assertEqual(self.bridge.sessions.snapshot()[0]['targets'], [3, 4])
        self.assertEqual(self.bridge.sessions.snapshot()[0]['skipped'], [1, 2])


if __name__ == '__main__':
    unittest.main()
