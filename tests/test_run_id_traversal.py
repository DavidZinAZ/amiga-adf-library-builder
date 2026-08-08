"""Path-traversal / arbitrary-write regression coverage for `export --run-id`.

These tests pin the fix for the path-traversal / arbitrary-write regression. The former behavior concatenated the
caller-supplied `run_id` verbatim into ``<work-root>/work/staging/<run_id>``,
so an absolute path (``/tmp/evil``) or a ``../`` segment escaped the managed
data root and wrote export output (ADF/NFO/JPG) anywhere on disk.

Now `exporter.export_all` takes `staging_dir` (portable path configuration: derived from
``PathConfig.staging_dir``, never the private ``work/staging`` path), sanitizes
`run_id` (rejecting separators and `..`/`.` traversal) and asserts the resolved
`staging_root` stays beneath the supplied `staging_dir`. These tests prove:

  1. unsafe run ids raise ValueError and write NOTHING outside staging;
  2. safe run ids (generated and explicit single-component) still pin one tree;
  3. the default (run_id=None) path is unchanged (unique + beneath staging);
  4. determinism and overwrite-protection guarantees from the overwrite-conflict
regression are intact.

Convention: `export_all` writes beneath ``<staging_dir>/<run_id>``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from amiga_adf_library_builder import artwork as artwork_mod
from amiga_adf_library_builder.exporter import (
    _sanitize_run_id,
    export_all,
    export_release,
)
from amiga_adf_library_builder.grouper import group_records
from amiga_adf_library_builder.parser import parse_filename
from amiga_adf_library_builder.pipeline import _run_id

AW = artwork_mod.ARTWORK_MAX_W
AH = artwork_mod.ARTWORK_MAX_H


def _write_original(original_dir: Path, name: str, content: bytes = b"X" * 2048):
    original_dir.mkdir(parents=True, exist_ok=True)
    (original_dir / name).write_bytes(content)


def _staging_root(staging_dir: Path, run_id: str) -> Path:
    return staging_dir / run_id


# --- 1. _sanitize_run_id rejects traversal / absolute / separator inputs -----


@pytest.mark.parametrize(
    "bad",
    [
        "/tmp/evil",
        "/abs/path",
        "../escape",
        "../../etc/passwd",
        "a/../b",
        "a/b/c",
        "a\\b",
        "..",
        ".",
        "./here",
        "weird/..",
        "",
        "   ",
    ],
)
def test_sanitize_run_id_rejects_traversal(bad):
    with pytest.raises(ValueError):
        _sanitize_run_id(bad)


@pytest.mark.parametrize(
    "good",
    [
        "run-A",
        "conflict-run-001",
        "shared-run-id",
        "20260804T150000Z-1234-00000",  # generated-shape id
    ],
)
def test_sanitize_run_id_accepts_safe(good):
    assert _sanitize_run_id(good) == good


# --- 2. export_all refuses to write outside the staging parent --------------


def _one_group(tmp_path):
    original_dir = tmp_path / "original"
    _write_original(original_dir, "Game One (v1.0) (Disk 1 of 1).adf", b"REAL")
    g = group_records([parse_filename("Game One (v1.0) (Disk 1 of 1).adf")])[0]
    return original_dir, g


@pytest.mark.parametrize(
    "bad_run_id",
    [
        "/tmp/evil",
        "../../../../etc",
        "a/../b",
        "a\\b\\c",
    ],
)
def test_export_all_rejects_unsafe_run_id_and_writes_nothing(tmp_path, bad_run_id):
    original_dir, g = _one_group(tmp_path)
    with pytest.raises(ValueError):
        export_all(
            [g],
            staging_dir=tmp_path / "work",
            run_id=bad_run_id,
            upstream_task_closed=True,
            verified_artwork_width=AW,
            verified_artwork_height=AH,
            original_dir=original_dir,
        )
    # No staging tree was created for the bad id, and nothing escaped the
    # work root.
    assert not (tmp_path / "work" / "staging" / bad_run_id).exists()
    # The malicious target must not exist.
    evil = Path("/tmp/evil")
    assert not evil.exists() or not any(evil.iterdir())


def test_export_all_with_dotdot_escapes_nothing_on_disk(tmp_path):
    """End-to-end: `..` segment must not create files outside staging."""
    original_dir, g = _one_group(tmp_path)
    with pytest.raises(ValueError):
        export_all(
            [g],
            staging_dir=tmp_path / "work",
            run_id="../escape-attempt",
            upstream_task_closed=True,
            verified_artwork_width=AW,
            verified_artwork_height=AH,
            original_dir=original_dir,
        )
    # The escape directory must not have been created.
    assert not (tmp_path / "escape-attempt").exists()
    # staging root still only contains safe content (here, nothing).
    staging = tmp_path / "work" / "staging"
    if staging.exists():
        assert list(staging.iterdir()) == []


# --- 3. Explicit safe run id still pins one shared staging tree -------------


def test_explicit_safe_run_id_writes_beneath_staging(tmp_path):
    original_dir, g = _one_group(tmp_path)
    run_id = "pinned-run"
    res = export_all(
        [g],
        staging_dir=tmp_path / "work",
        run_id=run_id,
        upstream_task_closed=True,
        verified_artwork_width=AW,
        verified_artwork_height=AH,
        original_dir=original_dir,
    )
    assert res.errors == []
    assert res.releases_exported == 1
    # Staging root resolves strictly beneath the supplied staging_dir.
    staging_parent = (tmp_path / "work").resolve()
    assert res.staging_root.resolve().is_relative_to(staging_parent)
    assert res.staging_root == _staging_root(tmp_path / "work", run_id)
    victim = res.staging_root / "ADF" / "Game One ver v1.0" / "Game One ver v1.0.adf"
    assert victim.exists()
    assert victim.read_bytes() == b"REAL"


# --- 4. Default (no run_id) behavior unchanged: unique + beneath staging ----


def test_default_run_id_is_unique_and_beneath_staging(tmp_path):
    original_dir, g = _one_group(tmp_path)
    r1 = export_all(
        [g],
        staging_dir=tmp_path / "work",
        run_id=_run_id(),
        upstream_task_closed=True,
        verified_artwork_width=AW,
        verified_artwork_height=AH,
        original_dir=original_dir,
    )
    r2 = export_all(
        [g],
        staging_dir=tmp_path / "work",
        run_id=_run_id(),
        upstream_task_closed=True,
        verified_artwork_width=AW,
        verified_artwork_height=AH,
        original_dir=original_dir,
    )
    assert r1.run_id != r2.run_id
    assert r1.staging_root != r2.staging_root
    staging_parent = (tmp_path / "work").resolve()
    assert r1.staging_root.resolve().is_relative_to(staging_parent)
    assert r2.staging_root.resolve().is_relative_to(staging_parent)
    assert r1.releases_exported == 1 and r2.releases_exported == 1


# --- 5. Determinism / overwrite-protection still intact (export_release) -----


def test_export_release_verify_only_reports_tamper(tmp_path):
    original_dir, g = _one_group(tmp_path)
    staging = _staging_root(tmp_path / "work", "run1")
    export_release(g, staging, original_dir=original_dir)
    victim = staging / "ADF" / "Game One ver v1.0" / "Game One ver v1.0.adf"
    victim.write_bytes(b"TAMPERED")
    written, unchanged, conflicts = export_release(
        g, staging, original_dir=original_dir, verify_only=True
    )
    assert not written
    assert conflicts
    assert victim.read_bytes() == b"TAMPERED"
