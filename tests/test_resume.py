import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from app import EXTENSION_VERSION
from app.automatic import AutomaticDownloads, Inbox
from app.bridge import Bridge
from app.catalog import Episode
from app.downloader import DownloadQueue
from app.paths import write_json
from app.presentation import catalog_view

SID = '1' * 24


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.auto = AutomaticDownloads(self.root, self.root / 'Inbox', self.root / 'Videos', self.root / 'Data', lambda event: None)
        self.auto.queue = Mock(running=False)

    def tearDown(self):
        self.tmp.cleanup()

    def snapshot(self, captures=None, total=3):
        captures = captures or {1: 'one'}
        state = {'seriesId': SID, 'title': 'Fixture', 'total': total, 'desktopCommandId': 'run', 'running': True,
                 'rows': {str(n): {'episode': n, 'pageUrl': f'https://www.reelshort.com/episodes/episode-{n}-{SID}',
                          'mediaUrl': f'https://cdn.example.org/{n}.webm' if n in captures else '',
                          'captureId': captures.get(n, '')} for n in range(1, total + 1)}}
        return Inbox(self.root / 'Inbox').save(state, 'run')

    def saved(self, episode):
        file = self.root / 'Videos' / f'Episode_{episode:03d}.webm'
        file.parent.mkdir(exist_ok=True)
        file.write_bytes(b'x' * 256)
        return file

    def test_patient_mode_never_queues_a_later_episode_across_a_gap(self):
        self.auto.begin(SID, 'run', patient=True)
        self.snapshot({3: 'third'})
        self.auto.tick()
        self.auto.queue.start.assert_not_called()
        self.snapshot({1: 'first', 2: 'second', 3: 'third'})
        self.auto.tick()
        self.assertEqual([e.episode for e in self.auto.queue.start.call_args.args[0]], [1])

    def test_capture_id_is_required_before_a_patient_download_is_published(self):
        self.auto.begin(SID, 'run', patient=True)
        self.snapshot({1: ''})
        self.auto.tick()
        self.auto.queue.start.assert_not_called()

    def test_failed_download_needs_a_fresh_capture_even_when_the_url_does_not_change(self):
        self.auto.begin(SID, 'run', patient=True)
        self.snapshot({1: 'old-capture'})
        self.auto.tick()
        failure = {'series_id': SID, 'episode': 1, 'status': 'فشل — راجع السجل', 'file': '', 'capture_id': 'old-capture'}
        with patch('app.automatic.time.monotonic', return_value=100):
            self.auto._download_event(('result', failure))
        with patch('app.automatic.time.monotonic', return_value=104):
            self.auto.tick()
            self.assertEqual(self.auto.queue.start.call_count, 1)
            self.snapshot({1: 'new-capture'})
            self.auto.tick()
        self.assertEqual(self.auto.queue.start.call_count, 2)
        self.assertEqual(self.auto.queue.start.call_args.args[0][0].capture_id, 'new-capture')

    def test_retry_obeys_its_cooldown_and_stop_cancels_further_submission(self):
        self.auto.begin(SID, 'run', patient=True)
        self.snapshot({1: 'old'})
        self.auto.tick()
        with patch('app.automatic.time.monotonic', return_value=100):
            self.auto._download_event(('result', {'series_id': SID, 'episode': 1, 'status': 'فشل', 'file': '', 'capture_id': 'old'}))
            self.snapshot({1: 'new'})
            self.auto.tick()
        self.assertEqual(self.auto.queue.start.call_count, 1)
        self.auto.stop()
        with patch('app.automatic.time.monotonic', return_value=200):
            self.auto.tick()
        self.assertEqual(self.auto.queue.start.call_count, 1)

    def test_each_completed_episode_is_saved_before_batch_done_and_survives_restart(self):
        self.auto.begin(SID, 'run', patient=True)
        file = self.saved(1)
        self.auto._download_event(('result', {'series_id': SID, 'episode': 1, 'status': 'مكتمل ومفحوص', 'file': str(file)}))
        record = json.loads((self.root / 'Data/completed.json').read_text())
        self.assertEqual(record[SID + '/1'], str(file))
        other = AutomaticDownloads(self.root, self.root / 'Inbox', self.root / 'Videos', self.root / 'Data', lambda event: None)
        self.assertEqual(other.completed_episodes(SID), [1])

    def test_resume_retains_completed_files_but_does_not_reuse_old_signed_sources(self):
        self.snapshot({1: 'old', 2: 'old'}, total=3)
        self.auto.completed[SID + '/1'] = str(self.saved(1))
        self.auto.begin(SID, 'new-run', patient=True)
        self.assertEqual(self.auto.completed_episodes(), [1])
        self.assertEqual(len(self.auto.entries), 3)
        self.assertFalse(any(e.media_url or e.capture_id for e in self.auto.entries))
        self.auto.tick()
        self.auto.queue.start.assert_not_called()

    def test_deleted_files_and_a_different_destination_are_not_skipped(self):
        file = self.saved(2)
        self.auto.completed[SID + '/2'] = str(file)
        self.assertEqual(self.auto.completed_episodes(SID), [2])
        self.auto.destination = self.root / 'Other'
        self.assertEqual(self.auto.completed_episodes(SID), [])
        self.auto.destination = self.root / 'Videos'
        file.unlink()
        self.assertEqual(self.auto.completed_episodes(SID), [])

    def test_progress_is_authenticated_and_bound_to_the_active_run(self):
        secret = 's' * 40
        credentials = self.root / 'connection.json'
        write_json(credentials, {'token': secret, 'extension_id': 'a' * 32})
        self.auto.begin(SID, 'run', patient=True)
        bridge = Bridge(credentials, progress=self.auto.progress)
        bridge.active_run_id, bridge.active_series_id = 'run', SID
        body = {'version': EXTENSION_VERSION, 'extensionId': 'a' * 32}
        origin = 'chrome-extension://' + 'a' * 32
        self.assertEqual(bridge.receive('/progress', origin, 'Bearer bad', body, 'Edg/157')[0], 401)
        code, response = bridge.receive('/progress', origin, 'Bearer ' + secret, body, 'Edg/157')
        self.assertEqual(code, 200)
        self.assertEqual(response['download']['runId'], 'run')
        bridge.active_run_id = 'another-run'
        self.assertNotIn('download', bridge.receive('/progress', origin, 'Bearer ' + secret, body, 'Edg/157')[1])

    def test_start_transfers_only_existing_completed_episode_numbers(self):
        bridge = Bridge(self.root / 'connection.json')
        bridge.command('start', SID, patient=True, completed_episodes=[1, 1, 46, False, 0, 151])
        self.assertTrue(bridge.pending_command['patient'])
        self.assertEqual(bridge.pending_command['completedEpisodes'], [1, 46])

    def test_valid_unrecorded_files_are_recovered_without_reopening_their_pages(self):
        folder = self.root / 'Videos' / ('Old title_' + SID)
        folder.mkdir(parents=True)
        good, bad = folder / 'Episode_046.mp4', folder / 'Episode_047.mp4'
        good.write_bytes(b'valid-video' * 50)
        bad.write_bytes(b'bad-response' * 50)
        (folder / 'Episode_048.mp4.part').write_bytes(b'part' * 100)
        (folder / 'Episode_049.mp4.invalid').write_bytes(b'invalid' * 100)
        foreign = self.root / 'Videos' / ('Other_' + 'f' * 24)
        foreign.mkdir()
        (foreign / 'Episode_045.mp4').write_bytes(b'video' * 100)
        with patch('app.automatic.validate_video', side_effect=lambda _root, file: file == good) as probe:
            self.assertEqual(self.auto.recover_completed(SID), [46])
            self.assertEqual({call.args[1] for call in probe.call_args_list}, {good, bad})
        self.assertEqual(json.loads((self.root / 'Data/completed.json').read_text())[SID + '/46'], str(good.resolve()))

    def test_cancelled_recovery_never_marks_an_unchecked_file_complete(self):
        folder = self.root / 'Videos' / ('Title_' + SID)
        folder.mkdir(parents=True)
        (folder / 'Episode_046.mp4').write_bytes(b'file' * 100)
        with patch('app.automatic.validate_video') as probe:
            self.assertEqual(self.auto.recover_completed(SID, lambda: True), [])
            probe.assert_not_called()

    def test_screenshot_case_counts_45_saved_and_61_remaining_without_missing_labels_on_saved_rows(self):
        entries = [Episode(SID, 'Fixture', n, 106, '', '') for n in range(1, 107)]
        rows, done, ready, remaining = catalog_view(entries, lambda e: e.episode <= 45, {})
        self.assertEqual((done, ready, remaining), (45, 0, 61))
        self.assertTrue(all(r[1] == 'محفوظ على الجهاز' for r in rows[:45]))
        self.assertTrue(all(r[1] == 'لم يُلتقط بعد' for r in rows[45:]))

    def test_invalid_completed_output_is_released_for_a_repair_download(self):
        item = Episode(SID, 'Fixture', 1, 1, '', 'https://cdn.example.org/1.webm')
        folder = self.root / 'Videos' / ('Fixture_' + SID)
        folder.mkdir(parents=True)
        file = folder / 'Episode_001.webm'
        file.write_text('HTTP error disguised as a successful video transfer')
        process = Mock(stdout=[f'RS_FILE:{file}\n'])
        process.wait.return_value = 0
        events = []
        queue = DownloadQueue(self.root, self.root / 'report.json', events.append)
        with patch('app.downloader.subprocess.Popen', return_value=process), patch('app.downloader.validate_video', return_value=False):
            queue._run([item], self.root / 'Videos')
        self.assertFalse(file.exists())
        self.assertTrue(file.with_suffix('.webm.invalid').is_file())
        self.assertEqual([e for e in events if e[0] == 'result'][0][1]['status'], 'فشل — راجع السجل')


if __name__ == '__main__':
    unittest.main()
