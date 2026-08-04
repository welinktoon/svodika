"""PyInstaller definition for the Windows Meeting Recorder distribution."""

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)


PROJECT_ROOT = Path(SPECPATH).resolve().parent
VERSION = os.environ.get("MEETING_RECORDER_VERSION", "1.0.0")
VERSION_PARTS = tuple(int(part) for part in VERSION.split("."))
FILE_VERSION = (*VERSION_PARTS, 0)

datas = [
    (str(PROJECT_ROOT / "ui_qt" / "assets"), "ui_qt/assets"),
    (str(PROJECT_ROOT / "ui_qt" / "styles"), "ui_qt/styles"),
]
binaries = []
hiddenimports = []

# These packages discover native components or resources dynamically.
for package_name in (
    "av",
    "ctranslate2",
    "faster_whisper",
    "huggingface_hub",
    "mss",
    "nvidia.cublas",
    "nvidia.cuda_nvrtc",
    "nvidia.cuda_runtime",
    "nvidia.cudnn",
    "qtawesome",
    "soundcard",
    "tokenizers",
):
    package_datas, package_binaries, package_hiddenimports = collect_all(
        package_name
    )
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

datas += collect_data_files("certifi")

version_info = VSVersionInfo(
    ffi=FixedFileInfo(
        filevers=FILE_VERSION,
        prodvers=FILE_VERSION,
        mask=0x3F,
        flags=0x0,
        OS=0x40004,
        fileType=0x1,
        subtype=0x0,
        date=(0, 0),
    ),
    kids=[
        StringFileInfo(
            [
                StringTable(
                    "041904B0",
                    [
                        StringStruct("CompanyName", "welinkton"),
                        StringStruct(
                            "FileDescription",
                            "Запись и локальная расшифровка встреч",
                        ),
                        StringStruct("FileVersion", VERSION),
                        StringStruct("InternalName", "MeetingRecorder"),
                        StringStruct(
                            "LegalCopyright",
                            "Copyright (c) welinkton",
                        ),
                        StringStruct(
                            "OriginalFilename",
                            "MeetingRecorder.exe",
                        ),
                        StringStruct("ProductName", "Svodika"),
                        StringStruct("ProductVersion", VERSION),
                    ],
                )
            ]
        ),
        VarFileInfo([VarStruct("Translation", [1049, 1200])]),
    ],
)

a = Analysis(
    [str(PROJECT_ROOT / "app_qt.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "IPython", "notebook"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MeetingRecorder",
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
    icon=str(PROJECT_ROOT / "ui_qt" / "assets" / "meeting-recorder-logo.ico"),
    version=version_info,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="MeetingRecorder",
)
