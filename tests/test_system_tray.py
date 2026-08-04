"""Tests for standard system-tray activation behavior."""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QSystemTrayIcon

from ui_qt.system_tray import SystemTrayManager


class _WindowStub:
    def __init__(self, *, visible: bool, minimized: bool = False):
        self.visible = visible
        self.minimized = minimized
        self.restore_calls = 0
        self.hide_calls = 0

    def isVisible(self) -> bool:
        return self.visible

    def isMinimized(self) -> bool:
        return self.minimized

    def restore_from_tray(self) -> None:
        self.restore_calls += 1
        self.visible = True
        self.minimized = False

    def hide(self) -> None:
        self.hide_calls += 1
        self.visible = False


class TestSystemTrayActivation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _manager(self, window: _WindowStub) -> SystemTrayManager:
        manager = SystemTrayManager(window)
        self.addCleanup(manager.hide)
        return manager

    def test_single_click_hides_a_visible_window(self):
        window = _WindowStub(visible=True)
        manager = self._manager(window)

        manager._on_activated(QSystemTrayIcon.ActivationReason.Trigger)

        self.assertEqual(window.hide_calls, 1)
        self.assertEqual(window.restore_calls, 0)

    def test_single_click_restores_a_hidden_or_minimized_window(self):
        for window in (
            _WindowStub(visible=False),
            _WindowStub(visible=True, minimized=True),
        ):
            with self.subTest(visible=window.visible, minimized=window.minimized):
                manager = self._manager(window)
                manager._on_activated(
                    QSystemTrayIcon.ActivationReason.Trigger
                )
                self.assertEqual(window.restore_calls, 1)
                self.assertEqual(window.hide_calls, 0)

    def test_double_click_always_restores(self):
        window = _WindowStub(visible=True)
        manager = self._manager(window)

        manager._on_activated(QSystemTrayIcon.ActivationReason.DoubleClick)

        self.assertEqual(window.restore_calls, 1)
        self.assertEqual(window.hide_calls, 0)


if __name__ == "__main__":
    unittest.main()
