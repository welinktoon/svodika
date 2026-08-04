"""Product identity that must be applied before Qt creates any windows."""

from __future__ import annotations

import ctypes
import sys

APP_NAME = "Svodika"
WINDOWS_APP_USER_MODEL_ID = "Svodika.Desktop.1"


def set_windows_app_identity() -> bool:
    """Set the Windows taskbar identity as early as possible."""
    if sys.platform != "win32":
        return False
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            WINDOWS_APP_USER_MODEL_ID
        )
    except Exception:
        return False
    return True
