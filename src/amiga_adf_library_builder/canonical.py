"""Canonical Game / Release / Disk domain model with per-field provenance.

GH-107 Slice 3. Pure data + one SQLite persistence surface (CanonicalLibrary).
No I/O beyond the canonical database; source media is never touched.

Design contract (documented precedence rule, binding):

Canonical-field resolution precedence, highest authority first:

1. ``curation``        — explicit operator decisions in the staged curation
                          state (Curation Layer outranks everything else,
                          per the GH-107 architecture principles).
2. ``curation_memory`` — operator decisions remembered for the file content
                          hash (FileIdentityStore curation memory; also a
                          human decision, but recorded indirectly via the
                          hash-identity path).
3. ``dat``             — Knowledge Layer DAT sources (TOSEC, No-Intro,
                          Fresh1G1R, custom), ranked among themselves by
                          ``authority_rank`` (lower rank wins; ties broken
                          by most recent ``observed_at``).
4. ``parser``          — values derived from the filename parser
                          (deterministic, lowest authority).

Conflicting claims are PRESERVED, never silently overwritten: every
``claim_field`` call appends a claim; resolution is computed on demand.
Manual (``curation``) claims are never dropped by later provider refreshes.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from pathlib import Path
from typing import Optional

SCHEMA_VERSION = 1

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def slugify_title(title: str) -> str:
    """Deterministic slug for a game title (stable, filename-independent)."""
    slug = _SLUG_RE.sub("-", title.strip().lower()).strip("-")
    if not slug:
        slug = "untitled"
    return slug[:80]


class SourceAuthority(IntEnum):
    """Documented authority ranking for claim sources (see module docstring)."""

    PARSER = 10
    DAT = 20
    CURATION_MEMORY = 30
    CURATION = 40

    @property
    def tier(self) -> str:
        return {
            SourceAuthority.PARSER: "parser",
            SourceAuthority.DAT: "dat",
            SourceAuthority.CURATION_MEMORY: "curation_memory",
            SourceAuthority.CURATION: "curation",
        }[self]


@dataclass(frozen=True)
class Provenance:
    """Where one observed field value came from.

    Attributes:
        source: short source id, e.g. ``"tosec"``, ``"operator"``, ``"parser"``.
        record_key: source record/key (e.g. DAT entry key) — may be empty.
        url: source record URL when the provider supplies one.
        authority: structured authority tier (determines precedence).
        authority_rank: within-tier ranking (DAT sources rank among each
            other; lower wins). Ignored outside the ``dat`` tier.
        confidence: provider-supplied confidence in [0.0, 1.0], or None.
        observed_at: ISO-8601 UTC timestamp of the observation.
    """

    source: str
    record_key: str = ""
    url: str = ""
    authority: SourceAuthority = SourceAuthority.PARSER
    authority_rank: int = 0
    confidence: Optional[float] = None
    observed_at: str = ""

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "record_key": self.record_key,
            "url": self.url,
            "authority": self.authority.tier,
            "authority_rank": self.authority_rank,
            "confidence": self.confidence,
            "observed_at": self.observed_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Provenance":
        tier = d.get("authority", "parser")
        authority = {
            "parser": SourceAuthority.PARSER,
            "dat": SourceAuthority.DAT,
            "curation_memory": SourceAuthority.CURATION_MEMORY,
            "curation": SourceAuthority.CURATION,
        }.get(tier, SourceAuthority.PARSER)
        return cls(
            source=d.get("source", ""),
            record_key=d.get("record_key", ""),
            url=d.get("url", ""),
            authority=authority,
            authority_rank=int(d.get("authority_rank", 0)),
            confidence=d.get("confidence"),
            observed_at=d.get("observed_at", ""),
        )

    def sort_key(self) -> tuple:
        """Deterministic precedence key (smaller wins).

        Manual curation never loses to a newer automated claim because the
        authority tier dominates the comparison. Within the same tier, the
        higher confidence and the later observation win; record_key/url
        break final ties so ordering is total and stable.
        """
        return (
            -int(self.authority),
            self.authority_rank,
            -(self.confidence if self.confidence is not None else 0.0),
            _sort_time_desc(self.observed_at),
            self.source,
            self.record_key,
            self.url,
        )


def _sort_time_desc(observed_at: str) -> str:
    # ISO-8601 UTC strings sort lexicographically; pad empty to sort last
    # within the descending observation-time comparison.
    return _NEG_EPOCH if not observed_at else observed_at


_NEG_EPOCH = "0000-00-00T00:00:00+00:00"


@dataclass
class CanonicalField:
    """One canonical field holding every observed claim (conflicts preserved)."""

    claims: list = field(default_factory=list)  # list[(Provenance, value)]

    def claim(self, provenance: Provenance, value) -> None:
        """Append a claim. Never overwrites; conflicts accumulate."""
        self.claims.append((provenance, value))

    def resolve(self):
        """Return the canonical (value, provenance) or (None, None).

        Deterministic: claims are ordered by Provenance.sort_key(); the
        first wins. Ties on identical provenance keys resolve to the value
        comparison so the result is stable for equal inputs regardless of
        insertion order.
        """
        if not self.claims:
            return None, None
        ranked = sorted(
            self.claims,
            key=lambda pc: (pc[0].sort_key(), str(pc[1])),
        )
        return ranked[0][1], ranked[0][0]

    def conflicting_values(self) -> list:
        """Distinct observed values, most-preferred first (conflict view)."""
        ranked = sorted(
            self.claims,
            key=lambda pc: (pc[0].sort_key(), str(pc[1])),
        )
        seen = []
        for _, value in ranked:
            if value not in seen:
                seen.append(value)
        return seen

    def to_dict(self) -> dict:
        return {
            "claims": [
                {"provenance": p.to_dict(), "value": v} for p, v in self.claims
            ]
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CanonicalField":
        out = cls()
        for c in d.get("claims", []):
            out.claims.append((Provenance.from_dict(c.get("provenance", {})), c.get("value")))
        return out


@dataclass
class Disk:
    """A concrete media member of a Release (one ADF file)."""

    disk_id: str                      # stable: sha256 of content (or path fallback hash)
    release_id: str
    filename: str
    disk_number: Optional[int] = None
    sha256: Optional[str] = None
    size: Optional[int] = None
    fields: dict = field(default_factory=dict)  # name -> CanonicalField
    identity_linked: bool = False     # linked into FileIdentityStore?

    def to_dict(self) -> dict:
        return {
            "disk_id": self.disk_id,
            "release_id": self.release_id,
            "filename": self.filename,
            "disk_number": self.disk_number,
            "sha256": self.sha256,
            "size": self.size,
            "fields": {k: v.to_dict() for k, v in self.fields.items()},
            "identity_linked": self.identity_linked,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Disk":
        return cls(
            disk_id=d["disk_id"],
            release_id=d["release_id"],
            filename=d.get("filename", ""),
            disk_number=d.get("disk_number"),
            sha256=d.get("sha256"),
            size=d.get("size"),
            fields={k: CanonicalField.from_dict(v) for k, v in d.get("fields", {}).items()},
            identity_linked=bool(d.get("identity_linked", False)),
        )


@dataclass
class Release:
    """A specific version/edition/region/language/publisher grouping of a Game."""

    release_id: str
    game_id: str
    fields: dict = field(default_factory=dict)  # name -> CanonicalField
    disks: list = field(default_factory=list)   # ordered list[Disk]
    # Provenance-annotated edition/region/language/publisher descriptors.
    edition: Optional[str] = None
    region: Optional[str] = None
    language: Optional[str] = None
    publisher: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "release_id": self.release_id,
            "game_id": self.game_id,
            "fields": {k: v.to_dict() for k, v in self.fields.items()},
            "disks": [d.to_dict() for d in self.disks],
            "edition": self.edition,
            "region": self.region,
            "language": self.language,
            "publisher": self.publisher,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Release":
        return cls(
            release_id=d["release_id"],
            game_id=d["game_id"],
            fields={k: CanonicalField.from_dict(v) for k, v in d.get("fields", {}).items()},
            disks=[Disk.from_dict(x) for x in d.get("disks", [])],
            edition=d.get("edition"),
            region=d.get("region"),
            language=d.get("language"),
            publisher=d.get("publisher"),
        )


@dataclass
class Game:
    """The abstract title/work identity (game-level, edition-agnostic)."""

    game_id: str                      # stable slug of the title
    fields: dict = field(default_factory=dict)  # name -> CanonicalField

    def to_dict(self) -> dict:
        return {"game_id": self.game_id, "fields": {k: v.to_dict() for k, v in self.fields.items()}}

    @classmethod
    def from_dict(cls, d: dict) -> "Game":
        return cls(game_id=d["game_id"], fields={k: CanonicalField.from_dict(v) for k, v in d.get("fields", {}).items()})


def _descriptor_hash(*parts: str) -> str:
    joined = "\x1f".join(p or "" for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def make_release_id(game_id: str, edition: str = "", region: str = "",
                    language: str = "", publisher: str = "") -> str:
    """Deterministic release id from the game identity plus descriptors."""
    return f"{game_id}:{_descriptor_hash(edition, region, language, publisher)}"


def make_disk_id(sha256: str, filename: str = "") -> str:
    """Stable disk id anchored on content identity, not mutable paths."""
    if sha256:
        return f"sha256:{sha256}"
    return f"name:{_descriptor_hash(filename)}"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

class CanonicalLibrary:
    """SQLite-backed canonical library (games / releases / disks + claims).

    Stored at ``<library_root>/curation/canonical.db``. Schema versioned via
    PRAGMA user_version with deterministic forward-only migrations.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._migrate()

    def _migrate(self) -> None:
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if version >= SCHEMA_VERSION:
            return
        cur = self._conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS game (
                game_id TEXT PRIMARY KEY
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS release (
                release_id TEXT PRIMARY KEY,
                game_id TEXT NOT NULL,
                edition TEXT,
                region TEXT,
                language TEXT,
                publisher TEXT,
                FOREIGN KEY (game_id) REFERENCES game(game_id)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS disk (
                disk_id TEXT NOT NULL,
                release_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                disk_number INTEGER,
                sha256 TEXT,
                size INTEGER,
                identity_linked INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (disk_id, release_id),
                FOREIGN KEY (release_id) REFERENCES release(release_id)
                    ON DELETE CASCADE
            )
            """
        )
        # One row per claimed (entity, field, provenance): conflicts preserved.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS field_claim (
                entity_type TEXT NOT NULL,      -- game|release|disk
                entity_id TEXT NOT NULL,
                field_name TEXT NOT NULL,
                value TEXT,
                source TEXT NOT NULL,
                record_key TEXT,
                url TEXT,
                authority TEXT NOT NULL,
                authority_rank INTEGER NOT NULL DEFAULT 0,
                confidence REAL,
                observed_at TEXT,
                PRIMARY KEY (entity_type, entity_id, field_name, source, record_key, value)
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_claim_entity "
            "ON field_claim(entity_type, entity_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_release_game ON release(game_id)"
        )
        cur.execute(
            f"PRAGMA user_version = {SCHEMA_VERSION}"
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "CanonicalLibrary":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- claim writes ---------------------------------------------------------

    def claim_field(self, entity_type: str, entity_id: str, field_name: str,
                    value, provenance: Provenance) -> None:
        """Record one observed claim. INSERT OR IGNORE: conflicts accumulate."""
        cur = self._conn.execute(
            """
            INSERT OR IGNORE INTO field_claim
            (entity_type, entity_id, field_name, value, source, record_key, url,
             authority, authority_rank, confidence, observed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entity_type, entity_id, field_name,
                None if value is None else str(value),
                provenance.source, provenance.record_key, provenance.url,
                provenance.authority.tier, provenance.authority_rank,
                provenance.confidence, provenance.observed_at or _now_iso(),
            ),
        )
        self._conn.commit()

    def claims_for(self, entity_type: str, entity_id: str,
                   field_name: str) -> list:
        """All preserved claims for one field, most-preferred first."""
        rows = self._conn.execute(
            """
            SELECT * FROM field_claim
            WHERE entity_type = ? AND entity_id = ? AND field_name = ?
            """,
            (entity_type, entity_id, field_name),
        ).fetchall()
        out = []
        for r in rows:
            prov = Provenance(
                source=r["source"], record_key=r["record_key"] or "",
                url=r["url"] or "",
                authority=dict(
                    parser=SourceAuthority.PARSER, dat=SourceAuthority.DAT,
                    curation_memory=SourceAuthority.CURATION_MEMORY,
                    curation=SourceAuthority.CURATION,
                ).get(r["authority"], SourceAuthority.PARSER),
                authority_rank=r["authority_rank"],
                confidence=r["confidence"], observed_at=r["observed_at"] or "",
            )
            out.append((prov, r["value"]))
        out.sort(key=lambda pc: (pc[0].sort_key(), str(pc[1])))
        return out

    def resolve_field(self, entity_type: str, entity_id: str, field_name: str):
        """Deterministically resolve one persisted field."""
        claims = self.claims_for(entity_type, entity_id, field_name)
        if not claims:
            return None, None
        prov, value = claims[0]
        return value, prov

    def has_curation_claim(self, entity_type: str, entity_id: str,
                           field_name: str) -> bool:
        row = self._conn.execute(
            """
            SELECT 1 FROM field_claim
            WHERE entity_type = ? AND entity_id = ? AND field_name = ?
              AND authority = 'curation'
            LIMIT 1
            """,
            (entity_type, entity_id, field_name),
        ).fetchone()
        return row is not None

    # --- entity writes --------------------------------------------------------

    def upsert_game(self, game: Game) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO game (game_id) VALUES (?)", (game.game_id,)
        )
        for fname, cfield in game.fields.items():
            for prov, value in cfield.claims:
                self.claim_field("game", game.game_id, fname, value, prov)
        self._conn.commit()

    def upsert_release(self, release: Release) -> None:
        self._conn.execute(
            """
            INSERT OR IGNORE INTO release
            (release_id, game_id, edition, region, language, publisher)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (release.release_id, release.game_id, release.edition,
             release.region, release.language, release.publisher),
        )
        self._conn.execute(
            "INSERT OR IGNORE INTO game (game_id) VALUES (?)",
            (release.game_id,),
        )
        for fname, cfield in release.fields.items():
            for prov, value in cfield.claims:
                self.claim_field("release", release.release_id, fname, value, prov)
        for disk in release.disks:
            self.upsert_disk(disk)
        self._conn.commit()

    def upsert_disk(self, disk: Disk) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO disk
            (disk_id, release_id, filename, disk_number, sha256, size,
             identity_linked)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (disk.disk_id, disk.release_id, disk.filename, disk.disk_number,
             disk.sha256, disk.size, 1 if disk.identity_linked else 0),
        )
        for fname, cfield in disk.fields.items():
            for prov, value in cfield.claims:
                self.claim_field("disk", disk.disk_id, fname, value, prov)
        self._conn.commit()

    # --- reads ----------------------------------------------------------------

    def list_games(self) -> list:
        rows = self._conn.execute("SELECT game_id FROM game ORDER BY game_id").fetchall()
        return [r["game_id"] for r in rows]

    def releases_for_game(self, game_id: str) -> list:
        rows = self._conn.execute(
            "SELECT release_id FROM release WHERE game_id = ? ORDER BY release_id",
            (game_id,),
        ).fetchall()
        return [r["release_id"] for r in rows]

    def disks_for_release(self, release_id: str) -> list:
        rows = self._conn.execute(
            """
            SELECT disk_id FROM disk WHERE release_id = ?
            ORDER BY COALESCE(disk_number, 999999), disk_id
            """,
            (release_id,),
        ).fetchall()
        return [r["disk_id"] for r in rows]

    def release_row(self, release_id: str) -> Optional[dict]:
        r = self._conn.execute(
            "SELECT * FROM release WHERE release_id = ?", (release_id,)
        ).fetchone()
        if r is None:
            return None
        return dict(r)

    def disk_row(self, disk_id: str, release_id: str) -> Optional[dict]:
        r = self._conn.execute(
            "SELECT * FROM disk WHERE disk_id = ? AND release_id = ?",
            (disk_id, release_id),
        ).fetchone()
        if r is None:
            return None
        return dict(r)


# ---------------------------------------------------------------------------
# Migration from staged curation state (backwards compatible)
# ---------------------------------------------------------------------------

def migrate_staged_library(staged, library: CanonicalLibrary,
                           identity_store=None) -> dict:
    """Import an existing ``StagedLibrary`` into the canonical model.

    Backwards compatible: accepts any loaded staged state (schema v1 JSON
    via ``StagedLibrary.from_dict``); nothing is deleted from the staged
    state file. Each staged release becomes a Game + Release; each staged
    ADF filename becomes a Disk (sha256-anchored when the identity store
    can resolve the content hash). Staged operator decisions (title, folder,
    notes, state) are recorded as ``curation``-authority claims so later
    provider refreshes can never silently overwrite them. ``release_key``
    is recorded verbatim as a ``curation_memory``-authority claim for trace
    continuity with Slice 2 hash memory.

    Returns a stats dict {games, releases, disks, curation_claims}.
    """
    stats = {"games": 0, "releases": 0, "disks": 0, "curation_claims": 0}
    seen_games = set()

    releases = getattr(staged, "releases", {}) or {}
    for key, entry in releases.items():
        title = getattr(entry, "title", None) or key
        game_id = slugify_title(title)
        if game_id not in seen_games:
            seen_games.add(game_id)

        # Resolve content hashes for the release's files when possible.
        sha_by_file = {}
        if identity_store is not None:
            for fname in list(getattr(entry, "adf_files", []) or []):
                sha = _sha_for_entry_file(identity_store, entry, fname)
                if sha:
                    sha_by_file[fname] = sha

        edition = getattr(entry, "edition", None)
        region = None
        language = None
        publisher = None

        release_id = make_release_id(game_id, edition or "", region or "",
                                     language or "", publisher or "")
        release = Release(
            release_id=release_id,
            game_id=game_id,
            edition=edition,
            region=region,
            language=language,
            publisher=publisher,
        )

        now = _now_iso()
        operator = Provenance(source="operator", authority=SourceAuthority.CURATION,
                              observed_at=now)
        trace = Provenance(source="staged_migration",
                           authority=SourceAuthority.CURATION_MEMORY,
                           record_key=key, observed_at=now)
        rf = release.fields.setdefault("release_key", CanonicalField())
        rf.claim(trace, key)
        if getattr(entry, "title", None):
            rf = release.fields.setdefault("title", CanonicalField())
            rf.claim(operator, entry.title)
            stats["curation_claims"] += 1
        if getattr(entry, "folder", None):
            rf = release.fields.setdefault("folder", CanonicalField())
            rf.claim(operator, entry.folder)
            stats["curation_claims"] += 1
        notes = getattr(entry, "notes", None)
        if notes:
            rf = release.fields.setdefault("notes", CanonicalField())
            rf.claim(operator, notes)
            stats["curation_claims"] += 1
        state = getattr(entry, "curation_state", None)
        if state is not None:
            rf = release.fields.setdefault("curation_state", CanonicalField())
            rf.claim(operator, getattr(state, "value", str(state)))
            stats["curation_claims"] += 1

        for n, fname in enumerate(sorted(sha_by_file), start=1):
            sha = sha_by_file[fname]
            disk = Disk(
                disk_id=make_disk_id(sha),
                release_id=release_id,
                filename=fname,
                disk_number=n,
                sha256=sha,
                identity_linked=True,
            )
            release.disks.append(disk)
            stats["disks"] += 1
        for fname in sorted(set(getattr(entry, "adf_files", []) or []) - set(sha_by_file)):
            disk = Disk(
                disk_id=make_disk_id("", fname),
                release_id=release_id,
                filename=fname,
                disk_number=None,
                identity_linked=False,
            )
            release.disks.append(disk)
            stats["disks"] += 1

        library.upsert_release(release)
        stats["releases"] += 1

    for game_id in sorted(seen_games):
        library.upsert_game(Game(game_id=game_id))
        stats["games"] += 1

    return stats


def _sha_for_entry_file(identity_store, entry, fname: str) -> Optional[str]:
    """Best-effort sha256 lookup for one staged ADF filename."""
    getter = getattr(identity_store, "sha_for_filename", None)
    if callable(getter):
        try:
            sha = getter(getattr(entry, "release_key", ""), fname)
        except Exception:
            return None
        if isinstance(sha, str):
            return sha
    return None
