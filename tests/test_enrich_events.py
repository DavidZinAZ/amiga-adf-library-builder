"""Tests for structured per-group enrichment diagnostics (structured logging).

`enrich_group` must emit typed `EnrichEvent` records covering the metadata and
artwork outcomes the operator needs to diagnose: cache hit/miss/refresh/negative,
metadata-not-found, artwork URL-not-found / download failure / invalid image /
resize failure / successful generation, and quarantine/review routing. The per-run
log renderer must surface these as a structured block with URLs redacted.
"""
from __future__ import annotations

from pathlib import Path

from amiga_adf_library_builder.enrich import EnrichCategory, EnrichEvent, enrich_group
from amiga_adf_library_builder.grouper import group_records
from amiga_adf_library_builder.logging_utils import write_run_log, redact
from amiga_adf_library_builder.models import ScanRecord
from amiga_adf_library_builder.parser import parse_filename
from amiga_adf_library_builder.paths import PathConfig, resolve_config


def _ufo_group():
    recs = [parse_filename(f"Example - Space Tactics (Disk {n} of 4).adf") for n in range(1, 5)]
    return group_records(recs)[0]


def _ufo_scans(group, tmp_path):
    return {
        r.source_filename: ScanRecord(
            path=tmp_path / r.source_filename,
            filename=r.source_filename,
            size=901120,
            sha256="abc",
            scanned_at="t",
        )
        for r in group.records
    }


def _cfg(root: Path) -> PathConfig:
    return resolve_config(library_root=str(root))[0]


def _categories(events) -> set[str]:
    return {e["category"] for e in events}


def test_offline_emits_cache_miss_and_metadata_not_found_and_artwork_skipped(tmp_path: Path) -> None:
    g = _ufo_group()
    scans = _ufo_scans(g, tmp_path)
    res = enrich_group(
        g,
        nfo_dir=tmp_path / "nfo",
        scans=scans,
        artwork_original_dir=tmp_path / "art",
        artwork_processed_dir=tmp_path / "proc",
    )
    cats = _categories(e.to_dict() for e in res.events)
    assert EnrichCategory.CACHE_MISS.value in cats
    assert EnrichCategory.METADATA_NOT_FOUND.value in cats
    assert EnrichCategory.ARTWORK_SKIPPED.value in cats
    # No network was attempted, so no artwork-download failure should appear.
    assert EnrichCategory.ARTWORK_DOWNLOAD_FAILED.value not in cats


def test_enrich_result_carries_events_field(tmp_path: Path) -> None:
    g = _ufo_group()
    scans = _ufo_scans(g, tmp_path)
    res = enrich_group(
        g,
        nfo_dir=tmp_path / "nfo",
        scans=scans,
        artwork_original_dir=tmp_path / "art",
        artwork_processed_dir=tmp_path / "proc",
    )
    assert isinstance(res.events, list)
    assert all(isinstance(e, EnrichEvent) for e in res.events)
    # Events must round-trip to dicts with the documented keys.
    for d in res.events:
        dct = d.to_dict()
        assert set(dct) == {"category", "detail", "url", "cache", "ok", "error"}


def test_redact_masks_artwork_url_in_event_rendering(tmp_path: Path) -> None:
    # A metadata-driven online run that records a (synthetic) artwork URL must
    # have the URL redacted in the rendered log, including query params.
    cfg = _cfg(tmp_path)
    log_path = write_run_log(
        logs_dir=cfg.logs_dir,
        run_id="run-redact",
        config_label="defaults",
        cfg=cfg,
        argv=["build", "--online"],
        command="build",
        result={
            "run_id": "run-redact",
            "online": True,
            "files_scanned": 0,
            "records_parsed": 0,
            "groups": 1,
            "catalog_new_scan": 0,
            "catalog_new_parse": 0,
            "nfo_written": [],
            "artwork_resized": [],
            "artwork_missing": [],
            "enrichment_notes": [],
            "review_routed": [],
            "unknown_routed": [],
            "applied_approvals": [],
            "unmatched_approvals": [],
            "hash_failures": [],
            "export_gate_open": False,
            "export_gate_reason": "blocked",
            "original_preserved": True,
            "original_problems": [],
            "per_group": [
                {
                    "release_key": "r1",
                    "title": "Demo",
                    "quarantine_reason": None,
                    "provider": "curated",
                    "artwork_missing": True,
                    "notes": [],
                    "events": [
                        {
                            "category": "artwork_download_failed",
                            "detail": "download raised an error",
                            "url": "https://img.example.com/cover.png?token=SECRET123&id=42",
                            "cache": None,
                            "ok": False,
                            "error": "HTTP 403 from https://img.example.com/cover.png?token=SECRET123",
                        }
                    ],
                }
            ],
        },
        started_at="2026-01-01T00:00:00+00:00",
        return_code=0,
    )
    assert log_path is not None
    text = log_path.read_text(encoding="utf-8")
    assert "SECRET123" not in text
    assert "token=REDACTED" in text
    # The error line is also redacted.
    assert "HTTP 403 from" in text
    assert "artwork_download_failed" in text


def test_route_event_appears_in_pipeline_per_group(tmp_path: Path) -> None:
    # A special-only incomplete set is routed to unknown/ (quarantine). Verify the
    # route_quarantine event is surfaced in pipeline result per_group.
    from amiga_adf_library_builder import pipeline

    data_root = tmp_path / "data"
    orig = data_root / "original"
    orig.mkdir(parents=True)
    # Two special disks, no main disk -> incomplete special-only set.
    for n in ("Example_Quest_III_Boot.adf", "Example_Quest_III_Character.adf"):
        (orig / n).write_bytes(b"x" * 10)
    cfg = _cfg(data_root)
    from amiga_adf_library_builder.initializer import ensure_managed_directories

    ensure_managed_directories(cfg)
    result = pipeline.run_pipeline(cfg=cfg)
    assert result["per_group"], "expected at least one group"
    cats = set()
    for group in result["per_group"]:
        cats |= _categories(group["events"])
    assert EnrichCategory.ROUTE_QUARANTINE.value in cats


def test_invalid_image_master_emits_artwork_invalid_image(tmp_path: Path) -> None:
    # structured logging D1: a corrupt/unsupported artwork master that cannot be decoded
    # by Pillow must produce an ARTWORK_INVALID_IMAGE diagnostic, distinct from
    # ARTWORK_RESIZE_FAILED. (Previously no code path emitted ARTWORK_INVALID_IMAGE.)
    g = _ufo_group()
    scans = _ufo_scans(g, tmp_path)
    art_dir = tmp_path / "art"
    art_dir.mkdir()
    # find_artwork_master matches by normalized title substring; a corrupt PNG
    # sharing the release title stem is picked up as the master.
    (art_dir / "example-space-tactics-corrupt.png").write_bytes(b"not an image")

    res = enrich_group(
        g,
        nfo_dir=tmp_path / "nfo",
        scans=scans,
        artwork_original_dir=art_dir,
        artwork_processed_dir=tmp_path / "proc",
    )
    cats = _categories(e.to_dict() for e in res.events)
    assert EnrichCategory.ARTWORK_INVALID_IMAGE.value in cats
    # The corrupt master is NOT a resize/processing-cap failure.
    assert EnrichCategory.ARTWORK_RESIZE_FAILED.value not in cats
