"""Regression tests for the simplified, task-oriented settings layout."""

import os
from unittest.mock import patch

from PyQt6.QtTest import QSignalSpy, QTest
from PyQt6.QtWidgets import QApplication, QLabel

from services.history_manager import history_manager
from services.settings import SettingsKey, settings_manager
from ui_qt.dialogs.settings_dialog import SettingsDialog


APP = QApplication.instance() or QApplication([])


def test_settings_are_grouped_by_user_task():
    dialog = SettingsDialog()

    assert [
        dialog.tabs.tabText(index)
        for index in range(dialog.tabs.count())
    ] == [
        "Запись",
        "Расшифровка",
        "Обработка текста",
        "Приложение",
    ]
    assert dialog._recording_tab_index == 0
    assert dialog._transcription_tab_index == 1
    assert dialog._application_tab_index == 3
    dialog.close()


def test_transcription_language_defaults_to_russian_and_offers_english():
    with patch.object(settings_manager, "load_all_settings", return_value={}):
        dialog = SettingsDialog()

    assert dialog.transcription_language_combo.currentData() == "ru"
    assert [
        dialog.transcription_language_combo.itemText(index)
        for index in range(dialog.transcription_language_combo.count())
    ] == ["Русский", "English"]
    assert [
        dialog.transcription_language_combo.itemData(index)
        for index in range(dialog.transcription_language_combo.count())
    ] == ["ru", "en"]
    dialog.close()


def test_transcription_device_choice_is_visible_and_checks_nvidia():
    with patch(
        "ui_qt.dialogs.settings_dialog.get_cuda_device_count",
        return_value=0,
    ):
        dialog = SettingsDialog()

    assert [
        dialog.whisper_device_combo.itemText(index)
        for index in range(dialog.whisper_device_combo.count())
    ] == ["Авто (рекомендуется)", "NVIDIA GPU", "CPU"]
    assert [
        dialog.whisper_device_combo.itemData(index)
        for index in range(dialog.whisper_device_combo.count())
    ] == ["auto", "cuda", "cpu"]
    cuda_index = dialog.whisper_device_combo.findData("cuda")
    assert not dialog.whisper_device_combo.model().item(cuda_index).isEnabled()
    assert "не обнаружена" in dialog.whisper_device_info.text().lower()
    dialog.close()


def test_transcription_device_is_saved_immediately_and_requests_reload():
    saved = {}
    initial = {
        SettingsKey.WHISPER_DEVICE: "auto",
        SettingsKey.WHISPER_COMPUTE_TYPE: "float16",
    }
    with patch(
        "ui_qt.dialogs.settings_dialog.get_cuda_device_count",
        return_value=1,
    ), patch.object(
        settings_manager,
        "load_all_settings",
        return_value=initial,
    ), patch.object(
        settings_manager,
        "save_all_settings",
        side_effect=lambda values: saved.update(values),
    ), patch.object(
        history_manager,
        "set_recordings_folder",
        return_value=0,
    ), patch.object(
        history_manager,
        "set_max_recordings",
    ), patch(
        "ui_qt.dialogs.settings_dialog.AudioRecorder.get_input_devices",
        return_value=[],
    ):
        dialog = SettingsDialog()
        dialog.set_embedded_mode()
        changed = QSignalSpy(dialog.settings_changed)

        dialog.whisper_device_combo.setCurrentIndex(
            dialog.whisper_device_combo.findData("cpu")
        )
        QTest.qWait(350)

    assert saved[SettingsKey.WHISPER_DEVICE] == "cpu"
    assert saved[SettingsKey.WHISPER_COMPUTE_TYPE] == "auto"
    assert changed[0][0]["_whisper_device_changed"] is True
    dialog.close()


def test_nonfunctional_duplicate_controls_are_not_exposed():
    dialog = SettingsDialog()

    assert not hasattr(dialog, "max_size_spinbox")
    assert not hasattr(dialog, "logging_check")
    assert not hasattr(dialog, "sample_rate_combo")
    assert not hasattr(dialog, "channels_combo")
    assert not hasattr(dialog, "threshold_slider")
    assert not hasattr(dialog, "cancel_btn")
    assert dialog.check_updates_button.text() == "Проверить обновления"
    dialog.close()


def test_transcription_tab_hides_advanced_delivery_and_preview_controls():
    dialog = SettingsDialog()
    labels = [label.text() for label in dialog.findChildren(QLabel)]

    assert "После расшифровки" not in labels
    assert "Текст во время записи" not in labels
    assert "Размер текста предпросмотра:" not in labels
    dialog.close()


def test_recording_retention_avoids_duplicate_copy_and_hides_irrelevant_fields():
    dialog = SettingsDialog()
    dialog.recording_retention_combo.setCurrentIndex(
        dialog.recording_retention_combo.findData("keep_all")
    )

    labels = [label.text() for label in dialog.findChildren(QLabel)]
    assert "Хранить записи" not in labels
    assert dialog.recording_retention_combo.accessibleName() == "Хранение записей"
    assert dialog.max_recordings_label.isHidden()
    assert dialog.max_recordings_spinbox.isHidden()
    assert dialog.retention_info.isHidden()

    dialog.recording_retention_combo.setCurrentIndex(
        dialog.recording_retention_combo.findData("custom")
    )

    assert not dialog.max_recordings_label.isHidden()
    assert not dialog.max_recordings_spinbox.isHidden()
    assert not dialog.retention_info.isHidden()
    dialog.close()


def test_update_check_button_emits_request():
    dialog = SettingsDialog()
    requests = QSignalSpy(dialog.check_updates_requested)

    dialog.check_updates_button.click()

    assert len(requests) == 1
    dialog.close()


def test_embedded_settings_apply_without_a_save_action():
    dialog = SettingsDialog()
    dialog.set_embedded_mode()

    assert dialog.save_bar.isHidden()

    dialog.minimize_tray_check.toggle()
    assert dialog.save_bar.isHidden()
    dialog.close()


def test_transcription_language_is_saved_immediately():
    saved = {}
    with patch.object(
        settings_manager,
        "load_all_settings",
        return_value={SettingsKey.TRANSCRIPTION_LANGUAGE: "ru"},
    ), patch.object(
        settings_manager,
        "save_all_settings",
        side_effect=lambda values: saved.update(values),
    ), patch.object(
        history_manager,
        "set_recordings_folder",
        return_value=0,
    ), patch.object(
        history_manager,
        "set_max_recordings",
    ), patch(
        "ui_qt.dialogs.settings_dialog.AudioRecorder.get_input_devices",
        return_value=[],
    ):
        dialog = SettingsDialog()
        dialog.set_embedded_mode()
        english_index = dialog.transcription_language_combo.findData("en")

        dialog.transcription_language_combo.setCurrentIndex(english_index)
        QTest.qWait(350)

    assert saved[SettingsKey.TRANSCRIPTION_LANGUAGE] == "en"
    assert dialog.save_bar.isHidden()
    dialog.close()


def test_saving_a_folder_applies_it_to_the_live_library(tmp_path):
    selected = tmp_path / "Новая папка"
    saved = {}

    with patch.object(
        settings_manager,
        "load_all_settings",
        side_effect=lambda: {},
    ), patch.object(
        settings_manager,
        "save_all_settings",
        side_effect=lambda values: saved.update(values),
    ), patch.object(
        settings_manager,
        "load_hf_access_policy",
        return_value="ask",
    ), patch.object(
        history_manager,
        "set_recordings_folder",
        return_value=3,
    ) as set_folder, patch.object(
        history_manager,
        "set_max_recordings",
    ), patch(
        "ui_qt.dialogs.settings_dialog.AudioRecorder.get_input_devices",
        return_value=[],
    ):
        dialog = SettingsDialog()
        dialog.set_embedded_mode()
        changed = QSignalSpy(dialog.settings_changed)
        dialog.recordings_folder_edit.setText(os.fspath(selected))

        assert dialog.save_bar.isHidden()
        QTest.qWait(350)

    expected = dialog._normalized_folder(os.fspath(selected))
    set_folder.assert_called_once_with(expected)
    assert saved["recordings_folder"] == expected
    assert saved[SettingsKey.TRANSCRIPTION_LANGUAGE] == "ru"
    assert changed[0][0]["_recordings_folder_changed"] is True
    assert changed[0][0]["_recordings_found"] == 3
    assert dialog.save_bar.isHidden()
    dialog.close()
