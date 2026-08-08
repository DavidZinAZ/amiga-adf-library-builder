"""Test isolation for portable path configuration portable-path configuration.

Every test that resolves a :class:`PathConfig` must NOT be influenced by a real
operator config on the host (XDG per-user config, system-wide config) or by
``AMIGA_ADF_*`` environment overrides. This fixture isolates those sources so
tests are deterministic regardless of where they run.

It also removes any stale ``AMIGA_ADF_*`` variables from the environment for the
duration of the test session.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_path_config(monkeypatch, tmp_path_factory):
    """Force all path discovery to temp dirs; clear AMIGA_ADF_* env vars."""
    xdg_config = tmp_path_factory.mktemp("xdg-config")
    xdg_cache = tmp_path_factory.mktemp("xdg-cache")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config))
    monkeypatch.setenv("XDG_CACHE_HOME", str(xdg_cache))
    for key in list(os.environ):
        if key.startswith("AMIGA_ADF_"):
            monkeypatch.delenv(key, raising=False)
    # Ensure the system-wide config location is never consulted in tests.
    monkeypatch.setattr(
        "amiga_adf_library_builder.paths.SYSTEM_CONFIG",
        Path(xdg_config) / "nope" / "config.toml",
    )
    yield
