"""Stable per-user paths used by installed and development builds."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


APP_DATA_ENV = "MEETING_RECORDER_DATA_DIR"
APP_DATA_FILES = (
    "openwhisper_settings.json",
    "openwhisper.db",
    "transcription_history.json",
    "transcription_history.json.bak",
    ".env",
)


def get_app_data_dir() -> Path:
    """Return a writable location that survives application updates."""
    override = os.environ.get(APP_DATA_ENV)
    if override:
        destination = Path(override).expanduser().resolve()
    elif sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        destination = base / "Welinkton" / "MeetingRecorder"
    elif sys.platform == "darwin":
        destination = (
            Path.home()
            / "Library"
            / "Application Support"
            / "Welinkton"
            / "MeetingRecorder"
        )
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        destination = base / "welinkton" / "meeting-recorder"

    destination.mkdir(parents=True, exist_ok=True)
    return destination


def migrate_legacy_user_data(destination: Path, legacy_root: Path | None = None) -> None:
    """Copy legacy working-directory data once, without overwriting newer data."""
    source_root = (legacy_root or Path.cwd()).resolve()
    if source_root == destination.resolve():
        return

    for filename in APP_DATA_FILES:
        source = source_root / filename
        target = destination / filename
        if source.is_file() and not target.exists():
            try:
                shutil.copy2(source, target)
            except OSError:
                # A read-only legacy folder must not prevent the application
                # from starting with fresh per-user state.
                continue
