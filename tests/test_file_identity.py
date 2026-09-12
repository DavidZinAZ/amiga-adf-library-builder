"""Tests for the file_identity module (GH-107 Slice 2)."""

import json
import tempfile
from pathlib import Path

import pytest

from amiga_adf_library_builder.file_identity import (
    CurationDecision,
    FileIdentityStore,
    IdentityRecord,
    PathObservation,
)


def _make_file(tmp_path: Path, name: str, content: bytes = b"test") -> Path:
    p = tmp_path / name
    p.write_bytes(content)
    return p


class TestSchemaMigration:
    def test_initial_schema_version(self, tmp_path):
        store = FileIdentityStore(tmp_path / "identity.db")
        cur = store._conn.execute("PRAGMA user_version")
        assert cur.fetchone()[0] == 1
        store.close()

    def test_reopen_idempotent(self, tmp_path):
        path = tmp_path / "identity.db"
        store = FileIdentityStore(path)
        store.close()
        store2 = FileIdentityStore(path)
        cur = store2._conn.execute("PRAGMA user_version")
        assert cur.fetchone()[0] == 1
        store2.close()


class TestFileRegistration:
    def test_register_new_file(self, tmp_path):
        f = _make_file(tmp_path, "game.adf", b"hello world")
        store = FileIdentityStore(tmp_path / "identity.db")
        record = store.register_file(f)
        assert record is not None
        assert record.size == 11
        assert record.sha256
        assert record.first_seen
        store.close()

    def test_register_same_path_reuses_identity(self, tmp_path):
        f = _make_file(tmp_path, "game.adf", b"same content")
        store = FileIdentityStore(tmp_path / "identity.db")
        r1 = store.register_file(f)
        r2 = store.register_file(f)
        assert r1 is not None
        assert r2 is not None
        assert r1.sha256 == r2.sha256
        assert r1.size == r2.size
        cur = store._conn.execute("SELECT COUNT(*) FROM file_identity")
        assert cur.fetchone()[0] == 1
        store.close()

    def test_register_same_content_different_paths(self, tmp_path):
        f1 = _make_file(tmp_path, "a.adf", b"shared content")
        f2 = _make_file(tmp_path, "b.adf", b"shared content")
        store = FileIdentityStore(tmp_path / "identity.db")
        r1 = store.register_file(f1)
        r2 = store.register_file(f2)
        assert r1 is not None
        assert r2 is not None
        assert r1.sha256 == r2.sha256
        obs = store.observations_for(r1.sha256)
        assert len(obs) == 2
        store.close()

    def test_register_nonexistent_file(self, tmp_path):
        store = FileIdentityStore(tmp_path / "identity.db")
        assert store.register_file(tmp_path / "nope.adf") is None
        store.close()

    def test_register_directory_returns_none(self, tmp_path):
        d = tmp_path / "subdir"
        d.mkdir()
        store = FileIdentityStore(tmp_path / "identity.db")
        assert store.register_file(d) is None
        store.close()

    def test_register_with_secondary_hashes(self, tmp_path):
        f = _make_file(tmp_path, "game.adf", b"data")
        store = FileIdentityStore(tmp_path / "identity.db")
        record = store.register_file(f, sha1="deadbeef", md5="cafebabe", crc32="1234abcd")
        assert record is not None
        assert record.sha1 == "deadbeef"
        assert record.md5 == "cafebabe"
        assert record.crc32 == "1234abcd"
        store.close()


class TestMoveTracking:
    def test_note_move(self, tmp_path):
        f = _make_file(tmp_path, "old.adf", b"content")
        store = FileIdentityStore(tmp_path / "identity.db")
        record = store.register_file(f)
        assert record is not None
        sha256 = record.sha256
        new_path = tmp_path / "new.adf"
        f.rename(new_path)
        store.note_move(str(f), str(new_path), sha256)
        obs = store.observations_for(sha256)
        assert len(obs) == 1
        assert obs[0].path == str(new_path)
        store.close()

    def test_note_move_missing_old_path(self, tmp_path):
        f = _make_file(tmp_path, "x.adf", b"data")
        store = FileIdentityStore(tmp_path / "identity.db")
        record = store.register_file(f)
        assert record is not None
        sha256 = record.sha256
        store.note_move("/nonexistent/old.adf", str(f), sha256)
        obs = store.observations_for(sha256)
        assert len(obs) == 1
        store.close()


class TestLookups:
    def test_resolve_sha256(self, tmp_path):
        f = _make_file(tmp_path, "game.adf", b"data")
        store = FileIdentityStore(tmp_path / "identity.db")
        record = store.register_file(f)
        assert record is not None
        found = store.resolve_sha256(record.sha256)
        assert found is not None
        assert found.sha256 == record.sha256
        assert found.size == record.size
        store.close()

    def test_resolve_unknown_hash(self, tmp_path):
        store = FileIdentityStore(tmp_path / "identity.db")
        assert store.resolve_sha256("0" * 64) is None
        store.close()

    def test_observations_for(self, tmp_path):
        f1 = _make_file(tmp_path, "a.adf", b"data")
        f2 = _make_file(tmp_path, "b.adf", b"data")
        store = FileIdentityStore(tmp_path / "identity.db")
        record = store.register_file(f1)
        assert record is not None
        store.register_file(f2)
        obs = store.observations_for(record.sha256)
        assert len(obs) == 2
        paths = {o.path for o in obs}
        assert str(f1) in paths
        assert str(f2) in paths
        store.close()


class TestCurationMemory:
    def test_remember_decision(self, tmp_path):
        store = FileIdentityStore(tmp_path / "identity.db")
        store.remember_decision(
            "a" * 64,
            release_key="game_001",
            curation_state="accepted",
            title="My Game",
            folder="Games",
            notes="Good one",
        )
        decision = store.decision_for("a" * 64)
        assert decision is not None
        assert decision.release_key == "game_001"
        assert decision.curation_state == "accepted"
        assert decision.title == "My Game"
        assert decision.folder == "Games"
        assert decision.notes == "Good one"
        store.close()

    def test_remember_decision_overwrites(self, tmp_path):
        store = FileIdentityStore(tmp_path / "identity.db")
        store.remember_decision("b" * 64, curation_state="pending")
        store.remember_decision("b" * 64, curation_state="accepted")
        decision = store.decision_for("b" * 64)
        assert decision is not None
        assert decision.curation_state == "accepted"
        store.close()

    def test_decision_for_unknown(self, tmp_path):
        store = FileIdentityStore(tmp_path / "identity.db")
        assert store.decision_for("c" * 64) is None
        store.close()

    def test_decision_json(self, tmp_path):
        store = FileIdentityStore(tmp_path / "identity.db")
        payload = {"key": "value", "num": 42}
        store.remember_decision("d" * 64, decision_json=json.dumps(payload))
        decision = store.decision_for("d" * 64)
        assert decision is not None
        assert decision.decision_json is not None
        assert json.loads(decision.decision_json) == payload
        store.close()


class TestImportStagedDecisions:
    def test_import_from_valid_state(self, tmp_path):
        state = {
            "library": {
                "releases": {
                    "r1": {
                        "release_key": "r1",
                        "title": "Game One",
                        "adf_files": ["a.adf", "b.adf"],
                        "curation_state": "accepted",
                        "folder": "Games",
                        "notes": "nice",
                    }
                }
            }
        }
        state_path = tmp_path / "test.library_state.json"
        state_path.write_text(json.dumps(state))
        _make_file(tmp_path, "a.adf", b"aaa")
        _make_file(tmp_path, "b.adf", b"bbb")

        store = FileIdentityStore(tmp_path / "identity.db")
        count = store.import_staged_decisions(state_path, original_dir=tmp_path)
        assert count == 2
        store.close()

    def test_import_missing_file(self, tmp_path):
        store = FileIdentityStore(tmp_path / "identity.db")
        count = store.import_staged_decisions(tmp_path / "missing.json")
        assert count == 0
        store.close()

    def test_import_malformed_json(self, tmp_path):
        state_path = tmp_path / "bad.json"
        state_path.write_text("{ not json")
        store = FileIdentityStore(tmp_path / "identity.db")
        assert store.import_staged_decisions(state_path) == 0
        store.close()

    def test_import_with_scan_map(self, tmp_path):
        state = {
            "library": {
                "releases": {
                    "r1": {
                        "release_key": "r1",
                        "adf_files": ["x.adf"],
                        "curation_state": "pending",
                    }
                }
            }
        }
        state_path = tmp_path / "test.library_state.json"
        state_path.write_text(json.dumps(state))

        store = FileIdentityStore(tmp_path / "identity.db")
        scan_map = {"x.adf": "a" * 64}
        count = store.import_staged_decisions(state_path, scan_map=scan_map)
        assert count == 1
        decision = store.decision_for("a" * 64)
        assert decision is not None
        assert decision.release_key == "r1"
        store.close()

    def test_import_skips_unresolvable(self, tmp_path):
        state = {
            "library": {
                "releases": {
                    "r1": {
                        "adf_files": ["missing.adf"],
                    }
                }
            }
        }
        state_path = tmp_path / "test.library_state.json"
        state_path.write_text(json.dumps(state))
        store = FileIdentityStore(tmp_path / "identity.db")
        count = store.import_staged_decisions(state_path)
        assert count == 0
        store.close()


class TestLifecycle:
    def test_context_manager(self, tmp_path):
        with FileIdentityStore(tmp_path / "identity.db") as store:
            record = store.register_file(_make_file(tmp_path, "a.adf"))
            assert record is not None

    def test_close(self, tmp_path):
        store = FileIdentityStore(tmp_path / "identity.db")
        store.close()
        store.close()
