"""Заставка запуска приложения."""

import logging
import math
import time
from pathlib import Path

from PyQt6.QtCore import QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PyQt6.QtWidgets import QApplication, QWidget

logger = logging.getLogger(__name__)

_GLOW_RADIANS_PER_SEC = 4.5
_ASSETS_DIR = Path(__file__).resolve().parent / "assets"


class LoadingScreen(QWidget):
    """Компактная заставка с логотипом записи и расшифровки."""

    finished = pyqtSignal()

    def __init__(self):
        super().__init__()

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Window
        )
        self.setWindowIcon(
            QIcon(str(_ASSETS_DIR / "meeting-recorder-logo.ico"))
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(450, 300)

        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            screen.center().x() - self.width() // 2,
            screen.center().y() - self.height() // 2,
        )

        self.progress_text = "Подождите немного…"
        self.logo = QPixmap(
            str(_ASSETS_DIR / "meeting-recorder-mark.png")
        )

        self.bg_color = QColor("#15171b")
        self.accent_color = QColor("#2d8cff")
        self.text_color = QColor("#f7f8fb")
        self.subtext_color = QColor("#9ba4b4")

        self._glow_started_at = time.monotonic()
        self._glow_timer = QTimer(self)
        self._glow_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._glow_timer.setInterval(33)
        self._glow_timer.timeout.connect(self.update)
        self._glow_timer.start()

    def _glow_phase(self) -> float:
        elapsed = time.monotonic() - self._glow_started_at
        return elapsed * _GLOW_RADIANS_PER_SEC

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        rect = self.rect()
        width, height = rect.width(), rect.height()

        background = QLinearGradient(0, 0, 0, height)
        background.setColorAt(0, self.bg_color)
        background.setColorAt(1, QColor("#101216"))

        panel = QPainterPath()
        panel.addRoundedRect(QRectF(rect).adjusted(1, 1, -1, -1), 18, 18)
        painter.fillPath(panel, background)
        painter.setPen(QPen(QColor("#2a2f39"), 1))
        painter.drawPath(panel)

        center_x = width / 2
        logo_center_y = 92
        pulse = 0.5 + 0.5 * math.sin(self._glow_phase())

        halo_radius = 72 + pulse * 8
        halo = QRadialGradient(center_x, logo_center_y, halo_radius)
        halo.setColorAt(0, QColor(45, 140, 255, int(40 + pulse * 28)))
        halo.setColorAt(0.55, QColor(45, 140, 255, int(12 + pulse * 12)))
        halo.setColorAt(1, QColor(45, 140, 255, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(halo))
        painter.drawEllipse(
            QRectF(
                center_x - halo_radius,
                logo_center_y - halo_radius,
                halo_radius * 2,
                halo_radius * 2,
            )
        )

        logo_size = 112
        if not self.logo.isNull():
            painter.drawPixmap(
                QRectF(
                    center_x - logo_size / 2,
                    logo_center_y - logo_size / 2,
                    logo_size,
                    logo_size,
                ),
                self.logo,
                QRectF(self.logo.rect()),
            )

        painter.setPen(self.text_color)
        painter.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        painter.drawText(
            QRectF(0, 166, width, 32),
            Qt.AlignmentFlag.AlignCenter,
            "Svodika",
        )

        painter.setPen(self.subtext_color)
        painter.setFont(QFont("Segoe UI", 9))
        painter.drawText(
            QRectF(24, 221, width - 48, 20),
            Qt.AlignmentFlag.AlignCenter,
            self.progress_text,
        )

        bar_rect = QRectF(142, 272, 166, 3)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#2a2f39"))
        painter.drawRoundedRect(bar_rect, 1.5, 1.5)

        sweep_width = 52
        sweep_range = bar_rect.width() + sweep_width
        sweep_x = bar_rect.x() - sweep_width + (
            (time.monotonic() - self._glow_started_at) * 72
        ) % sweep_range
        painter.save()
        painter.setClipRect(bar_rect)
        painter.setBrush(self.accent_color)
        painter.drawRoundedRect(
            QRectF(sweep_x, bar_rect.y(), sweep_width, bar_rect.height()),
            1.5,
            1.5,
        )
        painter.restore()

    def update_status(self, status_text: str):
        """Compatibility alias for the single visible loading message."""
        self.update_progress(status_text)

    def update_progress(self, progress_text: str):
        self.progress_text = progress_text
        self.update()

    def closeEvent(self, event):
        self._glow_timer.stop()
        event.accept()
        logger.info("Заставка запуска закрыта")

    def destroy(self, destroyWindow=True, destroySubWindows=True):
        self._glow_timer.stop()
        super().destroy(destroyWindow, destroySubWindows)
