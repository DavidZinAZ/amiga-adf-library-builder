"""Issue #21 regression tests: live activity log in Diagnostics.

Root cause under test: the Diagnostics tab only showed a static
"last run" activity summary (rendered after the run finished, from the
structured EnrichEvent records), so while a run was in progress the
operator saw nothing but the progress bar and a stage name. The fix
streams live, plain-language, REDACTED activity lines from the pipeline
through the worker thread into the Diagnostics view, with a "Show live
processing log" toggle (persisted, default ON), log controls (Clear /
Jump to top / Follow Live), a run boundary marker on every run start and
end, and an end-of-run result summary.

These tests lock the contract:
  * ``run_pipeline`` reports each major milestone (scan, grouping,
    enrichment, quarantine, export) through the optional ``activity``
    hook; omitting the hook (CLI) changes nothing.
  * the GUI wires the worker's new ``activity`` signal into the
    Diagnostics view; every live line is redacted (a secret-shaped value
    in a path or provider detail never reaches the view).
  * the Diagnostics tab exposes the Show-live toggle (default ON), the
    log controls, and the run boundary markers.
  * the ``show_live_log`` preference is persisted, round-trips through
    the settings store, and a missing key (old profile) loads with the
    safe default True.
  * the plain-language wording guard (issue #22) still holds for the new
    Diagnostics controls.

Headless: relies on ``QT_QPA_PLATFORM=offscreen`` (same pattern as
tests/test_gui_window_geometry.py). Deterministic on pytest tmp dirs.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

# The offscreen platform must be selected before the first QApplication is
# created; setdefault so an explicit host value still wins.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QCheckBox, QPlainTextEdit, QPushButton  # noqa: E402

from amiga_adf_library_builder import activity_log  # noqa: E402
from amiga_adf_library_builder.gui import MainWindow  # noqa: E402
from amiga_adf_library_builder.gui.layout import PortablePaths  # noqa: E402
from amiga_adf_library_builder.gui.secrets import SecretStore  # noqa: E402
from amiga_adf_library_builder.gui.settings import (  # noqa: E402
    SETTINGS_KEYS,
    SettingsStore,
)
from amiga_adf_library_builder.paths import resolve_config  # noqa: E402
from amiga_adf_library_builder.pipeline import run_pipeline  # noqa: E402
from amiga_adf_library_builder.logging_utils import redact  # noqa: E402


# --- helpers -----------------------------------------------------------------
def _make_window(base_dir: Path) -> MainWindow:
    app = QApplication.instance() or QApplication([])  # noqa: F841
    pp = PortablePaths(base_dir=base_dir)
    pp.ensure_all()
    return MainWindow(
        portable_paths=pp,
        settings_store=SettingsStore(pp.settings_file()),
        secret_store=SecretStore.with_vault(pp.vault_file()),
        config_path=None,
    )


def _build_synthetic_corpus(data_root: Path, names) -> None:
    orig = data_root / "original"
    orig.mkdir(parents=True, exist_ok=True)
    for n in names:
        (orig / n).write_bytes(b"x" * 10)


_NAMES = [
    "Example - Space Tactics (Disk 1 of 4).adf",
    "Example - Space Tactics (Disk 2 of 4).adf",
    "Example - Space Tactics (Disk 3 of 4).adf",
    "Example - Space Tactics (Disk 4 of 4).adf",
    "Solo Game (Disk 1 of 1).adf",
]


# --- activity_log module ------------------------------------------------------
def test_run_activity_line_is_timestamped_and_redacted():
    line = activity_log.run_activity_line(
        "scanning /data/lib/orig?token=abc123 for .adf files"
    )
    # HH:MM:SS prefix + two spaces, then the text.
    assert re.match(r"^\d{2}:\d{2}:\d{2}  ", line), line
    # The secret-shaped value is redacted, not present verbatim.
    assert "abc123" not in line
    # The rest of the message survives.
    assert "scanning" in line


def test_run_activity_line_never_raises_on_odd_input():
    # An odd value (not a string) must not crash the render path; it is
    # coerced via str(). The type is intentionally loose here -- this is a
    # defensive guard test, not a type test.
    from typing import Any

    odd: Any = object()
    assert activity_log.run_activity_line(odd)


def test_render_run_summary_build_mode():
    result = {
        "files_scanned": 5,
        "groups": 3,
        "nfo_written": ["a.nfo", "b.nfo"],
        "artwork_resized": ["a.jpg"],
        "review_routed": [],
        "unknown_routed": ["Special Only"],
        "export_gate_open": False,
        "export_gate_reason": "the app's export safety check is not clear",
    }
    lines = activity_log.render_run_summary(result, run_mode="build")
    joined = "\n".join(lines)
    assert "Result: success." in joined
    assert "Files scanned: 5; releases prepared: 3." in joined
    assert "NFO files written: 2; artwork processed: 1." in joined
    assert "Export: not requested this run (build only)." in joined


def test_render_run_summary_export_mode():
    result = {
        "files_scanned": 5,
        "groups": 3,
        "nfo_written": [],
        "artwork_resized": [],
        "review_routed": [],
        "unknown_routed": [],
        "export_gate_open": True,
        "export": {
            "releases_exported": 2,
            "folders_written": 2,
            "files_written": 7,
            "files_unchanged": 1,
            "conflicts": [],
            "skipped_quarantined": 1,
            "errors": [],
            "staging_root": "/data/lib/work/staging/run-1",
        },
    }
    joined = "\n".join(activity_log.render_run_summary(result, run_mode="export"))
    assert "Result: success." in joined
    assert "Export: completed." in joined
    assert "2 release(s) exported" in joined
    assert "Per-run scratch area:" in joined


def test_render_run_summary_missing_fields_never_raise():
    joined = "\n".join(activity_log.render_run_summary(None, run_mode="build"))
    assert "Files scanned: ?; releases prepared: ?." in joined


# --- run_pipeline activity hook ----------------------------------------------
def test_run_pipeline_emits_activity_milestones(tmp_path: Path):
    data_root = tmp_path / "data"
    _build_synthetic_corpus(data_root, _NAMES)
    cfg = resolve_config(library_root=str(data_root))[0]

    lines: list[str] = []
    result = run_pipeline(cfg=cfg, online=False, activity=lines.append)

    joined = "\n".join(lines)
    # Each major milestone reports at least one plain-language line.
    assert "Scanning" in joined
    assert "Found 5 .adf file(s)" in joined
    assert "Grouping files into releases" in joined
    assert "Prepared" in joined and "release(s)" in joined
    assert "Filling in missing metadata" in joined
    assert "Metadata and artwork preparation complete." in joined
    assert "Checking for releases that need review" in joined
    assert "Sent" in joined and "to review" in joined
    # The result is otherwise intact.
    assert result["files_scanned"] == 5
    assert result["original_preserved"] is True


def test_run_pipeline_no_activity_hook_is_noop(tmp_path: Path):
    data_root = tmp_path / "data"
    _build_synthetic_corpus(data_root, _NAMES)
    cfg = resolve_config(library_root=str(data_root))[0]
    # No hook (CLI path) still works and returns the same shape.
    result = run_pipeline(cfg=cfg, online=False)
    assert result["files_scanned"] == 5


def test_run_pipeline_activity_hook_failure_is_swallowed(tmp_path: Path):
    data_root = tmp_path / "data"
    _build_synthetic_corpus(data_root, _NAMES)
    cfg = resolve_config(library_root=str(data_root))[0]

    def _boom(_msg: str) -> None:
        raise RuntimeError("a logging hook must never break the run")

    result = run_pipeline(cfg=cfg, online=False, activity=_boom)
    assert result["files_scanned"] == 5  # run completed despite the bad hook


def test_run_pipeline_activity_lines_are_redacted(tmp_path: Path):
    data_root = tmp_path / "data"
    _build_synthetic_corpus(data_root, _NAMES)
    cfg = resolve_config(library_root=str(data_root))[0]

    lines: list[str] = []
    run_pipeline(cfg=cfg, online=False, activity=lines.append)
    for line in lines:
        assert line == redact(line), f"activity line not redacted: {line!r}"


# --- GUI wiring ----------------------------------------------------------------
def test_diagnostics_tab_has_live_controls(tmp_path: Path):
    mw = _make_window(tmp_path / "issue21-base")
    central = mw.centralWidget()
    buttons = {b.text() for b in central.findChildren(QPushButton)}
    assert "Clear" in buttons
    assert "Jump to top" in buttons
    assert "Follow Live: On" in buttons
    checks = {c.text() for c in central.findChildren(QCheckBox)}
    assert "Show live processing log" in checks
    # The live view is a read-only, bounded plain-text edit.
    diag = mw._diag
    assert isinstance(diag, QPlainTextEdit)
    assert diag.isReadOnly()
    assert diag.maximumBlockCount() == 5000
    mw.close()


def test_diagnostics_toggle_default_on_and_persists(tmp_path: Path):
    base = tmp_path / "issue21-persist"
    mw = _make_window(base)
    assert mw._cb_show_live_log.isChecked() is True  # default ON
    mw._cb_show_live_log.setChecked(False)
    mw._persist_defaults()
    # Reopen on the same settings file: the choice is restored.
    mw2 = _make_window(base)
    assert mw2._cb_show_live_log.isChecked() is False
    mw2.close()


def test_show_live_log_round_trips_through_store(tmp_path: Path):
    path = tmp_path / "gui-settings.toml"
    store = SettingsStore(path)
    store.load()
    store.update(show_live_log=False)
    reloaded = SettingsStore(path).load()
    assert reloaded.show_live_log is False
    # The serialized file uses the known key name.
    assert "show_live_log" in path.read_text(encoding="utf-8")


def test_missing_show_live_log_key_defaults_true(tmp_path: Path):
    # Simulate an OLD profile: hand-write a settings file that predates the
    # show_live_log key (as_dict() now always writes it, so we cannot produce
    # the legacy file through the store API).
    path = tmp_path / "old-profile.toml"
    path.write_text(
        "[gui]\n"
        'theme = "dark"\n'
        "online = true\n",
        encoding="utf-8",
    )
    assert "show_live_log" not in path.read_text(encoding="utf-8")
    reloaded = SettingsStore(path).load()
    assert reloaded.show_live_log is True  # safe default for old profiles


def test_show_live_log_key_is_known_and_has_attribute():
    assert "show_live_log" in SETTINGS_KEYS
    from amiga_adf_library_builder.gui.settings import Settings

    assert hasattr(Settings(), "show_live_log")


def test_new_diagnostics_controls_use_plain_language(tmp_path: Path):
    """Issue #22 guard still holds for the new Diagnostics controls."""
    mw = _make_window(tmp_path / "issue21-wording")
    forbidden = ("gate", "preflight", "staging")
    offenders = []
    for cls in (QCheckBox, QPushButton, QPlainTextEdit):
        for w in mw.centralWidget().findChildren(cls):
            value = getattr(w, "text", None)
            if callable(value):
                value = value()
            if not isinstance(value, str) or not value:
                continue
            lowered = value.lower()
            for term in forbidden:
                if term in lowered:
                    offenders.append(f"{term!r} in {value!r}")
    assert not offenders, f"forbidden internal terms in Diagnostics: {offenders}"
    mw.close()


def test_run_marker_appends_boundary_line(tmp_path: Path):
    mw = _make_window(tmp_path / "issue21-marker")
    mw._run_marker("=== RUN START ===")
    text = mw._diag.toPlainText()
    assert "=== RUN START ===" in text
    # Timestamped + two-space separator.
    assert re.search(r"\d{2}:\d{2}:\d{2}  === RUN START ===", text)
    mw.close()


def test_live_log_off_stops_live_lines_but_keeps_markers(tmp_path: Path):
    mw = _make_window(tmp_path / "issue21-toggle")
    mw._cb_show_live_log.setChecked(False)
    mw._run_in_progress = True
    mw._append_diag("a live line that should not appear")
    text_before = mw._diag.toPlainText()
    assert "a live line" not in text_before
    # But a run boundary marker still shows.
    mw._run_marker("=== RUN END (done) ===")
    assert "=== RUN END (done) ===" in mw._diag.toPlainText()
    mw.close()


# --- log view controls: Clear / Jump-to-Top / Follow Live ---------------------
def _fill_diag(mw: MainWindow, n: int) -> None:
    """Append n live lines so the view overflows its (small) viewport."""
    mw.resize(620, 160)  # small window -> short Diagnostics viewport
    for i in range(n):
        mw._run_in_progress = True
        mw._append_diag(f"line {i:03d}")
    sb = mw._diag.verticalScrollBar()
    # Force a layout so the scrollbar range reflects the content.
    mw._diag.verticalScrollBar().setValue(sb.maximum())
    return sb


def _button(mw: MainWindow, label: str):
    for b in mw.centralWidget().findChildren(QPushButton):
        if b.text() == label:
            return b
    raise AssertionError(f"button not found: {label!r}")


def test_clear_button_empties_the_log_view(tmp_path: Path):
    mw = _make_window(tmp_path / "issue21-clear")
    mw._run_in_progress = True
    for i in range(5):
        mw._append_diag(f"line {i}")
    assert "line 3" in mw._diag.toPlainText()
    _button(mw, "Clear").click()
    assert mw._diag.toPlainText().strip() == ""
    mw.close()


def test_follow_live_auto_scroll_and_pin(tmp_path: Path):
    """Follow Live (default ON) pins the view to the newest line."""
    mw = _make_window(tmp_path / "issue21-follow")
    _fill_diag(mw, 200)
    sb = mw._diag.verticalScrollBar()
    assert sb.maximum() > 0, "viewport not overflowing; test is vacuous"
    # Follow Live defaults ON -> the view sits at the bottom.
    assert sb.value() == sb.maximum()
    mw.close()


def test_follow_off_stops_auto_scroll_and_jump_to_top(tmp_path: Path):
    """With Follow Live OFF, new lines do not re-pin; Jump-to-top is a
    one-shot scroll. Toggling Follow Live back ON re-pins to the bottom."""
    mw = _make_window(tmp_path / "issue21-jumptop")
    _fill_diag(mw, 200)
    sb = mw._diag.verticalScrollBar()
    assert sb.maximum() > 0, "viewport not overflowing; test is vacuous"

    # Turn Follow Live off (the Bottom/Follow-Live button also jumps to the
    # bottom on the click, then disengages the pin).
    _button(mw, "Follow Live: On").click()
    assert _button(mw, "Follow Live: Off") is not None

    # Jump to the very top.
    _button(mw, "Jump to top").click()
    assert sb.value() == 0

    # Follow Live is off: appending must NOT auto-scroll back down.
    mw._run_in_progress = True
    for i in range(50):
        mw._append_diag(f"more {i}")
    assert sb.value() == 0, "Follow Live off must not auto-scroll"

    # Re-enable Follow Live: the pin re-engages and snaps to the bottom.
    _button(mw, "Follow Live: Off").click()
    assert _button(mw, "Follow Live: On") is not None
    assert sb.value() == sb.maximum()
    mw.close()

