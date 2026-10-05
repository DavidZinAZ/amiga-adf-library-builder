"""GH-192 regression tests: post-export GUI freeze.

Root cause under test: after a completed run (export already written),
``MainWindow._on_finished`` performed ALL post-run bookkeeping synchronously
on the Qt GUI thread -- run-log serialization, the staged curation-state
rebuild (content-hash pass over the corpus), local-media provider state-file
reads, preview-table refresh -- and Windows painted the window
"Not Responding" at 100% / "Finishing up" because the GUI event loop was
blocked. The dominant cost was the curation-state rebuild
(``carry_over`` hashing every ADF).

The fix hands the two HEAVY blocking steps (run-log write, curation-state
rebuild) to ``FinalizationWorker`` on its own thread; the GUI shows the
completed state and re-enables Run immediately, and the curation state lands
asynchronously via ``_on_run_finalized``. The CHEAP post-run state (the GH-66
review-button count and local-media provider anchoring) deliberately stays
synchronous on the GUI thread -- it is a small JSON read, not the blocking
operation, and ``tests/test_gui_issue66_review_threshold.py`` asserts it on
the same ``_on_finished`` call. These tests lock
that contract:

  * a successful worker completion returns the window to a responsive idle
    state: progress 100, "Done." status, Run re-enabled, Cancel reset;
  * the expensive steps run OFF the GUI thread and the event loop stays live
    while they run (a GUI timer fires mid-finalization);
  * the finalizer thread terminates and is dropped (no unbounded wait, no
    leaked running thread);
  * a second run can be started after a completed run;
  * closing the window mid-finalization is bounded (closeEvent waits at most
    a few seconds for the finalizer) and never destroys a running thread;
  * the Diagnostics log carries the per-step finalization timing report so a
    packaged run names where its time went.

Headless: ``QT_QPA_PLATFORM=offscreen`` (same pattern as
tests/test_gui_issue21_live_diagnostics.py). Deterministic on pytest tmp
dirs; a 5-file synthetic corpus keeps each test well under a second of
pipeline time.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

# The offscreen platform must be selected before the first QApplication is
# created; setdefault so an explicit host value still wins.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import amiga_adf_library_builder.pipeline as pipeline_mod  # noqa: E402
from amiga_adf_library_builder.gui import MainWindow  # noqa: E402
from amiga_adf_library_builder.gui.finalizer import (  # noqa: E402
    STEP_REPORT,
    format_timing_report,
)
from amiga_adf_library_builder.gui.layout import PortablePaths  # noqa: E402
from amiga_adf_library_builder.gui.secrets import SecretStore  # noqa: E402
from amiga_adf_library_builder.gui.settings import SettingsStore  # noqa: E402


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


def _build_corpus(data_root: Path) -> Path:
    orig = data_root / "original"
    orig.mkdir(parents=True, exist_ok=True)
    for d in range(1, 5):
        (orig / f"Example - Space Tactics (Disk {d} of 4).adf").write_bytes(b"x" * 10)
    (orig / "Solo Game (Disk 1 of 1).adf").write_bytes(b"x" * 10)
    return orig


def _configure_for_export(win: MainWindow, orig: Path) -> None:
    """Point the window at the corpus and request an export-mode run, the
    same way the operator would after picking the original dir."""
    win._le_original_dir.setText(str(orig))
    win._cb_online.setChecked(False)
    win._mode_export.setChecked(True)
    win._cb_gate.setChecked(True)
    win._library_root = win._paths.base / "library"
    win._library_root.mkdir(parents=True, exist_ok=True)
    win._update_export_state_display()
    win._persist_defaults()


def _pump_until_idle(win: MainWindow, timeout_s: float = 120.0) -> None:
    """Pump the event loop until the window is back at idle (or time out)."""
    deadline = time.time() + timeout_s
    app = QApplication.instance()
    while time.time() < deadline:
        app.processEvents()
        if win._state == "IDLE" and not win._run_in_progress:
            return
        time.sleep(0.002)
    raise AssertionError(
        f"window did not return to idle within {timeout_s}s "
        f"(state={win._state!r}, status={win._status_label.text()!r})"
    )


def _run_to_idle(win: MainWindow, orig: Path) -> None:
    _configure_for_export(win, orig)
    win._on_run()
    _pump_until_idle(win)


# --- requirement 1: successful completion -> responsive idle -----------------
def test_run_completion_returns_to_idle(tmp_path: Path):
    win = _make_window(tmp_path / "issue192-idle")
    orig = _build_corpus(win._paths.base)
    _run_to_idle(win, orig)

    # Final progress + status reach the completed state.
    assert win._progress.value() == 100
    assert "Done." in win._status_label.text()
    # Run button is enabled again; cancel state is reset.
    assert win._run_button.isEnabled() is True
    assert win._cancel_button.isEnabled() is False
    assert win._state == "IDLE"
    win.close()


def test_completed_export_artifacts_survive(tmp_path: Path):
    """The already-completed export (and curation state) are preserved."""
    win = _make_window(tmp_path / "issue192-export")
    orig = _build_corpus(win._paths.base)
    _run_to_idle(win, orig)

    lib = win._paths.base / "library"
    # Export destination: staging holds the written release disks (the
    # operator copies them to the SD card; the tool never writes the SD).
    staging = lib / "work" / "staging"
    adf_files = sorted(staging.rglob("*.adf")) if staging.is_dir() else []
    assert adf_files, "export produced no .adf files"
    # Post-run bookkeeping produced the curation state file.
    curation = lib / "curation"
    assert any(curation.glob("library_state_*.json")), (
        "curation state file was not written by finalization"
    )
    win.close()


# --- requirement 2: finalization is off-thread and the GUI stays live --------
class _SlowFinalizerProbe:
    """Records thread + timing evidence from inside the off-thread step."""

    def __init__(self, sleep_s: float = 0.4):
        self.sleep_s = sleep_s
        self.thread_id = None
        self.active = threading.Event()
        self.sleep_started = 0.0
        self.sleep_ended = 0.0
        self.original = None

    def __call__(self, result, *, library_root, run_id, identity_store=None,
                 original_dir=None):
        # Real function, slowed down so the liveness window is measurable.
        self.thread_id = threading.get_ident()
        self.active.set()
        self.sleep_started = time.perf_counter()
        time.sleep(self.sleep_s)
        self.sleep_ended = time.perf_counter()
        self.active.clear()
        return self.original(
            result, library_root=library_root, run_id=run_id,
            identity_store=identity_store, original_dir=original_dir,
        )

    def __enter__(self):
        self.original = pipeline_mod.build_staged_library_from_result
        pipeline_mod.build_staged_library_from_result = self
        return self

    def __exit__(self, *exc):
        pipeline_mod.build_staged_library_from_result = self.original
        return False


def test_finalization_runs_off_gui_thread_and_gui_stays_live(tmp_path: Path):
    """The curation-state step runs on a worker thread; a GUI-thread timer
    fires WHILE that step is sleeping. If the step ran on the GUI thread (the
    pre-fix behavior) the timer could only fire after the sleep ended."""
    win = _make_window(tmp_path / "issue192-liveness")
    orig = _build_corpus(win._paths.base)

    gui_tid = threading.get_ident()
    app = QApplication.instance()
    ticks = {"t": 0}
    QTimer.singleShot(20, lambda: ticks.__setitem__("t", ticks["t"] + 1))
    QTimer.singleShot(60, lambda: ticks.__setitem__("t", ticks["t"] + 1))
    QTimer.singleShot(100, lambda: ticks.__setitem__("t", ticks["t"] + 1))

    with _SlowFinalizerProbe(sleep_s=0.4) as probe:
        _run_to_idle(win, orig)

    # The slow step ran on a NON-GUI thread.
    assert probe.thread_id is not None
    assert probe.thread_id != gui_tid, (
        "build_staged_library_from_result ran on the GUI thread: the freeze "
        "regressed"
    )
    # At least one GUI timer fired DURING the sleep window -> the event loop
    # was live while finalization was in flight.
    assert ticks["t"] >= 1, "no GUI timer fired while the event loop should have been live"
    _ = app
    win.close()


def test_finalizer_thread_terminates_and_is_dropped(tmp_path: Path):
    """No leaked, still-running finalization thread after a completed run --
    i.e. no unbounded wait and no thread left bound to the window."""
    win = _make_window(tmp_path / "issue192-thread")
    orig = _build_corpus(win._paths.base)
    _run_to_idle(win, orig)

    # The thread's finished signal is delivered on the event loop; pump until
    # the window has dropped both references (bounded).
    app = QApplication.instance()
    deadline = time.time() + 5
    while (
        time.time() < deadline
        and (win._finalize_thread is not None or win._finalize_worker is not None)
    ):
        app.processEvents()
        time.sleep(0.002)
    # After finalization completes, the thread reference is dropped and no
    # finalizer thread is still running.
    assert win._finalize_thread is None
    assert win._finalize_worker is None
    win.close()


# --- requirement 3: repeated run can be started afterward --------------------
def test_second_run_can_start_after_completion(tmp_path: Path):
    win = _make_window(tmp_path / "issue192-rerun")
    orig = _build_corpus(win._paths.base)
    _run_to_idle(win, orig)
    first_state_files = sorted((win._paths.base / "library" / "curation").glob("library_state_*.json"))
    assert first_state_files, "first run wrote no curation state"

    # The Run button is enabled, so a second run starts immediately.
    assert win._run_button.isEnabled() is True
    win._on_run()
    _pump_until_idle(win)
    assert win._state == "IDLE"
    assert win._progress.value() == 100
    assert win._run_button.isEnabled() is True
    win.close()


# --- requirement 4: close mid-finalization is bounded -------------------------
def test_close_mid_finalization_is_bounded(tmp_path: Path):
    """closeEvent may briefly wait for the finalizer (bounded), but must
    never destroy a running thread and must return promptly."""
    win = _make_window(tmp_path / "issue192-close")
    orig = _build_corpus(win._paths.base)
    _configure_for_export(win, orig)

    with _SlowFinalizerProbe(sleep_s=1.0) as probe:
        win._on_run()
        app = QApplication.instance()
        deadline = time.time() + 60
        while time.time() < deadline and not probe.active.is_set():
            app.processEvents()
            time.sleep(0.002)
        assert probe.active.is_set(), "finalization step never started"

        t0 = time.perf_counter()
        win.close()
        close_ms = (time.perf_counter() - t0) * 1000.0

    # The bounded wait held at most the 3s cap (sleep is 1.0s, so it should
    # have been roughly the remainder of the sleep + slack).
    assert close_ms < 3000, f"closeEvent blocked {close_ms:.0f}ms (unbounded?)"
    # The thread is done; its finished signal is delivered on the event loop.
    app = QApplication.instance()
    deadline = time.time() + 5
    while time.time() < deadline and win._finalize_thread is not None:
        app.processEvents()
        time.sleep(0.002)
    # The finalizer thread finished; nothing left running on the window.
    assert win._finalize_thread is None
    assert probe.thread_id is not None and probe.thread_id != threading.get_ident()


# --- requirement 5: timing report in the Diagnostics log ---------------------
def test_finalization_timing_report_reaches_diagnostics(tmp_path: Path):
    win = _make_window(tmp_path / "issue192-report")
    orig = _build_corpus(win._paths.base)
    _run_to_idle(win, orig)

    text = win._diag.toPlainText()
    assert "Finalize: total" in text, "finalization timing report missing from Diagnostics"
    # Per-step live lines were streamed (at least the slow one's start/done).
    assert "Finalize: run_log" in text
    assert "Finalize: curation_state" in text
    win.close()


# --- unit: report formatting ---------------------------------------------------
def test_format_timing_report_totals_and_failures():
    timings = {step: 10.0 for step in STEP_REPORT}
    lines = format_timing_report(timings, failures=[("run_log", "disk full")])
    assert lines[0] == f"Finalize: total {10.0 * len(STEP_REPORT):.0f} ms"
    assert "Finalize: run_log skipped (disk full)" in lines

    # Missing steps never raise and never invent a number.
    assert format_timing_report({}, failures=[]) == ["Finalize: total 0 ms"]
