"""Exercise the real download queue and bundled executables using synthetic media."""
from __future__ import annotations

import argparse
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading

from app.catalog import Episode
from app.downloader import DownloadQueue
from app.paths import write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    payload = base64.b64decode((Path(__file__).resolve().parents[1] / "tests" / "fixture.webm.b64").read_text())

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "video/webm")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            item = Episode("0" * 24, "Synthetic fixture", 1, 1, "", f"http://127.0.0.1:{server.server_port}/fixture.webm")
            # Internal test fixture only: the user-facing CSV parser still requires public HTTPS.
            events = []
            downloads = DownloadQueue(args.root.resolve(), root / "report.json", events.append)
            for attempt in range(2):
                downloads.start([item], root / "output")
                downloads.thread.join(timeout=55)
                if downloads.running:
                    downloads.cancel()
                    raise RuntimeError("Download fixture timed out")
                done = [e for e in events if e[0] == "done"][-1]
                if not done[1] or done[1][0]["status"] != "مكتمل ومفحوص":
                    raise AssertionError(repr(events))
            videos = list((root / "output").rglob("*.webm"))
            assert len(videos) == 1, videos
            assert hashlib.sha256(videos[0].read_bytes()).digest() == hashlib.sha256(payload).digest()
            write_json(args.report, {"actual_ytdlp": True, "actual_ffprobe": True, "first_download": "passed", "repeat_download": "passed", "fixture_sha256": hashlib.sha256(payload).hexdigest(), "account_tested": False})
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
