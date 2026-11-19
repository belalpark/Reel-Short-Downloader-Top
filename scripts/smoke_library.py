"""Download 60 synthetic library videos with real bundled tools, close at 30, resume."""
from __future__ import annotations
import argparse
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import time
from unittest.mock import Mock
from urllib.parse import urlsplit

from app.automatic import AutomaticDownloads
from app.library_engine import LibraryEngine
from app.library_store import LibraryStore
from app.media_check import MediaVerifier
from app.paths import write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    payload = base64.b64decode((Path(__file__).resolve().parents[1] / 'tests/fixture.webm.b64').read_text())
    requests, queued = [], []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            requests.append(urlsplit(self.path).path)
            self.send_response(200)
            self.send_header('Content-Type', 'video/webm')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = LibraryStore(root / 'Data/library.sqlite')
            for n in range(1, 61):
                sid = f'{n:024x}'
                store.ingest({'seriesId': sid, 'title': f'Library fixture {n}', 'total': 1,
                    'rows': {'1': {'pageUrl': f'https://www.reelshort.com/ar/episodes/episode-1-fixture-{sid}',
                    'mediaUrl': f'https://v-mps.crazymaplestudios.com/{n}.webm',
                    'sourceType': 'html', 'playbackStatus': 'playback_verified', 'captureId': f'capture-{n}'}}})
                # The internal loopback fixture is never accepted by production imports.
                with store.db() as db:
                    db.execute('UPDATE episodes SET media_url=? WHERE sid=?',
                        (f'http://127.0.0.1:{server.server_port}/{n}.webm', sid))

            opened = []
            def no_browser(url):
                opened.append(url)
                raise AssertionError('A captured, valid library source must not require Edge')

            def create_engine():
                saved = LibraryStore(root / 'Data/library.sqlite')
                saved.recover_interrupted()
                auto = AutomaticDownloads(args.root.resolve(), root / 'Inbox', root / 'Output', root / 'Data', lambda _: None)
                bridge = Mock()
                bridge.status.return_value = {'connected': False, 'page_url': '', 'pending_kind': '', 'command_error': ''}
                engine = LibraryEngine(saved, bridge, auto, args.root.resolve(), root / 'Data', root / 'Output', lambda _: None,
                    verifier=MediaVerifier(allow_loopback=True), opener=no_browser)
                callback = engine.queue.emit
                def event(value):
                    if value[0] == 'result' and value[1]['status'] == 'مكتمل ومفحوص':
                        queued.append(value[1]['series_id'])
                    callback(value)
                engine.queue.emit = event
                return saved, auto, engine

            store, auto, engine = create_engine()
            engine.start_downloads()
            def run_until(target, timeout=480):
                end = time.monotonic() + timeout
                while time.monotonic() < end:
                    engine.tick()
                    if engine.queue.thread:
                        engine.queue.thread.join(timeout=50)
                        assert not engine.queue.running, 'Real video download timed out'
                    if sum(s['done'] for s in store.series()) >= target:
                        return
                    time.sleep(0.02)
                raise AssertionError(f'Library did not reach {target} completed videos')

            run_until(30)
            assert len(queued) == 30
            first_request_counts = {f'/{n}.webm': requests.count(f'/{n}.webm') for n in range(1, 31)}
            engine.close()
            assert store.setting('resume_downloads') is True
            auto.close()
            store, auto, engine = create_engine()
            assert sum(s['done'] for s in store.series()) == 30
            engine.restore_downloads()
            assert engine.downloading
            run_until(60)
            engine.tick()
            assert not engine.downloading and store.setting('resume_downloads') is False
            assert len(queued) == 60 and len(set(queued)) == 60
            assert all(requests.count(name) == count for name, count in first_request_counts.items())
            expected_hash = hashlib.sha256(payload).hexdigest()
            for s in store.series():
                file = Path(store.episodes(s['id'])[0]['file'])
                assert hashlib.sha256(file.read_bytes()).hexdigest() == expected_hash
            engine.close()
            auto.close()
            before = len(requests)
            store, auto, engine = create_engine()
            engine.start_downloads()
            engine.tick()
            assert engine.queue.thread is None and not engine.downloading
            assert len(requests) == before and not opened

            # Production incident: a saved catalog can mix ready, missing and unindexed rows.
            sid = f'{61:024x}'
            page = f'https://www.reelshort.com/ar/episodes/episode-1-fixture-{sid}'
            store.ingest({'seriesId':sid, 'title':'Mixed catalog', 'total':2, 'rows':{'1':{
                'pageUrl':page, 'mediaUrl':'https://v-mps.crazymaplestudios.com/61.webm',
                'sourceType':'html','playbackStatus':'playback_verified','captureId':'mixed'}}})
            with store.db() as db:
                db.execute('UPDATE episodes SET media_url=? WHERE sid=? AND episode=1',
                           (f'http://127.0.0.1:{server.server_port}/61.webm',sid))
            unknown = f'{62:024x}'
            store.add_series([{'id':unknown,'title':'Unindexed','pageUrl':f'https://www.reelshort.com/ar/movie/unindexed-{unknown}'}])
            engine.start_downloads()
            run_until(61)
            engine.tick()
            assert not engine.downloading and not engine.collecting and not opened
            engine.bridge.command.assert_not_called()
            assert set(requests[before:]) == {'/61.webm'}
            mixed = store.episodes(sid)
            assert [row['status'] for row in mixed] == ['done','missing']
            assert hashlib.sha256(Path(mixed[0]['file']).read_bytes()).hexdigest() == expected_hash
            engine.close()
            auto.close()
            write_json(args.report, {'synthetic_series': 60, 'episodes_per_series': 1,
                'actual_bundled_ytdlp': True, 'actual_bundled_ffprobe': True,
                'actual_source_checks': 60, 'closed_after_completed': 30,
                'restart_restored_download_intent': True, 'resume_only_remaining': 30,
                'completed_files_requested_again': False, 'all_60_file_hashes_match': True,
                'browser_required': False, 'mixed_library_no_catalog_commands':True,
                'mixed_library_only_new_file_requested':True,'mixed_library_returns_idle_with_missing':True,
                'real_account_tested': False})
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
