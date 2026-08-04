"""Thin compatibility entrypoint for the Qt application"""

import os
import logging
import platform
import site
import subprocess
import sys
import warnings
from pathlib import Path

from app_identity import set_windows_app_identity

warnings.filterwarnings("ignore", message="pkg_resources is deprecated")

# PyInstaller's multiprocessing dispatcher must run before Qt, models, or the
# single-instance guard are initialized in spawned capture workers.
if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()


def _register_cuda_dll_directories() -> None:
    """Make NVIDIA pip-packaged CUDA DLLs loadable by CTranslate2 on Windows.

    CTranslate2 (faster-whisper's engine) loads cublas64_12.dll / cuDNN lazily
    at inference time via the Win32 ``LoadLibrary`` call, which uses the standard
    search order and therefore consults ``PATH``. The nvidia-*-cu12 wheels drop
    those DLLs under ``site-packages/nvidia/<lib>/bin``, which is on neither the
    default search path nor ``PATH``.

    We register each directory two ways because the two consumers use different
    search mechanisms:

    * ``os.add_dll_directory`` — satisfies Python's own loader / ``ctypes``.
    * prepending to ``os.environ["PATH"]`` — satisfies CTranslate2's C++
      ``LoadLibrary`` call, which ignores the ``add_dll_directory`` registry.

    Without the PATH prepend, transcription fails with
    "Library cublas64_12.dll is not found or cannot be loaded" even when the
    wheel is installed. Install the DLLs with ``pip install -r requirements-gpu.txt``.
    """
    if sys.platform != "win32":
        return

    search_roots = []
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        search_roots.append(Path(frozen_root))
    for site_dir in site.getsitepackages():
        search_roots.append(Path(site_dir))
    user_site = site.getusersitepackages()
    if user_site:
        search_roots.append(Path(user_site))

    nvidia_subdirs = ("cublas", "cudnn", "cuda_runtime", "cuda_nvrtc")
    bin_dirs = []
    for root in search_roots:
        nvidia_root = root / "nvidia"
        if not nvidia_root.is_dir():
            continue
        for subdir in nvidia_subdirs:
            bin_dir = nvidia_root / subdir / "bin"
            if bin_dir.is_dir():
                os.add_dll_directory(str(bin_dir))
                bin_dirs.append(str(bin_dir))

    if bin_dirs:
        existing = os.environ.get("PATH", "")
        os.environ["PATH"] = os.pathsep.join(bin_dirs) + os.pathsep + existing


def _patch_subprocess_for_windows() -> None:
    """Patch subprocess.Popen to hide console windows on Windows."""
    if platform.system() != "Windows":
        return

    original_popen = subprocess.Popen

    class _NoConsolePopen(original_popen):
        """Popen wrapper that adds CREATE_NO_WINDOW on Windows."""

        def __init__(self, *args, **kwargs):
            if "creationflags" not in kwargs:
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            elif not (kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW):
                kwargs["creationflags"] |= subprocess.CREATE_NO_WINDOW
            super().__init__(*args, **kwargs)

    subprocess.Popen = _NoConsolePopen


set_windows_app_identity()
_register_cuda_dll_directories()
_patch_subprocess_for_windows()

from ui_qt.bootstrap import main

__all__ = ["main"]


def _exit_after_qt_cleanup(exit_code: int) -> None:
    """Exit a frozen Windows build without re-running Qt/SIP destructors.

    ``bootstrap.main`` has already stopped workers, closed the database, hidden
    the tray icon, destroyed the windows, and released its native handles when
    it returns.  Letting the frozen interpreter then finalize the same PyQt
    wrappers a second time can raise a native access violation in ``sip.pyd``.
    Source runs keep normal ``SystemExit`` semantics for debuggability.
    """
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        logging.shutdown()
        os._exit(int(exit_code))
        return
    raise SystemExit(exit_code)


if __name__ == "__main__":
    _exit_after_qt_cleanup(main())
