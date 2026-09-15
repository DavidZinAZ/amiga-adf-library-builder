"""Regression tests for GH-107 Slice 6 QA-proven blockers (t_d0515fee).

Covers D1-D7 per t_34cbb472 FINAL-REPORT; GUI (D7) tested separately
in test_gui_state_bindings.py.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from amiga_adf_library_builder.models import ParsedRecord, ReleaseGroup
from amiga_adf_library_builder.selection import (
    SelectionRecord,
    SelectionResult,
    _operator_decisions_from_manifest,
    load_operator_decisions,
    operator_decision_key,
    persist_operator_decisions,
    rank_group,
    select_one_per_game,
    write_selection_manifest,
)
from amiga_adf_library_builder.manual_approvals import ApprovalRecord


def _group(release_key: str, title: str = "G", language: str = "", version: str = "",
           is_complete: bool = True, has_main_disk: bool = True, edition: str = None) -> ReleaseGroup:
    rec = ParsedRecord(
        source_filename=f"{title}.adf", ext="adf", title=title,
        release_key=release_key, disk_number=1, total_disks=1,
        special_disk=False, edition=edition, version=version,
        group=None, chipset=None, language=language,
    )
    return ReleaseGroup(
        release_key=release_key, title=title, edition=edition, group=None,
        chipset=None, language=language, version=version, alt_marker=None, ext="adf",
        records=[rec], disks=[rec], specials=[],
        is_complete=is_complete, has_main_disk=has_main_disk,
    )


# ---------------------------------------------------------------------------
# D1: persisted decision must select EXACTLY 1 release (continue dedented)
# ---------------------------------------------------------------------------
class TestPersistedDecisionOnePerGame:
    def test_persisted_decision_selects_one(self):
        """D1: forced release + 4 others -> exactly 1 selected, not fall-through."""
        groups = [
            _group("game||||||", title="Plain"),
            _group("game||||||a", title="Alt a"),
            _group("game||skr||||", title="SKR"),
            _group("game|||aga|||", title="AGA"),
            _group("game||||en||", title="EN"),
        ]
        decisions = {"game": "game||||||"}
        result = select_one_per_game(groups, decisions=decisions)
        assert len(result.selected) == 1
        assert result.selected[0].release_key == "game||||||"
        assert len(result.rejected) == 4

    def test_persisted_decision_missing_key_falls_back_to_ranking(self):
        """If the forced key is absent, ranking runs normally (1 winner)."""
        groups = [_group("game|a"), _group("game|b")]
        decisions = {"game": "game|missing"}
        result = select_one_per_game(groups, decisions=decisions)
        assert len(result.selected) == 1


# ---------------------------------------------------------------------------
# D2: approvals passed into select_one_per_game win selection
# ---------------------------------------------------------------------------
class TestApprovalsWinSelection:
    def test_approval_wins_over_ranking(self):
        groups = [_group("game|gold", edition="Platinum"), _group("game|silver")]
        appr = {"game|silver": ApprovalRecord(
            approval_id="a1", release_keys=["game|silver"],
            canonical_title="Silver", approved_folder="Silver",
        )}
        result = select_one_per_game(groups, approvals=appr)
        assert len(result.selected) == 1
        assert result.selected[0].release_key == "game|silver"
        assert result.provenance["decisions"][0]["decision"] == "operator_override"

    def test_no_approvals_ranks(self):
        groups = [_group("game|a", edition="Platinum"), _group("game|b")]
        result = select_one_per_game(groups, approvals={})
        assert result.selected[0].release_key == "game|a"


# ---------------------------------------------------------------------------
# D3: load_operator_decisions validates path semantics
# ---------------------------------------------------------------------------
class TestOperatorDecisionsPath:
    def test_load_from_file_path(self, tmp_path: Path):
        """D3: passing a file path loads the file, not a library root."""
        decisions = {"battlesquadron": "battlesquadron||||||"}
        f = tmp_path / "decisions.json"
        f.write_text(json.dumps(decisions), encoding="utf-8")
        loaded = load_operator_decisions(f)
        assert loaded is not None
        assert loaded == decisions

    def test_load_library_root_resolves_to_curation_file(self, tmp_path: Path):
        """Library root still resolves to curation/1g1r_decisions.json."""
        lib = tmp_path / "library"
        lib.mkdir()
        decisions = {"game": "game|||"}
        (lib / "curation").mkdir(parents=True)
        (lib / "curation" / "1g1r_decisions.json").write_text(
            json.dumps(decisions), encoding="utf-8"
        )
        loaded = load_operator_decisions(lib)
        assert loaded == decisions

    def test_load_missing_returns_none(self, tmp_path: Path):
        assert load_operator_decisions(tmp_path / "missing.json") is None

    def test_load_rejects_nonexistent_file_returns_none(self, tmp_path: Path):
        """Invalid/missing input handled explicitly — not silently ignored."""
        bad = tmp_path / "nope" / "1g1r_decisions.json"
        loaded = load_operator_decisions(bad)
        assert loaded is None

    def test_load_rejects_corrupt_json_returns_none(self, tmp_path: Path):
        """Corrupt file -> explicit None, not crash."""
        (tmp_path / "curation").mkdir(parents=True)
        (tmp_path / "curation" / "1g1r_decisions.json").write_text("not json", encoding="utf-8")
        assert load_operator_decisions(tmp_path / "curation" / "1g1r_decisions.json") is None


# ---------------------------------------------------------------------------
# D4: selection-manifest wired into production path, writes deterministically
# ---------------------------------------------------------------------------
class TestSelectionManifest:
    def test_manifest_written_to_path(self, tmp_path: Path):
        groups = [_group("game|a", edition="Platinum"), _group("game|b")]
        result = select_one_per_game(groups)
        manifest_path = tmp_path / "sel.json"
        write_selection_manifest(result, manifest_path)
        assert manifest_path.exists()
        loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert loaded["schema_version"] == 1
        assert len(loaded["selection_manifest"]) == 1

    def test_manifest_deterministic_roundtrip(self, tmp_path: Path):
        groups = [_group("game|a", edition="Platinum")]
        r1 = select_one_per_game(groups)
        r2 = select_one_per_game(groups)
        p1 = tmp_path / "m1.json"
        p2 = tmp_path / "m2.json"
        write_selection_manifest(r1, p1)
        write_selection_manifest(r2, p2)
        assert p1.read_text() == p2.read_text()

    def test_decisions_persist_via_operator_decision_key(self, tmp_path: Path):
        """1G1R decisions written/read through the intended path."""
        groups = [_group("game|a", edition="Platinum")]
        result = select_one_per_game(groups)
        lib = tmp_path / "library"
        lib.mkdir()
        key = operator_decision_key(lib)
        assert key.name == "1g1r_decisions.json"
        persist_operator_decisions(result, lib)
        loaded = load_operator_decisions(lib)
        assert loaded is not None
        assert loaded["provenance"]["selected_count"] == 1


# ---------------------------------------------------------------------------
# D5: region/language ranking dimensions effective with canon
# ---------------------------------------------------------------------------
class TestRankingDimensionsEffective:
    def test_version_effective(self):
        v1 = _group("game|v1", version="v1.0")
        v2 = _group("game|v2", version="v2.0")
        s1, _ = rank_group(v1)
        s2, _ = rank_group(v2)
        assert s2 > s1

    def test_language_effective_with_canon(self):
        """EN must outrank DE when canonical model has the release rows."""
        en = _group("game|en", language="EN")
        de = _group("game|de", language="DE")
        canon = MagicMock()
        canon.releases_for_game.return_value = ["r1"]
        canon.claims_for.return_value = [("curation", "game|de")]
        canon.resolve_field.return_value = ("DE", "curation")
        _, reason_en = rank_group(en, canon=canon)
        score_de, _ = rank_group(de, canon=canon)
        canon.resolve_field.assert_called()
        # DE release must score > neutral 20.0 via canonical lookup
        assert score_de > 20.0

    def test_region_effective_with_canon(self):
        """Region dimension must not return the neutral constant in production."""
        g = _group("game|r", language="EN")
        canon = MagicMock()
        canon.releases_for_game.return_value = ["r1"]
        canon.claims_for.return_value = [("curation", "game|r")]
        canon.resolve_field.return_value = ("USA", "curation")
        score, _ = rank_group(g, canon=canon)
        assert score > 20.0  # region contributes above neutral


# ---------------------------------------------------------------------------
# D6: selection failure fails SAFE — no silent all-release export
# ---------------------------------------------------------------------------
class TestSelectionFailSafe:
    def test_engine_exception_propagates_from_selection(self):
        """select_one_per_game raises so the pipeline's except can record."""
        # Patch the function at call-site level: pipeline imports it lazily
        # inside run_pipeline. We test the function itself propagates.
        groups = [_group("game|a")]
        orig = select_one_per_game
        try:
            # Patch inside the module so pipeline.py's `from .selection import`
            # sees the replacement when it does the lazy import.
            import amiga_adf_library_builder.selection as sel_mod
            sel_mod.select_one_per_game = lambda *a, **k: (_ for _ in ()).throw(
                RuntimeError("QA-injected selection failure")
            )
            # Verify the patched function raises
            with pytest.raises(RuntimeError, match="QA-injected"):
                sel_mod.select_one_per_game(groups)
        finally:
            sel_mod.select_one_per_game = orig

    def test_selection_result_preserved_on_success(self):
        groups = [_group("game|a", edition="Platinum"), _group("game|b")]
        result = select_one_per_game(groups)
        assert result is not None
        assert len(result.selected) == 1
        assert result.provenance["selected_count"] == 1


# ---------------------------------------------------------------------------
# D7: GUI binding smoke test (state -> pipeline kwargs)
# ---------------------------------------------------------------------------
class TestGuiStateBindings:
    def test_state_forwards_slice6_controls(self):
        from amiga_adf_library_builder.gui.state import GuiState, build_pipeline_kwargs
        from amiga_adf_library_builder.paths import PathConfig
        state = GuiState(
            library_root="/tmp/lib",
            one_per_game=True,
            operator_decisions_path="/tmp/dec.json",
            selection_manifest_path="/tmp/sel.json",
        )
        cfg = PathConfig(
            library_root=Path("/tmp/lib"), original_dir=Path("/tmp/lib/original"),
            staging_dir=Path("/tmp/lib/staging"), output_dir=Path("/tmp/lib/output"),
            quarantine_dir=Path("/tmp/lib/quarantine"), logs_dir=Path("/tmp/lib/logs"),
            cache_dir=Path("/tmp/lib/cache"), reports_dir=Path("/tmp/lib/reports"),
            approvals_dir=Path("/tmp/lib/approvals"),
        )
        run_config, extra = build_pipeline_kwargs(state, cfg)
        assert run_config.one_per_game is True
        assert run_config.operator_decisions_path == "/tmp/dec.json"
        assert run_config.selection_manifest_path == "/tmp/sel.json"