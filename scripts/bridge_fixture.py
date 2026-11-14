"""Offline test harness: exercises the production bridge with ephemeral credentials."""
import json
from pathlib import Path
import signal
import tempfile
import threading

from app.bridge import Bridge

with tempfile.TemporaryDirectory() as folder:
    bridge = Bridge(Path(folder) / "connection.json")
    bridge.start()
    print(json.dumps({"code": bridge.enable_pairing()}), flush=True)
    done = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: done.set())
    try:
        done.wait()
    finally:
        bridge.close()
