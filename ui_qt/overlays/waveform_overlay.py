"""
PyQt6 waveform overlay.
Real-time audio visualization overlay with blur effects and animations.
"""
import logging
import math
import random
import sys
import time
from dataclasses import dataclass
from typing import Optional, List
from PyQt6.QtWidgets import QApplication, QWidget
from PyQt6.QtCore import Qt, QTimer, QRect, QRectF, pyqtSignal, QPoint
from PyQt6.QtGui import (
    QPainter, QPainterPath, QColor, QBrush, QPen,
    QLinearGradient, QFont, QFontMetrics, QCursor
)
import qtawesome as qta
from config import config
from services.settings import (
    resolve_overlay_state_visibility,
    resolve_streaming_overlay_font_size,
    settings_manager,
)
from ui_qt.utils.overlay_position import (
    max_height_for_anchor,
    preferred_overlay_position,
)
from ui_qt.waveform_styles import BaseWaveformStyle, ParticleStyle

logger = logging.getLogger(__name__)


def _round_pen(color: QColor, width: float) -> QPen:
    """Pen with round caps/joins so drawn glyph strokes look polished."""
    return QPen(
        color, width, Qt.PenStyle.SolidLine,
        Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin,
    )


@dataclass
class LargeFileOverlayInfo:
    """Display info for the large-file overlay states."""
    file_size_mb: float = 0.0
    chunk_count: int = 0


class STTParticle:
    """Particle for STT enable/disable animations."""

    def __init__(self, x: float, y: float, vx: float, vy: float, hue: float):
        self.x = x
        self.y = y
        self.vx = vx
        self.vy = vy
        self.hue = hue
        self.life = 1.0
        self.size = random.uniform(2.0, 4.0)

    def update(self, dt: float, damping: float = 0.98) -> bool:
        """Update particle position and life. Returns True if still alive."""
        self.x += self.vx * dt
        self.y += self.vy * dt
        self.vx *= damping
        self.vy *= damping
        self.life -= dt * 0.5  # Slower decay for longer visibility
        return self.life > 0

    def get_color(self) -> QColor:
        """Get particle color based on hue and life."""
        alpha = int(255 * self.life)
        return QColor.fromHsv(int(self.hue) % 360, 200, 230, alpha)


class WaveformOverlay(QWidget):
    """Small, quiet status pill for recording and transcription."""

    state_changed = pyqtSignal(str)

    # States
    STATE_IDLE = "idle"
    STATE_RECORDING = "recording"
    STATE_STREAMING = "streaming"
    STATE_PROCESSING = "processing"
    STATE_TRANSCRIBING = "transcribing"
    STATE_CLEANING = "cleaning"
    STATE_CANCELING = "canceling"
    STATE_STT_ENABLE = "stt_enable"
    STATE_STT_DISABLE = "stt_disable"
    STATE_COPIED = "copied"
    STATE_LARGE_FILE_SPLITTING = "large_file_splitting"
    STATE_LARGE_FILE_PROCESSING = "large_file_processing"

    _STATE_PRESENTATION = {
        STATE_RECORDING: ("fa6s.microphone", "Запись", "#7eb6ff"),
        STATE_STREAMING: ("fa6s.microphone", "Запись", "#7eb6ff"),
        STATE_PROCESSING: ("fa6s.hourglass-half", "Подготовка", "#aeb7c4"),
        STATE_TRANSCRIBING: ("fa6s.file-lines", "Расшифровка", "#7eb6ff"),
        STATE_CLEANING: ("fa6s.wand-magic-sparkles", "Правка текста", "#b9a2ff"),
        STATE_CANCELING: ("fa6s.xmark", "Отменено", "#ff8a84"),
        STATE_STT_ENABLE: ("fa6s.check", "Включено", "#6fd78a"),
        STATE_STT_DISABLE: ("fa6s.power-off", "Выключено", "#aeb7c4"),
        STATE_COPIED: ("fa6s.copy", "Скопировано", "#7eb6ff"),
        STATE_LARGE_FILE_SPLITTING: (
            "fa6s.scissors",
            "Подготовка файла",
            "#aeb7c4",
        ),
        STATE_LARGE_FILE_PROCESSING: (
            "fa6s.file-lines",
            "Обработка файла",
            "#7eb6ff",
        ),
    }

    def __init__(self):
        """Initialize the overlay."""
        super().__init__()

        # Window properties
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool |
            Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        if sys.platform == "darwin":
            # On macOS, Qt Tool windows are hidden whenever the app is not the
            # frontmost application (or when its main window is minimized). During
            # dictation the user is typically working in another app, so without
            # this the overlay disappears. Force it to stay visible regardless.
            self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)

        # Set fixed size from config
        self.overlay_width = config.WAVEFORM_OVERLAY_WIDTH
        self.overlay_height = config.WAVEFORM_OVERLAY_HEIGHT
        self._base_height = self.overlay_height
        self._streaming_max_height = getattr(
            config, "WAVEFORM_STREAMING_MAX_HEIGHT", 200
        )
        self.setFixedSize(self.overlay_width, self.overlay_height)

        # State
        self.current_state = self.STATE_IDLE
        self.audio_levels: List[float] = [0.0] * 20
        self.animation_time = 0.0
        self.cancel_progress = 0.0
        self.stt_particles: List[STTParticle] = []
        self._streaming_preview_text: str = ""
        self._streaming_font_size = resolve_streaming_overlay_font_size()
        # Cursor/caret anchor used to keep the overlay on-screen as it grows.
        self._anchor_pos: Optional[QPoint] = None

        # Large file information for warning states
        self.large_file_info = LargeFileOverlayInfo()

        # Load waveform style
        _, style_configs = settings_manager.load_waveform_style_settings()
        style_config = style_configs.get('particle', config.WAVEFORM_STYLE_CONFIGS.get('particle', {}))
        self.style: BaseWaveformStyle = ParticleStyle(
            self.overlay_width, self.overlay_height, style_config
        )

        # Animation
        self.timer = QTimer()
        self.timer.timeout.connect(self._update_animation)
        self.frame_rate = config.WAVEFORM_FRAME_RATE
        self.animation_duration = 0
        self.last_frame_time = time.time()

        # Hide by default
        self.hidden_timer = QTimer()
        self.hidden_timer.setSingleShot(True)
        self.hidden_timer.timeout.connect(self.hide)

    def paintEvent(self, event):
        """Paint the overlay."""
        try:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)

            # Draw background with blur effect
            self._draw_background(painter)

            self._draw_compact_state(painter)
        except Exception as e:
            # Log error but don't crash the overlay
            logger.error(f"Error drawing waveform frame: {e}", exc_info=True)
            # Draw a simple fallback
            try:
                painter = QPainter(self)
                painter.fillRect(self.rect(), QColor(28, 28, 30, 238))
                painter.setPen(QPen(QColor(245, 245, 247)))
                painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
                painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Ошибка")
            except Exception:
                pass  # If even fallback fails, just skip

    def _draw_compact_state(self, painter: QPainter):
        """Draw one static icon and a short Russian status label."""
        icon_name, label, color = self._STATE_PRESENTATION.get(
            self.current_state,
            ("fa6s.circle-info", "Готово", "#aeb7c4"),
        )
        icon_size = 15
        icon_left = 14
        icon_top = (self.height() - icon_size) // 2
        try:
            icon = qta.icon(icon_name, color=color)
            painter.drawPixmap(
                icon_left,
                icon_top,
                icon.pixmap(icon_size, icon_size),
            )
        except Exception:
            logger.debug("Could not draw overlay icon %s", icon_name)

        painter.setPen(QColor(244, 246, 250, 225))
        font = QFont("Segoe UI")
        font.setPixelSize(12)
        font.setWeight(QFont.Weight.Medium)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.0)
        painter.setFont(font)
        painter.drawText(
            QRect(
                icon_left + icon_size + 10,
                0,
                self.width() - icon_left - icon_size - 22,
                self.height(),
            ),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            label,
        )

    def _draw_streaming_state(self, painter: QPainter, rect: QRect):
        """Draw recording particles plus live preview text near the cursor.

        Args:
            painter: Active painter for this frame.
            rect: Full overlay bounds.
        """
        particle_height = min(self._base_height, rect.height())
        particle_rect = QRect(0, 0, rect.width(), particle_height)
        status = "Запись" if not self._streaming_preview_text else ""
        if self.style:
            # Keep particle physics in the compact recording band even when the
            # overlay grows to fit preview text.
            previous_height = self.style.height
            self.style.height = self._base_height
            try:
                self.style.draw_recording_state(painter, particle_rect, status)
            finally:
                self.style.height = previous_height

        if self._streaming_preview_text:
            self._draw_streaming_preview_text(painter, rect)

    def _streaming_preview_font(self) -> QFont:
        """Font used for live preview text (user-configurable size)."""
        return QFont("Segoe UI", self._streaming_font_size)

    def refresh_streaming_font_size(self):
        """Reload preview font size from settings and reflow if needed."""
        new_size = resolve_streaming_overlay_font_size()
        if new_size == self._streaming_font_size:
            return
        self._streaming_font_size = new_size
        if self._streaming_preview_text:
            self._apply_streaming_height()
            self.update()

    def _draw_streaming_preview_text(self, painter: QPainter, rect: QRect):
        """Draw wrapped streaming preview text under the particle band.

        Aligns to the bottom so the newest words stay visible when the text is
        taller than the overlay's height cap.
        """
        top = self._base_height - 8
        text_rect = QRect(10, top, rect.width() - 20, max(20, rect.height() - top - 8))
        painter.setPen(QPen(QColor(245, 245, 247)))
        painter.setFont(self._streaming_preview_font())
        painter.drawText(
            text_rect,
            int(
                Qt.AlignmentFlag.AlignLeft
                | Qt.AlignmentFlag.AlignBottom
                | Qt.TextFlag.TextWordWrap
            ),
            self._streaming_preview_text,
        )

    def clear_streaming_text(self):
        """Clear live preview text and restore the compact overlay size."""
        self._streaming_preview_text = ""
        self._apply_streaming_height()

    def update_streaming_text(self, text: str, is_final: bool = True):
        """Update live preview text shown during streaming recording.

        Args:
            text: Full preview transcript so far.
            is_final: Unused; kept for API compatibility with prior overlay.
        """
        self._streaming_preview_text = (text or "").strip()
        self._apply_streaming_height()
        self.update()

    def _available_geometry_for_anchor(self) -> Optional[QRect]:
        """Return available screen geometry for the current overlay anchor."""
        point = self._anchor_pos if self._anchor_pos is not None else QCursor.pos()
        screen = QApplication.screenAt(point)
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen is None:
            return None
        return screen.availableGeometry()

    def _reposition_near_anchor(self):
        """Place the unobtrusive pill in the lower-right screen corner."""
        available = self._available_geometry_for_anchor()
        if available is None:
            return
        x = available.right() - self.overlay_width - 24
        y = available.bottom() - self.overlay_height - 24
        self.move(x, y)

    def _effective_streaming_max_height(self) -> int:
        """Soft config max, further limited by free space near the anchor."""
        available = self._available_geometry_for_anchor()
        if available is None or self._anchor_pos is None:
            return self._streaming_max_height
        return max_height_for_anchor(
            self._anchor_pos,
            available,
            self._streaming_max_height,
        )

    def _apply_streaming_height(self):
        """Keep live status compact instead of growing over the active app."""
        if self.overlay_height != self._base_height:
            self.overlay_height = self._base_height
            self.setFixedSize(self.overlay_width, self.overlay_height)
        self._reposition_near_anchor()

    def _draw_background(self, painter: QPainter):
        """Draw the frosted rounded background matching the app theme."""
        # Inset by half the pen width so the 1px border isn't clipped.
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        radius = rect.height() / 2
        path.addRoundedRect(rect, radius, radius)

        painter.fillPath(path, QColor(22, 26, 33, 188))
        painter.setPen(QPen(QColor(255, 255, 255, 32), 1))
        painter.drawPath(path)

    def _draw_particle_swarm(self, painter: QPainter):
        """Render the active STT particle list with a glow halo for fresh particles."""
        painter.setPen(Qt.PenStyle.NoPen)
        for particle in self.stt_particles:
            color = particle.get_color()
            painter.setBrush(color)
            size = particle.size * particle.life
            painter.drawEllipse(QRectF(
                particle.x - size, particle.y - size,
                size * 2, size * 2
            ))

            if particle.life > 0.3:
                glow_color = QColor(color)
                glow_color.setAlpha(100)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(glow_color, 1))
                glow_size = size + 3
                painter.drawEllipse(QRectF(
                    particle.x - glow_size, particle.y - glow_size,
                    glow_size * 2, glow_size * 2
                ))
                painter.setPen(Qt.PenStyle.NoPen)

    def _draw_stt_enable_state(self, painter: QPainter):
        """Draw STT enable state with power up particle effect."""
        rect = self.rect()
        w, h = rect.width(), rect.height()

        # Draw checkmark first (behind particles) - fades in after particles converge
        if self.animation_time > 0.4:
            progress = min(1.0, (self.animation_time - 0.4) / 0.3)
            alpha = int(200 * progress)
            painter.setPen(_round_pen(QColor(48, 209, 88, alpha), 3))
            painter.drawLine(int(w // 2 - 15), int(h // 2), int(w // 2 - 5), int(h // 2 + 10))
            painter.drawLine(int(w // 2 - 5), int(h // 2 + 10), int(w // 2 + 15), int(h // 2 - 10))

        self._draw_particle_swarm(painter)

        # Status text
        painter.setPen(QPen(QColor(245, 245, 247)))
        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        painter.drawText(rect.adjusted(0, h - 25, 0, 0), Qt.AlignmentFlag.AlignCenter, "Включено")

    def _draw_stt_disable_state(self, painter: QPainter):
        """Draw STT disable state with power down particle effect."""
        rect = self.rect()
        w, h = rect.width(), rect.height()

        # Draw X first (behind particles) - appears quickly then particles explode from it
        if self.animation_time > 0.1:
            progress = min(1.0, (self.animation_time - 0.1) / 0.2)
            alpha = int(200 * progress)
            x_size = 15
            painter.setPen(_round_pen(QColor(255, 69, 58, alpha), 3))
            painter.drawLine(w // 2 - x_size, h // 2 - x_size, w // 2 + x_size, h // 2 + x_size)
            painter.drawLine(w // 2 + x_size, h // 2 - x_size, w // 2 - x_size, h // 2 + x_size)

        self._draw_particle_swarm(painter)

        # Status text
        painter.setPen(QPen(QColor(245, 245, 247)))
        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        painter.drawText(rect.adjusted(0, h - 25, 0, 0), Qt.AlignmentFlag.AlignCenter, "Выключено")

    def _draw_copied_state(self, painter: QPainter):
        """Draw copied to clipboard state with sparkle particle effect."""
        rect = self.rect()
        w, h = rect.width(), rect.height()

        # Draw clipboard icon first (behind particles) - fades in after particles converge
        if self.animation_time > 0.3:
            progress = min(1.0, (self.animation_time - 0.3) / 0.3)
            alpha = int(220 * progress)

            # Draw a stylized clipboard/document icon
            icon_color = QColor(100, 210, 255, alpha)
            painter.setPen(_round_pen(icon_color, 2))

            # Clipboard body
            cx, cy = w // 2, h // 2 - 5
            painter.drawRoundedRect(cx - 12, cy - 10, 24, 28, 3, 3)

            # Clipboard clip at top
            painter.drawRect(cx - 6, cy - 14, 12, 6)

            # Lines representing text
            painter.setPen(_round_pen(icon_color, 1.5))
            painter.drawLine(cx - 7, cy + 2, cx + 7, cy + 2)
            painter.drawLine(cx - 7, cy + 8, cx + 5, cy + 8)

        self._draw_particle_swarm(painter)

        # Status text
        painter.setPen(QPen(QColor(245, 245, 247)))
        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        painter.drawText(rect.adjusted(0, h - 25, 0, 0), Qt.AlignmentFlag.AlignCenter, "Скопировано")

    def _draw_cleaning_state(self, painter: QPainter):
        """Draw AI cleanup state with twinkling purple sparkles."""
        rect = self.rect()
        w, h = rect.width(), rect.height()
        purple = QColor(191, 90, 242)  # Apple system purple

        # Sparkle layout: (x_frac, y_frac, base_size, twinkle_phase). Phases are
        # staggered so the sparkles shimmer in sequence rather than in unison.
        sparkles = (
            (0.50, 0.42, 11.0, 0.0),
            (0.37, 0.28, 6.0, 1.3),
            (0.64, 0.30, 7.5, 2.6),
            (0.41, 0.58, 5.0, 3.9),
            (0.61, 0.55, 6.5, 5.2),
        )
        for x_frac, y_frac, base_size, phase in sparkles:
            twinkle = 0.5 + 0.5 * math.sin(self.animation_time * 3.0 + phase)
            color = QColor(purple)
            color.setAlpha(int(80 + 175 * twinkle))
            self._draw_sparkle(
                painter,
                x_frac * w,
                y_frac * h - 4,
                base_size * (0.55 + 0.45 * twinkle),
                color,
            )

        # Status text
        painter.setPen(QPen(purple))
        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        painter.drawText(
            rect.adjusted(0, h - 25, 0, 0),
            Qt.AlignmentFlag.AlignCenter,
            "Правка текста…",
        )

    @staticmethod
    def _draw_sparkle(painter: QPainter, cx: float, cy: float, size: float, color: QColor):
        """Draw a four-pointed sparkle with short diagonal accent rays."""
        painter.setPen(_round_pen(color, 2))
        painter.drawLine(int(cx), int(cy - size), int(cx), int(cy + size))
        painter.drawLine(int(cx - size), int(cy), int(cx + size), int(cy))

        accent = QColor(color)
        accent.setAlpha(int(color.alpha() * 0.55))
        diag = size * 0.45
        painter.setPen(_round_pen(accent, 1.5))
        painter.drawLine(int(cx - diag), int(cy - diag), int(cx + diag), int(cy + diag))
        painter.drawLine(int(cx - diag), int(cy + diag), int(cx + diag), int(cy - diag))

    def set_large_file_info(self, file_size_mb: float, chunk_count: int = 0):
        """Set information about the large file being processed.

        Args:
            file_size_mb: File size in megabytes.
            chunk_count: Number of chunks (for splitting backends).
        """
        self.large_file_info = LargeFileOverlayInfo(
            file_size_mb=file_size_mb,
            chunk_count=chunk_count,
        )

    def _draw_large_file_splitting_state(self, painter: QPainter):
        """Draw large file splitting warning (for API backends)."""
        rect = self.rect()
        w, h = rect.width(), rect.height()

        # Animated scissors icon in amber
        progress = (self.animation_time * 2) % 1.0
        center_x, center_y = w // 2, h // 2 - 10

        # Scissors blades animation (opening/closing)
        blade_angle = 12 + 8 * math.sin(progress * math.pi * 2)

        amber = QColor(255, 159, 10)
        painter.setPen(_round_pen(amber, 3))

        # Draw scissors (two crossing blades)
        # Top blade
        painter.drawLine(
            int(center_x - 18), int(center_y - blade_angle),
            int(center_x + 12), int(center_y + 2)
        )
        # Bottom blade
        painter.drawLine(
            int(center_x - 18), int(center_y + blade_angle),
            int(center_x + 12), int(center_y - 2)
        )
        # Handle circles
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(int(center_x - 24), int(center_y - blade_angle - 5), 10, 10)
        painter.drawEllipse(int(center_x - 24), int(center_y + blade_angle - 5), 10, 10)

        # Status text with file size
        painter.setPen(QPen(amber))
        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        text = f"Splitting ({self.large_file_info.file_size_mb:.1f} MB)..."
        painter.drawText(rect.adjusted(0, h - 25, 0, 0), Qt.AlignmentFlag.AlignCenter, text)

    def _draw_large_file_processing_state(self, painter: QPainter):
        """Draw large file processing info (for local backend)."""
        rect = self.rect()
        w, h = rect.width(), rect.height()

        # Animated timer/clock in cyan
        progress = (self.animation_time * 0.5) % 1.0
        center_x, center_y = w // 2, h // 2 - 10
        radius = 18

        cyan = QColor(100, 210, 255)
        painter.setPen(_round_pen(cyan, 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)

        # Clock circle
        painter.drawEllipse(center_x - radius, center_y - radius, radius * 2, radius * 2)

        # Clock hands (rotating)
        hand_angle = progress * 2 * math.pi - math.pi / 2
        hand_length = radius - 5
        hand_x = center_x + int(hand_length * math.cos(hand_angle))
        hand_y = center_y + int(hand_length * math.sin(hand_angle))
        painter.drawLine(center_x, center_y, hand_x, hand_y)

        # Short hour hand
        hour_angle = progress * 2 * math.pi / 12 - math.pi / 2
        hour_length = radius - 10
        hour_x = center_x + int(hour_length * math.cos(hour_angle))
        hour_y = center_y + int(hour_length * math.sin(hour_angle))
        painter.setPen(_round_pen(cyan, 3))
        painter.drawLine(center_x, center_y, hour_x, hour_y)

        # Center dot
        painter.setBrush(QBrush(cyan))
        painter.drawEllipse(center_x - 3, center_y - 3, 6, 6)

        # Status text with file size
        painter.setPen(QPen(cyan))
        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        text = f"Обработка: {self.large_file_info.file_size_mb:.1f} МБ…"
        painter.drawText(rect.adjusted(0, h - 25, 0, 0), Qt.AlignmentFlag.AlignCenter, text)

    def _update_animation(self):
        """Compatibility hook; compact states intentionally do not animate."""
        self.timer.stop()
        self.update()

    def set_state(self, state: str):
        """Set the overlay state."""
        if not resolve_overlay_state_visibility().get(state, True):
            self.hide()
            return
        if self.current_state != state:
            self.current_state = state
            self.animation_time = 0.0
            self.cancel_progress = 0.0
            self.last_frame_time = time.time()  # Reset to prevent huge delta on first frame

            self.stt_particles = []
            self.timer.stop()

            if state != self.STATE_STREAMING:
                self._streaming_preview_text = ""
                if self.overlay_height != self._base_height:
                    self.overlay_height = self._base_height
                    self.setFixedSize(self.overlay_width, self.overlay_height)

            self.state_changed.emit(state)
            logger.debug(f"Overlay state changed to: {state}")
            self.update()

            # Auto-hide after delay for certain states
            if state in [self.STATE_STT_ENABLE, self.STATE_STT_DISABLE, self.STATE_COPIED]:
                self.hidden_timer.start(config.OVERLAY_HIDE_DELAY_MS)

    def _init_particles(
        self,
        count: int,
        hue_range: tuple,
        mode: str,
        speed_range: tuple,
        size_range: tuple,
        edge_radius: tuple = (50, 90),
        velocity_jitter: float = 15.0,
        center_jitter: float = 8.0,
    ):
        """Initialize STT particles in either a converging or exploding pattern.

        Args:
            count: Number of particles to spawn.
            hue_range: (min, max) HSV hue for particle color.
            mode: 'converge' (spawn at edges, fly inward) or 'explode' (spawn near
                center, fly outward).
            speed_range: (min, max) particle speed.
            size_range: (min, max) particle radius.
            edge_radius: 'converge' only — (min, max) spawn distance from center.
            velocity_jitter: 'converge' only — random vx/vy noise added per particle.
            center_jitter: 'explode' only — half-width of the random spawn box around center.
        """
        self.stt_particles = []
        center_x = self.overlay_width // 2
        center_y = self.overlay_height // 2 - 5

        for i in range(count):
            angle = (i / count) * 2 * math.pi + random.uniform(-0.3, 0.3)
            speed = random.uniform(*speed_range)
            hue = random.uniform(*hue_range)

            if mode == 'converge':
                radius = random.uniform(*edge_radius)
                x = center_x + radius * math.cos(angle)
                y = center_y + radius * math.sin(angle)
                vx = -math.cos(angle) * speed + random.uniform(-velocity_jitter, velocity_jitter)
                vy = -math.sin(angle) * speed + random.uniform(-velocity_jitter, velocity_jitter)
            else:  # 'explode'
                x = center_x + random.uniform(-center_jitter, center_jitter)
                y = center_y + random.uniform(-center_jitter, center_jitter)
                vx = math.cos(angle) * speed
                vy = math.sin(angle) * speed

            particle = STTParticle(x, y, vx, vy, hue)
            particle.size = random.uniform(*size_range)
            self.stt_particles.append(particle)

    def _update_stt_particles(self, dt: float):
        """Update STT particle positions and apply forces."""
        center_x = self.overlay_width // 2
        center_y = self.overlay_height // 2 - 5

        alive_particles = []
        for particle in self.stt_particles:
            if self.current_state in [self.STATE_STT_ENABLE, self.STATE_COPIED]:
                # Power up / Copy: attract to center with swirl
                dx = center_x - particle.x
                dy = center_y - particle.y
                distance = math.sqrt(dx * dx + dy * dy)

                if distance > 3:
                    # Normalize
                    nx = dx / distance
                    ny = dy / distance

                    # Strong attraction + swirl
                    attraction = 800 / (distance + 5)
                    swirl = 200 if self.current_state == self.STATE_STT_ENABLE else 150

                    particle.vx += (nx * attraction - ny * swirl) * dt
                    particle.vy += (ny * attraction + nx * swirl) * dt
                else:
                    # At center, fade fast
                    particle.life -= dt * 3.0

            # Update physics
            if particle.update(dt, damping=0.92):
                alive_particles.append(particle)

        self.stt_particles = alive_particles

    def update_audio_levels(self, levels: List[float]):
        """Update audio level data."""
        self.audio_levels = levels[:20]  # Keep only 20 levels

        # Update style with audio levels
        if self.style:
            current_level = sum(levels) / len(levels) if levels else 0.0
            self.style.update_audio_levels(self.audio_levels, current_level)

    def hide(self):
        """Hide the overlay and stop animations."""
        # Stop animation timer
        self.timer.stop()
        self.hidden_timer.stop()

        # Reset state to IDLE to prevent hanging
        self.current_state = self.STATE_IDLE
        self.animation_time = 0.0
        self.cancel_progress = 0.0
        self._streaming_preview_text = ""
        self._anchor_pos = None
        if self.overlay_height != self._base_height:
            self.overlay_height = self._base_height
            self.setFixedSize(self.overlay_width, self.overlay_height)

        super().hide()

    def show_at_cursor(self, state: Optional[str] = None):
        """Show the compact overlay in the lower-right screen corner.

        Args:
            state: Optional state to set. If None, uses current state or RECORDING as default.
        """
        target_state = (
            state
            if state is not None
            else self.current_state
            if self.current_state != self.STATE_IDLE
            else self.STATE_RECORDING
        )
        if not resolve_overlay_state_visibility().get(target_state, True):
            self.hide()
            return

        self._anchor_pos = QCursor.pos()
        self._reposition_near_anchor()
        self.show()

        # Set state if provided, otherwise default to RECORDING
        self.set_state(target_state)

        # Height may change when entering streaming; re-clamp after state apply.
        self._reposition_near_anchor()

    def closeEvent(self, event):
        """Handle closing."""
        self.timer.stop()
        self.hidden_timer.stop()
        event.accept()
