"""Pipeline: orchestrate scan -> parse -> group -> enrich -> quarantine.

Phase 5 (Gotek export) is intentionally NOT invoked here; it is hard-gated by
``exporter_guard.export_gate_open`` and requires an explicit operator safety signal.

All writes target managed data directories (catalog, assets, review, unknown,
work). ``original/`` is read-only throughout.
"""
from __future__ import annotations

import itertools
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from . import artwork as artwork_mod
from . import catalog, enrich, exporter, grouper, quarantine, scanner
from .enrich import VERIFIED_ARTWORK_WIDTH, VERIFIED_ARTWORK_HEIGHT
from .exporter_guard import export_gate_open
from .logging_utils import redact
from .models import ParsedRecord, ReleaseGroup, ScanRecord
from .parser import parse_filename
from .naming import release_basename
from .paths import PathConfig

# Monotonic, process-global counter that guarantees a unique run identifier even
# when two operations start within the same wall-clock second. A bare
# second-granularity timestamp used to let a later run reuse / overwrite the
# staging directory of an earlier run that shared the same timestamp, which
# corrupted both isolation guarantees and overwrite-conflict detection.
_run_id_counter = itertools.count()


def _run_id() -> str:
    """Return a unique, deterministic-prefix run identifier.

    The leading component is still the UTC second (so run ids stay human
    readable and time-ordered), but a per-process monotonic counter and the
    process id make the value unique regardless of how many operations start
    inside the same second. Run ids are never reused within a process.
    """
    seq = next(_run_id_counter)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{os.getpid()}-{seq:05d}"


def run_pipeline(
    *,
    cfg: PathConfig,
    online: bool = False,
    refresh_metadata: bool = False,
    require_artwork: bool = False,
    # (GH-24) Independent selection of the two optional metadata types. Both
    # default ON so existing callers (CLI, tests) keep the current behaviour.
    # include_artwork gates BOTH local lookup and online acquisition of cover
    # artwork; include_manuals_rtfm gates the deterministic RTFM build. The
    # require_artwork export-stop gate above is a DISTINCT concept and is
    # unaffected by either of these.
    include_artwork: bool = True,
    include_manuals_rtfm: bool = True,
    upstream_task_closed: bool = False,
    run_id: Optional[str] = None,
    export: bool = False,
    verify_only: bool = False,
    verified_artwork_width: Optional[int] = VERIFIED_ARTWORK_WIDTH,
    verified_artwork_height: Optional[int] = VERIFIED_ARTWORK_HEIGHT,
    local_media_config_path: Optional[str] = None,
    rtfm_config_path: Optional[str] = None,
    playmatch_config_path: Optional[str] = None,
    hasheous_config_path: Optional[str] = None,
    activity: Optional[Callable[[str], None]] = None,
) -> dict:
    """Execute phases 2-4, 5 (optional), and 6. Returns a result summary dict.

    All filesystem locations come from ``cfg`` (:class:`PathConfig`). The
    original corpus (``cfg.original_dir``) is read-only throughout.

    ``activity`` (issue #21): optional live-log hook. When given, the pipeline
    reports each major milestone (scan, grouping, enrichment, export) as one
    plain-language line. The hook is optional and safe: a missing or failing
    callback never changes pipeline behavior. CLI callers omit it, so CLI
    output is byte-identical to before.
    """
    def _act(msg: str) -> None:
        if activity is None:
            return
        try:
            activity(redact(str(msg)))
        except Exception:  # logging must never break the pipeline
            pass
    library_root = cfg.library_root
    original_dir = cfg.original_dir
    catalog_dir = cfg.catalog_dir
    nfo_dir = cfg.nfo_dir
    artwork_original_dir = cfg.artwork_original_dir
    artwork_processed_dir = cfg.artwork_processed_dir
    metadata_cache_dir = cfg.metadata_cache_dir
    curated_metadata_dir = cfg.curated_metadata_dir
    review_dir = cfg.review_dir
    unknown_dir = cfg.quarantine_dir

    run_id = run_id or _run_id()

    # Phase 2: scan (read-only) + parse.
    _act(f"Scanning {redact(str(original_dir))} for .adf files…")
    scans = scanner.scan_intake(original_dir)
    scan_map = {s.filename: s for s in scans}
    records: list[ParsedRecord] = [parse_filename(s.filename) for s in scans]
    _act(f"Found {len(scans)} .adf file(s); {len(records)} record(s) parsed.")

    # Phase 3: group.
    _act("Grouping files into releases…")
    groups: list[ReleaseGroup] = grouper.group_records(records)
    _act(f"Prepared {len(groups)} release(s).")

    # manual-approval feature: apply operator manual approvals BEFORE enrich + quarantine so an
    # approved special-only set is retitled, de-quarantined, and routed for
    # export rather than written to unknown/. No-op when no config is present.
    from . import manual_approvals

    approvals = manual_approvals.load_approvals(cfg.library_root)
    apply_result = manual_approvals.apply_approvals(
        groups, approvals, original_dir=original_dir
    )
    groups = apply_result[0]
    _applied = apply_result[1]
    _unmatched = apply_result[2]
    _hash_failures = apply_result.hash_failures

    # Persist catalogue (reusable across runs; documented behavior).
    n_scan = catalog.write_scan_records(catalog_dir, scans)
    n_parse = catalog.write_parse_records(catalog_dir, records)
    catalog.write_groups(catalog_dir, groups, run_id)

    # Phase 4: enrich (offline NFO; artwork guarded).
    # Build the local-media provider (local-media provider base app) when configured. It is
    # read-only against any source library and copies selected sources into the
    # app's own cache; a disabled/absent config yields provider=None so the
    # existing offline + online artwork paths are unchanged. A provider failure
    # must never break the run -- we degrade to the standard enrich path.
    local_media_provider = None
    mobygames_enabled = False
    mobygames_api_key_env = "MOBYGAMES_API_KEY"
    if local_media_config_path:
        from . import local_media as lm

        try:
            lm_cfg = lm.load_local_media_config(local_media_config_path)
            if lm_cfg.enabled:
                lm.assert_read_only_roots(lm_cfg)
                # Issue #33 diagnostics: report the configured LaunchBox local
                # media roots that were scanned and any missing/inaccessible
                # ones. Missing roots are RETAINED in config (never deleted) and
                # surface here as diagnostics. Read-only, no network.
                lm_report = lm.scan_launchbox_roots(lm_cfg)
                for r in lm_report.missing_roots:
                    _act(
                        f"LaunchBox root not found (kept in config): {r.path}"
                        + (f" [{r.asset_type}]" if r.asset_type else "")
                    )
                _act(
                    f"Scanned {len(lm_report.roots)} LaunchBox local media "
                    f"root(s): {lm_report.total_image_candidates} image "
                    f"candidate(s), {lm_report.total_manual_files} manual "
                    f"file(s) (PDF/TXT)."
                )
                local_media_provider = lm.LocalMediaProvider(
                    lm_cfg, cfg.artwork_original_dir
                )
                local_media_provider.discover()
        except Exception:  # provider failure must not break the pipeline
            local_media_provider = None
    # Optional Playmatch ROM-hash identity resolver. OPTIONAL and DISABLED by
    # default; only built when a [playmatch] config is present AND enabled. The
    # provider is non-fatal on outage/timeout/oversize (degrades to None so the
    # pipeline continues unchanged). Hash-first: it reuses the sha256 already
    # computed by the scanner (passed via scans) and never refetches.
    playmatch_provider = None
    if playmatch_config_path:
        try:
            from . import playmatch as pm
            from .paths import load_playmatch_config

            pm_cfg = pm.PlaymatchConfig.from_dict(
                load_playmatch_config(playmatch_config_path)
            )
            if pm_cfg.enabled:
                playmatch_provider = pm.PlaymatchProvider(
                    pm_cfg, cfg.metadata_cache_dir
                )
                playmatch_provider.discover()
        except Exception:  # provider failure must not break the pipeline
            playmatch_provider = None
    # Optional Hasheous ROM-hash identity resolver. OPTIONAL and DISABLED by
    # default; only built when a [hasheous] config is present AND enabled. It
    # mirrors the Playmatch wiring exactly (reuses the scanner-computed sha256,
    # degrades to None on construction failure so the pipeline continues
    # unchanged, non-fatal on outage/timeout/oversize). The Hasheous provider is
    # invoked ALONGSIDE the PlaymatchProvider; both resolve the same hash-first
    # identity layer. Exact-hash identity outranks any weaker signal.
    hasheous_provider = None
    if hasheous_config_path:
        try:
            from . import hasheous as hs
            from .paths import load_hasheous_config

            hs_cfg = hs.HasheousConfig.from_dict(
                load_hasheous_config(hasheous_config_path)
            )
            if hs_cfg.enabled:
                hasheous_provider = hs.HasheousProvider(
                    hs_cfg, cfg.metadata_cache_dir
                )
                hasheous_provider.discover()
        except Exception:  # provider failure must not break the pipeline
            hasheous_provider = None
    _act(
        f"Filling in missing metadata for {len(groups)} release(s) "
        + ("from online sources (this can take a while)."
           if online
           else "from cached copies (offline).")
    )
    enrich_results = enrich.enrich_all(
        groups,
        nfo_dir=nfo_dir,
        scans=scans,
        artwork_original_dir=artwork_original_dir,
        artwork_processed_dir=artwork_processed_dir,
        metadata_cache_dir=metadata_cache_dir,
        curated_metadata_dir=curated_metadata_dir,
        online=online,
        refresh=refresh_metadata,
        local_media_provider=local_media_provider,
        playmatch_provider=playmatch_provider,
        hasheous_provider=hasheous_provider,
        mobygames_enabled=mobygames_enabled,
        mobygames_api_key_env=mobygames_api_key_env,
        include_artwork=include_artwork,
        activity=activity,
    )
    _act("Metadata and artwork preparation complete.")

    # Phase 4b: RTFM deterministic manual sidecar build (M1; offline, NO-AI).
    # Built only when an [rtfm] config is present and enabled. Strictly read-only
    # against the configured discovery roots; writes only under assets/rtfm.
    # A disabled/absent config yields rtfm_results=[] so the export phase is
    # unchanged. A failure must never break the run (degrade to no .rtfm).
    rtfm_results: list = []
    rtfm_dir = cfg.rtfm_dir
    # (GH-24) Manuals/RTFM selection is independent of artwork. When the
    # operator turns it off, the deterministic RTFM build is skipped entirely
    # (zero provider work) even if an [rtfm] config is present and enabled.
    if rtfm_config_path and include_manuals_rtfm:
        try:
            from . import rtfm as rtfm_mod
            from .paths import load_rtfm_config

            rtfm_cfg = rtfm_mod.RtfmConfig.from_dict(load_rtfm_config(rtfm_config_path))
            if rtfm_cfg.enabled:
                rtfm_results = rtfm_mod.build_rtfm_all(
                    groups, cfg=rtfm_cfg, rtfm_dir=rtfm_dir
                )
        except Exception:  # RTFM build failure must not break the pipeline
            rtfm_results = []

    # Phase 6: quarantine routing for flagged groups.
    _act("Checking for releases that need review…")
    quarantine_summary = quarantine.route_quarantine(
        groups, review_dir=review_dir, unknown_dir=unknown_dir, scans=scan_map
    )
    _act(
        f"Sent {len(quarantine_summary['review'])} release(s) to review; "
        f"{len(quarantine_summary['unknown'])} set aside as unrecognized."
    )

    # Phase 5: Gotek export (gated). Runs only when requested AND the gate is
    # open. Writes exclusively to a run-owned staging dir; never the SD card.
    export_result = None
    if export:
        _act("Preparing the export…")
        export_result = exporter.export_all(
            groups,
            staging_dir=cfg.staging_dir,
            run_id=run_id,
            upstream_task_closed=upstream_task_closed,
            verified_artwork_width=verified_artwork_width,
            verified_artwork_height=verified_artwork_height,
            artwork_original_dir=artwork_original_dir,
            artwork_processed_dir=artwork_processed_dir,
            nfo_dir=nfo_dir,
            rtfm_dir=rtfm_dir,
            original_dir=original_dir,
            verify_only=verify_only,
            require_artwork=require_artwork,
        )
        _act(
            f"Export finished: {export_result.releases_exported} release(s), "
            f"{export_result.folders_written} folder(s) written to "
            f"{redact(str(export_result.staging_root))}."
        )

    # Phase 5 gate check (report even when export not requested).
    gate_open, gate_reason = export_gate_open(
        upstream_task_closed,
        verified_artwork_width,
        verified_artwork_height,
    )

    # Preservation re-verify.
    ok, problems = scanner.records_byte_identical(scans)

    # Per-group diagnostic detail (structured logging): surfaced for the per-run log so
    # operators can see, per release, quarantine routing, the metadata provider
    # that answered (or that we ran offline), and the enrichment notes (including
    # artwork download/failure details and cache hits). Additive; existing
    # aggregate keys above are unchanged.
    per_group = []
    for g, r in zip(groups, enrich_results):
        # Route event (quarantine/review) is only known after Phase 6 runs, so it
        # is recorded here alongside the enrichment events from enrich_group.
        events = [e.to_dict() for e in r.events]
        if g.quarantine_reason:
            special_only = (not g.has_main_disk) and bool(g.specials)
            events.append({
                "category": ("route_quarantine" if special_only else "route_review"),
                "detail": g.quarantine_reason,
                "url": None,
                "cache": None,
                "ok": True,
                "error": None,
            })
        per_group.append(
            {
                "release_key": g.release_key,
                "title": g.title,
                "quarantine_reason": g.quarantine_reason,
                "provider": r.provider,
                "artwork_missing": (not g.quarantine_reason) and bool(r.artwork_missing),
                "notes": list(r.notes),
                "events": events,
            }
        )

    result: dict = {
        "run_id": run_id,
        "online": online,
        # (GH-24) The operator's per-type selection is recorded for observability.
        "include_artwork": bool(include_artwork),
        "include_manuals_rtfm": bool(include_manuals_rtfm),
        "metadata_providers": [r.provider for r in enrich_results],
        "metadata_records": [str(r.metadata_path) for r in enrich_results if r.metadata_path],
        "files_scanned": len(scans),
        "records_parsed": len(records),
        "groups": len(groups),
        "catalog_new_scan": n_scan,
        "catalog_new_parse": n_parse,
        "nfo_written": [str(r.nfo_path) for r in enrich_results if r.nfo_path],
        "artwork_resized": [str(r.artwork_resized) for r in enrich_results if r.artwork_resized],
        "artwork_missing": [release_basename(g) for g, r in zip(groups, enrich_results) if not g.quarantine_reason and r.artwork_missing],
        "enrichment_notes": [note for r in enrich_results for note in r.notes],
        "review_routed": quarantine_summary["review"],
        "unknown_routed": quarantine_summary["unknown"],
        "applied_approvals": _applied,
        "unmatched_approvals": _unmatched,
        "hash_failures": _hash_failures,
        "export_gate_open": gate_open,
        "export_gate_reason": gate_reason,
        "original_preserved": ok,
        "original_problems": problems,
        "per_group": per_group,
    }
    if export_result is not None:
        result["export"] = {
            "releases_exported": export_result.releases_exported,
            "folders_written": export_result.folders_written,
            "files_written": export_result.files_written,
            "files_unchanged": export_result.files_unchanged,
            "conflicts": export_result.conflicts,
            "skipped_quarantined": export_result.skipped_quarantined,
            "errors": export_result.errors,
            "staging_root": str(export_result.staging_root),
        }
    result["rtfm"] = {
        "configured": bool(rtfm_config_path),
        # (GH-24) True when an [rtfm] config is present AND the operator's
        # manuals/RTFM selection is on. A config that is present but deselected
        # reports selected=False and builds nothing.
        "selected": bool(rtfm_config_path and include_manuals_rtfm),
        "built": [str(r.rtfm_path) for r in rtfm_results if r.written],
        "routed_for_review": [
            {"release_key": r.release_key, "reason": r.review_reason}
            for r in rtfm_results
            if r.routed_for_review
        ],
        "provenance_written": [str(r.provenance_path) for r in rtfm_results if r.provenance_path],
    }
    return result
