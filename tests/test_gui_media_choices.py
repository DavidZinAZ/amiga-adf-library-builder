import json
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QDialog
from PySide6.QtCore import Qt

from amiga_adf_library_builder.gui.media_choice_dialog import MediaChoiceDialog
from amiga_adf_library_builder.gui.preview_widget import PreviewWidget
from amiga_adf_library_builder.models import StagedLibrary, StagedReleaseEntry
from amiga_adf_library_builder.library_state import CurationStateManager


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def entry(tmp_path):
    candidates = []
    for number in range(2):
        path = tmp_path / f"manual-{number}.txt"
        path.write_text(f"Manual {number}: Press FIRE to start.")
        candidates.append({"path": str(path), "purpose": "manual", "score": 1.0, "category": "manuals"})
    return StagedReleaseEntry(release_key="space quest", title="Space Quest",
        edition=None, group=None, chipset=None, media_candidates=candidates)


def test_preview_selection_extras_and_none(app, tmp_path):
    release = entry(tmp_path)
    dialog = MediaChoiceDialog(release, tmp_path / "preview")
    assert "Manual 0" in dialog.text.toPlainText()
    dialog.list.item(1).setCheckState(Qt.CheckState.Checked)
    dialog._use()
    assert len(dialog.decisions["manual"]) == 2
    dialog._none()
    assert dialog.decisions["manual"] == []
    dialog._automatic()
    assert dialog.decisions["manual"] is None
    dialog.close()


def test_choices_save_reload_and_carry_over(app, tmp_path, monkeypatch):
    release = entry(tmp_path)
    widget = PreviewWidget()
    widget._state.current_library = StagedLibrary(releases={release.release_key: release})
    path = tmp_path / "library_state_test.json"
    widget._state.current_state_path = path
    widget._state.state_manager = CurationStateManager(path)
    assert len(widget.media_review_pending()) == 1
    monkeypatch.setattr(MediaChoiceDialog, "exec", lambda self: (
        self.decisions.update({"manual": [release.media_candidates[1]]}) or QDialog.DialogCode.Accepted))
    widget._review_media_entry(release)
    restored = CurationStateManager(path).load()
    assert restored.releases[release.release_key].media_choices["manual"][0]["path"].endswith("manual-1.txt")
    assert widget.media_review_pending() == []
    widget._on_undo()
    assert "manual" not in CurationStateManager(path).load().releases[release.release_key].media_choices
    widget._on_redo()
    assert CurationStateManager(path).load().releases[release.release_key].media_choices == release.media_choices
    fresh = StagedLibrary(releases={release.release_key: entry(tmp_path)})
    fresh.carry_over(restored)
    assert fresh.releases[release.release_key].media_choices == release.media_choices
    widget.close()
