"""Three-column, note-centric screen for local recordings."""
from PyQt6.QtCore import (
    QFileSystemWatcher,
    QRectF,
    Qt,
    pyqtSignal,
    QUrl,
    QTimer,
    QSize,
)
from PyQt6.QtGui import (
    QColor,
    QDesktopServices,
    QPainter,
    QPen,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QFont,
)
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtWidgets import QListWidgetItem
import math
import os
import re
import threading
import time
import wave
from PyQt6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QLabel,
    QPushButton, QLineEdit, QComboBox, QFrame, QTextEdit, QCheckBox,
    QListWidget, QStyle, QStackedWidget, QSizePolicy, QMessageBox, QMenu,
    QStyledItemDelegate, QStyleOptionViewItem, QInputDialog)
import qtawesome as qta
from config import config
from services.codex_cleanup import (
    CodexCleanupMode,
    extract_original_transcript,
)
from services.history_manager import NO_SPEECH_TRANSCRIPT, history_manager
from services.hf_access import is_model_cached
from services.format_utils import format_file_size, format_timestamp
from services.settings import (
    settings_manager,
    SettingsKey,
    resolve_codex_cleanup_enabled,
)


class WaveformWidget(QWidget):
    """Audio-shaped playback timeline with a played/unplayed fill."""

    seek_requested = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Нажмите на волну, чтобы перемотать запись")
        self.setAccessibleName("Шкала воспроизведения записи")
        self.levels = self.placeholder_levels("meeting")
        self.progress = 0.0
        self.played_color = "#1769e0"
        self.unplayed_color = "#cbd5e1"

    @staticmethod
    def placeholder_levels(seed):
        """Return a stable, natural-looking shape while audio is analysed."""
        value = sum((index + 1) * ord(char) for index, char in enumerate(seed or ""))
        phase = (value % 31) / 7
        return [
            max(
                0.08,
                min(
                    0.88,
                    0.16
                    + 0.30 * abs(math.sin(index * 0.43 + phase))
                    + 0.20 * abs(math.sin(index * 0.17 + phase * 0.7))
                    + 0.12 * abs(math.sin(index * 0.91 + 1.3)),
                ),
            )
            for index in range(96)
        ]

    def set_levels(self, levels):
        if levels:
            self.levels = list(levels)
            self.update()

    def set_progress(self, progress):
        resolved = max(0.0, min(1.0, float(progress or 0.0)))
        if not math.isclose(resolved, self.progress, abs_tol=0.0005):
            self.progress = resolved
            self.update()

    def set_color(self, color):
        self.played_color = color
        self.update()

    def set_unplayed_color(self, color):
        self.unplayed_color = color
        self.update()

    def _draw_bars(self, painter, color):
        width = max(1.0, self.width() - 6.0)
        center = self.height() / 2.0
        count = max(1, len(self.levels))
        stride = 5.0
        bar_width = 2.6
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        x = 3.0
        while x < width:
            value_index = min(
                count - 1,
                int(((x - 3.0) / max(1.0, width - 3.0)) * count),
            )
            value = max(0.0, min(1.0, self.levels[value_index]))
            half_height = 2.0 + value * max(4.0, center - 4.0)
            bar = QRectF(
                x,
                center - half_height,
                bar_width,
                half_height * 2.0,
            )
            painter.drawRoundedRect(bar, bar_width / 2.0, bar_width / 2.0)
            x += stride

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._draw_bars(painter, self.unplayed_color)
        if self.progress > 0:
            painter.save()
            painter.setClipRect(
                QRectF(0, 0, self.width() * self.progress, self.height())
            )
            self._draw_bars(painter, self.played_color)
            painter.restore()

    def mousePressEvent(self, event):
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.width() > 0
        ):
            self.seek_requested.emit(event.position().x() / self.width())
            event.accept()
            return
        super().mousePressEvent(event)


class MeetingListDelegate(QStyledItemDelegate):
    """Paint filenames as titles and keep file facts visually secondary."""

    def sizeHint(self, option, index):
        return QSize(super().sizeHint(option, index).width(), 76)

    def paint(self, painter, option, index):
        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        lines = styled.text.splitlines()
        title = lines[0] if lines else ""
        metadata = "  ".join(lines[1:])
        styled.text = ""
        style = styled.widget.style() if styled.widget else None
        if style is not None:
            style.drawControl(
                QStyle.ControlElement.CE_ItemViewItem,
                styled,
                painter,
                styled.widget,
            )

        workspace = self.parent()
        dark = bool(getattr(workspace, "dark", False))
        title_color = QColor("#f3f6fb" if dark else "#18202b")
        metadata_color = QColor("#a8b4c7" if dark else "#6f7b8d")
        content = option.rect.adjusted(18, 9, -14, -9)

        title_font = QFont(option.font)
        title_font.setPixelSize(15)
        title_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(title_font)
        painter.setPen(title_color)
        title_text = painter.fontMetrics().elidedText(
            title,
            Qt.TextElideMode.ElideRight,
            content.width(),
        )
        painter.drawText(
            content.adjusted(0, 0, 0, -24),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            title_text,
        )

        metadata_font = QFont(option.font)
        metadata_font.setPixelSize(12)
        painter.setFont(metadata_font)
        painter.setPen(metadata_color)
        metadata_text = painter.fontMetrics().elidedText(
            metadata,
            Qt.TextElideMode.ElideRight,
            content.width(),
        )
        painter.drawText(
            content.adjusted(0, 27, 0, 0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            metadata_text,
        )


class ElidedLabel(QLabel):
    """Preserve the full title while drawing a clean trailing ellipsis."""

    def setText(self, text):
        value = str(text or "")
        super().setText(value)
        self.setToolTip(value)
        self.setAccessibleName(value)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setFont(self.font())
        painter.setPen(self.palette().windowText().color())
        text = self.fontMetrics().elidedText(
            self.text(),
            Qt.TextElideMode.ElideRight,
            max(0, self.contentsRect().width()),
        )
        painter.drawText(
            self.contentsRect(),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            text,
        )


class VoiceNotesWorkspace(QWidget):
    SORT_NEWEST = "newest"
    SORT_OLDEST = "oldest"
    SORT_SIZE = "size"
    SORT_DURATION = "duration"

    record_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    cancel_requested = pyqtSignal()
    transcribe_requested = pyqtSignal(str)
    model_selected = pyqtSignal(str)
    theme_changed = pyqtSignal(str)
    settings_requested = pyqtSignal()
    devices_requested = pyqtSignal()
    models_requested = pyqtSignal()
    codex_improve_requested = pyqtSignal(str, str, str, str)
    media_duration_ready = pyqtSignal(str, float)
    media_waveform_ready = pyqtSignal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("voiceNotesWorkspace")
        self.recording = False
        self.dark = False
        self._page_widgets = {}
        self._nav_buttons = {}
        self._selected_audio_path = ""
        self._selected_media_path = ""
        self._selected_history_id = ""
        self._selected_transcript_text = ""
        self._selected_original_text = ""
        self._selected_enhanced_by_codex = False
        self._waveform_cache = {}
        self._waveform_threads = set()
        self._waveform_threads_lock = threading.Lock()
        self._library_loaded = False
        self._transcription_state = "idle"
        self._active_transcription_path = ""
        self._transcription_error = ""
        self._record_started_at = 0.0
        self._record_timer = QTimer(self)
        self._record_timer.setInterval(250)
        self._record_timer.timeout.connect(self._update_recording_timer)
        self._library_snapshot = {}
        self._library_watcher = QFileSystemWatcher(self)
        self._library_watcher.directoryChanged.connect(
            self._schedule_external_library_refresh
        )
        self._library_watcher.fileChanged.connect(
            self._schedule_external_library_refresh
        )
        self._library_refresh_timer = QTimer(self)
        self._library_refresh_timer.setSingleShot(True)
        self._library_refresh_timer.setInterval(300)
        self._library_refresh_timer.timeout.connect(
            self._refresh_external_library_changes
        )
        self._library_poll_timer = QTimer(self)
        self._library_poll_timer.setInterval(2000)
        self._library_poll_timer.timeout.connect(
            self._poll_external_library_changes
        )
        self._audio_output = QAudioOutput(self)
        self._media_player = QMediaPlayer(self)
        self._media_player.setAudioOutput(self._audio_output)
        self._media_player.positionChanged.connect(
            self._on_media_position_changed
        )
        self._media_player.durationChanged.connect(self._on_media_duration_changed)
        self._media_player.playbackStateChanged.connect(self._update_play_button)
        self.media_duration_ready.connect(self._apply_probed_media_duration)
        self.media_waveform_ready.connect(self._apply_media_waveform)
        self._build()
        self._apply_theme()
        self.search.hide()
        self.sort_button.hide()
        self._show_no_selection()
        # Reading every transcript and recursively scanning the recordings
        # folder is useful, but not required to paint the first window.
        QTimer.singleShot(100, self._initial_library_load)

    def _icon(self, kind, color="#1769e0"):
        # One coherent Font Awesome solid icon family; never fall back to the
        # platform's mixed Qt stock icons.
        names = {
            "SP_FileIcon": "fa6s.video", "SP_MediaVolume": "fa6s.microphone",
            "SP_DriveHDIcon": "fa6s.brain", "SP_FileDialogDetailedView": "fa6s.gear",
            "SP_DesktopIcon": "fa6s.circle-half-stroke", "SP_FileDialogContentsView": "fa6s.sliders",
            "SP_TitleBarMenuButton": "fa6s.ellipsis", "SP_MediaPlay": "fa6s.play",
            "SP_DirOpenIcon": "fa6s.folder-open",
        }
        return qta.icon(names.get(kind.name, "fa6s.circle"), color=color)

    def _action_button(
        self,
        *,
        object_name,
        icon_name,
        label,
        tone="accent",
        callback=None,
    ):
        """Create a compact, accessible action using the shared icon family."""
        button = QPushButton()
        button.setObjectName(object_name)
        button.setProperty("iconName", icon_name)
        button.setProperty("iconTone", tone)
        button.setIconSize(QSize(18, 18))
        button.setFixedSize(42, 42)
        button.setToolTip(label)
        button.setAccessibleName(label)
        if callback:
            button.clicked.connect(callback)
        return button

    def _nav_button(self, label, icon, callback=None):
        button = QPushButton(label)
        button.setIcon(self._icon(icon))
        button.setProperty("iconKind", icon.name)
        button.setObjectName("navButton")
        button.setMinimumHeight(44)
        if callback:
            button.clicked.connect(callback)
        return button

    def _build(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        nav = QFrame(); nav.setObjectName("nav"); nav.setFixedWidth(196)
        nav_layout = QVBoxLayout(nav); nav_layout.setContentsMargins(20, 22, 14, 16); nav_layout.setSpacing(8)
        self.records_button = self._nav_button("Встречи", QStyle.StandardPixmap.SP_FileIcon)
        self.records_button.clicked.connect(lambda: self.show_page("records"))
        self.records_button.setProperty("active", True); nav_layout.addWidget(self.records_button)
        self.devices_button = self._nav_button("Устройства", QStyle.StandardPixmap.SP_MediaVolume, self.devices_requested)
        self.models_button = self._nav_button("Модели", QStyle.StandardPixmap.SP_DriveHDIcon, self.models_requested)
        self.settings_button = self._nav_button("Настройки", QStyle.StandardPixmap.SP_FileDialogDetailedView, self.settings_requested)
        nav_layout.addWidget(self.devices_button)
        nav_layout.addWidget(self.models_button)
        nav_layout.addWidget(self.settings_button)
        self._nav_buttons = {
            "records": self.records_button,
            "devices": self.devices_button,
            "models": self.models_button,
            "settings": self.settings_button,
        }
        nav_layout.addStretch()
        self.theme_button = self._nav_button("", QStyle.StandardPixmap.SP_DesktopIcon, self.toggle_theme)
        self.theme_button.setObjectName("themeButton")
        self.theme_button.setFixedSize(42, 42)
        self.theme_button.setToolTip("Включить тёмную тему")
        self.theme_button.setAccessibleName("Включить тёмную тему")
        nav_layout.addWidget(
            self.theme_button,
            alignment=Qt.AlignmentFlag.AlignLeft,
        )
        root.addWidget(nav)

        self.content_stack = QStackedWidget()
        self.content_stack.setObjectName("workspaceStack")
        records_page = QWidget()
        records_page.setObjectName("recordsPage")
        records_layout = QHBoxLayout(records_page)
        records_layout.setContentsMargins(0, 0, 0, 0)
        records_layout.setSpacing(0)

        listing = QFrame(); listing.setObjectName("list"); listing.setFixedWidth(388)
        list_layout = QVBoxLayout(listing); list_layout.setContentsMargins(24, 30, 20, 22); list_layout.setSpacing(16)
        header = QHBoxLayout(); title = QLabel("Все встречи"); title.setObjectName("sectionTitle"); header.addWidget(title); header.addStretch()
        list_layout.addLayout(header)
        self.search_row = QHBoxLayout()
        self.search_row.setSpacing(8)
        self.search = QLineEdit(); self.search.setPlaceholderText("Поиск во встречах и тексте"); self.search.setAccessibleName("Поиск по названиям встреч и расшифровкам"); self.search.setToolTip("Искать в названиях встреч и текстах расшифровок"); self.search.setClearButtonEnabled(True); self.search.textChanged.connect(self._filter_notes); self.search.setMinimumHeight(42); self.search_row.addWidget(self.search, 1)
        self.sort = QComboBox()
        self.sort.setAccessibleName("Сортировка встреч")
        self.sort.addItem("Сначала новые", self.SORT_NEWEST)
        self.sort.addItem("Сначала старые", self.SORT_OLDEST)
        self.sort.addItem("Сначала крупные", self.SORT_SIZE)
        self.sort.addItem("Сначала длинные", self.SORT_DURATION)
        self.sort.setToolTip("Сортировать встречи по дате, размеру файла или длительности")
        self.sort.currentIndexChanged.connect(self._on_sort_changed)
        # Keep a lightweight combo as the state model for keyboard/tests, but
        # present sorting as the requested icon menu beside search.
        self.sort.hide()
        self.sort_button = self._action_button(
            object_name="sortMeetingsButton",
            icon_name="fa6s.arrow-down-wide-short",
            label="Сортировка: Сначала новые",
        )
        self.sort_menu = QMenu(self.sort_button)
        self._sort_actions = []
        for index in range(self.sort.count()):
            action = self.sort_menu.addAction(self.sort.itemText(index))
            action.setCheckable(True)
            action.setChecked(index == self.sort.currentIndex())
            action.triggered.connect(
                lambda _checked=False, selected_index=index:
                self.sort.setCurrentIndex(selected_index)
            )
            self._sort_actions.append(action)
        self.sort_button.setMenu(self.sort_menu)
        self._align_action_menu(self.sort_button, self.sort_menu)
        self.search_row.addWidget(self.sort_button)
        list_layout.addLayout(self.search_row)
        self.notes = QListWidget(); self.notes.setObjectName("notes")
        self.notes.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.notes.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.notes.setWordWrap(False)
        self.notes.setItemDelegate(MeetingListDelegate(self))
        self.notes.currentItemChanged.connect(self._select_note)
        list_layout.addWidget(self.notes, 1); records_layout.addWidget(listing)

        main = QWidget(); main.setObjectName("main"); layout = QVBoxLayout(main); layout.setContentsMargins(40, 32, 48, 32); layout.setSpacing(20)
        top = QHBoxLayout(); top.setSpacing(8); self.note_name = ElidedLabel("Новая встреча"); self.note_name.setObjectName("noteName"); self.note_name.setMinimumWidth(0); self.note_name.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred); top.addWidget(self.note_name, 1)
        self.open_media_button = self._action_button(
            object_name="openMediaButton",
            icon_name="fa6s.arrow-up-right-from-square",
            label="Открыть запись встречи",
            callback=self._open_selected_media,
        )
        self.open_media_button.hide()
        top.addWidget(self.open_media_button)
        self.codex_improve_button = self._action_button(
            object_name="codexImproveButton",
            icon_name="fa6s.arrows-rotate",
            label="Переделать расшифровку и итоги",
        )
        self.codex_improve_button.setToolTip(
            "Переделать расшифровку и итоги без повторного распознавания"
        )
        self.codex_improve_button.setAccessibleName(
            "Переделать расшифровку и итоги через Codex"
        )
        self.codex_improve_menu = QMenu(self.codex_improve_button)
        self._codex_mode_actions = []
        mode_icons = {
            CodexCleanupMode.BRIEF: "fa6s.bolt",
            CodexCleanupMode.FULL: "fa6s.list-check",
            CodexCleanupMode.FULL_WITH_ORIGINAL: "fa6s.file-lines",
        }
        for mode in CodexCleanupMode.ALL:
            action = self.codex_improve_menu.addAction(
                CodexCleanupMode.LABELS[mode]
            )
            action.setProperty("iconName", mode_icons[mode])
            action.triggered.connect(
                lambda _checked=False, selected_mode=mode:
                self._request_codex_improvement(selected_mode)
            )
            self._codex_mode_actions.append(action)
        self.codex_improve_button.setMenu(self.codex_improve_menu)
        self._align_action_menu(
            self.codex_improve_button,
            self.codex_improve_menu,
        )
        self.codex_improve_button.hide()
        top.addWidget(self.codex_improve_button)
        self.rename_button = self._action_button(
            object_name="renameMeetingButton",
            icon_name="fa6s.pen",
            label="Переименовать встречу",
            callback=self._rename_selected_meeting,
        )
        self.rename_button.hide()
        top.addWidget(self.rename_button)
        self.trash_button = self._action_button(
            object_name="trashMeetingButton",
            icon_name="fa6s.trash-can",
            label="Переместить встречу в корзину",
            tone="danger",
            callback=self._move_selected_to_trash,
        )
        self.trash_button.setToolTip(
            "Переместить всю встречу и её расшифровки в корзину"
        )
        self.trash_button.hide()
        top.addWidget(self.trash_button)
        layout.addLayout(top)
        self.player = QFrame(); self.player.setObjectName("player"); player_layout = QHBoxLayout(self.player); player_layout.setContentsMargins(10, 10, 10, 10); player_layout.setSpacing(12)
        self.play_button = QPushButton(); self.play_button.setIcon(self._icon(QStyle.StandardPixmap.SP_MediaPlay)); self.play_button.setObjectName("playButton"); self.play_button.setAccessibleName("Воспроизвести"); self.play_button.clicked.connect(self._toggle_playback); player_layout.addWidget(self.play_button)
        self.elapsed_label = QLabel("00:00"); self.elapsed_label.setMinimumWidth(42); self.elapsed_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter); player_layout.addWidget(self.elapsed_label)
        self.waveform = WaveformWidget(); self.waveform.seek_requested.connect(self._seek_playback); player_layout.addWidget(self.waveform, 1)
        self.duration_label = QLabel("00:00"); self.duration_label.setMinimumWidth(42); self.duration_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter); player_layout.addWidget(self.duration_label); layout.addWidget(self.player)
        self.source = QLabel("Выберите встречу слева"); self.source.setObjectName("source"); layout.addWidget(self.source)

        self.empty = QWidget(); self.empty.setObjectName("empty"); empty = QVBoxLayout(self.empty); empty.setAlignment(Qt.AlignmentFlag.AlignCenter); empty.setSpacing(14)
        self.empty_icon = QLabel()
        self.empty_icon.setPixmap(
            qta.icon("fa6s.file-lines", color="#1769e0").pixmap(48, 48)
        )
        self.empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty.addWidget(self.empty_icon)
        self.empty_title = QLabel("Расшифровки нет"); self.empty_title.setObjectName("emptyTitle"); self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter); empty.addWidget(self.empty_title)
        self.empty_desc = QLabel("Выберите модель и запустите расшифровку"); self.empty_desc.setObjectName("muted"); self.empty_desc.setAlignment(Qt.AlignmentFlag.AlignCenter); self.empty_desc.setWordWrap(True); self.empty_desc.setFixedWidth(354); empty.addWidget(self.empty_desc)
        empty.addSpacing(18); self.model_label = QLabel("Модель"); self.model_label.setObjectName("fieldLabel"); empty.addWidget(self.model_label)
        self.model = QComboBox(); self.model.setAccessibleName("Модель расшифровки"); self._populate_installed_models(); self.model.currentIndexChanged.connect(lambda: self.model_selected.emit(self.model.currentData())); self.model.setFixedWidth(354); empty.addWidget(self.model)
        self.transcribe = QPushButton("Расшифровать"); self.transcribe.setObjectName("primary"); self.transcribe.setFixedWidth(354); self.transcribe.clicked.connect(self._request_transcription); empty.addWidget(self.transcribe)
        self.transcribe.setIconSize(QSize(14, 14))
        self._set_transcribe_button_icon("ready")
        self.folder_button = QPushButton("Открыть папку"); self.folder_button.setIcon(self._icon(QStyle.StandardPixmap.SP_DirOpenIcon)); self.folder_button.setObjectName("linkButton"); self.folder_button.setFixedWidth(354); self.folder_button.clicked.connect(self._open_recording_folder); empty.addWidget(self.folder_button)
        layout.addWidget(self.empty, 1)
        self.transcript = QTextEdit(); self.transcript.setReadOnly(True); self.transcript.hide(); layout.addWidget(self.transcript, 1)
        self.recording_bar = QFrame()
        self.recording_bar.setObjectName("recordingBar")
        self.recording_bar.setMinimumHeight(62)
        bottom = QHBoxLayout(self.recording_bar)
        bottom.setContentsMargins(14, 10, 14, 10)
        bottom.setSpacing(12)
        self.screen = QCheckBox("Экран и звук")
        self.screen.setChecked(settings_manager.load_all_settings().get(SettingsKey.VIDEO_RECORDING_ENABLED, config.VIDEO_RECORDING_ENABLED))
        self.screen.setToolTip("Записать экран, микрофон и звук встречи в MP4")
        bottom.addWidget(self.screen)
        bottom.addStretch()
        self.processing_actions = QFrame()
        self.processing_actions.setObjectName("processingActions")
        processing_layout = QHBoxLayout(self.processing_actions)
        processing_layout.setContentsMargins(0, 0, 0, 0)
        processing_layout.setSpacing(10)
        self.processing_status = QLabel("Обработка текста")
        self.processing_status.setObjectName("processingStatus")
        processing_layout.addWidget(self.processing_status)
        self.cancel_processing = QPushButton("Отмена")
        self.cancel_processing.setObjectName("cancelProcessing")
        self.cancel_processing.setAccessibleName("Отменить обработку")
        self.cancel_processing.clicked.connect(self.cancel_requested.emit)
        processing_layout.addWidget(self.cancel_processing)
        self.processing_actions.hide()
        bottom.addWidget(self.processing_actions)
        self.record_actions = QWidget()
        self.record_actions.setObjectName("recordActions")
        record_actions_layout = QHBoxLayout(self.record_actions)
        record_actions_layout.setContentsMargins(0, 0, 0, 0)
        record_actions_layout.setSpacing(8)
        self.record = QPushButton("Записать встречу")
        self.record.setIcon(qta.icon("fa6s.circle", color="#ffffff"))
        self.record.setObjectName("primary")
        self.record.setFixedWidth(174)
        self.record.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred
        )
        self.record.setToolTip("Начать запись встречи")
        self.record.clicked.connect(self.record_requested.emit)
        self.stop_record = QPushButton("Остановить")
        self.stop_record.setIcon(qta.icon("fa6s.stop", color="#ffffff"))
        self.stop_record.setObjectName("stopButton")
        self.stop_record.setFixedWidth(174)
        self.stop_record.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred
        )
        self.stop_record.setToolTip("Завершить запись и перейти к расшифровке")
        self.stop_record.clicked.connect(self.stop_requested.emit)
        self.stop_record.setEnabled(False)
        record_actions_layout.addWidget(self.record)
        record_actions_layout.addWidget(self.stop_record)
        bottom.addWidget(self.record_actions)
        bottom.addStretch()
        layout.addWidget(self.recording_bar)
        records_layout.addWidget(main, 1)
        self.content_stack.addWidget(records_page)
        self._page_widgets["records"] = records_page
        root.addWidget(self.content_stack, 1)

    def show_page(self, page_key):
        """Switch the right-hand content without opening another window."""
        page = self._page_widgets.get(page_key)
        if page is None:
            return
        self.content_stack.setCurrentWidget(page)
        for key, button in self._nav_buttons.items():
            button.setProperty("active", key == page_key)
            button.style().unpolish(button)
            button.style().polish(button)
            button.update()
        self._refresh_nav_icons()

    def set_embedded_page(self, page_key, widget, activate=True):
        """Register an embedded page and optionally make it active."""
        if self.content_stack.indexOf(widget) < 0:
            self.content_stack.addWidget(widget)
        self._page_widgets[page_key] = widget
        if activate:
            self.show_page(page_key)

    def _refresh_nav_icons(self):
        inactive = "#9aa8bc" if self.dark else "#46505e"
        for key, button in self._nav_buttons.items():
            icon_name = button.property("iconKind")
            if not icon_name:
                continue
            icon_kind = getattr(QStyle.StandardPixmap, icon_name)
            color = "#60a5fa" if self.dark and button.property("active") else (
                "#1769e0" if button.property("active") else inactive
            )
            button.setIcon(self._icon(icon_kind, color))

    def _align_action_menu(self, button, menu):
        """Open toolbar menus inward with a consistent gap from the button."""
        menu.aboutToShow.connect(
            lambda: QTimer.singleShot(
                0,
                lambda: self._position_action_menu(button, menu),
            )
        )

    @staticmethod
    def _position_action_menu(button, menu):
        """Right-align a popup to its button and keep it on the active screen."""
        menu.adjustSize()
        menu_size = menu.sizeHint()
        button_top_left = button.mapToGlobal(button.rect().topLeft())
        button_bottom_right = button.mapToGlobal(button.rect().bottomRight())
        screen = button.screen().availableGeometry()
        edge_margin = 8
        gap = 6

        x = button_bottom_right.x() + 1 - menu_size.width()
        x = max(
            screen.left() + edge_margin,
            min(x, screen.right() - menu_size.width() - edge_margin + 1),
        )
        y = button_bottom_right.y() + 1 + gap
        if y + menu_size.height() > screen.bottom() - edge_margin + 1:
            y = button_top_left.y() - gap - menu_size.height()
        menu.move(x, max(screen.top() + edge_margin, y))

    def _refresh_action_icons(self):
        """Keep action meaning, weight, and optical size consistent."""
        accent = "#60a5fa" if self.dark else "#1769e0"
        danger = "#f87171" if self.dark else "#d84747"
        muted = "#617086" if self.dark else "#a0a8b4"
        for button in (
            self.sort_button,
            self.open_media_button,
            self.codex_improve_button,
            self.rename_button,
            self.trash_button,
        ):
            color = danger if button.property("iconTone") == "danger" else accent
            button.setIcon(
                qta.icon(
                    button.property("iconName"),
                    color=color,
                    color_disabled=muted,
                )
            )
        for action in self._codex_mode_actions:
            action.setIcon(
                qta.icon(
                    action.property("iconName"),
                    color=accent,
                    color_disabled=muted,
                )
            )

    def _apply_theme(self):
        bg, panel, text, muted, border, hover, select, accent, danger = (
            ("#111722", "#17202d", "#f3f6fb", "#9aa8bc", "#2b3749", "#1d2939", "#203b5d", "#60a5fa", "#f87171")
            if self.dark
            else ("#fbfcfe", "#f4f7fb", "#18202b", "#6f7b8d", "#e2e8f0", "#edf3fa", "#e4efff", "#1769e0", "#d84747")
        )
        self.setStyleSheet(f"""
            QWidget#voiceNotesWorkspace,QStackedWidget#workspaceStack,QWidget#recordsPage,QWidget#main,QWidget#empty {{ background:{bg}; color:{text}; font-family:'Segoe UI'; font-size:14px; }}
            QLabel {{ background:transparent; color:{text}; }}
            QFrame#nav {{ background:{panel}; border-right:1px solid {border}; }} QFrame#list {{ background:{bg}; border-right:1px solid {border}; }}
            QLabel#sectionTitle {{ font-size:18px; font-weight:600; }} QLabel#noteName {{ font-size:28px; font-weight:600; }}
            QPushButton#navButton {{ background:transparent; border:0; border-radius:10px; padding:11px 14px; text-align:left; font-weight:400; }} QPushButton#navButton:hover {{ background:{hover}; }} QPushButton#navButton[active='true'] {{ background:{select}; color:{accent}; }}
            QPushButton#themeButton,QPushButton#sortMeetingsButton,QPushButton#openMediaButton,QPushButton#codexImproveButton,QPushButton#renameMeetingButton,QPushButton#trashMeetingButton,QPushButton#iconButton,QPushButton#playButton,QPushButton#linkButton {{ border:0; background:transparent; color:{accent}; padding:8px; border-radius:10px; }}
            QPushButton#themeButton:hover,QPushButton#sortMeetingsButton:hover,QPushButton#openMediaButton:hover,QPushButton#codexImproveButton:hover,QPushButton#renameMeetingButton:hover,QPushButton#trashMeetingButton:hover,QPushButton#iconButton:hover,QPushButton#playButton:hover,QPushButton#linkButton:hover {{ background:{hover}; }}
            QPushButton#themeButton:pressed,QPushButton#sortMeetingsButton:pressed,QPushButton#openMediaButton:pressed,QPushButton#codexImproveButton:pressed,QPushButton#renameMeetingButton:pressed,QPushButton#trashMeetingButton:pressed,QPushButton#iconButton:pressed,QPushButton#playButton:pressed {{ background:{select}; }}
            QPushButton#sortMeetingsButton:pressed,QPushButton#sortMeetingsButton:open,QPushButton#codexImproveButton:pressed,QPushButton#codexImproveButton:open {{ background:{hover}; }}
            QPushButton#openMediaButton:disabled,QPushButton#codexImproveButton:disabled,QPushButton#renameMeetingButton:disabled,QPushButton#trashMeetingButton:disabled,QPushButton#playButton:disabled {{ background:transparent; color:{muted}; }}
            QPushButton#trashMeetingButton {{ color:{danger}; }}
            QPushButton#codexImproveButton::menu-indicator,QPushButton#sortMeetingsButton::menu-indicator {{ image:none; width:0; }}
            QListWidget#notes {{ border:0; outline:0; background:{bg}; }} QListWidget#notes::item {{ border:0; border-radius:11px; margin:4px 0; padding:16px 18px; }} QListWidget#notes::item:hover {{ background:{hover}; }} QListWidget#notes::item:selected {{ background:{select}; color:{text}; }}
            QFrame#player {{ border-bottom:1px solid {border}; }} QLabel#wave {{ color:{accent}; font-size:17px; }} QLabel#source,QLabel#muted {{ color:{muted}; }} QLabel#emptyTitle {{ font-size:25px; font-weight:600; }} QLabel#fieldLabel {{ font-weight:600; }}
            QFrame#recordingBar {{ background:{panel}; border:1px solid {border}; border-radius:14px; }}
            QWidget#recordActions {{ background:transparent; border:0; }}
            QPushButton#primary {{ background:{accent}; color:#fff; border:0; border-radius:10px; padding:11px 18px; font-weight:600; text-align:center; }} QPushButton#primary:hover {{ background:#095aca; }} QPushButton#primary:disabled {{ background:{border}; color:{muted}; }} QPushButton#stopButton {{ background:{danger}; color:#fff; border:0; border-radius:10px; padding:11px 18px; font-weight:600; text-align:center; }} QPushButton#stopButton:hover {{ background:#c83737; }} QPushButton#stopButton:pressed {{ background:#ad2d2d; }} QPushButton#stopButton:disabled {{ background:{border}; color:{muted}; }} QTextEdit {{ border:0; background:transparent; color:{text}; font-size:16px; }} QCheckBox {{ background:transparent; color:{muted}; }} QLabel:disabled,QCheckBox:disabled {{ color:{muted}; }}
            QFrame#processingActions {{ background:transparent; border:0; border-radius:0; }} QLabel#processingStatus {{ color:{muted}; }} QPushButton#cancelProcessing {{ border:0; background:transparent; color:{danger}; padding:5px 8px; font-weight:600; }} QPushButton#cancelProcessing:hover {{ background:{hover}; border-radius:7px; }}
            QMenu {{ background:{panel}; color:{text}; border:1px solid {border}; border-radius:9px; padding:8px 10px; }}
            QMenu::item {{ border-radius:6px; margin:1px 0; padding:9px 18px 9px 18px; }}
            QMenu::indicator,QMenu::icon {{ position:relative; left:8px; }}
            QMenu::item:selected {{ background:{hover}; color:{text}; }}
        """)
        self._transcript_document_css = f"""
            body {{
                color: {text};
                font-family: 'Segoe UI';
                font-size: 16px;
                line-height: 145%;
            }}
            h1, h2, h3 {{
                color: {text};
                font-weight: 650;
                margin-top: 16px;
                margin-bottom: 10px;
            }}
            h1 {{ font-size: 24px; }}
            h2 {{ font-size: 21px; }}
            h3 {{ font-size: 18px; }}
            p {{ margin-top: 0; margin-bottom: 9px; }}
            ul, ol {{ margin-top: 6px; margin-bottom: 13px; margin-left: 28px; }}
            li {{ margin-bottom: 8px; margin-left: 7px; }}
            strong {{ font-weight: 650; }}
        """
        self.transcript.document().setDefaultStyleSheet(
            self._transcript_document_css
        )
        self.transcript.document().setDocumentMargin(18)
        self._apply_transcript_typography()
        self.theme_button.setToolTip(
            "Включить светлую тему" if self.dark else "Включить тёмную тему"
        )
        self.theme_button.setAccessibleName(
            "Включить светлую тему" if self.dark else "Включить тёмную тему"
        )
        self.theme_button.setIcon(
            qta.icon(
                "fa6s.sun" if self.dark else "fa6s.moon",
                color="#9aa8bc" if self.dark else "#46505e",
            )
        )
        self.waveform.set_color(accent)
        self.waveform.set_unplayed_color("#40506a" if self.dark else "#cbd5e1")
        self._refresh_action_icons()
        self._update_play_button(self._media_player.playbackState())
        self._set_empty_state_icon(
            "error" if self._transcription_state == "error" else (
                "busy"
                if self._transcription_state
                in {"processing", "transcribing", "cleaning"}
                else "ready"
            )
        )
        self._refresh_nav_icons()
        self._apply_transcription_controls_state()

    def set_theme(self, theme): self.dark = theme == "dark"; self._apply_theme()
    def toggle_theme(self): self.dark = not self.dark; self._apply_theme(); self.theme_changed.emit("dark" if self.dark else "light")
    @staticmethod
    def _format_time(seconds):
        seconds = max(0, int(seconds or 0))
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"

    @staticmethod
    def _clean_transcript_title(value):
        """Remove Markdown decoration from a transcript-derived title."""
        title = (value or "").strip()
        title = re.sub(r"^\s{0,3}#{1,6}\s*", "", title)
        title = re.sub(r"^\s*[-*+]\s+", "", title)
        title = title.strip("*_` ")
        return title or "Встреча без названия"

    @staticmethod
    def _title_with_meeting_date(title, timestamp):
        """Append the meeting date/time once, using a filename-safe format."""
        clean_title = (title or "Встреча без названия").strip()
        formatted = format_timestamp(timestamp or "").strip().replace(":", "-")
        if not formatted:
            return clean_title
        date_part = formatted.split(" ", 1)[0]
        if date_part and date_part in clean_title:
            return clean_title
        return f"{clean_title} · {formatted}"

    @staticmethod
    def _title_without_auto_date(title, timestamp):
        """Keep the rename field focused on the human part of the title."""
        formatted = format_timestamp(timestamp or "").strip().replace(":", "-")
        suffix = f" · {formatted}" if formatted else ""
        clean_title = (title or "").strip()
        return clean_title[:-len(suffix)].rstrip() if suffix and clean_title.endswith(suffix) else clean_title

    def _apply_transcript_typography(self):
        """Give every transcript comfortable line and paragraph spacing."""
        document = self.transcript.document()
        block = document.begin()
        while block.isValid():
            cursor = QTextCursor(block)
            block_format = cursor.blockFormat()
            block_format.setLineHeight(
                145,
                QTextBlockFormat.LineHeightTypes.ProportionalHeight.value,
            )
            block_format.setBottomMargin(
                9 if block.textList() is not None else 6
            )
            block_format.setTextIndent(7 if block.textList() is not None else 0)
            cursor.setBlockFormat(block_format)
            block = block.next()

    def _on_sort_changed(self) -> None:
        """Update the icon menu presentation and apply the selected ordering."""
        current_index = self.sort.currentIndex()
        for index, action in enumerate(self._sort_actions):
            action.setChecked(index == current_index)
        current_label = self.sort.currentText() or "Сначала новые"
        self.sort_button.setToolTip(f"Сортировка: {current_label}")
        self.sort_button.setAccessibleName(f"Сортировка: {current_label}")
        self._sort_notes()

    def _show_transcript_text(self, text, transcript_format=""):
        if transcript_format == ".md":
            self.transcript.setMarkdown(text)
        else:
            self.transcript.setPlainText(text)
        # QTextDocument rebuilds its formats when Markdown is loaded. Reapply
        # the active palette so dark-theme text does not fall back to black.
        self.transcript.document().setDefaultStyleSheet(
            self._transcript_document_css
        )
        self._apply_transcript_typography()
        self._highlight_search_matches(scroll_to_first=True)

    @staticmethod
    def _media_duration(path):
        if not path or not os.path.exists(path):
            return 0.0
        if os.path.splitext(path)[1].casefold() == ".wav":
            try:
                with wave.open(path, "rb") as audio:
                    rate = audio.getframerate()
                    return audio.getnframes() / rate if rate else 0.0
            except (OSError, EOFError, wave.Error):
                return 0.0
        try:
            import av

            with av.open(path) as media:
                if media.duration is not None:
                    return float(media.duration) / 1_000_000
                streams = list(media.streams.audio) + list(media.streams.video)
                for stream in streams:
                    if stream.duration is not None and stream.time_base:
                        return float(stream.duration * stream.time_base)
        except Exception:
            return 0.0
        return 0.0

    def _request_media_duration(self, path):
        """Probe one selected file without blocking the interface or locking it."""
        if os.path.splitext(path)[1].casefold() == ".wav":
            # A WAV header is tiny and deterministic; reading it inline avoids
            # a worker/file-close race while still completing immediately.
            self.media_duration_ready.emit(path, self._media_duration(path))
            return

        def worker():
            self.media_duration_ready.emit(path, self._media_duration(path))

        threading.Thread(
            target=worker,
            name="meeting-duration",
            daemon=True,
        ).start()

    def _apply_probed_media_duration(self, path, seconds):
        if not self._same_path(path, self._selected_media_path):
            return
        if seconds <= 0:
            self.duration_label.setText("—")
            return
        self.duration_label.setText(self._format_time(seconds))
        current_item = self.notes.currentItem()
        if current_item is None:
            return
        data = dict(current_item.data(Qt.ItemDataRole.UserRole) or {})
        data["duration"] = seconds
        current_item.setData(Qt.ItemDataRole.UserRole, data)
        self._update_item_duration_text(
            current_item, self._format_time(seconds)
        )

    @staticmethod
    def _update_item_duration_text(item, formatted_duration):
        """Reflect a newly probed duration in the selected library row."""
        lines = item.text().splitlines()
        if len(lines) < 2:
            return
        details = [part.strip() for part in lines[1].split("·")]
        if len(details) >= 4:
            details[1] = formatted_duration
        elif len(details) == 3:
            details.insert(1, formatted_duration)
        else:
            return
        item.setText(f"{lines[0]}\n" + "  ·  ".join(details))

    def _toggle_playback(self):
        if not self._selected_media_path or not os.path.exists(
            self._selected_media_path
        ):
            return
        if self._media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._media_player.pause()
            return
        current_source = self._media_player.source().toLocalFile()
        if os.path.normcase(current_source) != os.path.normcase(
            self._selected_media_path
        ):
            self._media_player.setSource(
                QUrl.fromLocalFile(self._selected_media_path)
            )
        self._media_player.play()

    def _on_media_position_changed(self, position_ms):
        """Keep the timestamp and played portion of the waveform in sync."""
        self.elapsed_label.setText(self._format_time(position_ms / 1000))
        duration_ms = self._media_player.duration()
        self.waveform.set_progress(
            position_ms / duration_ms if duration_ms > 0 else 0.0
        )

    def _seek_playback(self, progress):
        """Seek to the clicked point on the audio waveform."""
        if not self._selected_media_path:
            return
        current_source = self._media_player.source().toLocalFile()
        if not self._same_path(current_source, self._selected_media_path):
            self._media_player.setSource(
                QUrl.fromLocalFile(self._selected_media_path)
            )
        duration_ms = self._media_player.duration()
        if duration_ms <= 0:
            return
        self._media_player.setPosition(
            int(max(0.0, min(1.0, progress)) * duration_ms)
        )

    @staticmethod
    def _normalize_waveform(amplitudes, bucket_count=96):
        """Compress arbitrary amplitude samples into display-ready buckets."""
        if not amplitudes:
            return []
        bucket_count = max(1, int(bucket_count))
        buckets = []
        for bucket in range(bucket_count):
            start = int(bucket * len(amplitudes) / bucket_count)
            end = max(
                start + 1,
                int((bucket + 1) * len(amplitudes) / bucket_count),
            )
            section = amplitudes[start:end]
            buckets.append(sum(section) / len(section))
        nonzero = sorted(value for value in buckets if value > 0)
        if not nonzero:
            return []
        peak = nonzero[min(len(nonzero) - 1, int(len(nonzero) * 0.92))]
        peak = max(peak, 1e-9)
        return [
            max(0.05, min(1.0, (value / peak) ** 0.58))
            for value in buckets
        ]

    @classmethod
    def _media_waveform(cls, path, bucket_count=96):
        """Read real audio energy without loading a whole meeting into RAM."""
        extension = os.path.splitext(path)[1].casefold()
        if extension == ".wav":
            try:
                import numpy as np

                with wave.open(path, "rb") as audio:
                    frame_count = audio.getnframes()
                    frames_per_bucket = max(
                        1,
                        math.ceil(frame_count / max(1, bucket_count * 4)),
                    )
                    sample_width = audio.getsampwidth()
                    dtype = {1: np.uint8, 2: "<i2", 4: "<i4"}.get(
                        sample_width
                    )
                    if dtype is None:
                        return []
                    amplitudes = []
                    while True:
                        raw = audio.readframes(frames_per_bucket)
                        if not raw:
                            break
                        samples = np.frombuffer(raw, dtype=dtype).astype(np.float32)
                        if sample_width == 1:
                            samples -= 128.0
                        if samples.size:
                            amplitudes.append(
                                float(np.sqrt(np.mean(np.square(samples))))
                            )
                return cls._normalize_waveform(amplitudes, bucket_count)
            except (OSError, EOFError, wave.Error, ValueError):
                return []
        try:
            import av
            import numpy as np

            amplitudes = []
            with av.open(path) as media:
                audio_stream = next(iter(media.streams.audio), None)
                if audio_stream is None:
                    return []
                for frame in media.decode(audio_stream):
                    samples = frame.to_ndarray().astype(np.float32)
                    if samples.size:
                        amplitudes.append(
                            float(np.sqrt(np.mean(np.square(samples))))
                        )
            return cls._normalize_waveform(amplitudes, bucket_count)
        except Exception:
            return []

    def _request_media_waveform(self, path):
        """Analyse only the selected recording and never block the interface."""
        cached = self._waveform_cache.get(os.path.normcase(path))
        if cached:
            self.media_waveform_ready.emit(path, cached)
            return

        def worker():
            try:
                levels = self._media_waveform(path)
                if not levels:
                    levels = WaveformWidget.placeholder_levels(path)
                self.media_waveform_ready.emit(path, levels)
            finally:
                with self._waveform_threads_lock:
                    self._waveform_threads.discard(threading.current_thread())

        waveform_thread = threading.Thread(
            target=worker,
            name="meeting-waveform",
            daemon=True,
        )
        with self._waveform_threads_lock:
            self._waveform_threads.add(waveform_thread)
        waveform_thread.start()

    def _apply_media_waveform(self, path, levels):
        """Use analysis results only if the same recording is still selected."""
        if not levels:
            return
        self._waveform_cache[os.path.normcase(path)] = list(levels)
        if self._same_path(path, self._selected_media_path):
            self.waveform.set_levels(levels)

    def closeEvent(self, event):
        """Release short-lived waveform readers before the widget is closed."""
        deadline = time.monotonic() + 2.0
        with self._waveform_threads_lock:
            active_threads = list(self._waveform_threads)
        for waveform_thread in active_threads:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            waveform_thread.join(remaining)
        super().closeEvent(event)

    def _on_media_duration_changed(self, duration_ms):
        """Show metadata for the selected source as soon as Qt has read it."""
        self._on_media_position_changed(self._media_player.position())
        if duration_ms <= 0 or not self._selected_media_path:
            return
        current_source = self._media_player.source().toLocalFile()
        if current_source and not self._same_path(
            current_source, self._selected_media_path
        ):
            return
        self._apply_probed_media_duration(
            self._selected_media_path, duration_ms / 1000
        )

    def _update_play_button(self, state):
        icon_name = "fa6s.pause" if state == QMediaPlayer.PlaybackState.PlayingState else "fa6s.play"
        self.play_button.setIcon(
            qta.icon(icon_name, color="#60a5fa" if self.dark else "#1769e0")
        )
        self.play_button.setAccessibleName(
            "Пауза"
            if state == QMediaPlayer.PlaybackState.PlayingState
            else "Воспроизвести"
        )

    def _update_recording_timer(self):
        if self.recording:
            self.elapsed_label.setText(self._format_time(time.monotonic() - self._record_started_at))
    def _request_transcription(self):
        if self._transcription_state in {"processing", "transcribing", "cleaning"}:
            return
        if self._selected_audio_path and os.path.exists(self._selected_audio_path):
            audio_path = self._selected_audio_path
            self.set_transcription_state("processing", audio_path)
            self.transcribe_requested.emit(audio_path)

    def _set_transcribe_button_icon(self, state):
        icons = {
            "ready": ("fa6s.file-lines", "#ffffff"),
            "busy": ("fa6s.hourglass-half", "#8e99a8"),
            "error": ("fa6s.rotate-right", "#ffffff"),
            "complete": ("fa6s.check", "#ffffff"),
        }
        icon_name, color = icons.get(state, icons["ready"])
        self.transcribe.setIcon(qta.icon(icon_name, color=color))

    def _set_empty_state_icon(self, state):
        icons = {
            "ready": (
                "fa6s.file-lines",
                "#60a5fa" if self.dark else "#1769e0",
            ),
            "busy": (
                "fa6s.hourglass-half",
                "#7f8a99" if self.dark else "#768292",
            ),
            "error": ("fa6s.triangle-exclamation", "#b66a22"),
        }
        icon_name, color = icons.get(state, icons["ready"])
        self.empty_icon.setPixmap(qta.icon(icon_name, color=color).pixmap(48, 48))

    @staticmethod
    def _same_path(first, second):
        if not first or not second:
            return False
        return os.path.normcase(os.path.abspath(first)) == os.path.normcase(
            os.path.abspath(second)
        )

    def set_transcription_state(self, state, audio_path="", message=""):
        """Apply a stable, non-animated state to the transcription controls."""
        valid_states = {
            "idle",
            "processing",
            "transcribing",
            "cleaning",
            "complete",
            "error",
            "canceled",
        }
        self._transcription_state = state if state in valid_states else "idle"
        if audio_path:
            self._active_transcription_path = audio_path
        if self._transcription_state == "error":
            self._transcription_error = message or "Не удалось расшифровать файл"
        elif self._transcription_state not in {"processing", "transcribing", "cleaning"}:
            self._transcription_error = ""
        self._apply_transcription_controls_state()

    def _apply_transcription_controls_state(self):
        if not hasattr(self, "transcribe"):
            return
        busy = self._transcription_state in {"processing", "transcribing", "cleaning"}
        selected_is_active = self._same_path(
            self._selected_audio_path, self._active_transcription_path
        )
        has_source = bool(
            self._selected_audio_path
            and os.path.exists(self._selected_audio_path)
        )

        self.model.setEnabled(not busy and not self.recording)
        self.record.setEnabled(not busy and not self.recording)
        self.rename_button.setEnabled(not busy and not self.recording)
        self.trash_button.setEnabled(not busy and not self.recording)
        codex_enabled = resolve_codex_cleanup_enabled()
        self.codex_improve_button.setEnabled(
            codex_enabled and not busy and not self.recording
        )
        self.codex_improve_button.setToolTip(
            "Переделать расшифровку и итоги без повторного распознавания"
            if codex_enabled
            else "Сначала включите Codex в настройках обработки текста"
        )
        self.stop_record.setEnabled(self.recording)
        self.record_actions.setVisible(not busy)
        self.processing_actions.setVisible(busy)
        if self._transcription_state == "cleaning":
            self.processing_status.setText("Обработка текста в Codex…")
        else:
            self.processing_status.setText("Расшифровка…")
        self.record.setToolTip(
            "Дождитесь завершения расшифровки"
            if busy
            else "Начать запись встречи"
        )
        if self.recording:
            self.transcribe.setEnabled(False)
            self.transcribe.setText("Идёт запись")
            self.transcribe.setToolTip(
                "Остановите запись перед расшифровкой другой встречи"
            )
            self._set_transcribe_button_icon("busy")
            self.empty_icon.setPixmap(
                qta.icon("fa6s.microphone", color="#d84747").pixmap(48, 48)
            )
        elif busy:
            self.transcribe.setEnabled(False)
            self.transcribe.setText(
                "Расшифровка…"
                if selected_is_active
                else "Идёт другая расшифровка"
            )
            self.transcribe.setToolTip("Дождитесь завершения текущей расшифровки")
            self._set_transcribe_button_icon("busy")
            self._set_empty_state_icon("busy")
            if selected_is_active and not self.transcript.isVisible():
                self.empty_title.setText(
                    "Обработка текста"
                    if self._transcription_state == "cleaning"
                    else "Расшифровка"
                )
                self.empty_desc.setText(
                    "Codex приводит расшифровку в порядок"
                    if self._transcription_state == "cleaning"
                    else "Обрабатываем запись"
                )
        elif self._transcription_state == "error" and selected_is_active:
            self.transcribe.setEnabled(has_source)
            self.transcribe.setText("Повторить")
            self.transcribe.setToolTip("Повторить расшифровку")
            self._set_transcribe_button_icon("error")
            self._set_empty_state_icon("error")
            if not self.transcript.isVisible():
                self.empty_title.setText("Не удалось расшифровать")
                self.empty_desc.setText(self._transcription_error)
        else:
            self.transcribe.setEnabled(has_source)
            self.transcribe.setText("Расшифровать")
            self.transcribe.setToolTip("Запустить расшифровку выбранной встречи")
            self._set_transcribe_button_icon("ready")
            self._set_empty_state_icon("ready")

        visual_state = (
            "busy"
            if busy or self.recording
            else "error"
            if self._transcription_state == "error" and selected_is_active
            else "ready"
        )
        self.transcribe.setProperty("state", visual_state)
        self.transcribe.style().unpolish(self.transcribe)
        self.transcribe.style().polish(self.transcribe)
        self.transcribe.update()

    def _populate_installed_models(self):
        names = {"tiny": "Whisper tiny — самый быстрый", "base": "Whisper base — быстрый", "small": "Whisper small — оптимальный", "medium": "Whisper medium — точный", "turbo": "Whisper turbo — быстрый и точный"}
        current = settings_manager.get(SettingsKey.WHISPER_MODEL, "turbo")
        installed = [name for name in names if name == current or is_model_cached(name)]
        if not installed: installed = [current]
        for name in installed: self.model.addItem(names.get(name, f"Whisper {name}"), name)
        index = self.model.findData(current)
        self.model.setCurrentIndex(max(0, index))

    def _open_recording_folder(self):
        selected_path = self._selected_media_path or self._selected_audio_path
        folder = os.path.dirname(selected_path) if selected_path else history_manager.recordings_folder
        os.makedirs(folder, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _open_selected_media(self):
        if self._selected_media_path and os.path.exists(
            self._selected_media_path
        ):
            QDesktopServices.openUrl(
                QUrl.fromLocalFile(self._selected_media_path)
            )

    def _request_codex_improvement(self, mode=CodexCleanupMode.FULL):
        source_path = self._selected_audio_path or self._selected_media_path
        if (
            not self._selected_original_text.strip()
            or NO_SPEECH_TRANSCRIPT in self._selected_original_text
        ):
            return
        self.codex_improve_requested.emit(
            source_path,
            self._selected_original_text,
            self._selected_history_id,
            CodexCleanupMode.normalize(mode),
        )

    def _rename_selected_meeting(self):
        source_path = self._selected_media_path or self._selected_audio_path
        has_source = bool(source_path and os.path.exists(source_path))
        if not has_source and not self._selected_history_id:
            return
        current_item = self.notes.currentItem()
        current_data = (
            current_item.data(Qt.ItemDataRole.UserRole)
            if current_item is not None
            else {}
        ) or {}
        meeting_timestamp = current_data.get("timestamp") or ""
        current_title = self._title_without_auto_date(
            current_data.get("base_title") or self.note_name.text(),
            meeting_timestamp,
        )
        new_title, accepted = QInputDialog.getText(
            self,
            "Переименовать встречу",
            "Новое название:",
            text=current_title,
        )
        if not accepted:
            return
        resolved_title = self._title_with_meeting_date(
            new_title,
            meeting_timestamp,
        )
        if resolved_title == self.note_name.text().strip():
            return

        self._media_player.stop()
        self._media_player.setSource(QUrl())
        try:
            moved = (
                history_manager.rename_meeting(source_path, resolved_title)
                if has_source
                else {}
            )
            if not has_source:
                history_manager.rename_history_entry(
                    self._selected_history_id,
                    resolved_title,
                )
        except (ValueError, FileNotFoundError, FileExistsError, OSError) as exc:
            QMessageBox.warning(
                self,
                "Не удалось переименовать встречу",
                str(exc),
            )
            return

        for attribute in ("_selected_audio_path", "_selected_media_path"):
            old_path = getattr(self, attribute)
            replacement = next(
                (
                    new_path
                    for candidate, new_path in moved.items()
                    if self._same_path(candidate, old_path)
                ),
                old_path,
            )
            setattr(self, attribute, replacement)
        self.refresh_history()

    def _move_selected_to_trash(self):
        source_path = self._selected_media_path or self._selected_audio_path
        source_exists = bool(source_path and os.path.exists(source_path))
        if not source_exists and not self._selected_history_id:
            return
        meeting_name = self.note_name.text()
        if not source_exists:
            answer = QMessageBox.question(
                self,
                "Удалить архивную расшифровку?",
                (
                    f"«{meeting_name}» будет окончательно удалена из истории.\n\n"
                    "Исходной записи уже нет, поэтому восстановить эту расшифровку из корзины не получится."
                ),
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            if not history_manager.delete_entry(
                self._selected_history_id,
                delete_audio_file=False,
            ):
                QMessageBox.warning(
                    self,
                    "Не удалось удалить расшифровку",
                    "Архивная расшифровка уже отсутствует в истории.",
                )
                return
            self._selected_history_id = ""
            self.refresh_history()
            return
        answer = QMessageBox.question(
            self,
            "Переместить встречу в корзину?",
            (
                f"«{meeting_name}» будет перемещена в корзину вместе "
                "с аудио, видео и всеми вариантами расшифровки.\n\n"
                "При необходимости файлы можно восстановить из корзины."
            ),
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._media_player.stop()
        self._media_player.setSource(QUrl())
        try:
            history_manager.move_meeting_to_trash(
                source_path,
                self._selected_history_id,
            )
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Не удалось переместить встречу",
                str(exc),
            )
            return
        self._selected_audio_path = ""
        self._selected_media_path = ""
        self._selected_history_id = ""
        self.refresh_history()

    @staticmethod
    def _search_terms(text):
        """Return unique, case-insensitive terms from a meeting query."""
        terms = []
        for part in re.split(r"\s+", (text or "").casefold().strip()):
            if part and part not in terms:
                terms.append(part)
        return terms

    def _highlight_search_matches(self, scroll_to_first=False):
        """Outline every search match in the currently displayed transcript."""
        if not hasattr(self, "transcript"):
            return
        terms = self._search_terms(
            self.search.text() if hasattr(self, "search") else ""
        )
        document = self.transcript.document()
        plain_text = document.toPlainText()
        selections = []
        first_position = None

        if terms and plain_text:
            fill = QColor("#5a4318" if self.dark else "#fff0a8")
            outline = QColor("#ffc04d" if self.dark else "#a95e00")
            match_format = QTextCharFormat()
            match_format.setBackground(fill)
            outline_pen = QPen(outline)
            outline_pen.setWidthF(0.8)
            match_format.setTextOutline(outline_pen)

            for term in terms:
                for match in re.finditer(
                    re.escape(term),
                    plain_text,
                    flags=re.IGNORECASE,
                ):
                    cursor = QTextCursor(document)
                    cursor.setPosition(match.start())
                    cursor.setPosition(
                        match.end(),
                        QTextCursor.MoveMode.KeepAnchor,
                    )
                    selection = QTextEdit.ExtraSelection()
                    selection.cursor = cursor
                    selection.format = match_format
                    selections.append(selection)
                    if first_position is None or match.start() < first_position:
                        first_position = match.start()

        self.transcript.setExtraSelections(selections)
        if scroll_to_first and first_position is not None:
            cursor = QTextCursor(document)
            cursor.setPosition(first_position)
            self.transcript.setTextCursor(cursor)
            self.transcript.ensureCursorVisible()

    def _filter_notes(self, text):
        terms = self._search_terms(text)
        first_visible = None
        for index in range(self.notes.count()):
            item = self.notes.item(index)
            data = item.data(Qt.ItemDataRole.UserRole) or {}
            searchable_text = (
                f"{item.text()}\n{data.get('text') or ''}"
            ).casefold()
            matches = all(term in searchable_text for term in terms)
            item.setHidden(bool(terms) and not matches)
            if matches and first_visible is None:
                first_visible = item

        current = self.notes.currentItem()
        if terms and first_visible is not None and (
            current is None or current.isHidden()
        ):
            self.notes.setCurrentItem(first_visible)
        else:
            self._highlight_search_matches(scroll_to_first=bool(terms))

    @staticmethod
    def _meeting_size(*paths):
        """Return the total size of distinct existing media files."""
        total = 0
        seen = set()
        for path in paths:
            if not path:
                continue
            normalized = os.path.normcase(os.path.abspath(path))
            if normalized in seen:
                continue
            seen.add(normalized)
            try:
                total += os.path.getsize(path)
            except OSError:
                pass
        return total

    @staticmethod
    def _meeting_timestamp(timestamp, *paths):
        modified = []
        for path in paths:
            if not path:
                continue
            try:
                modified.append(os.path.getmtime(path))
            except OSError:
                pass
        if modified:
            return time.strftime(
                "%Y-%m-%dT%H:%M:%S", time.localtime(max(modified))
            )
        return str(timestamp or "")

    def _sort_notes(self):
        """Sort the combined database and folder-backed meeting list."""
        if not hasattr(self, "notes") or self.notes.count() < 2:
            return
        mode = self.sort.currentData() or self.SORT_NEWEST
        current = self.notes.currentItem()
        previous_blocked = self.notes.blockSignals(True)
        items = [self.notes.takeItem(0) for _ in range(self.notes.count())]

        def data_for(item):
            return item.data(Qt.ItemDataRole.UserRole) or {}

        if mode == self.SORT_OLDEST:
            items.sort(
                key=lambda item: (
                    not bool(data_for(item).get("timestamp")),
                    data_for(item).get("timestamp") or "",
                    item.text().casefold(),
                )
            )
        else:
            field = {
                self.SORT_SIZE: "size",
                self.SORT_DURATION: "duration",
            }.get(mode, "timestamp")
            items.sort(
                key=lambda item: (
                    data_for(item).get(field) or 0,
                    data_for(item).get("timestamp") or "",
                    item.text().casefold(),
                ),
                reverse=True,
            )
        for item in items:
            self.notes.addItem(item)
        self.notes.blockSignals(previous_blocked)
        if current is not None:
            self.notes.setCurrentItem(current)

    def _sync_library_watcher(self):
        """Watch both directories and files, including nested meeting folders."""
        desired = set(history_manager.get_library_watch_paths())
        current = set(self._library_watcher.directories())
        current.update(self._library_watcher.files())
        obsolete = list(current - desired)
        if obsolete:
            self._library_watcher.removePaths(obsolete)
        missing = [
            path for path in desired - current
            if os.path.exists(path)
        ]
        if missing:
            self._library_watcher.addPaths(missing)

    def _initial_library_load(self):
        if not self._library_loaded:
            self.refresh_history()

    def _capture_library_state(self):
        self._library_snapshot = history_manager.get_library_snapshot()
        self._sync_library_watcher()

    def _schedule_external_library_refresh(self, _path=""):
        """Coalesce the burst of events produced by a save or rename."""
        self._library_refresh_timer.start()

    def _poll_external_library_changes(self):
        """Fallback for file systems that do not emit reliable watcher events."""
        current = history_manager.get_library_snapshot()
        if current != self._library_snapshot:
            self._library_refresh_timer.start()

    def _refresh_external_library_changes(self):
        current = history_manager.get_library_snapshot()
        if current == self._library_snapshot:
            self._sync_library_watcher()
            return
        renames = history_manager.reconcile_external_renames(
            self._library_snapshot,
            current,
        )
        for attribute in ("_selected_audio_path", "_selected_media_path"):
            selected = getattr(self, attribute, "")
            if not selected:
                continue
            normalized = os.path.normcase(os.path.abspath(selected))
            for old_path, new_path in renames.items():
                if normalized == os.path.normcase(os.path.abspath(old_path)):
                    setattr(self, attribute, new_path)
                    break
        self._library_snapshot = current
        self.refresh_history()

    @staticmethod
    def _original_transcript_for_recording(recording, fallback=""):
        """Prefer the editable non-Codex sidecar as the reprocessing source."""
        if recording:
            candidates = []
            expected_raw_path = (
                os.path.splitext(recording.transcription_path)[0] + ".txt"
                if recording.transcription_path
                else ""
            )
            for path in getattr(recording, "bundle_paths", ()):
                name = os.path.basename(path).casefold()
                extension = os.path.splitext(path)[1].lower()
                if extension not in {".txt", ".md", ".json"}:
                    continue
                if ".codex." in name:
                    continue
                priority = (
                    0 if expected_raw_path and VoiceNotesWorkspace._same_path(
                        path, expected_raw_path
                    ) else 1 if extension == ".txt" else 2
                )
                candidates.append((priority, path))
            for _priority, path in sorted(
                candidates,
                key=lambda item: (item[0], item[1].casefold()),
            ):
                text = history_manager.read_transcript(path)
                if history_manager.has_transcript_content(text):
                    return text
        return extract_original_transcript(fallback)

    def refresh_history(self):
        selected_path = self._selected_media_path or self._selected_audio_path
        selected_history_id = self._selected_history_id
        self.notes.blockSignals(True); self.notes.clear()
        history_entries = history_manager.get_history()
        media_files = history_manager.get_media_files()
        if history_manager.reconcile_missing_history_media(media_files):
            history_entries = history_manager.get_history()
            media_files = history_manager.get_media_files()
        media_by_path = {}
        for recording in media_files:
            for path in (
                recording.file_path,
                recording.transcription_path,
                recording.audio_path,
                recording.video_path,
            ):
                if path:
                    media_by_path[
                        os.path.normcase(os.path.abspath(path))
                    ] = recording
        seen = set()
        for entry in history_entries:
            audio_path = history_manager.get_recording_path(entry.audio_file) if entry.audio_file else ""
            recording = (
                media_by_path.get(
                    os.path.normcase(os.path.abspath(audio_path))
                )
                if audio_path
                else None
            )
            video_path = recording.video_path if recording else ""
            if audio_path and not recording:
                extension = os.path.splitext(audio_path)[1].lower()
                if extension in {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v"}:
                    video_path = audio_path
                else:
                    sidecar = os.path.splitext(audio_path)[0] + ".mp4"
                    if os.path.exists(sidecar):
                        video_path = sidecar
            if recording:
                seen.update(
                    os.path.normcase(os.path.abspath(path))
                    for path in (
                        recording.file_path,
                        recording.transcription_path,
                        recording.audio_path,
                        recording.video_path,
                    )
                    if path
                )
            elif audio_path:
                seen.add(os.path.normcase(os.path.abspath(audio_path)))
                if video_path:
                    seen.add(os.path.normcase(os.path.abspath(video_path)))
            media_path = video_path or audio_path
            display_text = entry.text or ""
            enhanced_by_codex = entry.cleanup_provider == "codex"
            transcript_format = ".md" if enhanced_by_codex else ".txt"
            if audio_path:
                transcript_path = (
                    recording.transcript_path
                    if recording and recording.transcript_path
                    else os.path.splitext(audio_path)[0] + ".txt"
                )
                sidecar_text = history_manager.read_transcript(
                    transcript_path
                )
                if history_manager.has_transcript_content(sidecar_text):
                    # Files are the source of truth. This intentionally
                    # reflects edits made in Notepad/Word/another program.
                    display_text = sidecar_text
                    transcript_name = os.path.basename(
                        transcript_path
                    ).casefold()
                    enhanced_by_codex = ".codex." in transcript_name
                    transcript_format = os.path.splitext(
                        transcript_path
                    )[1].lower()
            no_speech = NO_SPEECH_TRANSCRIPT in display_text
            if no_speech:
                display_text = NO_SPEECH_TRANSCRIPT
            has_transcript = bool(display_text.strip())
            fallback_original = (
                getattr(entry, "raw_text", "")
                or extract_original_transcript(entry.text or display_text)
            )
            original_text = (
                display_text
                if has_transcript and not enhanced_by_codex
                else self._original_transcript_for_recording(
                    recording,
                    fallback_original,
                )
            )
            fallback_title = (
                os.path.splitext(os.path.basename(audio_path))[0]
                if audio_path
                else "Встреча без названия"
            )
            first_line = display_text.strip().splitlines()[0] if has_transcript else ""
            title_from_metadata = bool(re.match(
                r"^(?:исходник|source|обработка|processing)\s*:",
                first_line.strip(),
                flags=re.IGNORECASE,
            ))
            # For a file-backed meeting, the filename is the visible title.
            # Renaming a file in Explorer must immediately rename it here too.
            base_title = (
                getattr(entry, "display_title", "")
                or (
                    fallback_title
                    if audio_path
                    else self._clean_transcript_title(first_line)
                    if has_transcript and not no_speech and not title_from_metadata
                    else fallback_title
                )
            )
            # Do not probe every external media file during startup. Some
            # damaged meeting containers can crash native decoders; the Qt
            # player supplies the duration safely when a user selects one.
            seconds = float(entry.audio_duration or 0)
            duration = self._format_time(seconds)
            size_bytes = (
                recording.size_bytes
                if recording
                else self._meeting_size(audio_path, video_path)
            )
            if not size_bytes:
                size_bytes = int(getattr(entry, "file_size", 0) or 0)
            timestamp = (
                recording.timestamp
                if recording
                else self._meeting_timestamp(
                    getattr(entry, "timestamp", ""),
                    media_path,
                    audio_path,
                )
            )
            meeting_date = format_timestamp(timestamp)
            title = self._title_with_meeting_date(
                base_title,
                timestamp,
            )
            size = format_file_size(size_bytes) if size_bytes else "—"
            transcript_status = (
                "Речь не обнаружена"
                if no_speech
                else "Улучшено в Codex"
                if has_transcript and enhanced_by_codex
                else "Расшифровано" if has_transcript
                else "Нет расшифровки"
            )
            item = QListWidgetItem(
                f"{title}\n{meeting_date}  ·  "
                f"{duration}  ·  {size}  ·  {transcript_status}"
            )
            item.setData(Qt.ItemDataRole.UserRole, {
                "id": entry.id,
                "audio": audio_path,
                "media": media_path,
                "video": video_path,
                "text": display_text,
                "original_text": original_text,
                "transcript_format": transcript_format,
                "enhanced_by_codex": enhanced_by_codex,
                "model": entry.model,
                "duration": seconds,
                "size": size_bytes,
                "timestamp": timestamp,
                "base_title": self._title_without_auto_date(
                    base_title,
                    timestamp,
                ),
                "archived": not bool(
                    media_path and os.path.exists(media_path)
                ),
            })
            self.notes.addItem(item)
        for recording in media_files:
            transcript_text = history_manager.read_transcript(
                recording.transcript_path
            )
            if not history_manager.has_transcript_content(transcript_text):
                transcript_text = ""
            no_speech = NO_SPEECH_TRANSCRIPT in transcript_text
            if no_speech:
                transcript_text = NO_SPEECH_TRANSCRIPT
            enhanced_by_codex = bool(
                transcript_text
                and ".codex." in os.path.basename(
                    recording.transcript_path or ""
                ).casefold()
            )
            original_text = (
                transcript_text
                if transcript_text and not enhanced_by_codex
                else self._original_transcript_for_recording(
                    recording,
                    transcript_text,
                )
            )
            paths = {
                os.path.normcase(path)
                for path in (
                    recording.file_path,
                    recording.transcription_path,
                    recording.audio_path,
                    recording.video_path,
                )
                if path
            }
            if paths & seen:
                continue
            seen.update(paths)
            media_label = "Видео" if recording.media_type == "video" else "Аудио"
            transcript_status = (
                "Речь не обнаружена"
                if no_speech
                else "Улучшено в Codex"
                if enhanced_by_codex
                else "Расшифровано" if transcript_text
                else "Нет расшифровки"
            )
            base_title = os.path.splitext(recording.filename)[0]
            title = self._title_with_meeting_date(
                base_title,
                recording.timestamp,
            )
            item = QListWidgetItem(
                f"{title}\n"
                f"{recording.formatted_timestamp}  ·  {recording.formatted_size}  ·  "
                f"{transcript_status}"
            )
            item.setData(Qt.ItemDataRole.UserRole, {
                "id": recording.file_path,
                "audio": recording.transcription_path,
                "media": recording.file_path,
                "video": recording.video_path or "",
                "text": transcript_text,
                "original_text": original_text,
                "transcript_path": recording.transcript_path or "",
                "transcript_format": os.path.splitext(
                    recording.transcript_path or ""
                )[1].lower(),
                "enhanced_by_codex": enhanced_by_codex,
                "model": "",
                "duration": 0.0,
                "size": recording.size_bytes,
                "timestamp": recording.timestamp,
                "base_title": self._title_without_auto_date(
                    base_title,
                    recording.timestamp,
                ),
                "archived": False,
            })
            self.notes.addItem(item)
        self._sort_notes()
        self.notes.blockSignals(False)
        has_meetings = self.notes.count() > 0
        self.search.setVisible(has_meetings)
        self.sort_button.setVisible(has_meetings)
        restored_item = None
        if selected_path or selected_history_id:
            for index in range(self.notes.count()):
                item = self.notes.item(index)
                data = item.data(Qt.ItemDataRole.UserRole) or {}
                if (
                    selected_history_id
                    and data.get("id") == selected_history_id
                ):
                    restored_item = item
                    break
                candidates = (
                    data.get("media"),
                    data.get("audio"),
                    data.get("video"),
                    data.get("id"),
                )
                if any(
                    self._same_path(selected_path, candidate)
                    for candidate in candidates
                    if candidate
                ):
                    restored_item = item
                    break
        if restored_item is not None:
            self.notes.setCurrentItem(restored_item)
            self._select_note(restored_item)
        elif self.notes.count():
            self._show_library_selection()
        else:
            self._show_no_selection()
        self._filter_notes(self.search.text())
        self._library_loaded = True
        self._capture_library_state()
        if not self._library_poll_timer.isActive():
            self._library_poll_timer.start()

    def _show_library_selection(self):
        self._selected_audio_path = ""
        self._selected_media_path = ""
        self._selected_history_id = ""
        self._selected_transcript_text = ""
        self._selected_original_text = ""
        self._selected_enhanced_by_codex = False
        self.note_name.setText("Выберите встречу")
        self.open_media_button.hide()
        self.codex_improve_button.hide()
        self.rename_button.hide()
        self.trash_button.hide()
        self.source.hide()
        self.player.hide()
        self.play_button.setEnabled(False)
        self.transcript.hide()
        self.empty.show()
        self.empty_title.setText("Выберите встречу слева")
        self.empty_desc.setText("")
        self._set_transcription_controls_visible(False)
        self.transcribe.setEnabled(False)

    def _show_no_selection(self):
        self._selected_audio_path = ""
        self._selected_media_path = ""
        self._selected_history_id = ""
        self._selected_transcript_text = ""
        self._selected_original_text = ""
        self._selected_enhanced_by_codex = False
        self.note_name.setText("Встреч пока нет")
        self.open_media_button.hide()
        self.codex_improve_button.hide()
        self.rename_button.hide()
        self.trash_button.hide()
        self.source.clear()
        self.source.hide()
        self.elapsed_label.setText("00:00")
        self.waveform.set_progress(0.0)
        self.waveform.set_levels(
            WaveformWidget.placeholder_levels(self._selected_media_path)
        )
        self.duration_label.setText("00:00")
        self.player.hide()
        self.play_button.setEnabled(False)
        self.transcript.hide()
        self.empty.show()
        self.empty_title.setText("Запишите первую встречу")
        self.empty_desc.setText("Нажмите «Записать встречу»")
        self._set_transcription_controls_visible(False)
        self.transcribe.setEnabled(False)

    def _set_transcription_controls_visible(self, visible):
        for widget in (
            self.model_label,
            self.model,
            self.transcribe,
            self.folder_button,
        ):
            widget.setVisible(visible)

    def _select_note(self, current, previous=None):
        if not current: self._show_no_selection(); return
        data = current.data(Qt.ItemDataRole.UserRole) or {}
        self._selected_history_id = data.get("id") or ""
        self._selected_transcript_text = data.get("text") or ""
        self._selected_original_text = (
            data.get("original_text")
            or extract_original_transcript(self._selected_transcript_text)
        )
        self._selected_enhanced_by_codex = bool(
            data.get("enhanced_by_codex")
        )
        self._selected_audio_path = data.get("audio") or ""
        self._selected_media_path = (
            data.get("media") or self._selected_audio_path
        )
        self.note_name.setText(
            self._clean_transcript_title(current.text().splitlines()[0])
        )
        self.open_media_button.setVisible(
            bool(
                self._selected_media_path
                and os.path.exists(self._selected_media_path)
            )
        )
        self.rename_button.setVisible(
            bool(
                self._selected_history_id
                or (
                    (self._selected_media_path or self._selected_audio_path)
                    and os.path.exists(
                        self._selected_media_path or self._selected_audio_path
                    )
                )
            )
        )
        self.trash_button.setVisible(
            bool(
                data.get("archived")
                or (
                    (self._selected_media_path or self._selected_audio_path)
                    and os.path.exists(
                        self._selected_media_path or self._selected_audio_path
                    )
                )
            )
        )
        if data.get("archived"):
            self.trash_button.setToolTip(
                "Удалить архивную расшифровку из истории"
            )
            self.trash_button.setAccessibleName(
                "Удалить архивную расшифровку"
            )
        else:
            self.trash_button.setToolTip(
                "Переместить всю встречу и её расшифровки в корзину"
            )
            self.trash_button.setAccessibleName(
                "Переместить встречу в корзину"
            )
        self.codex_improve_button.setVisible(
            bool(
                self._selected_original_text.strip()
                and NO_SPEECH_TRANSCRIPT
                not in self._selected_original_text
            )
        )
        self.player.show()
        self.source.setText(
            os.path.basename(self._selected_media_path)
            or "Архивная расшифровка · исходная запись отсутствует"
        )
        source_stem = os.path.splitext(self.source.text())[0]
        visible_title = current.text().splitlines()[0].strip()
        self.source.setVisible(
            bool(self.source.text()) and source_stem.casefold() != visible_title.casefold()
        )
        if (
            self._media_player.playbackState()
            != QMediaPlayer.PlaybackState.StoppedState
        ):
            self._media_player.stop()
        if self._media_player.source().toLocalFile():
            # Release the previously played file so Explorer can rename it.
            self._media_player.setSource(QUrl())
        self.elapsed_label.setText("00:00")
        known_duration = float(data.get("duration") or 0)
        media_exists = bool(
            self._selected_media_path
            and os.path.exists(self._selected_media_path)
        )
        self.duration_label.setText(
            self._format_time(known_duration)
            if known_duration > 0
            else "…"
            if media_exists
            else "—"
        )
        self.play_button.setEnabled(media_exists)
        if media_exists:
            self._request_media_waveform(self._selected_media_path)
        if media_exists and known_duration <= 0:
            self._request_media_duration(self._selected_media_path)
        text = data.get("text", "")
        if text:
            self.empty.hide()
            self.transcript.show()
            self._show_transcript_text(
                text,
                data.get("transcript_format") or "",
            )
        else:
            self.transcript.hide()
            self.empty.show()
            self.empty_title.setText("Расшифровки нет")
            self.empty_desc.setText("Выберите модель и запустите")
            self._set_transcription_controls_visible(True)
            self._apply_transcription_controls_state()
        self._apply_transcription_controls_state()

    def set_transcript(self, text):
        self.set_transcription_state(
            "complete", self._active_transcription_path or self._selected_audio_path
        )
        self.empty.hide()
        self.transcript.show()
        self._show_transcript_text(text)
    def set_recording(self, value):
        was_recording = self.recording
        self.recording = bool(value)
        self.screen.setEnabled(not self.recording)
        if value:
            self._media_player.stop()
            self.player.show()
            self.transcript.hide()
            self.empty.show()
            self._set_transcription_controls_visible(False)
            self.empty_icon.setPixmap(
                qta.icon("fa6s.microphone", color="#d84747").pixmap(48, 48)
            )
            self.empty_title.setText("Идёт запись")
            self.empty_desc.setText(
                "Нажмите «Остановить запись», когда закончите"
            )
            self._record_started_at = time.monotonic()
            self.elapsed_label.setText("00:00")
            self.duration_label.setText("идёт запись")
            self.note_name.setText("Идёт запись встречи")
            self.source.setText(
                "Экран, микрофон и звук компьютера"
                if self.screen.isChecked()
                else "Только микрофон"
            )
            self.source.show()
            self._record_timer.start()
        else:
            self._record_timer.stop()
        self._apply_transcription_controls_state()
        if was_recording and not self.recording:
            self.note_name.setText("Обработка записи")
            self.empty_title.setText("Сохраняем встречу")
            self.empty_desc.setText("Подготавливаем файл к расшифровке")
            self._set_empty_state_icon("busy")
