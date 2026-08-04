"""Tests for the local CPU/NVIDIA engine selection control."""

from unittest.mock import patch

from PyQt6.QtWidgets import QApplication

from services.settings import SettingsKey, settings_manager
from ui_qt.widgets.local_engine_controls import LocalEngineControls


APP = QApplication.instance() or QApplication([])


def _settings():
    return {
        SettingsKey.WHISPER_MODEL: "auto",
        SettingsKey.WHISPER_DEVICE: "auto",
        SettingsKey.WHISPER_COMPUTE_TYPE: "auto",
    }


def test_nvidia_option_is_disabled_when_cuda_is_not_available():
    with (
        patch("ui_qt.widgets.local_engine_controls.get_cuda_device_count", return_value=0),
        patch.object(settings_manager, "load_all_settings", return_value=_settings()),
    ):
        controls = LocalEngineControls()

    cuda_index = controls.device_combo.findData("cuda")
    assert not controls.device_combo.model().item(cuda_index).isEnabled()
    assert "не обнаружена" in controls.availability_label.text().lower()
    controls.close()


def test_nvidia_option_is_available_when_cuda_is_detected():
    with (
        patch("ui_qt.widgets.local_engine_controls.get_cuda_device_count", return_value=1),
        patch.object(settings_manager, "load_all_settings", return_value=_settings()),
    ):
        controls = LocalEngineControls()

    cuda_index = controls.device_combo.findData("cuda")
    assert controls.device_combo.model().item(cuda_index).isEnabled()
    assert "доступна" in controls.availability_label.text().lower()
    controls.close()


def test_device_choice_is_saved_as_backend_value_not_display_text():
    saved = {}
    with (
        patch("ui_qt.widgets.local_engine_controls.get_cuda_device_count", return_value=1),
        patch.object(settings_manager, "load_all_settings", return_value=_settings()),
        patch.object(settings_manager, "save_all_settings", side_effect=saved.update),
    ):
        controls = LocalEngineControls()
        controls.device_combo.setCurrentIndex(controls.device_combo.findData("cpu"))

    assert saved[SettingsKey.WHISPER_DEVICE] == "cpu"
    controls.close()
