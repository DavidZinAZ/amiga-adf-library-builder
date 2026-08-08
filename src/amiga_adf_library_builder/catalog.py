"""Catalog: persistent, reusable JSON-lines store for scan + parse + group results.

Design: JSON lines for
auditability and simple reuse. The same file is appendable per run and can be
re-read to prove idempotent reuse. No silent overwrites:
records are keyed by source filename + SHA-256; re-adding an identical record
is a no-op, while a changed hash is logged, not clobbered.

Layout under ``<data_root>/catalog``:
  scan.jsonl      one ScanRecord per line
  parse.jsonl     one ParsedRecord per line
  groups.jsonl    one ReleaseGroup summary per line
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .models import ParsedRecord, ReleaseGroup, ScanRecord


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(__import__("json").loads(line))
    return out


def write_scan_records(catalog_dir: Path, records: Iterable[ScanRecord]) -> int:
    """Append scan records. Returns number of NEW lines written."""
    _ensure_dir(catalog_dir)
    target = catalog_dir / "scan.jsonl"
    existing = {(d["filename"], d["sha256"]) for d in _read_jsonl(target)}
    written = 0
    with open(target, "a", encoding="utf-8") as fh:
        for rec in records:
            key = (rec.filename, rec.sha256)
            if key in existing:
                continue
            fh.write(__import__("json").dumps(rec.to_dict()) + "\n")
            existing.add(key)
            written += 1
    return written


def write_parse_records(catalog_dir: Path, records: Iterable[ParsedRecord]) -> int:
    _ensure_dir(catalog_dir)
    target = catalog_dir / "parse.jsonl"
    existing = {d["source_filename"] for d in _read_jsonl(target)}
    written = 0
    with open(target, "a", encoding="utf-8") as fh:
        for rec in records:
            if rec.source_filename in existing:
                continue
            fh.write(__import__("json").dumps(rec.to_dict()) + "\n")
            existing.add(rec.source_filename)
            written += 1
    return written


def write_groups(catalog_dir: Path, groups: Iterable[ReleaseGroup], run_id: str) -> int:
    _ensure_dir(catalog_dir)
    target = catalog_dir / "groups.jsonl"
    written = 0
    with open(target, "a", encoding="utf-8") as fh:
        for g in groups:
            entry = g.to_dict()
            entry["run_id"] = run_id
            entry["written_at"] = _now()
            fh.write(__import__("json").dumps(entry) + "\n")
            written += 1
    return written


def read_parse_records(catalog_dir: Path) -> list[ParsedRecord]:
    return [ParsedRecord.from_dict(d) for d in _read_jsonl(catalog_dir / "parse.jsonl")]


def read_groups(catalog_dir: Path) -> list[dict]:
    return _read_jsonl(catalog_dir / "groups.jsonl")


def catalog_exists(catalog_dir: Path) -> bool:
    return (catalog_dir / "scan.jsonl").exists()
