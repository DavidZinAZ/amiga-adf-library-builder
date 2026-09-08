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
    try:
        record, source, events = lookup_metadata(
            title,
            cache_dir=cache_dir,
            curated_dir=curated_dir,
            group=_group_for_lookup(ctx),
            opener=ctx.opener,
            timeout=ctx.timeout,
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


def run_lookup(mode: str, ctx: LookupContext) -> LookupResult:
    """Route a lookup to the correct provider class and run it.

    This is the shared entry point for Online and Offline lookups. The routing
    decision (which provider class) is made once, here, via
    :func:`classify_lookup_mode`; the worker and the GUI both call this.
    """
    kind = classify_lookup_mode(mode)
    if kind == KIND_OFFLINE:
        return _offline_lookup(mode, ctx)
    return _online_lookup(mode, ctx)
