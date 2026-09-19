"""Pipeline: orchestrate scan -> parse -> group -> enrich -> quarantine.

Phase 5 (Gotek export) is intentionally NOT invoked here; it is hard-gated by
``exporter_guard.export_gate_open`` and requires an explicit operator safety signal.

All writes target managed data directories (catalog, assets, review, unknown,
work). ``original/`` is read-only throughout.
"""
from __future__ import annotations

import itertools
import json
import logging
import os
import sqlite3
import threading
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

from . import artwork as artwork_mod
from . import catalog, enrich, exporter, grouper, quarantine, scanner
from . import diagnostics
from .metadata_source import MetadataSourceManager
from .enrich import VERIFIED_ARTWORK_WIDTH, VERIFIED_ARTWORK_HEIGHT
from .exporter_guard import export_gate_open
from .logging_utils import redact
from .models import ParsedRecord, ReleaseGroup, ScanRecord, StagedLibrary, StagedReleaseEntry, StagedState, StagedChange, CurationAction
from .file_identity import FileIdentityStore
from .parser import parse_filename
from .naming import release_basename
from .canonical_naming import (
    export_name_for_release_group,
    _load_canonical_library,
)
from .paths import PathConfig
from .run_config import RunConfig


def _release_basename_with_warn(group: ReleaseGroup, library_root: Optional[Path] = None) -> str:
    """Return canonical_release_name(group, library_root) result.

    AR-005: release_basename is deprecated. Use canonical_release_name
    as the primary naming path; this helper preserves compatibility
    when the canonical DB is absent.
    """
    from .naming import canonical_release_name
    try:
        basename, _prov = canonical_release_name(group, library_root)
        return basename
    except Exception:
        return release_basename(group)

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


def _apply_curation(
    groups: list[ReleaseGroup],
    library_state_path: Optional[str],
    decisions: Optional[dict],
) -> tuple[Optional[StagedLibrary], dict]:
    """Apply curation state to groups between quarantine and 1G1R.

    Loads the staged library from ``library_state_path``, then for each
    group matches by ``release_key`` and:
      * ACCEPTED  — clears quarantine_reason and seeds the ``decisions``
        dict so ``select_one_per_game`` keeps this release as the
        winner for its game.
      * REJECTED  — sets quarantine_reason so the group is skipped by
        export.
      * Other states — leaves quarantine as-is (MODIFIED/NEEDS_REVIEW
        groups may still need manual review).

    Returns the loaded ``StagedLibrary`` (or ``None`` if no state file)
    and the updated ``decisions`` dict (seeded with ACCEPTED entries).
    """
    if not library_state_path:
        return None, decisions or {}

    state_path = Path(library_state_path)
    if not state_path.exists():
        return None, decisions or {}

    from .library_state import CurationStateManager

    manager = CurationStateManager(state_path)
    library = manager.load()

    if not library.releases:
        return library, decisions or {}

    updated_decisions = dict(decisions) if decisions else {}

    for group in groups:
        entry = library.releases.get(group.release_key)
        if entry is None:
            continue

        if entry.curation_state == StagedState.ACCEPTED:
            # Clear quarantine so the group survives to export.
            group.quarantine_reason = None
            # Seed 1G1R decisions: accepted entry wins its game.
            game_id = group.release_key.split("|")[0].lower()
            updated_decisions[game_id] = group.release_key.lower()
        elif entry.curation_state == StagedState.REJECTED:
            group.quarantine_reason = "rejected by curation"
        # MODIFIED/NEEDS_REVIEW: keep existing quarantine; operator
        # has flagged these for review and the pipeline should not
        # silently export them.

    return library, updated_decisions


def run_pipeline(
    cfg: PathConfig,
    run: RunConfig,
    **kwargs: Any,
) -> dict:
    """Execute phases 2-4, 5 (optional), and 6. Returns a result summary dict.

    All filesystem locations come from ``cfg`` (:class:`PathConfig`). The
    original corpus (``cfg.original_dir``) is read-only throughout.

    ``activity`` (issue #21): optional live-log hook. When given, the pipeline
    reports each major milestone (scan, grouping, enrichment, export) as one
    plain-language line. The hook is optional and safe: a missing or failing
    callback never changes pipeline behavior. CLI callers omit it, so CLI
    output is byte-identical to before.

    RunConfig fields govern behavioral toggles and config-path references.
    Complex runtime controls (``cancel_event``, ``activity``,
    ``progressive_prompt_callback``) may be passed via ``**kwargs``.
    """
    from . import run_config as _rc

    # Extract RunConfig fields for backward compatibility within the body.
    online = run.online
    refresh_metadata = run.refresh_metadata
    require_artwork = run.require_artwork
    include_artwork = run.include_artwork
    include_manuals_rtfm = run.include_manuals_rtfm
    upstream_task_closed = run.upstream_task_closed
    run_id = run.run_id
    export = run.export
    verify_only = run.verify_only
    verified_artwork_width = run.verified_artwork_width
    verified_artwork_height = run.verified_artwork_height
    local_media_config_path = run.local_media_config_path
    rtfm_config_path = run.rtfm_config_path
    playmatch_config_path = run.playmatch_config_path
    hasheous_config_path = run.hasheous_config_path
    igdb_config_path = run.igdb_config_path
    screenscraper_config_path = run.screenscraper_config_path
    retroachievements_config_path = run.retroachievements_config_path
    retrokit_config_path = run.retrokit_config_path
    one_per_game = run.one_per_game
    operator_decisions_path = run.operator_decisions_path
    selection_manifest_path = run.selection_manifest_path
    library_state_path = run.library_state_path
    convert_progressive_jpeg = kwargs.pop("convert_progressive_jpeg", run.convert_progressive_jpeg)
    progressive_prompt_callback = kwargs.pop("progressive_prompt_callback", run.progressive_prompt_callback)
    cancel_event = kwargs.pop("cancel_event", None)
    activity = kwargs.pop("activity", None)
    if kwargs:
        raise TypeError(f"unexpected keyword arguments: {sorted(kwargs)}")
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
    if local_media_config_path:
        try:
            from . import local_media as lm

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
    # (GH-164 RC4) Optional DAT/local metadata source manager.
    metadata_source_manager = None
    if metadata_cache_dir:
        try:
            metadata_source_manager = MetadataSourceManager(
                metadata_cache_dir / "metadata_sources.db"
            )
        except Exception:
            metadata_source_manager = None
    # Optional IGDB metadata/artwork provider. OPTIONAL and DISABLED by
    # default; only built when an [igdb] config is present AND enabled.
    # The provider uses title + Amiga platform search (not hash-first).
    # Credentials (client_id, client_secret) come from the SecretStore
    # / environment variables, never from config files.
    igdb_provider = None
    if igdb_config_path:
        try:
            from . import igdb as igdb_mod
            from .paths import load_igdb_config

            igdb_cfg = igdb_mod.IgdbConfig.from_dict(
                load_igdb_config(igdb_config_path)
            )
            if igdb_cfg.enabled:
                # Credentials from environment / SecretStore
                import os
                client_id = os.environ.get("IGDB_CLIENT_ID", "").strip()
                client_secret = os.environ.get("IGDB_CLIENT_SECRET", "").strip()
                if client_id and client_secret:
                    igdb_provider = igdb_mod.IgdbProvider(
                        igdb_cfg, cfg.metadata_cache_dir,
                        client_id=client_id, client_secret=client_secret
                    )
                    igdb_provider.discover()
                else:
                    # Missing credentials - provider stays disabled
                    igdb_provider = None
        except Exception:  # provider failure must not break the pipeline
            igdb_provider = None
    # Optional ScreenScraper metadata/artwork/manual provider. OPTIONAL and DISABLED by
    # default; only built when a [screenscraper] config is present AND enabled.
    # The provider uses hash-first (CRC/MD5/SHA1) lookup, then cached provider ID
    # reuse, then title + system search. Credentials (devid, devpassword, softname,
    # ssid, sspassword) come from environment variables / SecretStore only.
    screenscraper_provider = None
    if screenscraper_config_path:
        try:
            from . import screenscraper as ss_mod
            from .paths import load_screenscraper_config

            ss_cfg = ss_mod.ScreenScraperConfig.from_dict(
                load_screenscraper_config(screenscraper_config_path)
            )
            if ss_cfg.enabled:
                # Credentials from environment / SecretStore
                import os
                dev_id = os.environ.get("SCREENSCRAPER_DEV_ID", "").strip()
                dev_password = os.environ.get("SCREENSCRAPER_DEV_PASSWORD", "").strip()
                softname = os.environ.get("SCREENSCRAPER_SOFTNAME", "AmigaADFLibraryBuilder").strip()
                ssid = os.environ.get("SCREENSCRAPER_SSID", "").strip()
                sspassword = os.environ.get("SCREENSCRAPER_SSPASSWORD", "").strip()
                if dev_id and dev_password:
                    screenscraper_provider = ss_mod.ScreenScraperProvider(
                        ss_cfg, cfg.metadata_cache_dir,
                        dev_id=dev_id, dev_password=dev_password,
                        softname=softname, ssid=ssid, sspassword=sspassword
                    )
                    # ScreenScraper provider doesn't have a discover() method
                else:
                    # Missing credentials - provider stays disabled
                    screenscraper_provider = None
        except Exception:  # provider failure must not break the pipeline
            screenscraper_provider = None
    # Optional RetroAchievements metadata/artwork provider. OPTIONAL and DISABLED
    # by default; only built when a [retroachievements] config is present AND
    # enabled AND the API key is in the environment. The provider uses exact
    # MD5 hash-first identity (first non-special disk), with the game list
    # fetched (and cached) from the RA Web API. Credentials (API key) come from
    # the environment / SecretStore only.
    retroachievements_provider = None
    if retroachievements_config_path:
        try:
            from . import retroachievements as ra_mod
            from .paths import load_retroachievements_config

            ra_cfg = ra_mod.RaConfig.from_dict(
                load_retroachievements_config(retroachievements_config_path)
            )
            if ra_cfg.enabled:
                import os
                ra_api_key = os.environ.get("RETROACHIEVEMENTS_API_KEY", "").strip()
                if ra_api_key:
                    retroachievements_provider = ra_mod.RetroAchievementsProvider(
                        ra_cfg, cfg.metadata_cache_dir, ra_api_key
                    )
                    retroachievements_provider.discover()
                else:
                    # Missing API key -- provider stays disabled
                    retroachievements_provider = None
        except Exception:  # provider failure must not break the pipeline
            retroachievements_provider = None
    # Hall of Light optional provider gating. OPTIONAL and ENABLED by
    # default; disabled via the [hall-of-light] TOML table.
    halloflight_enabled = True  # backward compatible default
    if run.hall_of_light_config_path:
        try:
            from .paths import load_hall_of_light_config
            from .metadata import HallOfLightConfig
            hol_cfg = HallOfLightConfig.from_dict(
                load_hall_of_light_config(run.hall_of_light_config_path)
            )
            halloflight_enabled = hol_cfg.enabled
        except Exception:  # provider failure must not break the pipeline
            halloflight_enabled = True  # fail open
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
        igdb_provider=igdb_provider,
        screenscraper_provider=screenscraper_provider,
        retroachievements_provider=retroachievements_provider,
        halloflight_enabled=halloflight_enabled,
        include_artwork=include_artwork,
        activity=activity,
        cancel_event=cancel_event,
        metadata_source_manager=metadata_source_manager,
        library_root=library_root,
    )
    _act("Metadata and artwork preparation complete.")

    # Phase 4b: RTFM deterministic manual sidecar build (M1; offline, NO-AI).
    # Built only when an [rtfm] config is present and enabled. Strictly read-only
    # against the configured discovery roots; writes only under assets/rtfm.
    # A disabled/absent config yields rtfm_results=[] so the export phase is
    # unchanged. A failure must never break the run (degrade to no .rtfm).
    rtfm_results: list = []
    rtfm_dir = cfg.rtfm_dir
    manual_trace: dict[str, Any] = {
        "roots_searched": [],
        "files_indexed": 0,
        "candidates": [],
        "provider_statuses": [],
        "extraction_method": None,
        "output_path": None,
        "output_size": None,
        "export_status": "skipped",
        "category": "no-config",
        "detail": "",
        # GH-176 diagnostic fields: resolved enabled state and reason
        "resolved_enabled_state": False,
        "enabled_reason": "no RTFM config path",
        "config_source": "none",
        # GH-176 per-release diagnostics: match/no-match/review detail
        "per_release": [],
    }
    # (GH-24) Manuals/RTFM selection is independent of artwork. When the
    # operator turns it off, the deterministic RTFM build is skipped entirely
    # (zero provider work) even if an [rtfm] config is present and enabled.
    if rtfm_config_path and include_manuals_rtfm:
        try:
            from . import rtfm as rtfm_mod
            from .paths import load_rtfm_config

            rtfm_cfg = rtfm_mod.RtfmConfig.from_dict(load_rtfm_config(rtfm_config_path))
            # GH-176 diagnostic: resolved enabled state and reason
            _rtfm_config_source = "gui-rtfm.toml" if rtfm_config_path.endswith("gui-rtfm.toml") else "provider-config"
            manual_trace["resolved_enabled_state"] = bool(rtfm_cfg.enabled)
            manual_trace["config_source"] = _rtfm_config_source
            if rtfm_cfg.enabled:
                # Determine why RTFM is enabled
                _enabled_reason_parts = []
                if rtfm_cfg.manuals_roots:
                    _enabled_reason_parts.append(f"manuals_roots={list(rtfm_cfg.manuals_roots)}")
                if rtfm_cfg.instructions_roots:
                    _enabled_reason_parts.append(f"instructions_roots={list(rtfm_cfg.instructions_roots)}")
                if rtfm_cfg.cheats_roots:
                    _enabled_reason_parts.append(f"cheats_roots={list(rtfm_cfg.cheats_roots)}")
                if not _enabled_reason_parts:
                    _enabled_reason_parts.append("explicitly enabled")
                manual_trace["enabled_reason"] = "; ".join(_enabled_reason_parts)
                _act("RTFM phase: enabled, building manual sidecars…")
                manual_trace["category"] = "enabled"
                manual_trace["roots_searched"] = list(rtfm_cfg.manuals_roots)
                # Optional RetroKit / Archive.org manual provider (GH-10).
                # OPTIONAL and DISABLED by default; only built when a
                # [retrokit_manuals] config is present AND enabled AND the run
                # is online. The provider performs network fetches (index +
                # manual artifact), so --online is the operator's explicit
                # network-authorization signal, exactly like the other online
                # providers; an offline run never touches the network and the
                # deterministic RTFM build is unchanged. No credentials are
                # required (Archive.org is public). The cache lives under the
                # managed metadata cache dir. A provider failure, miss, or
                # outage degrades to extra_sources=None so the deterministic
                # offline RTFM build is unchanged.
                retrokit_sources = None
                if online and retrokit_config_path:
                    try:
                        _act("RTFM phase: querying RetroKit manual provider…")
                        from . import retrokit as rk_mod
                        from .paths import load_retrokit_config

                        rk_cfg = rk_mod.RetroKitConfig.from_dict(
                            load_retrokit_config(retrokit_config_path)
                        )
                        if rk_cfg.enabled:
                            rk_provider = rk_mod.RetroKitProvider(
                                rk_cfg, metadata_cache_dir
                            )
                            rk_results = [
                                rk_provider.resolve_and_acquire(g) for g in groups
                            ]
                            retrokit_sources = rk_mod.to_rtfm_sources(rk_results)
                            _act(f"RTFM phase: RetroKit returned {len(rk_results)} candidate(s)")
                            manual_trace["provider_statuses"].append("retrokit:queried")
                    except Exception:  # provider failure must not break the pipeline
                        retrokit_sources = None
                        manual_trace["provider_statuses"].append("retrokit:error")

                # (GH-183) Wire Lemon Amiga typed doc acquisition
                # into the RTFM pipeline. Query Lemon Amiga for
                # Hints/Solution/Cheat and convert to RtfmSource entries
                # with DocType classification so the RTFM builder can
                # produce readable RTFM with proper provenance.
                lemonamiga_sources = []
                try:
                    from .rtfm import lemonamiga_to_rtfm_sources
                    # Build a list of game objects from groups for
                    # Lemon Amiga lookup.
                    _games_for_lem = [
                        g for g in groups
                        if not g.quarantine_reason
                    ]
                    if _games_for_lem:
                        lemonamiga_sources = lemonamiga_to_rtfm_sources(
                            _games_for_lem,
                            config=None,  # Use defaults (lemonamiga enabled
                            # via provider config if available)
                        )
                        _act(
                            f"RTFM phase: Lemon Amiga returned "
                            f"{len(lemonamiga_sources)} typed doc candidate(s)"
                        )
                        manual_trace["provider_statuses"].append("lemon-amiga:queried")
                except Exception:  # Lemon Amiga failure must not break the run
                    manual_trace["provider_statuses"].append("lemon-amiga:error")

                # Combine all extra sources: RetroKit first, then
                # Lemon Amiga typed docs.
                _all_extra_sources = []
                if retrokit_sources:
                    _all_extra_sources.extend(retrokit_sources)
                if lemonamiga_sources:
                    _all_extra_sources.extend(lemonamiga_sources)

                rtfm_results = rtfm_mod.build_rtfm_all(
                    groups,
                    cfg=rtfm_cfg,
                    rtfm_dir=rtfm_dir,
                    extra_sources=_all_extra_sources if _all_extra_sources else None,
                    library_root=library_root,
                )
                _act(f"RTFM phase: built {len(rtfm_results)} sidecar(s)")
                manual_trace["category"] = "built"
                manual_trace["files_indexed"] = len(rtfm_results)
                manual_trace["candidates"] = [
                    {"release_key": r.release_key, "basename": r.basename, "written": r.written}
                    for r in rtfm_results
                ]
                # GH-176 per-release diagnostics
                manual_trace["per_release"] = [
                    {
                        "release_key": r.release_key,
                        "basename": r.basename,
                        "written": r.written,
                        "routed_for_review": r.routed_for_review,
                        "review_reason": r.review_reason,
                        "output_path": str(r.rtfm_path) if r.rtfm_path else None,
                        "sources_count": len(r.sources),
                        # RC-5: typed doc type from first source (if any),
                        # else the highest-priority source category.
                        "doc_type": (
                            r.sources[0].doc_type
                            if r.sources and r.sources[0].doc_type
                            else (r.sources[0].category if r.sources else "")
                        ),
                        # RC: whether the provider game ID was preserved
                        # across identification (non-empty provider_url_canonical).
                        "provider_id_preserved": any(
                            s.match_kind and s.match_kind != "none"
                            for s in (r.sources or [])
                        ),
                    }
                    for r in rtfm_results
                ]
                for _r in rtfm_results:
                    if _r.written and _r.rtfm_path:
                        manual_trace["output_path"] = str(_r.rtfm_path)
                        manual_trace["output_size"] = _r.rtfm_path.stat().st_size if _r.rtfm_path.is_file() else None
                        manual_trace["extraction_method"] = "text"
                        break
            else:
                _act("RTFM phase: config present but disabled — skipping")
                manual_trace["category"] = "disabled"
                manual_trace["detail"] = "[rtfm] enabled=false in config"
                manual_trace["enabled_reason"] = (
                    "rtfm_cfg.enabled=False; no manual roots or RTFM roots "
                    "present to auto-enable; operator explicitly disabled"
                )
                manual_trace["per_release"] = [
                    {"release_key": g.release_key, "status": "skipped",
                     "reason": "RTFM disabled in config"}
                    for g in groups
                ]
        except Exception as exc:
            # (GH-167 RC-C) Categorized failure records instead of
            # silently collapsing everything into []. Each group gets
            # a RtfmResult with an explicit reason so the operator
            # can distinguish no-config, disabled, generation-failed,
            # etc.
            from .rtfm import RtfmResult
            rtfm_results = []
            _cfg_template = globals().get("rtfm_cfg")
            if _cfg_template is not None:
                _cfg_template = getattr(_cfg_template, "template", "")
            else:
                _cfg_template = ""
            _act(f"RTFM phase: error — {exc}")
            manual_trace["category"] = "generation-failed"
            manual_trace["detail"] = str(exc)
            for _g in groups:
                _rk = getattr(_g, "release_key", "")
                _reason = str(exc)
                _category = "generation-failed"
                _lower = _reason.lower()
                if "config" in _lower or isinstance(exc, (KeyError, TypeError)):
                    _category = "no-config"
                elif "disabled" in _lower:
                    _category = "disabled"
                elif "source" in _lower or "discover" in _lower:
                    _category = "no-source-found"
                rtfm_results.append(RtfmResult(
                    release_key=_rk,
                    basename=getattr(_g, "title", _rk),
                    routed_for_review=True,
                    review_reason=f"{_category}: {_reason}",
                    template_used=_cfg_template,
                ))
                manual_trace["candidates"].append({
                    "release_key": _rk,
                    "category": _category,
                    "reason": _reason,
                })

    # Phase 6: quarantine routing for flagged groups.
    _act("Checking for releases that need review…")
    # (GH-164 RC3) Collect review_items from enrich results so they
    # persist through to the review/ directory and appear in review_routed.
    _all_review_items = []
    for _er in enrich_results:
        _all_review_items.extend(getattr(_er, "review_items", []))
    quarantine_summary = quarantine.route_quarantine(
        groups, review_dir=review_dir, unknown_dir=unknown_dir, scans=scan_map,
        review_items=_all_review_items,
    )
    _act(
        f"Sent {len(quarantine_summary['review'])} release(s) to review; "
        f"{len(quarantine_summary['unknown'])} set aside as unrecognized."
    )

    # Phase 5: Gotek export (gated). Runs only when requested AND the gate is
    # open. Writes exclusively to a run-owned staging dir; never the SD card.
    export_result = None
    selection_result: Optional[object] = None
    if export:
        _act("Preparing the export…")
        # (GH-107 Slice 6) 1G1R selection: pick one release per game
        # before the export phase so only the selected releases are exported.
        selection_result = None
        try:
            from .selection import select_one_per_game, load_operator_decisions, write_selection_manifest
            decisions = None
            if operator_decisions_path:
                decisions = load_operator_decisions(Path(operator_decisions_path))
            # (GH-136) Apply curation state between quarantine and
            # 1G1R: ACCEPTED entries clear quarantine and seed
            # decisions; REJECTED entries get a quarantine flag.
            staged_library, decisions = _apply_curation(
                groups, library_state_path, decisions
            )
            if one_per_game:
                # Load canonical library once for region/language/version scoring.
                # If no canonical.db exists yet (fresh CLI export), create one
                # from the current staged result so region/language rank effectively.
                _canon = _load_canonical_library(library_root)
                if _canon is None:
                    _canon = _ensure_canonical_library(library_root, groups)
                selection_result = select_one_per_game(
                    groups, approvals=approvals, canon=_canon, decisions=decisions
                )
                if _canon is not None:
                    try:
                        _canon.close()
                    except Exception:
                        pass
            else:
                selection_result = None
            if selection_result is not None:
                groups = list(selection_result.selected)
                _act(
                    f"1G1R selection: {selection_result.provenance['selected_count']} "
                    f"release(s) selected from {len(set(g.release_key.split('|')[0].lower() for g in groups + selection_result.rejected))} game(s)."
                )
            else:
                _act("1G1R selection: skipped (--no-1g1r)")
            if selection_result is not None and selection_manifest_path:
                try:
                    write_selection_manifest(selection_result, Path(selection_manifest_path))
                    _act(f"Selection manifest written to {selection_manifest_path}")
                except Exception as mexc:
                    _act(f"Selection manifest write failed: {mexc}")
        except Exception as exc:
            # Selection failure: fail safe — do NOT export all releases.
            selection_result = None
            _act(f"1G1R selection failed: {exc}; export blocked — no releases selected.")
            from .selection import SelectionResult
            result = {
                "run_id": run_id,
                "selection_failed": True,
                "selection_error": str(exc),
                "export_gate_open": False,
                "export_gate_reason": "selection failed; export blocked",
            }
            if selection_manifest_path:
                try:
                    from .selection import write_selection_manifest as _wsm
                    _wsm(
                        SelectionResult(selected=[], rejected=[], selection_manifest=[], provenance={"selected_count": 0, "rejected_count": 0, "decisions": [], "selection_error": str(exc)}),
                        Path(selection_manifest_path),
                    )
                except Exception:
                    pass
            return result
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
            # (GH-102) Progressive JPEG conversion policy.
            convert_progressive_jpeg=convert_progressive_jpeg,
            # (GH-102) Forward per-image progressive-conversion prompt callback.
            progressive_prompt_callback=progressive_prompt_callback,
            library_root=library_root,
            # (GH-136) Staged library for artwork fallback.
            staged_library=staged_library,
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
        # (GH-107 Slice 5) Canonical naming: propose the canonical export
        # name when the canonical DB is available; fall back to
        # release_basename(group) when no canonical match exists.
        # (AR-005: release_basename is deprecated; canonical path preferred.)
        _canon = _load_canonical_library(library_root)
        if _canon is not None:
            try:
                _cn = export_name_for_release_group(_canon, g)
                _folder = _cn.basename
                _canon_prov = _cn.provenance_text
            finally:
                _canon.close()
        else:
            from .naming import canonical_release_name
            try:
                _folder, _cn = canonical_release_name(g, library_root)
                _canon_prov = _cn
            except Exception:
                _folder = release_basename(g)
                _canon_prov = "fallback: no canonical DB"
        per_group.append(
            {
                "release_key": g.release_key,
                "title": g.title,
                # (GH-99) Canonical staged identity fields: the ReleaseGroup
                # already carries the parsed edition/crack-group when the
                # filenames provide evidence. These are the values the
                # Preview & Curation table displays (defect 2).
                "edition": g.edition,
                "group": g.group,
                # (GH-99) Canonical metadata match confidence, preserved
                # verbatim from the resolved MetadataRecord (None when no
                # metadata resolved). Feeds the builder's confidence field and
                # the Preview & Curation "Confidence" column (defect 2).
                "confidence": r.metadata_confidence,
                "quarantine_reason": g.quarantine_reason,
                "provider": r.provider,
                "artwork_missing": (not g.quarantine_reason) and bool(r.artwork_missing),
                "notes": list(r.notes),
                # (GH-167 RC-A) Processed artwork path: used to set
                # entry.artwork_front at staging so the Preview Curation
                # artwork preview renders the actual artifact instead of
                # a silent blank panel.
                "artwork_processed": r.artwork_resized,
                # (GH-167 RC-C) RTFM results for this release group:
                # mapped to entry.rtfm_files at staging so the Preview
                # Curation RTFM panel shows the artifact path instead
                # of "(none)".
                "rtfm_results": [],
                "events": events,
                # (GH-86) Full discovered source inventory for this release
                # (ordered main disks followed by special disks) so the curation
                # preview can show the original ADF files. Source read-only:
                # these are filenames, not copies.
                "source_files": [rec.source_filename for rec in (g.disks + g.specials)],
                # (GH-86) Planned export folder basename (release_basename is the
                # single canonical naming source; honours operator folder override).
                # (GH-107 Slice 5) "folder" uses the canonical proposed name when
                # the canonical DB is available, else falls back to release_basename.
                # (AR-005: release_basename is deprecated; canonical path preferred.)
                "folder": _folder,
                # (GH-107 Slice 5) Provenance for the proposed folder name.
                "canonical_proposed_name": {
                    "basename": _folder,
                    "provenance": _canon_prov,
                },
            }
        )

    # (GH-167 RC-C) Map rtfm_results to per_group entries
    # so staging can populate entry.rtfm_files.
    # Convert RtfmResult objects to JSON-serializable dicts.
    _rtfm_by_key: dict[str, list] = {}
    for _r in rtfm_results:
        _rk = getattr(_r, "release_key", "")
        if _rk:
            _rtfm_by_key.setdefault(_rk, []).append(_r.to_dict())
    for _pg in per_group:
        _pg["rtfm_results"] = _rtfm_by_key.get(_pg["release_key"], [])

    # (GH-44) Run-level provider-attempt diagnostics: derive one structured
    # attempt per (provider, release) from the events above, then roll the
    # attempts up into per-provider success/failure counts, sanitized error
    # samples, and the zero-result reason taxonomy. Pure (no I/O); degrades
    # to an empty roll-up if the event stream is empty.
    provider_diagnostics: dict = {"providers": [], "zero_asset_releases": {},
                                  "reason_taxonomy": {},
                                  "totals": {"attempts": 0, "matched": 0,
                                             "error": 0, "review": 0, "assets": 0}}
    try:
        _attempts = []
        for pg in per_group:
            _attempts.extend(
                diagnostics.attempt_from_enrich_events(
                    pg["events"],
                    title=pg["title"],
                    release_key=pg["release_key"],
                )
            )
        provider_diagnostics = diagnostics.aggregate_provider_attempts(_attempts)
        provider_diagnostics = {
            **provider_diagnostics,
            # Keep the result dict JSON-serializable (CLI emits json.dumps).
            "providers": [
                s.to_dict() if hasattr(s, "to_dict") else s
                for s in provider_diagnostics.get("providers", [])
            ],
        }
    except Exception:  # diagnostics must never break the pipeline
        provider_diagnostics = {
            "providers": [], "zero_asset_releases": {}, "reason_taxonomy": {},
            "totals": {"attempts": 0, "matched": 0, "error": 0, "review": 0,
                       "assets": 0},
            "error": "diagnostics roll-up failed",
        }

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
        "artwork_missing": [
            _release_basename_with_warn(g, library_root=library_root)
            for g, r in zip(groups, enrich_results)
            if not g.quarantine_reason and r.artwork_missing
        ],
        "enrichment_notes": [note for r in enrich_results for note in r.notes],
        "review_routed": quarantine_summary["review"],
        "review_routed_count": len(quarantine_summary["review"]),
        "review_items_count": len(_all_review_items),
        # (GH-164 RC7) Result semantics: split identified vs unresolved
        "identified": [
            r.metadata_path or r.nfo_path
            for r in enrich_results
            if r.metadata_confidence and r.metadata_confidence >= 0.90
        ],
        "unresolved": [
            str(g.release_key) for g, r in zip(groups, enrich_results)
            if not r.metadata_path and not r.nfo_path
            and not r.artwork_master
        ],
        "review_required": [
            str(g.release_key) for g, r in zip(groups, enrich_results)
            if r.needs_manual_review or r.review_items
        ],
        "provider_failures": provider_diagnostics.get("totals", {}).get("error", 0),
        "dat_failures": len(_all_review_items),
        # (GH-164 RC7) Split between authoritative and fuzzy matches
        "exact_or_authoritative": [
            str(g.release_key) for g, r in zip(groups, enrich_results)
            if r.metadata_confidence and r.metadata_confidence >= 0.90
            or r.artwork_master and r.artwork_resized
        ],
        "fuzzy_or_manual": [
            str(g.release_key) for g, r in zip(groups, enrich_results)
            if r.metadata_confidence and r.metadata_confidence < 0.90
            or (r.needs_manual_review and not r.metadata_confidence)
        ],
        "unknown_routed": quarantine_summary["unknown"],
        "applied_approvals": _applied,
        "unmatched_approvals": _unmatched,
        "hash_failures": _hash_failures,
        "export_gate_open": gate_open,
        "export_gate_reason": gate_reason,
        "original_preserved": ok,
        "original_problems": problems,
        "per_group": per_group,
        # (GH-44) Run-level provider-attempt roll-up: per-provider
        # success/failure, match/asset counts, sanitized error samples, and
        # the zero-result reason taxonomy. Consumed by the GUI Diagnostics
        # tab (run summary) and the per-run log.
        "provider_diagnostics": provider_diagnostics,
    }
    if selection_result is not None:
        result["selection"] = {
            "selected_count": selection_result.provenance["selected_count"],
            "rejected_count": selection_result.provenance["rejected_count"],
            "decisions": selection_result.provenance["decisions"],
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
        # (GH-173 C6) Per-release manual acquisition diagnostics
        # with full reason taxonomy. Never empty — always reports
        # why RTFM produced (or did not produce) output.
        "manual_trace": manual_trace,
    }
    # Update export_status in manual_trace based on whether
    # RTFM output was actually copied to the export.
    if export_result is not None:
        manual_trace["export_status"] = (
            "completed" if export_result.files_written else "skipped"
        )
    return result


def build_staged_library_from_result(
    result: dict,
    *,
    library_root: Path,
    run_id: str,
    identity_store: Optional[FileIdentityStore] = None,
    original_dir: Optional[Path] = None,
) -> Optional[Path]:
    """Build a StagedLibrary from pipeline result and save as a state file.

    Creates a curation state file that the Preview & Curation widget can load.
    The file is saved as ``library_state_<run_id>.json`` under
    ``<library_root>/curation/`` -- a managed directory that is independent of
    both the read-only ``original/`` corpus and the export ``output/``
    destination. Writing the state file into ``output/`` would (a) fail on a
    fresh library where that dir does not yet exist and (b) falsely populate
    the export destination, so the curation state is deliberately kept apart.

    Returns the path to the saved state file, or None if no groups were
    processed.
    """
    per_group = result.get("per_group", [])
    if not per_group:
        return None

    curation_dir = Path(library_root) / "curation"
    curation_dir.mkdir(parents=True, exist_ok=True)

    # (GH-99, defect 3) Restore prior curation decisions from the previous
    # state file before the fresh per-release state overwrites them. The
    # state file is the internal curation database: it is keyed by
    # release_key (strong identity) and holds the operator's merge/match/
    # edition/group/state decisions. Membership (adf_files) is always
    # rebuilt from the fresh scan; only the staged curation decisions are
    # carried over, so a previously curated ADF does not require the same
    # work again unless the underlying identity changed.
    previous = StagedLibrary()
    prev_path = _find_previous_state_file(curation_dir, run_id)
    if prev_path is not None:
        try:
            with open(prev_path, "r", encoding="utf-8") as fh:
                prev_data = json.load(fh)
            previous = StagedLibrary.from_dict(prev_data.get("library", {}))
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            previous = StagedLibrary()  # unreadable prior state: start fresh

    library = StagedLibrary()
    for pg in per_group:
        release_key = pg.get("release_key", "")
        title = pg.get("title", "")
        quarantine_reason = pg.get("quarantine_reason")
        artwork_missing = pg.get("artwork_missing", False)
        notes = pg.get("notes", [])
        # (GH-86) Full discovered source inventory + planned export folder.
        source_files = list(pg.get("source_files") or [])
        folder = pg.get("folder")

        # (GH-99, defect 2) Canonical staged edition/group when the scan
        # parsed them; None (displayed blank) when unknown. Never guessed.
        edition = pg.get("edition")
        group = pg.get("group")
        # (GH-99, defect 2) Canonical metadata match confidence, verbatim from
        # the pipeline (None when no metadata resolved). Stored in both the
        # display confidence field and the provenance match_confidence field.
        confidence = pg.get("confidence")
        if confidence is not None:
            try:
                confidence = float(confidence)
            except (TypeError, ValueError):
                confidence = None

        # Determine initial curation state based on quarantine status.
        # Quarantined / flagged releases route to review; clean ones to pending.
        curation_state = (
            StagedState.NEEDS_REVIEW if quarantine_reason else StagedState.PENDING
        )

        entry = StagedReleaseEntry(
            release_key=release_key,
            title=title,
            edition=edition,
            group=group,
            chipset=None,
            language=None,
            version=None,
            alt_marker=None,
            ext="adf",
            adf_files=source_files,
            folder=folder,
            match_confidence=confidence,
            confidence=(confidence if confidence is not None else 0.0),
            curation_state=curation_state,
        )
        # (GH-99, defect 7) Line-oriented, human-readable notes. Each
        # pipeline note is one line; the underlying values (paths, provider
        # names, not-found markers) are preserved verbatim.
        note_lines: list[str] = []
        if artwork_missing:
            note_lines.append("Artwork missing from metadata providers.")
        for note in notes:
            text = str(note).strip()
            if text:
                note_lines.append(text)
        if note_lines:
            entry.notes = "\n".join(note_lines)

        # (GH-167 RC-A) Reconcile processed artwork path into
        # entry.artwork_front so the Preview Curation artwork preview
        # renders the actual artifact instead of a silent blank panel.
        # Only set when no curation-operator artwork choice exists
        # (CURATION-authority claims are preserved by carry_over).
        artwork_processed = pg.get("artwork_processed")
        if artwork_processed and not entry.artwork_front:
            entry.artwork_front = str(artwork_processed)

        # (GH-167 RC-C) Populate entry.rtfm_files from rtfm_results
        # so the Preview Curation RTFM panel shows the artifact path
        # instead of "(none)". Also categorizes failures via
        # RtfmResult.routed_for_review/review_reason.
        for _r in pg.get("rtfm_results", []):
            if _r.get("written", False) and _r.get("rtfm_path"):
                rtfm_path = str(_r["rtfm_path"])
                if rtfm_path and rtfm_path not in entry.rtfm_files:
                    entry.rtfm_files.append(rtfm_path)

        # Add initial state change action
        entry.actions.append(StagedChange(
            action=CurationAction.STATE_CHANGE,
            timestamp=datetime.now(timezone.utc).isoformat(),
            details=f"Initial state from pipeline: {curation_state.value}",
        ))

        library.releases[release_key] = entry

    # (GH-99, defect 3) Restore the operator's prior staged decisions on
    # top of the freshly built state (same release_key only).
    # (GH-107 Slice 2) Also try content-hash matching for same-content-under-new-path.
    library.carry_over(
        previous, identity_store=identity_store, original_dir=original_dir
    )

    # (GH-107 Slice 3) Build/persist the canonical Game/Release/Disk model
    # for this run at <library_root>/curation/canonical.db. The staged state
    # file remains authoritative for curation; the canonical library records
    # the same releases with per-field provenance. Operator curation claims
    # (authority 'curation') recorded here survive later provider refreshes:
    # claim rows are additive and higher-authority claims always win
    # resolution.
    try:
        _persist_canonical_library(
            library, curation_dir, identity_store=identity_store
        )
    except Exception as exc:
        # Canonical persistence is best-effort: the staged state file is the
        # curation authority and must never fail to build because of it.
        # Surface the failure so the operator can see it in the activity log.
        logger.warning(
            "canonical.db persistence failed (non-fatal): %s", exc
        )

    # Save under the managed curation dir (independent of output/ and original/).
    state_path = curation_dir / f"library_state_{run_id}.json"
    from .library_state import CurationStateManager, CurationStateMeta, CurationStateFile
    meta = CurationStateMeta(
        schema_version=1,
        created_at=datetime.now(timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat(),
        source_workspace=f"pipeline_run_{run_id}",
        entry_count=len(library.releases),
    )
    state_file = CurationStateFile(meta=meta, library=library)

    # Atomic write via temp file
    tmp_path = state_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(state_file.to_dict(), indent=2))
    tmp_path.replace(state_path)

    return state_path


def _persist_canonical_library(
    library, curation_dir: Path, *, identity_store=None
) -> Optional[Path]:
    """Persist the canonical Game/Release/Disk model for a staged library.

    (GH-107 Slice 3) Production integration point: called from
    ``build_staged_library_from_result`` on every staged-library build so
    the canonical model always reflects the real application state.
    Migration is additive and deterministic; failures never abort the
    staged build (the staged state file remains the curation authority).
    """
    try:
        from .canonical import (
            CanonicalLibrary,
            migrate_staged_library,
            SourceAuthority,
        )
    except ImportError:  # pragma: no cover - canonical model always present
        return None
    db_path = curation_dir / "canonical.db"
    try:
        with CanonicalLibrary(db_path) as canon:
            migrate_staged_library(
                library, canon, identity_store=identity_store,
                seed_authority=SourceAuthority.CURATION,
            )
            # Scope retirement: releases not in current library are
            # marked retired (soft delete) to halt monotonic growth.
            active_ids = {
                r.release_id for r in library.releases.values()
            }
            canon.retire_releases(active_ids)
    except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
        logger.warning(
            "canonical.db write failed (non-fatal): %s", exc
        )
        return None
    return db_path


def _find_previous_state_file(curation_dir: Path, run_id: str) -> Optional[Path]:
    """Return the most recent state file in ``curation_dir`` other than the
    one being written for ``run_id`` (GH-99 defect 3).

    State files are named ``library_state_<run_id>.json``. The previous run's
    file is the newest one whose name differs from the current run's target.
    Returns None when no prior state exists (first run).
    """
    current_name = f"library_state_{run_id}.json"
    candidates = []
    try:
        for p in curation_dir.iterdir():
            if p.name == current_name or not p.name.startswith("library_state_") or not p.name.endswith(".json"):
                continue
            if p.name.endswith(".tmp"):
                continue
            try:
                mtime = p.stat().st_mtime
            except OSError:
                continue
            candidates.append((mtime, p))
    except OSError:
        return None
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[-1][1]


def _ensure_canonical_library(
    library_root: Path,
    groups: list[ReleaseGroup],
) -> Optional["CanonicalLibrary"]:
    """Create a canonical.db from current groups when none exists and return it.

    On a fresh CLI export, no canonical.db exists yet and region/language
    scoring would be neutral (20.0). This helper builds a minimal canonical
    model from the pipeline's current groups so those fields become effective
    in the normal production export path without inventing a second canonical
    store. When canonical.db already exists, it is opened read-only and
    returned unchanged.

    Returns the opened CanonicalLibrary, or None on failure.
    """
    try:
        from .canonical import (
            CanonicalLibrary,
            migrate_staged_library,
            SourceAuthority,
        )
        from .models import StagedLibrary, StagedReleaseEntry, StagedState, StagedChange, CurationAction
    except ImportError:
        return None
    curation_dir = library_root / "curation"
    curation_dir.mkdir(parents=True, exist_ok=True)
    db_path = curation_dir / "canonical.db"
    if db_path.is_file():
        try:
            return CanonicalLibrary(db_path)
        except (OSError, sqlite3.Error):
            return None
    library = StagedLibrary()
    for g in groups:
        entry = StagedReleaseEntry(
            release_key=g.release_key,
            title=g.title,
            edition=g.edition,
            group=g.group,
            chipset=None,
            language=g.language,
            region=g.region if hasattr(g, "region") else None,
            version=g.version,
            alt_marker=None,
            ext="adf",
            adf_files=[r.source_filename for r in g.records],
            folder=None,
            match_confidence=None,
            confidence=0.0,
            curation_state=StagedState.PENDING,
        )
        library.releases[g.release_key] = entry
    try:
        with CanonicalLibrary(db_path) as canon:
            migrate_staged_library(
                library, canon, seed_authority=SourceAuthority.SEED
            )
        return CanonicalLibrary(db_path)
    except (OSError, sqlite3.Error, ValueError, TypeError):
        return None