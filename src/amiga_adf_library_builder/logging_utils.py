"""Per-run diagnostic logging to the configured ``logs_dir`` (structured logging).

The builder resolves and creates a ``logs_dir`` but, prior to this module, never
wrote anything there: ``pipeline.run_pipeline`` returned only an in-memory aggregate
``result`` and the CLI printed it to stdout. This module makes a normal scan/build/
export run leave a durable, per-run log (tied to ``run_id``) without shell redirection.

Design rules:
  * Writing the log MUST NEVER break a run. Any failure (unwritable dir, formatting
    bug) is caught; a stderr warning is emitted and the run continues. The previous
    "console-only" behavior is the safe fallback.
  * Secrets, tokens, and sensitive query parameters are redacted before anything is
    written (structured logging + security review).
  * The run-id is sanitized into a single safe path component so a malicious or odd
    run-id cannot escape ``logs_dir``.
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .paths import PathConfig

# Query-parameter / header name fragments whose VALUES must be redacted.
SENSITIVE_TOKENS = (
    "token",
    "api_key",
    "apikey",
    "access_token",
    "accesskey",
    "secret",
    "client_secret",
    "private_key",
    "signature",
    "sig",
    "password",
    "passwd",
    "pwd",
    "key",
    "auth",
)

# key=value where the key carries a sensitive fragment (URLs and shell lines).
# The value stops at whitespace or '&' (query-string separator) so a redacted
# param does not absorb a following non-sensitive param (e.g. ?token=X&id=42).
_SENSITIVE_KV_RE = re.compile(
    r"(?P<key>[A-Za-z0-9_.\\-]*?(?:"
    + "|".join(re.escape(t) for t in SENSITIVE_TOKENS)
    + r")[A-Za-z0-9_.\\-]*?)\s*=\s*(?P<val>[^\s&]+)",
    re.IGNORECASE,
)
# Authorization / Bearer token headers. Keep the keyword(s) but redact only the
# credential that follows: "Bearer <tok>", "Authorization: <tok>", or
# "Authorization: Bearer <tok>".
_AUTH_RE = re.compile(
    r"(?P<pre>(?:authorization\s*[:=]\s*)?bearer\b\s*(?:token\s*)?)(?P<tok>\S+)",
    re.IGNORECASE,
)

# Characters that are never safe inside a single log filename component.
_UNSAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._\-]")


def redact(text: str) -> str:
    """Return ``text`` with secret/token/sensitive-param VALUES replaced by REDACTED.

    Applies to URLs (``?token=SECRET``) and shell/argv lines (``--key=ABC``) and to
    ``Authorization`` / ``Bearer`` headers. Only the VALUE is redacted; the parameter
    or header name is preserved so logs stay debuggable.
    """
    if not text:
        return text
    out = _SENSITIVE_KV_RE.sub(lambda m: f"{m.group('key')}=REDACTED", text)
    out = _AUTH_RE.sub(lambda m: f"{m.group('pre')}REDACTED", out)
    return out


def _safe_log_component(run_id: str) -> str:
    """Reduce ``run_id`` to a single safe filename component.

    Unlike the exporter's staging sanitizer (which rejects traversal-bearing ids),
    this is best-effort: separators and traversal segments are flattened to ``_`` so a
    log can always be written, while never producing a path that escapes ``logs_dir``.
    """
    s = _UNSAFE_FILENAME_RE.sub("_", str(run_id))
    s = s.strip("._-")
    return s or "run"


def _log_path_for(logs_dir: Path, run_id: str) -> Path:
    """Resolve a unique, safe ``.log`` path beneath ``logs_dir`` for ``run_id``."""
    base = _safe_log_component(run_id)
    log_path = logs_dir / f"{base}.log"
    # Avoid overwriting an existing log for the same run-id; append a counter.
    if log_path.exists():
        i = 1
        while True:
            cand = logs_dir / f"{base}.{i}.log"
            if not cand.exists():
                log_path = cand
                break
            i += 1
    return log_path


def write_run_log(
    *,
    logs_dir,
    run_id: str,
    config_label: str,
    cfg: PathConfig,
    argv: Optional[list],
    command: Optional[str],
    result: dict,
    started_at: str,
    return_code: int = 0,
) -> Optional[Path]:
    """Write a non-empty per-run diagnostic log under ``logs_dir``.

    Returns the written log path on success, or ``None`` if the log could not be
    written (e.g. an unwritable ``logs_dir``). Never raises.
    """
    try:
        logs_dir = Path(logs_dir)
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = _log_path_for(logs_dir, run_id)
        text = _render(
            log_path=log_path,
            run_id=run_id,
            config_label=config_label,
            cfg=cfg,
            argv=argv,
            command=command,
            result=result,
            started_at=started_at,
            return_code=return_code,
        )
        log_path.write_text(text, encoding="utf-8")
        return log_path
    except OSError as exc:
        print(
            f"warning: could not write run log to {logs_dir}: {exc}",
            file=sys.stderr,
        )
        return None
    except Exception as exc:  # pragma: no cover - defensive; logging must not break runs
        print(f"warning: run log generation failed: {exc}", file=sys.stderr)
        return None


def _fmt_list(values) -> str:
    if not values:
        return "(none)"
    return "\n".join(f"    - {redact(str(v))}" for v in values)


def _render(
    *,
    log_path: Path,
    run_id: str,
    config_label: str,
    cfg: PathConfig,
    argv: Optional[list],
    command: Optional[str],
    result: dict,
    started_at: str,
    return_code: int,
) -> str:
    finished_at = datetime.now(timezone.utc).isoformat()
    argv_str = redact(" ".join(str(a) for a in argv)) if argv else "(not captured)"

    lines = []
    lines.append("=" * 72)
    lines.append("Amiga ADF Library Builder — run log")
    lines.append("=" * 72)
    lines.append(f"run_id         : {run_id}")
    lines.append(f"command        : {command or '(unknown)'}")
    lines.append(f"effective_opts : {argv_str}")
    lines.append(f"config_source  : {config_label}")
    lines.append(f"started_at     : {started_at}")
    lines.append(f"logs_dir       : {cfg.logs_dir}")
    lines.append("")

    lines.append("--- Resolved configuration paths ---")
    for name in (
        "library_root",
        "original_dir",
        "staging_dir",
        "output_dir",
        "quarantine_dir",
        "approvals_dir",
        "reports_dir",
        "cache_dir",
    ):
        lines.append(f"{name:15}: {getattr(cfg, name)}")
    lines.append("")

    per_group = result.get("per_group") or []
    lines.append(f"--- Per-group diagnostics ({len(per_group)} groups) ---")
    if not per_group:
        lines.append("  (no groups)")
    for i, g in enumerate(per_group, 1):
        title = g.get("title") or "(unknown title)"
        lines.append(f"[group {i}] {g.get('release_key')} — {title}")
        qr = g.get("quarantine_reason")
        lines.append(f"  quarantine/review : {qr if qr else '(none)'}")
        lines.append(f"  metadata provider : {g.get('provider') or '(offline)'}")
        lines.append(f"  artwork_missing   : {bool(g.get('artwork_missing'))}")

        # Structured per-group diagnostics (structured logging): each enrichment outcome is
        # recorded as a typed event so metadata/artwork failures are diagnosable
        # by category rather than buried in prose. URLs and details are redacted.
        events = g.get("events") or []
        lines.append(f"  diagnostics ({len(events)}):")
        if events:
            for ev in events:
                category = ev.get("category") or "(unknown)"
                ok = ev.get("ok", True)
                status = "OK " if ok else "ERR"
                cache = ev.get("cache")
                cache_part = f" cache={cache}" if cache else ""
                detail = redact(str(ev.get("detail") or ""))
                url = ev.get("url")
                url_part = f" url={redact(str(url))}" if url else ""
                err = ev.get("error")
                err_part = f" error={redact(str(err))}" if err else ""
                lines.append(
                    f"    - [{status}] {category}{cache_part}{url_part}: {detail}{err_part}"
                )
        else:
            lines.append("    - (none)")

        notes = g.get("notes") or []
        lines.append(f"  notes ({len(notes)}):")
        if notes:
            for n in notes:
                lines.append(f"    - {redact(str(n))}")
        else:
            lines.append("    - (none)")
        lines.append("")

    export = result.get("export")
    lines.append("--- Export / publication summary ---")
    if export:
        for k in (
            "releases_exported",
            "folders_written",
            "files_written",
            "files_unchanged",
            "conflicts",
            "skipped_quarantined",
            "errors",
            "staging_root",
        ):
            if k in export:
                lines.append(f"  {k:18}: {export[k]}")
    else:
        lines.append("  (export not requested)")
    lines.append("")

    lines.append("--- Aggregate result ---")
    for k in (
        "files_scanned",
        "records_parsed",
        "groups",
        "catalog_new_scan",
        "catalog_new_parse",
        "nfo_written",
        "artwork_resized",
        "artwork_missing",
        "review_routed",
        "unknown_routed",
        "applied_approvals",
        "unmatched_approvals",
        "hash_failures",
        "export_gate_open",
        "export_gate_reason",
        "original_preserved",
        "online",
    ):
        if k in result:
            val = result[k]
            if k == "nfo_written" and isinstance(val, list):
                val = len(val)
            if k == "artwork_resized" and isinstance(val, list):
                val = len(val)
            if k == "artwork_missing" and isinstance(val, list):
                val = len(val)
            if k == "original_problems" and isinstance(val, list):
                val = f"{len(val)} problem(s)"
            lines.append(f"  {k:18}: {val}")
    lines.append("")

    lines.append("--- Run finished ---")
    lines.append(f"finished_at    : {finished_at}")
    lines.append(f"return_code    : {return_code}")
    lines.append(f"log_path       : {log_path}")
    lines.append("=" * 72)
    return "\n".join(lines) + "\n"
