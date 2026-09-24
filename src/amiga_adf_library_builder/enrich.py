"""Metadata and artwork enrichment with persistent provenance-aware caching."""
from __future__ import annotations

import mimetypes
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timezone
import hashlib
import json
import threading
import warnings
from pathlib import Path
from typing import Callable, Optional

from . import artwork as artwork_mod
from .logging_utils import redact
from .metadata import MetadataRecord, cache_key, guard_url, lookup_metadata
from .metadata_source import MetadataSourceManager
from .playmatch import PlaymatchMatchMethod
from .hasheous import HasheousMatchMethod
from .igdb import IgdbMatchMethod
from . import screenscraper as ss_mod
from .models import ReleaseGroup, ScanRecord
from .utils import write_json_atomic, now_iso as _now_iso
from .naming import _sanitize
from .nfo_render import render_gotek_nfo
import os

# Retained for exporter-gate compatibility. Artwork policy is aspect-fit within
# 150x150, never cropped or upscaled; there is no minimum source dimension.
VERIFIED_ARTWORK_WIDTH: Optional[int] = 150
VERIFIED_ARTWORK_HEIGHT: Optional[int] = 150



@dataclass
class ReviewItem:
    """A persisted review item produced by enrichment analysis.

    Each metadata_relevance_review, local_media_review, or per-provider
    review event maps to exactly one ReviewItem, which is written to
    the review/ directory with full evidence and appears in review_routed.
    """

    source: str                    # local_media | metadata-online | <provider>
    provider: str                  # provider name (or "system" for local-media)
    candidate_title: str           # candidate title or path
    score: float                   # confidence/relevance score
    reason: str                    # machine-readable reason category
    evidence: list[str]            # human-readable evidence strings
    release_key: str               # the release this review is for
    routed_at: str                 # ISO timestamp

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "provider": self.provider,
            "candidate_title": self.candidate_title,
            "score": self.score,
            "reason": self.reason,
            "evidence": list(self.evidence),
            "release_key": self.release_key,
            "routed_at": self.routed_at,
        }


@dataclass
class EnrichResult:
    nfo_path: Optional[Path]
    artwork_master: Optional[Path]
    artwork_resized: Optional[Path]
    resized: bool
    notes: list[str]
    metadata_path: Optional[Path] = None
    provider: str = ""
    artwork_missing: bool = False
    events: list = field(default_factory=list)
    # DAT/local metadata results per release, with source identifiers.
    # Each entry: {"source_id", "source_name", "match_type", "title", "matched"}.
    dat_results: list = field(default_factory=list)
    # Cross-provider fail-safe flag. Set True when two enabled hash-first identity
    # providers (Playmatch + Hasheous) resolve the SAME sha256 to DISAGREEING
    # exact-hash identities; the group is routed to manual review rather than
    # silently accepting a winner. Additive; defaults False for all existing paths.
    needs_manual_review: bool = False
    # (GH-99) Canonical metadata match confidence from the resolved
    # MetadataRecord, when one was found. None when no metadata resolved.
    # This is the value the Preview & Curation "Confidence" column should show;
    # it is never guessed and is preserved verbatim from the provider record.
    metadata_confidence: Optional[float] = None
    # (GH-164) Review items produced during enrichment. Each review
    # event generates exactly one ReviewItem that persists through
    # to the review/ directory and appears in review_routed.
    review_items: list[ReviewItem] = field(default_factory=list)


class EnrichCategory(str, Enum):
    """Structured per-group diagnostic categories (structured logging).

    Each value maps to an explicit outcome the operator must be able to see in
    the per-run log: metadata cache state, metadata/artwork lookup results, and
    artwork success/failure reasons.
    """

    METADATA_LOOKUP = "metadata_lookup"
    CACHE_HIT = "cache_hit"
    CACHE_MISS = "cache_miss"
    CACHE_REFRESH = "cache_refresh"
    METADATA_NOT_FOUND = "metadata_not_found"
    ARTWORK_LOOKUP = "artwork_lookup"
    ARTWORK_URL_NOT_FOUND = "artwork_url_not_found"
    ARTWORK_DOWNLOAD_FAILED = "artwork_download_failed"
    ARTWORK_INVALID_IMAGE = "artwork_invalid_image"
    ARTWORK_RESIZE_FAILED = "artwork_resize_failed"
    ARTWORK_GENERATED = "artwork_generated"
    ARTWORK_SKIPPED = "artwork_skipped"
    LOCAL_MEDIA = "local_media"
    LOCAL_MEDIA_MISS = "local_media_miss"
    LOCAL_MEDIA_REVIEW = "local_media_review"
    ROUTE_QUARANTINE = "route_quarantine"
    ROUTE_REVIEW = "route_review"
    METADATA_RELEVANCE_REJECTED = "metadata_relevance_rejected"
    METADATA_RELEVANCE_REVIEW = "metadata_relevance_review"
    PLAYMATCH = "playmatch"
    PLAYMATCH_MISS = "playmatch_miss"
    PLAYMATCH_REVIEW = "playmatch_review"
    HASHEOUS = "hasheous"
    HASHEOUS_MISS = "hasheous_miss"
    HASHEOUS_REVIEW = "hasheous_review"
    IGDB = "igdb"
    IGDB_MISS = "igdb_miss"
    IGDB_REVIEW = "igdb_review"
    SCREENSCRAPER = "screenscraper"
    SCREENSCRAPER_MISS = "screenscraper_miss"
    SCREENSCRAPER_REVIEW = "screenscraper_review"
    RETROACHIEVEMENTS = "retroachievements"
    RETROACHIEVEMENTS_MISS = "retroachievements_miss"
    RETROACHIEVEMENTS_REVIEW = "retroachievements_review"
    DAT = "dat"


@dataclass
class EnrichEvent:
    """One structured, machine-classifiable enrichment diagnostic (structured logging).

    Carried on :class:`EnrichResult` and rendered (redacted) into the per-run
    log so failures are diagnosable by category rather than buried in prose.
    """

    category: EnrichCategory
    detail: str = ""
    url: Optional[str] = None
    cache: Optional[str] = None  # hit | miss | refresh | negative
    ok: bool = True
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "category": self.category.value,
            "detail": self.detail,
            "url": self.url,
            "cache": self.cache,
            "ok": self.ok,
            "error": self.error,
        }


def _clean(value: Optional[str], fallback: str = "Unknown") -> str:
    return (value or "").strip() or fallback


def _wrap(text: str, width: int = 78) -> list[str]:
    import textwrap
    return textwrap.wrap(" ".join(text.split()), width=width) or []





def _is_invalid_image_error(exc: Exception) -> bool:
    """Return True if ``exc`` indicates the artwork master is not a decodable image.

    A corrupt/truncated/unsupported file (or a file that is not an image at all)
    fails when Pillow opens or identifies it. That is the ``ARTWORK_INVALID_IMAGE``
    case, distinct from a genuine resize/processing-cap failure, so the operator
    can diagnose an unusable master without confusing it with a processing error.
    """
    # PIL raises UnidentifiedImageError (subclass of OSError) at open/decode time.
    try:
        from PIL import Image
        if isinstance(exc, Image.UnidentifiedImageError):
            return True
    except Exception:
        pass
    # Some environments/older Pillow raise OSError("cannot identify image file").
    msg = str(exc).lower()
    if isinstance(exc, OSError) and "cannot identify image" in msg:
        return True
    return False


def _build_provenance_record(group: ReleaseGroup, scans: dict[str, ScanRecord],
                             metadata: Optional[MetadataRecord], *, mode: str,
                             approval_sources: Optional[list]) -> dict:
    """Build the durable provenance record (Gotek NFO contract).

    This is the structured, machine-readable companion to the human-readable
    ``build_provenance_text`` output. It captures everything that used to be
    embedded in the Gotek-facing NFO but must now live outside it: original
    source filenames, SHA-256 hashes and sizes, manual-approval URLs/roles,
    metadata provider and source URL, retrieval timestamp, confidence, query,
    and enrichment mode.
    """
    from datetime import datetime, timezone

    rep = group.records[0] if group.records else None
    title = metadata.canonical_title if metadata else group.title
    year = (metadata.year if metadata else "") or (rep.year if rep else "")
    publisher = (metadata.publisher if metadata else "") or (rep.publisher if rep else "")

    source_images = []
    for record in group.records:
        scan = scans.get(record.source_filename)
        source_images.append({
            "filename": record.source_filename,
            "format": (record.ext or "").upper(),
            "sha256": scan.sha256 if scan else None,
            "size": scan.size if scan else None,
        })

    metadata_provenance = None
    if metadata:
        metadata_provenance = {
            "provider": metadata.provider,
            "source_url": metadata.source_url or None,
            "provider_id": metadata.provider_id or None,
            "retrieved_at": metadata.retrieved_at or None,
            "confidence": metadata.confidence,
            "query": metadata.query,
            "artwork_url": metadata.artwork_url or None,
            "artwork_source_url": metadata.artwork_source_url or None,
            "artwork_provider": metadata.artwork_provider or None,
            "relevance_category": metadata.relevance_category or None,
            "relevance_confidence": (metadata.relevance_confidence or 0.0) if metadata.relevance_category else None,
            "relevance_evidence": (metadata.relevance_evidence or []) if metadata.relevance_category else [],
        }

    return {
        "schema": "gotek-nfo-provenance/1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "release_key": group.release_key,
        "title": title or group.title or "Unknown",
        "year": year or None,
        "publisher": publisher or None,
        "edition": group.edition or None,
        "group": group.group or None,
        "chipset": group.chipset or None,
        "language": group.language or None,
        "version": group.version or None,
        "alt_marker": group.alt_marker or None,
        "trainer": bool(rep.trainer) if rep else False,
        "disks": len(group.disks),
        "specials": len(group.specials),
        "description": metadata.description if metadata else None,
        "source_images": source_images,
        "approved_sources": [
            {"role": (su.get("role") or "reference"), "url": su.get("url") or None}
            for su in (approval_sources or [])
            if su.get("url")
        ],
        "metadata_provenance": metadata_provenance,
        "enrichment_mode": mode,
    }


def build_provenance_text(group: ReleaseGroup, scans: dict[str, ScanRecord],
                   metadata: Optional[MetadataRecord] = None,
                   *, mode: str = "offline",
                   approval_sources: Optional[list] = None) -> str:
    """Render the durable, full provenance record for one release (Gotek NFO contract).

    This is NOT the Gotek-facing display NFO. The Gotek NFO is rendered by
    ``nfo_render.render_gotek_nfo`` and is limited to ``Title:`` / ``Blurb:``
    at <= 512 bytes. The text returned here is written to a durable per-release
    sidecar (``<basename>.provenance.txt``) under ``assets/nfo`` so that all
    detailed source / metadata / manual-approval provenance survives outside
    the Gotek-facing NFO.

    ``approval_sources`` is a list of ``{"url": str, "role": str}`` entries
    from a ratified manual-approval record (manual-approval feature). When supplied, an
    ``Approved source:`` provenance line is emitted PER ROLE, citing the exact
    operator URL verbatim. Roles with no supplied URL are OMITTED -- the system
    never guesses or synthesizes a URL (ratified section 5).
    """
    from datetime import datetime, timezone

    rep = group.records[0] if group.records else None
    title = metadata.canonical_title if metadata else group.title
    year = (metadata.year if metadata else "") or (rep.year if rep else "")
    publisher = (metadata.publisher if metadata else "") or (rep.publisher if rep else "")
    lines = [
        "Amiga ADF Library Builder — Release Provenance (durable, not Gotek display NFO)",
        "=" * 52,
        f"Title: {_clean(title)}",
    ]
    if group.edition:
        lines.append(f"Edition: {group.edition}")
    if year:
        lines.append(f"Year: {year}")
    if metadata and metadata.developer:
        lines.append(f"Developer: {metadata.developer}")
    if publisher:
        lines.append(f"Publisher: {publisher}")
    if metadata and metadata.genres:
        lines.append(f"Genre: {', '.join(metadata.genres)}")
    if metadata and metadata.platforms:
        lines.append(f"Platforms: {', '.join(metadata.platforms)}")
    if group.chipset:
        lines.append(f"Chipset: {group.chipset}")
    if group.language:
        lines.append(f"Language: {group.language}")
    if group.version:
        lines.append(f"Version: {group.version}")
    if group.group:
        lines.append(f"Release group: {group.group}")
    if group.alt_marker:
        lines.append(f"Alternate dump: {group.alt_marker}")
    if rep and rep.trainer:
        lines.append("Trainer: yes")
    lines.append(f"Disk set: {len(group.disks)} main disk(s)" + (f" + {len(group.specials)} special" if group.specials else ""))

    if metadata and metadata.description:
        lines.extend(["", "Description:"])
        lines.extend(_wrap(metadata.description))

    # manual-approval feature provenance: emit one exact per-role "Approved source:" line for
    # each supplied approval URL. Omit roles that were not supplied (no guessing).
    approval_sources = list(approval_sources or [])
    if approval_sources:
        lines.extend(["", "Approved source:"])
        for su in approval_sources:
            url = su.get("url", "")
            role = su.get("role", "") or "reference"
            if not url:
                continue
            lines.append(f"- ({role}) {url}")

    lines.extend(["", "Source images:"])
    for record in group.records:
        scan = scans.get(record.source_filename)
        if scan:
            lines.append(f"- {record.source_filename}")
            lines.append(f"  SHA256: {scan.sha256}  Size: {scan.size}  Format: {record.ext.upper()}")
        else:
            lines.append(f"- {record.source_filename} (no scan record)")

    lines.extend(["", "Metadata provenance:"])
    if metadata:
        lines.append(f"- Provider: {metadata.provider}")
        lines.append(f"- Source: {metadata.source_url or 'not supplied'}")
        lines.append(f"- Provider ID: {metadata.provider_id or 'not supplied'}")
        lines.append(f"- Retrieved: {metadata.retrieved_at}")
        lines.append(f"- Match confidence: {metadata.confidence:.2f}")
        lines.append(f"- Query: {metadata.query}")
        if metadata.relevance_category:
            ev = "; ".join(metadata.relevance_evidence or [])
            lines.append(
                f"- Relevance: {metadata.relevance_category} "
                f"(conf {metadata.relevance_confidence:.2f}) [{ev}]"
            )
        if metadata.artwork_url:
            lines.append(f"- Artwork image: {metadata.artwork_url}")
            lines.append(f"- Artwork page: {metadata.artwork_source_url or metadata.source_url or 'not supplied'}")
            lines.append(f"- Artwork provider: {metadata.artwork_provider or 'not supplied'}")
    else:
        lines.append("- No online or curated record available; filename-derived fields only.")
    lines.append(f"Enrichment mode: {mode}")
    lines.append(f"Generated at: {datetime.now(timezone.utc).isoformat()}")
    return "\n".join(lines) + "\n"


def _download_artwork(record: MetadataRecord, dest_dir: Path, title: str,
                      *, timeout: float = 30.0, max_bytes: int = 12_000_000) -> Optional[Path]:
    if not record.artwork_url:
        return None
    # Guard against fetching non-public address space. _download_artwork always
    # performs a real network request, so resolve DNS here.
    guard_url(record.artwork_url, resolve=True)
    request = urllib.request.Request(record.artwork_url, headers={"User-Agent": f"AmigaADFLibraryBuilder/{__import__('amiga_adf_library_builder._version', fromlist=['__version__']).__version__}", "Accept": "image/*"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        content_type = response.headers.get_content_type()
        data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise RuntimeError("artwork download exceeds 12 MB safety limit")
    if not data:
        raise RuntimeError("artwork download returned an empty body")
    if content_type and not content_type.startswith("image/"):
        raise RuntimeError(f"artwork URL returned non-image content type: {content_type}")
    suffix = mimetypes.guess_extension(content_type) or Path(urllib.parse.urlparse(record.artwork_url).path).suffix
    suffix = suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
        suffix = ".jpg"
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{cache_key(title)}{suffix}"
    if not dest.exists() or dest.read_bytes() != data:
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(dest)
    sidecar = dest.with_suffix(dest.suffix + ".source.json")
    provenance = {
        "image_url": record.artwork_url,
        "source_page": record.artwork_source_url or record.source_url,
        "provider": record.artwork_provider or record.provider,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
    }
    sidecar.write_text(json.dumps(provenance, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return dest


def _find_existing_master(group: ReleaseGroup, artwork_original_dir: Path) -> Optional[Path]:
    return artwork_mod.find_artwork_master(group, artwork_original_dir)


def _resolve_local_media_master(group: ReleaseGroup, provider) -> tuple[Optional[Path], list]:
    """Resolve a master from the configured local-media provider (provider order #2).

    The provider copies the selected source into the application's OWN cache and
    returns a :class:`LocalMediaResult`. On a confident match the cached master
    path is returned and structured diagnostics are emitted; an uncertain match
    is surfaced as a manual-review event (nothing is silently accepted); a miss
    emits a quiet miss event. Never mutates the source library (the provider
    guarantees read-only access).

    Emits detailed per-candidate diagnostics for QA and security review:
    each candidate is logged with its matching key/strategy, score, and
    rejection reason (matched/rejected/unmatched).

    GH-49: Supports three outcomes:
    - auto_match: cached and ready
    - needs_review: flagged for manual review, not cached
    - no_match: nothing found
    """
    from . import local_media as lm

    if provider is None:
        return None, []
    events: list = []
    try:
        result = provider.resolve(group)
    except lm.LocalMediaDisabled:
        return None, []
    except Exception as exc:  # defensive: local-media failure must not break enrich
        events.append(EnrichEvent(
            category=EnrichCategory.LOCAL_MEDIA,
            detail="local-media provider raised an error",
            ok=False, error=str(exc),
        ))
        return None, events

    # GH-164 RC6: Replace per-candidate EnrichEvent emission with
    # group-level summaries. Per-candidate audit is retained behind
    # the detailed_diagnostics flag to prevent ~9,600 miss events/run.
    candidates_evaluated = getattr(result, "candidates_evaluated", []) or []
    matched_count = 0
    rejected_count = 0
    unmatched_count = 0
    needs_review_count = 0
    _detail_candidates: list[dict] = []

    for cand_diag in candidates_evaluated:
        method = cand_diag.get("method", "none")
        score = cand_diag.get("score", 0.0)
        path = cand_diag.get("path", "")
        category = cand_diag.get("category", "")
        norm_stem = cand_diag.get("norm_stem", "")

        # GH-49: Determine outcome for this candidate based on result.outcome
        is_auto_match = (
            result.outcome == "auto_match"
            and result.found
            and result.cached_path is not None
            and Path(path).name == Path(result.cached_path).name
            and method == result.match_method.value
        )
        is_manual_lock = (
            result.outcome == "auto_match"
            and result.found
            and result.manual_review_reason == "manually locked (protected from auto-overwrite)"
        )
        is_needs_review = result.outcome == "needs_review" and method == result.match_method.value

        # Track counts and optional detail for debug mode
        _detail: dict = {"method": method, "score": score, "category": category}
        if is_auto_match or is_manual_lock:
            matched_count += 1
            _detail["outcome"] = "matched"
        elif method != "none" and score >= getattr(provider.config, "confidence_threshold", lm.AUTO_ACCEPT_MIN_CONF):
            rejected_count += 1
            _detail["outcome"] = "rejected"
            _detail["reason"] = "lower priority category"
        elif is_needs_review:
            needs_review_count += 1
            _detail["outcome"] = "needs_review"
            _detail["reason"] = result.manual_review_reason or "requires review"
        elif method in ("fuzzy", "fuzzy_manual"):
            rejected_count += 1
            _detail["outcome"] = "rejected"
            _detail["reason"] = "below confidence threshold"
        else:
            unmatched_count += 1
            _detail["outcome"] = "unmatched"

        # Only keep per-candidate detail when debug diagnostics are enabled
        if getattr(provider.config, "detailed_diagnostics", False):
            _detail["path"] = str(path)
            _detail["norm_stem"] = norm_stem
            _detail_candidates.append(_detail)

    # Group-level summary event (replaces ~9,600 per-candidate events)
    events.append(EnrichEvent(
        category=EnrichCategory.LOCAL_MEDIA,
        detail=(
            f"local-media scan: {len(candidates_evaluated)} candidates evaluated; "
            f"{matched_count} matched; {rejected_count} rejected; "
            f"{needs_review_count} needs review; {unmatched_count} unmatched"
        ),
        cache="hit" if result.outcome == "auto_match" else "miss", ok=True,
    ))
    if unmatched_count > 0 and not getattr(provider.config, "detailed_diagnostics", False):
        events.append(EnrichEvent(
            category=EnrichCategory.LOCAL_MEDIA_MISS,
            detail=f"{unmatched_count} candidates unmatched (detail suppressed; enable detailed_diagnostics for per-candidate audit)",
            cache="miss", ok=True,
        ))
    elif getattr(provider.config, "detailed_diagnostics", False) and _detail_candidates:
        # Emit detailed per-candidate audit only when explicitly enabled
        for _detail in _detail_candidates[:50]:  # Cap at 50 to prevent abuse
            events.append(EnrichEvent(
                category=EnrichCategory.LOCAL_MEDIA_MISS,
                detail=f"candidate detail: {_detail}",
                cache="miss", ok=True,
            ))

    # GH-49: Handle three outcomes
    if result.outcome == "auto_match" and result.found and result.cached_path is not None:
        return Path(result.cached_path), events
    if result.outcome == "needs_review":
        events.append(EnrichEvent(
            category=EnrichCategory.LOCAL_MEDIA_REVIEW,
            detail=(
                f"uncertain match routed to manual review: "
                f"{result.manual_review_reason or 'low confidence'}"
            ),
            cache="miss", ok=False,
        ))
        return None, events
    events.append(EnrichEvent(
        category=EnrichCategory.LOCAL_MEDIA_MISS,
        detail="no local-media match for this release",
        cache="miss", ok=True,
    ))
    return None, events


def resize_artwork(master: Path, artwork_processed_dir: Path,
                   width: Optional[int] = None, height: Optional[int] = None) -> Path:
    """Compatibility wrapper around the verified aspect-fit artwork processor."""
    if not width or not height:
        raise RuntimeError("Artwork resize blocked: verified dimensions are unresolved")
    data = artwork_mod.process_artwork_bytes(master, target_w=width, target_h=height)
    out_dir = Path(artwork_processed_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"{Path(master).stem}-gotek.jpg"
    dest.write_bytes(data)
    return dest



def _build_review_items(events: list) -> list:
    """Convert review-category EnrichEvents into ReviewItems (GH-164 RC3).

    Each review event produces exactly one persisted ReviewItem so that
    enrich review events are never silently discarded.
    """
    from .utils import now_iso as _now_iso_func
    items = []
    for ev in events:
        if ev.category not in (
            EnrichCategory.METADATA_RELEVANCE_REVIEW,
            EnrichCategory.LOCAL_MEDIA_REVIEW,
            EnrichCategory.PLAYMATCH_REVIEW,
            EnrichCategory.HASHEOUS_REVIEW,
            EnrichCategory.IGDB_REVIEW,
            EnrichCategory.SCREENSCRAPER_REVIEW,
            EnrichCategory.RETROACHIEVEMENTS_REVIEW,
        ):
            continue
        items.append(ReviewItem(
            source=("metadata-online" if ev.category == EnrichCategory.METADATA_RELEVANCE_REVIEW
                    else "local_media" if ev.category == EnrichCategory.LOCAL_MEDIA_REVIEW
                    else ev.category.value.replace("_review", "-review")),
            provider=ev.category.value.replace("_", "-"),
            candidate_title=ev.detail or "",
            score=0.0,
            reason=ev.error or "review",
            evidence=[ev.detail or ev.error or ""],
            release_key="",
            routed_at=_now_iso_func(),
        ))
    return items


def enrich_group(group: ReleaseGroup, *, nfo_dir: Path, scans: dict[str, ScanRecord],
                 artwork_original_dir: Path, artwork_processed_dir: Path,
                 metadata_cache_dir: Optional[Path] = None, curated_metadata_dir: Optional[Path] = None,
                 online: bool = False, refresh: bool = False,
                 local_media_provider=None, playmatch_provider=None,
                 hasheous_provider=None, igdb_provider=None,
                 screenscraper_provider=None,
                 retroachievements_provider=None,
                 halloflight_enabled: bool = True,
                 metadata_source_manager: MetadataSourceManager = None,
                 include_artwork: bool = True,
                 cancel_event: Optional[threading.Event] = None,
                 activity: Optional[Callable[[str], None]] = None,
                 library_root: Optional[Path] = None) -> EnrichResult:
    metadata_cache_dir = Path(metadata_cache_dir or (Path(nfo_dir).parent / "metadata-cache"))
    curated_metadata_dir = Path(curated_metadata_dir or (Path(nfo_dir).parent / "metadata-curated"))
    notes: list[str] = []
    events: list[EnrichEvent] = []
    metadata: Optional[MetadataRecord] = None
    provider = "offline"
    metadata_path: Optional[Path] = None

    # (Issue #21) live activity hook: one plain-language, redacted line per
    # notable step, prefixed with the release title. Optional and safe: a
    # missing/failed callback never affects enrichment.
    _title = group.title or group.release_key

    def _act(msg: str) -> None:
        if activity is None:
            return
        try:
            activity(f"{_title}: {redact(str(msg))}")
        except Exception:  # logging must never break enrichment
            pass

    # manual-approval feature: approved source URLs from a matched manual-approval record.
    # Forced as provenance (and as the metadata/artwork source where the role
    # matches). Exact URLs, never guessed.
    approval_sources: list = list(getattr(group, "approved_sources", None) or [])

    # Cached metadata is useful offline; network is used only with --online.
    from .metadata import load_cached
    lookup_title = " ".join(x for x in [group.title or "", group.edition or ""] if x).strip()
    if online:
        events.append(EnrichEvent(
            category=EnrichCategory.METADATA_LOOKUP,
            detail=f"query={lookup_title!r}", cache=("refresh" if refresh else "miss"),
        ))
        _act(
            "Looking up metadata online (refreshing cached copy)."
            if refresh
            else "Looking up metadata online…"
        )
        try:
            metadata, provider, relevance_events = lookup_metadata(
                lookup_title, cache_dir=metadata_cache_dir,
                curated_dir=curated_metadata_dir, refresh=refresh, group=group,
                activity=activity,
                halloflight_enabled=halloflight_enabled,
            )
            # Surface online relevance fall-through decisions as structured
            # diagnostics (bounded: one event per rejected/reviewed candidate).
            for rev in relevance_events:
                if rev["category"] == "rejected":
                    events.append(EnrichEvent(
                        category=EnrichCategory.METADATA_RELEVANCE_REJECTED,
                        detail=(f"{rev['provider']} candidate {rev['canonical_title']!r} "
                                f"rejected: {rev['reason']} (conf {rev['confidence']:.2f})"),
                        url=(metadata.source_url if metadata else None),
                        ok=False, error=rev["reason"],
                    ))
                elif rev["category"] == "review":
                    events.append(EnrichEvent(
                        category=EnrichCategory.METADATA_RELEVANCE_REVIEW,
                        detail=(f"{rev['provider']} candidate {rev['canonical_title']!r} "
                                f"routed to review: {rev['reason']} (conf {rev['confidence']:.2f})"),
                        url=(metadata.source_url if metadata else None),
                        ok=False, error=rev["reason"],
                    ))
            if metadata:
                _act(f"Metadata found (source: {provider}).")
                notes.append(f"metadata lookup: {provider}")
                events.append(EnrichEvent(
                    category=EnrichCategory.METADATA_LOOKUP,
                    detail=f"result=hit provider={provider}",
                    url=(metadata.source_url or None), cache=("refresh" if refresh else "miss"),
                    ok=True,
                ))
                if provider == "cache" and not refresh:
                    events.append(EnrichEvent(
                        category=EnrichCategory.CACHE_HIT,
                        detail="reused cached metadata record", cache="hit",
                    ))
                elif refresh:
                    events.append(EnrichEvent(
                        category=EnrichCategory.CACHE_REFRESH,
                        detail="refreshed cached metadata record", cache="refresh",
                    ))
            else:
                _act("No metadata found from online sources.")
                notes.append("metadata lookup: not-found")
                events.append(EnrichEvent(
                    category=EnrichCategory.METADATA_NOT_FOUND,
                    detail="online lookup returned no record", cache="miss",
                ))
        except Exception as exc:
            _act(f"Online lookup problem: {exc}")
            notes.append(f"metadata lookup failed: {exc}")
            events.append(EnrichEvent(
                category=EnrichCategory.METADATA_NOT_FOUND,
                detail="lookup raised an error", cache=("refresh" if refresh else "miss"),
                ok=False, error=str(exc),
            ))
    else:
        metadata = load_cached(metadata_cache_dir, lookup_title)
        if metadata:
            provider = "cache"
            _act("Using a cached metadata copy (offline).")
            notes.append("reused cached metadata offline")
            events.append(EnrichEvent(
                category=EnrichCategory.CACHE_HIT,
                detail="reused cached metadata record", cache="hit",
            ))
        else:
            _act("No cached metadata available (offline).")
            notes.append("offline; no cached metadata")
            events.append(EnrichEvent(
                category=EnrichCategory.CACHE_MISS,
                detail="offline and no cached metadata record present", cache="miss",
            ))
            events.append(EnrichEvent(
                category=EnrichCategory.METADATA_NOT_FOUND,
                detail="no metadata available offline", cache="negative",
            ))

    if metadata:
        metadata_path = Path(metadata_cache_dir) / f"{cache_key(lookup_title)}.json"

    # manual-approval feature: force approved source URLs into the metadata record's provenance
    # fields when their role matches. Exact URLs; never guessed or overwritten
    # by a network lookup for approved groups.
    for su in approval_sources:
        url = su.get("url", "")
        role = (su.get("role", "") or "reference").lower()
        if not url:
            continue
        if role == "artwork":
            metadata = metadata or MetadataRecord(canonical_title=lookup_title or group.title or "Unknown")
            metadata.artwork_url = url
            metadata.artwork_source_url = metadata.artwork_source_url or url
            metadata.artwork_provider = metadata.artwork_provider or "manual-approval"
        else:  # metadata / reference
            metadata = metadata or MetadataRecord(canonical_title=lookup_title or group.title or "Unknown")
            metadata.source_url = url
    # Optional Playmatch ROM-hash identity resolver. Hash-first; reuses the
    # scanner-computed sha256 (passed via `scans`) and never refetches. A
    # returned provider_id is captured for downstream correlation. Failures are
    # fully non-fatal (the provider already degrades to a NONE result); we only
    # record structured diagnostics and never let it break the run.
    # Cross-provider fail-safe bookkeeping. We DEFER committing each provider's
    # "success" event/note until after BOTH providers have resolved, so a
    # disagreeing authoritative exact-hash identity for the same ROM hash can be
    # routed to manual review instead of silently accepted (issue #11/#12
    # hash-first fail-safe posture). The non-found / manual-review / miss paths
    # below stay fully immediate and unchanged.
    _pm_success_event: Optional[EnrichEvent] = None
    _pm_success_note: Optional[str] = None
    _hs_success_event: Optional[EnrichEvent] = None
    _hs_success_note: Optional[str] = None

    playmatch_result = None
    if playmatch_provider is not None:
        try:
            _act("Trying playmatch identity resolver…")
            playmatch_result = playmatch_provider.resolve(group, scans=scans)
            if playmatch_result is not None:
                if playmatch_result.found:
                    _pm_success_event = EnrichEvent(
                        category=EnrichCategory.PLAYMATCH,
                        detail=(f"resolved via {playmatch_result.match_method.value} "
                                f"conf={playmatch_result.confidence:.2f} "
                                f"provider_id={playmatch_result.provider_id}"),
                        ok=True,
                    )
                    _act(f"Playmatch resolved identity (provider_id: {playmatch_result.provider_id}).")
                    if playmatch_result.provider_id:
                        _pm_success_note = (
                            f"playmatch provider_id: {playmatch_result.provider_id}"
                        )
                elif playmatch_result.needs_manual_review:
                    events.append(EnrichEvent(
                        category=EnrichCategory.PLAYMATCH_REVIEW,
                        detail=(f"playmatch needs manual review: "
                                f"{playmatch_result.manual_review_reason}"),
                        ok=False, error=playmatch_result.manual_review_reason,
                    ))
                    notes.append("playmatch: routed to manual review")
                    _act("Playmatch: routed to manual review.")
                else:
                    _pm_te = getattr(playmatch_result, "transport_error", None)
                    reason = f"transport error: {_pm_te}" if _pm_te else "no hash match"
                    _act(f"Playmatch: no match ({reason}).")
                    events.append(EnrichEvent(
                        category=EnrichCategory.PLAYMATCH_MISS,
                        detail="playmatch: no identity match"
                        + (f" (transport: {_pm_te})" if _pm_te else ""),
                        cache="miss",
                        ok=not _pm_te,
                        error=_pm_te,
                    ))
        except Exception as exc:  # defensive: never break enrich
            events.append(EnrichEvent(
                category=EnrichCategory.PLAYMATCH_MISS,
                detail=f"playmatch resolve raised: {exc}",
                ok=False, error=str(exc),
            ))
            _act(f"Playmatch error: {exc}.")

    # Optional Hasheous ROM-hash identity resolver. Hash-first; reuses the
    # scanner-computed sha256 (passed via `scans`) and never refetches. A
    # returned provider_id / external_ids is captured for downstream correlation.
    # Failures are fully non-fatal (the provider already degrades to a NONE
    # result); we only record structured diagnostics and never let it break the
    # run. Exact-hash identity from Hasheous outranks any weaker signal already
    # present (mirrors the Playmatch contract; the Hasheous provider itself
    # ranks EXACT_HASH highest, so when both providers are enabled the stronger
    # hash identity wins deterministically).
    hasheous_result = None
    if hasheous_provider is not None:
        try:
            _act("Trying hasheous identity resolver…")
            hasheous_result = hasheous_provider.resolve(group, scans=scans)
            if hasheous_result is not None:
                if hasheous_result.found:
                    _hs_success_event = EnrichEvent(
                        category=EnrichCategory.HASHEOUS,
                        detail=(
                            f"resolved via {hasheous_result.match_method.value} "
                            f"conf={hasheous_result.confidence:.2f} "
                            f"provider_id={hasheous_result.provider_id}"
                            + (f" external_ids={hasheous_result.external_ids}"
                               if hasheous_result.external_ids else "")
                        ),
                        ok=True,
                    )
                    _act(f"Hasheous resolved identity (provider_id: {hasheous_result.provider_id}).")
                    if hasheous_result.provider_id:
                        _hs_success_note = (
                            f"hasheous provider_id: {hasheous_result.provider_id}"
                        )
                elif hasheous_result.needs_manual_review:
                    events.append(EnrichEvent(
                        category=EnrichCategory.HASHEOUS_REVIEW,
                        detail=(
                            f"hasheous needs manual review: "
                            f"{hasheous_result.manual_review_reason}"
                        ),
                        ok=False, error=hasheous_result.manual_review_reason,
                    ))
                    notes.append("hasheous: routed to manual review")
                    _act("Hasheous: routed to manual review.")
                else:
                    _hs_te = getattr(hasheous_result, "transport_error", None)
                    reason = f"transport error: {_hs_te}" if _hs_te else "no hash match"
                    _act(f"Hasheous: no match ({reason}).")
                    events.append(EnrichEvent(
                        category=EnrichCategory.HASHEOUS_MISS,
                        detail="hasheous: no identity match"
                        + (f" (transport: {_hs_te})" if _hs_te else ""),
                        cache="miss",
                        ok=not _hs_te,
                        error=_hs_te,
                    ))
        except Exception as exc:  # defensive: never break enrich
            events.append(EnrichEvent(
                category=EnrichCategory.HASHEOUS_MISS,
                detail=f"hasheous resolve raised: {exc}",
                ok=False, error=str(exc),
            ))
            _act(f"Hasheous error: {exc}.")

    # (GH-164 RC4) DAT/local metadata participation.
    # Queryed after exact-hash providers and before online metadata,
    # with identity outranking fuzzy online title matches.
    # Each disk's sha256 is queried independently so multi-disk
    # releases can match on any disk.
    _dat_events: list[EnrichEvent] = []
    _dat_results: list[dict] = []  # per-disk match results with source info
    if metadata_source_manager is not None:
        try:
            _act("Checking DAT/local metadata sources…")
            _sources = metadata_source_manager.list_sources()
            _enabled_sources = [s for s in _sources if s.enabled]
            if _enabled_sources:
                _dat_events.append(EnrichEvent(
                    category=EnrichCategory.DAT,
                    detail=f"dat_loaded sources={len(_enabled_sources)} entries={sum(s.entry_count for s in _enabled_sources)}",
                    cache="hit", ok=True,
                ))
                # Query by sha256 for each disk (exact-hash precedence)
                _sha_matches = []
                for _rec in scans.values():
                    _sha = getattr(_rec, "sha256", None)
                    if _sha:
                        _disk_matches = metadata_source_manager.lookup_by_sha256(_sha)
                        if _disk_matches:
                            for _entry in _disk_matches:
                                _sha_matches.append({
                                    "entry": _entry,
                                    "sha256": _sha,
                                    "source_id": _entry.source_id,
                                    "source_name": _entry.source_id,
                                    "match_type": "sha256",
                                })
                if _sha_matches:
                    # Use the first sha256 match; track all for diagnostics
                    _dat_entry = _sha_matches[0]["entry"]
                    metadata = MetadataRecord(
                        canonical_title=_dat_entry.title or lookup_title or group.title or "Unknown",
                        description=_dat_entry.title,
                        year=_dat_entry.year,
                        publisher=_dat_entry.publisher,
                        platforms=[],
                    )
                    metadata.provider = "dat"
                    metadata.confidence = 1.0
                    for _m in _sha_matches:
                        _dat_events.append(EnrichEvent(
                            category=EnrichCategory.DAT,
                            detail=f"dat_hash_match source={_m['source_id']} title={_m['entry'].title!r} disk_sha256={_m['sha256'][:8]}",
                            cache="hit", ok=True,
                        ))
                        _dat_results.append({
                            "source_id": _m["source_id"],
                            "source_name": _m["source_name"],
                            "match_type": "sha256",
                            "title": _m["entry"].title,
                            "matched": True,
                        })
                    _act(f"DAT hash match: {_dat_entry.title}")
                else:
                    # Fallback: title-based lookup
                    _title_matches = metadata_source_manager.lookup_by_title(
                        lookup_title or group.title or ""
                    )
                    if _title_matches:
                        _dat_entry = _title_matches[0]
                        _dat_events.append(EnrichEvent(
                            category=EnrichCategory.DAT,
                            detail=f"dat_title_candidate source={_dat_entry.source_id} title={_dat_entry.title!r}",
                            cache="hit", ok=True,
                        ))
                        _dat_results.append({
                            "source_id": _dat_entry.source_id,
                            "source_name": _dat_entry.source_id,
                            "match_type": "title",
                            "title": _dat_entry.title,
                            "matched": True,
                        })
                    else:
                        _dat_events.append(EnrichEvent(
                            category=EnrichCategory.DAT,
                            detail="dat_no_match",
                            cache="miss", ok=True,
                        ))
                        _dat_results.append({
                            "source_id": "",
                            "source_name": "",
                            "match_type": "",
                            "title": "",
                            "matched": False,
                        })
            else:
                _dat_events.append(EnrichEvent(
                    category=EnrichCategory.DAT,
                    detail="dat_source_disabled",
                    cache="negative", ok=True,
                ))
        except Exception as exc:
            _dat_events.append(EnrichEvent(
                category=EnrichCategory.DAT,
                detail=f"dat_unavailable: {exc}",
                cache="negative", ok=False, error=str(exc),
            ))
    else:
        _dat_events.append(EnrichEvent(
            category=EnrichCategory.DAT,
            detail="dat_not_configured",
            cache="negative", ok=True,
        ))

    # Optional IGDB metadata/artwork provider. Title + Amiga platform search.
    # Non-hash-first; runs independently of Playmatch/Hasheous.
    _igdb_success_event: Optional[EnrichEvent] = None
    _igdb_success_note: Optional[str] = None
    igdb_result = None
    if igdb_provider is not None:
        try:
            _act("Trying igdb metadata lookup…")
            igdb_result = igdb_provider.resolve(group)
            if igdb_result is not None:
                if igdb_result.found:
                    _igdb_success_event = EnrichEvent(
                        category=EnrichCategory.IGDB,
                        detail=(f"resolved via {igdb_result.match_method.value} "
                                f"conf={igdb_result.confidence:.2f} "
                                f"provider_id={igdb_result.provider_id}"),
                        ok=True,
                    )
                    _act(f"Igdb resolved identity (provider_id: {igdb_result.provider_id}).")
                    if igdb_result.provider_id:
                        _igdb_success_note = (
                            f"igdb provider_id: {igdb_result.provider_id}"
                        )
                    # Merge IGDB metadata into the main metadata record if it improves things
                    if igdb_result.metadata and (not metadata or igdb_result.confidence > (metadata.confidence or 0.0)):
                        # Use IGDB metadata as primary source
                        if metadata is None:
                            metadata = MetadataRecord(canonical_title=lookup_title or group.title or "Unknown")
                        # Merge fields - IGDB is authoritative for online metadata
                        md = igdb_result.metadata
                        if md.get("canonical_title"):
                            metadata.canonical_title = md["canonical_title"]
                        if md.get("description"):
                            metadata.description = md["description"]
                        if md.get("year"):
                            metadata.year = md["year"]
                        if md.get("genres"):
                            metadata.genres = md["genres"]
                        if md.get("platforms"):
                            metadata.platforms = md["platforms"]
                        if md.get("source_url"):
                            metadata.source_url = md["source_url"]
                        if md.get("provider_id"):
                            metadata.provider_id = md["provider_id"]
                        metadata.provider = "igdb"
                        metadata.confidence = max(metadata.confidence, igdb_result.confidence)
                        # Artwork URLs from IGDB
                        if md.get("artwork_urls"):
                            metadata.artwork_page_urls = md["artwork_urls"]
                            metadata.artwork_provider = md.get("artwork_provider", "igdb")
                        # External IDs
                        if igdb_result.external_ids:
                            # Store external IDs for potential downstream use
                            pass
                elif igdb_result.needs_manual_review:
                    events.append(EnrichEvent(
                        category=EnrichCategory.IGDB_REVIEW,
                        detail=(f"igdb needs manual review: "
                                f"{igdb_result.manual_review_reason}"),
                        ok=False, error=igdb_result.manual_review_reason,
                    ))
                    notes.append("igdb: routed to manual review")
                    _act("Igdb: routed to manual review.")
                else:
                    _igdb_te = getattr(igdb_result, "transport_error", None)
                    reason = f"transport error: {_igdb_te}" if _igdb_te else "no title match"
                    _act(f"Igdb: no match ({reason}).")
                    events.append(EnrichEvent(
                        category=EnrichCategory.IGDB_MISS,
                        detail="igdb: no identity match"
                        + (f" (transport: {_igdb_te})" if _igdb_te else ""),
                        cache="miss",
                        ok=not _igdb_te,
                        error=_igdb_te,
                    ))
        except Exception as exc:  # defensive: never break enrich
            events.append(EnrichEvent(
                category=EnrichCategory.IGDB_MISS,
                detail=f"igdb resolve raised: {exc}",
                ok=False, error=str(exc),
            ))
            _act(f"Igdb error: {exc}.")

    # Optional ScreenScraper metadata/artwork/manual provider. Hash-first (CRC/MD5/SHA1),
    # then cached provider ID reuse, then title + system search.
    # Non-fatal; failures are caught and logged as structured events.
    _ss_success_event: Optional[EnrichEvent] = None
    _ss_success_note: Optional[str] = None
    screenscraper_result = None
    if screenscraper_provider is not None:
        try:
            # Use the enrich_group_with_screenscraper high-level function
            _act("Trying screenscraper metadata lookup…")
            screenscraper_result = ss_mod.enrich_group_with_screenscraper(
                group, scans, ss_mod.ScreenScraperConfig.from_dict({}),
                metadata_cache_dir,
                dev_id=os.environ.get("SCREENSCRAPER_DEV_ID", "").strip(),
                dev_password=os.environ.get("SCREENSCRAPER_DEV_PASSWORD", "").strip(),
                softname=os.environ.get("SCREENSCRAPER_SOFTNAME", "AmigaADFLibraryBuilder").strip(),
                ssid=os.environ.get("SCREENSCRAPER_SSID", "").strip(),
                sspassword=os.environ.get("SCREENSCRAPER_SSPASSWORD", "").strip(),
                online=online,
                opener=None,  # Uses default opener
                resolve_urls=False,
            )
            if screenscraper_result is not None:
                if screenscraper_result.found:
                    _ss_success_event = EnrichEvent(
                        category=EnrichCategory.SCREENSCRAPER,
                        detail=(f"resolved via {screenscraper_result.match_method.value} "
                                f"conf={screenscraper_result.confidence:.2f} "
                                f"provider_id={screenscraper_result.provider_id}"),
                        ok=True,
                    )
                    _act(f"ScreenScraper resolved identity (provider_id: {screenscraper_result.provider_id}).")
                    if screenscraper_result.provider_id:
                        _ss_success_note = (
                            f"screenscraper provider_id: {screenscraper_result.provider_id}"
                        )
                    # Merge ScreenScraper metadata into the main metadata record if it improves things
                    if screenscraper_result.provider_id and (not metadata or screenscraper_result.confidence > (metadata.confidence or 0.0)):
                        # Use ScreenScraper metadata as primary source
                        if metadata is None:
                            metadata = MetadataRecord(canonical_title=lookup_title or group.title or "Unknown")
                        # Merge fields
                        if screenscraper_result.canonical_title:
                            metadata.canonical_title = screenscraper_result.canonical_title
                        if screenscraper_result.description:
                            metadata.description = screenscraper_result.description
                        if screenscraper_result.year:
                            metadata.year = screenscraper_result.year
                        if screenscraper_result.genre:
                            metadata.genres = [screenscraper_result.genre]
                        if screenscraper_result.developer:
                            metadata.developer = screenscraper_result.developer
                        if screenscraper_result.publisher:
                            metadata.publisher = screenscraper_result.publisher
                        if screenscraper_result.artwork_source_url:
                            metadata.source_url = screenscraper_result.artwork_source_url
                        if screenscraper_result.provider_id:
                            metadata.provider_id = screenscraper_result.provider_id
                        metadata.provider = "screenscraper"
                        metadata.confidence = max(metadata.confidence, screenscraper_result.confidence)
                        # Artwork URLs from ScreenScraper
                        if screenscraper_result.artwork_url:
                            metadata.artwork_page_urls = [screenscraper_result.artwork_url]
                            metadata.artwork_provider = screenscraper_result.artwork_provider
                        # External IDs
                        if screenscraper_result.external_ids:
                            pass  # Store for potential downstream use
                elif screenscraper_result.needs_manual_review:
                    events.append(EnrichEvent(
                        category=EnrichCategory.SCREENSCRAPER_REVIEW,
                        detail=(f"screenscraper needs manual review: "
                                f"{screenscraper_result.relevance_category or 'ambiguous match'}"),
                        ok=False, error=screenscraper_result.relevance_category or "ambiguous match",
                    ))
                    notes.append("screenscraper: routed to manual review")
                    _act("ScreenScraper: routed to manual review.")
                else:
                    _ss_te = getattr(screenscraper_result, "transport_error", None)
                    reason = f"transport error: {_ss_te}" if _ss_te else "no identity match"
                    _act(f"ScreenScraper: no match ({reason}).")
                    events.append(EnrichEvent(
                        category=EnrichCategory.SCREENSCRAPER_MISS,
                        detail="screenscraper: no identity match"
                        + (f" (transport: {_ss_te})" if _ss_te else ""),
                        cache="miss",
                        ok=not _ss_te,
                        error=_ss_te,
                    ))
        except Exception as exc:  # defensive: never break enrich
            events.append(EnrichEvent(
                category=EnrichCategory.SCREENSCRAPER_MISS,
                detail=f"screenscraper resolve raised: {exc}",
                ok=False, error=str(exc),
            ))
            _act(f"ScreenScraper error: {exc}.")

    # Optional RetroAchievements metadata/artwork provider. Exact-MD5 hash-first
    # identity (first non-special disk), with the game list fetched (and cached)
    # from the RA Web API. Independent of the sha256-based Playmatch/Hasheous
    # identity sources: it resolves a DIFFERENT hash in a DIFFERENT namespace
    # (RA game id), so it is NOT part of the cross-provider exact-hash fail-safe
    # below. Non-fatal; failures degrade to a clean miss and are logged.
    _ra_success_event: Optional[EnrichEvent] = None
    _ra_success_note: Optional[str] = None
    ra_result = None
    if retroachievements_provider is not None:
        try:
            _act("Trying retroachievements metadata lookup…")
            ra_result = retroachievements_provider.resolve(group, scans=scans, online=online)
            if ra_result is not None:
                if ra_result.found:
                    _ra_success_event = EnrichEvent(
                        category=EnrichCategory.RETROACHIEVEMENTS,
                        detail=(f"resolved via {ra_result.match_method.value} "
                                f"conf={ra_result.confidence:.2f} "
                                f"provider_id={ra_result.provider_id}"),
                        ok=True,
                    )
                    _act(f"RetroAchievements resolved identity (provider_id: {ra_result.provider_id}).")
                    if ra_result.provider_id:
                        _ra_success_note = (
                            f"retroachievements provider_id: {ra_result.provider_id}"
                        )
                    # Merge RA metadata if it improves things. Exact MD5 (conf 1.0)
                    # is the strongest identity signal; it wins unless the existing
                    # record is already conf 1.0 (deterministic tiebreak: first).
                    if ra_result.metadata and (not metadata or ra_result.confidence > (metadata.confidence or 0.0)):
                        if metadata is None:
                            metadata = MetadataRecord(canonical_title=lookup_title or group.title or "Unknown")
                        md = ra_result.metadata
                        if md.get("canonical_title"):
                            metadata.canonical_title = md["canonical_title"]
                        if md.get("source_url"):
                            metadata.source_url = md["source_url"]
                        if md.get("provider_id"):
                            metadata.provider_id = md["provider_id"]
                        metadata.provider = "retroachievements"
                        metadata.confidence = max(metadata.confidence, ra_result.confidence)
                        metadata.retrieved_at = md.get("retrieved_at") or metadata.retrieved_at
                        ra_icons = md.get("artwork_urls") or []
                        if ra_icons:
                            metadata.artwork_url = ra_icons[0]
                            metadata.artwork_page_urls = list(ra_icons)
                            metadata.artwork_source_url = metadata.artwork_source_url or md.get("source_url", "")
                            metadata.artwork_provider = md.get("artwork_provider") or "retroachievements"
                elif ra_result.needs_manual_review:
                    events.append(EnrichEvent(
                        category=EnrichCategory.RETROACHIEVEMENTS_REVIEW,
                        detail=(f"retroachievements needs manual review: "
                                f"{ra_result.manual_review_reason}"),
                        ok=False, error=ra_result.manual_review_reason,
                    ))
                    notes.append("retroachievements: routed to manual review")
                    _act("RetroAchievements: routed to manual review.")
                else:
                    _ra_te = getattr(ra_result, "transport_error", None)
                    reason = f"transport error: {_ra_te}" if _ra_te else f"no match ({ra_result.match_method.value})"
                    _act(f"RetroAchievements: no match ({reason}).")
                    events.append(EnrichEvent(
                        category=EnrichCategory.RETROACHIEVEMENTS_MISS,
                        detail=(f"retroachievements: no identity match "
                                f"({ra_result.match_method.value})"),
                        cache="miss",
                        ok=not _ra_te,
                        error=_ra_te,
                    ))
        except Exception as exc:  # defensive: never break enrich
            events.append(EnrichEvent(
                category=EnrichCategory.RETROACHIEVEMENTS_MISS,
                detail=f"retroachievements resolve raised: {exc}",
                ok=False, error=str(exc),
            ))
            _act(f"RetroAchievements error: {exc}.")

    # --- Cross-provider exact-hash fail-safe (issue #11/#12 hash-first posture) ---
    # When BOTH hash-first providers are enabled and each resolves the SAME
    # sha256 to an EXACT-HASH identity (match_method == EXACT_HASH, conf 1.0),
    # and those authoritative identities DISAGREE, the combined result MUST
    # fail-safe: route to manual review and SUPPRESS both per-provider success
    # records so no conflicting provider_id is ever presented as an accepted
    # identity. We NEVER pick a winner.
    #   * Exact-hash AGREEMENT (same identity) -> record both normally (unchanged).
    #   * Only one provider is exact-hash (the other is a miss / needs_review /
    #     title fallback) -> do NOT force review; preserve hash-first precedence
    #     (exact-hash outranks title; CANONICAL_REUSE 0.95 < EXACT_HASH 1.0).
    #   * Single-provider conflicts are handled by each provider's own
    #     needs_manual_review path above and remain untouched.
    needs_manual_review = False
    if (playmatch_provider is not None and hasheous_provider is not None
            and playmatch_result is not None and hasheous_result is not None
            and playmatch_result.found and hasheous_result.found
            and playmatch_result.match_method == PlaymatchMatchMethod.EXACT_HASH
            and hasheous_result.match_method == HasheousMatchMethod.EXACT_HASH):
        def _authoritative_ids(result):
            ids: set = set()
            if result.provider_id:
                ids.add(("provider_id", result.provider_id))
            # PlaymatchResult carries no external_ids; HasheousResult does.
            ext = getattr(result, "external_ids", None) or {}
            for k, v in ext.items():
                if v is not None:
                    ids.add(("external_ids", f"{k}={v}"))
            return ids

        if _authoritative_ids(playmatch_result) != _authoritative_ids(hasheous_result):
            needs_manual_review = True
            events.append(EnrichEvent(
                category=EnrichCategory.PLAYMATCH_REVIEW,
                detail=(f"cross-provider exact-hash disagreement: playmatch="
                        f"{playmatch_result.provider_id} vs hasheous="
                        f"{hasheous_result.provider_id}; routed to manual review"),
                ok=False, error="cross-provider exact-hash identity conflict",
            ))
            events.append(EnrichEvent(
                category=EnrichCategory.HASHEOUS_REVIEW,
                detail=(f"cross-provider exact-hash disagreement: playmatch="
                        f"{playmatch_result.provider_id} vs hasheous="
                        f"{hasheous_result.provider_id}; routed to manual review"),
                ok=False, error="cross-provider exact-hash identity conflict",
            ))
            notes.append(
                "cross-provider exact-hash identity conflict: routed to manual review"
            )
            # Suppress both per-provider success records so no conflicting id is
            # presented as an accepted identity.
            _pm_success_event = None
            _pm_success_note = None
            _hs_success_event = None
            _hs_success_note = None
            _ss_success_event = None
            _ss_success_note = None

    if _pm_success_event is not None:
        events.append(_pm_success_event)
        if _pm_success_note is not None:
            notes.append(_pm_success_note)
    if _hs_success_event is not None:
        events.append(_hs_success_event)
        if _hs_success_note is not None:
            notes.append(_hs_success_note)
    if _ss_success_event is not None:
        events.append(_ss_success_event)
        if _ss_success_note is not None:
            notes.append(_ss_success_note)

    from .canonical_naming import _load_canonical_library, identity_for_release_group
    from .canonical import SourceAuthority, Provenance
    display_title = metadata.canonical_title if metadata else group.title
    canon = _load_canonical_library(library_root) if library_root is not None else None
    if canon is not None:
        with canon:
            identity = identity_for_release_group(canon, group)
            if identity:
                value, prov = canon.resolve_field("game", identity[1], "title")
                if value and (metadata is None or prov.authority > SourceAuthority.PARSER):
                    display_title = value
                elif display_title:
                    canon.claim_field("game", identity[1], "title", display_title,
                                      Provenance(source=provider, authority=SourceAuthority.SEED))
    group.title = display_title or group.title
    if metadata is not None:
        metadata.canonical_title = group.title

    master = None
    processed: Optional[Path] = None
    if include_artwork:
        master = _find_existing_master(group, artwork_original_dir)
        # Provider order #2: configured local-media libraries. Only consulted when no
        # approved local-artwork master already exists. The provider copies a
        # selected source into the app cache and returns the cached master; it never
        # writes into the source library.
        if master is None and local_media_provider is not None:
            lm_master, lm_events = _resolve_local_media_master(group, local_media_provider)
            events.extend(lm_events)
            if lm_master is not None:
                master = lm_master
                _act("Found artwork in a local library.")
        if online and metadata and metadata.artwork_url and master is None:
            _act("Fetching artwork online…")
            events.append(EnrichEvent(
                category=EnrichCategory.ARTWORK_LOOKUP,
                detail="downloading artwork master from metadata URL",
                url=metadata.artwork_url,
            ))
            try:
                master = _download_artwork(metadata, artwork_original_dir, lookup_title or "unknown")
                notes.append(f"downloaded and preserved artwork master: {master}")
                events.append(EnrichEvent(
                    category=EnrichCategory.ARTWORK_GENERATED,
                    detail="downloaded artwork master", url=metadata.artwork_url, ok=True,
                ))
                _act("Artwork downloaded and ready.")
            except Exception as exc:
                _act(f"Artwork download failed: {exc}")
                notes.append(f"artwork download failed: {exc}")
                events.append(EnrichEvent(
                    category=EnrichCategory.ARTWORK_DOWNLOAD_FAILED,
                    detail="artwork download raised an error",
                    url=metadata.artwork_url, ok=False, error=str(exc),
                ))
        elif online and metadata and not metadata.artwork_url and master is None:
            _act("No artwork image found for this release.")
            events.append(EnrichEvent(
                category=EnrichCategory.ARTWORK_URL_NOT_FOUND,
                detail="metadata present but no artwork URL to download", ok=False,
            ))

        if master:
            try:
                data = artwork_mod.process_artwork_bytes(
                    master, target_w=150, target_h=150,
                    max_w=artwork_mod.ARTWORK_MAX_W,
                    max_h=artwork_mod.ARTWORK_MAX_H,
                    max_bytes=artwork_mod.ARTWORK_MAX_BYTES,
                )
                Path(artwork_processed_dir).mkdir(parents=True, exist_ok=True)
                from .naming import canonical_release_name, _sanitize
                try:
                    _cn_basename, _prov = canonical_release_name(group, library_root)
                except Exception:
                    _cn_basename = _sanitize(group.title or "Unknown")
                processed = Path(artwork_processed_dir) / f"{_cn_basename}.jpg"
                if not processed.exists() or processed.read_bytes() != data:
                    processed.write_bytes(data)
                notes.append(f"processed artwork: {processed}")
                events.append(EnrichEvent(
                    category=EnrichCategory.ARTWORK_GENERATED,
                    detail="resized artwork to Gotek master", ok=True,
                ))
                _act("Artwork processed to final size.")
            except Exception as exc:
                _act(f"Artwork processing failed: {exc}")
                notes.append(f"artwork processing failed: {exc}")
                # A corrupt/unsupported source image fails at Image.open()/decode,
                # which is a distinct failure from a genuine resize/processing error.
                # Surface it as ARTWORK_INVALID_IMAGE so the operator can tell an
                # unusable master apart from a processing-cap failure (structured logging).
                if _is_invalid_image_error(exc):
                    events.append(EnrichEvent(
                        category=EnrichCategory.ARTWORK_INVALID_IMAGE,
                        detail="artwork master is not a valid/decodable image",
                        ok=False, error=str(exc),
                    ))
                else:
                    events.append(EnrichEvent(
                        category=EnrichCategory.ARTWORK_RESIZE_FAILED,
                        detail="artwork resize/processing raised an error",
                        ok=False, error=str(exc),
                    ))
        else:
            _act("No artwork available for this release.")
            notes.append("no artwork master available")
            events.append(EnrichEvent(
                category=EnrichCategory.ARTWORK_SKIPPED,
                detail="no artwork master available; NFO only", ok=True,
            ))
    else:
        # Artwork selection disabled (GH-24): no local lookup, no online download,
        # no processing. Zero provider/network work. NFO + provenance still written.
        _act("Artwork selection is off; no cover artwork will be prepared.")
        notes.append("artwork selection disabled by operator")
        events.append(EnrichEvent(
            category=EnrichCategory.ARTWORK_SKIPPED,
            detail="artwork selection disabled by operator; NFO only", ok=True,
        ))

    Path(nfo_dir).mkdir(parents=True, exist_ok=True)
    from .naming import canonical_release_name, _sanitize
    try:
        basename, _prov = canonical_release_name(group, library_root)
    except Exception:
        basename = _sanitize(group.title or "Unknown")
    nfo_path = Path(nfo_dir) / f"{basename}.nfo"

    # Gotek-facing display NFO: Title: + Blurb: at <= 512 bytes (Gotek NFO contract).
    rep = group.records[0] if group.records else None
    canonical_title = metadata.canonical_title if metadata else group.title
    year = (metadata.year if metadata else "") or (rep.year if rep else "")
    publisher = (metadata.publisher if metadata else "") or (rep.publisher if rep else "")
    description = metadata.description if metadata else ""
    nfo_path.write_text(
        render_gotek_nfo(
            title=canonical_title or group.title or "Unknown",
            year=year or "",
            publisher=publisher or "",
            description=description or "",
        ),
        encoding="utf-8",
    )
    notes.append(f"NFO written: {nfo_path}")

    # Durable provenance is preserved OUTSIDE the Gotek-facing NFO. It lives as
    # structured JSON + a human-readable text sidecar under assets/nfo (which the
    # exporter never copies into the SD-card /ADF or /DSK output). This keeps the
    # full source hashes, approval URLs/roles, metadata provider/source, retrieval
    # timestamp, confidence/query, and enrichment mode durable without bloating
    # the 512-byte display NFO (Gotek NFO contract).
    enrichment_mode = "online" if online else provider
    provenance = _build_provenance_record(
        group, scans, metadata, mode=enrichment_mode,
        approval_sources=approval_sources,
    )
    provenance_path = Path(nfo_dir) / f"{basename}.provenance.json"
    write_json_atomic(provenance_path, provenance)
    notes.append(f"provenance written: {provenance_path}")
    provenance_txt_path = Path(nfo_dir) / f"{basename}.provenance.txt"
    provenance_txt_path.write_text(
        build_provenance_text(
            group, scans, metadata, mode=enrichment_mode,
            approval_sources=approval_sources,
        ),
        encoding="utf-8",
    )

    # Combine every manual-review routing signal into a single flag for the
    # returned EnrichResult (acceptance check #10). The cross-provider
    # exact-hash fail-safe above already sets `needs_manual_review` True on
    # disagreement; per-provider `needs_manual_review` results and any
    # routing-to-review events must surface here too, so callers get one
    # reliable signal. No event category/detail/ok/note is altered.
    needs_manual_review = (
        needs_manual_review
        or (playmatch_result is not None and playmatch_result.needs_manual_review)
        or (hasheous_result is not None and hasheous_result.needs_manual_review)
        or (igdb_result is not None and igdb_result.needs_manual_review)
        or (screenscraper_result is not None and screenscraper_result.needs_manual_review)
        or (ra_result is not None and ra_result.needs_manual_review)
        or any(e.category in (
            EnrichCategory.PLAYMATCH_REVIEW,
            EnrichCategory.HASHEOUS_REVIEW,
            EnrichCategory.IGDB_REVIEW,
            EnrichCategory.SCREENSCRAPER_REVIEW,
            EnrichCategory.RETROACHIEVEMENTS_REVIEW,
            EnrichCategory.LOCAL_MEDIA_REVIEW,
        ) for e in events)
    )
    if _igdb_success_event is not None:
        events.append(_igdb_success_event)
        if _igdb_success_note is not None:
            notes.append(_igdb_success_note)
    if _ra_success_event is not None:
        events.append(_ra_success_event)
        if _ra_success_note is not None:
            notes.append(_ra_success_note)
    _review_items = _build_review_items(events)
    return EnrichResult(nfo_path, master, processed, processed is None, notes, metadata_path, provider, processed is None, events, needs_manual_review=needs_manual_review,
                        metadata_confidence=(metadata.confidence if metadata is not None else None),
                        review_items=_review_items, dat_results=_dat_results)


def enrich_all(groups: list[ReleaseGroup], *, nfo_dir: Path, scans: list[ScanRecord],
               artwork_original_dir: Path, artwork_processed_dir: Path,
               metadata_cache_dir: Optional[Path] = None,
               curated_metadata_dir: Optional[Path] = None,
               online: bool = False, refresh: bool = False,
               local_media_provider=None, playmatch_provider=None,
               hasheous_provider=None, igdb_provider=None,
               screenscraper_provider=None,
               retroachievements_provider=None,
               halloflight_enabled: bool = True,
               include_artwork: bool = True,
               cancel_event: Optional[threading.Event] = None,
               activity: Optional[Callable[[str], None]] = None,
               metadata_source_manager: MetadataSourceManager = None,
               library_root: Optional[Path] = None) -> list[EnrichResult]:
    scan_map = {s.filename: s for s in scans}
    metadata_cache_dir = Path(metadata_cache_dir or (Path(nfo_dir).parent / "metadata-cache"))
    curated_metadata_dir = Path(curated_metadata_dir or (Path(nfo_dir).parent / "metadata-curated"))
    total = len(groups)
    results: list[EnrichResult] = []
    for idx, group in enumerate(groups, start=1):
        if cancel_event is not None and cancel_event.is_set():
            if activity is not None:
                try:
                    activity("Cancelled by operator — stopping enrichment.")
                except Exception:
                    pass
            break
        if activity is not None:
            try:
                activity(
                    f"Preparing release {idx} of {total}"
                    + (f": {group.title}" if group.title else "")
                )
            except Exception:  # logging must never break the run
                pass
        results.append(enrich_group(group, nfo_dir=nfo_dir, scans=scan_map,
                     artwork_original_dir=artwork_original_dir,
                     artwork_processed_dir=artwork_processed_dir,
                     metadata_cache_dir=metadata_cache_dir,
                     curated_metadata_dir=curated_metadata_dir,
                     online=online, refresh=refresh,
                     local_media_provider=local_media_provider,
                     playmatch_provider=playmatch_provider,
                     hasheous_provider=hasheous_provider,
                     igdb_provider=igdb_provider,
                     screenscraper_provider=screenscraper_provider,
                     retroachievements_provider=retroachievements_provider,
                     halloflight_enabled=halloflight_enabled,
                     include_artwork=include_artwork,
                     cancel_event=cancel_event,
                     activity=activity,
                     metadata_source_manager=metadata_source_manager,
                     library_root=library_root))
    return results