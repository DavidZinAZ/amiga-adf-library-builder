"""Application entry point for the Windows GUI (PyInstaller onedir/onefile hook).

Dixie (packaging owner) wires this into the PyInstaller spec. This module owns:

  * high-DPI / multi-monitor attribute setup (before QApplication is created);
  * the portable app-base resolution (no hard-coded user/home paths);
  * construction of the portable paths, settings store, and secret store;
  * creation + ``exec()`` of :class:`MainWindow`.

It deliberately keeps the core imports lazy where useful so the GUI can be
imported headless for the equivalence tests.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import QApplication

from .layout import PortablePaths
from .main_window import MainWindow
from .secrets import PortableVaultBackend, SecretStore
from .settings import SettingsStore


class GuiApp:
    """Owns the QApplication lifecycle and primary window."""

    def __init__(
        self,
        *,
        base_dir: Optional[Path] = None,
        config_path: Optional[str] = None,
        master_password: Optional[str] = None,
    ) -> None:
        # High-DPI / multi-monitor MUST be set before QApplication is created.
        QApplication.setAttribute(Qt_AA_EnableHighDpiScaling(), True)  # type: ignore[arg-type]
        QApplication.setAttribute(Qt_AA_UseHighDpiPixmaps(), True)  # type: ignore[arg-type]

        self._app = QApplication([])
        self._paths = PortablePaths(base_dir=base_dir)
        self._settings_store = SettingsStore(self._paths.settings_file())
        self._secret_store = SecretStore.with_vault(
            self._paths.vault_file(), master_password=master_password
        )
        self._config_path = config_path
        self._window = MainWindow(
            portable_paths=self._paths,
            settings_store=self._settings_store,
            secret_store=self._secret_store,
            config_path=config_path,
        )

    def window(self) -> MainWindow:
        return self._window

    def run(self) -> int:
        self._window.show()
        return self._app.exec()


def Qt_AA_EnableHighDpiScaling():  # noqa: N802 - mirrors the Qt attribute name
    from PySide6.QtCore import Qt

    return Qt.AA_EnableHighDpiScaling


def Qt_AA_UseHighDpiPixmaps():  # noqa: N802
    from PySide6.QtCore import Qt

    return Qt.AA_UseHighDpiPixmaps


def run(
    *,
    base_dir: Optional[Path] = None,
    config_path: Optional[str] = None,
    master_password: Optional[str] = None,
) -> int:
    """Construct and run the GUI. Returns the QApplication exit code."""
    app = GuiApp(
        base_dir=base_dir, config_path=config_path, master_password=master_password
    )
    return app.run()


def main() -> None:
    """Console-script entry (``amiga-adf-gui``) for development / debugging."""
    raise SystemExit(run())


if __name__ == "__main__":
    main()
