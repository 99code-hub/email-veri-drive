# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []

# ---------------------------------------------------------------------------
# Bundle everything Selenium needs (submodules + data files + binaries)
# ---------------------------------------------------------------------------
d, b, h = collect_all("selenium")
datas += d
binaries += b
hiddenimports += h

# ---------------------------------------------------------------------------
# Bundle pyperclip.
#
# pyperclip selects its clipboard backend dynamically at import time
# (it does its own `import` calls inside `determine_clipboard()`),
# which PyInstaller's static analysis cannot see. `collect_all` walks
# the package and pulls in every submodule it finds, so this is the
# safest way to guarantee the correct backend lands in the EXE.
# ---------------------------------------------------------------------------
d, b, h = collect_all("pyperclip")
datas += d
binaries += b
hiddenimports += h

# pyperclip's Windows backend calls into the Win32 clipboard API via
# `ctypes.windll.user32` / `ctypes.wintypes`. Those live in the stdlib
# and are normally auto-detected, but the import chain is dynamic and
# PyInstaller occasionally misses `ctypes.wintypes`. Listing them
# explicitly is cheap and prevents a `ModuleNotFoundError: ctypes.wintypes`
# when the EXE runs on a clean Windows box.
hiddenimports += [
    "ctypes",
    "ctypes.wintypes",
]

a = Analysis(
    ["testing.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="testing",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
