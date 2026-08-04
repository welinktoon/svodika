"""
Confirm/edit dialog for a single learned cleanup rule.
"""
from typing import Optional

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit,
)
from PyQt6.QtGui import QFont

from ui_qt.widgets import PrimaryButton, Button


class CleanupRuleDialog(QDialog):
    """Popup to confirm an AI-polished learned rule or edit an existing one."""

    def __init__(
        self,
        rule: str,
        original: Optional[str] = None,
        notice: Optional[str] = None,
        parent=None,
    ):
        """Initialize the rule dialog.

        Args:
            rule: Rule text to confirm or edit (polished text when confirming).
            original: The raw instruction the rule was polished from. None when
                editing an existing rule. When set and polish succeeded, the
                user can choose polished (recommended) or exactly as typed.
            notice: Optional warning line (e.g. AI polish unavailable).
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._original = (original or "").strip()
        self._polished = (rule or "").strip()
        self._offer_choice = (
            original is not None
            and not notice
            and bool(self._original)
            and self._polished
            and self._polished.casefold() != self._original.casefold()
        )
        self.setWindowTitle(
            "Подтверждение правила" if original is not None else "Изменение правила"
        )
        self.setMinimumSize(460, 280 if self._offer_choice else 260)
        self.resize(520, 340 if self._offer_choice else 300)
        self._setup_ui(rule, original, notice)

    def _setup_ui(
        self, rule: str, original: Optional[str], notice: Optional[str]
    ) -> None:
        """Build the dialog layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel(
            "Подтвердите правило" if original is not None else "Измените правило"
        )
        title.setObjectName("headerLabel")
        title.setFont(QFont("Segoe UI", 14, QFont.Weight.DemiBold))
        layout.addWidget(title)

        if original is not None and original.strip():
            said = QLabel(f'Вы сказали: «{original.strip()}»')
            said.setObjectName("infoLabel")
            said.setWordWrap(True)
            layout.addWidget(said)

        if notice:
            warn = QLabel(notice)
            warn.setObjectName("infoLabel")
            warn.setWordWrap(True)
            layout.addWidget(warn)

        if self._offer_choice:
            info = QLabel(
                "ИИ преобразовал инструкцию в более понятное правило. Рекомендуется "
                "улучшенный вариант, но можно сохранить исходную формулировку. "
                "Перед сохранением текст можно изменить."
            )
        else:
            info = QLabel(
                "Это правило будет применяться при обработке каждой расшифровки. "
                "При необходимости измените его и сохраните."
            )
        info.setObjectName("infoLabel")
        info.setWordWrap(True)
        layout.addWidget(info)

        self.rule_edit = QTextEdit()
        self.rule_edit.setAcceptRichText(False)
        self.rule_edit.setFont(QFont("Segoe UI", 12))
        self.rule_edit.setPlainText(rule or "")
        self.rule_edit.setPlaceholderText("Введите правило…")
        self.rule_edit.setMinimumHeight(80)
        layout.addWidget(self.rule_edit, stretch=1)

        buttons = QHBoxLayout()
        buttons.addStretch()

        cancel_btn = Button("Отмена")
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(cancel_btn)

        if self._offer_choice:
            as_typed_btn = Button("Сохранить исходный текст")
            as_typed_btn.setToolTip("Сохранить вашу формулировку без изменений ИИ")
            as_typed_btn.clicked.connect(self._accept_as_typed)
            buttons.addWidget(as_typed_btn)

            polished_btn = PrimaryButton("Использовать улучшенный")
            polished_btn.setToolTip(
                "Сохранить улучшенное ИИ правило с вашими правками"
            )
            polished_btn.clicked.connect(self._accept_polished)
            buttons.addWidget(polished_btn)
        else:
            save_btn = PrimaryButton("Сохранить правило")
            save_btn.clicked.connect(self.accept)
            buttons.addWidget(save_btn)

        layout.addLayout(buttons)

    def _accept_as_typed(self) -> None:
        """Accept using the user's original wording."""
        self.rule_edit.setPlainText(self._original)
        self.accept()

    def _accept_polished(self) -> None:
        """Accept the polished text, or the user's edits if they changed it."""
        text = self.rule_edit.toPlainText().strip()
        if not text:
            self.rule_edit.setPlainText(self._polished)
        self.accept()

    def rule_text(self) -> str:
        """Return the edited rule text."""
        return self.rule_edit.toPlainText().strip()
