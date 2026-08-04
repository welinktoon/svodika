"""
System Tray Implementation for PyQt6 UI.
Manages system tray icon and menu.
"""
import logging
from pathlib import Path
from typing import Optional, Callable
from PyQt6.QtWidgets import QSystemTrayIcon, QMenu, QApplication
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtCore import pyqtSignal

logger = logging.getLogger(__name__)
APP_ICON = (
    Path(__file__).resolve().parent
    / "assets"
    / "meeting-recorder-logo.ico"
)


class SystemTrayManager(QSystemTrayIcon):
    """Manages system tray icon and menu."""

    show_requested = pyqtSignal()
    hide_requested = pyqtSignal()
    exit_requested = pyqtSignal()
    toggle_recording = pyqtSignal()

    def __init__(self, main_window=None):
        """Initialize system tray manager."""
        super().__init__()
        self.main_window = main_window

        # Callbacks
        self.on_show: Optional[Callable] = None
        self.on_hide: Optional[Callable] = None
        self.on_exit: Optional[Callable] = None

        self._setup_icon()
        self._setup_menu()
        self._connect_signals()

        self.show()
        logger.info("System tray initialized")

    def _setup_icon(self):
        """Setup the tray icon."""
        self.setIcon(QIcon(str(APP_ICON)))
        self.setToolTip("Svodika")

    def _setup_menu(self):
        """Setup the tray context menu."""
        self.menu = QMenu()  # Styled by the app-wide theme's QMenu rules

        # Show action
        show_action = self.menu.addAction("Показать")
        show_action.triggered.connect(self._on_show)

        # Hide action
        hide_action = self.menu.addAction("Скрыть")
        hide_action.triggered.connect(self._on_hide)

        # Toggle recording action
        self.menu.addSeparator()
        self.toggle_action = self.menu.addAction("Начать запись")
        self.toggle_action.triggered.connect(self._on_toggle)

        # Settings action
        self.menu.addSeparator()
        settings_action = self.menu.addAction("Настройки")
        settings_action.setMenuRole(QAction.MenuRole.NoRole)
        settings_action.triggered.connect(self._on_settings)

        # Exit action
        self.menu.addSeparator()
        exit_action = self.menu.addAction("Выйти")
        exit_action.setMenuRole(QAction.MenuRole.NoRole)
        exit_action.triggered.connect(self._on_exit)

        self.setContextMenu(self.menu)

    def _connect_signals(self):
        """Connect signals."""
        self.activated.connect(self._on_activated)

    def _on_activated(self, reason):
        """Handle tray icon activation."""
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if (
                self.main_window
                and self.main_window.isVisible()
                and not self.main_window.isMinimized()
            ):
                self._on_hide()
            else:
                self._on_show()
        elif reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            # A double-click always leaves the app visible, even on platforms
            # that report the first click separately as Trigger.
            self._on_show()

    def _on_show(self):
        """Handle show action."""
        if self.main_window:
            self.main_window.restore_from_tray()

        if self.on_show:
            self.on_show()

        self.show_requested.emit()

    def _on_hide(self):
        """Handle hide action."""
        if self.main_window:
            self.main_window.hide()

        if self.on_hide:
            self.on_hide()

        self.hide_requested.emit()

    def _on_toggle(self):
        """Handle toggle recording action."""
        self.toggle_recording.emit()

    def _on_settings(self):
        """Handle settings action."""
        if self.main_window:
            self.main_window.open_settings()

    def _on_exit(self):
        """Handle exit action."""
        if self.on_exit:
            self.on_exit()

        self.exit_requested.emit()
        QApplication.instance().quit()

    def set_recording(self, is_recording: bool):
        """Update the menu based on recording state."""
        self.toggle_action.setText(
            "Остановить запись" if is_recording else "Начать запись"
        )
