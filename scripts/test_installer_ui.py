"""Windows-only acceptance: close setup unpaired, retry, refuse Finish, uninstall."""
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
                result = ctypes.c_size_t()
                # Custom VCL captions are not always available through cross-process GetWindowText.
                USER32.SendMessageTimeoutW(handle, 0x000D, len(title), ctypes.cast(title, ctypes.c_void_p).value, 0x0002, 200, ctypes.byref(result))
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


def main():
    root = Path(__file__).resolve().parents[1]
    evidence = root / "test-results"
    evidence.mkdir(exist_ok=True)
    installer = next((root / "release").glob("*Setup*.exe"))
    target = Path(os.environ["RUNNER_TEMP"]) / "ReelShortInteractiveTest"
    log = evidence / "installer-interactive.log"
    process = subprocess.Popen([str(installer), "/SP-", "/NORESTART", f"/DIR={target}", f"/LOG={log}"])
    try:
        wait_for(setup_window, "installer welcome")
        print('Installer controls:', windows(setup_window()[0]), flush=True)
        last_click = 0.0
        def advance_to_activation():
            nonlocal last_click
            activation = activation_window()
            if activation:
                return activation
            installer_window = setup_window()
            if installer_window and time.monotonic() - last_click > 0.7:
                for handle, text in buttons(installer_window[0]):
                    if text in ("Next", "Next >", "Install"):
                        USER32.SetForegroundWindow(installer_window[0])
                        USER32.SetActiveWindow(installer_window[0])
                        # Post rather than Send: the Next handler can synchronously wait for the app.
                        USER32.PostMessageW(handle, 0x00F5, 0, 0)
                        last_click = time.monotonic()
                        break
            return None
        activation = wait_for(advance_to_activation, "interactive application activation", timeout=100)
        print('Actual setup application opened', flush=True)
        USER32.PostMessageW(activation[0], 0x0010, 0, 0)  # WM_CLOSE; production setup returns 12.
        def retry_button():
            wizard = setup_window()
            if not wizard or activation_window():
                return None
            controls = buttons(wizard[0])
            assert not any("Finish" in text for _, text, cls in windows(wizard[0]) if "Button" in cls), "Finish was visible before activation"
            return next((handle for handle, text in controls if "Retry verification" in text), None)
        retry = wait_for(retry_button, "verification page after cancellation")
        ImageGrab.grab().save(evidence / "installer-incomplete.png")
        USER32.PostMessageW(retry, 0x00F5, 0, 0)
        activation = wait_for(activation_window, "retry reopens actual activation application")
        USER32.PostMessageW(activation[0], 0x0010, 0, 0)
        wait_for(retry_button, "second refusal")
        wizard = setup_window()
        cancel = next(h for h, text in buttons(wizard[0]) if text == "Cancel")
        click(cancel)
        exit_code = process.wait(timeout=30)
        assert exit_code != 0, "Incomplete interactive setup returned success"
        app = target / "ReelShortDownloaderTop.exe"
        assert app.is_file(), "File-copy phase was not exercised"
        uninstaller = target / "unins000.exe"
        assert uninstaller.is_file(), "Incomplete install has no removal path"
        remove = subprocess.run([str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], timeout=60)
        assert remove.returncode == 0 and not app.exists(), "Uninstall did not remove application files"
        write_json(evidence / "installer-interactive.json", {"real_installer": True, "actual_setup_application": True, "unpaired_close_exit_code": exit_code, "finish_blocked": True, "retry_reopened_application": True, "uninstall_passed": True, "successful_edge_activation_tested": False})
    finally:
        if process.poll() is None:
            ImageGrab.grab().save(evidence / "installer-timeout.png")
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)


if __name__ == "__main__":
    main()
