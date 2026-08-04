"""Stable per-user paths used by installed and development builds."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


APP_DATA_ENV = "SVODIKA_DATA_DIR"
LEGACY_APP_DATA_ENV = "MEETING_RECORDER_DATA_DIR"
APP_DATA_FILES = (
    "openwhisper_settings.json",
    "openwhisper.db",
    "transcription_history.json",
    "transcription_history.json.bak",
    ".env",
)


def get_app_data_dir() -> Path:
    """Return a writable location that survives application updates."""
    override = os.environ.get(APP_DATA_ENV) or os.environ.get(
        LEGACY_APP_DATA_ENV
    )
    if override:
        destination = Path(override).expanduser().resolve()
    elif sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        destination = base / "Svodika"
    elif sys.platform == "darwin":
        destination = (
            Path.home()
            / "Library"
            / "Application Support"
            / "Svodika"
        )
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        destination = base / "svodika"

    destination.mkdir(parents=True, exist_ok=True)
    return destination


def _legacy_app_data_dirs() -> tuple[Path, ...]:
    """Return branded storage paths used before the Svodika rename."""
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        base = (
            Path(local_app_data)
            if local_app_data
            else Path.home() / "AppData" / "Local"
        )
        return (base / "Welinkton" / "MeetingRecorder",)
    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "Welinkton"
            / "MeetingRecorder",
        )
    base = Path(
        os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
    )
    return (base / "welinkton" / "meeting-recorder",)


def migrate_legacy_user_data(
    destination: Path,
    legacy_root: Path | None = None,
) -> None:
    """Copy legacy data once without overwriting newer Svodika files."""
    source_roots = (
        (legacy_root,)
        if legacy_root is not None
        else (*_legacy_app_data_dirs(), Path.cwd())
    )
    resolved_destination = destination.resolve()

    for source_root in source_roots:
        resolved_source = source_root.resolve()
        if resolved_source == resolved_destination:
            continue

        for filename in APP_DATA_FILES:
            source = resolved_source / filename
            target = destination / filename
            if source.is_file() and not target.exists():
                try:
                    shutil.copy2(source, target)
                except OSError:
                    # A read-only legacy folder must not prevent startup.
                    continue
