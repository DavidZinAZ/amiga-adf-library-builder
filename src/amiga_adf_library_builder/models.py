"""Shared data models for the Amiga ADF Library Builder.

Pure data definitions and small value types. No I/O here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path
from enum import Enum


# Disk-number conventions we understand.
DISK_DIGIT_RE = None  # populated lazily to avoid import cost at module load
LETTER_ORDINAL = {chr(ord("A") + i): i + 1 for i in range(26)}

# Special-disk role tokens (lowercase, matched against filename stems).
SPECIAL_DISK_ROLES = (
    "boot",
    "character",
    "char",
    "save",
    "intro",
    "utility",
    "util",
    "companion",
)

# Tokens that mark a filename as a candidate near-duplicate/special without a
# determinable main game disk.
AMBIGUOUS_SUFFIX_TOKENS = (
    "boot",
    "character",
    "char",
    "save",
    "intro",
)


@dataclass
class ScanRecord:
    """One intake file: path, size, and SHA-256 (computed read-only)."""

    path: Path
    filename: str
    size: int
    sha256: str
    scanned_at: str  # ISO timestamp

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "filename": self.filename,
            "size": self.size,
            "sha256": self.sha256,
            "scanned_at": self.scanned_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ScanRecord":
        return cls(
            path=Path(d["path"]),
            filename=d["filename"],
            size=int(d["size"]),
            sha256=d["sha256"],
            scanned_at=d["scanned_at"],
        )


@dataclass
class ParsedRecord:
    """Structured metadata parsed from a single filename.

    Fields are populated only when the filename carries evidence; unknown
    values stay None. The contract is explicit: never guess.
    """

    source_filename: str
    ext: str  # 'adf' or 'dsk'

    title: Optional[str] = None
    year: Optional[str] = None  # preserves '(199x)' style indeterminate years
    publisher: Optional[str] = None
    chipset: Optional[str] = None  # e.g. 'AGA/M3'
    language: Optional[str] = None
    version: Optional[str] = None
    group: Optional[str] = None  # release/crack group, e.g. 'SKR'
    trainer: bool = False
    alt_marker: Optional[str] = None  # e.g. 'a', 'a2'
    edition: Optional[str] = None  # e.g. 'Platinum Edition'

    # Disk ordering
    disk_number: Optional[int] = None  # 1-based ordinal within its set
    total_disks: Optional[int] = None

    # Special disk
    special_disk: bool = False
    special_role: Optional[str] = None  # boot/character/save/intro/...

    # Grouping key parts (filled by parser for convenience)
    group_key: str = ""

    # Normalized identity used to cluster disks of one release.
    release_key: str = ""

    def to_dict(self) -> dict:
        return {
            "source_filename": self.source_filename,
            "ext": self.ext,
            "title": self.title,
            "year": self.year,
            "publisher": self.publisher,
            "chipset": self.chipset,
            "language": self.language,
            "version": self.version,
            "group": self.group,
            "trainer": self.trainer,
            "alt_marker": self.alt_marker,
            "edition": self.edition,
            "disk_number": self.disk_number,
            "total_disks": self.total_disks,
            "special_disk": self.special_disk,
            "special_role": self.special_role,
            "group_key": self.group_key,
            "release_key": self.release_key,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ParsedRecord":
        return cls(
            source_filename=d["source_filename"],
            ext=d.get("ext", ""),
            title=d.get("title"),
            year=d.get("year"),
            publisher=d.get("publisher"),
            chipset=d.get("chipset"),
            language=d.get("language"),
            version=d.get("version"),
            group=d.get("group"),
            trainer=bool(d.get("trainer", False)),
            alt_marker=d.get("alt_marker"),
            edition=d.get("edition"),
            disk_number=d.get("disk_number"),
            total_disks=d.get("total_disks"),
            special_disk=bool(d.get("special_disk", False)),
            special_role=d.get("special_role"),
            group_key=d.get("group_key", ""),
            release_key=d.get("release_key", ""),
        )


@dataclass
class ReleaseGroup:
    """A clustered set of one or more parsed records sharing a release identity."""

    release_key: str
    title: Optional[str]
    edition: Optional[str]
    group: Optional[str]
    chipset: Optional[str]
    language: Optional[str]
    version: Optional[str]
    alt_marker: Optional[str]
    ext: str
    records: list = field(default_factory=list)  # list[ParsedRecord]
    disks: list = field(default_factory=list)  # ordered list[ParsedRecord] (non-special)
    specials: list = field(default_factory=list)  # list[ParsedRecord] (special disks)
    is_complete: bool = False
    has_main_disk: bool = False
    quarantine_reason: Optional[str] = None
    # Operator-approved Gotek folder override (see manual_approvals.py).
    # When set, release_basename() uses it directly (FAT32-sanitized).
    folder: Optional[str] = None
    # Approved source URLs propagated from a matched approval record for NFO
    # provenance (manual-approval feature). Each entry: {"url": str, "role": str}. Verbatim
    # (exact URL as supplied); never guessed or synthesized.
    approved_sources: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "release_key": self.release_key,
            "title": self.title,
            "edition": self.edition,
            "group": self.group,
            "chipset": self.chipset,
            "language": self.language,
            "version": self.version,
            "alt_marker": self.alt_marker,
            "ext": self.ext,
            "is_complete": self.is_complete,
            "has_main_disk": self.has_main_disk,
            "quarantine_reason": self.quarantine_reason,
            "folder": self.folder,
            "approved_sources": list(self.approved_sources),
            "disk_count": len(self.disks),
            "special_count": len(self.specials),
            "record_filenames": [r.source_filename for r in self.records],
        }


class StagedState(Enum):
    """State of a release in the staged library preview."""
    PENDING = "pending"        # Awaiting curation decision
    ACCEPTED = "accepted"      # Approved for export
    REJECTED = "rejected"      # Excluded from export
    MODIFIED = "modified"      # Custom changes applied
    NEEDS_REVIEW = "needs_review"  # Requires human review


class CurationAction(Enum):
    """Types of curation actions for decision log."""
    STATE_CHANGE = "state_change"
    NOTE_ADDED = "note_added"
    ARTWORK_CHANGED = "artwork_changed"
    METADATA_EDIT = "metadata_edit"
    BULK_EDIT = "bulk_edit"
    RENAME = "rename"
    MOVE = "move"
    ACCEPT_MATCH = "accept_match"
    REJECT_MATCH = "reject_match"
    ACCEPT_METADATA_ONLY = "accept_metadata_only"
    KEEP_FILENAME = "keep_filename"
    ARTWORK_SELECTED = "artwork_selected"
    MANUAL_SELECTED = "manual_selected"


@dataclass
class StagedChange:
    """A single curation change for decision log."""
    action: CurationAction
    timestamp: str
    details: str

    def to_dict(self) -> dict:
        return {
            "action": self.action.value,
            "timestamp": self.timestamp,
            "details": self.details,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "StagedChange":
        return cls(
            action=CurationAction(d["action"]),
            timestamp=d.get("timestamp", ""),
            details=d.get("details", ""),
        )


@dataclass
class StagedReleaseEntry:
    """A release entry in the staged library preview with curation state."""

    # Core identity from the pipeline
    release_key: str
    title: Optional[str]
    edition: Optional[str]
    group: Optional[str]
    chipset: Optional[str]
    language: Optional[str]
    version: Optional[str]
    alt_marker: Optional[str]
    ext: str

    # ADF files in this release
    adf_files: list[str] = field(default_factory=list)

    # Artwork paths
    artwork_front: Optional[str] = None
    artwork_back: Optional[str] = None
    artwork_spine: Optional[str] = None
    artwork_other: list[str] = field(default_factory=list)

    # RTFM/Manual files
    rtfm_files: list[str] = field(default_factory=list)

    # Metadata provenance
    metadata_source: Optional[str] = None
    match_confidence: Optional[float] = None
    locked_fields: list[str] = field(default_factory=list)

    # Confidence score (0.0 - 1.0)
    confidence: float = 0.0

    # Curation state
    curation_state: StagedState = StagedState.PENDING
    notes: Optional[str] = None

    # Change history for decision log
    actions: list[StagedChange] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "release_key": self.release_key,
            "title": self.title,
            "edition": self.edition,
            "group": self.group,
            "chipset": self.chipset,
            "language": self.language,
            "version": self.version,
            "alt_marker": self.alt_marker,
            "ext": self.ext,
            "adf_files": self.adf_files,
            "artwork_front": self.artwork_front,
            "artwork_back": self.artwork_back,
            "artwork_spine": self.artwork_spine,
            "artwork_other": self.artwork_other,
            "rtfm_files": self.rtfm_files,
            "metadata_source": self.metadata_source,
            "match_confidence": self.match_confidence,
            "locked_fields": self.locked_fields,
            "confidence": self.confidence,
            "curation_state": self.curation_state.value,
            "notes": self.notes,
            "actions": [c.to_dict() for c in self.actions],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "StagedReleaseEntry":
        return cls(
            release_key=d["release_key"],
            title=d.get("title"),
            edition=d.get("edition"),
            group=d.get("group"),
            chipset=d.get("chipset"),
            language=d.get("language"),
            version=d.get("version"),
            alt_marker=d.get("alt_marker"),
            ext=d.get("ext", "adf"),
            adf_files=d.get("adf_files", []),
            artwork_front=d.get("artwork_front"),
            artwork_back=d.get("artwork_back"),
            artwork_spine=d.get("artwork_spine"),
            artwork_other=d.get("artwork_other", []),
            rtfm_files=d.get("rtfm_files", []),
            metadata_source=d.get("metadata_source"),
            match_confidence=d.get("match_confidence"),
            locked_fields=d.get("locked_fields", []),
            confidence=d.get("confidence", 0.0),
            curation_state=StagedState(d.get("curation_state", "pending")),
            notes=d.get("notes"),
            actions=[StagedChange.from_dict(c) for c in d.get("actions", [])],
        )


@dataclass
class StagedLibrary:
    """Complete staged library state for the preview & curation workspace."""
    releases: dict[str, StagedReleaseEntry] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "releases": {k: v.to_dict() for k, v in self.releases.items()},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "StagedLibrary":
        return cls(
            releases={k: StagedReleaseEntry.from_dict(v) for k, v in d.get("releases", {}).items()},
        )
