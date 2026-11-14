"""Offline integration fixture. The real receiver/watcher run; only external downloads are spied."""
import json
from pathlib import Path
import signal
import sys
import tempfile

from app.automatic import AutomaticDownloads, provision_extension
from app.bridge import Bridge


class ObservableBridge(Bridge):
    def receive(self, path, origin, auth, body, user_agent):
        status, result = super().receive(path, origin, auth, body, user_agent)
        if path in ('/connect', '/heartbeat', '/exchange') and status == 200:
            print(json.dumps({'authenticated_heartbeat': True}), flush=True)
        return status, result


class QueueSpy:
    running = False

    def start(self, entries, destination):
        print(json.dumps({'queued': [e.episode for e in entries]}), flush=True)

    def cancel(self):
        pass


with tempfile.TemporaryDirectory() as name:
    root = Path(name)
    package = Path(__file__).resolve().parents[1]
    provision_extension(package / 'extension', root / 'Extension', root / 'connection.json')
    bridge = ObservableBridge(root / 'connection.json', inbox=root / 'Inbox')
    auto = AutomaticDownloads(package, root / 'Inbox', root / 'Videos', root / 'Data', lambda event: None)
    auto.queue = QueueSpy()
    bridge.start()
    auto.start_worker()
    print(json.dumps({'extension_path': str(root / 'Extension'), 'inbox': str(root / 'Inbox')}), flush=True)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        for line in sys.stdin:
            request = json.loads(line)
            if request['command'] == 'restart':
                auto.stop()
                bridge.close()
                bridge = ObservableBridge(root / 'connection.json', inbox=root / 'Inbox')
                bridge.start()
                print(json.dumps({'bridge_restarted': True}), flush=True)
                continue
            if request['command'] == 'start':
                auto.stop()
                command = bridge.command('start', request['series_id'])
                auto.begin(request['series_id'], command)
            else:
                auto.stop()
                command = bridge.command('stop', request['series_id'])
            print(json.dumps({'command_id': command}), flush=True)
    finally:
        auto.close()
        bridge.close()
