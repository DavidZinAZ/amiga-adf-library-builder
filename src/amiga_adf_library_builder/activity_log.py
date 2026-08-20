"""Shared run-activity helpers for the GUI Diagnostics view (issue #21).

The GUI's Diagnostics tab is a live processing/activity log. This module
holds the small pieces shared by the GUI (``gui.main_window``) and the core
pipeline (``pipeline`` / ``enrich``):

  * :func:`format_activity_timestamp` -- one timestamp format for every
    Diagnostics entry (local time, ``HH:MM:SS``);
  * :func:`run_activity_line` -- a ready-to-append Diagnostics line
    (``HH:MM:SS  text``);
  * :func:`render_run_summary` -- the end-of-run result summary lines
    (success / failure / skipped export, counts, output destination(s));
  * :func:`describe_activity_events` -- plain-language descriptions of the
    structured :class:`~amiga_adf_library_builder.enrich.EnrichEvent`
    records the core already collects, so the GUI shows what happened during
    online provider work (lookups, cache hits/misses, artwork downloads and
    failures) instead of only repeating stage names.

Design rules (same as the rest of the codebase):
  * UI copy is plain language -- no jargon (no "gate", "preflight",
    "staging", "vault" in user-visible strings).
  * Every text that leaves this module is run through
    :func:`amiga_adf_library_builder.logging_utils.redact` so a provider URL
    or error message can never carry a secret value into the log view.
  * Rendering never raises: an unknown/odd event degrades to a generic line,
    never a crash.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from .logging_utils import redact


def format_activity_timestamp(when: Optional[datetime] = None) -> str:
    """Timestamp prefix for one Diagnostics entry.

    Local time (the time the operator sees on their own clock),
    ``HH:MM:SS``. ``None`` means "now".
    """
    dt = when or datetime.now()
    return dt.strftime("%H:%M:%S")


def run_activity_line(text: str, when: Optional[datetime] = None) -> str:
    """One timestamped, redacted Diagnostics line.

    The format is ``HH:MM:SS  text`` (two spaces) so every entry in the
    view is scannable and machine-checkable.
    """
    return f"{format_activity_timestamp(when)}  {redact(str(text))}"


def _fmt_count(value: Any) -> str:
    """Format a count value for the summary.

    Handles ints, lists, tuples, and None. For collections, returns the length.
    """
    if value is None:
        return "?"
    if isinstance(value, (list, tuple)):
        return str(len(value))
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return "?"


def render_run_summary(
    result: Optional[dict],
    *,
    run_mode: str = "build",
    cancelled: bool = False,
) -> list[str]:
    """Render the end-of-run result summary as plain-language lines.

    The summary is what the operator needs to know at a glance after a run:

      * the outcome -- success, failure, or cancelled;
      * whether the export happened, and why not (not requested, or the
        app's export safety check stopped it);
      * the counts -- files scanned, releases prepared, NFO files written,
        artwork processed, and (for an export) files written / folders
        created / files skipped;
      * the output destination path(s) -- the export destination and the
        per-run scratch area.

    ``result`` is the dict :func:`pipeline.run_pipeline` returns (or ``None``
    when the run failed before producing one). The function never raises: a
    missing or odd field degrades to a readable value.
    """
    lines: list[str] = []
    result = result or {}
    export = result.get("export") if isinstance(result.get("export"), dict) else None
    gate_open = bool(result.get("export_gate_open", False))
    gate_reason = result.get("export_gate_reason")

    # --- outcome --------------------------------------------------------------
    if cancelled:
        lines.append("Result: cancelled by the operator.")
    elif export is not None and (export.get("errors") or export.get("conflicts")):
        n_err = len(export.get("errors") or [])
        n_conf = len(export.get("conflicts") or [])
        lines.append(
            f"Result: finished with problems "
            f"({n_err} export error(s), {n_conf} conflict(s) -- see details below)."
        )
    else:
        lines.append("Result: success.")

    # --- what the run produced -------------------------------------------------
    lines.append(
        f"Files scanned: {_fmt_count(result.get('files_scanned'))}; "
        f"releases prepared: {_fmt_count(result.get('groups'))}."
    )
    nfo_written = result.get("nfo_written")
    n_nfo = len(nfo_written) if isinstance(nfo_written, (list, tuple)) else "?"
    artwork_resized = result.get("artwork_resized")
    n_art = len(artwork_resized) if isinstance(artwork_resized, (list, tuple)) else "?"
    lines.append(f"NFO files written: {n_nfo}; artwork processed: {n_art}.")

    review = len(result.get("review_routed") or [])
    unknown = len(result.get("unknown_routed") or [])
    lines.append(
        f"Sent to review: {review}; set aside as unrecognized: {unknown}."
    )

    # --- export outcome (success / failure / skipped) --------------------------
    if cancelled:
        lines.append("Export: not run (the run was cancelled).")
    elif run_mode != "export":
        lines.append("Export: not requested this run (build only).")
    elif export is None:
        if gate_open:
            lines.append("Export: did not run (the pipeline stopped before export).")
        else:
            why = f" -- {gate_reason}" if gate_reason else ""
            lines.append(
                "Export: skipped. The app's export safety check is not "
                f"clear{why}."
            )
    elif export.get("errors") and export.get("releases_exported", 0) == 0:
        # Gate closed inside export_all: export ran but produced nothing.
        why = f" -- {export['errors'][0]}" if export.get("errors") else ""
        lines.append(
            "Export: skipped. The app's export safety check is not "
            f"clear{why}."
        )
    else:
        lines.append(
            "Export: completed. "
            f"{_fmt_count(export.get('releases_exported'))} release(s) exported, "
            f"{_fmt_count(export.get('folders_written'))} folder(s) created, "
            f"{_fmt_count(export.get('files_written'))} file(s) written, "
            f"{_fmt_count(export.get('files_unchanged'))} already up to date, "
            f"{_fmt_count(export.get('skipped_quarantined'))} skipped."
        )
        errors = export.get("errors") or []
        if errors:
            lines.append(f"Export problems: {len(errors)} -- see the details above.")

    # --- where the output went ---------------------------------------------------
    if export is not None and export.get("staging_root"):
        lines.append(f"Per-run scratch area: {redact(str(export['staging_root']))}")
    if not cancelled:
        lines.append(
            "See the full log file for per-release details "
            "(the log files button under Run opens the log folder)."
        )
    return lines
