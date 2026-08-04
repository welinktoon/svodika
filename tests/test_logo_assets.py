import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QIcon, QImage
from PyQt6.QtWidgets import QApplication


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSETS = PROJECT_ROOT / "ui_qt" / "assets"


def _alpha_bbox(name: str) -> tuple[int, int, int, int]:
    image = QImage(str(ASSETS / name)).convertToFormat(
        QImage.Format.Format_RGBA8888
    )
    assert not image.isNull()
    assert (image.width(), image.height()) == (1024, 1024)
    assert image.hasAlphaChannel()
    assert all(
        image.pixelColor(x, y).alpha() == 0
        for x, y in ((0, 0), (1023, 0), (0, 1023), (1023, 1023))
    )

    pixels = image.constBits().asstring(image.sizeInBytes())
    alpha = pixels[3::4]
    left, top = image.width(), image.height()
    right = bottom = 0
    for index, opacity in enumerate(alpha):
        if not opacity:
            continue
        x = index % image.width()
        y = index // image.width()
        left = min(left, x)
        top = min(top, y)
        right = max(right, x + 1)
        bottom = max(bottom, y + 1)

    assert right > left and bottom > top
    return left, top, right, bottom


def test_circle_logo_is_transparent_square_and_exactly_centered():
    left, top, right, bottom = _alpha_bbox("meeting-recorder-logo.png")

    assert right - left == bottom - top
    assert left + right == 1024
    assert top + bottom == 1024


def test_microphone_mark_is_transparent_and_optically_centered():
    left, top, right, bottom = _alpha_bbox("meeting-recorder-mark.png")

    assert abs((left + right) - 1024) <= 1
    assert abs((top + bottom) - 1024) <= 1


def test_windows_icon_contains_small_and_large_frames():
    app = QApplication.instance() or QApplication([])
    icon = QIcon(str(ASSETS / "meeting-recorder-logo.ico"))

    assert not icon.isNull()
    assert {(size.width(), size.height()) for size in icon.availableSizes()} == {
        (16, 16),
        (20, 20),
        (24, 24),
        (32, 32),
        (40, 40),
        (48, 48),
        (64, 64),
        (128, 128),
        (256, 256),
    }
    assert app is QApplication.instance()


def test_internal_branding_uses_microphone_only_mark():
    loading_screen = (PROJECT_ROOT / "ui_qt" / "loading_screen.py").read_text(
        encoding="utf-8"
    )
    main_window = (PROJECT_ROOT / "ui_qt" / "main_window.py").read_text(
        encoding="utf-8"
    )

    assert 'meeting-recorder-mark.png' in loading_screen
    assert 'meeting-recorder-mark.png' in main_window
