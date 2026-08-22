"""Windows GUI subpackage for amiga-adf-library-builder.

This package is an OPTIONAL extra (``pip install amiga-adf-library-builder[gui]``
pulls PySide6). It is a presentation layer over the shared core: it builds a
:class:`~amiga_adf_library_builder.paths.PathConfig` and invokes
``pipeline.run_pipeline`` -- never reimplementing scanning/parsing/grouping/
enrichment/export/validation.

Key entry points:
  * :func:`run` / :class:`app.GuiApp` -- application entry (PyInstaller hook).
  * :func:`build_path_config_from_gui_state` -- CLI<->GUI equivalence enabler.
  * :class:`MainWindow` -- primary window.
"""

from __future__ import annotations

__version__ = "0.2.5+gui"

from .layout import PortablePaths
from .main_window import MainWindow
from .providers import (
    Provider,
    ProviderMetadata,
    ProviderRegistry,
    default_registry,
)
from .secrets import SecretStore, get_gui_redactor, install_gui_redaction
from .settings import Settings, SettingsStore
from .state import GuiState, build_path_config_from_gui_state, build_pipeline_kwargs

__all__ = [
    "PortablePaths",
    "MainWindow",
    "Provider",
    "ProviderMetadata",
    "ProviderRegistry",
    "default_registry",
    "SecretStore",
    "install_gui_redaction",
    "get_gui_redactor",
    "Settings",
    "SettingsStore",
    "GuiState",
    "build_path_config_from_gui_state",
    "build_pipeline_kwargs",
    "__version__",
]
