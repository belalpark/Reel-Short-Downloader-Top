"""Install through Edge's normal, visible extension controls. Never change browser policies."""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import time

from .runtime import edge_path, process_options


def _normal(text):
    return re.sub(r"[\u064b-\u065f\u0640]", "", str(text)).strip().casefold()


def _named(window, names, ids=()):
    wanted = {_normal(n) for n in names}
    controls = window.descendants()
    for item in controls:
        if item.element_info.automation_id in ids:
            return item
    for item in controls:
        if _normal(item.element_info.name) in wanted and item.element_info.control_type in ("Button", "CheckBox", "RadioButton"):
            return item
    for item in controls:
        if _normal(item.element_info.name) in wanted:
            return item
    return None


def _activate(item):
    try:
        item.invoke()
    except Exception:
        item.click_input()


def install_unpacked(folder: Path, emit=lambda _: None, profile: Path | None = None):
    if os.name != "nt":
        raise OSError("إضافة الملحق تلقائيًا متاحة على Windows.")
    edge = edge_path()
    if not edge:
        raise OSError("ثبّت Microsoft Edge ثم اضغط إضافة الملحق.")
    from pywinauto import Desktop
    desktop = Desktop(backend="uia")
    before = {w.handle for w in desktop.windows(class_name="Chrome_WidgetWin_1")}
    args = [str(edge), "--force-renderer-accessibility", "--no-first-run", "--new-window"]
    if profile:
        args.append("--user-data-dir=" + str(profile))
    args.append("edge://extensions/")
    subprocess.Popen(args, **process_options())
    emit("جارٍ فتح صفحة الملحقات في Edge…")
    target = None
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        candidates = desktop.windows(class_name="Chrome_WidgetWin_1")
        target = next((w for w in candidates if w.handle not in before), None)
        if target:
            break
        time.sleep(0.4)
    if target is None:
        raise OSError("لم تظهر نافذة الملحقات؛ أكمل نافذة Edge الظاهرة ثم أعد المحاولة.")
    target.set_focus()
    # Edge can replace an internal command-line URL with New tab. Navigate the
    # visible address bar as the user would; keep the existing profile/session.
    address = _named(target, ("Address and search bar", "شريط العناوين والبحث", "شريط العنوان والبحث"), ("view_1017",))
    if address is not None:
        address.set_edit_text("edge://extensions/")
        address.type_keys("{ENTER}")
    else:
        target.type_keys("^l")
        target.type_keys("edge://extensions/{ENTER}", with_spaces=True)
    developer = None
    menu = None
    opened_menu = False
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        developer = _named(target, ("Developer mode", "وضع المطور", "وضع المطوّر"), ("dev-switch", "devMode"))
        if developer:
            break
        if not opened_menu:
            menu = _named(target, ("Extensions menu", "قائمة الملحقات", "قائمة الامتدادات"))
            if menu is not None:
                _activate(menu)
                opened_menu = True
        time.sleep(0.5)
    if developer is None:
        raise OSError("أكمل إعداد Edge الظاهر، ثم اضغط إضافة الملحق مجددًا.")
    emit("جارٍ إضافة الملحق…")
    try:
        checked = developer.get_toggle_state()
    except (AttributeError, RuntimeError):
        checked = developer.iface_toggle.CurrentToggleState
    if checked == 0:
        try:
            developer.toggle()
        except (AttributeError, RuntimeError):
            _activate(developer)
    if opened_menu and menu is not None:
        try:
            # Enabling developer mode re-renders the menu; the old UIA element
            # can silently ignore Invoke. Reacquire and click the visible control.
            menu = _named(target, ("Extensions menu", "قائمة الملحقات", "قائمة الامتدادات"))
            if menu is not None:
                menu.click_input()
                time.sleep(0.5)
        except Exception:
            pass  # Drawer may already have closed during the layout update.
    load = None
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        load = _named(target, ("Load unpacked", "تحميل الملحق غير المحزم", "تحميل ملحق غير محزم", "تحميل غير محزم", "تحميل الملفات غير المحزمة"), ("loadUnpacked",))
        if load:
            break
        time.sleep(0.4)
    if load is None:
        raise OSError("تعذر إتمام الإضافة تلقائيًا؛ استخدم فتح صفحة الإضافات أدناه.")
    _activate(load)
    dialog = None
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        dialog = next((w for w in desktop.windows(class_name="#32770") if w.process_id() == target.process_id()), None)
        if dialog:
            break
        time.sleep(0.3)
    if dialog is None:
        raise OSError("لم يظهر اختيار مجلد الملحق؛ أكمل الخطوة الظاهرة في Edge.")
    dialog.set_focus()
    edits = dialog.descendants(control_type="Edit")
    entry = next((e for e in edits if e.element_info.automation_id == "1148"), None)
    if entry:
        entry.set_edit_text(str(folder))
    else:
        dialog.type_keys("%d")
        time.sleep(0.3)
        entry = next((e for e in dialog.descendants(control_type="Edit") if e.has_keyboard_focus()), None)
        if entry is None:
            raise OSError("اختر مجلد الملحق من النافذة المفتوحة؛ المسار معروض في التطبيق.")
        entry.set_edit_text(str(folder))
        entry.type_keys("{ENTER}")
        time.sleep(0.4)
    select = _named(dialog, ("Select Folder", "Select folder", "Select", "تحديد مجلد", "تحديد المجلد", "اختيار مجلد", "اختيار المجلد"), ("1",))
    if select is None:
        raise OSError("اضغط تحديد المجلد في نافذة Edge المفتوحة لإتمام الإضافة.")
    _activate(select)
    emit("تم إرسال الملحق إلى Edge؛ جارٍ التحقق من تشغيله…")
    return target.process_id()
