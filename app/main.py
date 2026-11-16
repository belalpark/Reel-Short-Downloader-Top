from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

from app.paths import write_json
from app.runtime import check_runtime


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--setup", action="store_true")
    mode.add_argument("--check-runtime", action="store_true")
    mode.add_argument("--verify-install", action="store_true")
    mode.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--screenshot", type=Path)
    parser.add_argument('--library-screenshot', type=Path)
    args = parser.parse_args()
    if args.check_runtime or args.verify_install:
        checks = check_runtime()
        if args.report:
            write_json(args.report, {"checks": checks, "extension_setup": "application step 2"})
        if sys.stdout:
            print(json.dumps(checks, ensure_ascii=False))
        return 0 if all(c["ok"] for c in checks) else 12
    import tkinter as tk
    from tkinter import ttk
    from app.ui import DesktopApp
    if sys.platform == "win32":
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
    app = DesktopApp(root, smoke=args.smoke_test)
    if args.smoke_test:
        deadline = time.monotonic() + 30
        def capture():
            if app.checking and time.monotonic() < deadline:
                root.after(200, capture)
                return
            try:
                root.update_idletasks()
                assert len(app.step_buttons) == 3
                def descendants(widget):
                    for child in widget.winfo_children():
                        yield child
                        yield from descendants(child)
                assert not any(isinstance(w, (tk.Entry, ttk.Entry)) for w in descendants(root))
                for button in app.step_buttons:
                    assert button.winfo_viewable()
                    assert button.winfo_rooty() + button.winfo_height() <= root.winfo_rooty()+root.winfo_height()
                if sys.platform == "win32":
                    from pywinauto import Desktop
                    assert Desktop(backend="uia")
                if args.screenshot:
                    from PIL import ImageGrab
                    args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                    ImageGrab.grab(bbox=(root.winfo_rootx(), root.winfo_rooty(), root.winfo_rootx()+root.winfo_width(), root.winfo_rooty()+root.winfo_height())).save(args.screenshot)
                if args.report:
                    write_json(args.report, {"gui_created": True, "steps": [1, 2, 3], "manual_csv_required": False,
                        "pairing_code_required": False, "edge_automation_imported": sys.platform == "win32", "runtime_checks_completed": not app.checking,
                        "width": root.winfo_width(), "height": root.winfo_height()})
                if args.library_screenshot:
                    from PIL import ImageGrab
                    for n in range(1,61):
                        sid=f'{n:024x}'
                        page=f'https://www.reelshort.com/ar/episodes/episode-1-fixture-{sid}'
                        app.library_store.add_series([{'id':sid,'title':f'مسلسل اختبار {n:02d}','pageUrl':page}])
                        app.library_store.ingest({'seriesId':sid,'title':f'مسلسل اختبار {n:02d}','total':1,'rows':{'1':{'pageUrl':page}}})
                    app.open_library()
                    root.update()
                    view=app.library_window.window
                    assert len(app.library_window.table.get_children())==60
                    args.library_screenshot.parent.mkdir(parents=True,exist_ok=True)
                    ImageGrab.grab(bbox=(view.winfo_rootx(),view.winfo_rooty(),view.winfo_rootx()+view.winfo_width(),view.winfo_rooty()+view.winfo_height())).save(args.library_screenshot)
            finally:
                root.destroy()
        root.after(1500, capture)
    try:
        root.mainloop()
    finally:
        app.cleanup()
    return app.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
