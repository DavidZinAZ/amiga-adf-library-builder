"""Theme management for the Windows GUI (Light / Dark / System).

Themes are plain QSS strings loaded by name. A ``System`` theme follows the
host OS setting (resolved via the Qt style hint at apply time). Additional
themes can be dropped into the themes directory later; the manager discovers
them as ``<name>.qss`` files and exposes their names.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

#: Built-in theme names. New built-in themes are added here.
BUILTIN_THEMES: tuple[str, ...] = ("light", "dark", "system")

#: Fallback theme used when an unknown name is requested.
DEFAULT_THEME = "system"


def _light_qss() -> str:
    return """
QWidget {
    background-color: #f5f5f5;
    color: #1c1c1c;
    font-size: 10pt;
}
QMainWindow, QDialog {
    background-color: #ececec;
}
QPushButton {
    background-color: #ffffff;
    border: 1px solid #b0b0b0;
    border-radius: 4px;
    padding: 5px 12px;
}
QPushButton:hover { background-color: #e6f0ff; }
QPushButton:disabled { color: #9e9e9e; background-color: #f0f0f0; }
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTextEdit {
    background-color: #ffffff;
    border: 1px solid #b0b0b0;
    border-radius: 3px;
    padding: 3px;
}
QGroupBox {
    border: 1px solid #b0b0b0;
    border-radius: 4px;
    margin-top: 8px;
    padding-top: 6px;
}
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 3px; }
QProgressBar {
    border: 1px solid #b0b0b0;
    border-radius: 3px;
    text-align: center;
}
QProgressBar::chunk { background-color: #2f6fb0; }
QTabWidget::pane { border: 1px solid #b0b0b0; }
QHeaderView::section {
    background-color: #e0e0e0;
    border: 1px solid #b0b0b0;
    padding: 3px;
}
"""


def _dark_qss() -> str:
    return """
QWidget {
    background-color: #2b2b2b;
    color: #e6e6e6;
    font-size: 10pt;
}
QMainWindow, QDialog {
    background-color: #232323;
}
QPushButton {
    background-color: #3a3a3a;
    border: 1px solid #555555;
    border-radius: 4px;
    padding: 5px 12px;
}
QPushButton:hover { background-color: #454545; }
QPushButton:disabled { color: #777777; background-color: #2f2f2f; }
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTextEdit {
    background-color: #1f1f1f;
    color: #e6e6e6;
    border: 1px solid #555555;
    border-radius: 3px;
    padding: 3px;
}
QGroupBox {
    border: 1px solid #555555;
    border-radius: 4px;
    margin-top: 8px;
    padding-top: 6px;
}
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 3px; }
QProgressBar {
    border: 1px solid #555555;
    border-radius: 3px;
    text-align: center;
}
QProgressBar::chunk { background-color: #4a90d9; }
QTabWidget::pane { border: 1px solid #555555; }
QHeaderView::section {
    background-color: #333333;
    border: 1px solid #555555;
    padding: 3px;
}
"""


# Name -> QSS (built-in themes only; file themes are discovered at runtime).
_BUILTIN_QSS = {
    "light": _light_qss(),
    "dark": _dark_qss(),
}


def available_themes(themes_dir: Optional[Path] = None) -> list[str]:
    """Return the list of theme names (built-in plus any ``*.qss`` on disk)."""
    names = list(BUILTIN_THEMES)
    if themes_dir is not None:
        d = Path(themes_dir)
        if d.is_dir():
            for p in sorted(d.glob("*.qss")):
                name = p.stem
                if name not in names:
                    names.append(name)
    return names


def _resolve_system_qss() -> str:
    """Return the QSS for the OS-chosen theme (dark or light)."""
    try:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            hint = app.styleHints()
            if hint is not None and hint.colorScheme().value == 0:  # Dark
                return _BUILTIN_QSS["dark"]
    except Exception:
        # If we cannot query the OS, fall back to light.
        pass
    return _BUILTIN_QSS["light"]


def load_theme(name: str, *, themes_dir: Optional[Path] = None) -> str:
    """Return the QSS string for ``name``.

    ``system`` resolves the OS theme at call time. Unknown names fall back to
    :data:`DEFAULT_THEME`. File-based themes (``<name>.qss``) override built-ins.
    """
    name = (name or DEFAULT_THEME).lower()
    if name == "system":
        return _resolve_system_qss()

    if themes_dir is not None:
        d = Path(themes_dir)
        file_path = d / f"{name}.qss"
        if file_path.is_file():
            try:
                return file_path.read_text(encoding="utf-8")
            except OSError:
                pass

    return _BUILTIN_QSS.get(name, _BUILTIN_QSS["light"])


def apply_theme(name: str, *, themes_dir: Optional[Path] = None) -> str:
    """Apply ``name`` to the current ``QApplication``; return the applied QSS."""
    from PySide6.QtWidgets import QApplication

    qss = load_theme(name, themes_dir=themes_dir)
    app = QApplication.instance()
    if app is not None:
        app.setStyleSheet(qss)
    return qss


def _qt_in_use() -> bool:
    """Best-effort check that a QApplication exists (used in tests)."""
    if "PySide6.QtWidgets" not in sys.modules:
        return False
    try:
        from PySide6.QtWidgets import QApplication

        return QApplication.instance() is not None
    except Exception:
        return False
