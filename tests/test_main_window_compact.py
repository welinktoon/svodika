"""Tests for the main window's compact recording mode."""

import os
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from config import config
from services.settings import SettingsKey, settings_manager
from ui_qt.main_window import MainWindow


class TestMainWindowCompactMode(unittest.TestCase):
    """Exercise compact/full transitions without displaying a real window."""

    @classmethod
    def setUpClass(cls):
        """Create the shared Qt application."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Create a main window with isolated settings access."""
        self.load_settings = patch.object(
            settings_manager,
            "load_all_settings",
            return_value={},
        )
        self.get_setting = patch.object(
            settings_manager,
            "get",
            side_effect=lambda key, default=None: default,
        )
        self.save_setting = patch.object(settings_manager, "save_setting")
        self.load_settings.start()
        self.mock_get_setting = self.get_setting.start()
        self.saved_setting = self.save_setting.start()
        self.window = MainWindow()

    def tearDown(self):
        """Close the window and restore settings methods."""
        self.window._force_quit = True
        self.window.close()
        self.app.processEvents()
        self.save_setting.stop()
        self.get_setting.stop()
        self.load_settings.stop()

    def test_compact_mode_uses_fixed_controller_and_restores_geometry(self):
        """Compact mode swaps the workspace without losing full geometry."""
        self.window.setGeometry(40, 50, 620, 600)
        full_geometry = self.window.geometry()

        self.window.set_compact_mode(True)

        self.assertTrue(self.window._compact_mode)
        self.assertEqual(self.window.size().width(), config.MAIN_WINDOW_COMPACT_WIDTH)
        self.assertEqual(self.window.size().height(), config.MAIN_WINDOW_COMPACT_HEIGHT)
        self.assertLessEqual(
            self.window.minimumSizeHint().width(),
            config.MAIN_WINDOW_COMPACT_WIDTH,
            msg=(
                f"title={self.window.title_bar.minimumSizeHint().width()} "
                f"footer={self.window.footer.minimumSizeHint().width()} "
                f"controller={self.window.compact_controller.minimumSizeHint().width()}"
            ),
        )
        self.assertLessEqual(
            self.window.minimumSizeHint().height(),
            config.MAIN_WINDOW_COMPACT_HEIGHT,
        )
        self.assertTrue(self.window.compact_controller.isVisibleTo(self.window))
        self.assertFalse(self.window.tabbed_content.isVisibleTo(self.window))
        self.assertFalse(self.window.history_edge_tab.isVisibleTo(self.window))
        self.assertEqual(self.window.compact_button.text(), "Полный размер")

        self.window.set_compact_mode(False)

        self.assertFalse(self.window._compact_mode)
        self.assertEqual(self.window.geometry(), full_geometry)
        self.assertTrue(self.window.voice_notes_workspace.isVisibleTo(self.window))
        self.assertFalse(self.window.tabbed_content.isVisibleTo(self.window))
        self.assertEqual(self.window.compact_button.text(), "Компактный режим")

    def test_compact_controls_delegate_to_quick_record(self):
        """Compact controls use the existing recording signal path."""
        toggles = []
        canceled = []
        self.window.record_toggled.connect(toggles.append)
        self.window.record_canceled.connect(lambda: canceled.append(True))
        self.window.set_compact_mode(True)

        self.window.compact_controller.record_button.click()
        self.assertEqual(toggles, [True])
        self.assertTrue(self.window.is_recording)

        self.window.compact_controller.cancel_button.click()
        self.assertEqual(canceled, [True])
        self.assertFalse(self.window.is_recording)

    def test_compact_mode_selection_is_persisted(self):
        """Mode transitions write the compact preference setting."""
        self.window.set_compact_mode(True)
        self.saved_setting.assert_any_call(SettingsKey.COMPACT_MODE, True)

        self.window.set_compact_mode(False)
        self.saved_setting.assert_any_call(SettingsKey.COMPACT_MODE, False)

    def test_persisted_compact_mode_is_restored(self):
        """Startup restoration applies the saved compact preference."""
        self.mock_get_setting.side_effect = (
            lambda key, default=None: True
            if key == SettingsKey.COMPACT_MODE
            else default
        )

        self.window._restore_compact_mode()

        self.assertTrue(self.window._compact_mode)
        self.assertEqual(self.window.compact_button.text(), "Полный размер")

    def test_collapsed_transcript_caps_tall_saved_geometry_on_restore(self):
        """Collapsed startup does not reserve space for the hidden transcript."""
        saved_geometry = {
            "x": 10,
            "y": 10,
            "width": 700,
            "height": 900,
            "format": self.window._geometry_format,
            "history_expanded": False,
        }
        self.mock_get_setting.side_effect = (
            lambda key, default=None: saved_geometry
            if key == SettingsKey.WINDOW_GEOMETRY
            else default
        )

        self.window._restore_window_geometry()

        self.assertTrue(self.window.quick_record_tab.is_transcription_collapsed())
        self.assertEqual(
            self.window.height(),
            config.MAIN_WINDOW_COLLAPSED_RESTORE_MAX_HEIGHT,
        )
        self.assertEqual(self.window.width(), config.MAIN_WINDOW_DEFAULT_WIDTH)

    def test_window_flags_keep_standard_taskbar_controls(self):
        """Windows uses its native frame while every platform keeps controls."""
        flags = self.window.windowFlags()

        self.assertTrue(flags & Qt.WindowType.Window)
        self.assertEqual(
            bool(flags & Qt.WindowType.FramelessWindowHint),
            sys.platform != "win32",
        )
        self.assertTrue(flags & Qt.WindowType.WindowSystemMenuHint)
        self.assertTrue(flags & Qt.WindowType.WindowMinimizeButtonHint)
        self.assertTrue(flags & Qt.WindowType.WindowMaximizeButtonHint)
        self.assertTrue(flags & Qt.WindowType.WindowCloseButtonHint)
        expected_maximum_width = (
            16777215 if sys.platform == "win32" else config.MAIN_WINDOW_MAX_WIDTH
        )
        self.assertEqual(self.window.maximumWidth(), expected_maximum_width)

        self.window.set_compact_mode(True)
        self.window.set_compact_mode(False)

        self.assertEqual(self.window.maximumWidth(), expected_maximum_width)

    def test_repeated_custom_maximize_toggles_return_to_normal(self):
        """Rapid repeated toggles do not leave state or icon out of sync."""
        self.window.show()
        self.app.processEvents()

        for expected_maximized in (True, False, True, False):
            self.window.title_bar._toggle_maximize()
            self.app.processEvents()
            self.assertEqual(
                self.window.title_bar._is_maximized,
                expected_maximized,
            )

        self.assertFalse(self.window.isMaximized())
        self.assertEqual(
            self.window.title_bar.maximize_btn.toolTip(),
            "Развернуть",
        )

    def test_title_bar_brand_has_balanced_spacing(self):
        """The title bar leaves comfortable space around the brand lockup."""
        layout = self.window.title_bar.layout()

        self.assertEqual(layout.contentsMargins().left(), 16)
        self.assertEqual(self.window.title_bar.brand_icon.width(), 28)
        self.assertEqual(layout.itemAt(1).spacerItem().sizeHint().width(), 4)

    def test_minimized_window_restores_to_normal_state(self):
        """The shared restore path handles a taskbar-minimized window."""
        self.window.show()
        self.app.processEvents()

        self.window.showMinimized()
        self.app.processEvents()
        self.assertTrue(self.window.isMinimized())

        self.window.restore_from_tray()
        self.app.processEvents()
        self.assertTrue(self.window.isVisible())
        self.assertFalse(self.window.isMinimized())

    def test_taskbar_restore_preserves_maximized_state(self):
        """Minimizing a maximized window does not restore it as a small one."""
        self.window.showMaximized()
        self.app.processEvents()

        self.window.showMinimized()
        self.app.processEvents()
        self.window.restore_from_tray()
        self.app.processEvents()

        self.assertTrue(self.window.isMaximized())


if __name__ == "__main__":
    unittest.main()
