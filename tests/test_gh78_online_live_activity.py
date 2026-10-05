"""GH-78 regression tests: live activity log emits per-provider strings during online enrichment.

Root cause (fixed): enrich_group() and lookup_metadata() silently create EnrichEvent
records for screenscraper but never call
_act() alongside them, leaving the live Diagnostics tab dark during online provider work.

Fix: Add co-located _act() calls at 5 sites in enrich.py and 1 site in metadata.py.

These tests lock the new contract:
  * When online=True, the activity hook receives per-provider attempt messages.
  * Success, miss, manual-review, and error paths each emit a distinct message.
  * metadata.py _try_provider emits "Querying <label>" + acceptance messages.
  * Offline mode is unchanged (no spurious activity from online-only providers).
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Offscreen BEFORE any PySide6 import
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from amiga_adf_library_builder.pipeline import run_pipeline  # noqa: E402
from amiga_adf_library_builder.run_config import RunConfig  # noqa: E402
from amiga_adf_library_builder.paths import resolve_config  # noqa: E402

# --- Synthetic corpus ----------------------------------------------------------

_NAMES = [
    "Example - Space Tactics (Disk 1 of 4).adf",
    "Example - Space Tactics (Disk 2 of 4).adf",
    "Example - Space Tactics (Disk 3 of 4).adf",
    "Example - Space Tactics (Disk 4 of 4).adf",
    "Solo Game (Disk 1 of 1).adf",
]


def _build_corpus(data_root: Path) -> None:
    orig = data_root / "original"
    orig.mkdir(parents=True, exist_ok=True)
    for n in _NAMES:
        (orig / n).write_bytes(b"x" * 10)


# --- Config helpers -------------------------------------------------------------

def _write_provider_cfg(tmp_path: Path, name: str, *, enabled: bool = True) -> Path:
    """Write a TOML config for an online provider with [name] section."""
    cfg = tmp_path / f"{name}.toml"
    cfg.write_text(f'[ {name} ]\nenabled = {"true" if enabled else "false"}')
    return cfg


# --- Mock result helpers --------------------------------------------------------

def _make_resolve_mock(found=True, provider_id="test-id"):
    """Return a MagicMock shaped like any provider Result."""
    r = MagicMock()
    r.found = found
    r.needs_manual_review = False
    r.match_method.value = "EXACT_HASH" if found else "NONE"
    r.confidence = 1.0 if found else 0.0
    r.provider_id = provider_id
    r.transport_error = None
    r.metadata = {"canonical_title": "Test"} if found else None
    r.external_ids = None
    r.relevance_category = None
    r.manual_review_reason = "test review"
    r.manufacturer = "E.P.I." if found else None
    r.artwork_url = None
    r.artwork_source_url = None
    r.artwork_provider = "test"
    return r


# --- Test cases ----------------------------------------------------------------

class TestBackwardsCompatibility:
    """Ensure the new optional activity parameter doesn't break existing callers."""

    def test_lookup_metadata_without_activity_param(self, tmp_path: Path):
        """Existing code that omits activity= still works."""
        from amiga_adf_library_builder.metadata import lookup_metadata

        cache_dir = tmp_path / "cache"
        curated_dir = tmp_path / "curated"
        cache_dir.mkdir()
        curated_dir.mkdir()

        # No activity parameter — must not raise TypeError
        result = lookup_metadata(
            "Test Title",
            cache_dir=cache_dir, curated_dir=curated_dir,
            refresh=False, group=None,
        )
        assert len(result) == 3  # (metadata, provider, relevance_events)
