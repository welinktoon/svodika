"""Tests for the quiet lower-right recording/transcription status pill."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from config import config
from ui_qt.overlays.waveform_overlay import WaveformOverlay


class TestCompactStatusOverlay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_overlay_is_compact_static_and_russian(self):
        overlay = WaveformOverlay()
        self.addCleanup(overlay.close)

        self.assertEqual(overlay.width(), config.WAVEFORM_OVERLAY_WIDTH)
        self.assertEqual(overlay.height(), config.WAVEFORM_OVERLAY_HEIGHT)
        self.assertLessEqual(overlay.width(), 200)
        self.assertLessEqual(overlay.height(), 48)

        overlay.set_state(overlay.STATE_TRANSCRIBING)

        self.assertFalse(overlay.timer.isActive())

    def test_recording_state_exposes_red_icon_only_stop_action(self):
        overlay = WaveformOverlay()
        self.addCleanup(overlay.close)
        requested = []
        overlay.stop_requested.connect(lambda: requested.append(True))

        overlay.show_at_cursor(overlay.STATE_RECORDING)
        self.app.processEvents()

        self.assertTrue(overlay.stop_button.isVisible())
        self.assertEqual(overlay.stop_button.text(), "")
        self.assertEqual(
            overlay.testAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents
            ),
            False,
        )
        overlay.stop_button.click()
        self.assertEqual(requested, [True])
        self.assertFalse(overlay.stop_button.isEnabled())

        overlay.set_state(overlay.STATE_PROCESSING)
        self.assertFalse(overlay.stop_button.isVisible())
        self.assertTrue(
            overlay.testAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents
            )
        )
        self.assertEqual(
            overlay._STATE_PRESENTATION[overlay.STATE_TRANSCRIBING][1],
            "Расшифровка",
        )
        self.assertEqual(
            overlay._STATE_PRESENTATION[overlay.STATE_RECORDING][1],
            "Запись",
        )

    def test_overlay_opens_near_lower_right_corner(self):
        overlay = WaveformOverlay()
        self.addCleanup(overlay.close)

        overlay.show_at_cursor(overlay.STATE_RECORDING)
        self.app.processEvents()

        screen = QApplication.screenAt(overlay.frameGeometry().center())
        if screen is None:
            screen = QApplication.primaryScreen()
        available = screen.availableGeometry()
        self.assertLessEqual(
            abs(
                overlay.frameGeometry().right()
                - (available.right() - 24)
            ),
            2,
        )
        self.assertLessEqual(
            abs(
                overlay.frameGeometry().bottom()
                - (available.bottom() - 24)
            ),
            2,
        )
        self.assertFalse(overlay.timer.isActive())


if __name__ == "__main__":
    unittest.main()
