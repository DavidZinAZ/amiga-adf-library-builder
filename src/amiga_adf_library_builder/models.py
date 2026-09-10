"""Shared data models for the Amiga ADF Library Builder.

Pure data definitions and small value types. No I/O here.
"""
from __future__ import annotations

import json
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
    MERGE = "merge"
    ACCEPT_MATCH = "accept_match"
    REJECT_MATCH = "reject_match"
    ACCEPT_METADATA_ONLY = "accept_metadata_only"
    KEEP_FILENAME = "keep_filename"
    ARTWORK_SELECTED = "artwork_selected"
    MANUAL_SELECTED = "manual_selected"
    REVIEW_RESOLVED = "review_resolved"


@dataclass
class StagedChange:
    """A single curation change for decision log."""
    action: CurationAction
    timestamp: str
    details: str
    payload: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "action": self.action.value,
            "timestamp": self.timestamp,
            "details": self.details,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "StagedChange":
        return cls(
            action=CurationAction(d["action"]),
            timestamp=d.get("timestamp", ""),
            details=d.get("details", ""),
            payload=d.get("payload"),
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

    # Planned folder override (operator-approved or curation-planned).
    # When set, this takes precedence over derived identity for export path.
    # Distinct from 'group' which holds the release/crack group identity.
    folder: Optional[str] = None

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
            "folder": self.folder,
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
            folder=d.get("folder"),
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

    def move_adfs(
        self,
        src_key: str,
        dst_key: str,
        filenames: list[str],
    ) -> tuple[StagedReleaseEntry, StagedReleaseEntry]:
        """Move ADF files from one staged release to another.

        Args:
            src_key: Source release key
            dst_key: Destination release key
            filenames: List of ADF filenames to move

        Returns:
            Tuple of (source_entry, destination_entry) after move

        Raises:
            ValueError: If source or destination doesn't exist, if src_key == dst_key,
                        if any filename is not in source, or if any filename already
                        exists in destination (duplicate prevention).
        """
        if src_key == dst_key:
            raise ValueError("Source and destination release cannot be the same")

        src_entry = self.releases.get(src_key)
        if not src_entry:
            raise ValueError(f"Source release not found: {src_key}")

        dst_entry = self.releases.get(dst_key)
        if not dst_entry:
            raise ValueError(f"Destination release not found: {dst_key}")

        # Empty selection is rejected: a no-op that changes nothing and
        # records nothing. The UI layer additionally prevents empty
        # selections from being submitted at all.
        if not filenames:
            return src_entry, dst_entry

        # Verify all filenames exist in source
        for fname in filenames:
            if fname not in src_entry.adf_files:
                raise ValueError(f"ADF file not found in source: {fname}")

        # Verify no duplicates in destination
        for fname in filenames:
            if fname in dst_entry.adf_files:
                raise ValueError(f"ADF file already exists in destination: {fname}")

        # Store source state for undo
        src_files_before = list(src_entry.adf_files)
        dst_files_before = list(dst_entry.adf_files)
        src_state_before = src_entry.curation_state

        # Remove from source preserving order of remaining files
        new_src_files = [f for f in src_entry.adf_files if f not in filenames]
        src_entry.adf_files = new_src_files

        # Append to destination preserving moved order
        dst_entry.adf_files.extend(filenames)

        # Post-move source state: NEEDS_REVIEW when the source emptied,
        # otherwise unchanged. Recorded in the payload so redo can restore
        # the exact post-move state.
        src_state_after = (
            StagedState.NEEDS_REVIEW if not src_entry.adf_files else src_state_before
        )

        # Record decision log on both source and destination. The payload
        # carries the full undo context; the UI layer records the logged
        # action for undo exactly once.
        from datetime import datetime, timezone
        timestamp = datetime.now(timezone.utc).isoformat()

        payload = json.dumps({
            "src_key": src_key,
            "dst_key": dst_key,
            "moved_files": list(filenames),
            "src_files_before": src_files_before,
            "dst_files_before": dst_files_before,
            "src_files_after": list(src_entry.adf_files),
            "dst_files_after": list(dst_entry.adf_files),
            "src_curation_state": src_state_before.value,
            "src_state_after": src_state_after.value,
        })

        src_action = StagedChange(
            action=CurationAction.MOVE,
            timestamp=timestamp,
            details=f"Moved {len(filenames)} ADF(s) to {dst_entry.release_key} ({dst_entry.title})",
            payload=payload,
        )
        src_entry.actions.append(src_action)

        dst_action = StagedChange(
            action=CurationAction.MOVE,
            timestamp=timestamp,
            details=f"Received {len(filenames)} ADF(s) from {src_entry.release_key} ({src_entry.title})",
            payload=payload,
        )
        dst_entry.actions.append(dst_action)

        # If source is now empty, set to NEEDS_REVIEW (record real previous state).
        # The transition action carries the same payload so a single undo entry
        # restores files AND state together.
        if not src_entry.adf_files:
            prev_state = src_entry.curation_state
            src_entry.curation_state = StagedState.NEEDS_REVIEW
            empty_action = StagedChange(
                action=CurationAction.STATE_CHANGE,
                timestamp=timestamp,
                details=f"State changed from {prev_state.value} to needs_review (empty after move)",
                payload=payload,
            )
            src_entry.actions.append(empty_action)

        return src_entry, dst_entry

    def merge_release(self, src_key: str, dst_key: str) -> tuple[StagedReleaseEntry, StagedReleaseEntry]:
        """Merge all ADFs from source release into destination release.

        Args:
            src_key: Source release key (will be emptied)
            dst_key: Destination release key (will receive all ADFs)

        Returns:
            Tuple of (source_entry, destination_entry) after merge

        Raises:
            ValueError: If source or destination doesn't exist, if src_key == dst_key,
                        or if any ADF from source already exists in destination.
        """
        if src_key == dst_key:
            raise ValueError("Source and destination release cannot be the same")

        src_entry = self.releases.get(src_key)
        if not src_entry:
            raise ValueError(f"Source release not found: {src_key}")

        dst_entry = self.releases.get(dst_key)
        if not dst_entry:
            raise ValueError(f"Destination release not found: {dst_key}")

        # Check for duplicates
        for fname in src_entry.adf_files:
            if fname in dst_entry.adf_files:
                raise ValueError(f"ADF file already exists in destination: {fname}")

        # Store pre-merge state for undo
        src_files_before = list(src_entry.adf_files)
        dst_files_before = list(dst_entry.adf_files)
        src_state_before = src_entry.curation_state

        # Append source ADFs to destination
        # Destination's existing files first, then source's files in their
        # original order (preserves disk ordinals on both sides).
        dst_entry.adf_files.extend(src_entry.adf_files)

        # Merge metadata: destination wins; promote only blank destination fields from source
        if not dst_entry.title and src_entry.title:
            dst_entry.title = src_entry.title
        if not dst_entry.edition and src_entry.edition:
            dst_entry.edition = src_entry.edition
        if not dst_entry.group and src_entry.group:
            dst_entry.group = src_entry.group
        if not dst_entry.chipset and src_entry.chipset:
            dst_entry.chipset = src_entry.chipset
        if not dst_entry.language and src_entry.language:
            dst_entry.language = src_entry.language
        if not dst_entry.version and src_entry.version:
            dst_entry.version = src_entry.version
        if not dst_entry.alt_marker and src_entry.alt_marker:
            dst_entry.alt_marker = src_entry.alt_marker

        # Do NOT carry over source locked_fields to avoid introducing hidden
        # protection constraints. Do NOT carry over source action history: the
        # destination audit trail must contain exactly one MERGE entry for this
        # operation, not a copy of the source's unrelated history.
        # Preserve destination curation state and locked_fields.

        # Empty source entry but retain it in the library with NEEDS_REVIEW
        src_entry.adf_files = []
        src_entry.curation_state = StagedState.NEEDS_REVIEW

        # Record decision log on both source and destination. The payload
        # carries the full undo context (before/after files + state); the UI
        # layer records the logged action for undo exactly once.
        from datetime import datetime, timezone
        timestamp = datetime.now(timezone.utc).isoformat()

        payload = json.dumps({
            "src_key": src_key,
            "dst_key": dst_key,
            "src_files_before": src_files_before,
            "dst_files_before": dst_files_before,
            "src_files_after": list(src_entry.adf_files),
            "dst_files_after": list(dst_entry.adf_files),
            "src_curation_state": src_state_before.value,
            # Merge always empties the source and marks it NEEDS_REVIEW.
            "src_state_after": StagedState.NEEDS_REVIEW.value,
        })

        src_action = StagedChange(
            action=CurationAction.MERGE,
            timestamp=timestamp,
            details=f"Merged into {dst_entry.release_key} ({dst_entry.title}); {len(src_files_before)} ADF(s) moved",
            payload=payload,
        )
        src_entry.actions.append(src_action)

        # Empty-source state transition recorded with the real previous state
        state_action = StagedChange(
            action=CurationAction.STATE_CHANGE,
            timestamp=timestamp,
            details=f"State changed from {src_state_before.value} to needs_review (empty after merge)",
            payload=payload,
        )
        src_entry.actions.append(state_action)

        dst_action = StagedChange(
            action=CurationAction.MERGE,
            timestamp=timestamp,
            details=f"Merged from {src_key} ({src_entry.title}); {len(src_files_before)} ADF(s) received",
            payload=payload,
        )
        dst_entry.actions.append(dst_action)

        return src_entry, dst_entry

    def carry_over(self, previous: "StagedLibrary") -> int:
        """Restore prior curation decisions from a previous state library.

        (GH-99) Called by the state builder after a pipeline run, before the
        state file is saved. For each release whose ``release_key`` was also
        curated in ``previous``, the operator's staged decisions are restored
        so the same merge/match/state work is never asked twice.

        What is carried over (per release_key):
          * curation_state, notes, actions (decision log);
          * edited metadata: title, edition, group, chipset, language,
            version, alt_marker;
          * provenance: metadata_source, match_confidence, confidence,
            locked_fields;
          * artwork/RTFM path selections: artwork_front/back/spine/other,
            rtfm_files;
          * folder override.

        What is NOT carried over: ``adf_files`` (the ADF membership always
        comes from the fresh scan; carrying stale membership could point at
        files that no longer exist) and ``ext``.

        Strong-key discipline: identity is the pipeline-derived
        ``release_key`` only. A previous entry is never applied to an
        unrelated release, and previous entries that no longer exist simply
        do not apply (they are not re-created).

        Returns the number of releases that had a matching previous entry.
        """
        carried = 0
        for release_key, entry in self.releases.items():
            prev = previous.releases.get(release_key)
            if prev is None:
                continue
            entry.curation_state = prev.curation_state
            entry.title = prev.title
            entry.edition = prev.edition
            entry.group = prev.group
            entry.chipset = prev.chipset
            entry.language = prev.language
            entry.version = prev.version
            entry.alt_marker = prev.alt_marker
            entry.artwork_front = prev.artwork_front
            entry.artwork_back = prev.artwork_back
            entry.artwork_spine = prev.artwork_spine
            entry.artwork_other = list(prev.artwork_other)
            entry.rtfm_files = list(prev.rtfm_files)
            entry.metadata_source = prev.metadata_source
            # (GH-99, defect 2) Confidence is canonical *metadata* from the
            # current run's match, not an operator curation decision. A fresh
            # run may have re-resolved a different (higher/lower) confidence,
            # so a freshly-resolved value must NOT be clobbered by the previous
            # run's. Carry the previous value over only when the current run
            # resolved none (0.0 / None), so the column is populated from the
            # canonical staged metadata when known but never shows a stale
            # confidence for a changed match.
            fresh_conf = entry.confidence
            fresh_match = entry.match_confidence
            if not fresh_conf and not fresh_match:
                entry.match_confidence = prev.match_confidence
                entry.confidence = prev.confidence
            entry.locked_fields = list(prev.locked_fields)
            entry.folder = prev.folder
            entry.notes = prev.notes
            entry.actions = list(prev.actions)
            carried += 1
        return carried
