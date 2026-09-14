"""Canonical application version source.

This module is the single source of truth for the application version.
It reads ``pyproject.toml [project].version`` at import time so that
every downstream module derives from the same value — no hardcoded
strings can drift.

The version string is intentionally the exact value from pyproject.toml
(e.g. ``"0.2.13"``).  Callers that need a display-ready form can use
``__version__`` directly.
"""

from __future__ import annotations

import re
from pathlib import Path

# Locate pyproject.toml relative to this file:
# _version.py -> amiga_adf_library_builder/ -> src/ -> repo root
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

__all__ = ["__version__"]
