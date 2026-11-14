# Third-party components

The Windows preview bundles a Python interpreter through PyInstaller, Tcl/Tk, Pillow, yt-dlp, and a shared LGPL FFmpeg build. Their licenses remain in force independently of this project's original code.

| Component | Source and license information |
| --- | --- |
| Python | https://www.python.org/psf/license/ — PSF license and included notices |
| Tcl/Tk | https://www.tcl-lang.org/software/tcltk/license.html — Tcl/Tk license |
| PyInstaller | https://pyinstaller.org/en/stable/license.html — GPL with distribution exception for bundled applications |
| Pillow | https://github.com/python-pillow/Pillow/blob/main/LICENSE |
| pywinauto | https://github.com/pywinauto/pywinauto — BSD license; visible Windows UI automation |
| comtypes | https://github.com/enthought/comtypes — MIT license |
| pywin32 | https://github.com/mhammond/pywin32 — included Python/PSF notices |
| yt-dlp | https://github.com/yt-dlp/yt-dlp — Unlicense; bundled executable has additional third-party licenses |
| FFmpeg | https://ffmpeg.org/legal.html — chosen shared LGPL build, not the GPL build |
| FFmpeg build scripts | https://github.com/FFmpeg-Builds/FFmpeg-Builds |

Exact executable and archive versions, immutable release URLs and SHA256 digests are recorded in `vendor-lock.json`. The build retains FFmpeg package documentation/licenses and downloads yt-dlp's LICENSE and THIRD_PARTY_LICENSES.txt from its pinned source tag into the delivered `licenses` directory. Python/Tcl/Tk/Pillow package license texts are also copied into that directory during packaging.

FFmpeg source corresponding to the selected build: https://github.com/FFmpeg/FFmpeg/tree/f1acb0a266. Build recipe and configuration: the repository above, and the included `ffmpeg -version` output. DLLs are distributed as separate replaceable files. The application runs FFmpeg/yt-dlp as separate processes, and does not require a modified version of either tool.

Before distributing a public production release, verify complete license/source availability for every binary in that exact build, retain the notices, and complete `docs/ACCEPTANCE.md`. There is no production-release approval recorded in this repository.
