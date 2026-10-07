"""Live, crash-safe run log for the GUI.

Problem this module fixes
-------------------------
Prior to this module the per-run log file was created only once, at the very
end of a run, by ``FinalizationWorker._write_run_log`` (GH-192 moved the log
write off the GUI thread). If the GUI process exited or crashed before
finalization -- the operator closed the window, an uncaught exception escaped
the worker thread, the machine lost power -- **no log file existed at all**:
the only diagnostic evidence of the run was gone.

Contract
--------
* :meth:`LiveRunLog.start` opens the run log file immediately when the
  operator presses Run, writes a redacted header (run id, app version /
  build identity, command mode, online state, resolved paths, run options)
  and flushes it to disk.
* Every progress line (the same redacted lines the Diagnostics view shows)
  is appended **live** and flushed per line, so an unexpected termination at
  any moment leaves the evidence written so far on disk.
* Worker-thread failures, background-thread failures, and uncaught
  exceptions (main thread or worker thread) are captured with exception
  type, message, full traceback, the current stage, and the last progress
  line (which names the current release/file when known).
* The partial log is never deleted, truncated, or replaced during cleanup.
  On a successful run the finalizer APPENDS the detailed summary (with
  ``return_code: 0``) to this same file, so the file spans the whole run.

Threading
---------
``LiveRunLog`` is a plain (non-Qt) class guarded by a single lock: it is
written from the GUI thread (header, close markers) and from the pipeline
worker thread (activity lines, worker exceptions). Writes are small text
appends followed by ``flush()``; no buffering window exists in which a
crash would lose an acknowledged line.

A process-wide ``current()`` reference lets the installed
``sys.excepthook`` / ``threading.excepthook`` crash hooks (see
:func:`install_crash_hooks`) find the active log without any Qt plumbing.
The reference is set on run start and cleared when the run reaches its
terminal state.

The canonical log path is unchanged: ``cfg.logs_dir``
(``<Library-Root>/logs``), the same directory the CLI and the finalizer
use. The portable ``<base>/logs`` directory is for app diagnostics only.
"""

from __future__ import annotations

import platform
import sys
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .. import activity_log
from ..logging_utils import redact

__all__ = [
    "LiveRunLog",
    "current",
    "set_current",
    "install_crash_hooks",
    "new_run_id",
    "identity_lines",
]

# ---------------------------------------------------------------------------
# process-wide "active live log" reference (for the crash hooks)
# ---------------------------------------------------------------------------
_current: Optional["LiveRunLog"] = None
_current_lock = threading.Lock()


def current() -> Optional["LiveRunLog"]:
    """Return the active live run log, or ``None`` when no run is live."""
    with _current_lock:
        return _current


def set_current(logger: Optional["LiveRunLog"]) -> None:
    """Set (or clear) the active live run log. Thread-safe."""
    global _current
    with _current_lock:
        _current = logger


def new_run_id() -> str:
    """Return the next canonical run id.

    Reuses the pipeline's own generator (UTC second + pid + per-process
    monotonic counter) so the GUI-assigned id is byte-compatible with the
    one ``run_pipeline`` would have generated -- and, when pre-assigned to
    ``RunConfig.run_id``, is the id the pipeline actually uses.
    """
    from ..pipeline import _run_id  # lazy: keeps GUI import light

    return _run_id()


def identity_lines() -> list[str]:
    """App version / build-identity lines for the run-start header."""
    from . import __version__ as gui_version

    frozen = bool(getattr(sys, "frozen", False))
    return [
        f"app_version    : {gui_version}",
        f"build_identity : {'pyinstaller-frozen' if frozen else 'python-source'}",
        f"python         : {platform.python_version()}",
        f"platform       : {platform.platform()}",
        f"hostname       : {platform.node()}",
    ]


class LiveRunLog:
    """Append-only, per-run, flush-per-line diagnostic log file.

    ``path`` is the reserved log file (named from the run id with the SAME
    component-sanitization the finalizer's ``write_run_log`` uses, so the
    finalizer appends to exactly this file).
    """

    def __init__(self, path: Path, started_at: str) -> None:
        self.path = path
        self.started_at = started_at
        self._fh = path.open("a", encoding="utf-8")
        self._lock = threading.Lock()
        self._stage = "starting"
        self._last_line = ""

    # -- construction --------------------------------------------------------
    @classmethod
    def start(
        cls,
        *,
        logs_dir,
        run_id: str,
        header_lines: list[str],
    ) -> "LiveRunLog":
        """Open the run log under ``logs_dir`` and write the run-start header.

        The file is created (with the run-start banner + header) and flushed
        before this returns. Raises ``OSError`` when the directory cannot be
        created -- the caller treats that as "no live log" and the run
        proceeds (logging must never break a run).
        """
        from ..logging_utils import _log_path_for

        logs_dir = Path(logs_dir)
        logs_dir.mkdir(parents=True, exist_ok=True)
        path = _log_path_for(logs_dir, run_id)
        log = cls(path, datetime.now(timezone.utc).isoformat())
        with log._lock:
            log._write_block(
                [
                    "=" * 72,
                    "Amiga ADF Library Builder - live run log (GUI)",
                    "=" * 72,
                    f"run_id         : {run_id}",
                    *header_lines,
                    "",
                    "=== RUN START ===",
                    "",
                ]
            )
        return log

    # -- live writes ---------------------------------------------------------
    def set_stage(self, stage: str) -> None:
        """Record the current pipeline stage (used in crash records)."""
        with self._lock:
            self._stage = stage

    def log(self, line: str) -> None:
        """Append one redacted, timestamped progress line and flush it."""
        text = activity_log.run_activity_line(redact(str(line)))
        with self._lock:
            self._last_line = text
            self._write_block([text])

    def log_exception(self, exc: BaseException, stage: Optional[str] = None) -> None:
        """Append a full exception capture and flush it.

        Records exception type, message, traceback, the current stage, and
        the last progress line (the current release/file when known).
        """
        with self._lock:
            now = datetime.now(timezone.utc).isoformat()
            tb = "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            ).rstrip()
            self._write_block(
                [
                    "",
                    "=" * 72,
                    "EXCEPTION CAPTURED",
                    "=" * 72,
                    f"time          : {now}",
                    f"stage         : {stage or self._stage}",
                    f"last_progress : {self._last_line or '(none)'}",
                    f"exception     : {type(exc).__name__}: {exc}",
                ]
                + tb.splitlines()
                + ["END EXCEPTION", "=" * 72, ""]
            )

    def close(self) -> None:
        """Close the file. Late writes (crash hooks) re-open in append mode."""
        with self._lock:
            self._close_fh()

    # -- internals (caller holds the lock) -----------------------------------
    def _write_block(self, lines: list[str]) -> None:
        if self._fh is None:
            # Self-heal after close(): a late crash record must still land
            # on disk in the SAME file (append, never replace).
            self._fh = self.path.open("a", encoding="utf-8")
        self._fh.write("\n".join(lines) + "\n")
        self._fh.flush()

    def _close_fh(self) -> None:
        if self._fh is not None:
            try:
                self._fh.flush()
            except Exception:  # pragma: no cover - defensive
                pass
            try:
                self._fh.close()
            except Exception:  # pragma: no cover - defensive
                pass
            self._fh = None


# ---------------------------------------------------------------------------
# crash hooks: uncaught exceptions -> live run log
# ---------------------------------------------------------------------------
def _record_to_live_log(exc: Optional[BaseException]) -> None:
    if exc is None:
        return
    logger = current()
    if logger is None:
        return
    try:
        logger.log_exception(exc)
    except Exception:  # a logging failure must never mask the original crash
        pass


def install_crash_hooks() -> None:
    """Route uncaught exceptions into the active live run log.

    * ``sys.excepthook`` catches uncaught exceptions on the main thread AND
      on Qt worker threads (PySide delivers slot exceptions to it) -- the
      case where a run dies and the process would otherwise exit with no
      log at all.
    * ``threading.excepthook`` catches uncaught exceptions on plain
      background threads (e.g. the pipeline's parallel enrichment pool).

    Both hooks append the capture to the active live log (when a run is
    live) and then chain to the previously installed hook, so console
    builds keep their normal traceback output. Idempotent: a second call
    is a no-op.
    """
    if getattr(sys, "_live_run_log_hooks_installed", False):
        return

    base_excepthook = sys.excepthook

    def _excepthook(exc_type, exc_value, exc_tb) -> None:  # noqa: ANN001
        _record_to_live_log(exc_value)
        base_excepthook(exc_type, exc_value, exc_tb)

    sys.excepthook = _excepthook

    base_thread_hook = threading.excepthook

    def _thread_hook(args) -> None:  # noqa: ANN001
        _record_to_live_log(args.exc_value)
        base_thread_hook(args)

    threading.excepthook = _thread_hook
    sys._live_run_log_hooks_installed = True  # type: ignore[attr-defined]
