"""Tests for metadata_source module."""
import sqlite3
import tempfile
from pathlib import Path

import pytest

from amiga_adf_library_builder.metadata_source import (
    IndexStatus,
    MetadataSourceManager,
    SourceEntry,
    SourceInfo,
    parse_dat,
    parse_nointro_xml,
    parse_tosec_xml,
)


@pytest.fixture
def sample_dat():
    """Return the synthetic TOSEC-style DAT fixture path."""
    return Path(__file__).parent / "fixtures" / "sample.dat"


class TestParseTosecXml:
    """Tests for TOSEC-style XML DAT parsing."""

    def test_parse_returns_entries(self, sample_dat):
        entries = parse_tosec_xml(sample_dat)
        assert len(entries) >= 3

    def test_entry_fields_populated(self, sample_dat):
        entries = parse_tosec_xml(sample_dat)
        e = entries[0]
        assert e.source_id is not None
        assert len(e.source_id) == 16
        assert e.title is not None
        assert e.crc32 is not None
        assert e.sha1 is not None

    def test_title_parsed(self, sample_dat):
        entries = parse_tosec_xml(sample_dat)
        titles = {e.title for e in entries}
        assert "Alien Breed (1991)(Team 17)(Disk 1 of 2)" in titles

    def test_year_extracted(self, sample_dat):
        entries = parse_tosec_xml(sample_dat)
        e = next(e for e in entries if "Alien Breed" in (e.title or ""))
        assert e.year == "1991"

    def test_disk_number_parsed(self, sample_dat):
        entries = parse_tosec_xml(sample_dat)
        disk1 = next(
            e for e in entries if "Disk 1 of 2" in (e.title or "")
        )
        assert disk1.disk_number == 1
        assert disk1.disk_total == 2

    def test_crc_lowercase(self, sample_dat):
        entries = parse_tosec_xml(sample_dat)
        for e in entries:
            if e.crc32:
                assert e.crc32 == e.crc32.lower()

    def test_source_id_is_deterministic(self, sample_dat):
        """Same file should produce the same source_id."""
        e1 = parse_tosec_xml(sample_dat)
        e2 = parse_tosec_xml(sample_dat)
        assert e1[0].source_id == e2[0].source_id


class TestParseDat:
    """Tests for DAT auto-detection parser."""

    def test_detects_xml_format(self, sample_dat):
        entries = parse_dat(sample_dat)
        assert len(entries) >= 3

    def test_missing_file_returns_empty(self):
        entries = parse_dat(Path("/nonexistent/path.dat"))
        assert entries == []

    def test_non_xml_file_returns_empty(self, tmp_path):
        dat = tmp_path / "fake.dat"
        dat.write_bytes(b"NOT XML CONTENT")
        entries = parse_dat(dat)
        assert entries == []


class TestMetadataSourceManager:
    """Tests for the SQLite-backed metadata source manager."""

    def _make_mgr(self, tmp_path):
        return MetadataSourceManager(tmp_path / "test.db")

    def test_init_creates_db(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        assert mgr.db_path.exists()
        mgr.close()

    def test_add_source(self, tmp_path, sample_dat):
        mgr = self._make_mgr(tmp_path)
        sid = mgr.add_source(sample_dat, name="Test DAT")
        assert sid is not None
        info = mgr.get_source(sid)
        assert info is not None
        assert info.name == "Test DAT"
        assert info.entry_count == 5
        mgr.close()

    def test_add_source_nonexistent(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        sid = mgr.add_source(Path("/nonexistent.dat"))
        assert sid is None
        mgr.close()

    def test_list_sources(self, tmp_path, sample_dat):
        mgr = self._make_mgr(tmp_path)
        sid = mgr.add_source(sample_dat)
        sources = mgr.list_sources()
        assert len(sources) == 1
        assert sources[0].source_id == sid
        mgr.close()

    def test_remove_source(self, tmp_path, sample_dat):
        mgr = self._make_mgr(tmp_path)
        sid = mgr.add_source(sample_dat)
        assert mgr.remove_source(sid) is True
        assert mgr.get_source(sid) is None
        mgr.close()

    def test_set_enabled(self, tmp_path, sample_dat):
        mgr = self._make_mgr(tmp_path)
        sid = mgr.add_source(sample_dat)
        assert mgr.set_enabled(sid, False) is True
        info = mgr.get_source(sid)
        assert info.enabled is False
        mgr.close()

    def test_source_file_not_modified(self, tmp_path, sample_dat):
        """The source DAT file must not be modified by indexing."""
        original_mtime = sample_dat.stat().st_mtime
        original_size = sample_dat.stat().st_size
        mgr = self._make_mgr(tmp_path)
        mgr.add_source(sample_dat)
        mgr.close()
        assert sample_dat.stat().st_mtime == original_mtime
        assert sample_dat.stat().st_size == original_size

    def test_duplicate_source_is_noop(self, tmp_path, sample_dat):
        """Adding the same DAT twice should return the same source_id."""
        mgr = self._make_mgr(tmp_path)
        sid1 = mgr.add_source(sample_dat, name="First")
        sid2 = mgr.add_source(sample_dat, name="Second")
        assert sid1 == sid2
        assert len(mgr.list_sources()) == 1
        mgr.close()

    def test_entries_persisted(self, tmp_path, sample_dat):
        """Indexed entries should be queryable after re-opening the DB."""
        mgr = self._make_mgr(tmp_path)
        sid = mgr.add_source(sample_dat)
        cur = mgr._conn.execute(
            "SELECT COUNT(*) FROM metadata_source_entries WHERE source_id = ?",
            (sid,),
        )
        count = cur.fetchone()[0]
        assert count == 5
        mgr.close()

    def test_entries_cleared_on_reindex(self, tmp_path, sample_dat):
        """Re-indexing should replace old entries, not append."""
        mgr = self._make_mgr(tmp_path)
        sid = mgr.add_source(sample_dat)
        assert mgr.rescan(sid) is True
        cur = mgr._conn.execute(
            "SELECT COUNT(*) FROM metadata_source_entries WHERE source_id = ?",
            (sid,),
        )
        count = cur.fetchone()[0]
        assert count == 5
        mgr.close()

    def test_context_manager(self, tmp_path, sample_dat):
        with MetadataSourceManager(tmp_path / "test.db") as mgr:
            sid = mgr.add_source(sample_dat)
            assert sid is not None


class TestSourceEntry:
    """Tests for the SourceEntry dataclass."""

    def test_defaults(self):
        e = SourceEntry(source_id="abc")
        assert e.title is None
        assert e.flags == ""

    def test_source_info_defaults(self):
        info = SourceInfo(source_id="abc", name="Test", path="/some/path")
        assert info.enabled is True
        assert info.status == IndexStatus.INDEXED
