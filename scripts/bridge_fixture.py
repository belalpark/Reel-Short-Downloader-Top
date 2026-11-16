"""Offline integration fixture. The real receiver/watcher run; only external downloads are spied."""
import json
import base64
from pathlib import Path
import signal
import sys
import tempfile
import threading
import time

from app.automatic import AutomaticDownloads, provision_extension
from app.bridge import Bridge
from app.paths import write_json


class ObservableBridge(Bridge):
    def receive(self, path, origin, auth, body, user_agent):
        status, result = super().receive(path, origin, auth, body, user_agent)
        if path in ('/connect', '/heartbeat', '/exchange') and status == 200:
            print(json.dumps({'authenticated_heartbeat': True}), flush=True)
        return status, result


class QueueSpy:
    running = False

    def __init__(self, auto):
        self.auto = auto
        self.failures = {}

    def start(self, entries, destination):
        print(json.dumps({'queued': [e.episode for e in entries]}), flush=True)
        if not self.auto.patient:
            return
        self.running = True
        def finish():
            try:
                time.sleep(0.25)
                for item in entries:
                    left = self.failures.get(item.episode, 0)
                    if left:
                        self.failures[item.episode] = left - 1
                        result = {'series_id': item.series_id, 'episode': item.episode, 'status': 'فشل', 'file': '', 'capture_id': item.capture_id}
                    else:
                        file = destination / ('Episode_%03d.webm' % item.episode)
                        file.parent.mkdir(parents=True, exist_ok=True)
                        file.write_bytes(base64.b64decode((package / 'tests/fixture.webm.b64').read_text()))
                        result = {'series_id': item.series_id, 'episode': item.episode, 'status': 'مكتمل ومفحوص', 'file': str(file), 'capture_id': item.capture_id}
                    self.auto._download_event(('result', result))
                    print(json.dumps({'download_result': {'episode': item.episode, 'success': not left, 'capture_id': item.capture_id}}), flush=True)
            finally:
                self.running = False
        threading.Thread(target=finish, daemon=True).start()

    def cancel(self):
        pass


with tempfile.TemporaryDirectory() as name:
    root = Path(name)
    package = Path(__file__).resolve().parents[1]
    provision_extension(package / 'extension', root / 'Extension', root / 'connection.json')
    auto = AutomaticDownloads(package, root / 'Inbox', root / 'Videos', root / 'Data', lambda event: None)
    auto.queue = QueueSpy(auto)
    bridge = ObservableBridge(root / 'connection.json', inbox=root / 'Inbox', progress=auto.progress)
    bridge.start()
    auto.start_worker()
    print(json.dumps({'extension_path': str(root / 'Extension'), 'inbox': str(root / 'Inbox')}), flush=True)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        for line in sys.stdin:
            request = json.loads(line)
            if request['command'] == 'restart':
                auto.close()
                bridge.close()
                auto = AutomaticDownloads(package, root / 'Inbox', root / 'Videos', root / 'Data', lambda event: None)
                auto.queue = QueueSpy(auto)
                auto.start_worker()
                bridge = ObservableBridge(root / 'connection.json', inbox=root / 'Inbox', progress=auto.progress)
                bridge.start()
                print(json.dumps({'bridge_restarted': True}), flush=True)
                continue
            if request['command'] == 'start':
                auto.stop()
                sid = request['series_id']
                for ep in request.get('seed_completed', []):
                    file = root / 'Videos' / ('Episode_%03d.webm' % ep)
                    file.parent.mkdir(parents=True, exist_ok=True)
                    file.write_bytes(base64.b64decode((package / 'tests/fixture.webm.b64').read_text()))
                    auto.completed[sid + '/' + str(ep)] = str(file)
                write_json(root / 'Data/completed.json', auto.completed)
                patient = bool(request.get('patient'))
                command = bridge.command('start', sid, patient=patient, completed_episodes=auto.completed_episodes(sid))
                auto.begin(sid, command, patient=patient)
                auto.queue.failures = {int(k): int(v) for k, v in request.get('download_failures', {}).items()}
            else:
                auto.stop()
                command = bridge.command('stop', request['series_id'])
            print(json.dumps({'command_id': command}), flush=True)
    finally:
        auto.close()
        bridge.close()
