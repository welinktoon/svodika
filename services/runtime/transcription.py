"""Recording and transcription helpers for the application controller."""

from __future__ import annotations

import logging
import os
import shutil
import threading
import time
from typing import TYPE_CHECKING, Optional

import pyperclip

from config import config
from services.hotkey_manager import is_accessibility_trusted, send_paste
from services.audio_processor import audio_processor
from services.history_manager import NO_SPEECH_TRANSCRIPT, history_manager
from services.screen_recorder import ScreenRecorder
from services.transcript_cleanup import CleanupInfo, TranscriptCleanup
from services.codex_cleanup import (
    CodexCleanupMode,
    CodexTranscriptCleanup,
    extract_original_transcript,
)
try:
    from services.settings import (
        CodexCleanupTrigger,
        SettingsKey,
        compose_transcript_cleanup_prompt,
        resolve_codex_cleanup_enabled,
        resolve_codex_cleanup_mode,
        resolve_codex_cleanup_trigger,
        resolve_transcript_cleanup_model,
        resolve_transcript_cleanup_prompt,
        resolve_transcript_cleanup_provider,
        resolve_transcript_cleanup_reasoning,
        resolve_transcript_cleanup_rules,
        settings_manager,
    )
except ImportError:  # pragma: no cover - supports lightweight test stubs
    from services.settings import settings_manager

    class SettingsKey:
        AUTO_PASTE = "auto_paste"
        COPY_CLIPBOARD = "copy_clipboard"
        TRANSCRIPT_CLEANUP_ENABLED = "transcript_cleanup_enabled"

    def resolve_transcript_cleanup_prompt(settings=None):
        return config.TRANSCRIPT_CLEANUP_PROMPT

    def resolve_transcript_cleanup_provider(settings=None):
        return config.TRANSCRIPT_CLEANUP_PROVIDER

    def resolve_transcript_cleanup_model(settings=None):
        return config.TRANSCRIPT_CLEANUP_MODEL

    def resolve_transcript_cleanup_reasoning(settings=None):
        return config.TRANSCRIPT_CLEANUP_REASONING

    def resolve_transcript_cleanup_rules(settings=None):
        return []

    def compose_transcript_cleanup_prompt(base_prompt, rules):
        return base_prompt

    def resolve_codex_cleanup_enabled(settings=None):
        return False

    def resolve_codex_cleanup_mode(settings=None):
        return CodexCleanupMode.FULL

    def resolve_codex_cleanup_trigger(settings=None):
        return "manual"

    class CodexCleanupTrigger:
        MANUAL = "manual"
        AUTOMATIC = "automatic"

from ui_qt.overlay_state import OverlayState

if TYPE_CHECKING:
    from services.application_controller import ApplicationController

logger = logging.getLogger(__name__)


def _transcript_body(text: str) -> str:
    """Remove the source/model header before sending saved text to Codex."""
    lines = (text or "").splitlines()
    metadata_prefixes = (
        "исходник:",
        "источник:",
        "исходный файл:",
        "модель:",
        "обработка:",
        "source:",
        "model:",
    )
    index = 0
    saw_metadata = False
    while index < len(lines):
        stripped = lines[index].strip()
        if not stripped:
            index += 1
            if saw_metadata:
                break
            continue
        if stripped.casefold().startswith(metadata_prefixes):
            saw_metadata = True
            index += 1
            continue
        break
    if not saw_metadata:
        return (text or "").strip()
    return "\n".join(lines[index:]).strip() or (text or "").strip()


class CodexCleanupCanceled(Exception):
    """Internal control flow for a user-canceled Codex cleanup."""


class TranscriptionRuntime:
    """Owns recording flow and transcription job orchestration."""

    def __init__(self, controller: "ApplicationController"):
        self.controller = controller
        self._transcript_cleanup = TranscriptCleanup()
        self._codex_cleanup = CodexTranscriptCleanup()
        self._cleanup_fallback_message = ""
        self._job_lock = threading.Lock()
        self._active_transcription_path = ""

    @property
    def is_transcribing(self) -> bool:
        with self._job_lock:
            return bool(self._active_transcription_path)

    def _claim_transcription_job(self, audio_path: str) -> bool:
        with self._job_lock:
            if self._active_transcription_path:
                return False
            self._active_transcription_path = os.path.abspath(audio_path)
            return True

    def _release_transcription_job(self) -> str:
        with self._job_lock:
            audio_path = self._active_transcription_path
            self._active_transcription_path = ""
            return audio_path

    def _reject_duplicate_transcription(self) -> None:
        self.controller.status_update.emit("Расшифровка уже идёт")
        self.controller.ui_controller.set_transcription_state(
            "transcribing", self._active_transcription_path
        )

    def _start_screen_recorder(self, recorder: ScreenRecorder) -> None:
        """Start optional screen capture without blocking the Qt event loop."""
        if recorder.start():
            logger.info("Screen recording started")
            return
        logger.warning(
            "Screen recording did not start: %s",
            recorder.error,
        )
        if self.controller.screen_recorder is recorder:
            self.controller._pending_screen_path = None

    def start_recording(self) -> None:
        """Start audio recording."""
        if self.controller.recorder.start_recording():
            logger.info("Recording started")
            workspace = getattr(self.controller.ui_controller.main_window, "voice_notes_workspace", None)
            if workspace is not None and workspace.screen.isChecked():
                screen_path = history_manager.new_meeting_path(".mp4")
                settings = settings_manager.load_all_settings()
                try:
                    video_fps = int(settings.get(
                        SettingsKey.VIDEO_RECORDING_FPS,
                        config.VIDEO_RECORDING_FPS,
                    ))
                except (TypeError, ValueError):
                    video_fps = config.VIDEO_RECORDING_FPS
                try:
                    video_crf = int(settings.get(
                        SettingsKey.VIDEO_RECORDING_CRF,
                        config.VIDEO_RECORDING_CRF,
                    ))
                except (TypeError, ValueError):
                    video_crf = config.VIDEO_RECORDING_CRF
                recorder = ScreenRecorder(
                    screen_path,
                    fps=max(5, min(30, video_fps)),
                    crf=max(18, min(28, video_crf)),
                    audio_sample_rate=self.controller.recorder.rate,
                    capture_system_audio=True,
                )
                self.controller.screen_recorder = recorder
                self.controller._pending_screen_path = screen_path
                threading.Thread(
                    target=self._start_screen_recorder,
                    args=(recorder,),
                    name="meeting-screen-start",
                    daemon=True,
                ).start()
            self.controller.ui_controller.clear_transcription_stats()
            self.controller.ui_controller.main_window.clear_partial_transcription()
            self.controller.streaming_runtime.start_streaming_session()
            self.controller.recording_state_changed.emit(True)
            self.controller.overlay_state_update.emit(OverlayState.RECORDING)
            self.controller.status_update.emit("Идёт запись…")
        else:
            self.controller.overlay_state_update.emit(OverlayState.NONE)
            self.controller.status_update.emit("Не удалось начать запись")

    def stop_recording(self) -> None:
        """Stop audio recording and start transcription."""
        if self.controller._streaming_enabled:
            # Dismiss preview overlay immediately so the classic waveform
            # processing/transcribing states are the only post-stop UI.
            self.controller.streaming_overlay_hide.emit()

        self.controller.streaming_runtime.stop_streaming_session()

        if not self.controller.recorder.stop_recording():
            if self.controller.screen_recorder is not None:
                self.controller.screen_recorder.stop()
                self.controller.screen_recorder.cleanup_auxiliary()
                self.controller.screen_recorder = None
            self.controller.overlay_state_update.emit(OverlayState.NONE)
            self.controller.status_update.emit("Не удалось остановить запись")
            return

        self.controller.recording_state_changed.emit(False)
        self.controller.overlay_state_update.emit(OverlayState.PROCESSING)
        self.controller.status_update.emit("Обработка записи…")

        if not self.controller.recorder.wait_for_stop_completion():
            logger.warning(
                "Proceeding without confirmed post-roll completion; "
                "tail of recording may be short"
            )

        screen_recorder = self.controller.screen_recorder
        if screen_recorder is not None:
            screen_recorder.stop()
            if screen_recorder.error:
                logger.error(
                    "Screen recording failed: %s",
                    screen_recorder.error,
                )
                self.controller._pending_screen_path = None
            self.controller.screen_recorder = None

        if not self.controller.recorder.has_recording_data():
            logger.error("No recording data available")
            self.on_transcription_error("No audio data recorded")
            return

        if not self.controller.recorder.save_recording():
            logger.error("Failed to save recording")
            self.on_transcription_error("Failed to save audio file")
            return

        if not os.path.exists(config.RECORDED_AUDIO_FILE):
            logger.error(f"Audio file not found: {config.RECORDED_AUDIO_FILE}")
            self.on_transcription_error("Audio file not created")
            return

        file_size = os.path.getsize(config.RECORDED_AUDIO_FILE)
        logger.info(f"Audio file size: {file_size} bytes")
        if file_size < 100:
            logger.error(f"Audio file too small: {file_size} bytes")
            self.on_transcription_error("Audio file is empty or corrupted")
            return

        source_audio_path = config.RECORDED_AUDIO_FILE
        if (
            screen_recorder is not None
            and self.controller._pending_screen_path
        ):
            meeting_audio_path = os.path.splitext(
                self.controller._pending_screen_path
            )[0] + ".wav"
            source_audio_path = screen_recorder.build_meeting_audio(
                config.RECORDED_AUDIO_FILE,
                meeting_audio_path,
            )

        save_recording = getattr(history_manager, "save_recording", None)
        persisted_audio_path = (
            save_recording(source_audio_path)
            if callable(save_recording)
            else source_audio_path
        )
        if not persisted_audio_path:
            self.on_transcription_error("Не удалось сохранить аудиофайл")
            return
        self.controller._pending_audio_path = persisted_audio_path
        self.controller._pending_audio_duration = (
            self.controller.recorder.get_recording_duration()
        )
        self.controller._pending_file_size = file_size

        if (
            self.controller._pending_screen_path
            and os.path.exists(self.controller._pending_screen_path)
        ):
            video_path = os.path.splitext(persisted_audio_path)[0] + ".mp4"
            try:
                if os.path.normcase(self.controller._pending_screen_path) != os.path.normcase(video_path):
                    shutil.move(self.controller._pending_screen_path, video_path)
                self.controller._pending_screen_path = video_path
            except OSError as exc:
                logger.error("Failed to bind screen video to recording: %s", exc)
            if screen_recorder is not None:
                if not screen_recorder.mux_audio(persisted_audio_path):
                    logger.warning("Meeting video was saved without an audio track")
        if screen_recorder is not None:
            screen_recorder.cleanup_auxiliary()

        try:
            self.controller.ui_controller.refresh_history()
            self._submit_transcription_job(persisted_audio_path)
            logger.info(
                "Transcription started. Duration: "
                f"{self.controller.recorder.get_recording_duration():.2f}s"
            )
        except Exception as exc:
            logger.error(f"Failed to start transcription: {exc}")
            self.on_transcription_error(f"Failed to process audio: {exc}")

    def toggle_recording(self) -> None:
        """Toggle between starting and stopping recording."""
        logger.info(
            f"Toggle recording. Current state: {self.controller.recorder.is_recording}"
        )
        if not self.controller.recorder.is_recording:
            self.start_recording()
        else:
            self.stop_recording()

    def cancel(self) -> None:
        """Cancel an active recording or transcription, depending on state."""
        logger.info(f"Cancel called. Recording: {self.controller.recorder.is_recording}")

        if self.controller.recorder.is_recording:
            self._cancel_recording()
        elif self._codex_cleanup.is_running:
            self._cancel_codex_cleanup()
        elif self.controller.current_backend and self.controller.current_backend.is_transcribing:
            self._cancel_transcription()
        else:
            self.controller.overlay_state_update.emit(OverlayState.CANCELING)
            self.controller.status_update.emit("Отменено")

    def _cancel_codex_cleanup(self) -> None:
        """Stop the local Codex process while keeping the original transcript safe."""
        self._codex_cleanup.cancel()
        audio_path = self._release_transcription_job()
        self.controller.ui_controller.set_transcription_state(
            "canceled", audio_path
        )
        self.controller.overlay_state_update.emit(OverlayState.CANCELING)
        self.controller.status_update.emit("Обработка текста отменена")
        self.controller.ui_controller.refresh_history()
        self.controller._pending_audio_path = None
        self.controller._pending_audio_duration = None
        self.controller._pending_file_size = None
        self.controller._pending_screen_path = None
        logger.info("Codex transcript cleanup canceled")

    def _cancel_recording(self) -> None:
        """Discard the active recording without transcribing."""
        self.controller.streaming_runtime.cancel_streaming_session()
        if self.controller.screen_recorder is not None:
            self.controller.screen_recorder.stop()
            self.controller.screen_recorder.cleanup_auxiliary()
            self.controller.screen_recorder = None
        if self.controller._pending_screen_path:
            try:
                if os.path.exists(self.controller._pending_screen_path):
                    os.remove(self.controller._pending_screen_path)
            except OSError:
                logger.warning("Could not delete canceled screen recording")
            self.controller._pending_screen_path = None
        self.controller.recording_state_changed.emit(False)
        self.controller.recorder.stop_recording()
        self.controller.recorder.clear_recording_data()
        self.controller.overlay_state_update.emit(OverlayState.CANCELING)
        self.controller.status_update.emit("Запись отменена")
        logger.info("Recording canceled")

    def _cancel_transcription(self) -> None:
        """Cancel an in-progress transcription job."""
        self.controller.current_backend.cancel_transcription()
        audio_path = self._release_transcription_job()
        self.controller.ui_controller.set_transcription_state(
            "canceled", audio_path
        )
        self.controller.overlay_state_update.emit(OverlayState.CANCELING)
        self.controller.status_update.emit("Расшифровка отменена")
        self.controller.ui_controller.refresh_history()
        self.controller._pending_audio_path = None
        self.controller._pending_audio_duration = None
        self.controller._pending_file_size = None
        self.controller._pending_screen_path = None
        logger.info("Transcription canceled")

    def retranscribe_audio(self, audio_path: str) -> None:
        """Re-transcribe an existing audio file.

        Args:
            audio_path: Path to the saved recording.
        """
        if not os.path.exists(audio_path):
            logger.error(
                f"Audio file not found for re-transcription: {audio_path}"
            )
            self.controller.overlay_state_update.emit(OverlayState.NONE)
            self.controller.status_update.emit("Ошибка: аудиофайл не найден")
            return
        if self.is_transcribing:
            self._reject_duplicate_transcription()
            return

        logger.info("Re-transcribing audio file: %s", audio_path)
        self.controller._pending_audio_path = audio_path
        self.controller.overlay_state_update.emit(OverlayState.PROCESSING)
        self.controller.status_update.emit("Обработка записи…")

        try:
            self.controller._pending_file_size = os.path.getsize(audio_path)
            self.controller._pending_audio_duration = None
            self._submit_transcription_job(audio_path)
        except Exception as exc:
            logger.error(f"Failed to start re-transcription: {exc}")
            self.on_transcription_error(f"Failed to process audio: {exc}")

    def upload_audio_file(self, audio_path: str) -> None:
        """Transcribe an uploaded audio file."""
        if not os.path.exists(audio_path):
            logger.error(f"Uploaded audio file not found: {audio_path}")
            self.controller.overlay_state_update.emit(OverlayState.NONE)
            self.controller.status_update.emit("Ошибка: аудиофайл не найден")
            return
        if self.is_transcribing:
            self._reject_duplicate_transcription()
            return

        logger.info(f"Processing uploaded audio file: {audio_path}")
        # Keep the transcript durably linked to the selected meeting source.
        # HistoryManager keeps files already inside the recordings folder in
        # place and copies external imports into it.
        self.controller._pending_audio_path = audio_path
        self.controller.overlay_state_update.emit(OverlayState.PROCESSING)
        self.controller.status_update.emit("Обработка выбранного файла…")

        try:
            self.controller._pending_file_size = os.path.getsize(audio_path)
            self.controller._pending_audio_duration = None
            self._submit_transcription_job(audio_path)
        except Exception as exc:
            logger.error(f"Failed to process uploaded audio: {exc}")
            self.on_transcription_error(f"Failed to process audio: {exc}")

    def improve_existing_transcript(
        self,
        audio_path: str,
        transcript: str,
        history_entry_id: str = "",
        mode: str = "",
    ) -> None:
        """Improve an already saved transcript without running Whisper again."""
        logger.info(
            "Manual Codex improvement requested: %s",
            audio_path,
        )
        has_audio = bool(audio_path and os.path.isfile(audio_path))
        has_history_entry = bool(
            history_entry_id
            and history_manager.get_entry_by_id(history_entry_id)
        )
        if not has_audio and not has_history_entry:
            self.controller.status_update.emit(
                "Не удалось найти запись выбранной встречи"
            )
            return
        if not transcript or not transcript.strip():
            self.controller.status_update.emit(
                "У выбранной встречи пока нет расшифровки"
            )
            return
        settings = settings_manager.load_all_settings()
        if not resolve_codex_cleanup_enabled(settings):
            self.controller.status_update.emit(
                "Сначала включите Codex в настройках обработки текста"
            )
            return
        job_path = audio_path if has_audio else os.path.join(
            history_manager.recordings_folder,
            f".history-{history_entry_id}",
        )
        if not self._claim_transcription_job(job_path):
            self._reject_duplicate_transcription()
            return

        selected_mode = CodexCleanupMode.normalize(
            mode or resolve_codex_cleanup_mode(settings)
        )
        original_transcript = extract_original_transcript(
            _transcript_body(transcript)
        )
        self.controller.overlay_state_update.emit(OverlayState.CLEANING)
        self.controller.status_update.emit(
            f"Codex: {CodexCleanupMode.LABELS[selected_mode]}…"
        )
        self.controller.ui_controller.set_transcription_state(
            "cleaning", job_path
        )
        self.controller.executor.submit(
            self._improve_existing_transcript_worker,
            audio_path,
            original_transcript,
            selected_mode,
            "\n".join(resolve_transcript_cleanup_rules(settings)),
            history_entry_id,
        )
        logger.info("Manual Codex improvement submitted")

    def _improve_existing_transcript_worker(
        self,
        audio_path: str,
        transcript: str,
        mode: str,
        extra_prompt: str,
        history_entry_id: str = "",
    ) -> None:
        try:
            logger.info("Manual Codex improvement worker started")
            fixed = self._codex_cleanup.cleanup(
                transcript,
                mode=mode,
                extra_prompt=extra_prompt,
            )
            if self._codex_cleanup.last_error == "canceled":
                return
            if self._codex_cleanup.last_error is not None:
                self.controller.codex_improvement_failed.emit(
                    audio_path,
                    self._codex_cleanup.last_error,
                )
                return
            if history_entry_id and not os.path.isfile(audio_path):
                transcript_path = history_manager.save_codex_history_version(
                    history_entry_id,
                    transcript,
                    fixed,
                    model=mode,
                )
            else:
                transcript_path = history_manager.save_transcript_version(
                    audio_path,
                    fixed,
                    model=mode,
                    variant="codex",
                    history_entry_id=history_entry_id,
                    original_text=transcript,
                )
            if not transcript_path:
                self.controller.codex_improvement_failed.emit(
                    audio_path,
                    "Не удалось сохранить улучшенную версию",
                )
                return
            self.controller.codex_improvement_completed.emit(
                audio_path,
                fixed,
            )
            logger.info(
                "Manual Codex improvement completed: %s",
                transcript_path,
            )
        except Exception as exc:
            logger.exception("Manual Codex improvement failed")
            self.controller.codex_improvement_failed.emit(
                audio_path,
                str(exc),
            )

    def on_codex_improvement_complete(
        self,
        audio_path: str,
        transcript: str,
    ) -> None:
        self._release_transcription_job()
        self.controller.ui_controller.set_transcript(transcript)
        self.controller.ui_controller.set_transcription_state(
            "complete", audio_path
        )
        self.controller.ui_controller.refresh_history()
        self.controller.ui_controller.set_status(
            "Готово — создана улучшенная версия Codex"
        )
        self.controller.overlay_state_update.emit(OverlayState.NONE)

    def on_codex_improvement_error(
        self,
        audio_path: str,
        error_message: str,
    ) -> None:
        self._release_transcription_job()
        logger.warning(
            "Manual Codex improvement failed for %s: %s",
            audio_path,
            error_message,
        )
        self.controller.ui_controller.set_transcription_state(
            "error", audio_path, error_message
        )
        self.controller.ui_controller.set_status(
            f"Не удалось улучшить через Codex: {error_message}"
        )
        self.controller.overlay_state_update.emit(OverlayState.NONE)
        if hasattr(
            self.controller.ui_controller,
            "show_codex_improvement_error",
        ):
            self.controller.ui_controller.show_codex_improvement_error(
                error_message
            )

    def _maybe_cleanup_transcript(
        self, raw: str
    ) -> tuple[str, Optional[str], Optional[CleanupInfo]]:
        """Optionally clean up ASR text.

        Args:
            raw: Unprocessed ASR transcript.

        Returns:
            Tuple of (fixed text, raw text when it differs from fixed, and
            the CleanupInfo of the run when cleanup actually happened —
            None when cleanup was disabled, unavailable, or failed).
        """
        settings = settings_manager.load_all_settings()
        self._cleanup_fallback_message = ""
        if (
            resolve_codex_cleanup_enabled(settings)
            and resolve_codex_cleanup_trigger(settings)
            == CodexCleanupTrigger.AUTOMATIC
            and raw
            and raw.strip()
        ):
            # Persist the Whisper result before starting Codex. Cancellation,
            # login problems, or a network error can never lose the source text.
            history_manager.save_transcript_version(
                self._active_transcription_path,
                raw,
                model=self.controller._current_model_name,
            )
            self.controller.ui_controller.refresh_history()
            self.controller.overlay_state_update.emit(OverlayState.CLEANING)
            self.controller.status_update.emit("Обработка текста в Codex…")
            self.controller.ui_controller.set_transcription_state(
                "cleaning", self._active_transcription_path
            )
            rules = resolve_transcript_cleanup_rules(settings)
            fixed = self._codex_cleanup.cleanup(
                raw,
                mode=resolve_codex_cleanup_mode(settings),
                extra_prompt="\n".join(rules),
            )
            if self._codex_cleanup.last_error == "canceled":
                raise CodexCleanupCanceled()
            if self._codex_cleanup.last_error is None:
                return (
                    fixed,
                    raw if fixed != raw else None,
                    CleanupInfo(
                        provider="codex",
                        model=resolve_codex_cleanup_mode(settings),
                    ),
                )
            self._cleanup_fallback_message = (
                "Codex недоступен — сохранён исходный текст"
            )
            logger.warning(
                "Codex cleanup failed; using raw transcript: %s",
                self._codex_cleanup.last_error,
            )
            return raw, None, None

        enabled = settings.get(
            SettingsKey.TRANSCRIPT_CLEANUP_ENABLED,
            config.TRANSCRIPT_CLEANUP_ENABLED,
        )
        if not enabled or not raw or not raw.strip():
            return raw, None, None

        # Re-apply provider/model each run so Settings changes take effect
        # without restarting (a provider switch rebuilds the client).
        self._transcript_cleanup.configure(
            resolve_transcript_cleanup_provider(settings),
            resolve_transcript_cleanup_model(settings),
            resolve_transcript_cleanup_reasoning(settings),
        )
        if not self._transcript_cleanup.is_available():
            logger.warning(
                "Transcript cleanup enabled but unavailable; using raw text"
            )
            return raw, None, None

        self.controller.overlay_state_update.emit(OverlayState.CLEANING)
        self.controller.status_update.emit("Обработка текста…")
        prompt = compose_transcript_cleanup_prompt(
            resolve_transcript_cleanup_prompt(settings),
            resolve_transcript_cleanup_rules(settings),
        )
        fixed = self._transcript_cleanup.cleanup(raw, system_prompt=prompt)
        # A changed transcript also proves cleanup ran, covering stubs that
        # bypass the real cleanup() and never touch last_error.
        cleaned = self._transcript_cleanup.last_error is None or fixed != raw
        info = (
            CleanupInfo(
                provider=self._transcript_cleanup.provider,
                model=self._transcript_cleanup.model,
            )
            if cleaned
            else None
        )
        if fixed != raw:
            return fixed, raw, info
        return fixed, None, info

    def transcribe_audio_file(self, audio_path: str) -> None:
        """Transcribe a single audio file in a background thread."""
        try:
            if self.controller._pending_file_size is None:
                self.controller._pending_file_size = os.path.getsize(audio_path)
            self.controller.overlay_state_update.emit(OverlayState.TRANSCRIBING)
            self.controller.status_update.emit("Расшифровка…")
            self.controller.ui_controller.set_transcription_state(
                "transcribing", audio_path
            )
            self.controller._transcription_start_time = time.time()
            raw = self.controller.current_backend.transcribe(audio_path)
            fixed, raw_text, cleanup_info = self._maybe_cleanup_transcript(raw)
            self.controller.transcription_completed.emit(fixed, raw_text, cleanup_info)
        except CodexCleanupCanceled:
            logger.info("Codex cleanup worker stopped after cancellation")
        except Exception as exc:
            logger.error(f"Transcription failed: {exc}")
            self.controller.transcription_failed.emit(str(exc))

    def transcribe_large_audio_file(self, audio_path: str) -> None:
        """Transcribe a large audio file by splitting it into chunks."""
        chunk_files = []
        if self.controller._pending_file_size is None:
            self.controller._pending_file_size = os.path.getsize(audio_path)
        self.controller._transcription_start_time = time.time()
        try:
            self.controller.ui_controller.set_transcription_state(
                "transcribing", audio_path
            )
            def progress_callback(message: str) -> None:
                self.controller.status_update.emit(message)

            chunk_files = audio_processor.split_audio_file(
                audio_path, progress_callback
            )
            if not chunk_files:
                raise Exception("Не удалось разделить аудиофайл")

            if hasattr(self.controller.current_backend, "transcribe_chunks"):
                self.controller.overlay_state_update.emit(OverlayState.TRANSCRIBING)
                self.controller.status_update.emit(
                    f"Расшифровка частей: {len(chunk_files)}…"
                )
                raw = self.controller.current_backend.transcribe_chunks(
                    chunk_files
                )
            else:
                transcripts = []
                for index, chunk_file in enumerate(chunk_files):
                    self.controller.overlay_state_update.emit(OverlayState.TRANSCRIBING)
                    self.controller.status_update.emit(
                        f"Расшифровка части {index + 1} из {len(chunk_files)}…"
                    )
                    transcripts.append(
                        self.controller.current_backend.transcribe(chunk_file)
                    )
                raw = audio_processor.combine_transcriptions(transcripts)

            fixed, raw_text, cleanup_info = self._maybe_cleanup_transcript(raw)
            self.controller.transcription_completed.emit(fixed, raw_text, cleanup_info)
        except CodexCleanupCanceled:
            logger.info("Codex cleanup worker stopped after cancellation")
        except Exception as exc:
            logger.error(f"Large audio transcription failed: {exc}")
            self.controller.transcription_failed.emit(str(exc))
        finally:
            try:
                audio_processor.cleanup_temp_files()
            except Exception as cleanup_error:
                logger.warning(
                    f"Failed to cleanup temp files: {cleanup_error}"
                )

    def on_transcription_complete(
        self,
        transcript: str,
        raw_text: Optional[str] = None,
        cleanup_info: Optional[CleanupInfo] = None,
    ) -> None:
        """Handle transcription completion."""
        completed_audio_path = (
            self.controller._pending_audio_path
            or self._active_transcription_path
        )
        if not transcript or not transcript.strip():
            marker_path = history_manager.save_transcript_version(
                completed_audio_path,
                NO_SPEECH_TRANSCRIPT,
                model=self.controller._current_model_name,
            )
            self.controller.ui_controller.set_transcript(NO_SPEECH_TRANSCRIPT)
            self.controller.ui_controller.set_transcription_state(
                "complete",
                completed_audio_path,
            )
            self.controller.ui_controller.set_status(
                "Речь не обнаружена — создана пометка в расшифровке"
            )
            self.controller.overlay_state_update.emit(OverlayState.NONE)
            self.controller._transcription_start_time = None
            self.controller.ui_controller.refresh_history()
            self._release_transcription_job()
            self.controller._pending_audio_path = None
            self.controller._pending_audio_duration = None
            self.controller._pending_file_size = None
            self.controller._pending_screen_path = None
            logger.info(
                "Transcription contained no speech; marker saved to %s",
                marker_path,
            )
            return
        self.controller.ui_controller.set_transcript(transcript, raw=raw_text)
        self.controller.ui_controller.set_transcription_state(
            "complete", completed_audio_path
        )
        self.controller.ui_controller.set_status(
            self._cleanup_fallback_message or "Расшифровка готова"
        )
        self.controller.overlay_state_update.emit(OverlayState.NONE)

        transcription_time = None
        if self.controller._transcription_start_time is not None:
            transcription_time = time.time() - self.controller._transcription_start_time
            self.controller._transcription_start_time = None

        if transcription_time is not None:
            self.controller.ui_controller.set_transcription_stats(
                transcription_time,
                self.controller._pending_audio_duration or 0.0,
                self.controller._pending_file_size or 0,
            )

        try:
            model_info = self.controller._current_model_name
            if self.controller._current_model_name == "local_whisper":
                local_backend = self.controller.transcription_backends.get("local_whisper")
                if local_backend and hasattr(local_backend, "device_info"):
                    model_info = f"local_whisper ({local_backend.device_info})"

            history_manager.add_entry(
                text=transcript,
                model=model_info,
                source_audio_path=self.controller._pending_audio_path,
                transcription_time=transcription_time,
                audio_duration=self.controller._pending_audio_duration,
                file_size=self.controller._pending_file_size,
                raw_text=raw_text,
                cleanup_provider=cleanup_info.provider if cleanup_info else None,
                cleanup_model=cleanup_info.model if cleanup_info else None,
                screen_video_path=self.controller._pending_screen_path,
            )
            self.controller.ui_controller.refresh_history()
            logger.info("Transcription saved to history")
        except Exception as exc:
            logger.error(f"Failed to save transcription to history: {exc}")
        finally:
            self._release_transcription_job()
            self.controller._pending_audio_path = None
            self.controller._pending_audio_duration = None
            self.controller._pending_file_size = None
            self.controller._pending_screen_path = None

        settings = settings_manager.load_all_settings()
        copy_clipboard = settings.get(SettingsKey.COPY_CLIPBOARD, True)
        auto_paste = settings.get(SettingsKey.AUTO_PASTE, True)

        # Synthetic paste posts a key event, which needs macOS Accessibility
        # permission. Without it, degrade to clipboard so the text isn't lost and
        # the user can paste manually with Cmd+V.
        paste_blocked = auto_paste and not is_accessibility_trusted()

        if copy_clipboard or paste_blocked:
            try:
                pyperclip.copy(transcript)
                logger.info("Transcription copied to clipboard")
            except Exception as exc:
                logger.error(f"Failed to copy to clipboard: {exc}")

        if auto_paste and not paste_blocked:
            try:
                send_paste()
                logger.info("Transcription auto-pasted")
                self.controller.ui_controller.set_status("Готово — текст вставлен")
            except Exception as exc:
                logger.error(f"Failed to auto-paste: {exc}")
                self.controller.ui_controller.set_status(
                    "Расшифровка готова — вставить текст не удалось"
                )
        elif paste_blocked:
            logger.warning(
                "Auto-paste skipped: macOS Accessibility permission not granted."
            )
            self.controller.ui_controller.set_status(
                "Скопировано в буфер обмена — вставьте текст вручную"
            )
        else:
            self.controller.ui_controller.set_status("Готово")

        if cleanup_info and cleanup_info.provider == "codex":
            self.controller.ui_controller.set_status("Готово — улучшено в Codex")
        elif self._cleanup_fallback_message:
            self.controller.ui_controller.set_status(
                self._cleanup_fallback_message
            )

    def on_transcription_error(self, error_message: str) -> None:
        """Handle transcription error."""
        lowered_error = (error_message or "").casefold()
        if (
            "invalid data found when processing input" in lowered_error
            or "moov atom not found" in lowered_error
        ):
            error_message = (
                "Файл повреждён или запись не была корректно завершена. "
                "Не удалось прочитать аудиодорожку"
            )
        failed_audio_path = (
            self.controller._pending_audio_path
            or self._active_transcription_path
        )
        self.controller.ui_controller.set_status(f"Ошибка: {error_message}")
        self.controller.ui_controller.set_transcript(f"Ошибка: {error_message}")
        self.controller.ui_controller.set_transcription_state(
            "error", failed_audio_path, error_message
        )
        self.controller.overlay_state_update.emit(OverlayState.NONE)
        self._release_transcription_job()
        self.controller._pending_audio_path = None
        self.controller._pending_audio_duration = None
        self.controller._pending_file_size = None
        self.controller._pending_screen_path = None

    def on_model_changed(self, model_name: str) -> None:
        """Handle model selection change."""
        model_value = config.MODEL_VALUE_MAP.get(model_name)
        if model_value and model_value in self.controller.transcription_backends:
            self.controller.current_backend = self.controller.transcription_backends[
                model_value
            ]
            self.controller._current_model_name = model_value
            settings_manager.save_model_selection(model_value)
            logger.info(f"Switched to model: {model_value}")

            if model_value == "local_whisper":
                local_backend = self.controller.transcription_backends.get("local_whisper")
                if local_backend and hasattr(local_backend, "device_info"):
                    self.controller.ui_controller.set_device_info(
                        local_backend.device_info
                    )
                # A missing local model needs the download-consent flow the
                # moment the user selects this backend.
                self.controller.ensure_local_model_available()
            else:
                self.controller.ui_controller.set_device_info("")

            # Streaming preview requires Local Whisper; rebuild when backend changes.
            self.controller.streaming_runtime.reconfigure_streaming()

    def show_large_file_overlay(self, file_size_mb: float, is_splitting: bool) -> None:
        """Show the large-file overlay state."""
        overlay = self.controller.ui_controller.overlay
        overlay.set_large_file_info(file_size_mb)

        if is_splitting:
            overlay.show_at_cursor(overlay.STATE_LARGE_FILE_SPLITTING)
        else:
            overlay.show_at_cursor(overlay.STATE_LARGE_FILE_PROCESSING)

    def _submit_transcription_job(self, audio_path: str) -> None:
        backend = self.controller.current_backend
        if not backend.is_available() and getattr(backend, "is_model_missing", False):
            # Trigger the consent/download flow, but never transcribe with a
            # model the user has not approved downloading.
            self.controller.ensure_local_model_available()
            raise Exception(
                "Модель Whisper ещё не установлена — разрешите загрузку "
                "и повторите попытку"
            )

        if not self._claim_transcription_job(audio_path):
            self._reject_duplicate_transcription()
            return
        self.controller.ui_controller.set_transcription_state(
            "processing", audio_path
        )

        try:
            validate_audio_source = getattr(
                audio_processor,
                "validate_audio_source",
                None,
            )
            if callable(validate_audio_source):
                validate_audio_source(audio_path)
            needs_splitting, file_size_mb = audio_processor.check_file_size(
                audio_path
            )
            should_split = (
                needs_splitting
                and self.controller.current_backend.requires_file_splitting
            )

            if should_split:
                logger.info(
                    f"Large file ({file_size_mb:.2f} MB), backend requires splitting"
                )
                self.show_large_file_overlay(file_size_mb, is_splitting=True)
                self.controller.status_update.emit(
                    f"Подготовка большого файла: {file_size_mb:.1f} МБ…"
                )
                self.controller.executor.submit(
                    self.transcribe_large_audio_file, audio_path
                )
            elif needs_splitting:
                logger.info(
                    f"Large file ({file_size_mb:.2f} MB), processing without splitting"
                )
                self.show_large_file_overlay(file_size_mb, is_splitting=False)
                self.controller.status_update.emit(
                    f"Обработка большого файла: {file_size_mb:.1f} МБ…"
                )
                self.controller.executor.submit(
                    self.transcribe_audio_file, audio_path
                )
            else:
                self.controller.executor.submit(
                    self.transcribe_audio_file, audio_path
                )
        except Exception:
            self._release_transcription_job()
            raise
