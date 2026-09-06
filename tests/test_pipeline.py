"""Pipeline test: corpus preservation, grouping, idempotency, export gate.

Builds isolated temporary layouts with synthetic fixtures only. No maintainer
collection, host path, or external corpus is required.
"""
from pathlib import Path

import pytest

from amiga_adf_library_builder.exporter_guard import export_gate_open
from amiga_adf_library_builder.paths import PathConfig, resolve_config
from amiga_adf_library_builder.pipeline import run_pipeline, build_staged_library_from_result


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


def test_build_staged_library_from_result_creates_populated_state(tmp_path: Path) -> None:
    """Test that build_staged_library_from_result creates a populated library state file."""
    library_root = tmp_path / "lib"
    # The curation dir must NOT pre-exist: the builder creates it itself (this is
    # exactly the fresh-library condition the Windows run exercised).

    run_id = "test-run-001"
    result = {
        "run_id": run_id,
        "groups": 3,
        "per_group": [
            {
                "release_key": "Test_Quest_III_OCS",
                "title": "Test Quest III",
                "provider": "test-provider",
                "artwork_missing": True,
                "notes": ["Synthetic test corpus entry A"],
                "quarantine_reason": None,
                "source_files": [
                    "Test Quest III (Disk 1 of 2).adf",
                    "Test Quest III (Disk 2 of 2).adf",
                ],
                "folder": "Test Quest III",
            },
            {
                "release_key": "Another_Title_Demo",
                "title": "Another Title Demo",
                "provider": "test-provider",
                "artwork_missing": False,
                "notes": ["Synthetic test corpus entry B"],
                "quarantine_reason": None,
                "source_files": [
                    "Another Title Demo (Disk 1 of 1).adf",
                    "Another Title Demo Boot.adf",
                ],
                "folder": "Another Title Demo",
            },
            {
                "release_key": "Incomplete_Special_Only",
                "title": "Incomplete Special Only",
                "provider": "test-provider",
                "artwork_missing": False,
                "notes": [],
                "quarantine_reason": "Incomplete set: only special disk",
                "source_files": ["Incomplete Special Only Boot.adf"],
                "folder": "Incomplete Special Only",
            },
        ],
    }

    state_path = build_staged_library_from_result(result, library_root=library_root, run_id=run_id)

    assert state_path is not None
    assert state_path.exists()
    assert state_path.name == f"library_state_{run_id}.json"
    # The state file lives under the managed curation dir, NOT under output/.
    assert state_path.parent == (library_root / "curation")

    # Verify the state file can be loaded and has the expected content
    from amiga_adf_library_builder.library_state import CurationStateManager
    manager = CurationStateManager(state_path)
    library = manager.load()

    # Should have 3 releases
    assert len(library.releases) == 3

    # Verify the three entries
    assert "Test_Quest_III_OCS" in library.releases
    assert "Another_Title_Demo" in library.releases
    assert "Incomplete_Special_Only" in library.releases

    # Verify curation states
    assert library.releases["Test_Quest_III_OCS"].curation_state.value == "pending"
    assert library.releases["Another_Title_Demo"].curation_state.value == "pending"
    assert library.releases["Incomplete_Special_Only"].curation_state.value == "needs_review"

    # Verify notes for artwork_missing
    assert "Artwork missing from metadata providers." in library.releases["Test_Quest_III_OCS"].notes

    # (GH-86) Original identity must be preserved: adf_files populated from the
    # discovered source inventory, and the planned export folder recorded.
    entry_a = library.releases["Test_Quest_III_OCS"]
    assert entry_a.adf_files == [
        "Test Quest III (Disk 1 of 2).adf",
        "Test Quest III (Disk 2 of 2).adf",
    ]
    assert entry_a.folder == "Test Quest III"
    assert library.releases["Another_Title_Demo"].adf_files == [
        "Another Title Demo (Disk 1 of 1).adf",
        "Another Title Demo Boot.adf",
    ]
    assert library.releases["Incomplete_Special_Only"].adf_files == [
        "Incomplete Special Only Boot.adf",
    ]

    # Verify actions were created
    for entry in library.releases.values():
        assert len(entry.actions) >= 1
        assert entry.actions[0].action.value == "state_change"
        assert "Initial state from pipeline" in entry.actions[0].details


def test_build_staged_library_from_result_returns_none_for_empty_result(tmp_path: Path) -> None:
    """Test that build_staged_library_from_result returns None when per_group is empty."""
    library_root = tmp_path / "lib"

    result = {
        "run_id": "test-empty",
        "groups": 0,
        "per_group": [],
    }

    state_path = build_staged_library_from_result(result, library_root=library_root, run_id="test-empty")

    assert state_path is None
    # No state file and no curation dir should be created for an empty run.
    assert not (library_root / "curation").exists()


def test_build_staged_library_writes_to_curation_not_output(tmp_path: Path) -> None:
    """GH-86 Windows regression: the curation state must NOT be written into the
    export output dir.

    The Windows Actions run 34016970787 failed with FileNotFoundError because the
    state file was written to ``output_dir`` (a dir that does not exist on a fresh
    library and is the export destination that must stay empty until export). This
    test pins both halves of the fix:
      * the builder creates and writes ``<library_root>/curation/`` on its own
        (no FileNotFoundError even when nothing pre-exists); and
      * the export ``output/`` dir remains absent/empty after the builder runs.
    """
    library_root = tmp_path / "lib"
    output_dir = library_root / "output"
    # A fresh library: neither curation/ nor output/ exists yet.
    assert not (library_root / "curation").exists()
    assert not output_dir.exists()

    result = {
        "run_id": "gh86-windows-run",
        "groups": 1,
        "per_group": [
            {
                "release_key": "Gh86_Space_Tactics",
                "title": "Gh86 Space Tactics",
                "provider": "offline",
                "artwork_missing": True,
                "notes": [],
                "quarantine_reason": None,
                "source_files": [
                    "Gh86 Space Tactics (Disk 1 of 4).adf",
                    "Gh86 Space Tactics (Disk 2 of 4).adf",
                    "Gh86 Space Tactics (Disk 3 of 4).adf",
                    "Gh86 Space Tactics (Disk 4 of 4).adf",
                ],
                "folder": "Gh86 Space Tactics",
            },
        ],
    }

    # Must not raise FileNotFoundError (the original Windows failure).
    state_path = build_staged_library_from_result(
        result, library_root=library_root, run_id="gh86-windows-run"
    )

    assert state_path is not None
    assert state_path.parent == (library_root / "curation")
    assert state_path.exists()

    # The export output dir must remain empty (no preview-only export writes).
    if output_dir.exists():
        files = [p for p in output_dir.rglob("*") if p.is_file()]
        assert files == []
    else:
        assert not output_dir.exists()

    # The loaded library exposes the original ADF identity for the selected row.
    from amiga_adf_library_builder.library_state import CurationStateManager

    library = CurationStateManager(state_path).load()
    entry = library.releases["Gh86_Space_Tactics"]
    assert len(entry.adf_files) == 4
    assert entry.folder == "Gh86 Space Tactics"
    assert entry.curation_state.value == "pending"
