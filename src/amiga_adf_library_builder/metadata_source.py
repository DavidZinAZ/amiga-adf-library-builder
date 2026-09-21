"""Metadata Source Manager — DAT indexing/storage foundation.

Provides a local, read-only SQLite index of metadata sourced from DAT files
(TOSEC-style XML, No-Intro XML). The raw DAT files are NEVER modified;
parsing is idempotent and bounded. Each source is tracked with an entry
count, enabled flag, and indexing status.

Layout (under the manager's data directory):
  metadata_sources.db   SQLite database with tables:
                          metadata_source_entries  (the indexed entries)
                          metadata_sources         (source registry)
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional
from .utils import sha256_file

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1


# --- indexing status ----------------------------------------------------------
class IndexStatus:
    """Source indexing status values."""

    INDEXED = "Indexed"  # Fully indexed, up-to-date
    CLEAN = "Clean"  # Indexed, no changes detected since last scan


# --- data model ---------------------------------------------------------------
@dataclass
class SourceEntry:
    """One indexed entry from a DAT source."""

    source_id: str
    sha1: Optional[str] = None
    md5: Optional[str] = None
    crc32: Optional[str] = None
    size: Optional[int] = None
    title: Optional[str] = None
    year: Optional[str] = None
    publisher: Optional[str] = None
    region: Optional[str] = None
    language: Optional[str] = None
    disk_number: Optional[int] = None
    disk_total: Optional[int] = None
    flags: str = ""


@dataclass
class SourceInfo:
    """Tracked DAT/folder source."""

    source_id: str
    name: str
    path: str
    enabled: bool = True
    status: str = IndexStatus.INDEXED
    entry_count: int = 0
    last_indexed: Optional[str] = None
    sha256: Optional[str] = None  # content hash for change detection
    source_type: str = "dat"  # "dat" | "folder"


# --- DAT parser ---------------------------------------------------------------
def _text(elem, tag: str) -> Optional[str]:
    """Return the trimmed text of a child element, or None."""
    child = elem.find(tag)
    if child is None or child.text is None:
        return None
    return child.text.strip() or None


def _parse_disk_number(name: str) -> tuple[Optional[int], Optional[int]]:
    """Extract disk number/total from a filename like 'Game (Disk 1 of 2).adf'."""
    import re
    m = re.search(r"\(?\s*[Dd]isk\s+(\d+)\s+[Oo]f\s+(\d+)\s*\)?", name)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"\(?\s*(\d+)\s*/\s*(\d+)\s*\)?", name)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


def parse_tosec_xml(dat_path: Path, source_id: Optional[str] = None) -> list[SourceEntry]:
    """Parse a TOSEC-style XML DAT file.

    Handles the standard Logiqx DTD:
      <datafile>
        <game name="...">
          <description>...</description>
          <rom name="..." size="..." crc="..." md5="..." sha1="..."/>
        </game>
      </datafile>
    """
    entries: list[SourceEntry] = []
    if source_id is None:
        source_id = hashlib.sha256(str(dat_path.resolve()).encode("utf-8")).hexdigest()[:16]
    try:
        tree = ET.parse(dat_path)
    except ET.ParseError as exc:
        logger.warning("Failed to parse DAT %s: %s", dat_path, exc)
        return entries
    root = tree.getroot()
    for game in root.findall(".//game"):
        game_name = game.get("name", "")
        description = _text(game, "description") or game_name
        # Extract year/publisher from description if present
        year = None
        publisher = None
        import re
        ym = re.search(r"\((\d{4})\)", description)
        if ym:
            year = ym.group(1)
        pm = re.search(r"\(\d{4}\)\(([^)]+)\)", description)
        if pm:
            publisher = pm.group(1)
        for rom in game.findall("rom"):
            rom_name = rom.get("name", "")
            size = rom.get("size")
            crc = rom.get("crc")
            md5 = rom.get("md5")
            sha1 = rom.get("sha1")
            disk_number, disk_total = _parse_disk_number(rom_name)
            entries.append(
                SourceEntry(
                    source_id=source_id,
                    sha1=sha1.lower() if sha1 else None,
                    md5=md5.lower() if md5 else None,
                    crc32=crc.lower() if crc else None,
                    size=int(size) if size and size.isdigit() else None,
                    title=description,
                    year=year,
                    publisher=publisher,
                    disk_number=disk_number,
                    disk_total=disk_total,
                )
            )
    return entries


def parse_nointro_xml(dat_path: Path, source_id: Optional[str] = None) -> list[SourceEntry]:
    """Parse a No-Intro XML DAT file.

    Format:
      <datafile>
        <header><name>...</name>...</header>
        <game name="...">
          <rom name="..." size="..." crc="..." md5="..." sha1="..."/>
        </game>
      </datafile>
    """
    # No-Intro uses the same structure as TOSEC; delegate.
    return parse_tosec_xml(dat_path, source_id=source_id)


def parse_dat(dat_path: Path, source_id: Optional[str] = None) -> list[SourceEntry]:
    """Auto-detect DAT format and parse. Returns empty list on failure.

    When source_id is provided, it is used as the source identifier
    for all parsed entries (needed for folder sources where multiple
    DAT files belong to the same source).
    """
    if not dat_path.is_file():
        logger.warning("DAT file not found: %s", dat_path)
        return []
    try:
        with open(dat_path, "rb") as fh:
            header = fh.read(200)
    except OSError as exc:
        logger.warning("Cannot read %s: %s", dat_path, exc)
        return []
    if b"<?xml" in header or b"<datafile" in header:
        # Peek further to distinguish formats
        try:
            with open(dat_path, "r", encoding="utf-8", errors="replace") as fh:
                head = fh.read(2000)
        except OSError:
            head = ""
        if "no-intro" in head.lower() or "no_intro" in head.lower():
            return parse_nointro_xml(dat_path, source_id=source_id)
        return parse_tosec_xml(dat_path, source_id=source_id)
    # Unknown format: return empty (graceful degradation)
    logger.warning("Unknown DAT format: %s", dat_path)
    return []


# --- manager -----------------------------------------------------------------
class MetadataSourceManager:
    """Manages the local SQLite index of metadata sources.

    Args:
        db_path: Path to the SQLite database. Parent dir is created on init.
    """


    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._migrate()
        self._last_scan_errors: list[dict] = []

    def _migrate(self) -> None:
        """Explicit deterministic migration via PRAGMA user_version.

        Mirrors the pattern in file_identity.py. Creates tables if the
        schema version is missing or stale; otherwise a no-op.
        """
        cur = self._conn.cursor()
        version = cur.execute("PRAGMA user_version").fetchone()[0]
        if version >= SCHEMA_VERSION:
            return
        self._create_tables()
        cur.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self._conn.commit()

    def _create_tables(self) -> None:
        """Create the schema if it does not exist."""
        cur = self._conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS metadata_sources (
                source_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                path TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'Indexed',
                entry_count INTEGER NOT NULL DEFAULT 0,
                last_indexed TEXT,
                sha256 TEXT,
                source_type TEXT NOT NULL DEFAULT 'dat'
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS metadata_source_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL,
                sha1 TEXT,
                md5 TEXT,
                crc32 TEXT,
                size INTEGER,
                title TEXT,
                year TEXT,
                publisher TEXT,
                region TEXT,
                language TEXT,
                disk_number INTEGER,
                disk_total INTEGER,
                flags TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (source_id) REFERENCES metadata_sources(source_id)
                    ON DELETE CASCADE
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_entries_source "
            "ON metadata_source_entries(source_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_entries_crc "
            "ON metadata_source_entries(crc32)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_entries_sha1 "
            "ON metadata_source_entries(sha1)"
        )
        self._conn.commit()

    @staticmethod
    def _sha256_file(path: Path) -> Optional[str]:
        """Compute SHA-256 of a file, or None on error."""
        try:
            h = hashlib.sha256()
            with open(path, "rb") as fh:
                while True:
                    chunk = fh.read(1024 * 1024)
                    if not chunk:
                        break
                    h.update(chunk)
            return h.hexdigest()
        except OSError:
            return None

    def add_source(self, path: Path, *, name: Optional[str] = None,
                   source_type: str = "dat") -> Optional[str]:
        """Add a DAT file or folder as a metadata source.

        Parses the source and populates the index. The source file is never
        modified. Returns the source_id on success, None on failure.

        Idempotent: adding the same path twice (matched by content hash for
        DAT files) is a no-op and returns the existing source_id.
        """
        path = Path(path)
        if not path.exists():
            logger.warning("Source path does not exist: %s", path)
            return None
        if source_type == "dat":
            sha = self._sha256_file(path)
            if sha is None:
                return None
            # Check for duplicate by content hash
            cur = self._conn.execute(
                "SELECT source_id FROM metadata_sources WHERE sha256 = ?", (sha,)
            )
            row = cur.fetchone()
            if row:
                logger.info("Source already indexed: %s (id=%s)", path, row["source_id"])
                return row["source_id"]
        else:
            sha = None
        source_id = hashlib.sha256(
            str(path.resolve()).encode("utf-8")
        ).hexdigest()[:16]
        display_name = name or path.name
        # Parse entries
        if source_type == "dat":
            entries = parse_dat(path, source_id=source_id)
        else:
            entries, _ = self._scan_folder(path, source_id=source_id)
        # Insert source + entries atomically
        try:
            cur = self._conn.cursor()
            cur.execute(
                "INSERT OR REPLACE INTO metadata_sources "
                "(source_id, name, path, enabled, status, entry_count, last_indexed, sha256, source_type) "
                "VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?)",
                (
                    source_id,
                    display_name,
                    str(path),
                    IndexStatus.INDEXED,
                    len(entries),
                    datetime.now(timezone.utc).isoformat(),
                    sha,
                    source_type,
                ),
            )
            # Remove old entries for re-index
            cur.execute(
                "DELETE FROM metadata_source_entries WHERE source_id = ?",
                (source_id,),
            )
            now = datetime.now(timezone.utc).isoformat()
            for e in entries:
                cur.execute(
                    "INSERT INTO metadata_source_entries "
                    "(source_id, sha1, md5, crc32, size, title, year, publisher, "
                    "region, language, disk_number, disk_total, flags) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        e.source_id, e.sha1, e.md5, e.crc32, e.size,
                        e.title, e.year, e.publisher, e.region, e.language,
                        e.disk_number, e.disk_total, e.flags,
                    ),
                )
            self._conn.commit()
            logger.info("Indexed %d entries from %s (id=%s)", len(entries), path, source_id)
            return source_id
        except sqlite3.Error as exc:
            logger.error("Failed to add source %s: %s", path, exc)
            self._conn.rollback()
            return None

    def _scan_folder(self, folder: Path, source_id: Optional[str] = None) -> tuple[list[SourceEntry], list[dict]]:
        """Scan a folder for DAT files recursively.

        Returns (entries, errors) where entries is the list of parsed
        SourceEntry objects and errors is a list of dicts with 'file' and
        'reason' keys for files that failed to parse.
        """
        entries: list[SourceEntry] = []
        errors: list[dict] = []
        if not folder.is_dir():
            return entries, errors
        for dat_file in folder.rglob("*.dat"):
            try:
                parsed = parse_dat(dat_file, source_id=source_id)
                if not parsed:
                    errors.append({
                        "file": str(dat_file),
                        "reason": "Parse returned empty (malformed or unknown format)",
                    })
                entries.extend(parsed)
            except Exception as exc:
                errors.append({"file": str(dat_file), "reason": str(exc)})
        return entries, errors

    def remove_source(self, source_id: str) -> bool:
        """Remove a source and all its entries. Returns True on success."""
        try:
            self._conn.execute(
                "DELETE FROM metadata_source_entries WHERE source_id = ?",
                (source_id,),
            )
            self._conn.execute(
                "DELETE FROM metadata_sources WHERE source_id = ?",
                (source_id,),
            )
            self._conn.commit()
            return True
        except sqlite3.Error:
            self._conn.rollback()
            return False

    def set_enabled(self, source_id: str, enabled: bool) -> bool:
        """Toggle a source's enabled flag."""
        try:
            self._conn.execute(
                "UPDATE metadata_sources SET enabled = ? WHERE source_id = ?",
                (1 if enabled else 0, source_id),
            )
            self._conn.commit()
            return True
        except sqlite3.Error:
            self._conn.rollback()
            return False

    def rescan(self, source_id: str) -> bool:
        """Re-parse a DAT source and rebuild its entries.

        Preserves last-known-good entries if the new parse returns empty
        when the source previously had entries. Returns True on success,
        False on failure (preserving prior data).
        """
        row = self.get_source(source_id)
        if row is None:
            self._last_scan_errors = []
            return False
        path = Path(row.path)
        if not path.exists():
            logger.warning("Source path missing on rescan: %s", path)
            self._last_scan_errors = []
            return False
        source_type = row.source_type
        old_entry_count = row.entry_count
        # Parse new entries
        if source_type == "dat":
            entries = parse_dat(path, source_id=source_id)
            errors = []
        else:
            entries, errors = self._scan_folder(path, source_id=source_id)
        sha = self._sha256_file(path) if source_type == "dat" else None
        # Last-known-good preservation: if we had entries before and now
        # have none, treat as failure and preserve old data
        if old_entry_count > 0 and len(entries) == 0:
            logger.warning(
                "Rescan of %s returned 0 entries but source previously had "
                "%d; preserving last-known-good data. Errors: %s",
                path, old_entry_count, errors,
            )
            self._last_scan_errors = errors
            return False
        try:
            cur = self._conn.cursor()
            cur.execute(
                "UPDATE metadata_sources SET entry_count = ?, last_indexed = ?, "
                "status = ?, sha256 = ? WHERE source_id = ?",
                (
                    len(entries),
                    datetime.now(timezone.utc).isoformat(),
                    IndexStatus.INDEXED,
                    sha,
                    source_id,
                ),
            )
            cur.execute(
                "DELETE FROM metadata_source_entries WHERE source_id = ?",
                (source_id,),
            )
            for e in entries:
                cur.execute(
                    "INSERT INTO metadata_source_entries "
                    "(source_id, sha1, md5, crc32, size, title, year, publisher, "
                    "region, language, disk_number, disk_total, flags) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        e.source_id, e.sha1, e.md5, e.crc32, e.size,
                        e.title, e.year, e.publisher, e.region, e.language,
                        e.disk_number, e.disk_total, e.flags,
                    ),
                )
            self._conn.commit()
            self._last_scan_errors = errors
            if errors:
                logger.warning(
                    "Rescan of %s completed with %d file error(s): %s",
                    path, len(errors), errors,
                )
            return True
        except sqlite3.Error:
            self._conn.rollback()
            self._last_scan_errors = []
            return False

    def reindex_changed(self) -> tuple[int, int]:
        """Re-index sources whose content hash has changed.

        Returns (reindexed_count, skipped_count).
        """
        sources = self.list_sources()
        reindexed = 0
        skipped = 0
        for src in sources:
            if src.source_type != "dat":
                skipped += 1
                continue
            path = Path(src.path)
            if not path.exists():
                skipped += 1
                continue
            sha = self._sha256_file(path)
            if sha is None:
                skipped += 1
                continue
            if sha == src.sha256:
                skipped += 1
                continue
            if self.rescan(src.source_id):
                reindexed += 1
            else:
                skipped += 1
        return reindexed, skipped

    def get_source(self, source_id: str) -> Optional[SourceInfo]:
        """Return a single SourceInfo by id, or None."""
        cur = self._conn.execute(
            "SELECT * FROM metadata_sources WHERE source_id = ?", (source_id,)
        )
        row = cur.fetchone()
        if row is None:
            return None
        return self._row_to_source(row)

    def list_sources(self) -> list[SourceInfo]:
        """Return all tracked sources."""
        cur = self._conn.execute("SELECT * FROM metadata_sources ORDER BY name")
        return [self._row_to_source(row) for row in cur.fetchall()]

    @staticmethod
    def _row_to_source(row: sqlite3.Row) -> SourceInfo:
        return SourceInfo(
            source_id=row["source_id"],
            name=row["name"],
            path=row["path"],
            enabled=bool(row["enabled"]),
            status=row["status"],
            entry_count=row["entry_count"],
            last_indexed=row["last_indexed"],
            sha256=row["sha256"],
            source_type=row["source_type"],
        )

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        try:
            self._conn.close()
        except sqlite3.Error:
            pass

    def __enter__(self) -> "MetadataSourceManager":
        return self

    def lookup_by_sha256(self, sha256: str) -> list[SourceEntry]:
        """Look up indexed entries by SHA-256 hash (exact or alternate).

        Returns matching SourceEntry objects or empty list.
        Only searches enabled sources.
        """
        if not sha256:
            return []
        prefix = sha256[:16] if len(sha256) >= 16 else sha256
        cur = self._conn.execute(
            "SELECT e.* FROM metadata_source_entries e "
            "JOIN metadata_sources s ON e.source_id = s.source_id "
            "WHERE s.enabled = 1 AND (e.sha1 LIKE ? OR e.md5 LIKE ?)",
            (prefix + "%", prefix + "%"),
        )
        return [self._row_to_entry(row) for row in cur.fetchall()]

    def lookup_by_title(self, title: str, limit: int = 5) -> list[SourceEntry]:
        """Look up indexed entries by title (case-insensitive partial match).

        Returns matching SourceEntry objects or empty list.
        Only searches enabled sources.
        """
        if not title:
            return []
        pattern = f"%{title.lower()}%"
        cur = self._conn.execute(
            "SELECT e.* FROM metadata_source_entries e "
            "JOIN metadata_sources s ON e.source_id = s.source_id "
            "WHERE s.enabled = 1 AND lower(e.title) LIKE ? "
            "LIMIT ?",
            (pattern, limit),
        )
        return [self._row_to_entry(row) for row in cur.fetchall()]

    @staticmethod
    def _row_to_entry(row: "sqlite3.Row") -> SourceEntry:
        """Convert a database row to a SourceEntry."""
        return SourceEntry(
            source_id=row["source_id"],
            sha1=row["sha1"],
            md5=row["md5"],
            crc32=row["crc32"],
            size=row["size"],
            title=row["title"],
            year=row["year"],
            publisher=row["publisher"],
            region=row["region"],
            language=row["language"],
            disk_number=row["disk_number"],
            disk_total=row["disk_total"],
            flags=row["flags"],
        )

    def __exit__(self, *exc) -> None:
        self.close()
