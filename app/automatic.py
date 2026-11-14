"""Fixed inbox, automatic CSV consumption and a deduplicated download queue."""
from __future__ import annotations

import csv
import os
from pathlib import Path
import re
import secrets
import shutil
import tempfile
import threading
from urllib.parse import unquote, urlsplit

from . import EXTENSION_VERSION
from .catalog import load_catalog
from .downloader import DownloadQueue
from .paths import read_json, write_json


def series_from_page(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or parsed.netloc != "www.reelshort.com":
            return ""
        match = re.search(r"(?:^|[-/])([a-f0-9]{24})(?:[-/]|$)", unquote(parsed.path), re.I)
        return match[1].lower() if match else ""
    except (ValueError, TypeError):
        return ""


def provision_extension(package: Path, destination: Path, credentials: Path) -> str:
    """Copy immutable extension files to a stable per-user directory and provision a private token."""
    saved = read_json(credentials, {})
    token = saved.get("token")
    if not isinstance(token, str) or len(token) < 32:
        token = secrets.token_urlsafe(32)
    write_json(credentials, {"token": token, "extension_id": ""})
    destination.mkdir(parents=True, exist_ok=True)
    for source in package.iterdir():
        if source.is_file() and source.name != "desktop_config.json":
            shutil.copy2(source, destination / source.name)
    write_json(destination / "desktop_config.json", {"version": EXTENSION_VERSION, "token": token})
    return token


class Inbox:
    def __init__(self, folder: Path):
        self.folder = folder
        self.lock = threading.Lock()

    def save(self, state: dict, run_id: str) -> Path:
        sid = str(state.get("seriesId", ""))
        total = state.get("total")
        if not re.fullmatch(r"[a-f0-9]{24}", sid) or type(total) is not int or not 1 <= total <= 150:
            raise ValueError("Invalid series index")
        if not run_id or state.get("desktopCommandId") != run_id or not isinstance(state.get("rows"), dict):
            raise ValueError("Unexpected scan session")
        rows = []
        for ep in range(1, total + 1):
            item = state["rows"].get(str(ep), {})
            if not isinstance(item, dict):
                raise ValueError("Invalid episode row")
            page = str(item.get("pageUrl", ""))
            if series_from_page(page) != sid or not re.search(rf"/episodes/episode-{ep}(?:[-/]|$)", urlsplit(page).path, re.I):
                page = ""
            title = str(state.get("title", "Series"))[:250]
            if re.match(r"^[\s]*[=+@-]", title):
                title = "'" + title
            rows.append({"SeriesID": sid, "SeriesTitle": title, "Episode": ep, "TotalEpisodes": total,
                         "PageURL": page, "MediaURL": str(item.get("mediaUrl", "")),
                         "PlaybackStatus": str(item.get("playbackStatus", "not_checked"))[:100]})
        self.folder.mkdir(parents=True, exist_ok=True)
        target = self.folder / (sid + ".csv")
        with self.lock:
            fd, name = tempfile.mkstemp(prefix="incoming-", suffix=".csv", dir=self.folder)
            try:
                with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as output:
                    writer = csv.DictWriter(output, fieldnames=rows[0].keys())
                    writer.writeheader()
                    writer.writerows(rows)
                load_catalog(Path(name))  # Same validation used by the actual downloader.
                os.replace(name, target)
                write_json(target.with_suffix(".json"), {"run_id": run_id, "series_id": sid,
                    "revision": secrets.token_hex(16),
                    "running": bool(state.get("running")), "paused": bool(state.get("paused")), "finished": bool(state.get("finished")),
                    "pass": int(state.get("autoPass", 1)), "note": str(state.get("note", ""))[:500],
                    "updated_at": str(state.get("updatedAt", ""))})
            finally:
                Path(name).unlink(missing_ok=True)
        return target


class AutomaticDownloads:
    def __init__(self, package: Path, inbox: Path, destination: Path, data: Path, emit):
        self.inbox, self.destination, self.data, self.emit = inbox, destination, data, emit
        self.lock = threading.RLock()
        self.enabled = False
        self.series_id = self.run_id = ""
        self.entries = []
        self.submitted = {}
        self.completed = read_json(data / "completed.json", {})
        if not isinstance(self.completed, dict):
            self.completed = {}
        self.last_stamp = None
        self.shutdown = threading.Event()
        self.queue = DownloadQueue(package, data / "last_session.json", self._download_event)
        self.worker = None

    def start_worker(self):
        self.worker = threading.Thread(target=self._watch, daemon=True, name="automatic-inbox")
        self.worker.start()

    def begin(self, series_id: str, run_id: str):
        with self.lock:
            if self.enabled or self.queue.running:
                raise ValueError("التنزيل يعمل بالفعل؛ اضغط إيقاف قبل بدء مسلسل آخر.")
            self.series_id, self.run_id = series_id, run_id
            self.submitted, self.entries = {}, []
            self.last_stamp = None
            self.enabled = True

    def stop(self):
        with self.lock:
            self.enabled = False
        self.queue.cancel()

    def _key(self, episode):
        return self.series_id + "/" + str(episode)

    def _is_complete(self, item):
        path = self.completed.get(self._key(item.episode))
        if not isinstance(path, str):
            return False
        file = Path(path)
        return file.is_file() and file.stat().st_size >= 128 and file.resolve().is_relative_to(self.destination.resolve())

    def tick(self):
        with self.lock:
            if not self.enabled:
                return
            path = self.inbox / (self.series_id + ".csv")
            meta_path = path.with_suffix(".json")
            meta = read_json(meta_path, {})
            if meta.get("run_id") != self.run_id or not path.is_file():
                return
            stamp = meta.get("revision") or (path.stat().st_mtime_ns, meta_path.stat().st_mtime_ns)
            if stamp != self.last_stamp:
                entries = load_catalog(path)
                if entries[0].series_id != self.series_id:
                    raise ValueError("Unexpected inbox series")
                self.entries, self.last_stamp = entries, stamp
                self.emit(("catalog", entries, meta, dict(self.completed)))
            if self.queue.running:
                return
            pending = [e for e in self.entries if e.media_url and not self._is_complete(e)
                       and self.submitted.get(e.episode) != e.media_url]
            if pending:
                for item in pending:
                    self.submitted[item.episode] = item.media_url
                self.queue.start(pending, self.destination)

    def _download_event(self, event):
        if event[0] == "done":
            with self.lock:
                for result in event[1]:
                    if result["status"] == "مكتمل ومفحوص" and result.get("file"):
                        self.completed[result["series_id"] + "/" + str(result["episode"])] = result["file"]
                write_json(self.data / "completed.json", self.completed)
        self.emit(event)

    def _watch(self):
        while not self.shutdown.wait(0.5):
            try:
                self.tick()
            except (OSError, ValueError) as error:
                self.emit(("log", str(error)))

    def close(self):
        self.shutdown.set()
        self.stop()
        if self.worker:
            self.worker.join(timeout=2)
