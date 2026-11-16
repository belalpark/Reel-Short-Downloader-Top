"""Fixed inbox, automatic CSV consumption and a deduplicated download queue."""
from __future__ import annotations

import csv
from dataclasses import replace
import os
from pathlib import Path
import re
import secrets
import shutil
import tempfile
import threading
import time
from urllib.parse import unquote, urlsplit

from . import EXTENSION_VERSION
from .catalog import load_catalog
from .downloader import DownloadQueue, validate_video
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
                         "PlaybackStatus": str(item.get("playbackStatus", "not_checked"))[:100],
                         "SourceStatus": str(item.get("status", ""))[:100], "SourceType": str(item.get("sourceType", ""))[:100],
                         "CaptureID": str(item.get("captureId", ""))[:100]})
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
        self.package = package
        self.lock = threading.RLock()
        self.enabled = False
        self.series_id = self.run_id = ""
        self.entries = []
        self.submitted = {}
        self.capture_submitted = {}
        self.patient = False
        self.failures, self.retry_at, self.outcomes = {}, {}, {}
        self.active_episode = 0
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

    def begin(self, series_id: str, run_id: str, patient: bool = False):
        with self.lock:
            if self.enabled or self.queue.running:
                raise ValueError("التنزيل يعمل بالفعل؛ اضغط إيقاف قبل بدء مسلسل آخر.")
            self.series_id, self.run_id = series_id, run_id
            self.submitted, self.entries = {}, []
            self.capture_submitted = {}
            self.failures, self.retry_at, self.outcomes = {}, {}, {}
            self.active_episode = 0
            self.patient = patient
            if patient:
                try:
                    saved = load_catalog(self.inbox / (series_id + ".csv"))
                    if saved[0].series_id == series_id:
                        self.entries = [replace(e, media_url="", capture_id="") for e in saved]
                except (OSError, ValueError):
                    pass
            self.last_stamp = None
            self.enabled = True

    def stop(self):
        with self.lock:
            self.enabled = False
        self.queue.cancel()

    def _key(self, episode):
        return self.series_id + "/" + str(episode)

    def _completed_file(self, series_id, episode):
        path = self.completed.get(series_id + "/" + str(episode))
        if not isinstance(path, str):
            return False
        file = Path(path)
        try:
            return file.is_file() and file.stat().st_size >= 128 and file.resolve().is_relative_to(self.destination.resolve())
        except OSError:
            return False

    def _is_complete(self, item):
        return self._completed_file(item.series_id, item.episode)

    def completed_episodes(self, series_id=None):
        sid = series_id or self.series_id
        return [n for n in range(1, 151) if self._completed_file(sid, n)]

    def recover_completed(self, series_id, cancelled=lambda: False):
        """Recover valid files older versions saved before their batch checkpoint."""
        if not re.fullmatch(r"[a-f0-9]{24}", series_id):
            raise ValueError("Invalid series")
        destination = self.destination.resolve()
        recovered = {}
        for folder in destination.glob('*_' + series_id):
            if cancelled():
                return self.completed_episodes(series_id)
            if not folder.is_dir():
                continue
            for file in folder.glob('Episode_*'):
                if cancelled():
                    return self.completed_episodes(series_id)
                match = re.fullmatch(r'Episode_([0-9]{3})\.(mp4|mkv|webm|m4v|mov)', file.name, re.I)
                if not match or not 1 <= int(match[1]) <= 150 or self._completed_file(series_id, int(match[1])):
                    continue
                if file.resolve().is_relative_to(destination) and validate_video(self.package, file) and not cancelled():
                    recovered[series_id + '/' + str(int(match[1]))] = str(file.resolve())
        if recovered:
            with self.lock:
                self.completed.update(recovered)
                write_json(self.data / 'completed.json', self.completed)
        return self.completed_episodes(series_id)

    def progress(self):
        with self.lock:
            outcome = self.outcomes.get(self.active_episode, {})
            return {"runId": self.run_id, "seriesId": self.series_id, "enabled": self.enabled,
                    "completedEpisodes": self.completed_episodes(), "episode": self.active_episode,
                    "outcome": dict(outcome), "retryInMs": max(0, round((self.retry_at.get(self.active_episode, 0) - time.monotonic()) * 1000))}

    def pending(self):
        remaining = [e for e in self.entries if not self._is_complete(e)]
        candidates = remaining[:1] if self.patient else remaining
        return [e for e in candidates if e.media_url and (not self.patient or e.capture_id) and
                (self.submitted.get(e.episode) != e.media_url or
                 (self.patient and self.capture_submitted.get(e.episode) != e.capture_id))
                and time.monotonic() >= self.retry_at.get(e.episode, 0)]

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
            pending = self.pending()
            if pending:
                for item in pending:
                    self.submitted[item.episode] = item.media_url
                    self.capture_submitted[item.episode] = item.capture_id
                    self.outcomes.pop(item.episode, None)
                self.active_episode = pending[0].episode
                self.queue.start(pending, self.destination)

    def _download_event(self, event):
        if event[0] in ("result", "done"):
            with self.lock:
                changed = False
                for result in ([event[1]] if event[0] == "result" else event[1]):
                    if result.get("series_id") != self.series_id:
                        continue
                    episode = result["episode"]
                    if result["status"] == "مكتمل ومفحوص" and result.get("file"):
                        self.completed[result["series_id"] + "/" + str(episode)] = result["file"]
                        self.outcomes.pop(episode, None)
                        self.retry_at.pop(episode, None)
                        changed = True
                    elif event[0] == "result" and self.enabled and not result["status"].startswith("متوقف"):
                        attempts = self.failures.get(episode, 0) + 1
                        self.failures[episode] = attempts
                        self.retry_at[episode] = time.monotonic() + min(60, 3 * 2 ** min(5, attempts - 1))
                        self.outcomes[episode] = {"status": "failed", "captureId": result.get("capture_id", self.capture_submitted.get(episode, "")), "attempts": attempts}
                if changed:
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
