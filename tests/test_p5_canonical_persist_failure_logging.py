"""GH-153 P5 regression test: canonical.db write failures must emit warnings.

Tests that _persist_canonical_library logs a warning when canonical.db
persistence fails (e.g., when the target path is a directory rather than
a file), preserving the best-effort semantics while making failures
visible to the operator.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest

from amiga_adf_library_builder.models import StagedLibrary
from amiga_adf_library_builder.pipeline import _persist_canonical_library


def test_persist_canonical_failure_logs_warning(tmp_path, caplog):
    """AR-001/P5 Item 2: canonical.db write failure must emit a warning log."""
    curation_dir = tmp_path / "curation"
    curation_dir.mkdir()
    # Make canonical.db a directory so sqlite3.connect raises OSError
    (curation_dir / "canonical.db").mkdir()

    library = StagedLibrary()
    with caplog.at_level(logging.WARNING):
        result = _persist_canonical_library(library, curation_dir)

    assert result is None
    assert any(
        "canonical.db" in record.message
        and record.levelno == logging.WARNING
        for record in caplog.records
    )


def test_persist_canonical_success_logs_no_warning(tmp_path, caplog):
    """AR-001/P5 Item 2: successful canonical.db write must not emit a warning."""
    curation_dir = tmp_path / "curation"
    curation_dir.mkdir()

    library = StagedLibrary()
    with caplog.at_level(logging.WARNING):
        result = _persist_canonical_library(library, curation_dir)

    # Result may be None (CanonicalLibrary import may need proper setup)
    # but no WARNING should be emitted on success path
    warning_records = [
        r for r in caplog.records
        if "canonical.db" in r.message and r.levelno == logging.WARNING
    ]
    assert len(warning_records) == 0
