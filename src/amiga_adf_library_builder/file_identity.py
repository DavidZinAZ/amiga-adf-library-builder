"""File identity store — persistent hash-based content identity & curation memory.

SQLite-backed store at ``data_dir/identity.db`` (same data_dir convention as
``metadata_sources.db``). Provides:

  * **Content identity** — SHA-256 primary key; SHA-1/MD5/CRC-32 secondary
    hashes optional (populated where a source already provides them).
  * **Path observations** — transient locations separated from identity; the
    same content at different paths shares one identity row with multiple
    observations.
  * **Curation memory** — user decisions (state, folder, notes) keyed by
    content hash so they re-apply when the same content reappears under a
    new filename/path.

Backward compatibility: absence of the identity DB degrades gracefully (the
store simply has no records). Existing ``.library_state.json`` v1 files
remain valid and loadable unchanged; their decisions can be backfilled into
``curation_memory`` via :meth:`import_staged_decisions`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1


# --- data model ---------------------------------------------------------------


@dataclass
class IdentityRecord:
    """Immutable content identity (one row of ``file_identity``)."""

    sha256: str
    size: int
    sha1: Optional[str] = None
    md5: Optional[str] = None
    crc32: Optional[str] = None
    first_seen: str = ""
    last_seen: str = ""


@dataclass
class PathObservation:
    """One observed location of a known content hash."""

    sha256: str
    path: str
    filename: str
    observed_at: str
    last_seen_at: str


@dataclass
class CurationDecision:
    """A remembered curation decision keyed by content hash."""

    sha256: str
    release_key: Optional[str] = None
    curation_state: Optional[str] = None
    title: Optional[str] = None
    folder: Optional[str] = None
    notes: Optional[str] = None
    decision_json: Optional[str] = None
    updated_at: str = ""


# --- store --------------------------------------------------------------------


class FileIdentityStore:
    """SQLite-backed file identity and curation memory store.

    Args:
        db_path: Path to the identity database. Parent dir is created on init.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._migrate()
        # In-memory memo: (path, size, mtime) -> IdentityRecord
        self._cache: dict[tuple[str, int, float], IdentityRecord] = {}

    # --- schema migration -----------------------------------------------------

    def _migrate(self) -> None:
        """Explicit deterministic v1 migration via PRAGMA user_version."""
        cur = self._conn.cursor()
        version = cur.execute("PRAGMA user_version").fetchone()[0]
        if version >= SCHEMA_VERSION:
            return
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS file_identity (
                sha256 TEXT PRIMARY KEY,
                size INTEGER NOT NULL,
                sha1 TEXT,
                md5 TEXT,
                crc32 TEXT,
                first_seen TEXT,
                last_seen TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS path_observation (
                sha256 TEXT NOT NULL,
                path TEXT NOT NULL,
                filename TEXT NOT NULL,
                observed_at TEXT,
                last_seen_at TEXT,
                PRIMARY KEY (sha256, path),
                FOREIGN KEY (sha256) REFERENCES file_identity(sha256)
                    ON DELETE CASCADE
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS curation_memory (
                sha256 TEXT PRIMARY KEY,
                release_key TEXT,
                curation_state TEXT,
                title TEXT,
                folder TEXT,
                notes TEXT,
                decision_json TEXT,
                updated_at TEXT,
                FOREIGN KEY (sha256) REFERENCES file_identity(sha256)
                    ON DELETE CASCADE
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_path_obs_sha256 "
            "ON path_observation(sha256)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_curation_release_key "
            "ON curation_memory(release_key)"
        )
        cur.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self._conn.commit()

    # --- hashing --------------------------------------------------------------

    @staticmethod
    def _sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
        """Return hex SHA-256 of a file without loading it fully into memory."""
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(chunk_size), b""):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def hash_file(path: Path) -> Optional[str]:
        """Public wrapper: SHA-256 of a file, or None on read error."""
        try:
            return FileIdentityStore._sha256_file(path)
        except (OSError, ValueError):
            return None

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    # --- file registration ----------------------------------------------------

    def register_file(
        self,
        path: Path,
        *,
        sha1: Optional[str] = None,
        md5: Optional[str] = None,
        crc32: Optional[str] = None,
        release_key: Optional[str] = None,
    ) -> Optional[IdentityRecord]:
        """Register a file's identity and observe its location.

        Hashes once; reuses the existing identity row when the same path has
        already been observed with the same size (persistent memo) or when an
        in-memory cache entry matches ``(path, size, mtime)`` within this
        process. Returns ``None`` when the file is not a regular file.
        """
        path = Path(path)
        if not path.is_file():
            return None
        try:
            stat = path.stat()
        except OSError:
            return None
        size = stat.st_size
        mtime = stat.st_mtime
        resolved = str(path)
        filename = path.name

        # Persistent memo: same path + size already observed.
        obs = self._get_observation_by_path(resolved, size)
        if obs:
            record = self.resolve_sha256(obs["sha256"])
            if record:
                self._touch_observation(obs["sha256"], resolved, filename)
                return record

        # In-memory memo: same path + size + mtime this process.
        cache_key = (resolved, size, mtime)
        if cache_key in self._cache:
            return self._cache[cache_key]

        sha256 = self._sha256_file(path)
        now = self._now_iso()
        self._upsert_identity(sha256, size, sha1, md5, crc32, now)
        self._upsert_observation(sha256, resolved, filename, now)

        record = IdentityRecord(
            sha256=sha256,
            size=size,
            sha1=sha1,
            md5=md5,
            crc32=crc32,
            first_seen=now,
            last_seen=now,
        )
        self._cache[cache_key] = record
        return record

    # --- move tracking ---------------------------------------------------------

    def note_move(
        self, old_path: str, new_path: str, sha256: str
    ) -> None:
        """Record that content moved from ``old_path`` to ``new_path``.

        Removes the old path observation, adds the new one, and updates the
        identity's last_seen timestamp. Safe to call when the old observation
        does not exist (no-op for that half).
        """
        now = self._now_iso()
        cur = self._conn.cursor()
        cur.execute(
            "DELETE FROM path_observation WHERE sha256 = ? AND path = ?",
            (sha256, str(old_path)),
        )
        filename = Path(new_path).name
        cur.execute(
            """
            INSERT INTO path_observation (sha256, path, filename, observed_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(sha256, path) DO UPDATE SET
                filename = excluded.filename,
                last_seen_at = excluded.last_seen_at
            """,
            (sha256, str(new_path), filename, now, now),
        )
        cur.execute(
            "UPDATE file_identity SET last_seen = ? WHERE sha256 = ?",
            (now, sha256),
        )
        self._conn.commit()

    # --- lookups ---------------------------------------------------------------

    def resolve_sha256(self, sha256: str) -> Optional[IdentityRecord]:
        """Return the identity record for a content hash, or ``None``."""
        cur = self._conn.execute(
            "SELECT * FROM file_identity WHERE sha256 = ?", (sha256,)
        )
        row = cur.fetchone()
        if row is None:
            return None
        return IdentityRecord(
            sha256=row["sha256"],
            size=row["size"],
            sha1=row["sha1"],
            md5=row["md5"],
            crc32=row["crc32"],
            first_seen=row["first_seen"] or "",
            last_seen=row["last_seen"] or "",
        )

    def observations_for(self, sha256: str) -> list[PathObservation]:
        """Return all path observations for a content hash."""
        cur = self._conn.execute(
            "SELECT * FROM path_observation WHERE sha256 = ?", (sha256,)
        )
        return [
            PathObservation(
                sha256=row["sha256"],
                path=row["path"],
                filename=row["filename"],
                observed_at=row["observed_at"] or "",
                last_seen_at=row["last_seen_at"] or "",
            )
            for row in cur.fetchall()
        ]

    # --- curation memory -------------------------------------------------------

    def remember_decision(
        self,
        sha256: str,
        *,
        release_key: Optional[str] = None,
        curation_state: Optional[str] = None,
        title: Optional[str] = None,
        folder: Optional[str] = None,
        notes: Optional[str] = None,
        decision_json: Optional[str] = None,
    ) -> None:
        """Persist (insert or update) a curation decision for a content hash."""
        now = self._now_iso()
        # Ensure a stub identity row exists so the FK is satisfied even when
        # the file has never been registered through register_file (e.g. a
        # decision made during metadata lookup before scanning the ADF).
        self._ensure_identity_stub(sha256)
        self._conn.execute(
            """
            INSERT INTO curation_memory
                (sha256, release_key, curation_state, title, folder, notes,
                 decision_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(sha256) DO UPDATE SET
                release_key = excluded.release_key,
                curation_state = excluded.curation_state,
                title = excluded.title,
                folder = excluded.folder,
                notes = excluded.notes,
                decision_json = excluded.decision_json,
                updated_at = excluded.updated_at
            """,
            (sha256, release_key, curation_state, title, folder, notes,
             decision_json, now),
        )
        self._conn.commit()

    def decision_for(self, sha256: str) -> Optional[CurationDecision]:
        """Return the remembered curation decision for a hash, or ``None``."""
        cur = self._conn.execute(
            "SELECT * FROM curation_memory WHERE sha256 = ?", (sha256,)
        )
        row = cur.fetchone()
        if row is None:
            return None
        return CurationDecision(
            sha256=row["sha256"],
            release_key=row["release_key"],
            curation_state=row["curation_state"],
            title=row["title"],
            folder=row["folder"],
            notes=row["notes"],
            decision_json=row["decision_json"],
            updated_at=row["updated_at"] or "",
        )

    # --- backward-compat import -----------------------------------------------

    def import_staged_decisions(
        self,
        state_path: Path,
        *,
        scan_map: Optional[dict[str, str]] = None,
        original_dir: Optional[Path] = None,
    ) -> int:
        """Backfill ``curation_memory`` from a ``.library_state.json`` v1 file.

        For each release entry, maps its ``adf_files`` filenames to SHA-256
        hashes (via ``scan_map``, or by hashing the files under
        ``original_dir``) and remembers the release's curation_state against
        each hash. Returns the number of hashes remembered.

        Graceful degradation: returns 0 when the file is missing, malformed,
        or when no sha256 mapping can be resolved for any entry.
        """
        state_path = Path(state_path)
        if not state_path.is_file():
            return 0
        try:
            with open(state_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            return 0
        if not isinstance(data, dict):
            return 0

        library = data.get("library", {})
        releases = library.get("releases", {})
        if not isinstance(releases, dict):
            return 0

        remembered = 0
        for _key, entry in releases.items():
            if not isinstance(entry, dict):
                continue
            curation_state = entry.get("curation_state")
            release_key = entry.get("release_key")
            title = entry.get("title")
            folder = entry.get("folder")
            notes = entry.get("notes")
            for fname in entry.get("adf_files", []):
                if not isinstance(fname, str):
                    continue
                sha256 = self._resolve_sha256_for_filename(
                    fname, scan_map=scan_map, original_dir=original_dir
                )
                if sha256 is None:
                    continue
                self.remember_decision(
                    sha256,
                    release_key=release_key,
                    curation_state=curation_state,
                    title=title,
                    folder=folder,
                    notes=notes,
                )
                remembered += 1
        return remembered

    def _resolve_sha256_for_filename(
        self,
        filename: str,
        *,
        scan_map: Optional[dict[str, str]],
        original_dir: Optional[Path],
    ) -> Optional[str]:
        """Resolve a filename to its SHA-256 via scan_map or original_dir."""
        if scan_map and filename in scan_map:
            return scan_map[filename]
        if original_dir is not None:
            candidate = Path(original_dir) / filename
            if candidate.is_file():
                return self._sha256_file(candidate)
        return None

    # --- internals -------------------------------------------------------------

    def _get_observation_by_path(
        self, path: str, size: int
    ) -> Optional[sqlite3.Row]:
        cur = self._conn.execute(
            "SELECT sha256, path, filename, observed_at, last_seen_at "
            "FROM path_observation WHERE path = ? AND sha256 IN "
            "(SELECT sha256 FROM file_identity WHERE size = ?)",
            (path, size),
        )
        return cur.fetchone()

    def _touch_observation(
        self, sha256: str, path: str, filename: str
    ) -> None:
        now = self._now_iso()
        self._conn.execute(
            """
            UPDATE path_observation
            SET filename = ?, last_seen_at = ?
            WHERE sha256 = ? AND path = ?
            """,
            (filename, now, sha256, path),
        )
        self._conn.execute(
            "UPDATE file_identity SET last_seen = ? WHERE sha256 = ?",
            (now, sha256),
        )
        self._conn.commit()

    def _ensure_identity_stub(self, sha256: str) -> None:
        """Insert a minimal identity row if one does not already exist.

        Used by :meth:`remember_decision` so a curation decision can be
        persisted for a hash before the file has been registered through
        :meth:`register_file`.
        """
        self._conn.execute(
            """
            INSERT OR IGNORE INTO file_identity (sha256, size, first_seen, last_seen)
            VALUES (?, 0, '', '')
            """,
            (sha256,),
        )
        self._conn.commit()

    def _upsert_identity(
        self,
        sha256: str,
        size: int,
        sha1: Optional[str],
        md5: Optional[str],
        crc32: Optional[str],
        now: str,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO file_identity (sha256, size, sha1, md5, crc32, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(sha256) DO UPDATE SET
                last_seen = excluded.last_seen
            """,
            (sha256, size, sha1, md5, crc32, now, now),
        )
        self._conn.commit()

    def _upsert_observation(
        self,
        sha256: str,
        path: str,
        filename: str,
        now: str,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO path_observation (sha256, path, filename, observed_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(sha256, path) DO UPDATE SET
                filename = excluded.filename,
                last_seen_at = excluded.last_seen_at
            """,
            (sha256, path, filename, now, now),
        )
        self._conn.commit()

    # --- lifecycle -------------------------------------------------------------

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.Error:
            pass

    def __enter__(self) -> "FileIdentityStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
