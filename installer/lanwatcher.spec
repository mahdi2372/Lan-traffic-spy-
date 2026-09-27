# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for LAN Watcher (Windows one-file console-less exe).

Build:  pyinstaller installer/lanwatcher.spec
Output: dist/LANWatcher.exe
"""

import sys
from pathlib import Path

root = Path(SPECPATH).parent  # noqa: F821 - SPECPATH injected by PyInstaller

_datas = [
    (str(root / "lanwatcher" / "web"), "lanwatcher/web"),
    (str(root / "LICENSE"), "."),
    (str(root / "docs"), "docs"),
]
if (root / "lanwatcher" / "data").exists():
    _datas.append((str(root / "lanwatcher" / "data"), "lanwatcher/data"))

a = Analysis(
    [str(root / "launcher.py")],
    pathex=[str(root)],
    binaries=[],
    datas=_datas,
    hiddenimports=[
        "webview",
        "psutil",
        "cryptography.fernet",
        "lanwatcher.desktop.window",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "pydoc_data"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="LANWatcher",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # windowed app; logs go to the app data dir
    disable_windowed_traceback=False,
    icon=str(root / "installer" / "lanwatcher.ico") if (root / "installer" / "lanwatcher.ico").exists() else None,
)
