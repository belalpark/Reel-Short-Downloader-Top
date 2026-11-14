"""Offline test harness: exercises the production bridge with ephemeral credentials."""
import json
from pathlib import Path
import signal
import tempfile
import threading

from app.bridge import Bridge


class ObservableBridge(Bridge):
    def receive(self, path, origin, auth, body, user_agent):
        status, result = super().receive(path, origin, auth, body, user_agent)
        if path == "/heartbeat" and status == 200:
            print(json.dumps({"authenticated_heartbeat": True}), flush=True)
        return status, result


with tempfile.TemporaryDirectory() as folder:
    bridge = ObservableBridge(Path(folder) / "connection.json")
    bridge.start()
    print(json.dumps({"code": bridge.enable_pairing()}), flush=True)
    done = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: done.set())
    try:
        done.wait()
    finally:
        bridge.close()
