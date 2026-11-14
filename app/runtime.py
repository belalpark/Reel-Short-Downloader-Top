from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile

from .paths import bundle_root, data_root, downloads_root


def process_options() -> dict:
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def edge_path() -> Path | None:
    if sys.platform == "win32":
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe", 0, winreg.KEY_READ | view) as key:
                        path = Path(winreg.QueryValue(key, None).strip('"'))
                        if path.is_file():
                            return path
                except OSError:
                    pass
    for variable in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        base = os.environ.get(variable)
        if base:
            candidate = Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            if candidate.is_file():
                return candidate
    return None


def verify_integrity(root: Path) -> tuple[bool, str]:
    try:
        manifest = json.loads((root / "integrity.json").read_text(encoding="utf-8"))
        files = manifest["files"]
        required = {"extension/manifest.json", "tools/yt-dlp.exe", "tools/ffmpeg.exe", "tools/ffprobe.exe"}
        if not isinstance(files, dict) or not required.issubset(files):
            raise ValueError("Incomplete package manifest")
        for relative, expected in files.items():
            path = (root / relative).resolve()
            if not path.is_relative_to(root.resolve()) or not path.is_file():
                raise ValueError("Missing package file: " + relative)
            h = hashlib.sha256()
            with path.open("rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(chunk)
            if h.hexdigest() != expected:
                raise ValueError("Changed package file: " + relative)
        return True, f"{len(files)} files verified"
    except (OSError, KeyError, ValueError, TypeError) as e:
        return False, str(e)


def _writable(folder: Path) -> tuple[bool, str]:
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=folder) as f:
            f.write(b"readiness probe")
        return True, str(folder)
    except OSError as e:
        return False, str(e)


def check_runtime(root: Path | None = None, output: Path | None = None) -> list[dict]:
    root = root or bundle_root()
    checks = []
    windows_ok = sys.platform == "win32" and sys.getwindowsversion().major >= 10 and platform.machine().lower() in {"amd64", "x86_64"}
    checks.append({"name": "Windows 10/11 x64", "ok": windows_ok, "detail": platform.platform()})
    edge = edge_path()
    checks.append({"name": "Microsoft Edge", "ok": edge is not None, "detail": str(edge or "Install Microsoft Edge, then retry.")})
    ok, detail = verify_integrity(root)
    checks.append({"name": "Package integrity", "ok": ok, "detail": detail})
    # Never execute a bundled tool until its bytes match the package manifest.
    for tool, argument, expected in (("yt-dlp", "--version", None), ("ffmpeg", "-version", "ffmpeg version"), ("ffprobe", "-version", "ffprobe version")):
        valid, message = False, "Package integrity check failed"
        if ok:
            try:
                run = subprocess.run([str(root / "tools" / (tool + ".exe")), argument], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20, **process_options())
                message = (run.stdout or run.stderr).splitlines()[0][:200] if (run.stdout or run.stderr) else "No version output"
                valid = run.returncode == 0 and (expected in message if expected else bool(message))
            except (OSError, subprocess.TimeoutExpired) as e:
                message = str(e)
        checks.append({"name": tool, "ok": valid, "detail": message})
    for name, folder in (("User data", data_root()), ("Downloads", output or downloads_root())):
        valid, detail = _writable(folder)
        checks.append({"name": name, "ok": valid, "detail": detail})
    return checks


def open_edge(url: str) -> None:
    if url not in ("edge://extensions/", "https://www.reelshort.com/"):
        raise ValueError("Unsupported browser destination")
    edge = edge_path()
    if not edge:
        raise OSError("Microsoft Edge غير مثبت. ثبته ثم أعد الفحص.")
    subprocess.Popen([str(edge), "--new-window", url], **process_options())
