"""Portable, app-relative directory layout for the Windows GUI.

The GUI must never hard-code user/home paths. Every runtime directory
(config, data, logs, cache, themes) is resolved relative to an *app base
directory* so the build is portable (extract-and-run, spaces in paths work).

Default base resolution (override with ``AMIGA_ADF_GUI_BASE`` or by passing
``base_dir`` explicitly, e.g. in tests):

* When frozen by PyInstaller, ``sys.executable`` lives inside the app dir, so
  its parent is the portable app root.
* In development, the same rule resolves to the venv/bin dir; callers that want
  a project-local layout should pass ``base_dir`` explicitly.

All directories are created lazily via :meth:`PortablePaths.ensure_all`.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional


def _default_base() -> Path:
    """Resolve the default app base directory (portable, never user/home)."""
    override = os.environ.get("AMIGA_ADF_GUI_BASE")
    if override:
        return Path(override).expanduser()
    # PyInstaller (and most onedir builds) place the executable inside the app
    # directory, so its parent is the portable root.
    exe = Path(sys.executable)
    if exe.name.lower().startswith("python"):
        # Development interpreter: fall back to the package directory so the GUI
        # does not litter the venv. Tests always pass base_dir explicitly.
        return Path(__file__).resolve().parents[3]
    return exe.parent.resolve()


class PortablePaths:
    """App-relative, portable directory layout (config/data/logs/cache/themes).

    No path under here may be derived from ``/home/<user>`` or the Windows user
    profile. The layout is fully relative to ``base`` so the same directory tree
    works from a USB stick, a network share, or ``C:\\Program Files``.
    """

    def __init__(self, base_dir: Optional["str | os.PathLike[str]"] = None) -> None:
        self.base = Path(base_dir).resolve() if base_dir else _default_base()

    # --- directories ---------------------------------------------------------
    @property
    def config_dir(self) -> Path:
        return self.base / "config"

    @property
    def data_dir(self) -> Path:
        return self.base / "data"

    @property
    def logs_dir(self) -> Path:
        return self.base / "logs"

    @property
    def cache_dir(self) -> Path:
        return self.base / "cache"

    @property
    def themes_dir(self) -> Path:
        return self.base / "themes"

    def all_dirs(self) -> list[Path]:
        return [
            self.config_dir,
            self.data_dir,
            self.logs_dir,
            self.cache_dir,
            self.themes_dir,
        ]

    def ensure_all(self) -> list[Path]:
        """Create every runtime directory; return the list actually created."""
        created: list[Path] = []
        for d in self.all_dirs():
            if not d.exists():
                d.mkdir(parents=True, exist_ok=True)
                created.append(d)
            elif not d.is_dir():
                raise NotADirectoryError(f"portable path is not a directory: {d}")
        return created

    def settings_file(self) -> Path:
        return self.config_dir / "gui-settings.toml"

    def vault_file(self) -> Path:
        return self.config_dir / "secrets.vault"
