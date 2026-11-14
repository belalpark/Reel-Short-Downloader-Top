import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from app import EXTENSION_VERSION
from app.automatic import AutomaticDownloads, Inbox, provision_extension, series_from_page
from app.bridge import Bridge
from app.catalog import load_catalog

SID = '0123456789abcdef01234567'
EXT = 'a' * 32


def state(run, media=1):
    return {'seriesId': SID, 'title': 'Auto', 'total': 3, 'desktopCommandId': run, 'autoPass': 1,
            'running': True, 'rows': {str(n): {'episode': n,
             'pageUrl': f'https://www.reelshort.com/episodes/episode-{n}-test-{SID}',
             'mediaUrl': f'https://cdn.example.org/{n}.mp4' if n <= media else ''} for n in range(1, 4)}}


class AutomaticTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.package = self.root / 'package'
        self.package.mkdir()
        (self.package / 'manifest.json').write_text(json.dumps({'version': EXTENSION_VERSION}))
        self.credentials = self.root / 'connection.json'

    def tearDown(self):
        self.tmp.cleanup()

    def bootstrap(self):
        return provision_extension(self.package, self.root / 'Extension', self.credentials)

    def test_automatic_connection_requires_no_pin(self):
        token = self.bootstrap()
        config = json.loads((self.root / 'Extension/desktop_config.json').read_text())
        self.assertEqual(config['token'], token)
        self.assertNotIn('code', config)
        bridge = Bridge(self.credentials)
        code, reply = bridge.receive('/connect', 'chrome-extension://' + EXT, 'Bearer ' + token,
            {'version': EXTENSION_VERSION, 'extensionId': EXT}, 'Mozilla/5.0 Edg/157.0')
        self.assertEqual(code, 200)
        self.assertTrue(bridge.status()['connected'])
        self.assertNotIn('token', reply)

    def test_wrong_secret_cannot_inject_a_snapshot(self):
        self.bootstrap()
        bridge = Bridge(self.credentials)
        code, _ = bridge.receive('/connect', 'chrome-extension://' + EXT, 'Bearer bad',
            {'version': EXTENSION_VERSION, 'extensionId': EXT}, 'Edg/157.0')
        self.assertEqual(code, 401)

    def test_snapshot_is_a_known_validated_csv(self):
        inbox = Inbox(self.root / 'Inbox')
        path = inbox.save(state('run-1'), 'run-1')
        self.assertEqual(path, self.root / 'Inbox' / (SID + '.csv'))
        self.assertEqual(len(load_catalog(path)), 3)
        self.assertEqual(sum(bool(x.media_url) for x in load_catalog(path)), 1)
        self.assertEqual(json.loads(path.with_suffix('.json').read_text())['run_id'], 'run-1')

    def test_hls_candidates_are_not_promoted(self):
        value = state('run', 0)
        value['rows']['1']['hlsCandidates'] = ['https://cdn.example.org/private.m3u8']
        path = Inbox(self.root / 'Inbox').save(value, 'run')
        self.assertFalse(any(x.media_url for x in load_catalog(path)))

    def test_wrong_scan_session_does_not_write(self):
        inbox = Inbox(self.root / 'Inbox')
        with self.assertRaises(ValueError):
            inbox.save(state('old'), 'new')
        self.assertFalse((self.root / 'Inbox' / (SID + '.csv')).exists())

    def test_invalid_media_does_not_replace_a_valid_inbox(self):
        inbox = Inbox(self.root / 'Inbox')
        path = inbox.save(state('run'), 'run')
        original = path.read_bytes()
        value = state('run')
        value['rows']['1']['mediaUrl'] = 'https://127.0.0.1/private.mp4'
        with self.assertRaises(ValueError):
            inbox.save(value, 'run')
        self.assertEqual(path.read_bytes(), original)

    def controller(self):
        controller = AutomaticDownloads(self.root, self.root / 'Inbox', self.root / 'Videos', self.root / 'Data', lambda event: None)
        controller.queue = Mock(running=False)
        return controller

    def test_watcher_automatically_queues_new_rows_without_duplicates(self):
        inbox = Inbox(self.root / 'Inbox')
        controller = self.controller()
        controller.begin(SID, 'run')
        path = inbox.save(state('run', 1), 'run')
        stamps = {p: (p.stat().st_atime_ns, p.stat().st_mtime_ns) for p in (path, path.with_suffix('.json'))}
        controller.tick()
        controller.tick()
        self.assertEqual(controller.queue.start.call_count, 1)
        self.assertEqual([x.episode for x in controller.queue.start.call_args.args[0]], [1])
        inbox.save(state('run', 2), 'run')
        # Windows can report the same timestamp for rapid replacements. New content must still queue.
        for path, stamp in stamps.items():
            os.utime(path, ns=stamp)
        controller.tick()
        self.assertEqual(controller.queue.start.call_count, 2)
        self.assertEqual([x.episode for x in controller.queue.start.call_args.args[0]], [2])

    def test_old_files_do_not_start_a_new_session(self):
        Inbox(self.root / 'Inbox').save(state('old'), 'old')
        controller = self.controller()
        controller.begin(SID, 'new')
        controller.tick()
        controller.queue.start.assert_not_called()

    def test_stop_prevents_automatic_consumption(self):
        controller = self.controller()
        controller.begin(SID, 'run')
        controller.stop()
        Inbox(self.root / 'Inbox').save(state('run'), 'run')
        controller.tick()
        controller.queue.start.assert_not_called()

    def test_only_current_series_gets_the_command(self):
        self.bootstrap()
        bridge = Bridge(self.credentials)
        token = json.loads(self.credentials.read_text())['token']
        body = {'version': EXTENSION_VERSION, 'extensionId': EXT}
        bridge.receive('/connect', 'chrome-extension://' + EXT, 'Bearer ' + token, body, 'Edg/157')
        command = bridge.command('start', SID)
        _, response = bridge.receive('/exchange', 'chrome-extension://' + EXT, 'Bearer ' + token,
            {**body, 'pageUrl': 'https://www.reelshort.com/'}, 'Edg/157')
        self.assertNotIn('command', response)
        _, response = bridge.receive('/exchange', 'chrome-extension://' + EXT, 'Bearer ' + token,
            {**body, 'pageUrl': f'https://www.reelshort.com/episodes/episode-1-test-{SID}'}, 'Edg/157')
        self.assertEqual(response['command']['id'], command)

    def test_series_id_requires_the_official_origin(self):
        self.assertEqual(series_from_page(f'https://www.reelshort.com/series-{SID}'), SID)
        self.assertEqual(series_from_page(f'https://attacker.example/series-{SID}'), '')

    def test_unacknowledged_command_expires_instead_of_leaving_start_disabled(self):
        bridge = Bridge(self.credentials)
        with patch('app.bridge.time.monotonic', return_value=100):
            bridge.command('start', SID)
        with patch('app.bridge.time.monotonic', return_value=116):
            result = bridge.status()
        self.assertEqual(result['pending_kind'], '')
        self.assertTrue(result['command_error'])

    def test_closed_website_is_not_advertised_as_an_active_series(self):
        bridge = Bridge(self.credentials)
        bridge.active_page = f'https://www.reelshort.com/series-{SID}'
        bridge.page_seen = 100
        with patch('app.bridge.time.monotonic', return_value=108):
            self.assertEqual(bridge.status()['page_url'], '')


if __name__ == '__main__':
    unittest.main()
