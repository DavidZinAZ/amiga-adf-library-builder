"""Pipeline test: corpus preservation, grouping, idempotency, export gate.

Builds isolated temporary layouts with synthetic fixtures only. No maintainer
collection, host path, or external corpus is required.
"""
from pathlib import Path

import pytest

from amiga_adf_library_builder.exporter_guard import export_gate_open
from amiga_adf_library_builder.paths import PathConfig, resolve_config
from amiga_adf_library_builder.pipeline import run_pipeline


def _cfg(root: Path) -> PathConfig:
    return resolve_config(library_root=str(root))[0]


def test_exporter_gate_blocked_without_upstream_close() -> None:
    open_, reason = export_gate_open(
        upstream_task_closed=False, verified_artwork_width=None, verified_artwork_height=None
    )
    assert open_ is False
    assert "upstream Gotek requirements verification" in reason


def test_exporter_gate_blocked_without_verified_dims() -> None:
    open_, reason = export_gate_open(
        upstream_task_closed=True, verified_artwork_width=None, verified_artwork_height=None
    )
    assert open_ is False
    assert "dimensions" in reason.lower()


def test_pipeline_runs_on_synthetic_corpus_and_preserves_originals(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    (data_root / "original").mkdir(parents=True)
    # 18 synthetic "files" for a stable group count.
    names = [
        "Example - Space Tactics (Disk 1 of 4).adf",
        "Example - Space Tactics (Disk 2 of 4).adf",
        "Example - Space Tactics (Disk 3 of 4).adf",
        "Example - Space Tactics (Disk 4 of 4).adf",
        "Example - Space Tactics (Disk A).adf",
        "Example_Quest_III_Boot.adf",
        "Example_Quest_III_Character.adf",
        "Example_Qest3_Char.adf",
        "Example_Castle_Quest_Disk_A.adf",
        "E.X.A.M.P.L.E. II - Galactic Bureau (Disk 1 of 5).adf",
        "E.X.A.M.P.L.E. II - Galactic Bureau (Disk 2 of 5).adf",
        "E.X.A.M.P.L.E. II - Galactic Bureau (Disk 3 of 5).adf",
        "E.X.A.M.P.L.E. II - Galactic Bureau (Disk 4 of 5).adf",
        "E.X.A.M.P.L.E. II - Galactic Bureau (Disk 5 of 5).adf",
        "Solo Game (Disk 1 of 1).adf",
        "Another Game (Disk 1 of 1).adf",
        "Special Only A.adf",
        "Special Only B.adf",
    ]
    for n in names:
        (data_root / "original" / n).write_bytes(b"x" * 10)

    before = {p.name: p.read_bytes() for p in (data_root / "original").iterdir() if p.is_file()}
    result = run_pipeline(cfg=_cfg(data_root), online=False)
    after = {p.name: p.read_bytes() for p in (data_root / "original").iterdir() if p.is_file()}

    # Acceptance A1: originals untouched (we only read them).
    assert before == after
    assert result["original_preserved"] is True
    assert result["files_scanned"] == 18
    assert result["export_gate_open"] is False  # Phase 5 hard-blocked
    # Quarantine: special-only sets routed.
    assert len(result["unknown_routed"]) >= 1


def test_pipeline_idempotent_catalog(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    orig = data_root / "original"
    orig.mkdir(parents=True)
    for n in range(1, 5):
        (orig / f"Example - Space Tactics (Disk {n} of 4).adf").write_bytes(b"x" * 10)
    r1 = run_pipeline(cfg=_cfg(data_root), run_id="run-1")
    r2 = run_pipeline(cfg=_cfg(data_root), run_id="run-2")
    # Catalog appends new scan/parse lines only once per unique file.
    assert r1["catalog_new_scan"] == 4
    assert r2["catalog_new_scan"] == 0
    assert r1["catalog_new_parse"] == 4
    assert r2["catalog_new_parse"] == 0
