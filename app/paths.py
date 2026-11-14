from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile

APP_NAME = "ReelShortDownloaderTop"


def bundle_root() -> Path:
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]


def data_root() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share"))
    return base / APP_NAME


def downloads_root() -> Path:
    # Use the Windows Known Folder API, including redirected/OneDrive folders.
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        import uuid

        folder_id = (ctypes.c_byte * 16).from_buffer_copy(uuid.UUID("374de290-123f-4565-9164-39c4925e467b").bytes_le)
        out = ctypes.c_wchar_p()
        shell = ctypes.WinDLL("shell32")
        shell.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
        shell.SHGetKnownFolderPath.restype = ctypes.c_long
        if shell.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None, ctypes.byref(out)) == 0:
            try:
                return Path(out.value) / "Reel Short Downloader Top"
            finally:
                ctypes.windll.ole32.CoTaskMemFree(ctypes.cast(out, ctypes.c_void_p))
    return Path.home() / "Downloads" / "Reel Short Downloader Top"


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # A unique temporary file also makes concurrent bridge/settings writes safe.
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
        if os.name != "nt":
            path.chmod(0o600)
    finally:
        Path(name).unlink(missing_ok=True)


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
