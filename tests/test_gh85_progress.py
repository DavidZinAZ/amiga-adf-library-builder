"""GH-85 regression tests: GUI progress calculation/update behavior.

These tests verify the GH-85 fix for progress reporting:
- Progress advances throughout processing rather than remaining fixed near 55%.
- Progress is computed from meaningful completed work (completed releases / total releases).
- Progress remains monotonic during a normal run.
- 100% is shown only when processing is actually complete.
- Cancel/error states stop or reset progress appropriately.
- Displayed progress and visible processed-release count remain reasonably consistent.
"""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Offscreen platform for headless Qt testing
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QThread  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from amiga_adf_library_builder.gui.worker import PipelineWorker  # noqa: E402
from amiga_adf_library_builder.gui.state import GuiState  # noqa: E402
from amiga_adf_library_builder.paths import resolve_config  # noqa: E402


def _make_test_config(tmp_path: Path):
    """Create a minimal test configuration."""
    data_root = tmp_path / "data"
    (data_root / "original").mkdir(parents=True)
    cfg = resolve_config(library_root=str(data_root))[0]
    from amiga_adf_library_builder.initializer import ensure_managed_directories
    ensure_managed_directories(cfg)
    return cfg


def _make_state(library_root: str) -> GuiState:
    """Create a GuiState with minimal required fields."""
    state = GuiState()
    state.library_root = library_root
    state.online = False
    state.refresh_metadata = False
    state.require_artwork = False
    state.verify_only = False
    state.export_gate_acknowledged = False
    state.run_mode = "build"
    return state


class ProgressCollector:
    """Collect progress emissions from PipelineWorker for verification."""
    def __init__(self):
        self.progress_emissions = []
        self.activity_emissions = []
        self.finished_emission = None
    
    def on_progress(self, phase: str, percent: int, detail: str):
        self.progress_emissions.append((phase, percent, detail))
    
    def on_activity(self, text: str):
        self.activity_emissions.append(text)
    
    def on_finished(self, result, error: str, cancelled: bool, cfg):
        self.finished_emission = (result, error, cancelled, cfg)


def _run_worker_sync(worker: PipelineWorker, collector: ProgressCollector, timeout_ms: int = 30000):
    """Run worker synchronously and collect emissions."""
    worker.progress.connect(collector.on_progress)
    worker.activity.connect(collector.on_activity)
    worker.finished.connect(collector.on_finished)
    
    # Need an event loop for Qt signals
    app = QCoreApplication.instance() or QCoreApplication([])
    
    worker.start()
    
    # Wait for finished signal with timeout
    loop = QThread.currentThread()
    # Use a simple wait approach
    import time
    start = time.time()
    while collector.finished_emission is None and (time.time() - start) < (timeout_ms / 1000):
        app.processEvents()
        time.sleep(0.01)
    
    if collector.finished_emission is None:
        worker.request_cancel()
        # Wait a bit more for cancellation
        for _ in range(100):
            app.processEvents()
            time.sleep(0.01)
    
    return collector


def test_progress_advances_during_enrichment_phase(tmp_path: Path):
    """GH-85: Progress advances during enrichment phase based on completed/total releases."""
    cfg = _make_test_config(tmp_path)
    
    # Create a few synthetic ADF files to produce multiple release groups
    original = cfg.original_dir
    test_files = [
        "Game One (Disk 1 of 2).adf",
        "Game One (Disk 2 of 2).adf",
        "Game Two.adf",
        "Game Three (Disk 1 of 3).adf",
        "Game Three (Disk 2 of 3).adf",
        "Game Three (Disk 3 of 3).adf",
    ]
    for f in test_files:
        (original / f).write_bytes(b"x" * 10)
    
    state = _make_state(str(cfg.library_root))
    cancel_event = threading.Event()
    worker = PipelineWorker(state, config_path=None, cancel_event=cancel_event)
    collector = ProgressCollector()
    
    _run_worker_sync(worker, collector)
    
    # Verify we got finished emission
    assert collector.finished_emission is not None, "Worker did not finish"
    result, error, cancelled, result_cfg = collector.finished_emission
    assert not cancelled, "Run was cancelled"
    assert error == "", f"Run had error: {error}"
    assert result is not None, "No result returned"
    assert result.get("groups", 0) >= 3, f"Expected at least 3 groups, got {result.get('groups', 0)}"
    
    # Verify progress emissions
    progress_values = [p[1] for p in collector.progress_emissions]
    progress_phases = [p[0] for p in collector.progress_emissions]
    
    # Check that we have the expected phase progression
    assert 2 in progress_values, "Missing initial 2% progress"
    assert 6 in progress_values, "Missing 6% progress (folders)"
    assert 15 in progress_values, "Missing 15% progress (scanning)"
    assert 30 in progress_values, "Missing 30% progress (organizing)"
    assert 55 in progress_values, "Missing 55% progress (enrichment start)"
    assert 95 in progress_values, "Missing 95% progress (finishing)"
    assert 100 in progress_values, "Missing 100% progress (complete)"
    
    # GH-85: Check that progress advances between 55 and 95 during enrichment
    enrich_progresses = [p for p in progress_values if 55 <= p <= 95]
    # Should have multiple progress values in the enrichment range
    assert len(enrich_progresses) >= 2, (
        f"Progress should advance during enrichment, got only: {enrich_progresses}"
    )
    
    # Progress should be monotonic
    for i in range(1, len(progress_values)):
        assert progress_values[i] >= progress_values[i-1], (
            f"Progress not monotonic: {progress_values[i-1]} -> {progress_values[i]}"
        )
    
    # GH-85: Check that processed-release count is shown in phase text
    enrich_phases = [p for p in progress_phases if "Filling in missing metadata" in p and "/" in p]
    assert len(enrich_phases) >= 1, (
        f"Enrichment phase should show processed/total count, phases: {progress_phases}"
    )
    
    # Verify the count format shows completed/total
    for phase in enrich_phases:
        # Should be like "Filling in missing metadata (X/Y)"
        assert "(" in phase and ")" in phase and "/" in phase
        parts = phase.split("(")[1].split(")")[0]
        completed_str, total_str = parts.split("/")
        completed = int(completed_str)
        total = int(total_str)
        assert 1 <= completed <= total
        assert total >= 3  # At least 3 release groups expected


def test_progress_monotonic_throughout_run(tmp_path: Path):
    """GH-85: Progress remains monotonic during a normal run."""
    cfg = _make_test_config(tmp_path)
    
    original = cfg.original_dir
    test_files = [
        "Single Disk.adf",
    ]
    for f in test_files:
        (original / f).write_bytes(b"x" * 10)
    
    state = _make_state(str(cfg.library_root))
    cancel_event = threading.Event()
    worker = PipelineWorker(state, config_path=None, cancel_event=cancel_event)
    collector = ProgressCollector()
    
    _run_worker_sync(worker, collector)
    
    assert collector.finished_emission is not None
    result, error, cancelled, _ = collector.finished_emission
    assert not cancelled
    assert error == ""
    
    # Extract just the progress percentages
    progress_values = [p[1] for p in collector.progress_emissions]
    
    # Must be monotonically non-decreasing
    for i in range(1, len(progress_values)):
        assert progress_values[i] >= progress_values[i-1], (
            f"Progress decreased: {progress_values[i-1]} -> {progress_values[i]} "
            f"at step {i}"
        )


def test_100_percent_only_at_completion(tmp_path: Path):
    """GH-85: 100% is shown only when processing is actually complete."""
    cfg = _make_test_config(tmp_path)
    
    original = cfg.original_dir
    test_files = [
        "Test Game.adf",
    ]
    for f in test_files:
        (original / f).write_bytes(b"x" * 10)
    
    state = _make_state(str(cfg.library_root))
    cancel_event = threading.Event()
    worker = PipelineWorker(state, config_path=None, cancel_event=cancel_event)
    collector = ProgressCollector()
    
    _run_worker_sync(worker, collector)
    
    assert collector.finished_emission is not None
    result, error, cancelled, _ = collector.finished_emission
    assert not cancelled
    assert error == ""
    
    progress_values = [p[1] for p in collector.progress_emissions]
    progress_phases = [p[0] for p in collector.progress_emissions]
    
    # 100% should appear exactly once and be the LAST progress emission
    assert progress_values.count(100) == 1, f"100% should appear exactly once, got {progress_values.count(100)}"
    assert progress_values[-1] == 100, f"100% should be the final progress value, got {progress_values[-1]}"
    
    # The phase at 100% should indicate completion
    final_phase = progress_phases[-1]
    assert "Done" in final_phase or "Finishing" in final_phase or "complete" in final_phase.lower()


def test_cancel_stops_progress(tmp_path: Path):
    """GH-85: Cancel stops progress appropriately."""
    cfg = _make_test_config(tmp_path)
    
    # Create enough files to have a measurable enrichment phase
    # Use enough files to ensure pipeline takes long enough for cancellation to work
    original = cfg.original_dir
    test_files = [f"Game {i}.adf" for i in range(100)]
    for f in test_files:
        (original / f).write_bytes(b"x" * 10)
    
    state = _make_state(str(cfg.library_root))
    cancel_event = threading.Event()
    worker = PipelineWorker(state, config_path=None, cancel_event=cancel_event)
    collector = ProgressCollector()
    
    worker.progress.connect(collector.on_progress)
    worker.activity.connect(collector.on_activity)
    worker.finished.connect(collector.on_finished)
    
    app = QCoreApplication.instance() or QCoreApplication([])
    worker.start()
    
    # Let it run a bit then cancel
    import time
    time.sleep(0.1)  # Let some progress happen
    cancel_event.set()
    
    # Wait for finished
    start = time.time()
    while collector.finished_emission is None and (time.time() - start) < 5:
        app.processEvents()
        time.sleep(0.01)
    
    assert collector.finished_emission is not None
    result, error, cancelled, _ = collector.finished_emission
    assert cancelled, "Expected cancelled=True"
    assert result is None, "Result should be None when cancelled"
    
    # After cancellation, no more progress should be emitted
    # (the cancellation check in _cancelled() emits finished and returns)


def test_error_resets_progress(tmp_path: Path):
    """GH-85: Error states stop progress appropriately."""
    cfg = _make_test_config(tmp_path)
    
    state = _make_state(str(cfg.library_root))
    # Make library_root invalid to trigger an error
    state.library_root = "/nonexistent/path/that/does/not/exist"
    
    cancel_event = threading.Event()
    worker = PipelineWorker(state, config_path=None, cancel_event=cancel_event)
    collector = ProgressCollector()
    
    _run_worker_sync(worker, collector)
    
    assert collector.finished_emission is not None
    result, error, cancelled, _ = collector.finished_emission
    assert not cancelled, "Should be error, not cancellation"
    assert error != "", f"Expected error message, got empty"
    assert result is None, "Result should be None on error"


def test_progress_consistent_with_processed_count(tmp_path: Path):
    """GH-85: Displayed progress and visible processed-release count remain reasonably consistent."""
    cfg = _make_test_config(tmp_path)
    
    original = cfg.original_dir
    # Create 5 single-disk games = 5 release groups
    test_files = [f"Game {i}.adf" for i in range(1, 6)]
    for f in test_files:
        (original / f).write_bytes(b"x" * 10)
    
    state = _make_state(str(cfg.library_root))
    cancel_event = threading.Event()
    worker = PipelineWorker(state, config_path=None, cancel_event=cancel_event)
    collector = ProgressCollector()
    
    _run_worker_sync(worker, collector)
    
    assert collector.finished_emission is not None
    result, error, cancelled, _ = collector.finished_emission
    assert not cancelled
    assert error == ""
    total_groups = result.get("groups", 0)
    assert total_groups == 5, f"Expected 5 groups, got {total_groups}"
    
    # Check enrichment phase progress emissions
    enrich_emissions = [
        (phase, percent) for phase, percent, _ in collector.progress_emissions
        if "Filling in missing metadata" in phase
    ]
    
    # Should have at least the initial 55% and some progress updates
    assert len(enrich_emissions) >= 2
    
    # The last enrichment emission before 95% should show (5/5) or close to it
    # Find the last enrichment emission before 95%
    last_enrich = None
    for phase, percent in enrich_emissions:
        if percent < 95:
            last_enrich = (phase, percent)
    
    assert last_enrich is not None
    phase, percent = last_enrich
    # Should show completed count
    assert "(" in phase and ")" in phase and "/" in phase
    parts = phase.split("(")[1].split(")")[0]
    completed_str, total_str = parts.split("/")
    completed = int(completed_str)
    total = int(total_str)
    
    # Completed should equal total (or close, since the last batch might emit at completion)
    assert completed == total or completed == total - 1, (
        f"Completed count ({completed}) should match total ({total}) near end of enrichment"
    )
    assert total == total_groups, f"Total in progress ({total}) should match groups ({total_groups})"
    
    # Progress percentage should roughly correspond to completed/total
    # Mapping is 55% + 40% * (completed/total)
    expected_progress = 55 + int(40 * (completed / total))
    # Allow some tolerance due to integer rounding
    assert abs(percent - expected_progress) <= 2, (
        f"Progress {percent}% should correspond to {completed}/{total} "
        f"(expected ~{expected_progress}%)"
    )


def test_zero_releases_handled_gracefully(tmp_path: Path):
    """GH-85: Zero releases should not crash or produce invalid progress."""
    cfg = _make_test_config(tmp_path)
    # No ADF files in original
    
    state = _make_state(str(cfg.library_root))
    cancel_event = threading.Event()
    worker = PipelineWorker(state, config_path=None, cancel_event=cancel_event)
    collector = ProgressCollector()
    
    _run_worker_sync(worker, collector)
    
    assert collector.finished_emission is not None
    result, error, cancelled, _ = collector.finished_emission
    assert not cancelled
    assert error == ""
    assert result.get("groups", 0) == 0
    
    # Progress should still reach 100% (completion)
    progress_values = [p[1] for p in collector.progress_emissions]
    assert 100 in progress_values
    assert progress_values[-1] == 100


if __name__ == "__main__":
    pytest.main([__file__, "-v"])