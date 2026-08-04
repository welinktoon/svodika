"""Meeting-folder discovery and local capture audio tests."""

import os
import tempfile
import time
import types
import wave
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import numpy as np

from services.history_manager import (
    HistoryManager,
    NO_SPEECH_TRANSCRIPT,
    _has_transcript_content,
)
from services.screen_recorder import ScreenRecorder, _process_is_alive


def _write_wav(path: Path, channels: int, frames: int = 1600) -> None:
    samples = (
        np.sin(np.arange(frames) * 0.08) * 4000
    ).astype(np.int16)
    if channels == 2:
        samples = np.repeat(samples[:, None], 2, axis=1)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(samples.tobytes())


def test_waveform_analysis_uses_real_audio_amplitude():
    from ui_qt.widgets.voice_notes_workspace import VoiceNotesWorkspace

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "waveform.wav"
        quiet = np.sin(np.arange(8000) * 0.08) * 500
        loud = np.sin(np.arange(8000) * 0.08) * 12000
        samples = np.concatenate((quiet, loud)).astype(np.int16)
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16000)
            output.writeframes(samples.tobytes())

        levels = VoiceNotesWorkspace._media_waveform(str(path), 24)

        assert len(levels) == 24
        assert max(levels[:10]) < min(levels[14:])


def test_new_meeting_files_use_readable_current_date_and_shared_stem():
    with tempfile.TemporaryDirectory() as directory:
        manager = HistoryManager(
            recordings_folder=directory,
            max_recordings=None,
        )
        moment = datetime(2026, 7, 28, 17, 30, 5)

        video = Path(manager.new_meeting_path(".mp4", moment))
        audio = video.with_suffix(".wav")
        transcript = video.with_suffix(".txt")

        assert video.name == "Встреча 28.07.2026 17-30-05.mp4"
        assert audio.name == "Встреча 28.07.2026 17-30-05.wav"
        assert transcript.name == "Встреча 28.07.2026 17-30-05.txt"


def test_new_meeting_path_does_not_overwrite_same_second():
    with tempfile.TemporaryDirectory() as directory:
        manager = HistoryManager(
            recordings_folder=directory,
            max_recordings=None,
        )
        moment = datetime(2026, 7, 28, 17, 30, 5)
        first = Path(manager.new_meeting_path(".wav", moment))
        first.write_bytes(b"existing")

        second = Path(manager.new_meeting_path(".wav", moment))

        assert second.name == "Встреча 28.07.2026 17-30-05 (2).wav"


def test_screen_audio_watchdog_recognizes_live_and_missing_parent():
    assert _process_is_alive(os.getpid())
    assert not _process_is_alive(2_147_483_647)


def test_transcript_metadata_without_speech_is_not_marked_complete():
    assert not _has_transcript_content(
        "Исходник: Встреча 28.07.2026 17-49-36.wav\n"
        "Модель: local_whisper (small | cuda)\n"
    )
    assert _has_transcript_content(
        "Исходник: Встреча.wav\nМодель: small\n\nТекст встречи."
    )


def test_no_speech_marker_counts_as_a_finished_transcription():
    assert _has_transcript_content(NO_SPEECH_TRANSCRIPT)


def test_meeting_bundle_includes_all_media_and_transcript_versions():
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        audio = folder / "meeting_20260728_191000.wav"
        video = folder / "meeting_20260728_191000.mp4"
        raw = folder / "meeting_20260728_191000.txt"
        codex = folder / "meeting_20260728_191000.codex.md"
        unrelated = folder / "meeting_20260728_191200.wav"
        _write_wav(audio, 1)
        video.write_bytes(b"video")
        raw.write_text(
            f"Source: {audio.name}\n\nRaw transcript.",
            encoding="utf-8",
        )
        codex.write_text(
            f"Source: {audio.name}\n\nStructured transcript.",
            encoding="utf-8",
        )
        _write_wav(unrelated, 1)
        manager = HistoryManager(
            recordings_folder=directory,
            max_recordings=None,
        )

        bundle = set(manager.get_meeting_bundle_paths(str(audio)))

        assert bundle == {
            str(audio),
            str(video),
            str(raw),
            str(codex),
        }
        assert str(unrelated) not in bundle


def test_move_meeting_to_trash_sends_whole_bundle_in_one_operation():
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        audio = folder / "meeting_20260728_200000.wav"
        video = folder / "meeting_20260728_200000.mp4"
        transcript = folder / "meeting_20260728_200000.txt"
        _write_wav(audio, 1)
        video.write_bytes(b"video")
        transcript.write_text(
            f"Source: {audio.name}\n\nTranscript.",
            encoding="utf-8",
        )
        manager = HistoryManager(
            recordings_folder=directory,
            max_recordings=None,
        )

        with patch(
            "services.history_manager._move_paths_to_trash"
        ) as move_to_trash, patch.object(
            manager,
            "get_history",
            return_value=[],
        ):
            moved = manager.move_meeting_to_trash(str(audio))

        assert set(moved) == {str(audio), str(video), str(transcript)}
        assert set(move_to_trash.call_args.args[0]) == set(moved)


def test_history_manager_removes_only_stale_system_audio_temporary_files():
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        stale = folder / "Встреча.system.wav"
        recent = folder / "Текущая.system.wav"
        ordinary = folder / "Обычная.wav"
        stale.write_bytes(b"stale")
        recent.write_bytes(b"recent")
        ordinary.write_bytes(b"keep")
        old = time.time() - 120
        os.utime(stale, (old, old))

        HistoryManager(recordings_folder=directory, max_recordings=None)

        assert not stale.exists()
        assert recent.exists()
        assert ordinary.exists()


def test_history_manager_removes_only_abandoned_app_screen_capture():
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        abandoned = folder / "Встреча 28.07.2026 17-55-19.mp4"
        paired = folder / "Встреча 28.07.2026 17-56-19.mp4"
        paired_audio = paired.with_suffix(".wav")
        ordinary = folder / "Старое видео.mp4"
        abandoned.write_bytes(b"tiny")
        paired.write_bytes(b"tiny")
        paired_audio.write_bytes(b"audio")
        ordinary.write_bytes(b"tiny")
        old = time.time() - 120
        for path in (abandoned, paired, paired_audio, ordinary):
            os.utime(path, (old, old))

        HistoryManager(recordings_folder=directory, max_recordings=None)

        assert not abandoned.exists()
        assert paired.exists()
        assert paired_audio.exists()
        assert ordinary.exists()


def test_media_scan_finds_audio_video_and_groups_sidecars():
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        nested = folder / "Архив"
        nested.mkdir()
        _write_wav(folder / "Командная встреча.wav", 1)
        (folder / "Командная встреча.mp4").write_bytes(b"video")
        (folder / "Отдельная запись.WEBM").write_bytes(b"video")
        (nested / "Созвон.mp3").write_bytes(b"audio")
        (folder / "ignore.txt").write_text("text", encoding="utf-8")

        manager = HistoryManager(
            recordings_folder=directory,
            max_recordings=None,
        )
        media = manager.get_media_files()

        assert len(media) == 3
        paired = next(
            item for item in media
            if item.filename == "Командная встреча.mp4"
        )
        assert paired.media_type == "video"
        assert paired.audio_path.endswith("Командная встреча.wav")
        assert paired.transcription_path == paired.audio_path
        assert paired.video_path.endswith("Командная встреча.mp4")
        assert any(
            item.filename == "Отдельная запись.WEBM"
            and item.transcription_path == item.file_path
            for item in media
        )
        assert any(item.filename == "Созвон.mp3" for item in media)


def test_switching_recordings_folder_scans_existing_meetings_immediately():
    with tempfile.TemporaryDirectory() as first_directory, tempfile.TemporaryDirectory() as second_directory:
        second = Path(second_directory)
        nested = second / "Архив"
        nested.mkdir()
        _write_wav(second / "Планёрка.wav", 1)
        (nested / "Демонстрация.webm").write_bytes(b"video")
        manager = HistoryManager(
            recordings_folder=first_directory,
            max_recordings=None,
        )

        meetings_found = manager.set_recordings_folder(second_directory)

        assert meetings_found == 2
        assert manager.recordings_folder == os.path.abspath(second_directory)
        assert {
            meeting.filename for meeting in manager.get_media_files()
        } == {"Планёрка.wav", "Демонстрация.webm"}


def test_library_snapshot_detects_external_file_and_transcript_changes():
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        audio = folder / "Исходное имя.wav"
        transcript = folder / "Исходное имя.txt"
        _write_wav(audio, 1)
        transcript.write_text("Первый текст", encoding="utf-8")
        manager = HistoryManager(
            recordings_folder=directory,
            max_recordings=None,
        )

        before = manager.get_library_snapshot()
        renamed = folder / "Новое имя.wav"
        audio.rename(renamed)
        transcript.write_text("Обновлённый текст", encoding="utf-8")
        after = manager.get_library_snapshot()

        assert os.fspath(audio) in before
        assert os.fspath(audio) not in after
        assert os.fspath(renamed) in after
        assert before[os.fspath(transcript)] != after[os.fspath(transcript)]


def test_external_media_rename_repairs_database_reference():
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        original = folder / "Исходное имя.wav"
        _write_wav(original, 1)
        manager = HistoryManager(
            recordings_folder=directory,
            max_recordings=None,
        )
        before = manager.get_library_snapshot()
        renamed = folder / "Новое имя.wav"
        original.rename(renamed)
        after = manager.get_library_snapshot()
        entry = types.SimpleNamespace(
            id="entry-id",
            audio_file=original.name,
        )

        with patch.object(
            manager,
            "get_history",
            return_value=[entry],
        ), patch(
            "services.history_manager.db.update_history_audio_file",
            return_value=True,
        ) as update_audio_file:
            renames = manager.reconcile_external_renames(before, after)

        assert renames == {os.fspath(original): os.fspath(renamed)}
        update_audio_file.assert_called_once_with(
            "entry-id",
            renamed.name,
            file_size=after[os.fspath(renamed)][0],
        )


def test_media_scan_attaches_the_best_existing_transcript():
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        video = folder / "Команда 16.07.26 13-00-49 — запись.webm"
        video.write_bytes(b"video")
        raw = folder / "Команда 16.07.26 13-00-49 — расшифровка raw.txt"
        raw.write_text("[00:00] черновик", encoding="utf-8")
        edited = (
            folder
            / "Команда 16.07.26 13-00-49 — расшифровка исправленная.md"
        )
        edited.write_text(
            "# Команда\n\n"
            "Источник: `Команда 16.07.26 13-00-49 — запись.webm`\n\n"
            "Исправленный текст встречи.",
            encoding="utf-8",
        )

        manager = HistoryManager(
            recordings_folder=directory,
            max_recordings=None,
        )
        meeting = manager.get_media_files()[0]

        assert meeting.transcript_path == os.fspath(edited)
        assert "Исправленный текст встречи" in manager.read_transcript(
            meeting.transcript_path
        )


def test_media_scan_prefers_codex_version_and_keeps_raw_version():
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        audio = folder / "Встреча 28.07.2026 19-10-00.wav"
        _write_wav(audio, 1)
        manager = HistoryManager(recordings_folder=directory, max_recordings=None)

        raw_path = manager.save_transcript_version(
            os.fspath(audio),
            "сырой текст встречи",
            model="Whisper small",
        )
        codex_path = manager.save_transcript_version(
            os.fspath(audio),
            "# Встреча\n\nАккуратный текст встречи.",
            model="full",
            variant="codex",
        )

        meeting = manager.get_media_files()[0]

        assert raw_path and Path(raw_path).exists()
        assert codex_path and Path(codex_path).exists()
        assert meeting.transcript_path == codex_path
        assert "Аккуратный текст встречи" in manager.read_transcript(
            meeting.transcript_path
        )


def test_saving_codex_sidecar_updates_existing_history_entry():
    with tempfile.TemporaryDirectory() as directory:
        audio = Path(directory) / "Планёрка.wav"
        _write_wav(audio, 1)
        manager = HistoryManager(recordings_folder=directory, max_recordings=None)
        entry = types.SimpleNamespace(
            raw_text="старый исходник",
            text="старые итоги",
        )

        with patch.object(
            manager,
            "get_entry_by_id",
            return_value=entry,
        ), patch(
            "services.history_manager.db.update_history_entry_cleanup",
            return_value=True,
        ) as update_cleanup:
            path = manager.save_transcript_version(
                os.fspath(audio),
                "новые полные итоги",
                model="full",
                variant="codex",
                history_entry_id="entry-id",
                original_text="актуальный исходник",
            )

        assert path and Path(path).exists()
        update_cleanup.assert_called_once_with(
            "entry-id",
            "новые полные итоги",
            "актуальный исходник",
            "codex",
            "full",
        )


def test_saving_codex_sidecar_for_filesystem_meeting_skips_database_update():
    """A discovered media path is a UI id, not a missing database primary key."""
    with tempfile.TemporaryDirectory() as directory:
        audio = Path(directory) / "Внешняя планёрка.webm"
        audio.write_bytes(b"webm")
        manager = HistoryManager(recordings_folder=directory, max_recordings=None)

        with patch.object(
            manager,
            "get_entry_by_id",
            return_value=None,
        ), patch(
            "services.history_manager.db.update_history_entry_cleanup",
        ) as update_cleanup:
            path = manager.save_transcript_version(
                os.fspath(audio),
                "# Итоги\n\nУлучшенный текст.",
                model="full",
                variant="codex",
                history_entry_id=os.fspath(audio),
                original_text="Исходный текст.",
            )

        assert path and Path(path).exists()
        assert "Улучшенный текст" in Path(path).read_text(encoding="utf-8")
        update_cleanup.assert_not_called()


def test_media_scan_groups_recording_and_recovered_audio_names():
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        video = folder / "Планёрка 02.07.26 14-45-03 — запись.webm"
        audio = (
            folder
            / "Планёрка 02.07.26 14-45-03 — восстановленное аудио.wav"
        )
        transcript = (
            folder
            / "Планёрка 02.07.26 14-45-03 — расшифровка.md"
        )
        video.write_bytes(b"video")
        _write_wav(audio, 1)
        transcript.write_text("# Планёрка\n\nТекст встречи.", encoding="utf-8")

        manager = HistoryManager(
            recordings_folder=directory,
            max_recordings=None,
        )
        media = manager.get_media_files()

        assert len(media) == 1
        assert media[0].video_path == os.fspath(video)
        assert media[0].audio_path == os.fspath(audio)
        assert media[0].transcript_path == os.fspath(transcript)


def test_similar_meetings_on_the_same_date_do_not_share_transcripts():
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        first = folder / "Встреча 07.07.26 14-04-46 — запись.webm"
        second = folder / "Встреча 07.07.26 14-12-21 — запись.webm"
        transcript = folder / "Встреча 07.07.26 14-12-21 — расшифровка.md"
        first.write_bytes(b"video")
        second.write_bytes(b"video")
        transcript.write_text("# Вторая встреча\n\nТекст.", encoding="utf-8")

        manager = HistoryManager(
            recordings_folder=directory,
            max_recordings=None,
        )
        media = manager.get_media_files()

        first_item = next(
            item for item in media if "14-04-46" in item.filename
        )
        second_item = next(
            item for item in media if "14-12-21" in item.filename
        )
        assert first_item.transcript_path is None
        assert second_item.transcript_path == os.fspath(transcript)


def test_compact_timestamp_meetings_do_not_share_transcripts():
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        first = folder / "meeting_20260728_170407.mp4"
        second = folder / "meeting_20260728_170709.mp4"
        transcript = folder / "meeting_20260728_170709.txt"
        first.write_bytes(b"video")
        second.write_bytes(b"video")
        transcript.write_text(
            "Исходник: meeting_20260728_170709.wav\n\nТекст.",
            encoding="utf-8",
        )

        manager = HistoryManager(
            recordings_folder=directory,
            max_recordings=None,
        )
        media = manager.get_media_files()

        first_item = next(item for item in media if "170407" in item.filename)
        second_item = next(item for item in media if "170709" in item.filename)
        assert first_item.transcript_path is None
        assert second_item.transcript_path == os.fspath(transcript)


def test_meeting_audio_mixes_microphone_and_computer_in_chunks():
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        microphone = folder / "microphone.wav"
        output = folder / "meeting.wav"
        recorder = ScreenRecorder(
            folder / "meeting.mp4",
            audio_sample_rate=16000,
        )
        _write_wav(microphone, 1, frames=3200)
        _write_wav(recorder.system_audio_file, 2, frames=2400)

        result = recorder.build_meeting_audio(microphone, output)

        assert result == os.fspath(output)
        with wave.open(str(output), "rb") as mixed:
            assert mixed.getnchannels() == 2
            assert mixed.getframerate() == 16000
            assert mixed.getnframes() == 3200
            assert mixed.readframes(100)


def test_screen_recorder_uses_configured_video_quality():
    recorder = ScreenRecorder("meeting.mp4", fps=24, crf=20)

    assert recorder.fps == 24
    assert recorder.crf == 20


def test_meeting_audio_falls_back_to_microphone_without_loopback():
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        microphone = folder / "microphone.wav"
        output = folder / "meeting.wav"
        _write_wav(microphone, 1)
        recorder = ScreenRecorder(folder / "meeting.mp4")
        recorder.system_audio_error = "loopback unavailable"

        recorder.build_meeting_audio(microphone, output)

        assert output.read_bytes() == microphone.read_bytes()
