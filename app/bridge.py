"""A paired, loopback-only readiness channel. It never accepts download commands."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
from pathlib import Path
import re
import secrets
import threading
import time

from . import EXTENSION_VERSION, VERSION
from .paths import read_json, write_json

PORT = 18765
ORIGIN = re.compile(r"^chrome-extension://([a-p]{32})$")


class Bridge:
    def __init__(self, credentials: Path, port: int = PORT):
        self.port = port
        self.credentials = credentials
        self.lock = threading.Lock()
        self.pair_code = ""
        self.pair_until = 0.0
        self.attempts = 0
        self.last_seen = 0.0
        self.browser = ""
        saved = read_json(credentials, {})
        self.token = str(saved.get("token", ""))
        self.extension_id = str(saved.get("extension_id", ""))
        self.server = None
        self.thread = None

    def enable_pairing(self) -> str:
        with self.lock:
            self.pair_code = f"{secrets.randbelow(100000000):08d}"
            self.pair_until = time.monotonic() + 300
            self.attempts = 0
            return self.pair_code

    def status(self) -> dict:
        with self.lock:
            age = time.monotonic() - self.last_seen if self.last_seen else None
            return {"connected": age is not None and age < 20 and self.browser == "Edge", "browser": self.browser, "age_seconds": round(age, 1) if age is not None else None, "extension_version": EXTENSION_VERSION}

    def receive(self, path: str, origin: str, auth: str, body: dict, user_agent: str) -> tuple[int, dict]:
        match = ORIGIN.fullmatch(origin)
        if not match:
            return 403, {"error": "extension_origin_required"}
        if body.get("version") != EXTENSION_VERSION or body.get("extensionId") != match[1]:
            return 409, {"error": "extension_version_or_identity_mismatch"}
        with self.lock:
            if path == "/pair":
                if time.monotonic() > self.pair_until or self.attempts >= 5 or not self.pair_code:
                    return 403, {"error": "pairing_expired_or_locked"}
                self.attempts += 1
                code = body.get("code")
                if not isinstance(code, str) or not hmac.compare_digest(code, self.pair_code):
                    return 403, {"error": "invalid_code"}
                token = secrets.token_urlsafe(32)
                # Persist before accepting the pair; a disk failure must not produce a false success.
                write_json(self.credentials, {"token": token, "extension_id": match[1]})
                self.extension_id, self.token = match[1], token
                self.pair_code = ""
            elif path == "/heartbeat":
                if not self.token or not hmac.compare_digest(auth, "Bearer " + self.token) or self.extension_id != match[1]:
                    return 401, {"error": "pairing_required"}
            else:
                return 404, {"error": "not_found"}
            self.last_seen = time.monotonic()
            self.browser = "Edge" if re.search(r"\bEdg/\d", user_agent) else "Other"
            reply = {"ok": True, "appVersion": VERSION, "browser": self.browser}
            if path == "/pair":
                reply["token"] = self.token
            return 200, reply

    def start(self) -> None:
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *_):
                pass  # Never log local credentials or user-supplied headers.

            def setup(self):
                super().setup()
                self.connection.settimeout(3)

            def _valid_host(self):
                return self.headers.get("Host") == f"127.0.0.1:{bridge.port}"

            def _reply(self, code, data):
                encoded = json.dumps(data).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.send_header("Cache-Control", "no-store")
                origin = self.headers.get("Origin", "")
                if ORIGIN.fullmatch(origin):
                    self.send_header("Access-Control-Allow-Origin", origin)
                    self.send_header("Vary", "Origin")
                self.end_headers()
                self.wfile.write(encoded)

            def do_OPTIONS(self):
                if not self._valid_host() or not ORIGIN.fullmatch(self.headers.get("Origin", "")):
                    self._reply(403, {"error": "invalid_origin_or_host"})
                    return
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", self.headers["Origin"])
                self.send_header("Access-Control-Allow-Methods", "POST")
                self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
                self.send_header("Access-Control-Allow-Private-Network", "true")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self):
                self._reply(405, {"error": "post_required"})

            def do_POST(self):
                if not self._valid_host():
                    self._reply(403, {"error": "invalid_host"})
                    return
                try:
                    raw_length = self.headers.get("Content-Length", "")
                    if not raw_length.isdecimal() or not 0 < int(raw_length) <= 2048 or self.headers.get("Transfer-Encoding"):
                        raise ValueError()
                    if self.headers.get_content_type() != "application/json":
                        raise ValueError()
                    data = json.loads(self.rfile.read(int(raw_length)))
                    if not isinstance(data, dict):
                        raise ValueError()
                except (ValueError, UnicodeError, TimeoutError):
                    self._reply(400, {"error": "invalid_request"})
                    return
                try:
                    code, reply = bridge.receive(self.path, self.headers.get("Origin", ""), self.headers.get("Authorization", ""), data, self.headers.get("User-Agent", ""))
                except OSError:
                    code, reply = 500, {"error": "cannot_save_pairing"}
                self._reply(code, reply)

        class Server(ThreadingHTTPServer):
            daemon_threads = True
            allow_reuse_address = False
            request_queue_size = 8

        self.server = Server(("127.0.0.1", self.port), Handler)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True, name="readiness-bridge")
        self.thread.start()

    def close(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
