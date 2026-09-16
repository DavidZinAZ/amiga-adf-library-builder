"""Shared lookup workflow for Online and Offline lookups (GH-88 + GH-89).

This module is the ONE shared implementation behind both the Online Lookup and
the Offline/Local Lookup entry points in the Preview/Curation workspace.
Routing between the two is explicit, centralized here, and reusable:

* ``classify_lookup_mode`` -- the single source of truth for what a lookup
  mode means and which provider class it belongs to. No other code duplicates
  this routing.
* ``providers_for_mode`` -- the exact provider list shown for a mode. Online
  modes list the online resolver chain; offline modes list the local
  (LaunchBox) media source and NOTHING else. An offline lookup can therefore
  never list or contact an online provider (Hall of Light, RAWG, MobyGames,
  Wikipedia, ...) -- that is a GH-89 hard requirement.

The two execution paths reuse the existing, tested infrastructure:

* Online: :func:`amiga_adf_library_builder.metadata.lookup_metadata` -- the
  curated -> cache -> keyed-online -> Hall of Light -> Wikipedia chain with
  relevance validation.
* Offline: :class:`amiga_adf_library_builder.local_media.LocalMediaProvider`
  -- the read-only, stdlib-only, local LaunchBox media scanner/resolver. It
  never opens a network socket.

Lookup is strictly read-only with respect to the library: it produces a
:class:`LookupResult`. Mutating staged curation state only happens when the
operator explicitly applies a result (see the preview widget).
"""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .local_media import (
    LocalMediaConfig,
    LocalMediaProvider,
    LocalMediaResult,
    load_local_media_config,
    scan_launchbox_roots,
)
from .metadata import MetadataRecord, lookup_metadata

#: Lookup modes accepted by :func:`run_lookup`.
MODE_ONLINE = "online"
MODE_OFFLINE = "offline"
MODE_ALTERNATE = "alternate"
ALL_MODES = (MODE_ONLINE, MODE_OFFLINE, MODE_ALTERNATE)

#: Lookup kinds a mode resolves to.
KIND_ONLINE = "online"
KIND_OFFLINE = "offline"


@dataclass
class LookupContext:
    """Everything a lookup needs to run, resolved once and passed through."""

    query: str
    release_key: str
    title: str = ""
    #: ADF filename stems of the selected release, used as offline match signal.
    disk_stems: list[str] = field(default_factory=list)
    cache_dir: Optional[Path] = None
    curated_dir: Optional[Path] = None
    #: Path to the app config file (carries the ``[local_media]`` table).
    config_path: Optional[Path] = None
    #: Injectable fetch function for offline testing of the online chain.
    opener: Optional[Callable[..., Any]] = None
    timeout: float = 20.0
    #: Path to the metadata_sources SQLite DB for DAT candidate collection.
    db_path: Optional[Path] = None
    #: Per-provider enabled state (id -> bool) for runtime bridge.
    provider_enabled: dict[str, bool] = field(default_factory=dict)
    #: Per-provider field overrides (id -> {key: value}).
    provider_fields: dict[str, dict[str, str]] = field(default_factory=dict)


@dataclass
class LookupResult:
    """A provider-class-tagged lookup outcome (one shared shape for both modes)."""

    mode: str
    kind: str
    provider_ids: list[str]
    #: "found" | "no_match" | "needs_review" | "error"
    status: str = "no_match"
    #: For online: the resolved record. For offline: the local media result.
    record: Optional[MetadataRecord] = None
    local_result: Optional[LocalMediaResult] = None
    #: Human-readable provider/source labels actually consulted.
    consulted: list[str] = field(default_factory=list)
    #: Human-readable local-source state (roots ok/missing, candidate count).
    local_source_state: list[str] = field(default_factory=list)
    error: str = ""
    confidence: float = 0.0

    @property
    def has_match(self) -> bool:
        return self.status in ("found", "needs_review")


def classify_lookup_mode(mode: str) -> str:
    """Map a lookup ``mode`` to its provider ``kind``.

    The single routing decision shared by Online and Offline lookups.
    "online" and the "alternate" re-search both resolve to the online
    provider chain; "offline" resolves to the local (LaunchBox) media source.
    """
    key = (mode or MODE_ONLINE).strip().lower()
    if key == MODE_OFFLINE:
        return KIND_OFFLINE
    # "online" and "alternate" (alternate is an online re-search with a custom
    # query) both use the online provider chain.
    return KIND_ONLINE


def providers_for_mode(mode: str) -> list[str]:
    """Return the ordered provider ids shown/used for a lookup ``mode``.

    Explicit and reusable: the GUI renders these ids, and the worker routes on
    them, so the online/offline provider classification lives in exactly one
    place. Offline modes list ONLY the local media source.
    """
    if classify_lookup_mode(mode) == KIND_OFFLINE:
        return ["local_media"]
    return [
        "curated",
        "cache",
        "rawg",
        "mobygames",
        "hall-of-light",
        "lemon-amiga",
        "wikipedia",
    ]


def _group_for_lookup(ctx: LookupContext) -> Any:
    """Build the small group object the local-media resolver needs.

    The resolver reads ``group.title`` and (optionally) ``group.records`` /
    ``group.release_key``. We provide the title plus the release's ADF stems as
    a lightweight match signal -- no pipeline group object is required.
    """
    title = (ctx.title or ctx.query or "").strip()
    return _LookupGroup(title=title, release_key=ctx.release_key, disk_stems=list(ctx.disk_stems))


@dataclass
class _LookupGroup:
    """Minimal group shape for LocalMediaProvider.resolve()."""

    title: str = ""
    release_key: str = ""
    disk_stems: list[str] = field(default_factory=list)

    @property
    def records(self) -> list:
        # No pipeline records available in the preview context; expose the
        # release's ADF stems as the disk-stem match signal instead.
        return [
            _LookupRecord(source_filename=stem)
            for stem in self.disk_stems
        ]


@dataclass
class _LookupRecord:
    source_filename: str = ""


def _local_source_state(cfg: LocalMediaConfig) -> list[str]:
    """Human-readable state of the configured local (LaunchBox) media roots."""
    lines: list[str] = []
    try:
        report = scan_launchbox_roots(cfg, recursive=cfg.recursive)
    except Exception as exc:  # pragma: no cover - defensive
        return [f"local source scan failed: {exc}"]
    if report is None:
        return ["no local media roots configured"]
    lines.append(f"local media source: {report.total_image_candidates} image candidate(s)")
    for status in report.scanned_media_roots + report.scanned_manual_roots:
        lines.append(f"root {status.status}: {status.path} ({status.file_count} files)")
    for missing in report.missing_roots:
        lines.append(f"root MISSING (not scanned): {missing.path}")
    return lines


def _offline_lookup(mode: str, ctx: LookupContext) -> LookupResult:
    """Run a real OFFLINE (local LaunchBox media) search.

    Never touches the network and never references an online provider.
    """
    result = LookupResult(mode=mode, kind=KIND_OFFLINE,
                          provider_ids=providers_for_mode(mode))
    config: Optional[LocalMediaConfig] = None
    if ctx.config_path is not None:
        try:
            config = load_local_media_config(ctx.config_path)
        except Exception as exc:  # unreadable/invalid config -> local-source state
            result.status = "error"
            result.error = f"could not read local media config: {exc}"
            return result
    if config is None or not config.enabled:
        result.status = "no_match"
        result.local_source_state = [
            "local media source NOT configured (local_media.enabled is false) -- "
            "no offline lookup source available"
        ]
        return result

    result.local_source_state = _local_source_state(config)
    if not (config.roots or config.media_roots):
        result.status = "no_match"
        result.local_source_state.append("no local media roots configured")
        return result

    cache_dir = ctx.cache_dir or Path("local_media_cache")
    try:
        provider = LocalMediaProvider(config, cache_dir)
    except Exception as exc:
        result.status = "error"
        result.error = f"could not initialize local media provider: {exc}"
        return result

    discovered = 0
    try:
        discovered = provider.discover()
    except Exception as exc:
        result.status = "error"
        result.error = f"local media discovery failed: {exc}"
        return result
    result.consulted = [f"local_media ({discovered} candidate image(s) indexed)"]

    try:
        local = provider.resolve(_group_for_lookup(ctx))
    except Exception as exc:
        result.status = "error"
        result.error = f"local media resolution failed: {exc}"
        return result

    result.local_result = local
    if local.found and local.outcome == "auto_match":
        result.status = "found"
        result.confidence = local.confidence
    elif local.outcome == "needs_review":
        result.status = "needs_review"
        result.confidence = local.confidence
    else:
        result.status = "no_match"
        result.confidence = local.confidence
    return result


def _online_lookup(mode: str, ctx: LookupContext) -> LookupResult:
    """Run a real ONLINE provider-backed search via the shared chain."""
    result = LookupResult(mode=mode, kind=KIND_ONLINE,
                          provider_ids=providers_for_mode(mode))
    cache_dir = ctx.cache_dir or Path("metadata_cache")
    curated_dir = ctx.curated_dir or cache_dir
    title = (ctx.query or ctx.title or "").strip()
    if not title:
        result.status = "error"
        result.error = "no search query supplied"
        return result
    # Build provider kwargs from ctx.provider_enabled so the runtime
    # chain respects GUI-set enablement state (GH-170 RC-C bridge).
    provider_kwargs: dict[str, bool] = {}
    for pid, enabled in ctx.provider_enabled.items():
        if not enabled:
            # Map provider IDs to the keyword arguments used by
            # lookup_metadata. Only pass disabled providers; defaults
            # handle the rest (enabled=True for Hall of Light etc.).
            if pid == "playmatch":
                provider_kwargs["playmatch_enabled"] = False
            elif pid == "lemonamiga":
                provider_kwargs["lemonamiga_enabled"] = False
            elif pid == "hall-of-light":
                provider_kwargs["halloflight_enabled"] = False
            elif pid == "igdb":
                provider_kwargs["igdb_enabled"] = False
            elif pid == "mobygames":
                provider_kwargs["mobygames_enabled"] = False
            elif pid == "wikipedia":
                provider_kwargs["wikipedia_enabled"] = False
    try:
        record, source, events = lookup_metadata(
            title,
            cache_dir=cache_dir,
            curated_dir=curated_dir,
            group=_group_for_lookup(ctx),
            opener=ctx.opener,
            timeout=ctx.timeout,
            **provider_kwargs,
        )
    except Exception as exc:
        result.status = "error"
        result.error = f"online lookup failed: {exc}"
        return result

    result.consulted = [ev.get("provider", "") for ev in events if ev.get("provider")]
    if record is not None:
        result.status = "found"
        result.record = record
        result.confidence = record.confidence
        result.consulted.append(f"resolved via {record.provider or source}")
    else:
        result.status = "no_match"
        result.consulted.append("no online provider matched")
    return result


def run_lookup(mode: str, ctx: LookupContext, *, collect_candidates: bool = False) -> Any:
    """Route a lookup to the correct provider class and run it.

    This is the shared entry point for Online and Offline lookups. The routing
    decision (which provider class) is made once, here, via
    :func:`classify_lookup_mode`; the worker and the GUI both call this.

    When ``collect_candidates=True``, runs all available lookup modes in parallel
    and returns a :class:`LookupResultCollection` with ranked candidates from each
    mode. When ``False`` (default), behaves exactly as before -- returns a single
    :class:`LookupResult`.
    """
    if collect_candidates:
        return _collect_all_candidates(ctx)
    kind = classify_lookup_mode(mode)
    if kind == KIND_OFFLINE:
        return _offline_lookup(mode, ctx)
    return _online_lookup(mode, ctx)


# --- Multi-candidate collection helpers --------------------------------------


@dataclass
class LookupResultCollection:
    """A ranked set of candidates collected from multiple lookup modes."""

    candidates: list[dict] = field(default_factory=list)
    online_ok: bool = False
    offline_ok: bool = False
    errors: list[str] = field(default_factory=list)
    #: Per-provider diagnostic status bands (no_match, error, etc.).
    provider_diagnostics: list[dict] = field(default_factory=list)

    @property
    def has_candidates(self) -> bool:
        return len(self.candidates) > 0

    @property
    def exact_matches(self) -> list[dict]:
        """Candidates with confidence >= 0.95 (exact or near-exact hash)."""
        return [c for c in self.candidates if c.get("confidence", 0) >= 0.95]

    @property
    def title_matches(self) -> list[dict]:
        """Candidates matched by title/normalization only (conf < 0.95)."""
        return [c for c in self.candidates if c.get("confidence", 0) < 0.95]


def _normalize_for_dedup(title: str) -> str:
    """Canonical key for deduplicating candidates across providers."""
    s = (title or "").strip().lower()
    # Collapse whitespace, strip common suffixes/prefixes
    s = " ".join(s.split())
    for suffix in (" amiga", ": amiga", " (amiga)"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def _merge_candidates(existing: list[dict], new_candidates: list[dict]) -> list[dict]:
    """Merge new candidates into existing, deduplicating by normalized title.

    Only real candidates (with a title) are merged. Status bands
    (no_match/error diagnostics) are kept separately in
    LookupResultCollection and are NOT selectable candidate rows.
    """
    seen: dict[str, dict] = {}
    for c in existing:
        title = c.get("title")
        if title:
            key = _normalize_for_dedup(title)
            if key not in seen or c.get("confidence", 0) > seen[key].get("confidence", 0):
                seen[key] = c
    for nc in new_candidates:
        title = nc.get("title")
        if title:
            key = _normalize_for_dedup(title)
            if key not in seen or nc.get("confidence", 0) > seen[key].get("confidence", 0):
                seen[key] = nc
    result = sorted(seen.values(), key=lambda c: (-c.get("confidence", 0), c.get("provider", "")))
    return result


def _result_to_candidate(result: "LookupResult") -> list[dict]:
    """Convert a LookupResult to one or more candidate dicts.

    no_match/error results do NOT produce selectable candidate rows;
    they are surfaced as status bands via LookupResultCollection.
    """
    candidates = []
    if result.status in ("found", "needs_review"):
        # --- DEF-4 FIX: compute match_type from actual evidence -------------
        def _compute_match_type(record=None, local_result=None, confidence=0.0):
            if local_result is not None:
                # Offline: use actual hash method from LocalMediaResult
                if hasattr(local_result, 'match_method') and \
                   local_result.match_method.value in ("sha1", "sha256", "sha1_partial", "crc32"):
                    return "exact_hash"
                return "normalized_title"
            if record is not None:
                # Online: use actual provider/record evidence.
                # GH-157 re-QA: ``exact_hash`` is reserved for ACTUAL hash
                # evidence. A ``MetadataRecord`` carries no hash, so a high
                # provider confidence is a title-record match, never a hash
                # match.
                conf = record.confidence if record.confidence is not None else confidence
                # High-confidence exact provider record hit
                if conf >= 0.99:
                    return "provider_record"
                # Provider-specific confidence tiers
                if conf >= 0.95:
                    return "provider_record"
                if conf >= 0.80:
                    return "normalized_title"
                return "fuzzy_title"
            # Pure fallback (shouldn't happen for found/needs_review)
            if confidence >= 0.95:
                return "provider_record"
            if confidence >= 0.80:
                return "normalized_title"
            return "fuzzy_title"
        # ---------------------------------------------------------------------

        c = {
            "mode": result.mode,
            "kind": result.kind,
            "status": result.status,
            "provider": result.record.provider if result.record else None,
            "consulted": list(result.consulted),
            "confidence": result.confidence,
            # --- DEF-4 FIX: single source of truth for match_type --------------
            "match_type": _compute_match_type(
                record=result.record,
                local_result=result.local_result,
                confidence=result.confidence,
            ),
            # -------------------------------------------------------------------
            "why": [],
        }
        if result.record:
            rec = result.record
            c["title"] = rec.canonical_title
            c["year"] = rec.year
            c["developer"] = rec.developer
            c["publisher"] = rec.publisher
            c["description"] = (rec.description or "")[:300]
            c["artwork_url"] = rec.artwork_url
            c["why"].append(f"matched via {rec.provider or 'unknown'}")
        if result.local_result:
            lr = result.local_result
            c["local_cached_path"] = str(lr.cached_path) if lr.cached_path else None
            c["local_category"] = lr.category
            c["local_outcome"] = lr.outcome
            c["local_review_reason"] = lr.manual_review_reason
            c["match_type"] = (
                "exact_hash" if lr.match_method.value in ("sha1", "sha256")
                else "normalized_title"
            )
            c["why"].append(f"local match: {lr.outcome}")
        # Source state info
        if result.local_source_state:
            c["source_state"] = list(result.local_source_state)
        if result.error:
            c["error"] = result.error
        candidates.append(c)
    return candidates


def _run_online_once(ctx: LookupContext) -> "LookupResult":
    """Run online lookup; handle config resolution inside."""
    try:
        from .paths import resolve_config
        paths_cfg, source = resolve_config()
        ctx_resolved = LookupContext(
            query=ctx.query,
            release_key=ctx.release_key,
            title=ctx.title,
            disk_stems=list(ctx.disk_stems),
            cache_dir=paths_cfg.metadata_cache_dir,
            curated_dir=paths_cfg.curated_metadata_dir,
            config_path=source.config_path,
            opener=ctx.opener,
            timeout=ctx.timeout,
            db_path=ctx.db_path,
            provider_enabled=ctx.provider_enabled,
            provider_fields=ctx.provider_fields,
        )
    except Exception:
        ctx_resolved = ctx
    return _online_lookup(MODE_ONLINE, ctx_resolved)


def _run_offline_once(ctx: LookupContext) -> "LookupResult":
    """Run offline lookup."""
    return _offline_lookup(MODE_OFFLINE, ctx)


def _run_dat_once(ctx: LookupContext) -> list[dict]:
    """Collect candidates from the DAT metadata source index.

    Returns a list of candidate dicts or empty list if unavailable.
    """
    candidates: list[dict] = []
    db_path = ctx.db_path
    if db_path is None:
        return candidates
    try:
        from .metadata_source import MetadataSourceManager
        from .manual_lookup import candidates_from_sources
        manager = MetadataSourceManager(db_path)
        # Use the query/title for source search.
        query = ctx.query or ctx.title or ""
        if query:
            candidates = candidates_from_sources(manager, title=query)
    except Exception:
        pass
    return candidates


def _collect_all_candidates(ctx: LookupContext) -> LookupResultCollection:
    """Run ALL available lookup modes in parallel and merge candidates.

    Online, offline, and DAT index are launched concurrently. Results are
    deduplicated by normalized title and ranked by confidence.
    Per-provider diagnostics are included in the result for display.
    """
    online_fut = None
    offline_fut = None
    dat_fut = None
    errors: list[str] = []
    provider_diagnostics: list[dict] = []
    online_r: LookupResult = LookupResult(mode="online", kind=KIND_ONLINE, provider_ids=[])
    offline_r: LookupResult = LookupResult(mode="offline", kind=KIND_OFFLINE, provider_ids=[])

    with ThreadPoolExecutor(max_workers=4) as pool:
        online_fut = pool.submit(_run_online_once, ctx)
        offline_fut = pool.submit(_run_offline_once, ctx)
        dat_fut = pool.submit(_run_dat_once, ctx)

    # Process online result
    online_candidates: list[dict] = []
    try:
        online_r = online_fut.result(timeout=60)
        online_ok = True
        online_candidates = _result_to_candidate(online_r)
        # Collect provider diagnostics from events.
        for ev in online_r.consulted:
            if isinstance(ev, dict):
                provider_diagnostics.append(ev)
    except Exception as exc:
        online_ok = False
        errors.append(f"Online lookup failed: {exc}")

    # Process offline result
    offline_candidates: list[dict] = []
    try:
        offline_r = offline_fut.result(timeout=60)
        offline_ok = True
        offline_candidates = _result_to_candidate(offline_r)
    except Exception as exc:
        offline_ok = False
        errors.append(f"Offline lookup failed: {exc}")

    # Process DAT candidates
    dat_candidates: list[dict] = []
    try:
        dat_candidates = dat_fut.result(timeout=60) if dat_fut else []
    except Exception as exc:
        errors.append(f"DAT lookup failed: {exc}")

    # Merge and rank all candidates.
    all_candidates = _merge_candidates(online_candidates, offline_candidates)
    all_candidates = _merge_candidates(all_candidates, dat_candidates)

    # Append status bands for any no_match/error results as diagnostics.
    if online_r.status == "no_match":
        provider_diagnostics.append({
            "provider": "online",
            "status": "no_match",
            "message": online_r.error or "no online provider matched",
        })
    if offline_r.status == "no_match":
        provider_diagnostics.append({
            "provider": "offline",
            "status": "no_match",
            "message": offline_r.error or "no offline source matched",
        })
    if not online_ok:
        provider_diagnostics.append({
            "provider": "online",
            "status": "error",
            "message": f"Online lookup failed: {errors[-1]}" if errors else "unknown",
        })

    return LookupResultCollection(
        candidates=all_candidates,
        online_ok=online_ok,
        offline_ok=offline_ok,
        errors=errors,
        provider_diagnostics=provider_diagnostics,
    )
