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
from pathlib import Path
from typing import Optional

from . import artwork as artwork_mod
from .metadata import MetadataRecord, cache_key, guard_url, lookup_metadata
from .models import ReleaseGroup, ScanRecord
from .naming import release_basename
from .nfo_render import render_gotek_nfo

# Retained for exporter-gate compatibility. Artwork policy is aspect-fit within
# 150x150, never cropped or upscaled; there is no minimum source dimension.
VERIFIED_ARTWORK_WIDTH: Optional[int] = 150
VERIFIED_ARTWORK_HEIGHT: Optional[int] = 150


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


def _write_json_atomic(path: Path, data: dict) -> None:
    """Write JSON to ``path`` via a temp file + atomic replace (no partial reads)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


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
    request = urllib.request.Request(record.artwork_url, headers={"User-Agent": "AmigaADFLibraryBuilder/0.2.1", "Accept": "image/*"})
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

    if result.found and result.cached_path is not None:
        events.append(EnrichEvent(
            category=EnrichCategory.LOCAL_MEDIA,
            detail=(
                f"matched {result.match_method.value} in "
                f"{result.category!r} (conf {result.confidence:.2f}); "
                f"cached {Path(result.cached_path).name}"
            ),
            cache="hit", ok=True,
        ))
        return Path(result.cached_path), events
    if result.needs_manual_review:
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


def enrich_group(group: ReleaseGroup, *, nfo_dir: Path, scans: dict[str, ScanRecord],
                 artwork_original_dir: Path, artwork_processed_dir: Path,
                 metadata_cache_dir: Optional[Path] = None, curated_metadata_dir: Optional[Path] = None,
                 online: bool = False, refresh: bool = False,
                 local_media_provider=None) -> EnrichResult:
    metadata_cache_dir = Path(metadata_cache_dir or (Path(nfo_dir).parent / "metadata-cache"))
    curated_metadata_dir = Path(curated_metadata_dir or (Path(nfo_dir).parent / "metadata-curated"))
    notes: list[str] = []
    events: list[EnrichEvent] = []
    metadata: Optional[MetadataRecord] = None
    provider = "offline"
    metadata_path: Optional[Path] = None

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
        try:
            metadata, provider, relevance_events = lookup_metadata(
                lookup_title, cache_dir=metadata_cache_dir,
                curated_dir=curated_metadata_dir, refresh=refresh, group=group,
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
                notes.append("metadata lookup: not-found")
                events.append(EnrichEvent(
                    category=EnrichCategory.METADATA_NOT_FOUND,
                    detail="online lookup returned no record", cache="miss",
                ))
        except Exception as exc:
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
            notes.append("reused cached metadata offline")
            events.append(EnrichEvent(
                category=EnrichCategory.CACHE_HIT,
                detail="reused cached metadata record", cache="hit",
            ))
        else:
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
    if online and metadata and metadata.artwork_url and master is None:
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
        except Exception as exc:
            notes.append(f"artwork download failed: {exc}")
            events.append(EnrichEvent(
                category=EnrichCategory.ARTWORK_DOWNLOAD_FAILED,
                detail="artwork download raised an error",
                url=metadata.artwork_url, ok=False, error=str(exc),
            ))
    elif online and metadata and not metadata.artwork_url and master is None:
        events.append(EnrichEvent(
            category=EnrichCategory.ARTWORK_URL_NOT_FOUND,
            detail="metadata present but no artwork URL to download", ok=False,
        ))

    processed: Optional[Path] = None
    if master:
        try:
            data = artwork_mod.process_artwork_bytes(
                master, target_w=150, target_h=150,
                max_w=artwork_mod.ARTWORK_MAX_W,
                max_h=artwork_mod.ARTWORK_MAX_H,
                max_bytes=artwork_mod.ARTWORK_MAX_BYTES,
            )
            Path(artwork_processed_dir).mkdir(parents=True, exist_ok=True)
            processed = Path(artwork_processed_dir) / f"{release_basename(group)}.jpg"
            if not processed.exists() or processed.read_bytes() != data:
                processed.write_bytes(data)
            notes.append(f"processed artwork: {processed}")
            events.append(EnrichEvent(
                category=EnrichCategory.ARTWORK_GENERATED,
                detail="resized artwork to Gotek master", ok=True,
            ))
        except Exception as exc:
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
        notes.append("no artwork master available")
        events.append(EnrichEvent(
            category=EnrichCategory.ARTWORK_SKIPPED,
            detail="no artwork master available; NFO only", ok=True,
        ))

    Path(nfo_dir).mkdir(parents=True, exist_ok=True)
    basename = release_basename(group)
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
    _write_json_atomic(provenance_path, provenance)
    notes.append(f"provenance written: {provenance_path}")
    provenance_txt_path = Path(nfo_dir) / f"{basename}.provenance.txt"
    provenance_txt_path.write_text(
        build_provenance_text(
            group, scans, metadata, mode=enrichment_mode,
            approval_sources=approval_sources,
        ),
        encoding="utf-8",
    )
    return EnrichResult(nfo_path, master, processed, processed is not None, notes, metadata_path, provider, processed is None, events)


def enrich_all(groups: list[ReleaseGroup], *, nfo_dir: Path, scans: list[ScanRecord],
               artwork_original_dir: Path, artwork_processed_dir: Path,
               metadata_cache_dir: Optional[Path] = None,
               curated_metadata_dir: Optional[Path] = None,
               online: bool = False, refresh: bool = False,
               local_media_provider=None) -> list[EnrichResult]:
    scan_map = {s.filename: s for s in scans}
    metadata_cache_dir = Path(metadata_cache_dir or (Path(nfo_dir).parent / "metadata-cache"))
    curated_metadata_dir = Path(curated_metadata_dir or (Path(nfo_dir).parent / "metadata-curated"))
    return [
        enrich_group(group, nfo_dir=nfo_dir, scans=scan_map,
                     artwork_original_dir=artwork_original_dir,
                     artwork_processed_dir=artwork_processed_dir,
                     metadata_cache_dir=metadata_cache_dir,
                     curated_metadata_dir=curated_metadata_dir,
                     online=online, refresh=refresh,
                     local_media_provider=local_media_provider)
        for group in groups
    ]
