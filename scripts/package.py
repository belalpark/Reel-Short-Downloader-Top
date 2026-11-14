"""Stage a self-contained Windows package and hash every immutable file."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def copy_runtime_licenses(target: Path):
    folder = target / "licenses" / "runtime"
    folder.mkdir(parents=True, exist_ok=True)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        raise RuntimeError("Python license is missing")
    shutil.copy2(python_license, folder / "Python-LICENSE.txt")
    for package in ("Pillow", "PyInstaller"):
        dist = importlib.metadata.distribution(package)
        copied = 0
        for relative in dist.files or []:
            if ".dist-info" not in str(relative) or not any(part.lower().startswith(("license", "copying")) for part in relative.parts):
                continue
            source = Path(dist.locate_file(relative))
            if source.is_file():
                shutil.copy2(source, folder / (package + "-" + source.name))
                copied += 1
        if not copied:
            raise RuntimeError(package + " license is missing")
    tcl_root = Path(sys.base_prefix) / "tcl"
    for name in ("tcl8.6", "tk8.6"):
        license_path = tcl_root / name / "license.terms"
        if not license_path.is_file():
            raise RuntimeError(name + " license is missing")
        shutil.copy2(license_path, folder / (name + "-license.terms"))


def digest(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def main():
    if sys.platform != "win32":
        raise SystemExit("Build this package on Windows; cross-compilation is not supported.")
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed", "--name", "ReelShortDownloaderTop", "--hidden-import", "PIL.ImageGrab", "--paths", str(ROOT), str(ROOT / "run.py")], cwd=ROOT, check=True)
    target = ROOT / "dist" / "ReelShortDownloaderTop"
    for source, relative in ((ROOT / "extension", "extension"), (ROOT / "vendor" / "tools", "tools"), (ROOT / "vendor" / "licenses", "licenses")):
        shutil.copytree(source, target / relative, dirs_exist_ok=True)
    for name in ("README.md", "THIRD_PARTY_NOTICES.md", "vendor-lock.json"):
        shutil.copy2(ROOT / name, target / name)
    copy_runtime_licenses(target)
    files = {p.relative_to(target).as_posix(): digest(p) for p in sorted(target.rglob("*")) if p.is_file() and p.name != "integrity.json"}
    (target / "integrity.json").write_text(json.dumps({"algorithm": "sha256", "files": files}, indent=2), encoding="utf-8")
    print(f"Staged {len(files)} files in {target}")


if __name__ == "__main__":
    main()
