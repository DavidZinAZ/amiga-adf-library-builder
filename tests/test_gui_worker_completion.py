from __future__ import annotations

import threading
from types import SimpleNamespace

from amiga_adf_library_builder.gui import worker as worker_mod
from amiga_adf_library_builder.gui.state import GuiState
from amiga_adf_library_builder.gui.worker import PipelineWorker


class _ThreadStub:
    def __init__(self):
        self.quit_called = False

    def quit(self):
        self.quit_called = True


def _run_worker(monkeypatch, pipeline_result, *, set_cancel_after_run=False):
    cfg = SimpleNamespace(
        library_root="library", cache_dir="cache", original_dir="original"
    )
    monkeypatch.setattr(
        worker_mod, "build_path_config_from_gui_state", lambda *a, **k: cfg
    )
    monkeypatch.setattr(
        worker_mod, "build_pipeline_kwargs", lambda *a, **k: ({}, {"cfg": cfg})
    )
    import amiga_adf_library_builder.initializer as initializer
    monkeypatch.setattr(initializer, "ensure_managed_directories", lambda cfg: None)

    cancel = threading.Event()

    def run_pipeline(*args, **kwargs):
        if set_cancel_after_run:
            cancel.set()  # late click after a successful pipeline/export return
        return pipeline_result

    import amiga_adf_library_builder.pipeline as pipeline_mod
    monkeypatch.setattr(pipeline_mod, "run_pipeline", run_pipeline)
    worker = PipelineWorker(GuiState(library_root="library"), cancel_event=cancel)
    thread = _ThreadStub()
    worker._thread = thread
    progress = []
    finished = []
    worker.progress.connect(lambda phase, value, detail: progress.append((phase, value)))
    worker.finished.connect(lambda result, error, cancelled, result_cfg: finished.append(
        (result, error, cancelled, result_cfg)
    ))
    worker._run()
    return worker, progress, finished, thread


def test_late_cancel_after_successful_export_does_not_override_success(monkeypatch):
    result = {"groups": 1, "export": {"releases_exported": 1}}
    worker, progress, finished, thread = _run_worker(
        monkeypatch, result, set_cancel_after_run=True
    )

    assert len(finished) == 1
    assert finished[0][0] is result
    assert finished[0][1:3] == ("", False)
    assert progress[-1] == ("Done", 100)
    assert worker.is_cancelled()
    assert thread.quit_called


def test_pipeline_explicit_cancel_is_not_reported_as_success(monkeypatch):
    worker, progress, finished, thread = _run_worker(
        monkeypatch, {"cancelled": True}, set_cancel_after_run=True
    )

    assert finished[0][0] is None
    assert finished[0][2] is True
    assert 100 not in [value for _, value in progress]
    assert thread.quit_called
