"""
History management for transcriptions and recordings.
Stores transcription history and manages the last N audio recordings.
"""
import logging
import os
import shutil
import json
import re
import sys
import time
from datetime import datetime
from difflib import SequenceMatcher
from typing import List, Optional
from dataclasses import dataclass

from config import config
from services.database import db
from services.format_utils import format_file_size, format_timestamp
from services.models import TranscriptionHistory as HistoryEntry
from services.settings import resolve_max_saved_recordings, settings_manager, SettingsKey

logger = logging.getLogger(__name__)

# Sentinel so callers can pass ``max_recordings=None`` for keep-all.
_UNSET = object()
SUPPORTED_AUDIO_EXTENSIONS = {
    ".wav", ".mp3", ".m4a", ".ogg", ".flac", ".wma", ".aac"
}
SUPPORTED_VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v"
}
SUPPORTED_TRANSCRIPT_EXTENSIONS = {".md", ".txt", ".json"}
SUPPORTED_LIBRARY_EXTENSIONS = (
    SUPPORTED_AUDIO_EXTENSIONS
    | SUPPORTED_VIDEO_EXTENSIONS
    | SUPPORTED_TRANSCRIPT_EXTENSIONS
)
NO_SPEECH_TRANSCRIPT = (
    "Нечего расшифровывать: в записи не обнаружена речь."
)

_MEETING_ROLE_WORDS = {
    "audio",
    "codex",
    "raw",
    "аудио",
    "видео",
    "видеозапись",
    "восстановленная",
    "восстановленное",
    "восстановленный",
    "вычитанная",
    "и",
    "исправленная",
    "итог",
    "найденная",
    "отполированная",
    "отредактированная",
    "расшифровка",
    "расшифровки",
    "запись",
}
_MEDIA_REFERENCE_PATTERNS = (
    re.compile(
        r"`([^`\r\n]+\.(?:wav|mp3|m4a|ogg|flac|wma|aac|mp4|mkv|webm|mov|avi|m4v))`",
        re.IGNORECASE,
    ),
    re.compile(
        r'"([^"\r\n]+\.(?:wav|mp3|m4a|ogg|flac|wma|aac|mp4|mkv|webm|mov|avi|m4v))"',
        re.IGNORECASE,
    ),
    re.compile(
        r"(?im)^(?:#+\s*)?(?:источник|исходный файл|запись|файл|расшифровка|source)"
        r"\s*:\s*(.+?\.(?:wav|mp3|m4a|ogg|flac|wma|aac|mp4|mkv|webm|mov|avi|m4v))\s*$",
    ),
)


def _meeting_identity(value: str) -> str:
    """Return a stable identity shared by media and transcript variants."""
    stem = os.path.splitext(os.path.basename(value))[0]
    stem = stem.casefold().replace("ё", "е")
    stem = re.sub(r"(?<!\d)20(\d{2})(?!\d)", r"\1", stem)
    words = re.findall(r"[a-zа-я0-9]+", stem)
    identity = "".join(
        word for word in words if word not in _MEETING_ROLE_WORDS
    )
    return identity or "".join(words)


def _date_signature(value: str):
    """Extract date and optional time so similar meetings are not confused."""
    stem = os.path.splitext(os.path.basename(value))[0]
    compact_match = re.search(
        r"(?<!\d)(20\d{2})(\d{2})(\d{2})[_-]"
        r"(\d{2})(\d{2})(\d{2})(?!\d)",
        stem,
    )
    if compact_match:
        year, month, day, hour, minute, second = compact_match.groups()
        return (
            (int(day), int(month), int(year[-2:])),
            (int(hour), int(minute), int(second)),
        )
    date_match = re.search(
        r"(?<!\d)(\d{1,2})[._-](\d{1,2})[._-](\d{2,4})(?!\d)",
        stem,
    )
    if not date_match:
        return None, None
    day, month, year = date_match.groups()
    if len(year) == 4:
        year = year[-2:]
    date = (int(day), int(month), int(year))
    remainder = stem[date_match.end():]
    time_match = re.search(
        r"(?<!\d)(\d{1,2})[-:](\d{2})(?:[-:](\d{2}))?(?!\d)",
        remainder,
    )
    if not time_match:
        return date, None
    hour, minute, second = time_match.groups()
    return date, (int(hour), int(minute), int(second or 0))


def _read_transcript_file(path: str) -> str:
    """Read a human transcript from Markdown, text, or Whisper JSON."""
    if not path or not os.path.isfile(path):
        return ""
    text = ""
    for encoding in ("utf-8-sig", "utf-16", "cp1251"):
        try:
            with open(path, "r", encoding=encoding) as handle:
                text = handle.read()
            break
        except (UnicodeError, OSError):
            continue
    if not text:
        return ""
    if os.path.splitext(path)[1].lower() != ".json":
        return text.strip()
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return text.strip()
    if isinstance(payload, dict):
        if isinstance(payload.get("text"), str):
            return payload["text"].strip()
        segments = payload.get("segments")
        if isinstance(segments, list):
            lines = []
            for segment in segments:
                if not isinstance(segment, dict):
                    continue
                content = str(segment.get("text") or "").strip()
                if not content:
                    continue
                start = float(segment.get("start") or 0)
                minutes, seconds = divmod(int(start), 60)
                lines.append(f"[{minutes:02d}:{seconds:02d}] {content}")
            if lines:
                return "\n".join(lines)
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _has_transcript_content(text: str) -> bool:
    """Distinguish actual speech text from source/model metadata only."""
    if not text or not text.strip():
        return False
    metadata_prefixes = (
        "исходник:",
        "источник:",
        "исходный файл:",
        "модель:",
        "source:",
        "model:",
    )
    for line in text.splitlines():
        stripped = line.strip().casefold()
        if not stripped:
            continue
        if stripped.startswith(metadata_prefixes):
            continue
        return True
    return False


def _referenced_media_identities(text: str):
    identities = set()
    for pattern in _MEDIA_REFERENCE_PATTERNS:
        for match in pattern.findall(text or ""):
            identity = _meeting_identity(match)
            if identity:
                identities.add(identity)
    return identities


def _transcript_quality(path: str, text: str) -> int:
    """Prefer edited Markdown over raw model output for the same meeting."""
    name = os.path.basename(path).casefold()
    score = {".md": 35, ".txt": 20, ".json": 5}.get(
        os.path.splitext(path)[1].lower(),
        0,
    )
    quality_markers = (
        ("codex", 100),
        ("исправлен", 45),
        ("отредактирован", 44),
        ("отполирован", 42),
        ("вычитан", 40),
        ("итог", 36),
        ("восстановлен", 30),
    )
    score += next(
        (bonus for marker, bonus in quality_markers if marker in name),
        0,
    )
    if "raw" in name:
        score -= 45
    if re.search(r"\[\d{1,2}:\d{2}(?::\d{2})?", text):
        score += 8
    score += min(12, len(text) // 8000)
    return score


def _transcript_match_score(
    meeting_key: str,
    media_paths,
    transcript,
) -> Optional[float]:
    transcript_key = transcript["identity"]
    text_casefold = transcript["text"].casefold()
    media_names = [
        os.path.basename(path).casefold()
        for path in media_paths
    ]
    if any(name and name in text_casefold for name in media_names):
        base_score = 1020.0
    elif meeting_key in transcript["references"]:
        base_score = 1010.0
    elif meeting_key == transcript_key:
        base_score = 1000.0
    else:
        media_date, media_time = _date_signature(media_paths[0])
        transcript_date, transcript_time = _date_signature(
            transcript["path"]
        )
        if (
            media_date
            and transcript_date
            and media_date != transcript_date
        ):
            return None
        if (
            media_time
            and transcript_time
            and media_time != transcript_time
        ):
            return None
        shorter = min(len(meeting_key), len(transcript_key))
        if (
            shorter >= 8
            and (
                meeting_key in transcript_key
                or transcript_key in meeting_key
            )
        ):
            base_score = 700.0 + (
                100.0 * shorter / max(len(meeting_key), len(transcript_key))
            )
        else:
            similarity = SequenceMatcher(
                None,
                meeting_key,
                transcript_key,
            ).ratio()
            if similarity < 0.84:
                return None
            base_score = 500.0 + similarity * 100.0
    return base_score + _transcript_quality(
        transcript["path"],
        transcript["text"],
    )


@dataclass
class RecordingInfo:
    """Represents a saved audio recording."""
    filename: str
    timestamp: str
    file_path: str
    size_bytes: int

    @property
    def formatted_timestamp(self) -> str:
        """Get human-readable timestamp."""
        return format_timestamp(self.timestamp)

    @property
    def formatted_size(self) -> str:
        """Get human-readable file size."""
        return format_file_size(self.size_bytes)


@dataclass
class MeetingMediaInfo:
    """One visible meeting source, optionally pairing audio with video."""

    filename: str
    timestamp: str
    file_path: str
    size_bytes: int
    media_type: str
    transcription_path: str
    audio_path: Optional[str] = None
    video_path: Optional[str] = None
    transcript_path: Optional[str] = None
    bundle_paths: tuple[str, ...] = ()

    @property
    def formatted_timestamp(self) -> str:
        return format_timestamp(self.timestamp)

    @property
    def formatted_size(self) -> str:
        return format_file_size(self.size_bytes)


class HistoryManager:
    """Manages transcription history and saved recordings."""

    def __init__(
        self,
        recordings_folder: str = None,
        max_recordings: Optional[int] = _UNSET,
    ):
        """Initialize the history manager.

        Args:
            recordings_folder: Path to folder for saved recordings.
            max_recordings: Maximum number of recordings to keep, or ``None``
                to keep all. When omitted, loads from settings (default custom
                limit from config).
        """
        self.recordings_folder = recordings_folder or settings_manager.get(
            SettingsKey.RECORDINGS_FOLDER, config.RECORDINGS_FOLDER
        )
        if max_recordings is _UNSET:
            self.max_recordings = resolve_max_saved_recordings()
        else:
            self.max_recordings = max_recordings

        # Ensure recordings folder exists
        os.makedirs(self.recordings_folder, exist_ok=True)
        self._cleanup_stale_auxiliary_files()

        logger.info(
            "HistoryManager initialized (recordings: %s, max: %s)",
            self.recordings_folder,
            self.max_recordings if self.max_recordings is not None else "all",
        )

    def set_recordings_folder(self, folder: str) -> int:
        """Apply a recordings folder and return the meetings found there.

        Settings can change while the application is running. Keeping this
        transition inside the manager prevents the UI from updating only the
        persisted path while the live library continues scanning the old one.
        """
        if not folder or not str(folder).strip():
            raise ValueError("Recordings folder cannot be empty")
        normalized = os.path.abspath(
            os.path.expandvars(os.path.expanduser(os.fspath(folder).strip()))
        )
        os.makedirs(normalized, exist_ok=True)
        self.recordings_folder = normalized
        meetings_found = len(self.get_media_files())
        logger.info(
            "Recordings folder changed to %s (%d meetings found)",
            normalized,
            meetings_found,
        )
        return meetings_found

    def get_library_snapshot(self) -> dict[str, tuple[int, int]]:
        """Return a cheap fingerprint of every visible meeting artifact.

        The snapshot is used by the UI to notice external creates, edits,
        deletes, size changes, and renames without continuously rebuilding the
        whole meeting list.
        """
        snapshot: dict[str, tuple[int, int]] = {}
        folder = os.path.abspath(self.recordings_folder)
        if not os.path.isdir(folder):
            return snapshot
        try:
            for root, directory_names, filenames in os.walk(folder):
                directory_names[:] = [
                    name for name in directory_names
                    if not name.startswith(".")
                ]
                for filename in filenames:
                    if (
                        os.path.splitext(filename)[1].lower()
                        not in SUPPORTED_LIBRARY_EXTENSIONS
                    ):
                        continue
                    path = os.path.abspath(os.path.join(root, filename))
                    try:
                        stat = os.stat(path)
                    except OSError:
                        continue
                    snapshot[path] = (stat.st_size, stat.st_mtime_ns)
        except OSError as exc:
            logger.debug("Could not fingerprint meeting folder: %s", exc)
        return snapshot

    def get_library_watch_paths(self) -> tuple[str, ...]:
        """Return existing folders and files worth watching for live changes."""
        folder = os.path.abspath(self.recordings_folder)
        if not os.path.isdir(folder):
            return ()
        paths = []
        try:
            for root, directory_names, filenames in os.walk(folder):
                directory_names[:] = [
                    name for name in directory_names
                    if not name.startswith(".")
                ]
                paths.append(os.path.abspath(root))
                paths.extend(
                    os.path.abspath(os.path.join(root, filename))
                    for filename in filenames
                    if (
                        os.path.splitext(filename)[1].lower()
                        in SUPPORTED_LIBRARY_EXTENSIONS
                    )
                )
        except OSError as exc:
            logger.debug("Could not enumerate meeting watch paths: %s", exc)
        return tuple(dict.fromkeys(paths))

    def reconcile_external_renames(
        self,
        previous_snapshot: dict[str, tuple[int, int]],
        current_snapshot: dict[str, tuple[int, int]],
    ) -> dict[str, str]:
        """Repair database media references after an external file rename.

        A rename preserves file size and modification time. We only accept a
        one-to-one fingerprint match, avoiding guesses when two recordings are
        indistinguishable.
        """
        media_extensions = (
            SUPPORTED_AUDIO_EXTENSIONS | SUPPORTED_VIDEO_EXTENSIONS
        )
        removed = {
            path: fingerprint
            for path, fingerprint in previous_snapshot.items()
            if (
                path not in current_snapshot
                and os.path.splitext(path)[1].lower() in media_extensions
            )
        }
        added = {
            path: fingerprint
            for path, fingerprint in current_snapshot.items()
            if (
                path not in previous_snapshot
                and os.path.splitext(path)[1].lower() in media_extensions
            )
        }
        if not removed or not added:
            return {}

        removed_by_fingerprint = {}
        added_by_fingerprint = {}
        for path, fingerprint in removed.items():
            removed_by_fingerprint.setdefault(fingerprint, []).append(path)
        for path, fingerprint in added.items():
            added_by_fingerprint.setdefault(fingerprint, []).append(path)

        renames = {}
        entries = self.get_history()
        folder = os.path.abspath(self.recordings_folder)
        for fingerprint, old_paths in removed_by_fingerprint.items():
            new_paths = added_by_fingerprint.get(fingerprint, [])
            if len(old_paths) != 1 or len(new_paths) != 1:
                continue
            old_path, new_path = old_paths[0], new_paths[0]
            if (
                os.path.splitext(old_path)[1].lower()
                != os.path.splitext(new_path)[1].lower()
            ):
                continue
            old_relative = os.path.relpath(old_path, folder)
            new_relative = os.path.relpath(new_path, folder)
            old_names = {
                os.path.normcase(old_relative),
                os.path.normcase(os.path.basename(old_relative)),
            }
            matched = False
            for entry in entries:
                if not entry.audio_file:
                    continue
                entry_names = {
                    os.path.normcase(entry.audio_file),
                    os.path.normcase(os.path.basename(entry.audio_file)),
                }
                if not old_names & entry_names:
                    continue
                matched = (
                    db.update_history_audio_file(
                        entry.id,
                        new_relative,
                        file_size=fingerprint[0],
                    )
                    or matched
                )
            if matched:
                renames[old_path] = new_path
                logger.info(
                    "Reconciled externally renamed meeting file: %s -> %s",
                    old_relative,
                    new_relative,
                )
        return renames

    def reconcile_missing_history_media(
        self,
        meetings: Optional[List[MeetingMediaInfo]] = None,
    ) -> int:
        """Recover renamed media references after the app was not running.

        Missing database references are matched only when exactly one
        unclaimed file has the same extension and byte size.
        """
        meetings = meetings if meetings is not None else self.get_media_files()
        entries = self.get_history()
        folder = os.path.abspath(self.recordings_folder)
        claimed = {
            os.path.normcase(os.path.abspath(path))
            for entry in entries
            for path in (
                self.get_recording_path(entry.audio_file)
                if entry.audio_file
                else None,
            )
            if path
        }
        candidates = []
        for meeting in meetings:
            for path in (meeting.audio_path, meeting.video_path, meeting.file_path):
                if not path:
                    continue
                normalized = os.path.normcase(os.path.abspath(path))
                if normalized in claimed:
                    continue
                if any(
                    os.path.normcase(os.path.abspath(existing)) == normalized
                    for existing in candidates
                ):
                    continue
                candidates.append(path)

        updated = 0
        for entry in entries:
            if not entry.audio_file or self.get_recording_path(entry.audio_file):
                continue
            expected_size = int(entry.file_size or 0)
            if expected_size <= 0:
                continue
            expected_extension = os.path.splitext(entry.audio_file)[1].lower()
            matches = []
            for path in candidates:
                if os.path.splitext(path)[1].lower() != expected_extension:
                    continue
                try:
                    if os.path.getsize(path) == expected_size:
                        matches.append(path)
                except OSError:
                    continue
            if len(matches) != 1:
                continue
            new_path = matches[0]
            new_relative = os.path.relpath(new_path, folder)
            if db.update_history_audio_file(
                entry.id,
                new_relative,
                file_size=expected_size,
            ):
                updated += 1
                candidates.remove(new_path)
                logger.info(
                    "Recovered renamed meeting reference: %s -> %s",
                    entry.audio_file,
                    new_relative,
                )
        return updated

    def _cleanup_stale_auxiliary_files(self) -> None:
        """Remove abandoned loopback WAV files left by an earlier crash."""
        cutoff = time.time() - 60
        try:
            with os.scandir(self.recordings_folder) as entries:
                for entry in entries:
                    if (
                        not entry.is_file()
                        or not entry.name.casefold().endswith(".system.wav")
                    ):
                        continue
                    if entry.stat().st_mtime >= cutoff:
                        continue
                    try:
                        os.unlink(entry.path)
                        logger.info(
                            "Removed stale system-audio temporary file: %s",
                            entry.name,
                        )
                    except OSError as exc:
                        logger.warning(
                            "Could not remove stale system-audio file %s: %s",
                            entry.name,
                            exc,
                        )
        except OSError as exc:
            logger.warning(
                "Could not scan recording folder for stale temporary files: %s",
                exc,
            )

        # A force-closed screen capture can leave only an MP4 container header.
        # Cleanup is deliberately restricted to this application's timestamped
        # filenames and to tiny, old files with no matching WAV.
        incomplete_capture = re.compile(
            r"^Встреча \d{2}\.\d{2}\.\d{4} \d{2}-\d{2}-\d{2}(?: \(\d+\))?\.mp4$",
            re.IGNORECASE,
        )
        try:
            with os.scandir(self.recordings_folder) as entries:
                for entry in entries:
                    if not entry.is_file() or not incomplete_capture.match(entry.name):
                        continue
                    stat = entry.stat()
                    if stat.st_mtime >= cutoff or stat.st_size > 1024:
                        continue
                    wav_path = os.path.splitext(entry.path)[0] + ".wav"
                    if os.path.exists(wav_path):
                        continue
                    try:
                        os.unlink(entry.path)
                        logger.info(
                            "Removed incomplete screen recording: %s",
                            entry.name,
                        )
                    except OSError as exc:
                        logger.warning(
                            "Could not remove incomplete screen recording %s: %s",
                            entry.name,
                            exc,
                        )
        except OSError as exc:
            logger.warning(
                "Could not scan recording folder for incomplete captures: %s",
                exc,
            )

    def set_max_recordings(self, max_recordings: Optional[int]) -> None:
        """Update the retention limit and rotate immediately if needed.

        Args:
            max_recordings: Maximum recordings to keep, or ``None`` to keep all.
        """
        self.max_recordings = max_recordings
        logger.info(
            "Recording retention updated (max: %s)",
            max_recordings if max_recordings is not None else "all",
        )
        self._rotate_recordings()

    def new_meeting_path(
        self,
        extension: str,
        moment: Optional[datetime] = None,
    ) -> str:
        """Return an unused, readable path for a new meeting artifact.

        Windows file names cannot contain ``:``, so the date is written in the
        familiar ``day.month.year`` format and the time uses hyphens.  Audio,
        video and transcript sidecars reuse this stem.
        """
        extension = extension if extension.startswith(".") else f".{extension}"
        current = moment or datetime.now()
        stem = f"Встреча {current.strftime('%d.%m.%Y %H-%M-%S')}"
        candidate = os.path.join(
            self.recordings_folder,
            f"{stem}{extension.lower()}",
        )
        suffix = 2
        while os.path.exists(candidate):
            candidate = os.path.join(
                self.recordings_folder,
                f"{stem} ({suffix}){extension.lower()}",
            )
            suffix += 1
        return candidate

    def add_entry(
        self,
        text: str,
        model: str,
        source_audio_path: Optional[str] = None,
        transcription_time: Optional[float] = None,
        audio_duration: Optional[float] = None,
        file_size: Optional[int] = None,
        raw_text: Optional[str] = None,
        cleanup_provider: Optional[str] = None,
        cleanup_model: Optional[str] = None,
        screen_video_path: Optional[str] = None,
    ) -> HistoryEntry:
        """Add a new transcription to history.

        Args:
            text: The transcribed text (fixed/cleaned when cleanup ran).
            model: The model used for transcription (display name or internal value).
            source_audio_path: Optional path to source audio file to save.
            transcription_time: Time taken to transcribe in seconds.
            audio_duration: Duration of the audio in seconds.
            file_size: Size of the audio file in bytes.
            raw_text: Unprocessed ASR text when distinct from ``text``.
            cleanup_provider: Cleanup API provider when cleanup ran.
            cleanup_model: Cleanup chat model id when cleanup ran.

        Returns:
            The created HistoryEntry.
        """
        saved_audio_path = None

        # Save the audio recording if provided
        if source_audio_path and os.path.exists(source_audio_path):
            saved_audio_path = self._save_recording(source_audio_path)

        # Keep a human-readable transcript beside its audio source. The first
        # line is an explicit source reference, which makes the binding durable
        # even outside the application/database.
        if saved_audio_path:
            try:
                audio_path = os.path.join(self.recordings_folder, saved_audio_path)
                transcript_path = os.path.splitext(audio_path)[0] + ".txt"
                visible_text = (
                    raw_text
                    if cleanup_provider == "codex" and raw_text
                    else text
                )
                with open(transcript_path, "w", encoding="utf-8") as handle:
                    handle.write(f"Исходник: {os.path.basename(audio_path)}\n")
                    handle.write(f"Модель: {model}\n\n")
                    handle.write(visible_text)
                if cleanup_provider == "codex":
                    codex_path = os.path.splitext(audio_path)[0] + ".codex.md"
                    with open(codex_path, "w", encoding="utf-8") as handle:
                        handle.write(f"Исходник: {os.path.basename(audio_path)}\n")
                        handle.write(
                            f"Обработка: Codex ({cleanup_model or 'по умолчанию'})\n\n"
                        )
                        handle.write(text)
                if screen_video_path and os.path.exists(screen_video_path):
                    video_path = os.path.splitext(audio_path)[0] + ".mp4"
                    if os.path.normcase(screen_video_path) != os.path.normcase(video_path):
                        shutil.move(screen_video_path, video_path)
            except OSError as exc:
                logger.error("Failed to save transcript beside recording: %s", exc)

        # Create the entry
        entry = HistoryEntry.create(
            text=text,
            model=model,
            audio_file=saved_audio_path,
            transcription_time=transcription_time,
            audio_duration=audio_duration,
            file_size=file_size,
            raw_text=raw_text,
            cleanup_provider=cleanup_provider,
            cleanup_model=cleanup_model,
        )

        # Save to database
        db.add_history_entry(
            entry_id=entry.id,
            text=entry.text,
            timestamp=entry.timestamp,
            model=entry.model,
            audio_file=entry.audio_file,
            transcription_time=entry.transcription_time,
            audio_duration=entry.audio_duration,
            file_size=entry.file_size,
            raw_text=entry.raw_text,
            cleanup_provider=entry.cleanup_provider,
            cleanup_model=entry.cleanup_model,
        )

        logger.info(f"Added history entry: {entry.id[:8]}...")
        return entry

    def save_transcript_version(
        self,
        audio_path: str,
        text: str,
        model: str = "",
        variant: str = "",
        history_entry_id: str = "",
        original_text: str = "",
    ) -> Optional[str]:
        """Persist a raw or enhanced transcript beside its meeting media."""
        if not audio_path or not text or not text.strip():
            return None
        audio_path = os.path.abspath(audio_path)
        stem = os.path.splitext(audio_path)[0]
        if variant == "codex":
            transcript_path = stem + ".codex.md"
            metadata = f"Обработка: Codex ({model or 'по умолчанию'})"
        else:
            transcript_path = stem + ".txt"
            metadata = f"Модель: {model}" if model else ""
        try:
            with open(transcript_path, "w", encoding="utf-8") as handle:
                handle.write(f"Исходник: {os.path.basename(audio_path)}\n")
                if metadata:
                    handle.write(metadata + "\n")
                handle.write("\n")
                handle.write(text)
            if variant == "codex" and history_entry_id:
                entry = self.get_entry_by_id(history_entry_id)
                # Files discovered directly in the recordings folder use their
                # full path as a stable UI identifier.  They deliberately have
                # no database row, so a successful sidecar write must not be
                # reported as a save failure merely because that path cannot be
                # updated as a DB primary key.
                if entry is not None:
                    source_text = original_text.strip()
                    if not source_text:
                        source_text = (
                            (entry.raw_text or "").strip()
                            or (entry.text or "").strip()
                        )
                    if not db.update_history_entry_cleanup(
                        history_entry_id,
                        text,
                        source_text,
                        "codex",
                        model,
                    ):
                        return None
                else:
                    logger.info(
                        "Saved Codex sidecar for filesystem-only meeting: %s",
                        transcript_path,
                    )
            return transcript_path
        except OSError as exc:
            logger.error("Failed to save transcript version %s: %s", transcript_path, exc)
            return None

    def save_codex_history_version(
        self,
        entry_id: str,
        original_text: str,
        improved_text: str,
        model: str = "",
    ) -> Optional[str]:
        """Save Codex output for a database-only history entry.

        Older entries can retain their transcript after the recording itself
        has been removed.  Give those entries a durable sidecar and update the
        same database row instead of requiring a nonexistent audio file.
        """
        entry = self.get_entry_by_id(entry_id)
        if entry is None or not improved_text.strip():
            return None

        stamp = (entry.timestamp or "")[:19].replace("T", " ").replace(":", "-")
        if not stamp:
            stamp = entry_id[:8]
        filename = f"Расшифровка {stamp} ({entry_id[:8]}).codex.md"
        transcript_path = os.path.join(self.recordings_folder, filename)
        try:
            os.makedirs(self.recordings_folder, exist_ok=True)
            with open(transcript_path, "w", encoding="utf-8") as handle:
                handle.write(f"История: {entry_id}\n")
                handle.write(f"Обработка: Codex ({model or 'по умолчанию'})\n\n")
                handle.write(improved_text)
        except OSError as exc:
            logger.error(
                "Failed to save Codex version for history entry %s: %s",
                entry_id,
                exc,
            )
            return None

        if not db.update_history_entry_cleanup(
            entry_id,
            improved_text,
            original_text,
            "codex",
            model,
        ):
            return None
        return transcript_path

    def _save_recording(self, source_path: str) -> Optional[str]:
        """Save a recording to the recordings folder with rotation.

        Args:
            source_path: Path to the source audio file.

        Returns:
            Relative path to saved recording, or None if failed.
        """
        try:
            source_path = os.path.abspath(source_path)
            recordings_folder = os.path.abspath(self.recordings_folder)
            if os.path.dirname(source_path) == recordings_folder:
                self._rotate_recordings()
                return os.path.basename(source_path)

            # Use one readable Russian date/time convention for new meetings.
            dest_path = self.new_meeting_path(".wav")
            filename = os.path.basename(dest_path)

            # Copy the file
            shutil.copy2(source_path, dest_path)
            logger.info(f"Saved recording: {filename}")

            # Rotate old recordings
            self._rotate_recordings()

            return filename

        except Exception as e:
            logger.error(f"Failed to save recording: {e}")
            return None

    def save_recording(self, source_path: str) -> Optional[str]:
        """Persist a source recording immediately and return its full path.

        Recordings are saved before transcription starts so a model error or
        cancellation never loses the user's audio.
        """
        filename = self._save_recording(source_path)
        if not filename:
            return None
        return os.path.join(self.recordings_folder, filename)

    def _rotate_recordings(self) -> None:
        """Remove oldest recordings if we exceed max_recordings."""
        if self.max_recordings is None:
            return

        try:
            recordings = self.get_recordings()

            if len(recordings) > self.max_recordings:
                # Sort by timestamp (oldest first)
                recordings.sort(key=lambda r: r.timestamp)

                # Remove oldest recordings
                to_remove = recordings[:-self.max_recordings]
                for rec in to_remove:
                    try:
                        self._remove_recording_bundle(rec.file_path)
                        logger.info(f"Removed old recording: {rec.filename}")

                        # Clear audio_file reference in database
                        db.clear_history_audio_file(rec.filename)

                    except Exception as e:
                        logger.error(f"Failed to remove recording {rec.filename}: {e}")

        except Exception as e:
            logger.error(f"Failed to rotate recordings: {e}")

    def get_history(self, limit: Optional[int] = None) -> List[HistoryEntry]:
        """Get transcription history entries.

        Args:
            limit: Optional maximum number of entries to return.

        Returns:
            List of HistoryEntry objects (newest first).
        """
        return db.get_history_entries(limit)

    def get_recordings(self) -> List[RecordingInfo]:
        """Get list of saved recordings.

        Returns:
            List of RecordingInfo objects (newest first).
        """
        recordings = []

        try:
            if not os.path.exists(self.recordings_folder):
                return recordings

            for filename in os.listdir(self.recordings_folder):
                if filename.endswith('.wav'):
                    file_path = os.path.join(self.recordings_folder, filename)

                    # Get file info
                    stat = os.stat(file_path)

                    # Extract timestamp from filename (recording_YYYYMMDD_HHMMSS.wav)
                    try:
                        parts = filename.replace('recording_', '').replace('.wav', '')
                        try:
                            dt = datetime.strptime(parts, "%Y%m%d_%H%M%S_%f")
                        except ValueError:
                            dt = datetime.strptime(parts, "%Y%m%d_%H%M%S")
                        timestamp = dt.isoformat()
                    except Exception:
                        # Fallback to file modification time
                        timestamp = datetime.fromtimestamp(stat.st_mtime).isoformat()

                    recordings.append(RecordingInfo(
                        filename=filename,
                        timestamp=timestamp,
                        file_path=file_path,
                        size_bytes=stat.st_size
                    ))

            # Sort by timestamp (newest first)
            recordings.sort(key=lambda r: r.timestamp, reverse=True)

        except Exception as e:
            logger.error(f"Failed to get recordings: {e}")

        return recordings

    def get_media_files(self) -> List[MeetingMediaInfo]:
        """Find existing meeting audio and video in the selected folder.

        Media variants such as ``— запись.webm`` and
        ``— восстановленное аудио.wav`` are shown as one meeting. Existing
        Markdown/text/JSON transcripts are attached by their explicit source
        reference first and by their normalized meeting name second.
        """
        meetings: List[MeetingMediaInfo] = []
        folder = os.path.abspath(self.recordings_folder)
        if not os.path.isdir(folder):
            return meetings

        grouped = {}
        transcripts = []
        try:
            for root, directory_names, filenames in os.walk(folder):
                directory_names[:] = [
                    name for name in directory_names
                    if not name.startswith(".")
                ]
                for filename in filenames:
                    extension = os.path.splitext(filename)[1].lower()
                    path = os.path.join(root, filename)
                    if extension in SUPPORTED_TRANSCRIPT_EXTENSIONS:
                        text = _read_transcript_file(path)
                        if text:
                            transcripts.append(
                                {
                                    "path": path,
                                    "text": text,
                                    "identity": _meeting_identity(filename),
                                    "references": _referenced_media_identities(
                                        text
                                    ),
                                }
                            )
                        continue
                    if (
                        extension not in SUPPORTED_AUDIO_EXTENSIONS
                        and extension not in SUPPORTED_VIDEO_EXTENSIONS
                    ):
                        continue
                    identity = _meeting_identity(filename)
                    key = (
                        os.path.normcase(root),
                        identity,
                    )
                    group = grouped.setdefault(
                        key,
                        {
                            "identity": identity,
                            "root": root,
                            "audio": [],
                            "video": [],
                        },
                    )
                    kind = (
                        "video"
                        if extension in SUPPORTED_VIDEO_EXTENSIONS
                        else "audio"
                    )
                    group[kind].append(path)

            audio_priority = {
                ".wav": 0, ".flac": 1, ".m4a": 2, ".mp3": 3,
                ".ogg": 4, ".wma": 5, ".aac": 6,
            }
            video_priority = {
                ".mp4": 0, ".mkv": 1, ".webm": 2, ".mov": 3,
                ".m4v": 4, ".avi": 5,
            }
            for group in grouped.values():
                audio_files = sorted(
                    group["audio"],
                    key=lambda path: (
                        audio_priority.get(
                            os.path.splitext(path)[1].lower(),
                            99,
                        ),
                        os.path.basename(path).casefold(),
                    ),
                )
                video_files = sorted(
                    group["video"],
                    key=lambda path: (
                        0
                        if "запись" in os.path.basename(path).casefold()
                        else 1,
                        video_priority.get(
                            os.path.splitext(path)[1].lower(),
                            99,
                        ),
                        os.path.basename(path).casefold(),
                    ),
                )
                audio_path = audio_files[0] if audio_files else None
                video_path = video_files[0] if video_files else None
                primary_path = video_path or audio_path
                if not primary_path:
                    continue
                transcription_path = audio_path or video_path
                all_media_paths = audio_files + video_files
                bundle_paths = list(
                    dict.fromkeys(
                        path for path in (audio_path, video_path) if path
                    )
                )
                stats = [os.stat(path) for path in bundle_paths]
                modified = max(stat.st_mtime for stat in stats)
                best_transcript_path = None
                best_transcript_score = float("-inf")
                matched_transcript_paths = []
                for transcript in transcripts:
                    score = _transcript_match_score(
                        group["identity"],
                        all_media_paths,
                        transcript,
                    )
                    if score is None:
                        continue
                    matched_transcript_paths.append(transcript["path"])
                    if (
                        os.path.normcase(
                            os.path.dirname(transcript["path"])
                        )
                        == os.path.normcase(group["root"])
                    ):
                        score += 10
                    if score > best_transcript_score:
                        best_transcript_score = score
                        best_transcript_path = transcript["path"]
                meetings.append(
                    MeetingMediaInfo(
                        filename=os.path.basename(primary_path),
                        timestamp=datetime.fromtimestamp(modified).isoformat(),
                        file_path=primary_path,
                        size_bytes=sum(stat.st_size for stat in stats),
                        media_type="video" if video_path else "audio",
                        transcription_path=transcription_path,
                        audio_path=audio_path,
                        video_path=video_path,
                        transcript_path=best_transcript_path,
                        bundle_paths=tuple(
                            dict.fromkeys(
                                all_media_paths + matched_transcript_paths
                            )
                        ),
                    )
                )
        except OSError as exc:
            logger.error("Failed to scan meeting folder: %s", exc)

        meetings.sort(key=lambda item: item.timestamp, reverse=True)
        return meetings

    @staticmethod
    def read_transcript(path: str) -> str:
        """Return readable transcript text for a discovered sidecar file."""
        return _read_transcript_file(path)

    @staticmethod
    def has_transcript_content(text: str) -> bool:
        """Return True only when a transcript contains recognized speech."""
        return _has_transcript_content(text)

    def get_entry_by_id(self, entry_id: str) -> Optional[HistoryEntry]:
        """Get a specific history entry by ID.

        Args:
            entry_id: The entry ID to find.

        Returns:
            The HistoryEntry or None if not found.
        """
        return db.get_history_entry_by_id(entry_id)

    def get_meeting_bundle_paths(self, source_path: str) -> tuple[str, ...]:
        """Return every media/transcript file belonging to one meeting."""
        if not source_path:
            return ()
        folder = os.path.abspath(self.recordings_folder)
        source = os.path.abspath(source_path)
        try:
            if os.path.commonpath((folder, source)) != folder:
                logger.warning(
                    "Refusing to resolve a meeting outside the recordings folder: %s",
                    source,
                )
                return ()
        except ValueError:
            return ()

        normalized_source = os.path.normcase(source)
        for meeting in self.get_media_files():
            bundle = tuple(
                path for path in meeting.bundle_paths
                if path and os.path.isfile(path)
            )
            if any(
                os.path.normcase(os.path.abspath(path)) == normalized_source
                for path in bundle
            ):
                return bundle
        return (source,) if os.path.isfile(source) else ()

    def move_meeting_to_trash(
        self,
        source_path: str,
        history_entry_id: str = "",
    ) -> tuple[str, ...]:
        """Move a complete meeting package to the OS recycle bin."""
        paths = self.get_meeting_bundle_paths(source_path)
        if not paths:
            raise FileNotFoundError("Файлы выбранной встречи не найдены")

        _move_paths_to_trash(paths)
        moved = {
            os.path.normcase(os.path.abspath(path))
            for path in paths
        }
        moved_names = {
            os.path.normcase(os.path.basename(path))
            for path in paths
        }

        # Remove every database row linked to the moved package, not only the
        # currently visible row. A meeting can have been transcribed more than
        # once and therefore have several history entries.
        for entry in self.get_history():
            entry_path = (
                self.get_recording_path(entry.audio_file)
                if entry.audio_file
                else None
            )
            if (
                entry.id == history_entry_id
                or (
                    entry_path
                    and os.path.normcase(os.path.abspath(entry_path)) in moved
                )
                or (
                    entry.audio_file
                    and os.path.normcase(os.path.basename(entry.audio_file))
                    in moved_names
                )
            ):
                db.delete_history_entry(entry.id)

        logger.info(
            "Moved meeting package to trash (%d files): %s",
            len(paths),
            os.path.basename(source_path),
        )
        return paths

    def delete_entry(
        self,
        entry_id: str,
        delete_audio_file: bool = False,
    ) -> bool:
        """Delete a history entry and optionally its saved audio file.

        Args:
            entry_id: The entry ID to delete.
            delete_audio_file: Whether to also delete the entry's saved audio.

        Returns:
            True if deleted, False if not found.
        """
        entry = db.get_history_entry_by_id(entry_id) if delete_audio_file else None
        result = db.delete_history_entry(entry_id)
        if result:
            logger.info(f"Deleted history entry: {entry_id[:8]}...")
            if entry and entry.audio_file:
                self._delete_recording_file(entry.audio_file)
        return result

    def _delete_recording_file(self, filename: str) -> bool:
        """Delete a saved recording and clear any remaining database references."""
        audio_path = self.get_recording_path(filename)
        if not audio_path:
            db.clear_history_audio_file(filename)
            logger.info("Saved recording already absent: %s", filename)
            return True

        try:
            self._remove_recording_bundle(audio_path)
        except OSError as exc:
            logger.error("Failed to delete saved recording %s: %s", filename, exc)
            return False

        db.clear_history_audio_file(filename)
        logger.info("Deleted saved recording: %s", filename)
        return True

    @staticmethod
    def _remove_recording_bundle(audio_path: str) -> None:
        """Remove audio and its transcript/screen-video sidecars."""
        stem, _ = os.path.splitext(audio_path)
        for path in (audio_path, stem + ".txt", stem + ".mp4"):
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                logger.warning("Could not remove recording sidecar: %s", path)

    def clear_history(self) -> None:
        """Clear all history entries (keeps recordings)."""
        db.clear_history()
        logger.info("History cleared")

    def clear_history_and_recordings(self) -> None:
        """Clear all history entries and delete saved recordings from disk."""
        for rec in self.get_recordings():
            try:
                self._remove_recording_bundle(rec.file_path)
            except Exception as e:
                logger.error(f"Failed to remove recording {rec.filename}: {e}")
        db.clear_history()
        logger.info("History and recordings cleared")

    def get_recording_path(self, filename: str) -> Optional[str]:
        """Get full path to a recording by filename.

        Args:
            filename: The recording filename.

        Returns:
            Full path to the file, or None if not found.
        """
        if not filename:
            return None

        file_path = os.path.join(self.recordings_folder, filename)
        if os.path.exists(file_path):
            return file_path
        return None


class _LazyHistoryManager:
    """Create the history manager only when history is first used."""

    def __init__(self) -> None:
        self._instance: Optional[HistoryManager] = None

    def _get_instance(self) -> HistoryManager:
        if self._instance is None:
            self._instance = HistoryManager()
        return self._instance

    def __getattr__(self, name: str):
        return getattr(self._get_instance(), name)


# Public lazy history manager proxy.
history_manager = _LazyHistoryManager()


def _move_paths_to_trash(paths) -> None:
    """Move existing files to the platform trash without permanent deletion."""
    existing = [
        os.path.abspath(path)
        for path in dict.fromkeys(paths)
        if path and os.path.exists(path)
    ]
    if not existing:
        raise FileNotFoundError("Файлы встречи уже отсутствуют")

    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = (
                ("hwnd", wintypes.HWND),
                ("wFunc", wintypes.UINT),
                ("pFrom", wintypes.LPCWSTR),
                ("pTo", wintypes.LPCWSTR),
                ("fFlags", wintypes.WORD),
                ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", ctypes.c_void_p),
                ("lpszProgressTitle", wintypes.LPCWSTR),
            )

        operation = SHFILEOPSTRUCTW()
        operation.wFunc = 3  # FO_DELETE
        operation.pFrom = "\0".join(existing) + "\0\0"
        operation.fFlags = (
            0x0040  # FOF_ALLOWUNDO: use the Recycle Bin
            | 0x0010  # FOF_NOCONFIRMATION
            | 0x0004  # FOF_SILENT
            | 0x0400  # FOF_NOERRORUI
        )
        result = ctypes.windll.shell32.SHFileOperationW(
            ctypes.byref(operation)
        )
        if result or operation.fAnyOperationsAborted:
            raise OSError(
                result,
                "Windows не удалось переместить встречу в корзину",
            )
        return

    try:
        from send2trash import send2trash
    except ImportError as exc:
        raise RuntimeError(
            "Перенос в корзину доступен только после установки send2trash"
        ) from exc
    for path in existing:
        send2trash(path)
