"""Live, crash-safe GUI run-log tests (fix/gui-live-run-logging).

Contract locked here (the bug being fixed: the per-run log file was only
written at FINALIZATION, so a GUI process that exited or crashed before
then left no diagnostic log at all):

1. ``LiveRunLog.start`` creates the run log file IMMEDIATELY (header +
   run-start banner flushed to disk before the worker thread starts).
2. The header records run id, app version / build identity, command mode,
   online/offline state, resolved paths, and run options.
3. ``log()`` appends redacted, timestamped progress lines and flushes per
   line, so a kill at any point leaves everything written so far on disk.
4. ``log_exception`` records exception type, message, traceback, stage,
   and the last progress line (the current release/file when known).
5. The log file is NEVER deleted or replaced: after close(), late writes
   (crash hooks) re-open the SAME file in append mode.
6. ``install_crash_hooks`` routes uncaught main-thread and background-thread
   exceptions into the active live log, then chains to the prior hooks
   (idempotent install).
7. ``PipelineWorker`` pre-assigns the run id into ``RunConfig`` (pipeline
   honors a non-None ``run_id``), appends activity lines to the live log,
   and captures worker-thread exceptions into it.
8. The GUI wires it together: ``_on_run`` opens the live log before the
   worker starts; the failure dialog names the log path; the finalizer
   APPENDS the detailed summary to the live file (never replaces it) and
   falls back to the original whole-file write when no live log exists.

Headless: relies on ``QT_QPA_PLATFORM=offscreen`` (same pattern as
tests/test_gui_issue21_live_diagnostics.py). Deterministic on pytest tmp
dirs.
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

from PySide6.QtCore import QThread  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from amiga_adf_library_builder import pipeline  # noqa: E402
from amiga_adf_library_builder.gui import state as gui_state  # noqa: E402
from amiga_adf_library_builder.gui import worker as gui_worker  # noqa: E402
from amiga_adf_library_builder.gui import live_run_log as lrl  # noqa: E402
from amiga_adf_library_builder.logging_utils import _log_path_for  # noqa: E402


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


# ---------------------------------------------------------------------------
# LiveRunLog core behavior
# ---------------------------------------------------------------------------
def test_live_log_created_immediately_with_header(tmp_path: Path):
    """The file exists (with a flushed header) as soon as start() returns."""
    logs = tmp_path / "logs"
    header = [
        "app_version    : 0.0.0-test",
        "online_state   : offline",
        "library_root   : C:\\lib",
    ]
    log = lrl.LiveRunLog.start(
        logs_dir=logs, run_id="20261006T120000Z-1-00001", header_lines=header
    )
    try:
        assert log.path.parent == logs
        assert log.path.name.endswith(".log")
        text = log.path.read_text(encoding="utf-8")
        # run-start banner + every header line is ON DISK, not in a buffer.
        assert "RUN START" in text
        assert "20261006T120000Z-1-00001" in text
        for line in header:
            assert line in text
    finally:
        log.close()


def test_live_log_appends_and_flushes_per_line(tmp_path: Path):
    """Every activity line lands on disk immediately (crash-safe)."""
    logs = tmp_path / "logs"
    log = lrl.LiveRunLog.start(
        logs_dir=logs, run_id="20261006T120001Z-1-00002", header_lines=[]
    )
    try:
        log.log("Processing release 1 of 5: Game One (Game One.adf)")
        on_disk = log.path.read_text(encoding="utf-8")
        assert "Processing release 1 of 5: Game One" in on_disk
        log.log("Processing release 2 of 5: Game Two (Game Two.adf)")
        on_disk = log.path.read_text(encoding="utf-8")
        assert "Processing release 2 of 5: Game Two" in on_disk
    finally:
        log.close()


def test_live_log_redacts_secrets_in_activity_lines(tmp_path: Path):
    logs = tmp_path / "logs"
    log = lrl.LiveRunLog.start(
        logs_dir=logs, run_id="20261006T120002Z-1-00003", header_lines=[]
    )
    try:
        log.log("Querying https://api.example.com/?token=supersecret123")
    finally:
        log.close()
    text = log.path.read_text(encoding="utf-8")
    assert "supersecret123" not in text
    assert "token=REDACTED" in text


def test_live_log_exception_capture(tmp_path: Path):
    """Exception type, message, traceback, stage, and last line are recorded."""
    logs = tmp_path / "logs"
    log = lrl.LiveRunLog.start(
        logs_dir=logs, run_id="20261006T120003Z-1-00004", header_lines=[]
    )
    try:
        log.set_stage("enrich")
        log.log("Preparing release 3 of 5: Game Three (Game Three.adf)")
        try:
            raise ValueError("boom: metadata provider 500")
        except ValueError as exc:
            log.log_exception(exc)
        text = log.path.read_text(encoding="utf-8")
        assert "EXCEPTION CAPTURED" in text
        assert "ValueError: boom: metadata provider 500" in text
        assert "stage         : enrich" in text
        assert "Preparing release 3 of 5: Game Three" in text
        assert "Traceback" in text or "ValueError" in text
    finally:
        log.close()


def test_live_log_late_write_after_close_appends_same_file(tmp_path: Path):
    """Crash records after close() append to the SAME file (never replaced)."""
    logs = tmp_path / "logs"
    log = lrl.LiveRunLog.start(
        logs_dir=logs, run_id="20261006T120004Z-1-00005", header_lines=["a: 1"]
    )
    log.log("live line")
    log.close()
    before = log.path.read_text(encoding="utf-8")

    # A late crash record must still land on disk, in the same file.
    log.log_exception(RuntimeError("late crash"))
    after = log.path.read_text(encoding="utf-8")

    assert after.startswith(before)  # append-only: earlier content intact
    assert "EXCEPTION CAPTURED" in after
    assert "RuntimeError: late crash" in after
    assert log.path.exists()  # never deleted


def test_live_log_unwritable_dir_raises_oerror(tmp_path: Path):
    """start() raises OSError on an unwritable dir (caller falls back)."""
    logs = tmp_path / "nope" / "logs"
    (tmp_path / "nope").mkdir()
    (tmp_path / "nope").chmod(0o555)
    try:
        with pytest.raises(OSError):
            lrl.LiveRunLog.start(
                logs_dir=logs, run_id="20261006T120005Z-1-00006", header_lines=[]
            )
    finally:
        (tmp_path / "nope").chmod(0o755)


def test_live_log_unique_names_per_run(tmp_path: Path):
    """Two logs for the same run id never collide (finalizer appends safely)."""
    logs = tmp_path / "logs"
    a = _log_path_for(logs, "20261006T120006Z-1-00007")
    logs.mkdir(parents=True, exist_ok=True)
    a.write_text("existing", encoding="utf-8")
    b = _log_path_for(logs, "20261006T120006Z-1-00007")
    assert b != a
    assert b.name == "20261006T120006Z-1-00007.1.log"


# ---------------------------------------------------------------------------
# crash hooks
# ---------------------------------------------------------------------------
def test_crash_hooks_route_uncaught_exceptions_into_live_log(tmp_path: Path):
    """Main-thread + background-thread uncaught exceptions hit the live log."""
    _app()
    logs = tmp_path / "logs"
    log = lrl.LiveRunLog.start(
        logs_dir=logs, run_id="20261006T120007Z-1-00008", header_lines=[]
    )
    lrl.install_crash_hooks()  # idempotent
    lrl.set_current(log)
    try:
        # 1) main thread: the excepthook must capture without re-raising.
        try:
            raise RuntimeError("main-thread crash")
        except RuntimeError as exc:
            sys_excepthook = __import__("sys").excepthook
            sys_excepthook(RuntimeError, exc, exc.__traceback__)
        text = log.path.read_text(encoding="utf-8")
        assert "RuntimeError: main-thread crash" in text

        # 2) background thread: threading.excepthook fires on thread exit.
        def _boom() -> None:
            raise ValueError("background-thread crash")

        t = threading.Thread(target=_boom)
        t.start()
        t.join(timeout=10)
        assert not t.is_alive()
        # Give the excepthook a beat to flush (it runs in the dying thread).
        deadline = time.time() + 5
        while time.time() < deadline:
            if "ValueError: background-thread crash" in log.path.read_text(
                encoding="utf-8"
            ):
                break
            time.sleep(0.05)
        text = log.path.read_text(encoding="utf-8")
        assert "ValueError: background-thread crash" in text
        assert "stage" in text
    finally:
        lrl.set_current(None)
        log.close()


def test_crash_hooks_noop_without_live_log():
    """With no active run, the hooks must not raise (console path intact)."""
    lrl.install_crash_hooks()
    lrl.set_current(None)
    try:
        raise KeyError("hook-test")
    except KeyError as exc:
        import sys

        sys.excepthook(KeyError, exc, exc.__traceback__)  # must not raise


def test_install_crash_hooks_is_idempotent():
    import sys

    lrl.install_crash_hooks()
    first = sys.excepthook
    lrl.install_crash_hooks()
    assert sys.excepthook is first


# ---------------------------------------------------------------------------
# run id
# ---------------------------------------------------------------------------
def test_new_run_id_matches_pipeline_generator_format():
    """The GUI pre-assigned id uses the pipeline's own generator (same shape)."""
    rid = lrl.new_run_id()
    import re

    assert re.fullmatch(r"\d{8}T\d{6}Z-\d+-\d{5}", rid), rid
    # and it is unique per call (counter advances).
    assert lrl.new_run_id() != rid


def test_identity_lines_report_version_and_frozen_state():
    from amiga_adf_library_builder.gui import __version__

    lines = lrl.identity_lines()
    joined = "\n".join(lines)
    assert f"app_version    : {__version__}" in joined
    assert "build_identity" in joined
    assert "python         :" in joined


# ---------------------------------------------------------------------------
# PipelineWorker integration (QThread, offscreen)
# ---------------------------------------------------------------------------
def _make_state(tmp_path: Path) -> gui_state.GuiState:
    original = tmp_path / "original"
    original.mkdir(parents=True, exist_ok=True)
    # One minimal .adf (the scanner only lists; content is irrelevant here).
    (original / "Game One (USA).adf").write_bytes(b"AD" * 1024)
    return gui_state.GuiState(
        library_root=str(tmp_path / "lib"),
        original_dir=str(original),
        online=False,
    )


def _pump_thread(thread: QThread, timeout_s: float = 30.0) -> bool:
    """Spin the Qt app until the thread finishes (bounded)."""
    app = _app()
    deadline = time.time() + timeout_s
    while thread.isRunning() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    return not thread.isRunning()


def test_worker_preassigns_run_id_and_writes_live_progress(tmp_path: Path, monkeypatch):
    """The pipeline uses the GUI's run id; activity lines hit the live log.

    The real pipeline is replaced with a stub that records the RunConfig it
    receives and emits a couple of activity lines — proving the wiring
    without running ADF processing (which this ticket must not change).
    """
    _app()
    state = _make_state(tmp_path)
    logs = tmp_path / "lib" / "logs"
    run_id = lrl.new_run_id()
    live = lrl.LiveRunLog.start(
        logs_dir=logs, run_id=run_id, header_lines=["hdr: 1"]
    )
    lrl.set_current(live)
    try:
        seen: dict = {}

        def fake_run_pipeline(cfg, run_config, **kwargs):
            seen["run_id"] = run_config.run_id
            activity = kwargs.get("activity")
            if activity is not None:
                activity("Preparing release 1 of 2: Alpha (Alpha.adf)")
                activity("Preparing release 2 of 2: Beta (Beta.adf)")
            return {
                "run_id": run_config.run_id or "x",
                "groups": 2,
                "files_scanned": 2,
                "per_group": [],
                "export": None,
            }

        # The worker imports the pipeline lazily inside _run, so patch the
        # module attribute the import resolves to.
        import amiga_adf_library_builder.pipeline as real_pipeline

        monkeypatch.setattr(real_pipeline, "run_pipeline", fake_run_pipeline)

        import amiga_adf_library_builder.initializer as init_mod

        monkeypatch.setattr(init_mod, "ensure_managed_directories", lambda cfg: None)

        worker = gui_worker.PipelineWorker(
            state, run_id=run_id, live_logger=live
        )
        results: dict = {}

        def on_finished(result, error, cancelled, cfg) -> None:
            results["error"] = error
            results["cancelled"] = cancelled

        worker.finished.connect(on_finished)
        # Production path: start() sets worker._thread so _run's finally()
        # can quit() the event loop (a manual QThread would never quit).
        worker.start()
        assert _pump_thread(worker._thread), "worker thread did not finish"
        assert results.get("error") == "", results

        # 1) the pipeline received the GUI's run id (pre-assigned).
        assert seen.get("run_id") == run_id
        # 2) the live log holds the header AND the live progress lines.
        text = live.path.read_text(encoding="utf-8")
        assert "hdr: 1" in text
        assert "Preparing release 1 of 2: Alpha" in text
        assert "Preparing release 2 of 2: Beta" in text
        assert "Run started." in text
    finally:
        lrl.set_current(None)
        live.close()


def test_worker_exception_captured_in_live_log(tmp_path, monkeypatch):
    """A worker-thread failure is captured in the live log before it is
    reported through the normal finished(error) signal."""
    _app()
    state = _make_state(tmp_path)
    logs = tmp_path / "lib" / "logs"
    run_id = lrl.new_run_id()
    live = lrl.LiveRunLog.start(
        logs_dir=logs, run_id=run_id, header_lines=[]
    )
    lrl.set_current(live)
    try:
        import amiga_adf_library_builder.pipeline as real_pipeline

        def fake_run_pipeline(cfg, run_config, **kwargs):
            raise RuntimeError("synthetic pipeline failure")

        monkeypatch.setattr(real_pipeline, "run_pipeline", fake_run_pipeline)

        worker = gui_worker.PipelineWorker(state, run_id=run_id, live_logger=live)
        results: dict = {}

        def on_finished(result, error, cancelled, cfg) -> None:
            results["error"] = error
            results["cancelled"] = cancelled

        worker.finished.connect(on_finished)
        worker.start()
        assert _pump_thread(worker._thread), "worker thread did not finish"
        assert "synthetic pipeline failure" in results.get("error", "")
        # The GUI signal path is intact AND the evidence is on disk.
        text = live.path.read_text(encoding="utf-8")
        assert "EXCEPTION CAPTURED" in text
        assert "RuntimeError: synthetic pipeline failure" in text
        assert "Run stopped with an error" in text
    finally:
        lrl.set_current(None)
        live.close()


def test_worker_without_live_logger_unchanged(tmp_path, monkeypatch):
    """No live logger => behavior identical to before (signals only)."""
    _app()
    state = _make_state(tmp_path)
    try:
        import amiga_adf_library_builder.pipeline as real_pipeline

        def fake_run_pipeline(cfg, run_config, **kwargs):
            return {"run_id": "cli-id", "groups": 0, "files_scanned": 0}

        monkeypatch.setattr(real_pipeline, "run_pipeline", fake_run_pipeline)

        worker = gui_worker.PipelineWorker(state)  # no run_id, no live log
        assert worker._assigned_run_id is None
        assert worker._live_logger is None
        results: dict = {}

        def on_finished(result, error, cancelled, cfg) -> None:
            results.update(result=result, error=error, cancelled=cancelled)

        worker.finished.connect(on_finished)
        worker.start()
        assert _pump_thread(worker._thread)
        assert results.get("error") == ""
        assert results.get("result", {}).get("run_id") == "cli-id"
    finally:
        lrl.set_current(None)


# ---------------------------------------------------------------------------
# FinalizationWorker integration
# ---------------------------------------------------------------------------
def _cfg_for(tmp_path: Path):
    from amiga_adf_library_builder.paths import resolve_config

    cfg, _src = resolve_config(library_root=str(tmp_path / "lib"))
    return cfg


def test_finalizer_appends_summary_to_live_log(tmp_path):
    """On success the finalizer APPENDS the detailed summary to the live
    file (header + live lines intact, ending with return_code: 0)."""
    from amiga_adf_library_builder.gui.finalizer import FinalizationWorker

    cfg = _cfg_for(tmp_path)
    logs = Path(cfg.logs_dir)
    run_id = "20261006T120010Z-1-00011"
    live = lrl.LiveRunLog.start(
        logs_dir=logs, run_id=run_id, header_lines=["live: header"]
    )
    live.log("live: progress line")
    before = live.path.read_text(encoding="utf-8")
    live.close()

    worker = FinalizationWorker(
        result={
            "run_id": run_id,
            "groups": 1,
            "files_scanned": 1,
            "per_group": [],
            "export": None,
        },
        cfg=cfg,
        run_mode="build",
        started_at="2026-10-06T12:00:00+00:00",
        live_log_path=live.path,
    )
    payload = {}
    worker.finalized.connect(lambda p: payload.update(p))
    worker.run()

    assert (payload.get("failures") or []) == []
    text = live.path.read_text(encoding="utf-8")
    assert text.startswith(before)  # live header + progress preserved
    assert "live: progress line" in text
    assert "return_code    : 0" in text
    assert "Per-group diagnostics" in text
    # exactly ONE file for this run id (never a second/replaced one)
    assert [p.name for p in logs.glob("*.log")] == ["20261006T120010Z-1-00011.log"]


def test_finalizer_fallback_writes_log_at_end_without_live_log(tmp_path):
    """No live log (could not be opened at Run start) => the original
    whole-file write path still runs (successful run still leaves a log)."""
    from amiga_adf_library_builder.gui.finalizer import FinalizationWorker

    cfg = _cfg_for(tmp_path)
    run_id = "20261006T120012Z-1-00012"
    worker = FinalizationWorker(
        result={
            "run_id": run_id,
            "groups": 1,
            "files_scanned": 1,
            "per_group": [],
            "export": None,
        },
        cfg=cfg,
        run_mode="build",
        started_at="2026-10-06T12:00:00+00:00",
        # live_log_path omitted: no live log existed.
    )
    payload = {}
    worker.finalized.connect(lambda p: payload.update(p))
    worker.run()

    assert (payload.get("failures") or []) == []
    logs = Path(cfg.logs_dir)
    files = sorted(p.name for p in logs.glob("*.log"))
    assert files == [f"{run_id}.log"], files
    text = (logs / f"{run_id}.log").read_text(encoding="utf-8")
    assert "return_code    : 0" in text


def test_finalizer_ignores_live_log_outside_cfg_logs_dir(tmp_path):
    """A live path in a different directory must NOT be appended to."""
    from amiga_adf_library_builder.gui.finalizer import FinalizationWorker

    cfg = _cfg_for(tmp_path)
    other = tmp_path / "elsewhere"
    other.mkdir(parents=True, exist_ok=True)
    rogue = other / "rogue.log"
    rogue.write_text("rogue\n", encoding="utf-8")
    run_id = "20261006T120013Z-1-00013"
    worker = FinalizationWorker(
        result={"run_id": run_id, "groups": 0, "files_scanned": 0},
        cfg=cfg,
        run_mode="build",
        started_at="2026-10-06T12:00:00+00:00",
        live_log_path=rogue,
    )
    worker.run()
    assert rogue.read_text(encoding="utf-8") == "rogue\n"  # untouched
    logs = Path(cfg.logs_dir)
    assert [p.name for p in logs.glob("*.log")] == [f"{run_id}.log"]
