"""Windows-only acceptance: requirements, normal Finish, app launch and uninstall."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import time

from PIL import ImageGrab

from app.paths import write_json

USER32 = ctypes.WinDLL("user32", use_last_error=True)
CALLBACK = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
for name in ("IsWindowVisible", "IsWindowEnabled"):
    function = getattr(USER32, name)
    function.argtypes = [wintypes.HWND]
    function.restype = wintypes.BOOL
USER32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
USER32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
USER32.EnumWindows.argtypes = [CALLBACK, wintypes.LPARAM]
USER32.EnumChildWindows.argtypes = [wintypes.HWND, CALLBACK, wintypes.LPARAM]
USER32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
USER32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
USER32.SendMessageW.restype = ctypes.c_ssize_t
USER32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM, wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
USER32.SendMessageTimeoutW.restype = ctypes.c_ssize_t
USER32.SetForegroundWindow.argtypes = [wintypes.HWND]
USER32.SetActiveWindow.argtypes = [wintypes.HWND]


def windows(parent=None):
    result = []
    def visit(handle, _):
        if USER32.IsWindowVisible(handle):
            title, cls = ctypes.create_unicode_buffer(2048), ctypes.create_unicode_buffer(256)
            USER32.GetWindowTextW(handle, title, len(title))
            USER32.GetClassNameW(handle, cls, len(cls))
            if parent and "Button" in cls.value:
                message_result = ctypes.c_size_t()
                # Custom VCL captions are not always available through cross-process GetWindowText.
                USER32.SendMessageTimeoutW(handle, 0x000D, len(title), ctypes.cast(title, ctypes.c_void_p).value, 0x0002, 200, ctypes.byref(message_result))
            result.append((handle, title.value, cls.value))
        return True
    callback = CALLBACK(visit)
    if parent:
        USER32.EnumChildWindows(parent, callback, 0)
    else:
        USER32.EnumWindows(callback, 0)
    return result


def wait_for(test, label, timeout=80):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = test()
        if value:
            return value
        time.sleep(0.2)
    details = [(title, cls, windows(handle)) for handle, title, cls in windows() if "Reel Short Downloader Top" in title]
    raise AssertionError("Timeout: " + label + "; application controls=" + repr(details))


def setup_window():
    return next((w for w in windows() if "Reel Short Downloader Top" in w[1] and "Setup" in w[1] and "Tk" not in w[2]), None)


def activation_window():
    return next((w for w in windows() if "Reel Short Downloader Top" in w[1] and "Tk" in w[2]), None)


def buttons(handle):
    return [(h, text.replace("&", "").strip()) for h, text, cls in windows(handle) if "Button" in cls and USER32.IsWindowEnabled(h)]


def click(handle):
    USER32.SendMessageW(handle, 0x00F5, 0, 0)  # BM_CLICK on the real native button.


def check_incomplete_installer(root, evidence):
    # Build a deliberately incomplete copy; do not alter the healthy installer.
    target = Path(os.environ["RUNNER_TEMP"]) / "ReelShortMissingToolTest"
    build = Path(os.environ["RUNNER_TEMP"]) / "ReelShortNegativeBuild"
    build.mkdir(exist_ok=True)
    tool = root / "dist/ReelShortDownloaderTop/tools/ffmpeg.exe"
    backup = build / "ffmpeg.exe"
    tool.replace(backup)
    try:
        compiler = Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe")
        compiled = subprocess.run([str(compiler), "/O" + str(build), "/FReelShortNegative", str(root / "installer/ReelShortDownloaderTop.iss")], cwd=root, capture_output=True, text=True, errors="replace", timeout=180)
        (evidence / "installer-negative-build.log").write_text(compiled.stdout + compiled.stderr, encoding="utf-8")
        assert compiled.returncode == 0, "Negative installer fixture failed to compile"
    finally:
        backup.replace(tool)
    process = subprocess.Popen([str(build / "ReelShortNegative.exe"), "/SP-", "/NORESTART", f"/DIR={target}", f"/LOG={evidence / 'installer-incomplete.log'}"])
    try:
        last_click = 0.0
        def requirements():
            nonlocal last_click
            wizard = setup_window()
            if not wizard:
                return None
            controls = buttons(wizard[0])
            assert not any(text == "Finish" for _, text in controls), "Finish appeared despite missing FFmpeg"
            if any(text == "Retry" for _, text in controls):
                return wizard
            if time.monotonic() - last_click > 0.7:
                for handle, text in controls:
                    if text in ("Next", "Next >", "Install"):
                        USER32.PostMessageW(handle, 0x00F5, 0, 0)
                        last_click = time.monotonic()
                        break
            return None
        wizard = wait_for(requirements, "missing tool blocks Finish", timeout=120)
        ImageGrab.grab().save(evidence / "installer-incomplete.png")
        cancel = next(h for h, text in buttons(wizard[0]) if text == "Cancel")
        click(cancel)
        assert process.wait(timeout=30) == 12, "Incomplete setup did not report failure"
        removed = subprocess.run([str(target / "unins000.exe"), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], timeout=60)
        assert removed.returncode == 0 and not (target / "ReelShortDownloaderTop.exe").exists()
    finally:
        if process.poll() is None:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)


def main():
    root = Path(__file__).resolve().parents[1]
    evidence = root / "test-results"
    evidence.mkdir(exist_ok=True)
    installer = next((root / "release").glob("*Setup*.exe"))
    target = Path(os.environ["RUNNER_TEMP"]) / "ReelShortInteractiveTest"
    process = subprocess.Popen([str(installer), "/SP-", "/NORESTART", f"/DIR={target}", f"/LOG={evidence / 'installer-interactive.log'}"])
    try:
        wait_for(setup_window, "installer welcome")
        last_click = 0.0
        def advance_to_finish():
            nonlocal last_click
            wizard = setup_window()
            if not wizard:
                return None
            controls = buttons(wizard[0])
            finish = next((h for h, text in controls if text == "Finish"), None)
            if finish:
                return finish
            if time.monotonic() - last_click > 0.7:
                for handle, text in controls:
                    if text in ("Next", "Next >", "Install"):
                        USER32.PostMessageW(handle, 0x00F5, 0, 0)
                        last_click = time.monotonic()
                        break
            return None
        finish = wait_for(advance_to_finish, "healthy interactive installer Finish", timeout=120)
        assert not activation_window(), "Installer must not demand a separate pairing wizard"
        report_path = Path(os.environ["LOCALAPPDATA"]) / "ReelShortDownloaderTop" / "installer-runtime.json"
        import json
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert all(item["ok"] for item in report["checks"]), "Finish appeared with an unhealthy requirement"
        ImageGrab.grab().save(evidence / "installer-finish.png")
        click(finish)
        assert process.wait(timeout=30) == 0
        application = wait_for(activation_window, "normal application launched after Finish")
        ImageGrab.grab().save(evidence / "installed-application.png")
        USER32.PostMessageW(application[0], 0x0010, 0, 0)
        wait_for(lambda: not activation_window(), "application exit")
        time.sleep(2)
        uninstaller = target / "unins000.exe"
        remove = subprocess.run([str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], timeout=60)
        assert remove.returncode == 0 and not (target / "ReelShortDownloaderTop.exe").exists()
        check_incomplete_installer(root, evidence)
        write_json(evidence / "installer-interactive.json", {"real_installer": True,
            "finish_after_core_requirements": True, "pairing_wizard_required": False,
            "application_launched": True, "uninstall_passed": True,
            "missing_tool_blocks_finish": True, "incomplete_exit_code": 12})
    finally:
        if process.poll() is None:
            ImageGrab.grab().save(evidence / "installer-timeout.png")
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)


if __name__ == "__main__":
    main()
