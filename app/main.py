from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from app.paths import write_json
from app.runtime import check_runtime


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--setup", action="store_true", help="Interactive readiness gate; returns 12 if incomplete")
    mode.add_argument("--check-runtime", action="store_true", help="Check bundled runtime; does not verify extension activation")
    mode.add_argument("--verify-install", action="store_true", help="Fail closed in unattended installation; interactive pairing is required")
    mode.add_argument("--smoke-test", action="store_true", help="Open and close the real GUI; never counts as setup completion")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--screenshot", type=Path, help="GUI smoke-test evidence")
    args = parser.parse_args()
    if args.check_runtime or args.verify_install:
        checks = check_runtime()
        # A silent first install cannot demonstrate a live, user-activated Edge extension.
        # There is deliberately no flag to skip or forge the activation gate.
        if args.verify_install:
            checks.append({"name": "Live Edge activation", "ok": False, "detail": "Run the interactive installer to pair Microsoft Edge."})
        if args.report:
            write_json(args.report, {"checks": checks})
        if sys.stdout:
            print(json.dumps(checks, ensure_ascii=False))
        return 0 if all(c["ok"] for c in checks) else 12

    import tkinter as tk
    from app.ui import DesktopApp
    if sys.platform == "win32":
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (OSError, AttributeError):
            pass
    root = tk.Tk()
    app = DesktopApp(root, setup=args.setup, smoke=args.smoke_test)
    if args.smoke_test:
        def capture_and_close():
            try:
                root.update_idletasks()
                if args.screenshot:
                    from PIL import ImageGrab
                    args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                    ImageGrab.grab(bbox=(root.winfo_rootx(), root.winfo_rooty(), root.winfo_rootx()+root.winfo_width(), root.winfo_rooty()+root.winfo_height())).save(args.screenshot)
                if args.report:
                    write_json(args.report, {"gui_created": True, "width": root.winfo_width(), "height": root.winfo_height(), "setup_verified": False})
            finally:
                root.destroy()
        root.after(1800, capture_and_close)
    try:
        root.mainloop()
    finally:
        app.cleanup()
    return app.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
