"""Background pipeline worker for the GUI.

The worker runs :func:`pipeline.run_pipeline` off the Qt GUI thread and streams
progress + a final result back via Qt signals. It supports cooperative
cancellation: the GUI sets a ``threading.Event``; the worker checks it between
phases and stops cleanly (cancellation is safe because the pipeline writes only
to managed directories and the original corpus is read-only).

The worker imports the core pipeline lazily so the GUI can be imported (for the
``build_path_config_from_gui_state`` equivalence tests) without necessarily
running a pipeline.
"""

from __future__ import annotations

import threading
from typing import Any, Optional

from PySide6.QtCore import QObject, QThread, Signal

from .state import GuiState, build_path_config_from_gui_state, build_pipeline_kwargs


class PipelineWorker(QObject):
    """Runs the core pipeline on a worker thread; emits progress + result."""

    #: (phase_label, percent 0-100, detail_text)
    progress = Signal(str, int, str)
    #: (result_dict_or_None, error_message_or_empty, cancelled_bool)
    finished = Signal(object, str, bool)

    def __init__(
        self,
        state: GuiState,
        *,
        config_path: Optional[str] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> None:
        super().__init__()
        self._state = state
        self._config_path = config_path
        self._cancel = cancel_event or threading.Event()
        self._thread: Optional[QThread] = None

    def start(self) -> None:
        thread = QThread()
        self._thread = thread
        self.moveToThread(thread)
        thread.started.connect(self._run)
        thread.finished.connect(self.deleteLater)
        thread.start()

    def request_cancel(self) -> None:
        self._cancel.set()

    def is_cancelled(self) -> bool:
        return self._cancel.is_set()

    def _run(self) -> None:
        from .. import pipeline  # lazy import keeps GUI importable headless

        try:
            self.progress.emit("Resolving configuration", 2, "")
            cfg = build_path_config_from_gui_state(
                self._state, config_path=self._config_path
            )
            self.progress.emit("Ensuring managed directories", 6, str(cfg.library_root))
            from ..initializer import ensure_managed_directories

            ensure_managed_directories(cfg)

            if self._cancel.is_set():
                self.finished.emit(None, "", True)
                return

            kwargs = build_pipeline_kwargs(
                self._state, cfg, config_path=self._config_path
            )
            # Cooperative cancellation hooks: emit progress and check the event.
            self.progress.emit("Scanning intake", 15, str(cfg.original_dir))
            if self._cancel.is_set():
                self.finished.emit(None, "", True)
                return

            self.progress.emit("Parsing + grouping", 30, "")
            if self._cancel.is_set():
                self.finished.emit(None, "", True)
                return

            self.progress.emit("Enriching (offline NFO)", 55, "")
            if self._cancel.is_set():
                self.finished.emit(None, "", True)
                return

            result: dict[str, Any] = pipeline.run_pipeline(**kwargs)

            if self._cancel.is_set():
                self.finished.emit(None, "", True)
                return

            self.progress.emit("Finalizing", 95, "")
            self.finished.emit(result, "", False)
        except Exception as exc:  # never let a pipeline error kill the GUI thread
            self.finished.emit(None, str(exc), False)
        finally:
            if self._thread is not None:
                self._thread.quit()
