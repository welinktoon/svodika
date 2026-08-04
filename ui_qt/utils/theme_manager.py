"""
Theme management for PyQt6 UI.
Handles stylesheet loading and theme switching.
"""
from pathlib import Path
import logging
from typing import Optional
from PyQt6.QtCore import QObject, pyqtSignal


def load_theme_stylesheet(theme_path: Path) -> str:
    """Load QSS and resolve asset paths for native Qt controls."""
    if not theme_path.exists():
        return ""
    asset_dir = (theme_path.parent.parent / "assets").resolve().as_posix()
    return theme_path.read_text(encoding="utf-8").replace(
        "@ASSET_DIR@", asset_dir
    )


class ThemeManager(QObject):
    """Manages application theme and stylesheet."""

    theme_changed = pyqtSignal(str)  # Emitted when theme changes

    def __init__(self):
        """Initialize theme manager."""
        super().__init__()
        self.current_theme = "dark"
        self._load_stylesheet()

    def _load_stylesheet(self) -> Optional[str]:
        """Load and cache the stylesheet."""
        try:
            theme_path = Path(__file__).parent.parent / "styles" / "dark.qss"
            if theme_path.exists():
                self._stylesheet = load_theme_stylesheet(theme_path)
                return self._stylesheet
        except Exception as e:
            logging.warning(f"Error loading stylesheet: {e}")

        return None

    @property
    def stylesheet(self) -> str:
        """Get the current stylesheet."""
        return getattr(self, '_stylesheet', '')

    def set_theme(self, theme_name: str):
        """Set the application theme."""
        self.current_theme = theme_name
        self.theme_changed.emit(theme_name)

    def get_color(self, color_name: str) -> str:
        """Get a color value from the theme."""
        colors = {
            "primary": "#60a5fa",
            "primary_hover": "#79b4ff",
            "secondary": "#93a1b5",
            "danger": "#f87171",
            "success": "#72d69b",
            "accent": "#60a5fa",
            "background": "#111722",
            "surface": "#17202d",
            "border": "#2b3749",
            "text": "#f3f6fb",
            "text_secondary": "#9aa8bc",
        }
        return colors.get(color_name, "#ffffff")
