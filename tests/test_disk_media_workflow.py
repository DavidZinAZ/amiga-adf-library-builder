from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QTabWidget
from PIL import Image

from amiga_adf_library_builder.models import StagedReleaseEntry, StagedLibrary
from amiga_adf_library_builder.gui.preview_widget import PreviewWidget
from amiga_adf_library_builder.gui.media_choice_dialog import MediaChoiceDialog
from amiga_adf_library_builder.library_state import CurationStateManager
from amiga_adf_library_builder.parser import parse_filename
from amiga_adf_library_builder.grouper import group_records
from amiga_adf_library_builder.exporter import export_release


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def entry(key, disks):
    return StagedReleaseEntry(release_key=key, title=key, edition=None,
                             group=None, chipset=None, adf_files=disks)


def widget_for(tmp_path, *entries):
    widget = PreviewWidget()
    widget._state.current_library = StagedLibrary(releases={e.release_key: e for e in entries})
    widget._state.current_state_path = tmp_path / "state.json"
    widget._state.state_manager = CurationStateManager(widget._state.current_state_path)
    return widget


def test_disk_choices_save_reload_undo_and_carry_over(app, tmp_path):
    release = entry("Game", ["disk1.adf", "disk2.adf"])
    widget = widget_for(tmp_path, release)
    candidate = {"path": "cover.png", "purpose": "Box - Front"}
    widget._save_media_choices(release, {}, scope=("artwork", "per_disk"))
    widget._save_media_choices(release, {"Box - Front": [candidate]}, disk="disk2.adf")
    restored = widget._state.state_manager.load()
    assert restored.releases["Game"].disk_media_choices["disk2.adf"]["Box - Front"] == [candidate]
    widget._on_undo()
    assert "Box - Front" not in release.disk_media_choices.get("disk2.adf", {})
    widget._on_redo()
    assert release.disk_media_choices["disk2.adf"]["Box - Front"] == [candidate]
    fresh = StagedLibrary(releases={"Game": entry("Game", release.adf_files)})
    fresh.carry_over(restored)
    assert fresh.releases["Game"].disk_media_choices == release.disk_media_choices
    widget.close()


def test_media_assignment_changes_association_and_undo(app, tmp_path):
    source = entry("Source", ["source.adf"])
    destination = entry("Destination", ["dest.adf"])
    path = tmp_path / "manual.txt"
    path.write_text("Press FIRE.")
    candidate = {"path": str(path), "purpose": "manual"}
    source.media_candidates = [candidate]
    source.media_choices = {"manual": [candidate]}
    widget = widget_for(tmp_path, source, destination)
    widget._apply_media_assignment(source, destination, candidate)
    assert source.media_choices["manual"] == []
    assert destination.media_choices["manual"] == [candidate]
    assert path.read_text() == "Press FIRE."
    widget._on_undo()
    assert source.media_candidates == [candidate]
    assert destination.media_choices == {}
    widget._on_redo()
    assert destination.media_choices["manual"] == [candidate]
    widget.close()


def test_moving_disk_carries_custom_media_and_undo(app, tmp_path):
    source = entry("Source", ["one.adf", "two.adf"])
    destination = entry("Destination", ["three.adf"])
    source.media_scopes = {"artwork": "per_disk"}
    source.disk_media_choices = {"two.adf": {"Box - Front": [{"path": "custom.png"}]}}
    widget = widget_for(tmp_path, source, destination)
    widget._apply_move_adfs("Source", "Destination", ["two.adf"])
    assert "two.adf" in destination.disk_media_choices
    assert destination.media_scopes["artwork"] == "per_disk"
    widget._on_undo()
    assert "two.adf" in source.disk_media_choices
    assert destination.disk_media_choices == {}
    widget._on_redo()
    assert "two.adf" in destination.disk_media_choices
    widget.close()


def test_review_disk_selector_saves_to_selected_disk(app, tmp_path):
    release = entry("Game", ["one.adf", "two.adf"])
    path = tmp_path / "manual.txt"
    path.write_text("Controls")
    release.media_candidates = [{"path": str(path), "purpose": "manual"}]
    widget = widget_for(tmp_path, release)
    dialog = MediaChoiceDialog(release, tmp_path, initial_purpose="manual")
    dialog.media_scope_changed.connect(lambda key, scope: widget._save_media_choices(release, {}, scope=(key, scope)))
    dialog.disk_choices_saved.connect(lambda disk, choices: widget._save_media_choices(release, choices, disk=disk))
    dialog.scope.setCurrentIndex(1)
    dialog._use()
    assert "manual" in release.disk_media_choices["one.adf"]
    assert release.media_choices == {}
    dialog._next()
    assert dialog.disk.currentText() == "two.adf"
    dialog._previous()
    assert dialog.disk.currentText() == "one.adf"
    dialog.close()
    widget.close()


def test_export_disk_companions_shared_fallback_and_scope_rerun(tmp_path):
    names = [f"Example (Disk {n} of 2).adf" for n in (1, 2)]
    group = group_records([parse_filename(n) for n in names])[0]
    originals = tmp_path / "originals"
    originals.mkdir()
    for n in names:
        (originals / n).write_bytes(b"disk original")
    shared = tmp_path / "shared.png"
    custom = tmp_path / "custom.png"
    Image.new("RGB", (30, 30), "red").save(shared)
    Image.new("RGB", (30, 30), "blue").save(custom)
    manual = tmp_path / "manual.txt"
    manual.write_text("Disk two instructions: press FIRE.")
    release = entry(group.release_key, names)
    release.media_choices = {"Box - Front": [{"path": str(shared)}]}
    release.media_scopes = {"artwork": "per_disk", "manual": "per_disk"}
    release.disk_media_choices = {names[1]: {"Box - Front": [{"path": str(custom)}], "manual": [{"path": str(manual)}]}}
    library = StagedLibrary(releases={group.release_key: release})
    staging = tmp_path / "staging"
    _, _, conflicts = export_release(group, staging, original_dir=originals, staged_library=library, basename="Example")
    assert not conflicts
    folder = staging / "ADF" / "Example"
    assert (folder / "Example.jpg").exists()
    assert (folder / "Example-2.jpg").read_bytes() != (folder / "Example.jpg").read_bytes()
    assert not (folder / "Example-1.jpg").exists()  # Inherits shared artwork.
    assert "Disk two instructions" in (folder / "Example-2.rtfm").read_text()
    release.media_scopes = {}
    export_release(group, staging, original_dir=originals, staged_library=library, basename="Example")
    assert not (folder / "Example-2.jpg").exists()
    assert not (folder / "Example-2.rtfm").exists()
    assert all((originals / n).read_bytes() == b"disk original" for n in names)


def test_disk_override_preview_survives_reopening(app, tmp_path):
    release = entry("Game", ["one.adf"])
    manual = tmp_path / "custom.txt"
    manual.write_text("Unique disk instructions")
    release.media_scopes = {"manual": "per_disk"}
    release.disk_media_choices = {"one.adf": {"manual": [{"path": str(manual), "purpose": "manual"}]}}
    dialog = MediaChoiceDialog(release, tmp_path, initial_purpose="manual")
    assert "Unique disk instructions" in dialog.text.toPlainText()
    dialog.close()


def test_complete_disk_choices_clear_review_and_satisfy_artwork_requirement(tmp_path):
    from amiga_adf_library_builder.exporter import export_all
    from amiga_adf_library_builder.media_review import pending_purposes
    names = [f"Example (Disk {n} of 2).adf" for n in (1, 2)]
    group = group_records([parse_filename(n) for n in names])[0]
    originals = tmp_path / "originals"
    originals.mkdir()
    image = tmp_path / "cover.png"
    Image.new("RGB", (20, 20), "red").save(image)
    release = entry(group.release_key, names)
    candidates = [{"path": str(image), "purpose": "Box - Front"}, {"path": "other.png", "purpose": "Box - Front"}]
    release.media_candidates = candidates
    release.media_scopes = {"artwork": "per_disk"}
    release.disk_media_choices = {name: {"Box - Front": [candidates[0]]} for name in names}
    for name in names:
        (originals / name).write_bytes(b"original")
    assert pending_purposes(release) == set()
    result = export_all([group], staging_dir=tmp_path / "staging", run_id="test",
                        original_dir=originals, staged_library=StagedLibrary(releases={group.release_key: release}),
                        require_artwork=True, upstream_task_closed=True,
                        verified_artwork_width=2000, verified_artwork_height=2000)
    assert result.errors == []
    assert result.conflicts == []
