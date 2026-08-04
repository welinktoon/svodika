"""Regression tests for the meeting transcription action states."""

import os
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QListWidgetItem
from PyQt6.QtWidgets import QApplication, QMessageBox

from services.history_manager import history_manager
from ui_qt.ui_controller import UIController
from ui_qt.widgets.voice_notes_workspace import VoiceNotesWorkspace


class TestTranscriptionButtonStates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        history_patches = (
            patch.object(history_manager, "get_history", return_value=[]),
            patch.object(history_manager, "get_media_files", return_value=[]),
        )
        self._patches = history_patches
        for history_patch in self._patches:
            history_patch.start()
        self.workspace = VoiceNotesWorkspace()
        self.temp_file = tempfile.NamedTemporaryFile(
            suffix=".wav", delete=False
        )
        self.temp_file.write(b"audio")
        self.temp_file.close()
        self.workspace._selected_audio_path = self.temp_file.name
        self.workspace._selected_media_path = self.temp_file.name

    def tearDown(self):
        self.workspace.close()
        for history_patch in reversed(self._patches):
            history_patch.stop()
        os.unlink(self.temp_file.name)

    def test_repeated_request_is_ignored_while_processing(self):
        requests = []
        self.workspace.transcribe_requested.connect(requests.append)

        self.workspace._request_transcription()
        self.workspace._request_transcription()

        self.assertEqual(requests, [self.temp_file.name])
        self.assertFalse(self.workspace.transcribe.isEnabled())
        self.assertEqual(self.workspace.transcribe.text(), "Расшифровка…")
        self.assertEqual(
            self.workspace.transcribe.property("state"), "busy"
        )

    def test_codex_cleaning_has_small_status_and_cancel_action(self):
        cancellations = []
        self.workspace.cancel_requested.connect(
            lambda: cancellations.append(True)
        )

        self.workspace.set_transcription_state(
            "cleaning", self.temp_file.name
        )

        self.assertTrue(self.workspace.record_actions.isHidden())
        self.assertFalse(self.workspace.processing_actions.isHidden())
        self.assertEqual(
            self.workspace.processing_status.text(),
            "Обработка текста в Codex…",
        )
        self.assertFalse(self.workspace.transcribe.isEnabled())

        self.workspace.cancel_processing.click()
        self.assertEqual(cancellations, [True])
        self.assertIn(
            "QFrame#processingActions { background:transparent; border:0;",
            self.workspace.styleSheet(),
        )

    def test_popup_hover_does_not_use_the_strong_selection_color(self):
        """Menus and their open toolbar buttons use the neutral hover surface."""
        hover = "#1d2939" if self.workspace.dark else "#edf3fa"
        style = self.workspace.styleSheet()

        self.assertIn(
            "QPushButton#codexImproveButton:open "
            f"{{ background:{hover}; }}",
            style,
        )
        self.assertIn(
            f"QMenu::item:selected {{ background:{hover};",
            style,
        )
        self.assertIn("border-radius:9px; padding:8px 10px;", style)
        self.assertIn(
            "QMenu::indicator,QMenu::icon { position:relative; left:8px; }",
            style,
        )
        self.assertIn(
            "QMenu::item { border-radius:6px; margin:1px 0; "
            "padding:9px 18px 9px 18px; }",
            style,
        )

    def test_action_menu_is_right_aligned_with_a_six_pixel_gap(self):
        """Top-right popup menus open inward instead of beyond the window."""
        self.workspace.resize(1200, 800)
        self.workspace.show()
        self.workspace.codex_improve_button.show()
        self.app.processEvents()

        button = self.workspace.codex_improve_button
        menu = self.workspace.codex_improve_menu
        self.workspace._position_action_menu(button, menu)

        button_bottom_right = button.mapToGlobal(button.rect().bottomRight())
        expected_right = min(
            button_bottom_right.x(),
            button.screen().availableGeometry().right() - 8,
        )
        self.assertEqual(menu.geometry().right(), expected_right)
        self.assertEqual(menu.geometry().top(), button_bottom_right.y() + 7)

    def test_selecting_meeting_loads_duration_before_play(self):
        item = QListWidgetItem("Тестовая встреча\n29.07.2026")
        item.setData(
            Qt.ItemDataRole.UserRole,
            {
                "audio": self.temp_file.name,
                "media": self.temp_file.name,
                "text": "",
                "duration": 0.0,
            },
        )
        with patch.object(
            self.workspace, "_request_media_duration"
        ) as request_duration:
            self.workspace.notes.addItem(item)
            self.workspace.notes.setCurrentItem(item)

        request_duration.assert_called_once_with(self.temp_file.name)
        self.assertFalse(self.workspace._media_player.source().toLocalFile())
        self.assertEqual(self.workspace.duration_label.text(), "…")

        self.workspace._apply_probed_media_duration(
            self.temp_file.name, 125.0
        )

        self.assertEqual(self.workspace.duration_label.text(), "02:05")
        self.assertEqual(
            item.data(Qt.ItemDataRole.UserRole)["duration"], 125.0
        )

    def test_wav_duration_is_shown_immediately_without_locking_the_player(self):
        wav_path = Path(self.temp_file.name).with_name("duration-test.wav")
        with wave.open(os.fspath(wav_path), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16_000)
            audio.writeframes(b"\0\0" * 24_000)
        item = QListWidgetItem(
            "Запись с длительностью\n29.07.2026  ·  4,2 МБ  ·  Расшифровано"
        )
        item.setData(
            Qt.ItemDataRole.UserRole,
            {
                "audio": os.fspath(wav_path),
                "media": os.fspath(wav_path),
                "text": "",
                "duration": 0.0,
            },
        )
        try:
            self.workspace.notes.addItem(item)
            self.workspace.notes.setCurrentItem(item)

            self.assertEqual(self.workspace.duration_label.text(), "00:01")
            self.assertFalse(self.workspace._media_player.source().toLocalFile())
            self.assertEqual(
                item.data(Qt.ItemDataRole.UserRole)["duration"], 1.5
            )
            self.assertIn("29.07.2026  ·  00:01  ·  4,2 МБ", item.text())
        finally:
            wav_path.unlink(missing_ok=True)

    def test_error_state_enables_one_clear_retry_action(self):
        self.workspace.set_transcription_state(
            "error", self.temp_file.name, "Модель недоступна"
        )

        self.assertTrue(self.workspace.transcribe.isEnabled())
        self.assertEqual(self.workspace.transcribe.text(), "Повторить")
        self.assertEqual(
            self.workspace.transcribe.property("state"), "error"
        )
        self.assertEqual(
            self.workspace.empty_title.text(), "Не удалось расшифровать"
        )
        self.assertEqual(
            self.workspace.empty_desc.text(), "Модель недоступна"
        )

        requests = []
        self.workspace.transcribe_requested.connect(requests.append)
        self.workspace._request_transcription()

        self.assertEqual(requests, [self.temp_file.name])
        self.assertFalse(self.workspace.transcribe.isEnabled())

    def test_error_message_keeps_transcription_actions_in_one_column(self):
        self.workspace.set_transcription_state(
            "error",
            self.temp_file.name,
            "Failed to process audio: Файл повреждён или запись не была "
            "корректно завершена. Не удалось прочитать аудиодорожку",
        )

        self.assertTrue(self.workspace.empty_desc.wordWrap())
        self.assertEqual(self.workspace.empty_desc.width(), 354)
        self.assertEqual(self.workspace.model.width(), 354)
        self.assertEqual(self.workspace.transcribe.width(), 354)
        self.assertEqual(self.workspace.folder_button.width(), 354)

    def test_recording_replaces_start_with_explicit_stop_action(self):
        stops = []
        self.workspace.stop_requested.connect(lambda: stops.append(True))

        self.assertFalse(self.workspace.record.isHidden())
        self.assertFalse(self.workspace.stop_record.isHidden())
        self.assertTrue(self.workspace.record.isEnabled())
        self.assertFalse(self.workspace.stop_record.isEnabled())

        self.workspace.set_recording(True)

        self.assertFalse(self.workspace.record.isEnabled())
        self.assertTrue(self.workspace.stop_record.isEnabled())
        self.assertEqual(self.workspace.stop_record.text(), "Остановить")
        self.assertFalse(self.workspace.screen.isEnabled())
        self.assertFalse(self.workspace.transcribe.isEnabled())

        self.workspace.stop_record.click()
        self.assertEqual(stops, [True])

        self.workspace.set_recording(False)
        self.assertTrue(self.workspace.record.isEnabled())
        self.assertFalse(self.workspace.stop_record.isEnabled())
        self.assertEqual(self.workspace.record.text(), "Записать встречу")
        self.assertTrue(self.workspace.screen.isEnabled())

    def test_controller_refreshes_workspace_when_legacy_flag_changed_first(self):
        """The visible Stop action must not depend on the hidden tab's flag."""
        controller = UIController.__new__(UIController)
        controller.is_recording = False
        controller._transcription_source_tab = -1
        controller.main_window = type(
            "Window",
            (),
            {
                "is_recording": True,
                "updates": 0,
                "_update_recording_state": lambda window: setattr(
                    window, "updates", window.updates + 1
                ),
            },
        )()
        controller.on_record_start = lambda: None

        controller.start_recording()

        self.assertTrue(controller.is_recording)
        self.assertEqual(controller.main_window.updates, 1)

    def test_search_filters_and_restores_meeting_rows(self):
        first = QListWidgetItem("Планёрка\n28.07.2026")
        second = QListWidgetItem("Обсуждение дизайна\n27.07.2026")
        self.workspace.notes.addItem(first)
        self.workspace.notes.addItem(second)

        self.workspace.search.setText("дизайн")

        self.assertTrue(first.isHidden())
        self.assertFalse(second.isHidden())

        self.workspace.search.clear()

        self.assertFalse(first.isHidden())
        self.assertFalse(second.isHidden())

    def test_search_finds_words_inside_transcripts_and_outlines_matches(self):
        first = QListWidgetItem("Планёрка\n28.07.2026")
        first.setData(
            Qt.ItemDataRole.UserRole,
            {
                "text": "Обсудили квартальный бюджет и БЮДЖЕТ проекта.",
                "transcript_format": ".txt",
            },
        )
        second = QListWidgetItem("Демонстрация\n27.07.2026")
        second.setData(
            Qt.ItemDataRole.UserRole,
            {"text": "Показали новый интерфейс."},
        )
        self.workspace.notes.addItem(first)
        self.workspace.notes.addItem(second)

        self.workspace.search.setText("бюджет")

        self.assertFalse(first.isHidden())
        self.assertTrue(second.isHidden())
        self.assertIs(self.workspace.notes.currentItem(), first)
        highlighted = [
            selection.cursor.selectedText().casefold()
            for selection in self.workspace.transcript.extraSelections()
        ]
        self.assertEqual(highlighted, ["бюджет", "бюджет"])

        self.workspace.search.clear()

        self.assertEqual(self.workspace.transcript.extraSelections(), [])

    def test_search_requires_every_entered_word_across_meeting_content(self):
        item = QListWidgetItem("Статус проекта\n28.07.2026")
        item.setData(
            Qt.ItemDataRole.UserRole,
            {"text": "Согласовали квартальный бюджет."},
        )
        self.workspace.notes.addItem(item)

        self.workspace.search.setText("проект бюджет")
        self.assertFalse(item.isHidden())

        self.workspace.search.setText("проект отпуск")
        self.assertTrue(item.isHidden())

    def test_theme_toggle_is_compact_and_reversible(self):
        changes = []
        self.workspace.theme_changed.connect(changes.append)

        self.workspace.toggle_theme()
        self.assertTrue(self.workspace.dark)
        self.assertEqual(changes[-1], "dark")

        self.workspace.toggle_theme()
        self.assertFalse(self.workspace.dark)
        self.assertEqual(changes[-1], "light")

    def test_open_folder_and_media_use_local_file_urls(self):
        media_path = self.temp_file.name
        self.workspace._selected_media_path = media_path

        with patch.object(
            QDesktopServices,
            "openUrl",
            return_value=True,
        ) as open_url:
            self.workspace._open_recording_folder()
            self.workspace._open_selected_media()

        folder_url = open_url.call_args_list[0].args[0]
        media_url = open_url.call_args_list[1].args[0]
        self.assertEqual(
            os.path.normcase(os.path.normpath(folder_url.toLocalFile())),
            os.path.normcase(os.path.normpath(os.path.dirname(media_path))),
        )
        self.assertEqual(
            os.path.normcase(os.path.normpath(media_url.toLocalFile())),
            os.path.normcase(os.path.normpath(media_path)),
        )


    def test_selected_meeting_can_be_moved_to_trash_after_confirmation(self):
        item = QListWidgetItem("Тестовая встреча\n29.07.2026")
        item.setData(
            Qt.ItemDataRole.UserRole,
            {
                "id": "history-entry-id",
                "audio": self.temp_file.name,
                "media": self.temp_file.name,
                "text": "Текст",
            },
        )
        self.workspace.notes.addItem(item)
        self.workspace.notes.setCurrentItem(item)

        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Yes,
        ), patch.object(
            history_manager,
            "move_meeting_to_trash",
            return_value=(self.temp_file.name,),
        ) as move_to_trash:
            self.workspace.trash_button.click()

        move_to_trash.assert_called_once_with(
            self.temp_file.name,
            "history-entry-id",
        )

    def test_archived_transcript_exposes_permanent_delete_action(self):
        item = QListWidgetItem("Суть встречи · 03.08.2026 18-16\n03.08.2026")
        item.setData(
            Qt.ItemDataRole.UserRole,
            {
                "id": "archived-entry-id",
                "audio": "",
                "media": "",
                "text": "Архивная расшифровка",
                "archived": True,
                "timestamp": "2026-08-03T18:16:00",
            },
        )
        self.workspace.notes.addItem(item)
        self.workspace.notes.setCurrentItem(item)

        self.assertFalse(self.workspace.trash_button.isHidden())
        self.assertIn("архивную", self.workspace.trash_button.toolTip().lower())
        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Yes,
        ), patch.object(
            history_manager,
            "delete_entry",
            return_value=True,
        ) as delete_entry:
            self.workspace.trash_button.click()

        delete_entry.assert_called_once_with(
            "archived-entry-id",
            delete_audio_file=False,
        )

    def test_meeting_title_automatically_includes_safe_date(self):
        title = self.workspace._title_with_meeting_date(
            "Суть встречи",
            "2026-08-03T18:16:00",
        )

        self.assertEqual(title, "Суть встречи · 03.08.2026 18-16")
        self.assertEqual(
            self.workspace._title_with_meeting_date(
                title,
                "2026-08-03T18:16:00",
            ),
            title,
        )

    def test_raw_transcript_offers_manual_codex_improvement(self):
        requests = []
        self.workspace.codex_improve_requested.connect(
            lambda path, text, entry_id, mode: requests.append(
                (path, text, entry_id, mode)
            )
        )
        item = QListWidgetItem("Тестовая встреча\n29.07.2026")
        item.setData(
            Qt.ItemDataRole.UserRole,
            {
                "id": "history-entry-id",
                "audio": self.temp_file.name,
                "media": self.temp_file.name,
                "text": "Обычная расшифровка",
                "enhanced_by_codex": False,
            },
        )

        with patch(
            "ui_qt.widgets.voice_notes_workspace."
            "resolve_codex_cleanup_enabled",
            return_value=True,
        ):
            self.workspace.notes.addItem(item)
            self.workspace.notes.setCurrentItem(item)
            self.workspace._apply_transcription_controls_state()

            self.assertFalse(self.workspace.codex_improve_button.isHidden())
            self.assertTrue(self.workspace.codex_improve_button.isEnabled())
            self.assertEqual(self.workspace.codex_improve_button.text(), "")
            self.assertEqual(
                [
                    action.text()
                    for action in self.workspace.codex_improve_menu.actions()
                ],
                [
                    "Кратко",
                    "Полное",
                    "Полное + оригинальный текст",
                ],
            )
            self.workspace.codex_improve_menu.actions()[0].trigger()

        self.assertEqual(
            requests,
            [(
                self.temp_file.name,
                "Обычная расшифровка",
                "history-entry-id",
                "brief",
            )],
        )

    def test_existing_improved_version_can_be_redone_from_original(self):
        requests = []
        self.workspace.codex_improve_requested.connect(
            lambda path, text, entry_id, mode: requests.append(
                (path, text, entry_id, mode)
            )
        )
        item = QListWidgetItem("Улучшенная встреча\n29.07.2026")
        item.setData(
            Qt.ItemDataRole.UserRole,
            {
                "audio": self.temp_file.name,
                "media": self.temp_file.name,
                "text": "Улучшенная расшифровка",
                "original_text": "Исходная расшифровка",
                "enhanced_by_codex": True,
            },
        )

        self.workspace.notes.addItem(item)
        self.workspace.notes.setCurrentItem(item)

        self.assertFalse(self.workspace.codex_improve_button.isHidden())
        self.workspace.codex_improve_menu.actions()[2].trigger()
        self.assertEqual(
            requests,
            [(
                self.temp_file.name,
                "Исходная расшифровка",
                "",
                "full_with_original",
            )],
        )

    def test_reprocessing_prefers_edited_raw_sidecar_over_codex_output(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            raw = folder / "Тестовая встреча.txt"
            codex = folder / "Тестовая встреча.codex.md"
            raw.write_text("Исправленный вручную исходник", encoding="utf-8")
            codex.write_text("## Итоги\n\nСтарые итоги", encoding="utf-8")
            recording = SimpleNamespace(
                transcription_path=str(folder / "Тестовая встреча.wav"),
                bundle_paths=(str(codex), str(raw)),
            )

            self.assertEqual(
                self.workspace._original_transcript_for_recording(
                    recording,
                    "Старый исходник из базы",
                ),
                "Исправленный вручную исходник",
            )

    def test_empty_database_row_uses_no_speech_sidecar_as_completed(self):
        transcript_path = Path(self.temp_file.name).with_suffix(".txt")
        transcript_path.write_text(
            "Источник: silent.wav\n"
            "Модель: medium\n\n"
            "Нечего расшифровывать: в записи не обнаружена речь.",
            encoding="utf-8",
        )
        entry = SimpleNamespace(
            id="silent-entry",
            audio_file=os.path.basename(self.temp_file.name),
            text="",
            cleanup_provider=None,
            audio_duration=4.0,
            formatted_timestamp="29.07.2026 10:00",
            model="medium",
        )
        try:
            with patch.object(
                history_manager,
                "get_history",
                return_value=[entry],
            ), patch.object(
                history_manager,
                "get_recording_path",
                return_value=self.temp_file.name,
            ), patch.object(
                history_manager,
                "get_media_files",
                return_value=[],
            ):
                self.workspace.refresh_history()
                item = self.workspace.notes.item(0)
                self.workspace.notes.setCurrentItem(item)

            data = item.data(Qt.ItemDataRole.UserRole)
            self.assertEqual(
                data["text"],
                "Нечего расшифровывать: в записи не обнаружена речь.",
            )
            self.assertIn("Речь не обнаружена", item.text())
            self.assertFalse(self.workspace.transcript.isHidden())
            self.assertTrue(self.workspace.empty.isHidden())
            self.assertTrue(
                self.workspace.codex_improve_button.isHidden()
            )
        finally:
            transcript_path.unlink(missing_ok=True)

    def test_combined_library_defaults_to_newest_meeting_first(self):
        os.utime(self.temp_file.name, (1, 1))
        old_entry = SimpleNamespace(
            id="old-db-entry",
            audio_file=os.path.basename(self.temp_file.name),
            text="Старая встреча",
            cleanup_provider=None,
            audio_duration=60.0,
            file_size=100,
            timestamp="2026-07-28T10:00:00",
            formatted_timestamp="28.07.2026 10:00",
            model="medium",
        )
        fresh_media = SimpleNamespace(
            filename="Дейл 29.07.26 12-08-33 — запись.webm",
            timestamp="2026-07-29T12:17:05",
            formatted_timestamp="29.07.2026 12:17",
            formatted_size="36,1 МБ",
            size_bytes=36 * 1024 * 1024,
            file_path="C:/meetings/daily.webm",
            transcription_path="C:/meetings/daily.webm",
            audio_path=None,
            video_path="C:/meetings/daily.webm",
            transcript_path=None,
            media_type="video",
        )

        with patch.object(
            history_manager, "get_history", return_value=[old_entry]
        ), patch.object(
            history_manager,
            "get_recording_path",
            return_value=self.temp_file.name,
        ), patch.object(
            history_manager, "get_media_files", return_value=[fresh_media]
        ):
            self.workspace.refresh_history()

        self.assertEqual(
            self.workspace.sort.currentData(),
            self.workspace.SORT_NEWEST,
        )
        self.assertIn("Дейл 29.07.26", self.workspace.notes.item(0).text())

    def test_sort_is_an_icon_menu_in_the_search_row(self):
        self.assertTrue(self.workspace.sort.isHidden())
        self.assertEqual(self.workspace.search_row.indexOf(self.workspace.search), 0)
        self.assertEqual(
            self.workspace.search_row.indexOf(self.workspace.sort_button),
            1,
        )
        self.assertEqual(len(self.workspace._sort_actions), 4)
        self.workspace.sort.setCurrentIndex(
            self.workspace.sort.findData(self.workspace.SORT_OLDEST)
        )
        self.assertIn("Сначала старые", self.workspace.sort_button.toolTip())
        self.assertTrue(self.workspace._sort_actions[1].isChecked())

    def test_library_can_sort_by_size_and_duration(self):
        for title, size, duration, timestamp in (
            ("Короткая", 500, 30.0, "2026-07-29T12:00:00"),
            ("Длинная", 100, 300.0, "2026-07-28T12:00:00"),
        ):
            item = QListWidgetItem(title)
            item.setData(
                Qt.ItemDataRole.UserRole,
                {
                    "size": size,
                    "duration": duration,
                    "timestamp": timestamp,
                },
            )
            self.workspace.notes.addItem(item)

        self.workspace.sort.setCurrentIndex(
            self.workspace.sort.findData(self.workspace.SORT_SIZE)
        )
        self.assertEqual(self.workspace.notes.item(0).text(), "Короткая")

        self.workspace.sort.setCurrentIndex(
            self.workspace.sort.findData(self.workspace.SORT_DURATION)
        )
        self.assertEqual(self.workspace.notes.item(0).text(), "Длинная")


if __name__ == "__main__":
    unittest.main()
