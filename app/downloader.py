from __future__ import annotations

import json
import os
from pathlib import Path
import re
import signal
import subprocess
import threading

from .catalog import Episode, safe_name
from .paths import write_json
from .runtime import process_options


def redact(value: str) -> str:
    return re.sub(r"https?://[^\s<>\"']+", "[URL]", value)[:600]


def build_command(root: Path, output: Path, item: Episode) -> list[str]:
    if not item.media_url:
        raise ValueError("No explicit media URL")
    args = [str(root / "tools" / "yt-dlp.exe"), "--ignore-config", "--no-playlist", "--no-simulate", "--continue", "--no-overwrites", "--windows-filenames", "--no-mtime", "--no-colors", "--newline", "--progress", "--progress-template", "download:RS_PROGRESS:%(progress._percent_str)s", "--print", "after_move:RS_FILE:%(filepath)s", "--retries", "3", "--fragment-retries", "3", "--socket-timeout", "20", "--ffmpeg-location", str(root / "tools"), "--merge-output-format", "mp4/mkv", "-P", str(output), "-o", f"Episode_{item.episode:03d}.%(ext)s"]
    if item.page_url:
        args += ["--referer", item.page_url]
    return args + ["--", item.media_url]


def validate_video(root: Path, path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < 128:
        return False
    try:
        run = subprocess.run([str(root / "tools" / "ffprobe.exe"), "-v", "error", "-show_entries", "stream=codec_type", "-of", "json", str(path)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=25, **process_options())
        return run.returncode == 0 and any(s.get("codec_type") == "video" for s in json.loads(run.stdout).get("streams", []))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


class DownloadQueue:
    def __init__(self, root: Path, report: Path, emit):
        self.root, self.report, self.emit = root, report, emit
        self.cancelled = threading.Event()
        self.thread = None
        self.process = None
        self.lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self, entries: list[Episode], destination: Path) -> None:
        if self.running:
            raise RuntimeError("التنزيل يعمل بالفعل.")
        if not entries or any(not item.media_url for item in entries):
            raise ValueError("اختر حلقات بها روابط فيديو صريحة.")
        self.cancelled.clear()
        self.thread = threading.Thread(target=self._run, args=(list(entries), destination), daemon=True, name="download-queue")
        self.thread.start()

    def _terminate(self, process) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run([str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"), "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=10, **process_options())
        else:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    def cancel(self) -> None:
        self.cancelled.set()
        with self.lock:
            process = self.process
        if process:
            try:
                self._terminate(process)
            except (OSError, subprocess.TimeoutExpired):
                process.kill()

    def _run(self, entries: list[Episode], destination: Path) -> None:
        results = []
        try:
            for item in entries:
                if self.cancelled.is_set():
                    break
                folder = destination / f"{safe_name(item.title)}_{item.series_id}"
                folder.mkdir(parents=True, exist_ok=True)
                self.emit(("episode", item.episode, "جارٍ التنزيل"))
                found = None
                error = ""
                try:
                    options = process_options() if os.name == "nt" else {"start_new_session": True}
                    with self.lock:
                        if self.cancelled.is_set():
                            break
                        process = subprocess.Popen(build_command(self.root, folder, item), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", **options)
                        self.process = process
                    for line in process.stdout:
                        if line.startswith("RS_PROGRESS:"):
                            percent = re.search(r"(\d+(?:\.\d+)?)%", line)
                            if percent:
                                self.emit(("progress", item.episode, min(100.0, float(percent[1]))))
                        elif line.startswith("RS_FILE:"):
                            candidate = Path(line.removeprefix("RS_FILE:").strip()).resolve()
                            if candidate.is_relative_to(folder.resolve()):
                                found = candidate
                        else:
                            message = redact(line.strip())
                            if message:
                                self.emit(("log", message))
                                error = message
                    code = process.wait()
                    if self.cancelled.is_set():
                        status = "متوقف — يمكن الاستئناف"
                    elif code == 0 and found and validate_video(self.root, found):
                        status = "مكتمل ومفحوص"
                    else:
                        status = "فشل — راجع السجل"
                        self.emit(("log", f"Episode {item.episode}: {error or 'No valid output video was produced.'}"))
                except OSError as e:
                    status = "فشل — راجع السجل"
                    self.emit(("log", redact(str(e))))
                finally:
                    with self.lock:
                        self.process = None
                results.append({"series_id": item.series_id, "episode": item.episode, "status": status, "file": str(found) if found else ""})
                write_json(self.report, {"results": results, "cancelled": self.cancelled.is_set()})
                self.emit(("episode", item.episode, status))
        except Exception as e:
            self.emit(("log", "Queue error: " + redact(str(e))))
        finally:
            self.emit(("done", results, self.cancelled.is_set()))
