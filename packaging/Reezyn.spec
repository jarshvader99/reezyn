# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller recipe for the signed Reezyn.app bundle."""

import re
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, copy_metadata

ROOT = Path(SPECPATH).resolve().parent
VERSION = re.search(
    r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M
).group(1)

datas = []
binaries = []
hiddenimports = []

# Everything below ships native libraries or data next to its module, which
# PyInstaller cannot infer from imports alone.
for package in (
    "kokoro_onnx",
    "espeakng_loader",
    "phonemizer",
    "onnxruntime",
    "sounddevice",
    # Hands-free input: the mic, Apple's on-device recognizer, and typing.
    "AVFoundation",
    "Speech",
    "Quartz",
    "ApplicationServices",
):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

# kokoro_onnx and friends read their own distribution metadata at import.
for distribution in (
    "kokoro-onnx",
    "espeakng-loader",
    "phonemizer",
    "onnxruntime",
    "sounddevice",
    "reezyn",
):
    try:
        datas += copy_metadata(distribution)
    except Exception:
        pass

hiddenimports += ["rumps.notifications", "objc", "CoreFoundation", "AppKit"]

# The phone page is plain files served by the in-process HTTP server.
datas += [
    (str(ROOT / "reezyn" / "remote" / "web"), "reezyn/remote/web"),
]

icon = ROOT / "build" / "Reezyn.icns"

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "setuptools", "pip", "tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Reezyn",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Reezyn",
)

app = BUNDLE(
    coll,
    name="Reezyn.app",
    icon=str(icon) if icon.exists() else None,
    bundle_identifier="io.reezyn.reezyn",
    info_plist={
        "CFBundleDisplayName": "Reezyn",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "LSMinimumSystemVersion": "13.0",
        "LSUIElement": True,
        "LSApplicationCategoryType": "public.app-category.utilities",
        "NSHighResolutionCapable": True,
        "NSMicrophoneUsageDescription": (
            "Reezyn listens for its wake word so you can dictate prompts and "
            "answer an agent without touching the keyboard. Audio is "
            "recognized on this Mac and never recorded to disk."
        ),
        "NSSpeechRecognitionUsageDescription": (
            "Reezyn turns the words you speak into the terminal's input using "
            "on-device speech recognition."
        ),
        "NSHumanReadableCopyright": "Copyright © 2026 Joshua Harrison. Apache-2.0.",
        "NSPrincipalClass": "NSApplication",
    },
)
