"""Tests for structured logging: configured logs_dir must receive a run log.

Covers: arbitrary logs_dir, log path with spaces, run-id with unsafe characters,
and that an unwritable logs_dir degrades gracefully (warning, no crash, run
continues).
"""
from __future__ import annotations

import os
import stat
from dataclasses import replace
from pathlib import Path

import pytest

from amiga_adf_library_builder import logging_utils
from amiga_adf_library_builder.paths import PathConfig, resolve_config


def _cfg(root: Path) -> PathConfig:
    return resolve_config(library_root=str(root))[0]


def _with_logs(cfg: PathConfig, logs: Path) -> PathConfig:
    """Return cfg with logs_dir overridden (frozen dataclass)."""
    return replace(cfg, logs_dir=logs)


def _sample_result(run_id: str = "run-1") -> dict:
    return {
        "run_id": run_id,
        "online": False,
        "files_scanned": 2,
        "records_parsed": 2,
        "groups": 1,
        "catalog_new_scan": 2,
        "catalog_new_parse": 2,
        "nfo_written": ["/x/a.nfo"],
        "artwork_resized": [],
        "artwork_missing": [],
        "enrichment_notes": ["offline NFO written"],
        "review_routed": [],
        "unknown_routed": [],
        "applied_approvals": [],
        "unmatched_approvals": [],
        "hash_failures": [],
        "export_gate_open": False,
        "export_gate_reason": "phase 5 blocked",
        "original_preserved": True,
        "original_problems": [],
        "per_group": [
            {
                "release_key": "ufo|enemy unknown",
                "title": "Example - Space Tactics",
                "quarantine_reason": None,
                "provider": None,
                "artwork_missing": True,
                "notes": ["artwork not found offline", "NFO written"],
                "events": [
                    {
                        "category": "cache_miss",
                        "detail": "offline and no cached metadata record present",
                        "url": None,
                        "cache": "miss",
                        "ok": True,
                        "error": None,
                    },
                    {
                        "category": "metadata_not_found",
                        "detail": "no metadata available offline",
                        "url": None,
                        "cache": "negative",
                        "ok": True,
                        "error": None,
                    },
                    {
                        "category": "artwork_skipped",
                        "detail": "no artwork master available; NFO only",
                        "url": None,
                        "cache": None,
                        "ok": True,
                        "error": None,
                    },
                ],
            }
        ],
    }


def test_redact_masks_secrets_and_tokens() -> None:
    s = "GET https://api.example.com/meta?token=SECRET123&id=42"
    out = logging_utils.redact(s)
    assert "SECRET123" not in out
    assert "token=REDACTED" in out
    assert "id=42" in out  # non-sensitive param preserved

    s2 = "Authorization: Bearer abc.def.ghi"
    out2 = logging_utils.redact(s2)
    assert "abc.def.ghi" not in out2
    assert "Authorization" in out2  # header name preserved, value redacted

    s3 = "curl --api_key=xyz789 https://x"
    out3 = logging_utils.redact(s3)
    assert "xyz789" not in out3
    assert "api_key=REDACTED" in out3


def test_safe_log_component_flattens_unsafe_chars() -> None:
    assert logging_utils._safe_log_component("a/b/../c") == "a_b_.._c"
    assert logging_utils._safe_log_component("  weird/../name  ") == "weird_.._name"
    assert logging_utils._safe_log_component("") == "run"
    assert "/" not in logging_utils._safe_log_component("../../etc/passwd")


def test_write_run_log_creates_file_in_logs_dir(tmp_path: Path) -> None:
    logs = tmp_path / "my logs"  # path with a space
    cfg = _with_logs(_cfg(tmp_path), logs)
    path = logging_utils.write_run_log(
        logs_dir=cfg.logs_dir,
        run_id="20260101T000000Z-123-00001",
        config_label="defaults",
        cfg=cfg,
        argv=["build", "--online"],
        command="build",
        result=_sample_result(),
        started_at="2026-01-01T00:00:00+00:00",
        return_code=0,
    )
    assert path is not None
    assert path.parent == logs
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "run_id" in text
    assert "Example - Space Tactics" in text
    assert "artwork not found offline" in text  # per-group note captured
    assert "logs_dir" in text
    # Structured per-group diagnostics (structured logging) must be present and redacted.
    assert "[OK ] cache_miss cache=miss" in text
    assert "[OK ] metadata_not_found cache=negative" in text
    assert "[OK ] artwork_skipped" in text


def test_write_run_log_unwritable_dir_returns_none_no_raise(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    # Make directory read-only (no write) to simulate an unwritable logs_dir.
    os.chmod(logs, stat.S_IRUSR | stat.S_IXUSR)
    try:
        cfg = _with_logs(_cfg(tmp_path), logs)
        # Should not raise; should return None and emit a stderr warning.
        path = logging_utils.write_run_log(
            logs_dir=cfg.logs_dir,
            run_id="run-x",
            config_label="defaults",
            cfg=cfg,
            argv=None,
            command="build",
            result=_sample_result(),
            started_at="",
            return_code=0,
        )
        assert path is None
        # The directory still contains nothing written by us.
        assert list(logs.iterdir()) == []
    finally:
        os.chmod(logs, stat.S_IRWXU)  # restore for tmp cleanup


def test_pipeline_run_creates_log_under_configured_logs_dir(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    (data_root / "original").mkdir(parents=True)
    for n in range(1, 3):
        (data_root / f"Example - Space Tactics (Disk {n} of 4).adf").write_bytes(b"x" * 10)
    cfg = _cfg(data_root)
    logs_dir = cfg.logs_dir
    from amiga_adf_library_builder.initializer import ensure_managed_directories

    ensure_managed_directories(cfg)
    assert logs_dir.is_dir()
    from amiga_adf_library_builder import cli

    rc = cli.main(argv=["build", "--library-root", str(data_root)])
    assert rc == 0
    logs = sorted(logs_dir.glob("*.log"))
    assert logs, f"expected a run log under {logs_dir}"
    text = logs[0].read_text(encoding="utf-8")
    assert "Amiga ADF Library Builder — run log" in text
    assert "files_scanned" in text
    assert "per_group" not in text  # per-group section rendered inside the body


def test_redact_masks_key_and_pwd_query_params() -> None:
    # structured logging D2: bare 'key'/'pwd' query params carry secrets and leaked
    # before they were added to SENSITIVE_TOKENS. The value must be masked
    # and the following non-sensitive param preserved.
    out = logging_utils.redact("https://host/api/cover?key=SECRET&id=9")
    assert out == "https://host/api/cover?key=REDACTED&id=9", out

    out = logging_utils.redact("hunter2pwd"[:0] + "?pwd=hunter2&user=bob")
    assert out == "?pwd=REDACTED&user=bob", out

    # A genuine invalid-image repro string from a real run must never leak.
    out = logging_utils.redact("cannot identify image file '/x/cover.jpg'?key=SECRET")
    assert "SECRET" not in out
    assert "key=REDACTED" in out
