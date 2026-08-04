"""Tests for the thin Qt entrypoint import behavior."""

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_app_qt_import_does_not_eagerly_import_application_controller():
    code = """
import sys
import app_qt
assert hasattr(app_qt, 'main')
assert 'services.application_controller' not in sys.modules
"""

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr


def test_local_backend_module_does_not_eagerly_import_faster_whisper():
    code = """
import sys
import transcriber.local_backend
assert 'faster_whisper' not in sys.modules
"""

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr


def test_source_entrypoint_uses_normal_system_exit():
    import app_qt

    with patch.object(app_qt.sys, "platform", "linux"):
        with pytest.raises(SystemExit) as exc_info:
            app_qt._exit_after_qt_cleanup(7)

    assert exc_info.value.code == 7


def test_frozen_windows_entrypoint_skips_sip_interpreter_teardown():
    import app_qt

    with patch.object(app_qt.sys, "platform", "win32"), patch.object(
        app_qt.sys, "frozen", True, create=True
    ), patch.object(app_qt.logging, "shutdown") as shutdown, patch.object(
        app_qt.os, "_exit"
    ) as hard_exit:
        app_qt._exit_after_qt_cleanup(3)

    shutdown.assert_called_once_with()
    hard_exit.assert_called_once_with(3)
