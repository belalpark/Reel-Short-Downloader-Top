"""Real extension/store/engine, synthetic pages and controlled external downloader."""
import base64
import json
from pathlib import Path
import signal
import sys
import tempfile
import threading
import time

from app.automatic import AutomaticDownloads, provision_extension
from app.bridge import Bridge
from app.library_engine import LibraryEngine
from app.library_store import LibraryStore
from app.media_check import SourceCheck


def page(sid):
    return f'https://www.reelshort.com/ar/episodes/episode-1-library-{sid}'


def source(sid):
    return f'https://v-mps.crazymaplestudios.com/library-{sid}.mp4'


def snapshot(sid):
    return {'seriesId': sid, 'title': 'Library fixture ' + str(int(sid, 16)), 'total': 1,
        'rows': {'1': {'pageUrl': page(sid), 'mediaUrl': source(sid),
        'playbackStatus': 'playback_verified', 'sourceType': 'html', 'captureId': 'seed'}}}


class Verifier:
    def check(self, *_):
        return SourceCheck(True, '', time.time(), 0)


class QueueSpy:
    running = False

    def __init__(self, engine, payload):
        self.engine, self.payload = engine, payload

    def start(self, entries, destination):
        self.running = True
        print(json.dumps({'queued': [e.series_id for e in entries]}), flush=True)
        def finish():
            try:
                time.sleep(0.03)
                for item in entries:
                    file = destination / item.series_id / 'Episode_001.webm'
                    file.parent.mkdir(parents=True, exist_ok=True)
                    file.write_bytes(self.payload)
                    self.engine._download_event(('result', {'status': 'مكتمل ومفحوص', 'file': str(file)}))
            finally:
                self.running = False
        threading.Thread(target=finish, daemon=True).start()

    def cancel(self):
        pass


with tempfile.TemporaryDirectory() as name:
    root = Path(name)
    package = Path(__file__).resolve().parents[1]
    payload = base64.b64decode((package / 'tests/fixture.webm.b64').read_text())
    provision_extension(package / 'extension', root / 'Extension', root / 'connection.json')
    store = LibraryStore(root / 'Data/library.sqlite')
    auto = AutomaticDownloads(package, root / 'Inbox', root / 'Output', root / 'Data', lambda _: None)
    bridge = Bridge(root / 'connection.json', inbox=root / 'Inbox', progress=auto.progress, library=store)
    engine = LibraryEngine(store, bridge, auto, package, root / 'Data', root / 'Output', lambda _: None,
        verifier=Verifier(), opener=lambda url: print(json.dumps({'open_request': url}), flush=True))
    engine.queue = QueueSpy(engine, payload)
    bridge.snapshot_handler = engine.on_snapshot
    for n in range(1, 59):
        sid = f'{n:024x}'
        store.ingest(snapshot(sid))
        store.update_source(store.episodes(sid)[0], 'ready', verified=time.time())
    bridge.start()
    # This fixture already knows the valid seed files: avoid invoking a Windows
    # ffprobe binary on Linux. Real ffprobe validation is covered by smoke_library.
    auto._completed_file = lambda sid, ep: (lambda p: p and Path(p).is_file())(auto.completed.get(f'{sid}/{ep}'))
    auto.recover_completed = lambda sid, *_: auto.completed_episodes(sid)
    engine.start_worker()
    print(json.dumps({'extension_path': str(root / 'Extension')}), flush=True)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        for line in sys.stdin:
            request = json.loads(line)
            command = request['command']
            if command == 'collect':
                engine.start_collection()
            elif command == 'download':
                engine.start_downloads()
            elif command == 'refresh_one':
                sid = request['series_id']
                file = Path(auto.completed.pop(sid + '/1'))
                file.unlink()
                store.update_source(store.episodes(sid)[0], 'needs_refresh', error='source_expired')
                engine.start_downloads()
            elif command == 'status':
                print(json.dumps({'status': {'series': store.series(), 'collecting': engine.collecting,
                    'downloading': engine.downloading, 'capture': bool(engine.capture),
                    'discovery': bool(engine.discovery_id)}}), flush=True)
    finally:
        engine.close()
        bridge.close()
