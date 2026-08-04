"""Regression tests for the single-window sidebar navigation."""

import unittest

from PyQt6.QtWidgets import QApplication, QStyleOptionViewItem, QWidget

from ui_qt.main_window import MainWindow
from ui_qt.widgets.voice_notes_workspace import MeetingListDelegate


class TestEmbeddedSidebarNavigation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow()
        self.workspace = self.window.voice_notes_workspace

    def tearDown(self):
        self.window._force_quit = True
        self.window.close()

    def _connect_page(self, signal, key):
        page = QWidget()
        page.setObjectName(f"{key}Page")
        signal.connect(
            lambda: self.workspace.set_embedded_page(key, page)
        )
        return page

    def test_sidebar_pages_replace_content_in_the_same_window(self):
        devices = self._connect_page(
            self.window.devices_requested, "devices"
        )
        models = self._connect_page(
            self.window.model_manager_requested, "models"
        )
        settings = self._connect_page(
            self.window.settings_requested, "settings"
        )

        for button, key, page in (
            (self.workspace.devices_button, "devices", devices),
            (self.workspace.models_button, "models", models),
            (self.workspace.settings_button, "settings", settings),
        ):
            button.click()
            self.app.processEvents()
            self.assertIs(self.workspace.content_stack.currentWidget(), page)
            self.assertFalse(page.isWindow())
            self.assertTrue(button.property("active"))

        self.workspace.records_button.click()
        self.app.processEvents()
        self.assertEqual(
            self.workspace.content_stack.currentWidget().objectName(),
            "recordsPage",
        )
        self.assertTrue(self.workspace.records_button.property("active"))

    def test_meeting_header_actions_are_accessible_icon_buttons(self):
        actions = (
            (
                self.workspace.open_media_button,
                "Открыть запись встречи",
            ),
            (
                self.workspace.codex_improve_button,
                "Переделать расшифровку и итоги через Codex",
            ),
            (
                self.workspace.trash_button,
                "Переместить встречу в корзину",
            ),
        )

        for button, accessible_name in actions:
            self.assertEqual(button.text(), "")
            self.assertEqual(button.size().width(), 42)
            self.assertEqual(button.size().height(), 42)
            self.assertEqual(button.iconSize().width(), 18)
            self.assertFalse(button.icon().isNull())
            self.assertEqual(button.accessibleName(), accessible_name)

    def test_meeting_library_uses_roomier_navigation_and_title_delegate(self):
        nav_layout = self.workspace.records_button.parentWidget().layout()

        self.assertEqual(nav_layout.contentsMargins().left(), 20)
        self.assertIsInstance(
            self.workspace.notes.itemDelegate(),
            MeetingListDelegate,
        )
        self.assertEqual(
            self.workspace.notes.itemDelegate().sizeHint(
                QStyleOptionViewItem(),
                self.workspace.notes.model().index(0, 0),
            ).height(),
            76,
        )

    def test_waveform_progress_and_markdown_list_spacing(self):
        self.workspace.waveform.set_progress(0.42)
        self.assertAlmostEqual(self.workspace.waveform.progress, 0.42)
        self.workspace.waveform.set_progress(4)
        self.assertEqual(self.workspace.waveform.progress, 1.0)

        self.workspace._show_transcript_text(
            "# Участники и роли\n\n- Стас — участвует в разборе.\n",
            ".md",
        )
        block = self.workspace.transcript.document().begin()
        while block.isValid() and block.textList() is None:
            block = block.next()

        self.assertTrue(block.isValid())
        self.assertEqual(block.blockFormat().textIndent(), 7)


if __name__ == "__main__":
    unittest.main()
