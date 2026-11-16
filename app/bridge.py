"""Authenticated local bridge with automatic setup, scan commands and a fixed CSV inbox."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import socket
import sqlite3
import threading
import time
from urllib.parse import urlsplit

from . import EXTENSION_VERSION, VERSION
from .paths import read_json, write_json
from .automatic import Inbox, series_from_page

PORT = 18765
STALE_SECONDS = 75
ORIGIN = re.compile(r"^chrome-extension://([a-p]{32})$")


class Bridge:
    def __init__(self, credentials: Path, port: int = PORT, inbox: Path | None = None, progress=None, library=None):
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
        self.inbox = Inbox(inbox or credentials.parent / "Inbox")
        self.pending_command = None
        self.active_run_id = ""
        self.active_series_id = ""
        self.active_page = ""
        self.page_seen = 0.0
        self.command_since = 0.0
        self.command_error = ""
        self.download_progress = progress
        self.library = library
        self.snapshot_handler = None
        self.catalog_job_id = ''
        self.catalog_progress = {}

    def catalog_status(self):
        with self.lock:
            return dict(self.catalog_progress)

    def require_fresh_heartbeat(self):
        with self.lock:
            self.last_seen = 0

    def enable_pairing(self) -> str:
        with self.lock:
            self.pair_code = f"{secrets.randbelow(100000000):08d}"
            self.pair_until = time.monotonic() + 300
            self.attempts = 0
            return self.pair_code

    def status(self) -> dict:
        with self.lock:
            if self.pending_command and time.monotonic() - self.command_since > (60 if self.pending_command['kind'] in ('open', 'catalog') or self.pending_command.get('collectOnly') else 15):
                if self.pending_command["kind"] == "start":
                    self.command_error = "تعذر بدء الفحص. افتح المسلسل في Edge ثم اضغط بدء التنزيل مجددًا."
                self.pending_command = None
            age = time.monotonic() - self.last_seen if self.last_seen else None
            page = self.active_page if self.page_seen and time.monotonic() - self.page_seen < 7 else ""
            return {"connected": age is not None and age < STALE_SECONDS and self.browser == "Edge", "browser": self.browser, "age_seconds": round(age, 1) if age is not None else None, "extension_version": EXTENSION_VERSION, "page_url": page, "command_error": self.command_error, "pending_kind": self.pending_command["kind"] if self.pending_command else ""}

    def command(self, kind: str, series_id: str = "", *, patient=False, completed_episodes=(), page_url='', collect_only=False, collected_episodes=(), target_episodes=()) -> str:
        if kind not in ("start", "stop", "open", "catalog"):
            raise ValueError("Unsupported command")
        if kind in ('open', 'catalog'):
            parsed = urlsplit(page_url)
            if parsed.scheme != 'https' or parsed.netloc != 'www.reelshort.com' or len(page_url) > 4096:
                raise ValueError('Invalid website page')
        with self.lock:
            command_id = secrets.token_hex(16)
            self.pending_command = {"id": command_id, "kind": kind, "series_id": series_id}
            if kind == "start":
                self.pending_command.update(patient=bool(patient), completedEpisodes=sorted({n for n in completed_episodes if type(n) is int and 1 <= n <= 150}))
                self.pending_command.update(collectOnly=bool(collect_only), collectedEpisodes=sorted({n for n in collected_episodes if type(n) is int and 1 <= n <= 150}), targetEpisodes=sorted({n for n in target_episodes if type(n) is int and 1 <= n <= 150}))
            if kind in ('open', 'catalog'):
                self.pending_command['pageUrl'] = page_url
                self.active_run_id = self.active_series_id = self.active_page = ''
                self.page_seen = 0
            if kind == 'catalog':
                self.catalog_job_id = command_id
                self.catalog_progress = {'id': command_id, 'done': False, 'count': 0, 'updated': time.time()}
            if kind == 'stop':
                self.catalog_job_id = ''
            self.command_since = time.monotonic()
            self.command_error = ""
            if kind == "start":
                self.active_run_id, self.active_series_id = command_id, series_id
            return command_id

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
            elif path in ("/connect", "/heartbeat", "/exchange", "/snapshot", "/ack", "/progress", "/library/catalog"):
                if not self.token or not hmac.compare_digest(auth, "Bearer " + self.token):
                    return 401, {"error": "pairing_required"}
                if path == "/connect" and not self.extension_id:
                    self.extension_id = match[1]
                    write_json(self.credentials, {"token": self.token, "extension_id": self.extension_id})
                if self.extension_id != match[1]:
                    return 401, {"error": "unexpected_extension"}
            else:
                return 404, {"error": "not_found"}
            self.last_seen = time.monotonic()
            self.browser = "Edge" if re.search(r"\bEdg/\d", user_agent) else "Other"
            reply = {"ok": True, "appVersion": VERSION, "browser": self.browser}
            if path == "/pair":
                reply["token"] = self.token
            if path == "/progress":
                progress = self.download_progress() if self.download_progress else {}
                if progress.get("runId") == self.active_run_id and progress.get("seriesId") == self.active_series_id:
                    reply["download"] = progress
            if path == "/exchange":
                reply["runId"] = self.active_run_id
                page = str(body.get("pageUrl", ""))[:4096]
                if page.startswith("https://www.reelshort.com/"):
                    self.active_page = page
                    self.page_seen = time.monotonic()
                if self.pending_command and (self.pending_command['kind'] in ('open', 'catalog') or not self.pending_command["series_id"] or series_from_page(page) == self.pending_command["series_id"]):
                    reply["command"] = dict(self.pending_command)
            if path == "/ack" and self.pending_command and body.get("commandId") == self.pending_command["id"]:
                if self.pending_command['kind'] == 'open':
                    self.active_page, self.page_seen = '', 0
                self.command_error = str(body.get("error", ""))[:250]
                self.pending_command = None
            if path == '/library/catalog':
                if not self.library or not self.catalog_job_id or body.get('jobId') != self.catalog_job_id:
                    return 409, {'error': 'unexpected_catalog_job'}
                try:
                    self.library.add_series(body.get('items', []))
                except (ValueError, TypeError):
                    return 422, {'error': 'invalid_library_catalog'}
                self.catalog_progress = {'id': self.catalog_job_id, 'done': bool(body.get('done')), 'count': len(self.library.series()), 'error': str(body.get('error', ''))[:100], 'updated': time.time()}
            if path == "/snapshot":
                state = body.get("state")
                if not isinstance(state, dict) or state.get("seriesId") != self.active_series_id or state.get("desktopCommandId") != self.active_run_id:
                    return 409, {"error": "unexpected_scan_session"}
                try:
                    self.inbox.save(state, self.active_run_id)
                    if self.snapshot_handler:
                        self.snapshot_handler(state)
                except ValueError:
                    return 422, {"error": "invalid_episode_index"}
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

            def finish(self):
                try:
                    super().finish()
                finally:
                    # Complete the HTTP/1.0 close handshake before releasing the
                    # listener. Windows exclusive sockets need graceful closure.
                    try:
                        self.connection.shutdown(socket.SHUT_WR)
                        deadline = time.monotonic() + 0.5
                        while time.monotonic() < deadline:
                            self.connection.settimeout(max(0.01, deadline - time.monotonic()))
                            if not self.connection.recv(4096):
                                break
                    except OSError:
                        pass

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
                    if not raw_length.isdecimal() or not 0 < int(raw_length) <= 2 * 1024 * 1024 or self.headers.get("Transfer-Encoding"):
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
                except (OSError, sqlite3.Error):
                    code, reply = 500, {"error": "cannot_save_pairing"}
                self._reply(code, reply)

        class Server(ThreadingHTTPServer):
            daemon_threads = False
            block_on_close = True
            allow_reuse_address = os.name != "nt"
            allow_reuse_port = False
            request_queue_size = 8

            def server_bind(self):
                if os.name == "nt":
                    self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                super().server_bind()

        self.server = Server(("127.0.0.1", self.port), Handler)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True, name="readiness-bridge")
        self.thread.start()

    def close(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
