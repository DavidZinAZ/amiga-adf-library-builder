"""Canonical application version source.

This module is the single source of truth for the application version.

In a PyInstaller-frozen runtime (sys.frozen == True), the version is read
from the build-time generated ``_frozen_version`` module, which is bundled
into the artifact. In dev/editable installs (sys.frozen absent), it reads
``pyproject.toml [project].version`` at import time so that every downstream
module derives from the same value — no hardcoded strings can drift.
"""

from __future__ import annotations

import sys

__all__ = ["__version__"]

# PyInstaller frozen runtime: use the build-time baked version.
# The generated module is shipped inside the frozen bundle; no repo-root
# traversal is required, so the frozen app does not depend on pyproject.toml
# existing alongside the shipped EXE.
if getattr(sys, "frozen", False):
    from ._frozen_version import __version__  # type: ignore[import-not-found]
else:
    # Dev / editable install: parse pyproject.toml at runtime.
    import re
    from pathlib import Path

    _REPO_ROOT = Path(__file__).resolve().parents[2]
    _PYPROJECT = _REPO_ROOT / "pyproject.toml"

    def _parse_pyproject_version() -> str:
        """Read ``[project].version`` from pyproject.toml."""
        text = _PYPROJECT.read_text(encoding="utf-8")
        match = re.search(r'^version\s*=\s*"(.*?)"', text, re.MULTILINE)
        if not match:
            raise RuntimeError(
                f"Could not find [project].version in {_PYPROJECT}"
            )
        return match.group(1)

    __version__: str = _parse_pyproject_version()
