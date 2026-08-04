"""
PyQt6 main window.
Main application window with recording controls and transcription display.
"""
import logging
import sys
from pathlib import Path
from typing import Optional, Callable
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QTextEdit, QFrame, QPushButton
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QEvent, QPropertyAnimation, QRect, QSize
from PyQt6.QtGui import QAction, QFont, QIcon, QKeySequence, QPixmap
import qtawesome as qta

from config import config
from services.hotkey_manager import format_hotkey_display
from services.settings import SettingsKey, settings_manager
from ui_qt.utils.theme_manager import load_theme_stylesheet
from ui_qt.utils.collapse_animation import (
    SECTION_COLLAPSE_DURATION_MS,
    SECTION_COLLAPSE_EASING,
    UNLIMITED_HEIGHT,
)

logger = logging.getLogger(__name__)


class CustomTitleBar(QFrame):
    """Custom title bar for frameless window with integrated menu."""

    _MENU_BAR_STYLE = """
        QMenuBar {
            background-color: transparent;
            color: #9aa8bc;
            font-size: 12px;
            border: none;
            spacing: 0px;
        }
        QMenuBar::item {
            background-color: transparent;
            padding: 8px 10px 4px 10px;
        }
        QMenuBar::item:selected {
            background-color: #1d2939;
            color: #f3f6fb;
        }
        QMenuBar::item:pressed {
            background-color: #203b5d;
        }
        QMenu::separator {
            height: 1px;
            background-color: #2b3749;
            margin: 4px 8px;
        }
    """

    _TITLE_LABEL_STYLE = """
        QLabel {
            background-color: transparent;
            color: #f3f6fb;
            font-size: 13px;
            font-weight: 600;
            font-family: 'Segoe UI', sans-serif;
        }
    """

    _WINDOW_BUTTON_STYLE = """
        QPushButton {
            background-color: transparent;
            border: none;
            color: #9aa8bc;
            font-size: 14px;
            font-family: 'Segoe UI', sans-serif;
        }
        QPushButton:hover {
            background-color: #1d2939;
            color: #f3f6fb;
        }
    """

    _CLOSE_BUTTON_STYLE = """
        QPushButton {
            background-color: transparent;
            border: none;
            color: #9aa8bc;
            font-size: 14px;
            font-family: 'Segoe UI', sans-serif;
        }
        QPushButton:hover {
            background-color: #e05260;
            color: #ffffff;
        }
    """

    _TITLE_BAR_STYLE = """
        #customTitleBar {
            background-color: #17202d;
            border-bottom: 1px solid #2b3749;
        }
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_window = parent
        self._drag_position = None
        self._is_maximized = False
        self._normal_geometry = None  # Store geometry before maximizing
        self.setFixedHeight(38)
        self.setObjectName("customTitleBar")
        self.setAutoFillBackground(True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 0, 0)
        layout.setSpacing(0)

        self._build_menu_bar(layout)
        self._build_title_label(layout)
        layout.addStretch()
        self._build_window_buttons(layout)

        self.setStyleSheet(self._TITLE_BAR_STYLE)

    def _build_menu_bar(self, layout: QHBoxLayout) -> None:
        """Keep application actions available without visible duplicate menus."""
        from PyQt6.QtWidgets import QMenuBar
        self.menu_bar = QMenuBar(self)
        self.menu_bar.setStyleSheet(self._MENU_BAR_STYLE)
        self.menu_bar.hide()

    def _build_title_label(self, layout: QHBoxLayout) -> None:
        """Create a compact brand at the left edge."""
        self.brand_icon = QLabel()
        logo_path = (
            Path(__file__).resolve().parent
            / "assets"
            / "meeting-recorder-mark.png"
        )
        logo = QPixmap(str(logo_path))
        if not logo.isNull():
            self.brand_icon.setPixmap(
                logo.scaled(
                    24,
                    24,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        self.brand_icon.setFixedSize(28, 30)
        self.brand_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.brand_icon)
        layout.addSpacing(4)

        self.title_label = QLabel("Svodika")
        self.title_label.setStyleSheet(self._TITLE_LABEL_STYLE)
        layout.addWidget(self.title_label)

    def _build_window_buttons(self, layout: QHBoxLayout) -> None:
        """Create the minimize/maximize/close window-control buttons."""
        self.minimize_btn = QPushButton()
        self.minimize_btn.setIcon(qta.icon("fa6s.minus", color="#9aa8bc"))
        self.minimize_btn.setIconSize(QSize(12, 12))
        self.minimize_btn.setFixedSize(44, 38)
        self.minimize_btn.setStyleSheet(self._WINDOW_BUTTON_STYLE)
        self.minimize_btn.setToolTip("Свернуть")
        self.minimize_btn.setAccessibleName("Свернуть")
        self.minimize_btn.clicked.connect(self._minimize)

        self.maximize_btn = QPushButton()
        self.maximize_btn.setIcon(qta.icon("fa6s.expand", color="#9aa8bc"))
        self.maximize_btn.setIconSize(QSize(12, 12))
        self.maximize_btn.setFixedSize(44, 38)
        self.maximize_btn.setStyleSheet(self._WINDOW_BUTTON_STYLE)
        self.maximize_btn.setToolTip("Развернуть")
        self.maximize_btn.setAccessibleName("Развернуть")
        self.maximize_btn.clicked.connect(self._toggle_maximize)

        self.close_btn = QPushButton()
        self.close_btn.setIcon(qta.icon("fa6s.xmark", color="#9aa8bc"))
        self.close_btn.setIconSize(QSize(13, 13))
        self.close_btn.setFixedSize(44, 38)
        self.close_btn.setStyleSheet(self._CLOSE_BUTTON_STYLE)
        self.close_btn.setAccessibleName("Закрыть")
        self.close_btn.setToolTip("Закрыть")
        self.close_btn.clicked.connect(self._close)

        layout.addWidget(self.minimize_btn)
        layout.addWidget(self.maximize_btn)
        layout.addWidget(self.close_btn)

    def _minimize(self):
        if self.parent_window:
            self.parent_window.showMinimized()

    def _toggle_maximize(self):
        if self.parent_window:
            if getattr(self.parent_window, "_compact_mode", False):
                return
            is_maximized = (
                self.parent_window.isMaximized() or self._is_maximized
            )
            if is_maximized:
                # A geometry assignment alone does not clear Qt's maximized
                # window-state bit.  That made every second/third click appear
                # to stop working on Windows.
                normal_geometry = self._normal_geometry
                self.parent_window.showNormal()
                if normal_geometry:
                    self.parent_window.setGeometry(normal_geometry)
                self.sync_window_state(False)
            else:
                # Save current geometry before maximizing
                self._normal_geometry = QRect(self.parent_window.geometry())
                self.parent_window.showMaximized()
                self.sync_window_state(True)

    def sync_window_state(self, maximized: bool) -> None:
        """Keep the custom control in sync with native window-state changes."""
        self._is_maximized = bool(maximized)
        self.maximize_btn.setIcon(
            qta.icon(
                "fa6s.compress" if maximized else "fa6s.expand",
                color="#9aa8bc",
            )
        )
        label = "Восстановить" if maximized else "Развернуть"
        self.maximize_btn.setToolTip(label)
        self.maximize_btn.setAccessibleName(label)

    def _close(self):
        if self.parent_window:
            self.parent_window.close()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.parent_window:
            global_pos = event.globalPosition().toPoint()
            local_pos = self.parent_window.mapFromGlobal(global_pos)
            edge = self.parent_window._get_resize_edge(local_pos)
            if edge != (0, 0):
                self.parent_window._begin_resize(edge, global_pos)
                event.accept()
                return
            self._drag_position = global_pos - self.parent_window.frameGeometry().topLeft()
            event.accept()
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.parent_window and self.parent_window._resizing:
            self.parent_window._apply_resize_delta(event.globalPosition().toPoint())
            event.accept()
            return
        if event.buttons() == Qt.MouseButton.LeftButton and self._drag_position and self.parent_window:
            self.parent_window.move(event.globalPosition().toPoint() - self._drag_position)
            event.accept()
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.parent_window:
            self._drag_position = None
            if self.parent_window._resizing:
                self.parent_window._finish_resize()
                event.accept()
                return

        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle_maximize()


from ui_qt.widgets import (
    HeaderCard, Card, PrimaryButton, DangerButton,
    SuccessButton, WarningButton, ControlPanel, Button,
    HistorySidebar, HistoryEdgeTab, HotkeyHintFilter,
    TranscriptionStatsWidget,
    TabbedContentWidget, QuickRecordTab, UploadFileTab,
    CompactRecordController,
    VoiceNotesWorkspace,
)
from services.history_manager import history_manager
from ui_qt.dialogs.history_entry_dialog import HistoryEntryDialog


class MainWindow(QMainWindow):
    """PyQt6 main window with clean, professional design."""

    # Window-local keyboard shortcuts. Distinct from the global hotkeys in
    # config.DEFAULT_HOTKEYS, which work even when the app is unfocused.
    HISTORY_SHORTCUT = "Ctrl+H"
    COMPACT_SHORTCUT = "Ctrl+Shift+C"
    QUIT_SHORTCUT = "Ctrl+Q"

    # Signals for application events
    record_toggled = pyqtSignal(bool)
    record_canceled = pyqtSignal()
    model_changed = pyqtSignal(str)
    whisper_engine_changed = pyqtSignal()  # Local engine (model/device/quant) changed
    transcription_ready = pyqtSignal(str)
    settings_requested = pyqtSignal()
    devices_requested = pyqtSignal()
    model_manager_requested = pyqtSignal()
    hotkeys_requested = pyqtSignal()
    about_requested = pyqtSignal()
    history_toggle_requested = pyqtSignal()
    retranscribe_requested = pyqtSignal(str)  # audio_path
    codex_improve_requested = pyqtSignal(
        str, str, str, str
    )  # audio_path, original_transcript, history_entry_id, mode
    upload_file_requested = pyqtSignal(str)  # audio_path from upload tab Transcribe button
    tab_changed = pyqtSignal(int)  # Emitted when tab selection changes

    def __init__(self):
        """Initialize the main window."""
        super().__init__()
        self.setWindowTitle("Svodika")
        self.setWindowIcon(
            QIcon(
                str(
                    Path(__file__).resolve().parent
                    / "assets"
                    / "meeting-recorder-logo.ico"
                )
            )
        )

        # Windows gets its native non-client frame.  Besides using the standard
        # Windows 11 controls, this restores shell shadows, the resize outline,
        # rounded corners and reliable Snap/maximize behavior.  Other platforms
        # retain the existing custom title bar.
        self._use_native_window_frame = sys.platform == "win32"

        # Frameless window with custom title bar on non-Windows platforms.
        # Keep the explicit Window type flag: setWindowFlags() replaces *all*
        # flags, and a bare FramelessWindowHint drops the top-level Window type.
        # On macOS that produces an NSWindow that fails to order back to the
        # front after hide() (i.e. can't be restored from the tray); on Windows
        # it happens to work either way. Including Window is safe on both.
        window_flags = (
            Qt.WindowType.Window
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        if not self._use_native_window_frame:
            window_flags |= Qt.WindowType.FramelessWindowHint
        self.setWindowFlags(window_flags)
        self.setMinimumSize(
            config.MAIN_WINDOW_MIN_WIDTH,
            config.MAIN_WINDOW_MIN_HEIGHT,
        )
        # A finite maximum width makes Qt remove WS_MAXIMIZEBOX on Windows,
        # leaving a caption button that looks native but cannot participate in
        # normal Windows maximize/Snap behavior.  Let the native frame use the
        # whole monitor; keep the legacy width cap for the custom frame.
        if not self._use_native_window_frame:
            self.setMaximumWidth(config.MAIN_WINDOW_MAX_WIDTH)
        self.resize(
            config.MAIN_WINDOW_DEFAULT_WIDTH,
            config.MAIN_WINDOW_DEFAULT_HEIGHT,
        )

        # State
        self.is_recording = False
        self.current_model = config.MODEL_CHOICES[0]
        self._force_quit = False  # Flag to bypass minimize to tray on close
        self._initial_show_complete = False  # Track if initial show has completed
        self._compact_mode = False
        self._full_geometry = None

        # Window sizing for sidebar toggle
        self._collapsed_width = config.MAIN_WINDOW_DEFAULT_WIDTH
        self._sidebar_width = config.MAIN_WINDOW_HISTORY_SIDEBAR_WIDTH
        # Ignore geometry saved by the compact two-panel legacy UI.
        self._geometry_format = "voice_notes_workspace_v3"

        # Height actually reclaimed by the last transcription collapse, so the
        # matching expand restores exactly that much (see _on_transcription_collapsed).
        self._collapse_freed_height = 0

        # Same tracking for the Engine Settings panel (independent of transcription).
        self._engine_collapse_freed_height = 0

        # Edge resize support for frameless window
        self._resize_margin = 8  # Pixels from edge to trigger resize
        self._resizing = False
        self._resize_edge = None  # Tuple of (horizontal, vertical) edge flags
        self._resize_start_pos = None
        self._resize_start_geometry = None

        # Geometry persistence
        self._geometry_save_timer = None
        self._tab_history_refresh_timer = QTimer(self)
        self._tab_history_refresh_timer.setSingleShot(True)
        self._tab_history_refresh_timer.timeout.connect(
            self._refresh_history_sidebar_if_expanded
        )

        # Callbacks (will be set by controller)
        self.on_show_copied_animation: Optional[Callable] = None

        # Setup UI
        self._setup_ui()
        self._setup_menu()
        self._connect_signals()
        self._load_saved_settings()
        self._restore_window_geometry()
        self._restore_compact_mode()

        if self._use_native_window_frame:
            QTimer.singleShot(0, self._apply_windows_frame_theme)

        # Enable mouse tracking for resize cursor updates
        self.setMouseTracking(True)
        # Install event filter on application to catch mouse moves from all widgets
        from PyQt6.QtWidgets import QApplication
        QApplication.instance().installEventFilter(self)

    def _setup_ui(self):
        """Setup the user interface."""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        # Resize hit-testing is handled in code, so the content needs no frame.
        central_widget.setStyleSheet("""
            QWidget#centralWidget {
                border: 0;
            }
        """)
        central_widget.setObjectName("centralWidget")
        central_widget.setMouseTracking(True)

        # Outer layout for title bar + content
        outer_layout = QVBoxLayout(central_widget)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        # Custom title bar
        self.title_bar = CustomTitleBar(self)
        outer_layout.addWidget(self.title_bar)
        self.title_bar.setVisible(not self._use_native_window_frame)

        # Container for main content + sidebar
        content_wrapper = QWidget()
        root_layout = QHBoxLayout(content_wrapper)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        outer_layout.addWidget(content_wrapper, stretch=1)

        # Main content area (left side)
        main_area = QWidget()
        main_area_layout = QVBoxLayout(main_area)
        main_area_layout.setContentsMargins(0, 0, 0, 0)
        main_area_layout.setSpacing(0)

        # Tabbed Content Widget (Quick Record)
        self.tabbed_content = TabbedContentWidget()
        self.quick_record_tab = QuickRecordTab()

        self.tabbed_content.add_tab(self.quick_record_tab, "Quick Record")

        self.upload_file_tab = UploadFileTab()
        self.tabbed_content.add_tab(self.upload_file_tab, "Upload File")

        # All transcription tabs; used to fan out shared state (model
        # selection, engine settings, collapse mirroring, device info).
        self.transcription_tabs = (self.quick_record_tab, self.upload_file_tab)

        # Sync the stack with the tab bar after all tabs are added
        # (fixes timing issue where tab bar index is restored before stack has widgets)
        self.tabbed_content.sync_stack_with_tab_bar()

        self.compact_controller = CompactRecordController()
        self.compact_controller.hide()
        self.compact_controller.record_requested.connect(
            self.quick_record_tab.record_button.click
        )
        self.compact_controller.stop_requested.connect(
            self.quick_record_tab.stop_button.click
        )
        self.compact_controller.cancel_requested.connect(
            self.quick_record_tab.cancel_button.click
        )

        # Connect tab changed signal to update sidebar and emit signal
        self.tabbed_content.tab_changed.connect(self._on_tab_changed)

        # Connect signals shared by all transcription tabs
        for tab in self.transcription_tabs:
            tab.model_changed.connect(self._on_model_changed)
            tab.engine_settings_changed.connect(self._on_engine_settings_changed)
            tab.manage_models_requested.connect(self.model_manager_requested)
            tab.engine_settings_collapsed.connect(self._on_engine_settings_collapsed)
            tab.transcription_collapsed.connect(self._on_transcription_collapsed)
            tab.stats_widget.visibility_changed.connect(self._on_stats_visibility_changed)

        # Connect tab-specific signals
        self.quick_record_tab.record_toggled.connect(self._on_quick_record_toggled)
        self.quick_record_tab.record_canceled.connect(self._on_quick_record_canceled)
        self.upload_file_tab.upload_requested.connect(self._on_upload_file_transcribe)

        # Keep the original tabs alive for the existing controller while the
        # new note-centric workspace becomes the visible Windows interface.
        self.tabbed_content.hide()
        main_area_layout.addWidget(self.tabbed_content)
        self.voice_notes_workspace = VoiceNotesWorkspace()
        self.voice_notes_workspace.record_requested.connect(self.quick_record_tab.record_button.click)
        self.voice_notes_workspace.stop_requested.connect(self.quick_record_tab.stop_button.click)
        self.voice_notes_workspace.cancel_requested.connect(self.record_canceled.emit)
        self.voice_notes_workspace.transcribe_requested.connect(self._on_upload_file_transcribe)
        self.voice_notes_workspace.codex_improve_requested.connect(
            self.codex_improve_requested.emit
        )
        self.voice_notes_workspace.model_selected.connect(self._on_workspace_model_selected)
        self.voice_notes_workspace.theme_changed.connect(self._on_workspace_theme_changed)
        self.voice_notes_workspace.settings_requested.connect(self.settings_requested)
        self.voice_notes_workspace.devices_requested.connect(self.devices_requested)
        self.voice_notes_workspace.models_requested.connect(self.model_manager_requested)
        main_area_layout.addWidget(self.voice_notes_workspace, stretch=1)
        main_area_layout.addWidget(self.compact_controller)

        # Add main area to root layout
        root_layout.addWidget(main_area, stretch=1)

        # History edge tab (always visible toggle button)
        self.history_edge_tab = HistoryEdgeTab()
        self.history_edge_tab.set_shortcut_hint(self.HISTORY_SHORTCUT)
        self.history_edge_tab.clicked.connect(self.toggle_history)
        root_layout.addWidget(self.history_edge_tab)

        # History sidebar (right side)
        self.history_sidebar = HistorySidebar()
        self.history_sidebar.entry_selected.connect(self._on_history_entry_selected)
        self.history_sidebar.entry_copied.connect(self._on_history_entry_copied)
        self.history_sidebar.entry_deleted.connect(self._on_history_entry_deleted)
        self.history_sidebar.retranscribe_requested.connect(self._on_retranscribe_requested)
        self.history_sidebar.width_animated.connect(self._on_sidebar_width_animated)
        root_layout.addWidget(self.history_sidebar)
        self.history_edge_tab.hide()
        self.history_sidebar.hide()

        # Sync the sidebar with the restored tab (must be after history_sidebar is created)
        self._on_tab_changed(self.tabbed_content.current_index())

        self._build_footer(outer_layout)
        # Tray/compact/quit controls live in the app menu and made the visual
        # workspace feel like a debug panel rather than a note app.
        self.footer.hide()

    _FOOTER_BAR_STYLE = """
        QWidget#footerBar {
            background-color: #1c1c1e;
            border-top: 1px solid #2c2c2e;
        }
    """

    _TRAY_BUTTON_STYLE = """
        QPushButton#trayButton {
            background-color: #2c2c2e;
            color: #e5e5e7;
            border: 1px solid #3a3a3c;
            border-radius: 8px;
            padding: 6px 18px;
            font-weight: 600;
            font-size: 13px;
        }
        QPushButton#trayButton:hover {
            background-color: #0a84ff;
            color: #ffffff;
            border: 1px solid #0a84ff;
        }
        QPushButton#trayButton:pressed {
            background-color: #0060df;
            color: #ffffff;
        }
    """

    _COMPACT_BUTTON_STYLE = """
        QPushButton#compactButton {
            background-color: #2c2c2e;
            color: #64d2ff;
            border: 1px solid #3a3a3c;
            border-radius: 8px;
            padding: 6px 18px;
            font-weight: 600;
            font-size: 13px;
        }
        QPushButton#compactButton:hover {
            background-color: #0a84ff;
            color: #ffffff;
            border: 1px solid #0a84ff;
        }
        QPushButton#compactButton:pressed {
            background-color: #0060df;
            color: #ffffff;
        }
    """

    _QUIT_BUTTON_STYLE = """
        QPushButton#quitButton {
            background-color: #2c2c2e;
            color: #ff453a;
            border: 1px solid #3a3a3c;
            border-radius: 8px;
            padding: 6px 18px;
            font-weight: 600;
            font-size: 13px;
        }
        QPushButton#quitButton:hover {
            background-color: #ff453a;
            color: #ffffff;
            border: 1px solid #ff453a;
        }
        QPushButton#quitButton:pressed {
            background-color: #d70015;
            color: #ffffff;
        }
    """

    def _build_footer(self, outer_layout: QVBoxLayout) -> None:
        """Create the bottom footer bar containing window actions."""
        self.footer = QWidget()
        self.footer.setObjectName("footerBar")
        self.footer.setFixedHeight(48)
        self.footer.setStyleSheet(self._FOOTER_BAR_STYLE)

        footer_layout = QHBoxLayout(self.footer)
        footer_layout.setContentsMargins(16, 7, 16, 7)
        footer_layout.setSpacing(0)
        footer_layout.addStretch()

        self.tray_button = Button("Свернуть в трей")
        self.tray_button.setObjectName("trayButton")
        self.tray_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.tray_button.setFixedHeight(34)
        self.tray_button.setMinimumWidth(140)
        self.tray_button.setStyleSheet(self._TRAY_BUTTON_STYLE)
        self.tray_button.set_hotkey(
            format_hotkey_display(config.DEFAULT_HOTKEYS["minimize_tray"])
        )
        self.tray_button.clicked.connect(self.minimize_to_tray)
        footer_layout.addWidget(self.tray_button)

        footer_layout.addSpacing(10)

        self.compact_button = QPushButton("Компактный режим")
        self.compact_button.setObjectName("compactButton")
        self.compact_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.compact_button.setFixedHeight(34)
        self.compact_button.setMinimumWidth(100)
        self.compact_button.setStyleSheet(self._COMPACT_BUTTON_STYLE)
        HotkeyHintFilter(self.compact_button, self.COMPACT_SHORTCUT)
        self.compact_button.clicked.connect(self.toggle_compact_mode)
        footer_layout.addWidget(self.compact_button)

        footer_layout.addSpacing(10)

        self.quit_button = QPushButton("Выйти")
        self.quit_button.setObjectName("quitButton")
        self.quit_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.quit_button.setFixedHeight(34)
        self.quit_button.setMinimumWidth(100)
        self.quit_button.setStyleSheet(self._QUIT_BUTTON_STYLE)
        HotkeyHintFilter(self.quit_button, self.QUIT_SHORTCUT)
        self.quit_button.clicked.connect(self.quit_application)
        footer_layout.addWidget(self.quit_button)

        footer_layout.addStretch()

        outer_layout.addWidget(self.footer)

    def _setup_menu(self):
        """Setup the menu bar in the custom title bar."""
        # Hide the QMainWindow's built-in menu bar
        self.menuBar().hide()

        # Use the custom title bar's menu bar
        menubar = self.title_bar.menu_bar

        # File menu
        file_menu = menubar.addMenu("Файл")
        # Qt auto-assigns PreferencesRole to actions named "Settings", which
        # rewrites the label to "Preferences" on Windows. Keep our wording.
        settings_action = file_menu.addAction("Настройки", self.open_settings)
        settings_action.setMenuRole(QAction.MenuRole.NoRole)
        models_action = file_menu.addAction("Управление моделями…", self.open_model_manager)
        models_action.setMenuRole(QAction.MenuRole.NoRole)
        file_menu.addAction("Горячие клавиши", self.open_hotkey_settings)
        file_menu.addSeparator()
        file_menu.addAction("Свернуть в трей", self.minimize_to_tray)
        quit_action = file_menu.addAction(
            "Выйти", self.quit_application
        )
        quit_action.setMenuRole(QAction.MenuRole.NoRole)
        quit_action.setShortcut(QKeySequence(self.QUIT_SHORTCUT))

        # View menu
        view_menu = menubar.addMenu("Вид")
        history_action = view_menu.addAction("История", self.toggle_history)
        history_action.setShortcut(QKeySequence(self.HISTORY_SHORTCUT))
        compact_action = view_menu.addAction("Компактный режим", self.toggle_compact_mode)
        compact_action.setShortcut(QKeySequence(self.COMPACT_SHORTCUT))

        # Help menu
        help_menu = menubar.addMenu("Справка")
        about_action = help_menu.addAction("О приложении", self.show_about)
        about_action.setMenuRole(QAction.MenuRole.NoRole)

    def _connect_signals(self):
        """Connect signals to slots."""
        # Note: Button signals are now handled by QuickRecordTab
        # Tab connections are set up in _setup_ui
        pass

    def _load_saved_settings(self):
        """Load saved settings and apply to UI."""
        try:
            saved_model = settings_manager.load_model_selection()
            for tab in self.transcription_tabs:
                tab.set_model_selection(saved_model)
            self.current_model = self.quick_record_tab.current_model
            self._apply_local_engine_visibility(self.current_model)
            self._on_workspace_theme_changed(
                settings_manager.get(SettingsKey.THEME, "light"), persist=False
            )
            logger.info(f"Loaded saved model selection: {saved_model}")
        except Exception as e:
            logger.error(f"Failed to load saved settings: {e}")
            # Use default (already set)

    def _on_tab_changed(self, index: int):
        """Handle tab selection change."""
        logger.debug(f"Tab changed to index {index}")

        if self._compact_mode and index != TabbedContentWidget.TAB_QUICK_RECORD:
            self.set_compact_mode(False)

        self._schedule_history_sidebar_refresh()

        # Emit signal for external listeners
        self.tab_changed.emit(index)

    def _schedule_history_sidebar_refresh(self) -> None:
        """Defer visible sidebar refreshes so tab clicks stay responsive."""
        if not self.history_sidebar.is_expanded:
            return

        self._tab_history_refresh_timer.start(75)

    def _refresh_history_sidebar_if_expanded(self) -> None:
        """Refresh history only when the sidebar is actually visible."""
        if self.history_sidebar.is_expanded:
            self.history_sidebar.refresh()

    def _on_quick_record_toggled(self, is_recording: bool):
        """Handle record toggle from Quick Record tab."""
        self.is_recording = is_recording
        self.compact_controller.set_recording_state(is_recording)
        self.compact_controller.set_status(
            "Идёт запись…" if is_recording else "Готово к записи"
        )

        # Lock/unlock tabs during recording
        if is_recording:
            self.tabbed_content.set_recording_state(True, TabbedContentWidget.TAB_QUICK_RECORD)
        else:
            self.tabbed_content.set_recording_state(False, -1)

        self.record_toggled.emit(is_recording)

    def _on_quick_record_canceled(self):
        """Handle cancel from Quick Record tab."""
        self.is_recording = False
        self.compact_controller.set_recording_state(False)
        self.compact_controller.set_status("Готово к записи")
        self.tabbed_content.set_recording_state(False, -1)

        self.record_canceled.emit()

    def _on_model_changed(self, model_name: str):
        """Handle model selection change from either tab and keep both in sync."""
        self.current_model = model_name

        # Sync the other tabs' combos without re-emitting the signal
        for tab in self.transcription_tabs:
            combo = tab.model_combo
            if combo.currentText() != model_name:
                combo.blockSignals(True)
                combo.setCurrentText(model_name)
                tab.current_model = model_name
                combo.blockSignals(False)

        self._apply_local_engine_visibility(model_name)

        self.model_changed.emit(model_name)

    def _apply_local_engine_visibility(self, model_name: str):
        """Show the local-engine panel only when Local Whisper is the backend.

        Args:
            model_name: The backend display name (e.g. "Local Whisper").
        """
        is_local = config.MODEL_VALUE_MAP.get(model_name) == "local_whisper"
        for tab in self.transcription_tabs:
            tab.set_local_engine_visible(is_local)

    def _on_engine_settings_changed(self):
        """Keep both tabs' engine panels in sync, then notify listeners.

        The emitting widget has already persisted the three keys to settings, so
        both panels reload from that canonical source (signals blocked inside
        ``load_from_settings``). This avoids depending on ``sender()`` identity
        and guarantees the two tabs always agree. ``whisper_engine_changed`` then
        triggers the controller's background reload.
        """
        for tab in self.transcription_tabs:
            tab.local_engine.load_from_settings()
        self.whisper_engine_changed.emit()

    def _on_upload_file_transcribe(self, audio_path: str):
        """Handle Transcribe click from the Upload File tab."""
        self.upload_file_requested.emit(audio_path)

    def _on_workspace_model_selected(self, model_name: str):
        """Persist the local model selected beside the recording."""
        if not model_name:
            return
        values = settings_manager.load_all_settings()
        if values.get(SettingsKey.WHISPER_MODEL) == model_name:
            return
        values[SettingsKey.WHISPER_MODEL] = model_name
        settings_manager.save_all_settings(values)
        for tab in self.transcription_tabs:
            tab.local_engine.load_from_settings()
        self.whisper_engine_changed.emit()

    def _on_workspace_theme_changed(self, theme_name: str, persist: bool = True):
        """Apply one theme to the workspace and every inherited dialog."""
        from pathlib import Path
        from PyQt6.QtWidgets import QApplication
        theme_name = "dark" if theme_name == "dark" else "light"
        self.voice_notes_workspace.set_theme(theme_name)
        app = QApplication.instance()
        if app:
            if theme_name == "dark":
                theme_path = Path(__file__).parent / "styles" / "dark.qss"
                app.setStyleSheet(load_theme_stylesheet(theme_path))
            else:
                theme_path = Path(__file__).parent / "styles" / "light.qss"
                app.setStyleSheet(load_theme_stylesheet(theme_path))
        if theme_name == "light":
            self.title_bar.setStyleSheet("#customTitleBar{background:#ffffff;border-bottom:1px solid #e6e8ed;}")
            self.title_bar.title_label.setStyleSheet("color:#171a20;font-size:13px;font-weight:600;font-family:'Segoe UI';")
            self.title_bar.menu_bar.setStyleSheet("QMenuBar{background:transparent;color:#5f6672;border:0;} QMenuBar::item{padding:8px 10px 4px;} QMenuBar::item:selected{background:#edf4ff;color:#1769e0;}")
        else:
            self.title_bar.setStyleSheet(self.title_bar._TITLE_BAR_STYLE)
            self.title_bar.title_label.setStyleSheet(self.title_bar._TITLE_LABEL_STYLE)
            self.title_bar.menu_bar.setStyleSheet(self.title_bar._MENU_BAR_STYLE)
        self._apply_windows_frame_theme(theme_name)
        if persist:
            values = settings_manager.load_all_settings(); values[SettingsKey.THEME] = theme_name; settings_manager.save_all_settings(values)

    def _apply_windows_frame_theme(self, theme_name: str = "") -> None:
        """Match the native Windows 11 title bar and outline to the app theme."""
        if not self._use_native_window_frame:
            return
        try:
            import ctypes

            resolved_theme = theme_name or (
                "dark" if self.voice_notes_workspace.dark else "light"
            )
            hwnd = ctypes.c_void_p(int(self.winId()))
            dark_mode = ctypes.c_int(1 if resolved_theme == "dark" else 0)
            rounded_corners = ctypes.c_int(2)  # DWMWCP_ROUND
            # COLORREF uses BGR byte order.  Keep the outline subtle, like the
            # rest of the workspace dividers.
            border_rgb = (
                (43, 55, 73)
                if resolved_theme == "dark"
                else (226, 232, 240)
            )
            border_color = ctypes.c_uint(
                border_rgb[0] | border_rgb[1] << 8 | border_rgb[2] << 16
            )
            dwm = ctypes.windll.dwmapi
            dwm.DwmSetWindowAttribute(
                hwnd, 20, ctypes.byref(dark_mode), ctypes.sizeof(dark_mode)
            )
            dwm.DwmSetWindowAttribute(
                hwnd,
                33,
                ctypes.byref(rounded_corners),
                ctypes.sizeof(rounded_corners),
            )
            dwm.DwmSetWindowAttribute(
                hwnd,
                34,
                ctypes.byref(border_color),
                ctypes.sizeof(border_color),
            )
        except Exception:
            logger.debug(
                "Could not apply native Windows frame styling",
                exc_info=True,
            )

    def _update_recording_state(self):
        """Update UI states based on recording status."""
        # Delegate to quick record tab
        self.quick_record_tab.is_recording = self.is_recording
        self.quick_record_tab._update_recording_state()
        self.compact_controller.set_recording_state(self.is_recording)
        self.compact_controller.set_status(
            "Идёт запись…" if self.is_recording else "Готово к записи"
        )
        self.voice_notes_workspace.set_recording(self.is_recording)

        # Lock/unlock tabs during recording
        if self.is_recording:
            self.tabbed_content.set_recording_state(True, TabbedContentWidget.TAB_QUICK_RECORD)
        else:
            self.tabbed_content.set_recording_state(False, -1)

    def set_status(self, status_text: str):
        """Update the status label on the active tab."""
        # Update the Quick Record tab status
        self.quick_record_tab.set_status(status_text)
        self.compact_controller.set_status(status_text)

    def set_device_info(self, device_info: str):
        """Set the resolved-engine readout on both tabs' Local engine panels.

        Args:
            device_info: Device information string to display.
        """
        for tab in self.transcription_tabs:
            tab.set_device_info(device_info)

    def set_transcript(self, text: str, raw=None):
        """Set the transcription text.

        Args:
            text: Fixed/display transcript.
            raw: Optional unprocessed ASR text when distinct from ``text``.
        """
        self.quick_record_tab.set_transcript(text, raw=raw)
        self.voice_notes_workspace.set_transcript(text)

    def set_transcription_state(
        self, state: str, audio_path: str = "", message: str = ""
    ):
        """Reflect one transcription job across the meeting workspace."""
        self.voice_notes_workspace.set_transcription_state(
            state, audio_path, message
        )

    def append_transcription(self, text: str):
        """Append text to the transcription."""
        self.quick_record_tab.append_transcription(text)

    def clear_transcription(self):
        """Clear the transcription text."""
        self.quick_record_tab.clear_transcription()

    def set_partial_transcription(self, text: str, is_final: bool):
        """Display partial transcription with visual indicator.

        Args:
            text: Partial transcription text
            is_final: Whether this chunk is finalized
        """
        self.quick_record_tab.set_partial_transcription(text, is_final)

    def clear_partial_transcription(self):
        """Clear partial transcription buffer."""
        self.quick_record_tab.clear_partial_transcription()

    def set_transcription_stats(
        self,
        transcription_time: float,
        audio_duration: float,
        file_size: int
    ):
        """Set the transcription statistics display.

        Args:
            transcription_time: Time taken to transcribe in seconds.
            audio_duration: Duration of the audio in seconds.
            file_size: Size of the audio file in bytes.
        """
        self.quick_record_tab.set_transcription_stats(
            transcription_time, audio_duration, file_size
        )

    def clear_transcription_stats(self):
        """Clear and hide the transcription statistics display."""
        self.quick_record_tab.clear_transcription_stats()

    def _on_transcription_collapsed(self, collapsed: bool, delta: int):
        """Reclaim/restore window height when the transcription card toggles.

        Keeps both tabs in the same collapsed state, then animates the window
        height by the freed (or restored) body height so the change feels smooth.

        Args:
            collapsed: True if the card was just collapsed, False if expanded.
            delta: The body height that was hidden/shown, in pixels.
        """
        source = self.sender()
        for tab in self.transcription_tabs:
            if tab is not source:
                tab.set_transcription_collapsed(collapsed)

        current_height = self.height()
        if collapsed:
            if delta <= 0:
                return
            # Shrink by the body height the card gave up, clamped to the floor.
            # Record how much we ACTUALLY freed (the clamp may free less than
            # `delta`) so the matching expand restores precisely that amount.
            # Adding back the raw, elastic body height instead would overshoot
            # the original height and compound on every toggle — the runaway
            # "window keeps getting taller" bug.
            new_height = max(config.MAIN_WINDOW_MIN_HEIGHT, current_height - delta)
            self._collapse_freed_height = current_height - new_height
            self._animate_resize(self.width(), new_height)
        else:
            # Give back exactly what the matching collapse reclaimed. If we have
            # no tracked collapse this session (e.g. the app launched already
            # collapsed), grow once toward the default height instead.
            restore = self._collapse_freed_height
            self._collapse_freed_height = 0
            if restore > 0:
                self._animate_resize(self.width(), current_height + restore)
            elif current_height < config.MAIN_WINDOW_TRANSCRIPTION_EXPAND_HEIGHT:
                self._animate_resize(
                    self.width(), config.MAIN_WINDOW_TRANSCRIPTION_EXPAND_HEIGHT
                )

    def _on_engine_settings_collapsed(self, collapsed: bool, delta: int):
        """Reclaim/restore window height when the Engine Settings panel toggles.

        Keeps both tabs in the same collapsed state, then animates the window
        height by the freed (or restored) body height so the change feels smooth.

        Args:
            collapsed: True if the panel was just collapsed, False if expanded.
            delta: The body height that was hidden/shown, in pixels.
        """
        source = self.sender()
        for tab in self.transcription_tabs:
            if tab is not source:
                tab.set_engine_settings_collapsed(collapsed)

        current_height = self.height()
        if collapsed:
            if delta <= 0:
                return
            new_height = max(config.MAIN_WINDOW_MIN_HEIGHT, current_height - delta)
            self._engine_collapse_freed_height = current_height - new_height
            self._animate_resize(self.width(), new_height)
        else:
            restore = self._engine_collapse_freed_height
            self._engine_collapse_freed_height = 0
            if restore > 0:
                self._animate_resize(self.width(), current_height + restore)
            elif delta > 0:
                self._animate_resize(self.width(), current_height + delta)

    def _on_stats_visibility_changed(self, visible: bool):
        """Handle stats widget visibility change and adjust window height.

        Args:
            visible: True if stats are now visible, False if hidden.
        """
        # Get the stats widget height (approximately 60px when visible)
        stats_height = 60 if visible else 0
        current_height = self.height()

        if visible:
            # Expand window to fit stats
            new_height = current_height + stats_height
        else:
            # Shrink window when stats hidden
            new_height = max(
                config.MAIN_WINDOW_MIN_HEIGHT,
                current_height - stats_height,
            )

        # Animate the height change
        self._animate_resize(self.width(), new_height)

    def get_model_value(self) -> str:
        """Get the model value key."""
        return self.quick_record_tab.get_model_value()

    def open_settings(self):
        """Open settings dialog."""
        logger.info("Opening settings dialog")
        self.settings_requested.emit()

    def open_model_manager(self):
        """Open the Model Manager dialog."""
        logger.info("Opening model manager")
        self.model_manager_requested.emit()

    def open_hotkey_settings(self):
        """Open hotkey settings dialog."""
        logger.info("Opening hotkey settings")
        self.hotkeys_requested.emit()

    def switch_to_quick_record(self):
        """Switch to the Quick Record tab."""
        logger.info("Switching to Quick Record tab")
        self.tabbed_content.set_current_index(TabbedContentWidget.TAB_QUICK_RECORD)

    def show_about(self):
        """Show about dialog."""
        logger.info("Showing about dialog")
        self.about_requested.emit()

    def minimize_to_tray(self):
        """Minimize the window to the system tray."""
        logger.info("Minimizing to tray")
        self.hide()

    def toggle_compact_mode(self) -> None:
        """Toggle between the full workspace and compact recording controller."""
        self.set_compact_mode(not self._compact_mode)

    def set_compact_mode(self, compact: bool, persist: bool = True) -> None:
        """Apply compact or full main-window mode.

        Args:
            compact: Whether to show the compact recording controller.
            persist: Whether to save the selected mode to settings.
        """
        if compact == self._compact_mode:
            return

        if (
            hasattr(self, "_resize_animation")
            and self._resize_animation.state() == QPropertyAnimation.State.Running
        ):
            self._resize_animation.stop()

        if compact:
            if self.isMaximized():
                self.showNormal()
                self.title_bar.sync_window_state(False)

            self._full_geometry = QRect(self.geometry())
            self._save_geometry()
            self._compact_mode = True

            self.tabbed_content.hide()
            self.voice_notes_workspace.hide()
            self.compact_controller.show()
            self.history_edge_tab.hide()
            self.history_sidebar.hide()
            if not self._use_native_window_frame:
                self.title_bar.title_label.hide()
                self.title_bar.maximize_btn.hide()
            self.compact_button.setText("Полный размер")

            self.setMinimumSize(0, 0)
            self.setMaximumSize(UNLIMITED_HEIGHT, UNLIMITED_HEIGHT)
            self.setFixedSize(
                config.MAIN_WINDOW_COMPACT_WIDTH,
                config.MAIN_WINDOW_COMPACT_HEIGHT,
            )
            self._restore_compact_geometry()
        else:
            self._save_compact_geometry()
            self._compact_mode = False

            self.setMinimumSize(
                config.MAIN_WINDOW_MIN_WIDTH,
                config.MAIN_WINDOW_MIN_HEIGHT,
            )
            maximum_width = (
                UNLIMITED_HEIGHT
                if self._use_native_window_frame
                else config.MAIN_WINDOW_MAX_WIDTH
            )
            self.setMaximumSize(maximum_width, UNLIMITED_HEIGHT)
            self.compact_controller.hide()
            self.tabbed_content.hide()
            self.voice_notes_workspace.show()
            self.history_edge_tab.hide()
            self.history_sidebar.hide()
            if not self._use_native_window_frame:
                self.title_bar.title_label.show()
                self.title_bar.maximize_btn.show()
            self.compact_button.setText("Компактный режим")

            if self._full_geometry is not None:
                self.setGeometry(self._full_geometry)
            else:
                self._restore_window_geometry()

        if persist:
            try:
                settings_manager.save_setting(SettingsKey.COMPACT_MODE, compact)
            except Exception as e:
                logger.warning(f"Failed to save compact mode: {e}")

    def _restore_compact_mode(self) -> None:
        """Restore the persisted compact/full mode selection."""
        try:
            if settings_manager.get(SettingsKey.COMPACT_MODE, False) is True:
                self.set_compact_mode(True, persist=False)
        except Exception as e:
            logger.warning(f"Failed to restore compact mode: {e}")

    def _save_compact_geometry(self) -> None:
        """Persist the compact controller position separately from full geometry."""
        geo = self.geometry()
        try:
            settings_manager.save_setting(
                SettingsKey.COMPACT_WINDOW_GEOMETRY,
                {"x": geo.x(), "y": geo.y()},
            )
        except Exception as e:
            logger.warning(f"Failed to save compact window geometry: {e}")

    def _restore_compact_geometry(self) -> None:
        """Restore and clamp the compact controller position to the screen."""
        x = self.x()
        y = self.y()
        try:
            geo = settings_manager.get(SettingsKey.COMPACT_WINDOW_GEOMETRY)
            if isinstance(geo, dict) and {"x", "y"}.issubset(geo):
                x = int(geo["x"])
                y = int(geo["y"])
        except (TypeError, ValueError) as e:
            logger.warning(f"Invalid compact window geometry: {e}")

        from PyQt6.QtWidgets import QApplication

        screen = QApplication.screenAt(self.geometry().center()) or QApplication.primaryScreen()
        if screen:
            available = screen.availableGeometry()
            x = min(max(x, available.left()), available.right() - self.width() + 1)
            y = min(max(y, available.top()), available.bottom() - self.height() + 1)
        self.move(x, y)

    def toggle_tray_visibility(self):
        """Toggle between hidden-to-tray and visible foreground states."""
        if self.isVisible() and not self.isMinimized():
            self.minimize_to_tray()
            return

        self.restore_from_tray()

    def restore_from_tray(self):
        """Reliably bring the window back from the tray / hidden state.

        macOS needs the full clear-minimized + show + raise + activate
        sequence: once an app has no visible windows it is deactivated, so a
        bare showNormal() can leave the window hidden behind other apps (or not
        appear at all). The sequence is harmless on Windows, which restores fine
        from showNormal() alone.
        """
        logger.info("Restoring window from tray")
        # Drop any minimized bit and mark the window active before showing.
        restored_state = (
            self.windowState() & ~Qt.WindowState.WindowMinimized
        ) | Qt.WindowState.WindowActive
        was_maximized = bool(restored_state & Qt.WindowState.WindowMaximized)
        self.setWindowState(restored_state)
        if was_maximized:
            self.showMaximized()
        else:
            self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit_application(self):
        """Quit the application completely (bypasses minimize to tray)."""
        logger.info("Quitting application")
        self._save_geometry()
        self._force_quit = True
        from PyQt6.QtWidgets import QApplication
        QApplication.instance().quit()

    def toggle_history(self):
        """Toggle the history sidebar visibility."""
        logger.info("Toggling history sidebar")

        if self._compact_mode:
            self.set_compact_mode(False)
            if self.history_sidebar.is_expanded:
                return

        # Update the edge tab arrow direction immediately for instant visual feedback
        will_be_expanded = not self.history_sidebar.is_expanded
        self.history_edge_tab.set_expanded(will_be_expanded)

        # A running height animation would fight the per-frame lockstep resize.
        if (
            hasattr(self, '_resize_animation')
            and self._resize_animation.state() == QPropertyAnimation.State.Running
        ):
            self._resize_animation.stop()

        # Capture the width of everything except the sidebar so each animation
        # frame can hold the main content area at a constant width. Works
        # mid-animation too: the sidebar's current width is subtracted out.
        self._sidebar_base_width = self.width() - self.history_sidebar.width()
        self._collapsed_width = max(self.minimumWidth(), self._sidebar_base_width)

        # The sidebar's single animation drives the window width via
        # width_animated -> _on_sidebar_width_animated.
        self.history_sidebar.toggle()

        self.history_toggle_requested.emit()

    def _on_sidebar_width_animated(self, sidebar_width: int):
        """Resize the window in lockstep with the sidebar width animation.

        Args:
            sidebar_width: Current animated width of the history sidebar.
        """
        base = getattr(self, '_sidebar_base_width', None)
        if base is None:
            return

        target_width = min(self.maximumWidth(), base + sidebar_width)
        geo = self.geometry()
        self.setGeometry(geo.x(), geo.y(), target_width, geo.height())

    def _animate_resize(self, target_width: int, target_height: int):
        """Animate window resize.

        Args:
            target_width: Target window width.
            target_height: Target window height.
        """
        from PyQt6.QtCore import QRect

        if not hasattr(self, '_resize_animation'):
            self._resize_animation = QPropertyAnimation(self, b"geometry")
            self._resize_animation.setDuration(SECTION_COLLAPSE_DURATION_MS)
            self._resize_animation.setEasingCurve(SECTION_COLLAPSE_EASING)

        current_geo = self.geometry()
        target_geo = QRect(current_geo.x(), current_geo.y(), target_width, target_height)

        # Continue smoothly from the current frame when interrupting a resize.
        if self._resize_animation.state() == QPropertyAnimation.State.Running:
            current_geo = self._resize_animation.currentValue()

        self._resize_animation.stop()
        self._resize_animation.setDuration(SECTION_COLLAPSE_DURATION_MS)
        self._resize_animation.setEasingCurve(SECTION_COLLAPSE_EASING)
        self._resize_animation.setStartValue(current_geo)
        self._resize_animation.setEndValue(target_geo)
        self._resize_animation.start()

    def refresh_history(self):
        """Refresh the history sidebar content."""
        self.history_sidebar.refresh()
        self.voice_notes_workspace.refresh_history()

    def _on_history_entry_selected(self, entry_id: str):
        """Open the history entry viewer dialog for the selected tile."""
        entry = history_manager.get_entry_by_id(entry_id)
        if not entry:
            return

        dialog = HistoryEntryDialog(entry, parent=self)
        dialog.copied.connect(self._on_history_entry_copied_from_dialog)
        dialog.retranscribe_requested.connect(self._on_retranscribe_requested)
        dialog.delete_requested.connect(self._on_history_entry_delete_requested)
        dialog.exec()
        logger.info(f"Opened history entry dialog: {entry_id[:8]}...")

    def _on_history_entry_copied_from_dialog(self):
        """Handle copy from the history entry dialog."""
        self.set_status("Скопировано")
        QTimer.singleShot(2000, lambda: self.set_status("Готово к записи"))
        if self.on_show_copied_animation:
            self.on_show_copied_animation()

    def _on_history_entry_delete_requested(self, entry_id: str):
        """Delete a history entry requested from the viewer dialog."""
        if history_manager.delete_entry(entry_id):
            self.refresh_history()
            self._on_history_entry_deleted(entry_id)
            logger.info(f"Deleted history entry from dialog: {entry_id[:8]}...")

    def _on_history_entry_copied(self, entry_id: str):
        """Handle history entry copied notification."""
        self.set_status("Скопировано")
        # Auto-clear status after delay
        QTimer.singleShot(2000, lambda: self.set_status("Готово к записи"))

    def _on_history_entry_deleted(self, entry_id: str):
        """Handle history entry deleted notification."""
        self.set_status("Запись удалена")
        # Auto-clear status after delay
        QTimer.singleShot(2000, lambda: self.set_status("Готово к записи"))

    def _on_retranscribe_requested(self, audio_path: str):
        """Handle re-transcription request for a saved recording."""
        logger.info("Re-transcribe requested: %s", audio_path)
        self.retranscribe_requested.emit(audio_path)

    def closeEvent(self, event):
        """Handle window close event."""
        logger.info("Main window closing")
        self.tabbed_content.flush_pending_tab_selection()
        # If force quit is set, close immediately
        if self._force_quit:
            logger.info("Force quit - closing application")
            event.accept()
            return

        # Check if minimize to tray is enabled (default: True)
        try:
            settings = settings_manager.load_all_settings()
            minimize_tray = settings.get(SettingsKey.MINIMIZE_TRAY, True)  # Default to True
        except Exception as e:
            logger.error(f"Failed to load settings: {e}")
            minimize_tray = True  # Default to True on error

        if minimize_tray:
            # Hide window instead of closing (X button behavior)
            event.ignore()
            try:
                self.hide()
                logger.info("Window hidden to system tray")
            except Exception as e:
                logger.debug(f"Error hiding window: {e}")
                # If hiding fails, accept the close event
                event.accept()
        else:
            # Close normally
            event.accept()

    def update_hotkeys(
        self,
        record_key: str,
        cancel_key: str,
        enable_disable_key: str = "",
        minimize_key: str = "",
    ):
        """
        Update the hotkey display on buttons.

        Args:
            record_key: The key for recording
            cancel_key: The key for canceling
            enable_disable_key: The key for enabling/disabling STT
            minimize_key: The key for minimizing to the system tray
        """
        self.quick_record_tab.update_hotkeys(record_key, cancel_key, enable_disable_key)
        self.compact_controller.update_hotkeys(record_key, cancel_key)
        self.tray_button.set_hotkey(minimize_key)

    # ==================== Edge Resize Support ====================

    def _get_resize_edge(self, pos) -> tuple:
        """Determine which edge(s) the cursor is near.

        Args:
            pos: QPoint position relative to window.

        Returns:
            Tuple of (horizontal_edge, vertical_edge) where each is:
            -1 for left/top, 0 for none, 1 for right/bottom.
        """
        if self._compact_mode or self._use_native_window_frame:
            return (0, 0)

        rect = self.rect()
        margin = self._resize_margin

        horizontal = 0  # -1 = left, 0 = none, 1 = right
        vertical = 0    # -1 = top, 0 = none, 1 = bottom

        if pos.x() <= margin:
            horizontal = -1
        elif pos.x() >= rect.width() - margin:
            horizontal = 1

        if pos.y() <= margin:
            vertical = -1
        elif pos.y() >= rect.height() - margin:
            vertical = 1

        return (horizontal, vertical)

    def _update_cursor_for_edge(self, edge: tuple):
        """Update cursor shape based on edge.

        Args:
            edge: Tuple of (horizontal, vertical) edge flags.
        """
        from PyQt6.QtGui import QCursor

        h, v = edge

        if h == 0 and v == 0:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        elif h != 0 and v == 0:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif h == 0 and v != 0:
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        elif (h == -1 and v == -1) or (h == 1 and v == 1):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        else:  # (h == -1 and v == 1) or (h == 1 and v == -1)
            self.setCursor(Qt.CursorShape.SizeBDiagCursor)

    def _begin_resize(self, edge: tuple, global_pos) -> None:
        """Start a resize operation from a given edge and global position."""
        self._resizing = True
        self._resize_edge = edge
        self._resize_start_pos = global_pos
        self._resize_start_geometry = self.geometry()

    def _apply_resize_delta(self, global_pos) -> None:
        """Apply resize based on the stored start geometry and a global cursor position."""
        if not self._resizing or not self._resize_edge or not self._resize_start_geometry:
            return

        delta = global_pos - self._resize_start_pos
        geo = self._resize_start_geometry
        h, v = self._resize_edge

        new_x = geo.x()
        new_y = geo.y()
        new_width = geo.width()
        new_height = geo.height()

        # Handle horizontal resize
        if h == -1:  # Left edge
            new_width = max(self.minimumWidth(), geo.width() - delta.x())
            new_x = geo.x() + geo.width() - new_width
        elif h == 1:  # Right edge
            new_width = min(self.maximumWidth(), max(self.minimumWidth(), geo.width() + delta.x()))

        # Handle vertical resize
        if v == -1:  # Top edge
            new_height = max(self.minimumHeight(), geo.height() - delta.y())
            new_y = geo.y() + geo.height() - new_height
        elif v == 1:  # Bottom edge
            new_height = max(self.minimumHeight(), geo.height() + delta.y())

        self.setGeometry(new_x, new_y, new_width, new_height)

    def _finish_resize(self) -> None:
        """Finish a resize operation and persist geometry."""
        if not self._resizing:
            return
        self._resizing = False
        self._resize_edge = None
        self._resize_start_pos = None
        self._resize_start_geometry = None
        self._schedule_geometry_save()

    def mousePressEvent(self, event):
        """Handle mouse press for edge resize."""
        if event.button() == Qt.MouseButton.LeftButton:
            edge = self._get_resize_edge(event.position().toPoint())
            if edge != (0, 0):
                self._begin_resize(edge, event.globalPosition().toPoint())
                event.accept()
                return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """Handle mouse move for resize cursor and resizing."""
        if self._resizing and self._resize_edge:
            self._apply_resize_delta(event.globalPosition().toPoint())
            event.accept()
            return

        # Update cursor based on edge proximity
        edge = self._get_resize_edge(event.position().toPoint())
        self._update_cursor_for_edge(edge)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        """Handle mouse release to end resize."""
        if event.button() == Qt.MouseButton.LeftButton and self._resizing:
            self._finish_resize()
            event.accept()
            return

        super().mouseReleaseEvent(event)

    # ==================== Geometry Persistence ====================

    def _schedule_geometry_save(self):
        """Schedule geometry save with debounce to avoid excessive writes."""
        if self._geometry_save_timer is None:
            self._geometry_save_timer = QTimer(self)
            self._geometry_save_timer.setSingleShot(True)
            self._geometry_save_timer.timeout.connect(self._save_geometry)

        # Reset timer on each call (debounce)
        self._geometry_save_timer.stop()
        self._geometry_save_timer.start(500)  # Save 500ms after last change

    def _save_geometry(self):
        """Save current window geometry to settings."""
        if self.isMaximized() or self.isMinimized():
            return  # Don't save maximized/minimized state

        if self._compact_mode:
            self._save_compact_geometry()
            return

        geo = self.geometry()
        width = geo.width()
        history_expanded = (
            hasattr(self, "history_sidebar") and self.history_sidebar.is_expanded
        )
        if history_expanded:
            width = max(self.minimumWidth(), width - self._sidebar_width)
        self._collapsed_width = width

        try:
            settings_manager.save_setting(
                SettingsKey.WINDOW_GEOMETRY,
                {
                    'x': geo.x(),
                    'y': geo.y(),
                    'width': width,
                    'height': geo.height(),
                    'format': self._geometry_format,
                    'history_expanded': history_expanded,
                },
            )
        except Exception as e:
            logger.warning(f"Failed to save window geometry: {e}")

    def _restore_window_geometry(self):
        """Restore window geometry from settings."""
        try:
            geo = settings_manager.get(SettingsKey.WINDOW_GEOMETRY)
            if isinstance(geo, dict) and geo.get('format') != self._geometry_format:
                geo = None
            if isinstance(geo, dict) and {'x', 'y', 'width', 'height'}.issubset(geo.keys()):
                # Validate geometry is within screen bounds
                from PyQt6.QtWidgets import QApplication
                from PyQt6.QtCore import QRect

                screen = QApplication.primaryScreen()
                if screen:
                    screen_geo = screen.availableGeometry()
                    # Check if saved position is at least partially on screen
                    saved_rect = QRect(geo['x'], geo['y'], geo['width'], geo['height'])
                    if screen_geo.intersects(saved_rect):
                        raw_width = geo['width']
                        migrated_expanded_width = False
                        legacy_expanded_width = (
                            config.MAIN_WINDOW_DEFAULT_WIDTH
                            + config.MAIN_WINDOW_HISTORY_SIDEBAR_WIDTH
                            - config.MAIN_WINDOW_HISTORY_EDGE_TAB_WIDTH
                        )
                        if (
                            geo.get('format') != self._geometry_format
                            and raw_width >= legacy_expanded_width
                        ):
                            raw_width -= config.MAIN_WINDOW_HISTORY_SIDEBAR_WIDTH
                            migrated_expanded_width = True

                        # Ensure size constraints - both min and max for width and height
                        width = max(self.minimumWidth(), min(raw_width, self.maximumWidth()))
                        max_height = screen_geo.height()

                        # The transcript starts collapsed, so geometry saved while it
                        # was expanded must not leave its now-hidden body as blank
                        # vertical space. Apply this independently of window width
                        # (users can resize the main workspace horizontally).
                        transcript_collapsed = (
                            hasattr(self, "transcription_tabs")
                            and all(
                                tab.is_transcription_collapsed()
                                for tab in self.transcription_tabs
                            )
                        )
                        if transcript_collapsed:
                            max_height = min(
                                max_height,
                                config.MAIN_WINDOW_COLLAPSED_RESTORE_MAX_HEIGHT,
                            )

                        # Normalize narrow and legacy sidebar-width restores.
                        if width <= config.MAIN_WINDOW_DEFAULT_WIDTH or migrated_expanded_width:
                            width = config.MAIN_WINDOW_DEFAULT_WIDTH
                        height = max(self.minimumHeight(), min(geo['height'], max_height))
                        self._collapsed_width = width
                        restore_width = width
                        if (
                            hasattr(self, "history_sidebar")
                            and self.history_sidebar.is_expanded
                        ):
                            restore_width = min(
                                self.maximumWidth(),
                                width + self._sidebar_width,
                            )
                        self.setGeometry(geo['x'], geo['y'], restore_width, height)
                        logger.info(f"Restored window geometry: {geo}")
                        return

            # New workspaces should open in the centre, not pinned to a
            # previous compact-era corner position.
            from PyQt6.QtWidgets import QApplication
            screen = QApplication.primaryScreen()
            if screen:
                self.move(screen.availableGeometry().center() - self.rect().center())
            logger.debug("No valid saved geometry, using centred default")
        except Exception as e:
            logger.warning(f"Failed to restore window geometry: {e}")

    def resizeEvent(self, event):
        """Handle resize event to save geometry."""
        super().resizeEvent(event)
        if not self._resizing:  # Don't save during active drag resize (already handled)
            self._schedule_geometry_save()

    def moveEvent(self, event):
        """Handle move event to save geometry."""
        super().moveEvent(event)
        self._schedule_geometry_save()

    def changeEvent(self, event):
        """Synchronize custom controls with native maximize/restore actions."""
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self.title_bar.sync_window_state(self.isMaximized())

    def showEvent(self, event):
        """Handle show event - restore geometry when showing from tray."""
        super().showEvent(event)

        # Skip geometry restoration on initial show (already handled in __init__)
        # This prevents interference with Qt's initial layout calculation
        if not self._initial_show_complete:
            self._initial_show_complete = True
            return

        # Re-apply saved geometry when restoring from tray (subsequent shows)
        if not self.isMaximized():
            if self._compact_mode:
                self._restore_compact_geometry()
            else:
                self._restore_window_geometry()

    def eventFilter(self, obj, event):
        """Filter events to update resize cursor when hovering near edges."""
        if event.type() == QEvent.Type.MouseMove and not self._resizing:
            # Check if event has position info and is within our window
            if hasattr(event, 'globalPosition'):
                global_pos = event.globalPosition().toPoint()
                local_pos = self.mapFromGlobal(global_pos)

                # Only update cursor if mouse is within window bounds
                if self.rect().contains(local_pos):
                    edge = self._get_resize_edge(local_pos)
                    self._update_cursor_for_edge(edge)

        return super().eventFilter(obj, event)
