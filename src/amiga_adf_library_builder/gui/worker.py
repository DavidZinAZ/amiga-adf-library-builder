"""Background pipeline worker for the GUI.

The worker runs :func:`pipeline.run_pipeline` off the Qt GUI thread and streams
progress + a final result back via Qt signals. It supports cooperative
cancellation: the GUI sets a ``threading.Event``; the worker checks it between
phases and stops cleanly (cancellation is safe because the pipeline writes only
to managed directories and the original corpus is read-only).

Issue #21 (live Diagnostics): the worker also emits ``activity`` signals --
timestamped, plain-language, REDACTED lines describing what the run is doing
at each step (folders, scanning, metadata enrichment, export, summary) -- so
the Diagnostics view shows real progress instead of only stage names. The
redaction comes from :func:`amiga_adf_library_builder.logging_utils.redact`
(same filter the per-run log file uses) so a folder path or provider detail can
never leak a secret value into the UI.

The worker imports the core pipeline lazily so the GUI can be imported (for the
``build_path_config_from_gui_state`` equivalence tests) without necessarily
running a pipeline.
"""

from __future__ import annotations

import threading
from typing import Any, Optional

from PySide6.QtCore import QObject, QThread, Signal

from ..logging_utils import redact
from .state import GuiState, build_path_config_from_gui_state, build_pipeline_kwargs


class PipelineWorker(QObject):
    """Runs the core pipeline on a worker thread; emits progress + result."""

    #: (phase_label, percent 0-100, detail_text)
    progress = Signal(str, int, str)
    #: (result_dict_or_None, error_message_or_empty, cancelled_bool)
    finished = Signal(object, str, bool)
    #: (text,) -- one plain-language, redacted activity line for the live
    #: Diagnostics log (issue #21). The GUI appends it with a timestamp.
    activity = Signal(str)

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

    def _act(self, text: str) -> None:
        """Emit one redacted activity line (never raises)."""
        try:
            self.activity.emit(redact(str(text)))
        except Exception:  # a logging hiccup must never break the run
            pass

    def _cancelled(self) -> bool:
        if self._cancel.is_set():
            self.finished.emit(None, "", True)
            return True
        return False

    def _run(self) -> None:
        from .. import pipeline  # lazy import keeps GUI importable headless

        try:
            self._act("Run started.")
            self.progress.emit("Checking your settings", 2, "")
            cfg = build_path_config_from_gui_state(
                self._state, config_path=self._config_path
            )
            self._act(
                "Folders ready: library at "
                f"{redact(str(cfg.library_root))}."
            )
            self.progress.emit("Preparing folders", 6, str(cfg.library_root))
            from ..initializer import ensure_managed_directories

            ensure_managed_directories(cfg)

            if self._cancelled():
                return

            kwargs = build_pipeline_kwargs(
                self._state, cfg, config_path=self._config_path,
                activity=self._act,
            )
            # Cooperative cancellation hooks: emit progress and check the event.
            self.progress.emit(
                "Scanning the original disks", 15, str(cfg.original_dir)
            )
            if self._cancelled():
                return

            online = bool(self._state.online)
            self._act(
                "Metadata will be filled in from online sources "
                "(this can take a while)."
                if online
                else "Metadata will be filled in from cached copies (offline)."
            )
            self.progress.emit("Organizing and preparing metadata", 30, "")
            if self._cancelled():
                return

            self.progress.emit("Filling in missing metadata", 55, "")
            if self._cancelled():
                return

            result: dict[str, Any] = pipeline.run_pipeline(**kwargs)

            if self._cancelled():
                return

            self.progress.emit("Finishing up", 95, "")
            self.finished.emit(result, "", False)
        except Exception as exc:  # never let a pipeline error kill the GUI thread
            self._act(f"Run stopped with an error: {redact(str(exc))}")
            self.finished.emit(None, str(exc), False)
        finally:
            if self._thread is not None:
                self._thread.quit()
