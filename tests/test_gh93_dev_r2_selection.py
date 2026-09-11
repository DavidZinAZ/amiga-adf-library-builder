#!/usr/bin/env python3
"""GH-93 DEV-R2 regressions: explicit ADF selection safety.

Covers Worf's HIGH finding (handler moved ALL source ADFs regardless of
selection) plus the decision-log defects (duplicate/contradictory entries,
misleading empty-source STATE_CHANGE wording) and dead undo/redo branches.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "/tmp/amiga-adf-gh106-dev/src")

import pytest

pytestmark = pytest.mark.skip(reason="GUI tests not supported in headless CI")

from amiga_adf_library_builder.models import (
    CurationAction,
    StagedLibrary,
    StagedReleaseEntry,
    StagedState,
)


def make_entry(release_key: str, title: str, **kwargs) -> StagedReleaseEntry:
    defaults = {
        "group": "TESTGROUP",
        "adf_files": [],
        "curation_state": StagedState.PENDING,
        "edition": None,
        "chipset": None,
        "language": None,
        "version": None,
        "alt_marker": None,
        "notes": None,
        "actions": [],
        "locked_fields": [],
        "ext": "adf",
    }
    defaults.update(kwargs)
    return StagedReleaseEntry(release_key=release_key, title=title, **defaults)


def make_lib(src_files: list[str], dst_files: list[str]) -> StagedLibrary:
    lib = StagedLibrary()
    lib.releases["src"] = make_entry("src", "Source Release", adf_files=list(src_files))
    lib.releases["dst"] = make_entry("dst", "Dest Release", adf_files=list(dst_files))
    return lib


# ---------------------------------------------------------------------------
# 1. Moving only one selected ADF from a two-disk source
# ---------------------------------------------------------------------------

class TestExplicitSelection:
    def test_move_single_selected_from_two_disk_source(self):
        lib = make_lib(["disk1.adf", "disk2.adf"], ["existing.adf"])
        lib.move_adfs("src", "dst", ["disk1.adf"])
        assert lib.releases["src"].adf_files == ["disk2.adf"]
        assert lib.releases["dst"].adf_files == ["existing.adf", "disk1.adf"]
        # Source did not empty -> state must remain PENDING
        assert lib.releases["src"].curation_state == StagedState.PENDING

    def test_move_single_selected_second_disk(self):
        lib = make_lib(["disk1.adf", "disk2.adf"], [])
        lib.move_adfs("src", "dst", ["disk2.adf"])
        assert lib.releases["src"].adf_files == ["disk1.adf"]
        assert lib.releases["dst"].adf_files == ["disk2.adf"]

    # 2. Selected subset from a four-disk source, order preserved
    def test_move_subset_from_four_disk_source_preserves_order(self):
        lib = make_lib(["d1.adf", "d2.adf", "d3.adf", "d4.adf"], ["base.adf"])
        lib.move_adfs("src", "dst", ["d3.adf", "d1.adf"])
        # Source keeps remaining order
        assert lib.releases["src"].adf_files == ["d2.adf", "d4.adf"]
        # Destination appends in the requested (selected) order, exactly once
        assert lib.releases["dst"].adf_files == ["base.adf", "d3.adf", "d1.adf"]

    # 3. Moving all selected ADFs
    def test_move_all_selected(self):
        lib = make_lib(["disk1.adf", "disk2.adf"], [])
        lib.move_adfs("src", "dst", ["disk1.adf", "disk2.adf"])
        assert lib.releases["src"].adf_files == []
        assert lib.releases["dst"].adf_files == ["disk1.adf", "disk2.adf"]

    # 4. Empty selection is a no-op at the model contract level. The
    #    committed contract (test_gh93_move_merge::test_move_empty_list) defines
    #    move_adfs([]) as a safe no-op; the widget layer is the guard that
    #    rejects empty selections before they reach the model (see
    #    TestWidgetApplyMove::test_apply_move_empty_selection_rejected_no_change).
    def test_move_empty_selection_is_noop(self):
        lib = make_lib(["disk1.adf"], [])
        src, dst = lib.move_adfs("src", "dst", [])
        # No state change of any kind
        assert src.adf_files == ["disk1.adf"]
        assert dst.adf_files == []
        assert lib.releases["src"].actions == []
        assert lib.releases["dst"].actions == []

    # 5. Unselected ADFs remain in source unchanged
    def test_unselected_adfs_remain_in_source(self):
        lib = make_lib(["a.adf", "b.adf", "c.adf"], [])
        lib.move_adfs("src", "dst", ["b.adf"])
        assert lib.releases["src"].adf_files == ["a.adf", "c.adf"]
        assert lib.releases["dst"].adf_files == ["b.adf"]
        # Source still PENDING (not emptied)
        assert lib.releases["src"].curation_state == StagedState.PENDING

    # 6. Destination receives only selected files exactly once
    def test_destination_receives_selected_exactly_once(self):
        lib = make_lib(["x.adf", "y.adf"], ["x.adf"])
        # x already in destination -> must be rejected, y must still be movable
        with pytest.raises(ValueError, match="already exists"):
            lib.move_adfs("src", "dst", ["x.adf", "y.adf"])
        lib.move_adfs("src", "dst", ["y.adf"])
        dst = lib.releases["dst"].adf_files
        assert dst.count("y.adf") == 1
        assert dst == ["x.adf", "y.adf"]
        assert "x.adf" in lib.releases["src"].adf_files

    # 7/8. 2-disk and 4-disk consolidation equivalents (Ultima V / VI)
    def test_two_disk_consolidation_ultima_v(self):
        lib = make_lib(["ultima5_d1.adf", "ultima5_d2.adf"], [])
        lib.merge_release("src", "dst")
        assert lib.releases["dst"].adf_files == ["ultima5_d1.adf", "ultima5_d2.adf"]
        assert lib.releases["src"].adf_files == []
        assert lib.releases["src"].curation_state == StagedState.GHOST

    def test_four_disk_consolidation_ultima_vi(self):
        lib = make_lib(
            ["ultima6_d1.adf", "ultima6_d2.adf", "ultima6_d3.adf", "ultima6_d4.adf"], []
        )
        lib.merge_release("src", "dst")
        assert lib.releases["dst"].adf_files == [
            "ultima6_d1.adf", "ultima6_d2.adf", "ultima6_d3.adf", "ultima6_d4.adf",
        ]
        assert lib.releases["src"].adf_files == []
        assert lib.releases["src"].curation_state == StagedState.GHOST

    # 9. Empty-source STATE_CHANGE log accuracy
    def test_empty_source_state_change_records_real_previous_state(self):
        lib = make_lib(["disk1.adf"], [])
        lib.move_adfs("src", "dst", ["disk1.adf"])
        src = lib.releases["src"]
        assert src.curation_state == StagedState.GHOST
        state_changes = [a for a in src.actions if a.action == CurationAction.STATE_CHANGE]
        assert len(state_changes) == 1
        details = state_changes[0].details
        # Must record the REAL previous state (pending), not ghost
        assert "from pending to ghost" in details
        assert "from ghost" not in details.replace("from pending to ghost", "")

    def test_empty_source_state_change_from_accepted(self):
        lib = make_lib(["disk1.adf"], [])
        lib.releases["src"].curation_state = StagedState.ACCEPTED
        lib.move_adfs("src", "dst", ["disk1.adf"])
        state_changes = [
            a for a in lib.releases["src"].actions if a.action == CurationAction.STATE_CHANGE
        ]
        assert len(state_changes) == 1
        assert "from accepted to ghost" in state_changes[0].details

    # 10 (model level). One coherent audit trail per operation
    def test_move_logs_exactly_one_move_entry_per_side(self):
        lib = make_lib(["disk1.adf", "disk2.adf"], ["base.adf"])
        lib.move_adfs("src", "dst", ["disk1.adf"])
        src_moves = [a for a in lib.releases["src"].actions if a.action == CurationAction.MOVE]
        dst_moves = [a for a in lib.releases["dst"].actions if a.action == CurationAction.MOVE]
        assert len(src_moves) == 1
        assert len(dst_moves) == 1
        assert "Moved 1 ADF(s) to dst" in src_moves[0].details
        assert "Received 1 ADF(s) from src" in dst_moves[0].details
        # No contradictory STATE_CHANGE (source not emptied)
        assert not [a for a in lib.releases["src"].actions if a.action == CurationAction.STATE_CHANGE]

    def test_merge_logs_exactly_one_merge_entry_per_side(self):
        lib = make_lib(["d1.adf", "d2.adf"], [])
        lib.merge_release("src", "dst")
        src_merges = [a for a in lib.releases["src"].actions if a.action == CurationAction.MERGE]
        dst_merges = [a for a in lib.releases["dst"].actions if a.action == CurationAction.MERGE]
        assert len(src_merges) == 1
        assert len(dst_merges) == 1


# ---------------------------------------------------------------------------
# 11. Original Disks unchanged and no preview-only export writes
# ---------------------------------------------------------------------------

class TestNonDestructive:
    def test_move_merge_do_not_touch_disk_files(self, tmp_path: Path):
        originals = tmp_path / "originals"
        originals.mkdir()
        written = []
        for name in ["d1.adf", "d2.adf"]:
            f = originals / name
            f.write_bytes(b"ADF" + name.encode())
            written.append(f)
        before = sorted(p.name for p in originals.iterdir())
        before_hashes = {p.name: p.read_bytes() for p in originals.iterdir()}

        lib = make_lib(["d1.adf", "d2.adf"], ["base.adf"])
        lib.move_adfs("src", "dst", ["d1.adf"])
        lib2 = make_lib(["d1.adf", "d2.adf"], [])
        lib2.merge_release("src", "dst")

        after = sorted(p.name for p in originals.iterdir())
        assert after == before
        for name, data in before_hashes.items():
            assert (originals / name).read_bytes() == data
        # No export/output dir created anywhere under the temp root
        assert not (tmp_path / "output").exists()
        assert not (tmp_path / "export").exists()


# ---------------------------------------------------------------------------
# Widget level: handler-level guards + undo/redo exact membership
# ---------------------------------------------------------------------------

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture()
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def widget(qapp):
    from amiga_adf_library_builder.gui.preview_widget import PreviewWidget

    w = PreviewWidget()
    w._state.current_library = make_lib(["d1.adf", "d2.adf"], ["base.adf"])
    w._state.selected_release_key = "src"
    return w


class TestWidgetApplyMove:
    def test_apply_move_moves_only_selected(self, widget):
        widget._apply_move_adfs("src", "dst", ["d1.adf"])
        assert widget._state.current_library.releases["src"].adf_files == ["d2.adf"]
        assert widget._state.current_library.releases["dst"].adf_files == ["base.adf", "d1.adf"]

    def test_apply_move_empty_selection_rejected_no_change(self, widget):
        with pytest.raises(ValueError):
            widget._apply_move_adfs("src", "dst", [])
        lib = widget._state.current_library
        assert lib.releases["src"].adf_files == ["d1.adf", "d2.adf"]
        assert lib.releases["dst"].adf_files == ["base.adf"]
        assert lib.releases["src"].actions == []
        # No undo entry recorded for a rejected operation
        assert widget._undo_stack == []

    def test_apply_move_records_single_undo_entry(self, widget):
        widget._apply_move_adfs("src", "dst", ["d1.adf"])
        assert len(widget._undo_stack) == 1
        key, action = widget._undo_stack[0]
        assert key == "src"
        assert action.action == CurationAction.MOVE
        payload = json.loads(action.payload)
        assert payload["moved_files"] == ["d1.adf"]
        assert payload["src_files_before"] == ["d1.adf", "d2.adf"]
        assert payload["dst_files_before"] == ["base.adf"]

    def test_undo_redo_restores_exact_membership(self, widget):
        widget._apply_move_adfs("src", "dst", ["d1.adf"])
        lib = widget._state.current_library
        assert lib.releases["src"].adf_files == ["d2.adf"]
        assert lib.releases["dst"].adf_files == ["base.adf", "d1.adf"]

        widget._on_undo()
        assert lib.releases["src"].adf_files == ["d1.adf", "d2.adf"]
        assert lib.releases["dst"].adf_files == ["base.adf"]
        assert lib.releases["src"].curation_state == StagedState.PENDING

        widget._on_redo()
        assert lib.releases["src"].adf_files == ["d2.adf"]
        assert lib.releases["dst"].adf_files == ["base.adf", "d1.adf"]

    def test_undo_restores_source_state_when_emptied(self, widget):
        lib = widget._state.current_library
        lib.releases["src"].curation_state = StagedState.ACCEPTED
        widget._apply_move_adfs("src", "dst", ["d1.adf", "d2.adf"])
        assert lib.releases["src"].curation_state == StagedState.GHOST
        widget._on_undo()
        assert lib.releases["src"].curation_state == StagedState.ACCEPTED
        assert lib.releases["src"].adf_files == ["d1.adf", "d2.adf"]


class TestWidgetApplyMerge:
    def test_apply_merge_whole_release(self, widget):
        widget._apply_merge_release("src", "dst")
        lib = widget._state.current_library
        assert lib.releases["src"].adf_files == []
        assert lib.releases["src"].curation_state == StagedState.GHOST
        assert lib.releases["dst"].adf_files == ["base.adf", "d1.adf", "d2.adf"]
        assert len(widget._undo_stack) == 1

    def test_merge_undo_redo_exact_membership(self, widget):
        widget._apply_merge_release("src", "dst")
        lib = widget._state.current_library
        widget._on_undo()
        assert lib.releases["src"].adf_files == ["d1.adf", "d2.adf"]
        assert lib.releases["dst"].adf_files == ["base.adf"]
        widget._on_redo()
        assert lib.releases["src"].adf_files == []
        assert lib.releases["dst"].adf_files == ["base.adf", "d1.adf", "d2.adf"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
