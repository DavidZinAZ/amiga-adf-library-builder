#!/usr/bin/env python3
"""Targeted regression tests for R1-R4 (t_f56e7bbd DEV-REM3)."""
import sys, json, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest
from amiga_adf_library_builder.models import ParsedRecord, ReleaseGroup
from amiga_adf_library_builder.selection import select_one_per_game, rank_group
from amiga_adf_library_builder.manual_approvals import ApprovalRecord, write_approval_record, load_approvals
from amiga_adf_library_builder.gui.state import GuiState, build_pipeline_kwargs
from amiga_adf_library_builder.paths import PathConfig
from amiga_adf_library_builder.pipeline import run_pipeline, _ensure_canonical_library
from amiga_adf_library_builder.run_config import RunConfig

def _group(release_key, title="G", language="", version=""):
    rec = ParsedRecord(source_filename=f"{title}.adf", ext="adf", title=title,
                       release_key=release_key, disk_number=1, total_disks=1,
                       special_disk=False, edition=None, version=version,
                       group=None, chipset=None, language=language)
    return ReleaseGroup(release_key=release_key, title=title, edition=None,
                        group=None, chipset=None, language=language,
                        version=version, alt_marker=None, ext="adf",
                        records=[rec], disks=[rec], specials=[],
                        is_complete=True, has_main_disk=True)

class TestR1_ExactApprovalPrecedence:
    def test_exact_approval_wins_not_base_key_broadened(self):
        """R1: approval for exact release selects that release, not sibling."""
        groups = [_group("battlesquadron||||||"), _group("battlesquadron||skr||||")]
        lib = Path(tempfile.mkdtemp())
        for d in ["config"]: (lib / d).mkdir(parents=True)
        rec = write_approval_record(config_dir=lib/"config", release_keys=["battlesquadron||||||"],
                                     canonical_title="BS", approved_folder="BS")
        appr = load_approvals(lib)
        result = select_one_per_game(groups, approvals=appr)
        assert result.selected[0].release_key == "battlesquadron||||||"
        assert result.provenance["decisions"][0]["decision"] == "operator_override"

    def test_base_key_does_not_broaden(self):
        """R1: base-key form must NOT be used as fallback in _operator_override."""
        groups = [_group("game||||||a"), _group("game||||||b")]
        appr = {"game": ApprovalRecord(approval_id="a1", release_keys=["game"],
                                       canonical_title="G", approved_folder="G")}
        result = select_one_per_game(groups, approvals=appr)
        # With base-key disabled, neither matches exactly; ranking picks winner
        assert len(result.selected) == 1
        # The winner should NOT be labelled operator_override since no exact match
        assert result.provenance["decisions"][0]["decision"] != "operator_override"

class TestR2_FailClosedSelection:
    def test_selection_exception_blocks_export(self):
        """R2: selection exception -> no export, selection_failed=True."""
        orig = sys.modules["amiga_adf_library_builder.selection"].select_one_per_game
        sys.modules["amiga_adf_library_builder.selection"].select_one_per_game = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("injected"))
        try:
            WORK = Path(tempfile.mkdtemp())
            LIB = WORK / "library"
            for d in ["original","staging","output","quarantine","logs","cache","reports","approvals","curation","config"]:
                (LIB / d).mkdir(parents=True, exist_ok=True)
            (LIB / "original" / "x.adf").write_bytes(b"ADF" + b"\x00"*900)
            res = run_pipeline(
            cfg=PathConfig(library_root=LIB,original_dir=LIB/"original",staging_dir=LIB/"staging",output_dir=LIB/"output",quarantine_dir=LIB/"quarantine",logs_dir=LIB/"logs",cache_dir=LIB/"cache",reports_dir=LIB/"reports",approvals_dir=LIB/"approvals"),
            run=RunConfig(export=True, upstream_task_closed=True, run_id="r2-test"),
        )
            assert res.get("selection_failed") is True
            assert "selection_error" in res
            assert res.get("export_gate_open") is False
            assert "export" not in res
        finally:
            sys.modules["amiga_adf_library_builder.selection"].select_one_per_game = orig

class TestR3_GUISlice6Controls:
    def test_gui_state_has_slice6_fields(self):
        """R3: GuiState exposes one_per_game, operator_decisions_path, selection_manifest_path."""
        state = GuiState(one_per_game=False, operator_decisions_path="/tmp/d.json", selection_manifest_path="/tmp/m.json")
        assert state.one_per_game is False
        assert state.operator_decisions_path == "/tmp/d.json"
        assert state.selection_manifest_path == "/tmp/m.json"

    def test_build_pipeline_kwargs_forwards_slice6_controls(self):
        """R3: build_pipeline_kwargs forwards all three controls."""
        state = GuiState(library_root="/tmp/lib", one_per_game=False,
                         operator_decisions_path="/tmp/d.json", selection_manifest_path="/tmp/m.json")
        cfg = PathConfig(library_root=Path("/tmp/lib"), original_dir=Path("/tmp/lib/original"),
                         staging_dir=Path("/tmp/lib/staging"), output_dir=Path("/tmp/lib/output"),
                         quarantine_dir=Path("/tmp/lib/quarantine"), logs_dir=Path("/tmp/lib/logs"),
                         cache_dir=Path("/tmp/lib/cache"), reports_dir=Path("/tmp/lib/reports"),
                         approvals_dir=Path("/tmp/lib/approvals"))
        run_config, kwargs = build_pipeline_kwargs(state, cfg)
        assert run_config.one_per_game is False
        assert run_config.operator_decisions_path == "/tmp/d.json"
        assert run_config.selection_manifest_path == "/tmp/m.json"

class TestR4_CanonicalRegionLanguageEffective:
    def test_canonical_availability_creates_db(self):
        """R4: _ensure_canonical_library creates canonical.db when absent."""
        WORK = Path(tempfile.mkdtemp())
        LIB = WORK / "library"
        (LIB / "curation").mkdir(parents=True)
        groups = [_group("game|a", language="EN"), _group("game|b", language="DE")]
        canon = _ensure_canonical_library(LIB, groups)
        assert canon is not None
        assert (LIB / "curation" / "canonical.db").is_file()
        canon.close()

if __name__ == "__main__":
    pytest.main([__file__, "-v"])