"""Real bundled-tool repair, per-episode checkpoint and missing-only resume test."""
from __future__ import annotations
import argparse
import base64
from dataclasses import replace
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import time
from urllib.parse import urlsplit

from app.automatic import AutomaticDownloads
from app.catalog import Episode
from app.paths import write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    payload = base64.b64decode((Path(__file__).resolve().parents[1] / 'tests/fixture.webm.b64').read_text())
    state = {'valid_second': False, 'requests': []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            name = urlsplit(self.path).path
            state['requests'].append(name)
            body = payload if name == '/1.webm' or state['valid_second'] else b'Not a video: transient server response'
            self.send_response(200)
            self.send_header('Content-Type', 'video/webm')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sid = 'd' * 24
            entries = [Episode(sid, 'Repair fixture', n, 2, '', f'http://127.0.0.1:{server.server_port}/{n}.webm', capture_id=f'capture-{n}') for n in (1, 2)]
            # Internal loopback fixture only. User CSV validation still requires public HTTPS.
            def prepare(auto, run, values):
                auto.begin(sid, run, patient=True)
                auto.inbox.mkdir(parents=True, exist_ok=True)
                path = auto.inbox / (sid + '.csv')
                path.write_text('Internal queue fixture; not a user import')
                write_json(path.with_suffix('.json'), {'run_id': run, 'revision': run})
                auto.entries, auto.last_stamp = values, run

            auto = AutomaticDownloads(args.root.resolve(), root / 'Inbox', root / 'Output', root / 'Data', lambda event: None)
            prepare(auto, 'first-run', entries)
            auto.tick()
            auto.queue.thread.join(timeout=55)
            assert not auto.queue.running and auto.completed_episodes() == [1]
            saved_first = Path(auto.completed[sid + '/1'])
            first_hash = hashlib.sha256(saved_first.read_bytes()).digest()
            first_requests = state['requests'].count('/1.webm')
            assert sid + '/1' in json.loads((root / 'Data/completed.json').read_text())

            auto.tick()
            auto.queue.thread.join(timeout=55)
            assert not auto.queue.running and auto.outcomes[2]['status'] == 'failed'
            assert list((root / 'Output').rglob('*.invalid')), 'Invalid video must not occupy its final output name'
            assert auto.completed_episodes() == [1]
            state['valid_second'] = True
            auto.entries = [entries[0], replace(entries[1], capture_id='fresh-capture-same-url')]
            previous_thread = auto.queue.thread
            end = time.monotonic() + 12
            while auto.queue.thread is previous_thread and time.monotonic() < end:
                auto.tick()
                if auto.queue.thread is previous_thread:
                    time.sleep(0.1)
            assert auto.queue.thread is not previous_thread, 'Fresh capture was not retried after cooldown'
            auto.queue.thread.join(timeout=55)
            assert not auto.queue.running and auto.completed_episodes() == [1, 2]
            assert hashlib.sha256(saved_first.read_bytes()).digest() == first_hash
            saved_second = Path(auto.completed[sid + '/2'])
            assert hashlib.sha256(saved_second.read_bytes()).digest() == hashlib.sha256(payload).digest()
            auto.close()

            reopened = AutomaticDownloads(args.root.resolve(), root / 'Inbox', root / 'Output', root / 'Data', lambda event: None)
            prepare(reopened, 'resume-run', entries)
            reopened.tick()
            assert reopened.queue.thread is None
            assert reopened.completed_episodes() == [1, 2]
            assert state['requests'].count('/1.webm') == first_requests
            reopened.close()
            write_json(args.report, {'actual_bundled_ytdlp': True, 'actual_bundled_ffprobe': True,
                'first_episode_checkpointed_before_second': True, 'invalid_output_released_for_repair': True,
                'same_url_fresh_capture_retried': True, 'retry_obeys_cooldown': True,
                'repaired_video_sha256_matches': True, 'reopen_downloads_completed_again': False,
                'completed_episodes': [1, 2], 'real_account_tested': False})
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
