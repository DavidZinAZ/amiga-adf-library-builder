"""Deterministic overwrite / tamper / conflict-detection regression coverage.

These tests pin the fix for the deterministic overwrite-conflict regression. Root cause of the flaky qualification
test was two-fold:

  * A) ``pipeline._run_id()`` used a bare second-granularity wall-clock stamp
    with no uniqueness component, so two operations started within the same
    second could reuse / overwrite the same ``work/staging/<run-id>`` tree.
  * B) The qualification test wrote a tamper into a wall-clock-named staging dir
    but then re-ran verify-only with a freshly generated wall-clock run id, so
    the verifier inspected a DIFFERENT (empty) staging dir. The conflict was
    only detected when the clock was still in the same second.

These tests prove the behavior is now deterministic and that safety is intact.

Convention: ``export_all`` writes beneath ``<staging_dir>/<run_id>``.
Every helper here uses that exact root so writes and verify-only reads land on
the same tree.
"""

from __future__ import annotations

import os

from pathlib import Path

import pytest

from amiga_adf_library_builder import artwork as artwork_mod
from amiga_adf_library_builder.exporter import export_all, export_release
from amiga_adf_library_builder.grouper import group_records
from amiga_adf_library_builder.models import ParsedRecord, ReleaseGroup
from amiga_adf_library_builder.parser import parse_filename
from amiga_adf_library_builder.pipeline import _run_id, run_pipeline

AW = artwork_mod.ARTWORK_MAX_W
AH = artwork_mod.ARTWORK_MAX_H


def _write_original(original_dir: Path, name: str, content: bytes = b"A" * 2048):
    original_dir.mkdir(parents=True, exist_ok=True)
    (original_dir / name).write_bytes(content)


def _staging_root(staging_dir: Path, run_id: str) -> Path:
    return staging_dir / run_id


# --- 1. Two operations in the same second cannot reuse the same run state ----


def test_run_id_unique_within_same_second():
    a = _run_id()
    b = _run_id()
    assert a != b, "two run ids in the same second must not collide"
    # Timestamp prefix shared on a fast machine.
    assert a.split("-", 1)[0] == b.split("-", 1)[0]
    # Process id + monotonic counter guarantee uniqueness.
    assert os.getpid() == int(a.split("-")[1]) == int(b.split("-")[1])


def test_two_runs_same_second_distinct_staging(tmp_path):
    original_dir = tmp_path / "original"
    _write_original(original_dir, "Game One (v1.0) (Disk 1 of 1).adf", b"X")
    groups = group_records([parse_filename("Game One (v1.0) (Disk 1 of 1).adf")])

    rid1 = _run_id()
    rid2 = _run_id()
    assert rid1.split("-", 1)[0] == rid2.split("-", 1)[0]
    r1 = export_all(
        groups, staging_dir=tmp_path / "work", run_id=rid1,
        upstream_task_closed=True, verified_artwork_width=AW,
        verified_artwork_height=AH, original_dir=original_dir,
    )
    r2 = export_all(
        groups, staging_dir=tmp_path / "work", run_id=rid2,
        upstream_task_closed=True, verified_artwork_width=AW,
        verified_artwork_height=AH, original_dir=original_dir,
    )
    assert r1.staging_root != r2.staging_root
    assert (r1.staging_root / "ADF").exists()
    assert (r2.staging_root / "ADF").exists()


# --- 2. A deliberately modified staged/exported file is reported as conflict --


def test_tampered_staged_file_reported_as_conflict(tmp_path):
    original_dir = tmp_path / "original"
    _write_original(original_dir, "Game One (v1.0) (Disk 1 of 1).adf", b"REAL")
    g = group_records([parse_filename("Game One (v1.0) (Disk 1 of 1).adf")])[0]
    staging = _staging_root(tmp_path / "work", "run1")
    export_release(g, staging, original_dir=original_dir)
    victim = staging / "ADF" / "Game One ver v1.0" / "Game One ver v1.0.adf"
    victim.write_bytes(b"TAMPERED")

    written, unchanged, conflicts = export_release(
        g, staging, original_dir=original_dir, verify_only=True
    )
    assert not written
    assert conflicts
    assert victim.read_bytes() == b"TAMPERED"  # not silently clobbered


# --- 3. Verify-only does NOT erase / hide a conflict -------------------------


def test_verify_only_preserves_conflict_and_victim(tmp_path):
    original_dir = tmp_path / "original"
    _write_original(original_dir, "Game One (v1.0) (Disk 1 of 1).adf", b"REAL")
    g = group_records([parse_filename("Game One (v1.0) (Disk 1 of 1).adf")])[0]
    staging = _staging_root(tmp_path / "work", "run1")
    export_release(g, staging, original_dir=original_dir)
    victim = staging / "ADF" / "Game One ver v1.0" / "Game One ver v1.0.adf"
    victim.write_bytes(b"DO-NOT-TOUCH")

    res = export_all(
        [g], staging_dir=tmp_path / "work", run_id="run1",
        upstream_task_closed=True, verified_artwork_width=AW,
        verified_artwork_height=AH, original_dir=original_dir, verify_only=True,
    )
    assert res.conflicts, "verify-only must still report the tamper"
    assert victim.read_bytes() == b"DO-NOT-TOUCH"  # never normalized away
    assert res.files_written == []  # verify-only writes nothing


# --- 4. Full-suite order does not affect the result --------------------------
# (each test is isolated via tmp_path + explicit run ids; the qualification
#  harness also runs the target test inside a full-suite process repeatedly.)


def test_shared_run_id_write_then_verify_detects_conflict(tmp_path):
    """The exact shape of the formerly-flaky qualification test, isolated.

    Write with an explicit run id, tamper the SAME staging tree, then run
    verify-only with the SAME run id. Conflict must be detected deterministically.
    """
    original_dir = tmp_path / "original"
    names = [f"Example - Space Tactics (Disk {n} of 4)" for n in range(1, 5)]
    for n in names:
        _write_original(original_dir, f"{n}.adf", b"ORIG")
    groups = group_records([parse_filename(f"{n}.adf") for n in names])

    rid = "shared-run-id"
    export_all(
        groups, staging_dir=tmp_path / "work", run_id=rid,
        upstream_task_closed=True, verified_artwork_width=AW,
        verified_artwork_height=AH, original_dir=original_dir,
    )
    staging = _staging_root(tmp_path / "work", rid)
    victim = staging / "ADF" / "Example Space Tactics" / "Example Space Tactics-1.adf"
    victim.write_bytes(b"TAMPERED")
    res = export_all(
        groups, staging_dir=tmp_path / "work", run_id=rid,
        upstream_task_closed=True, verified_artwork_width=AW,
        verified_artwork_height=AH, original_dir=original_dir, verify_only=True,
    )
    assert res.conflicts, "expected at least one conflict on shared run id"
    assert victim.read_bytes() == b"TAMPERED"


# --- 5. Repeated clean runs remain deterministic -----------------------------


def test_repeated_verify_only_stable(tmp_path):
    original_dir = tmp_path / "original"
    _write_original(original_dir, "Game One (v1.0) (Disk 1 of 1).adf", b"REAL")
    g = group_records([parse_filename("Game One (v1.0) (Disk 1 of 1).adf")])[0]
    staging = _staging_root(tmp_path / "work", "run1")
    export_release(g, staging, original_dir=original_dir)
    victim = staging / "ADF" / "Game One ver v1.0" / "Game One ver v1.0.adf"
    victim.write_bytes(b"TAMPERED")

    for _ in range(3):
        res = export_all(
            [g], staging_dir=tmp_path / "work", run_id="run1",
            upstream_task_closed=True, verified_artwork_width=AW,
            verified_artwork_height=AH, original_dir=original_dir,
            verify_only=True,
        )
        assert res.conflicts
        assert victim.read_bytes() == b"TAMPERED"


# --- 6. Existing release-basename collision protections remain intact --------


def test_basename_collision_guard_still_refuses_distinct_release(tmp_path):
    original_dir = tmp_path / "original"
    _write_original(original_dir, "CollisionOne (Disk 1 of 1).adf", b"ONE")
    _write_original(original_dir, "CollisionTwo (Disk 1 of 1).adf", b"TWO")
    r1 = parse_filename("CollisionOne (Disk 1 of 1).adf")
    r1.disk_number = 1
    r2 = parse_filename("CollisionTwo (Disk 1 of 1).adf")
    r2.disk_number = 1
    gx = ReleaseGroup(
        release_key="k1", title="CollisionName", edition=None, group=None,
        chipset=None, language=None, version=None, alt_marker=None, ext="adf",
        records=[r1], disks=[r1], specials=[],
        has_main_disk=True, is_complete=True,
    )
    gy = ReleaseGroup(
        release_key="k2", title="CollisionName", edition=None, group=None,
        chipset=None, language=None, version=None, alt_marker=None, ext="adf",
        records=[r2], disks=[r2], specials=[],
        has_main_disk=True, is_complete=True,
    )
    res = export_all(
        [gx, gy], staging_dir=tmp_path / "work", run_id="run1",
        upstream_task_closed=True, verified_artwork_width=AW,
        verified_artwork_height=AH, original_dir=original_dir,
    )
    assert res.releases_exported == 1
    assert any("folder collision" in c for c in res.conflicts)


# --- 7. Originals and SD-card remain untouched -------------------------------


def test_export_never_writes_original(tmp_path):
    original_dir = tmp_path / "original"
    _write_original(original_dir, "Game One (v1.0) (Disk 1 of 1).adf", b"REAL")
    before = {p.name: p.read_bytes() for p in original_dir.iterdir()}

    # Real export stages from originals.
    g = group_records([parse_filename("Game One (v1.0) (Disk 1 of 1).adf")])[0]
    staging = _staging_root(tmp_path / "work", "run1")
    export_release(g, staging, original_dir=original_dir)

    # Tamper the staged output, then verify-only: must report conflict, must
    # not touch originals, and must not overwrite or hide the tampered victim.
    victim = staging / "ADF" / "Game One ver v1.0" / "Game One ver v1.0.adf"
    victim.write_bytes(b"TAMPERED")
    res = export_all(
        [g], staging_dir=tmp_path / "work", run_id="run1",
        upstream_task_closed=True, verified_artwork_width=AW,
        verified_artwork_height=AH, original_dir=original_dir, verify_only=True,
    )
    assert res.conflicts
    after = {p.name: p.read_bytes() for p in original_dir.iterdir()}
    assert before == after  # originals byte-identical
    assert victim.read_bytes() == b"TAMPERED"  # victim preserved by verify-only
