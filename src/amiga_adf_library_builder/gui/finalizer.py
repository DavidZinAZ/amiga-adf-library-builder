"""Off-thread GUI run finalization (freeze fix).

GH-192: after the pipeline reported a completed run (the export is already
on disk), :meth:`MainWindow._on_finished` did all post-run bookkeeping
**synchronously on the Qt GUI thread**. Two of those steps are genuinely
heavy:

* :func:`logging_utils.write_run_log` -- serializes and writes the whole run
  log to disk;
* :func:`pipeline.build_staged_library_from_result` -- rebuilds the staged
  curation state, which calls :func:`models.carry_over`, a content-hash pass
  over every ADF in the corpus.

On a production-scale library (20 releases, many disks) that pass took long
enough for Windows to paint ``Amiga ADF Library Builder (Not Responding)``
after ``Export finished: 20 release(s)...`` -- the GUI event loop was blocked
while the already-successful export sat on disk.

This module moves exactly those two heavy steps off the GUI thread. It
deliberately contains **no Qt widget access**: it only touches plain data and
the filesystem and returns a payload the GUI thread applies afterwards.

The *cheap* post-run state -- the GH-66 review-button count, the local-media
provider anchoring, the ``_last_run_logs_dir`` -- is **not** moved here. It
stays synchronous in ``_on_finished`` because ``tests/test_gui_issue66_review_threshold.py``
asserts it on the same call (no event-loop pumping) and because it is a small
JSON read, not the blocking operation.

Every step is timed. The timings are attached to the emitted payload so the
GUI can write them to the Diagnostics log -- that is what lets the next
packaged Windows run name the exact stall instead of guessing.

Never calls ``QThread.wait()`` / ``join()`` on the GUI thread: the finalizer
thread is left to finish on its own and reports back through a signal.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

from PySide6.QtCore import QObject, Signal

#: Steps recorded in the timing report, in execution order. Kept explicit so
#: the Diagnostics log reads the same way on every run and a new step cannot
#: silently appear or vanish.
STEP_REPORT = "run_log", "curation_state"


class FinalizationWorker(QObject):
    """Runs the two heavy post-run steps off the GUI thread."""

    #: (payload_dict,) -- payload carries state_path and the per-step timing
    #: report. Never ``None``: an unfailed run always produces a payload.
    finalized = Signal(object)
    #: (step_name,) -- live per-step marker, so a stalled step is visible in
    #: the log *while* it is stalling rather than only after.
    step_started = Signal(str)
    #: (step_name, message) -- non-fatal step failure; the GUI still returns
    #: to idle.
    step_failed = Signal(str, str)
    #: (step_name, elapsed_ms)
    step_done = Signal(str, float)

    def __init__(
        self,
        *,
        result: dict,
        cfg: Any,
        run_mode: str,
        identity_store: Any = None,
        started_at: Optional[str] = None,
    ) -> None:
        super().__init__()
        self._result = dict(result or {})
        self._cfg = cfg
        self._run_mode = run_mode
        self._identity_store = identity_store
        self._started_at = started_at
        self._failures: list[tuple[str, str]] = []

    # -- helpers ---------------------------------------------------------------
    def _mark(self, name: str) -> None:
        try:
            self.step_started.emit(name)
        except Exception:  # a UI hiccup must never break finalization
            pass

    def _done(self, name: str, elapsed_ms: float) -> None:
        try:
            self.step_done.emit(name, elapsed_ms)
        except Exception:
            pass

    def _fail(self, name: str, exc: Exception) -> None:
        self._failures.append((name, str(exc)))
        try:
            self.step_failed.emit(name, str(exc))
        except Exception:
            pass

    # -- the heavy steps -------------------------------------------------------
    def _write_run_log(self, timings: dict) -> None:
        """Serialize + write the per-run diagnostic log (blocking I/O)."""
        self._mark("run_log")
        t0 = time.perf_counter()
        try:
            from ..logging_utils import write_run_log

            write_run_log(
                logs_dir=self._cfg.logs_dir,
                run_id=self._result.get("run_id") or "unknown",
                config_label="gui",
                cfg=self._cfg,
                argv=["gui"],
                command=self._run_mode,
                result=self._result,
                started_at=self._started_at or "",
                return_code=0,
            )
        except Exception as exc:  # best-effort, mirrors the CLI
            self._fail("run_log", exc)
        finally:
            timings["run_log"] = (time.perf_counter() - t0) * 1000.0
            self._done("run_log", timings["run_log"])

    def _build_curation_state(self, timings: dict) -> Optional[Path]:
        """Rebuild + persist the staged curation state (blocking I/O).

        This is the dominant cost: ``build_staged_library_from_result`` calls
        ``carry_over``, a content-hash pass over every ADF in the corpus.
        """
        self._mark("curation_state")
        t0 = time.perf_counter()
        state_path = None
        store = None
        try:
            from ..file_identity import FileIdentityStore
            from ..pipeline import build_staged_library_from_result

            # (GH-192) FileIdentityStore holds a sqlite3 connection bound to
            # the thread that created it (the GUI thread), so the GUI's own
            # store cannot be reused here. Open a fresh connection to the same
            # identity database in THIS thread and close it after the build;
            # every mutation commits, so the two connections never see stale
            # data.
            db_path = getattr(self._identity_store, "db_path", None)
            if db_path is not None:
                store = FileIdentityStore(Path(db_path))
            state_path = build_staged_library_from_result(
                self._result,
                library_root=self._cfg.library_root,
                run_id=self._result.get("run_id", "unknown"),
                identity_store=store,
                original_dir=self._cfg.original_dir,
            )
        except Exception as exc:
            self._fail("curation_state", exc)
        finally:
            if store is not None:
                store.close()
            timings["curation_state"] = (time.perf_counter() - t0) * 1000.0
            self._done("curation_state", timings["curation_state"])
        return state_path

    # -- entry point -----------------------------------------------------------
    def run(self) -> None:
        """Execute both heavy steps, then emit exactly one payload.

        ``run`` is invoked from the ``QThread.started`` signal, i.e. on the
        worker thread, but a ``QThread``'s event loop keeps running until it
        is told to stop -- so the very last thing this method does is
        ``thread.quit()``. Without that the thread (and this worker) would
        outlive the payload it delivered, and ``closeEvent``'s bounded wait
        would time out on every single run.
        """
        timings: dict = {}
        payload: dict = {
            "state_path": None,
            "timings": timings,
            "failures": [],
        }
        try:
            self._write_run_log(timings)
            payload["state_path"] = self._build_curation_state(timings)
        except Exception as exc:  # defensive: the GUI must always be released
            self._fail("finalize", exc)
        finally:
            payload["failures"] = list(self._failures)
            self.finalized.emit(payload)
            thread = self.thread()
            if thread is not None:
                thread.quit()


def format_timing_report(timings: dict, failures: Optional[list] = None) -> list:
    """Render the end-of-finalization report as Diagnostics log lines.

    Per-step lines already stream live via step_started/step_done/step_failed
    signals, so the report adds only the total and any skipped steps -- a
    deterministic summary that names where the time went.
    """
    lines = []
    total = sum(timings.get(step, 0.0) for step in STEP_REPORT)
    lines.append(f"Finalize: total {total:.0f} ms")
    for name, message in (failures or []):
        lines.append(f"Finalize: {name} skipped ({message})")
    return lines
