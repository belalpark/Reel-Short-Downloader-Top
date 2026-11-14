"""Exercise the production one-click installer against real Edge in an isolated CI profile."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import time

from PIL import ImageGrab

from app.automatic import provision_extension
from app.bridge import Bridge
from app.edge_install import install_unpacked
from app.paths import write_json


def main():
    if os.name != 'nt':
        raise SystemExit('Run this acceptance check on Windows.')
    root = Path(__file__).resolve().parents[1]
    evidence = root / 'test-results'
    evidence.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=os.environ['RUNNER_TEMP'], prefix='reel-edge-') as temporary:
        work = Path(temporary)
        extension = work / 'Extension'
        provision_extension(root / 'dist' / 'ReelShortDownloaderTop' / 'extension', extension, work / 'connection.json')
        bridge = Bridge(work / 'connection.json', inbox=work / 'Inbox')
        browser_pid = None
        bridge.start()
        try:
            browser_pid = install_unpacked(extension, lambda text: print(text, flush=True), profile=work / 'EdgeProfile')
            deadline = time.monotonic() + 65
            while time.monotonic() < deadline and not bridge.status()['connected']:
                time.sleep(0.5)
            status = bridge.status()
            assert status['connected'], 'No verified Edge heartbeat after normal Load unpacked installation'
            ImageGrab.grab().save(evidence / 'edge-installed.png')
            write_json(evidence / 'edge-setup.json', {'normal_edge_ui': True, 'single_click_install': True,
                'pairing_code_required': False, 'edge_heartbeat_verified': True,
                'isolated_test_profile': True, 'real_account_tested': False,
                'extension_version': status['extension_version']})
            print('Real Edge installation and automatic authorization passed.', flush=True)
        except Exception:
            ImageGrab.grab().save(evidence / 'edge-setup-failure.png')
            from pywinauto import Desktop
            controls = []
            for window in Desktop(backend='uia').windows():
                try:
                    controls.append({'window': window.window_text(), 'controls': [
                        {'name': item.element_info.name, 'id': item.element_info.automation_id,
                         'type': item.element_info.control_type} for item in window.descendants()]})
                except Exception:
                    pass
            write_json(evidence / 'edge-setup-failure.json', controls)
            raise
        finally:
            bridge.close()
            if browser_pid:
                subprocess.run(['taskkill', '/PID', str(browser_pid), '/T', '/F'], capture_output=True, check=False)
            else:
                # Only close the browser created with this exact disposable profile.
                command = "$p='" + str(work / 'EdgeProfile').replace("'", "''") + "'; Get-CimInstance Win32_Process -Filter \"name = 'msedge.exe'\" | Where-Object { $_.CommandLine -and $_.CommandLine.Contains($p) } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
                subprocess.run(['powershell', '-NoProfile', '-Command', command], capture_output=True, check=False)
            time.sleep(1)


if __name__ == '__main__':
    main()
