"""
Simple multi-line editor dialog for the AI transcript cleanup prompt.
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit,
)
from PyQt6.QtGui import QFont

from ui_qt.widgets import PrimaryButton, Button


class CleanupPromptDialog(QDialog):
    """Popup editor for a longer cleanup system prompt."""

    def __init__(self, prompt: str, parent=None):
        """Initialize the prompt editor.

        Args:
            prompt: Initial prompt text to edit.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.setWindowTitle("Инструкция обработки текста")
        self.setMinimumSize(520, 420)
        self.resize(560, 480)
        self._setup_ui(prompt)

    def _setup_ui(self, prompt: str) -> None:
        """Build the editor layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel("Инструкция обработки")
        title.setObjectName("headerLabel")
        title.setFont(QFont("Segoe UI", 14, QFont.Weight.DemiBold))
        layout.addWidget(title)

        info = QLabel(
            "Эта инструкция отправляется ИИ при обработке расшифровки. "
            "Опишите, как нужно улучшать и форматировать текст."
        )
        info.setObjectName("infoLabel")
        info.setWordWrap(True)
        layout.addWidget(info)

        self.prompt_edit = QTextEdit()
        self.prompt_edit.setAcceptRichText(False)
        self.prompt_edit.setFont(QFont("Segoe UI", 12))
        self.prompt_edit.setPlainText(prompt or "")
        self.prompt_edit.setPlaceholderText("Введите инструкцию обработки…")
        layout.addWidget(self.prompt_edit, stretch=1)

        buttons = QHBoxLayout()
        buttons.addStretch()

        cancel_btn = Button("Отмена")
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(cancel_btn)

        save_btn = PrimaryButton("Применить")
        save_btn.clicked.connect(self.accept)
        buttons.addWidget(save_btn)

        layout.addLayout(buttons)

    def prompt_text(self) -> str:
        """Return the edited prompt text."""
        return self.prompt_edit.toPlainText()
