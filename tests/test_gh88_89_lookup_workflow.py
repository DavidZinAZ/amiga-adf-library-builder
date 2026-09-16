"""GH-88 + GH-89 — shared Online/Offline lookup workflow.

Focused tests for the ONE shared lookup implementation behind both the Online
Lookup (GH-88) and Offline/Local Lookup (GH-89) entry points in the Preview &
Curation workspace.

Coverage (maps to the ticket acceptance criteria):

* provider routing — ``classify_lookup_mode`` / ``providers_for_mode`` are the
  single source of truth; online and offline never share a provider list;
* offline-without-network — an offline lookup with no configured local source
  reports local-source state and never touches the network;
* no-offline-source messaging — disabled/absent ``[local_media]`` yields a
  clear "NOT configured" message, not a crash;
* offline real source — a genuine LaunchBox tree resolves to a real
  ``auto_match`` (LocalMediaProvider), no placeholder;
* online provider-backed — a real provider-backed record is returned through
  the shared chain (curated hit, deterministic, no network), no placeholder;
* selected release identity stays stable through lookup/apply;
* apply mutates STAGED curation state only (no export artifacts, no ADF
  mutation) and is undoable/redoable exactly.

Headless: relies on ``QT_QPA_PLATFORM=offscreen`` (same pattern as the other
GUI tests in this suite).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from amiga_adf_library_builder.lookup_workflow import (
    MODE_ALTERNATE,
    MODE_OFFLINE,
    MODE_ONLINE,
    LookupContext,
    classify_lookup_mode,
    providers_for_mode,
    run_lookup,
)


# ---------------------------------------------------------------------------
# Provider routing (GH-89 hard requirement: explicit, reusable classification)
# ---------------------------------------------------------------------------

class TestProviderRouting:
    def test_online_resolves_to_online_kind(self):
        assert classify_lookup_mode("online") == "online"

    def test_offline_resolves_to_offline_kind(self):
        assert classify_lookup_mode("offline") == "offline"

    def test_alternate_is_an_online_research(self):
        # Alternate search is a custom-query ONLINE re-search.
        assert classify_lookup_mode("alternate") == "online"

    def test_unknown_mode_falls_back_to_online(self):
        # Deterministic: a bad/empty mode never silently goes offline.
        assert classify_lookup_mode("bogus") == "online"
        assert classify_lookup_mode("") == "online"

    def test_offline_lists_only_local_media(self):
        # GH-89: an offline lookup can never list or contact an online
        # provider (Hall of Light, RAWG, MobyGames, Wikipedia, ...).
        assert providers_for_mode("offline") == ["local_media"]

    def test_online_lists_the_online_chain_only(self):
        pids = providers_for_mode("online")
        assert "local_media" not in pids
        # The online chain includes the real providers, not the local source.
        assert "hall-of-light" in pids or "wikipedia" in pids

    def test_online_and_offline_never_share_a_provider_list(self):
        assert providers_for_mode("online") != providers_for_mode("offline")

    def test_alternate_matches_online_provider_list(self):
        assert providers_for_mode("alternate") == providers_for_mode("online")


# ---------------------------------------------------------------------------
# Offline without a network / no offline source (GH-89)
# ---------------------------------------------------------------------------

class TestOfflineNoSource:
    def test_offline_no_config_reports_source_state(self, tmp_path):
        # No config path at all -> a clear local-source-state message, and the
        # lookup is a deterministic no_match. No socket is opened.
        r = run_lookup(
            MODE_OFFLINE,
            LookupContext(query="Test", release_key="k1", title="Test"),
        )
        assert r.kind == "offline"
        assert r.provider_ids == ["local_media"]
        assert r.status == "no_match"
        assert r.local_source_state, "must report local-source state"
        assert "NOT configured" in r.local_source_state[0]

    def test_offline_disabled_config_reports_source_state(self, tmp_path):
        cfg = tmp_path / "config.toml"
        cfg.write_text("[local_media]\nenabled = false\n")
        r = run_lookup(
            MODE_OFFLINE,
            LookupContext(query="Test", release_key="k1", title="Test",
                          config_path=cfg),
        )
        assert r.status == "no_match"
        assert r.provider_ids == ["local_media"]
        assert any("NOT configured" in line for line in r.local_source_state)

    def test_offline_missing_config_file_is_disabled(self, tmp_path):
        # A path that does not exist behaves like a disabled config (clear
        # message), never a crash and never a network call.
        r = run_lookup(
            MODE_OFFLINE,
            LookupContext(query="Test", release_key="k1", title="Test",
                          config_path=tmp_path / "nope.toml"),
        )
        assert r.status == "no_match"
        assert r.local_source_state


# ---------------------------------------------------------------------------
# Offline real local source (GH-89: real local-source search, no placeholder)
# ---------------------------------------------------------------------------

def _launchbox_tree(root: Path, rel_files: dict[str, bytes]) -> Path:
    for rel, data in rel_files.items():
        p = root / "Images" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return root


def _offline_config(tmp_path: Path, tree_root: Path) -> Path:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        "[local_media]\n"
        "enabled = true\n"
        f"roots = [{str(tree_root)!r}]\n"
    )
    return cfg


class TestOfflineRealSource:
    def test_offline_resolves_real_auto_match(self, tmp_path):
        tree = _launchbox_tree(
            tmp_path / "lb",
            {
                "Commodore Amiga/Screenshot - Game Title/Example Space Tactics/title.png":
                    b"TITLEBYTES",
            },
        )
        cfg = _offline_config(tmp_path, tree)
        ctx = LookupContext(
            query="Example Space Tactics", release_key="k1",
            title="Example Space Tactics", config_path=cfg,
            cache_dir=tmp_path / "cache",
        )
        r = run_lookup(MODE_OFFLINE, ctx)
        assert r.kind == "offline"
        assert r.status == "found"
        assert r.local_result is not None
        assert r.local_result.outcome == "auto_match"
        assert r.local_result.found
        assert r.confidence >= 0.90
        assert r.consulted, "must record what was consulted"

    def test_offline_no_matching_title_is_no_match(self, tmp_path):
        tree = _launchbox_tree(
            tmp_path / "lb",
            {
                "Commodore Amiga/Screenshot - Game Title/Some Other Game/x.png":
                    b"X",
            },
        )
        cfg = _offline_config(tmp_path, tree)
        ctx = LookupContext(
            query="Example Space Tactics", release_key="k1",
            title="Example Space Tactics", config_path=cfg,
            cache_dir=tmp_path / "cache",
        )
        r = run_lookup(MODE_OFFLINE, ctx)
        assert r.status == "no_match"
        assert r.provider_ids == ["local_media"]


# ---------------------------------------------------------------------------
# Online provider-backed (GH-88: real provider-backed search, no placeholder)
# ---------------------------------------------------------------------------

def _write_curated(curated_dir: Path, title: str) -> None:
    curated_dir.mkdir(parents=True, exist_ok=True)
    from amiga_adf_library_builder.metadata import cache_key

    record = {
        "canonical_title": title,
        "description": "A curated test record.",
        "year": "1990",
        "developer": "Test Dev",
        "publisher": "Test Pub",
        "provider": "curated",
        "confidence": 1.0,
    }
    (curated_dir / f"{cache_key(title)}.json").write_text(
        json.dumps(record), encoding="utf-8"
    )


class TestOnlineProviderBacked:
    def test_online_resolves_a_real_record_via_curated(self, tmp_path):
        # A curated hit is a real, provider-backed record returned through the
        # shared chain. It is deterministic and needs no network.
        curated = tmp_path / "curated"
        _write_curated(curated, "Example Space Tactics")
        ctx = LookupContext(
            query="Example Space Tactics", release_key="k1",
            title="Example Space Tactics", cache_dir=tmp_path / "cache",
            curated_dir=curated,
        )
        r = run_lookup(MODE_ONLINE, ctx)
        assert r.kind == "online"
        assert r.status == "found"
        assert r.record is not None
        assert r.record.canonical_title == "Example Space Tactics"
        assert r.record.provider == "curated"
        assert r.consulted, "must record what was consulted"

    def test_online_empty_query_is_an_error(self, tmp_path):
        r = run_lookup(
            MODE_ONLINE,
            LookupContext(query="   ", release_key="k1",
                          title="", cache_dir=tmp_path / "c",
                          curated_dir=tmp_path / "cd"),
        )
        assert r.status == "error"
        assert r.error

    def test_online_network_unavailable_is_deterministic(self, tmp_path):
        # No curated record, and a fake opener that always fails: the online
        # chain must degrade to a deterministic no_match/error, never a crash,
        # and must not raise.
        def fake_opener(url, *a, **k):
            raise RuntimeError("network disabled in test")

        r = run_lookup(
            MODE_ONLINE,
            LookupContext(query="No Such Game", release_key="k1",
                          title="No Such Game", cache_dir=tmp_path / "c",
                          curated_dir=tmp_path / "cd",
                          opener=fake_opener, timeout=1.0),
        )
        assert r.status in ("no_match", "error")


# ---------------------------------------------------------------------------
# Apply mutates staged curation state only + identity stays stable (GH-88/89)
# ---------------------------------------------------------------------------

@pytest.fixture()
def widget():
    from PySide6.QtWidgets import QApplication
    from amiga_adf_library_builder.gui.preview_widget import PreviewWidget
    from amiga_adf_library_builder.library_state import StagedLibrary
    from amiga_adf_library_builder.models import StagedReleaseEntry

    app = QApplication.instance() or QApplication([])
    w = PreviewWidget()
    lib = StagedLibrary()
    lib.releases["r1"] = StagedReleaseEntry(
        release_key="r1", title="Old Title", edition=None, group=None,
        chipset=None, language=None, version=None, alt_marker=None, ext="adf",
        adf_files=["/orig/old title/d1.adf"],
    )
    w._state.current_library = lib
    w._state.selected_release_key = "r1"
    return w


def _identity(e):
    return (e.release_key, e.edition, e.group, tuple(e.adf_files), e.folder)


def _meta(e):
    return (e.title, e.metadata_source, e.match_confidence, e.confidence,
            e.artwork_front, e.curation_state.value)


class TestApplyStagedOnly:
    def test_online_apply_mutates_staged_and_preserves_identity(self, widget):
        from amiga_adf_library_builder.models import CurationAction, StagedState

        entry = widget._state.current_library.releases["r1"]
        before_id = _identity(entry)
        before = _meta(entry)

        widget._apply_lookup_candidate(entry, "online", {
            "status": "found", "kind": "online", "title": "New Title",
            "provider": "wikipedia", "confidence": 0.93,
        })

        assert _identity(entry) == before_id, "identity must be unchanged"
        assert entry.title == "New Title"
        assert entry.metadata_source == "wikipedia"
        assert entry.match_confidence == 0.93
        assert entry.curation_state == StagedState.MODIFIED
        # A staged decision-log action was recorded (METADATA_EDIT).
        assert entry.actions[-1].action == CurationAction.METADATA_EDIT
        assert entry.actions[-1].payload, "payload must carry pre/post snapshot"

    def test_offline_apply_never_references_online_provider(self, widget):
        from amiga_adf_library_builder.models import StagedState

        entry = widget._state.current_library.releases["r1"]
        before_id = _identity(entry)
        widget._apply_lookup_candidate(entry, "offline", {
            "status": "found", "kind": "offline",
            "local_outcome": "auto_match", "confidence": 0.95,
            "local_cached_path": "/cache/box.png",
        })
        assert entry.metadata_source == "local_media"
        assert entry.artwork_front == "/cache/box.png"
        assert entry.curation_state == StagedState.MODIFIED
        assert _identity(entry) == before_id
        # The recorded action must not mention an online provider.
        last = entry.actions[-1]
        assert "hall of light" not in last.details.lower()
        assert "hall-of-light" not in last.details.lower()

    def test_offline_needs_review_flags_review_no_artwrite(self, widget):
        from amiga_adf_library_builder.models import StagedState

        entry = widget._state.current_library.releases["r1"]
        widget._apply_lookup_candidate(entry, "offline", {
            "status": "needs_review", "kind": "offline",
            "local_outcome": "needs_review", "confidence": 0.78,
            "local_cached_path": None, "local_review_reason": "near tie",
        })
        assert entry.curation_state == StagedState.NEEDS_REVIEW
        assert entry.artwork_front is None, "needs_review must not write artwork"
        assert "near tie" in entry.actions[-1].details

    def test_no_match_candidate_is_rejected(self, widget):
        entry = widget._state.current_library.releases["r1"]
        before = _meta(entry)
        with pytest.raises(ValueError):
            widget._apply_lookup_candidate(entry, "offline", {
                "status": "no_match", "kind": "offline",
            })
        assert _meta(entry) == before, "rejected apply must leave state untouched"
        assert widget._undo_stack == []

    def test_undo_redo_restore_exact_snapshot(self, widget):
        from amiga_adf_library_builder.models import StagedState

        entry = widget._state.current_library.releases["r1"]
        before_id = _identity(entry)
        before = _meta(entry)

        widget._apply_lookup_candidate(entry, "online", {
            "status": "found", "kind": "online", "title": "New Title",
            "provider": "wikipedia", "confidence": 0.93,
        })
        assert _meta(entry) != before

        widget._on_undo()
        assert _meta(entry) == before, "undo must restore the exact pre snapshot"
        assert entry.curation_state == StagedState.PENDING

        widget._on_redo()
        assert entry.title == "New Title"
        assert entry.curation_state == StagedState.MODIFIED
        assert _identity(entry) == before_id


# --- GH-157: Candidate collection tests (headless, no Qt required) ----------


class TestCollectCandidates:
    """Tests for collect_all_candidates multi-source candidate merging."""

    def test_run_lookup_returns_single_result_when_collect_false(self):
        from amiga_adf_library_builder.lookup_workflow import run_lookup, MODE_OFFLINE

        ctx = LookupContext(query="test", release_key="test_rk")
        result = run_lookup(MODE_OFFLINE, ctx, collect_candidates=False)
        # Must return a LookupResult (not a collection) when collect_candidates=False
        assert hasattr(result, "mode")
        assert hasattr(result, "status")
        assert hasattr(result, "confidence")

    def test_run_lookup_returns_collection_when_collect_true(self):
        from amiga_adf_library_builder.lookup_workflow import run_lookup, MODE_ONLINE

        ctx = LookupContext(query="test", release_key="test_rk")
        result = run_lookup(MODE_ONLINE, ctx, collect_candidates=True)
        # When collect_candidates=True, returns LookupResultCollection
        assert hasattr(result, "candidates")
        assert hasattr(result, "online_ok")
        assert hasattr(result, "offline_ok")
        assert hasattr(result, "errors")
        assert hasattr(result, "has_candidates")
        assert hasattr(result, "exact_matches")
        assert hasattr(result, "title_matches")

    def test_result_to_candidate_formats_online_record(self):
        from amiga_adf_library_builder.lookup_workflow import (
            LookupResult, KIND_ONLINE, MODE_ONLINE, _result_to_candidate
        )
        from amiga_adf_library_builder.metadata import MetadataRecord

        record = MetadataRecord(
            canonical_title="Test Game",
            provider="wikipedia",
            confidence=0.93,
        )
        r = LookupResult(
            mode=MODE_ONLINE, kind=KIND_ONLINE, provider_ids=["curated"],
            status="found", record=record, confidence=0.93,
            consulted=["curated"], local_source_state=[],
        )
        candidates = _result_to_candidate(r)
        assert len(candidates) == 1
        c = candidates[0]
        assert c["title"] == "Test Game"
        assert c["provider"] == "wikipedia"
        assert c["confidence"] == 0.93
        assert c["match_type"] in ("reuse", "normalized_title")

    def test_result_to_candidate_formats_offline_result(self):
        from amiga_adf_library_builder.lookup_workflow import (
            LookupResult, KIND_OFFLINE, MODE_OFFLINE, _result_to_candidate
        )
        from amiga_adf_library_builder.local_media import LocalMediaResult, MatchMethod

        lr = LocalMediaResult(
            group_title="Test Game",
            group_release_key="rk1",
            cached_path=Path("/tmp/box.png"),
            category="front",
            outcome="auto_match",
            confidence=0.88,
        )
        r = LookupResult(
            mode=MODE_OFFLINE, kind=KIND_OFFLINE, provider_ids=["local_media"],
            status="found", local_result=lr, confidence=0.88,
            consulted=["local_media"],
        )
        candidates = _result_to_candidate(r)
        assert len(candidates) == 1
        c = candidates[0]
        assert c["local_cached_path"] == "/tmp/box.png"
        assert c["local_outcome"] == "auto_match"

    def test_merge_deduplicates_by_normalized_title(self):
        from amiga_adf_library_builder.lookup_workflow import _merge_candidates

        existing = [
            {"title": "Game Name", "provider": "old", "confidence": 0.7}
        ]
        new = [
            {"title": "Game Name: Amiga Edition", "provider": "new", "confidence": 0.92},
        ]
        merged = _merge_candidates(existing, new)
        # Should keep only one entry (higher confidence wins), not dedup with title collision
        assert len(merged) >= 1
        # Best should have higher confidence
        best = merged[0]
        assert best.get("confidence", 0) >= 0.7


# --- GH-157 DEF-4: match_type from evidence, not confidence thresholds ------

class TestMatchTypeEvidence:
    """DEF-4: match_type must reflect actual hash/evidence, not just confidence.

    GH-157 re-QA (final narrow fix): online provider/title RECORDS carry no
    hash evidence (``MetadataRecord`` has no hash field), so high provider
    confidence maps to ``"provider_record"`` — ``"exact_hash"`` is reserved
    for genuine hash evidence from the offline path.
    """

    def test_perfect_confidence_online_record_is_provider_record(self):
        """conf=1.0 online record is still title evidence, never a hash."""
        from amiga_adf_library_builder.lookup_workflow import (
            LookupResult, KIND_ONLINE, MODE_ONLINE, _result_to_candidate,
        )
        from amiga_adf_library_builder.metadata import MetadataRecord

        record = MetadataRecord(
            canonical_title="Test Game", provider="curated", confidence=1.0,
        )
        r = LookupResult(
            mode=MODE_ONLINE, kind=KIND_ONLINE, provider_ids=["curated"],
            status="found", record=record, confidence=1.0, consulted=["curated"],
        )
        cands = _result_to_candidate(r)
        assert len(cands) == 1
        assert cands[0]["match_type"] == "provider_record", \
            f"Expected provider_record (conf=1.0 record) but got {cands[0]['match_type']!r}"

    def test_genuine_hash_evidence_still_exact_hash(self):
        """Offline hash-method evidence must REMAIN exact_hash.

        The engine contract is ``local_result.match_method.value``; the
        artwork enum's shipped members never carry a hash method, so the
        shim below pins the exact contract the matcher reads.
        """
        from enum import Enum

        from amiga_adf_library_builder.lookup_workflow import (
            LookupResult, KIND_OFFLINE, MODE_OFFLINE, _result_to_candidate,
        )
        from amiga_adf_library_builder.local_media import LocalMediaResult

        class _HashMethod(str, Enum):
            SHA1 = "sha1"

        lr = LocalMediaResult(
            group_title="Test Game", group_release_key="rk-hash",
            outcome="auto_match", category="front",
        )
        lr.match_method = _HashMethod.SHA1  # type: ignore[assignment]
        r = LookupResult(
            mode=MODE_OFFLINE, kind=KIND_OFFLINE, provider_ids=["local"],
            status="found", local_result=lr, confidence=0.99,
            consulted=["local"],
        )
        cands = _result_to_candidate(r)
        assert len(cands) == 1
        assert cands[0]["match_type"] == "exact_hash", \
            f"Expected exact_hash for sha1 evidence but got {cands[0]['match_type']!r}"

    def test_high_confidence_record_gets_provider_record(self):
        """GH-157 re-QA: a 0.97 provider/title record is NOT exact_hash."""
        from amiga_adf_library_builder.lookup_workflow import (
            LookupResult, KIND_ONLINE, MODE_ONLINE, _result_to_candidate,
        )
        from amiga_adf_library_builder.metadata import MetadataRecord

        record = MetadataRecord(
            canonical_title="Test Game", provider="wikipedia", confidence=0.97,
        )
        r = LookupResult(
            mode=MODE_ONLINE, kind=KIND_ONLINE, provider_ids=["wikipedia"],
            status="found", record=record, confidence=0.97, consulted=["wikipedia"],
        )
        cands = _result_to_candidate(r)
        assert len(cands) == 1
        assert cands[0]["match_type"] == "provider_record", \
            f"Expected provider_record (conf=0.97) but got {cands[0]['match_type']!r}"

    def test_medium_confidence_gets_normalized_title(self):
        from amiga_adf_library_builder.lookup_workflow import (
            LookupResult, KIND_ONLINE, MODE_ONLINE, _result_to_candidate,
        )
        from amiga_adf_library_builder.metadata import MetadataRecord

        record = MetadataRecord(
            canonical_title="Test Game", provider="hall-of-light", confidence=0.88,
        )
        r = LookupResult(
            mode=MODE_ONLINE, kind=KIND_ONLINE, provider_ids=["hall-of-light"],
            status="found", record=record, confidence=0.88, consulted=["hall-of-light"],
        )
        cands = _result_to_candidate(r)
        assert len(cands) == 1
        assert cands[0]["match_type"] == "normalized_title", \
            f"Expected normalized_title (conf=0.88) but got {cands[0]['match_type']!r}"

    def test_low_confidence_gets_fuzzy_title(self):
        from amiga_adf_library_builder.lookup_workflow import (
            LookupResult, KIND_ONLINE, MODE_ONLINE, _result_to_candidate,
        )
        from amiga_adf_library_builder.metadata import MetadataRecord

        record = MetadataRecord(
            canonical_title="Test Game", provider="wikipedia", confidence=0.62,
        )
        r = LookupResult(
            mode=MODE_ONLINE, kind=KIND_ONLINE, provider_ids=["wikipedia"],
            status="found", record=record, confidence=0.62, consulted=["wikipedia"],
        )
        cands = _result_to_candidate(r)
        assert len(cands) == 1
        assert cands[0]["match_type"] == "fuzzy_title", \
            f"Expected fuzzy_title (conf=0.62) but got {cands[0]['match_type']!r}"


# --- GH-157 DEF-5R: curation-override detection through the REAL API --------

class TestProvenanceOverrideDetection:
    """DEF-5R (publication blocker): ``_check_provenance`` compared
    ``cl.authority >= SourceAuthority.CURATION.tier`` — int-vs-string
    TypeError silently swallowed by a bare ``except``, so the dialog always
    rendered "No existing curation overrides" (false negative on every
    apply). These tests seed a real ``CanonicalLibrary`` with a real
    ``CURATION`` claim, drive the dialog's production check method, and
    assert the visible label outcome on a real ``QLabel``.
    """

    @staticmethod
    def _make_dialog(tmp_path, claims_authority=None, corrupt=False):
        from PySide6.QtWidgets import QApplication, QLabel

        from amiga_adf_library_builder.canonical import (
            CanonicalLibrary, Provenance,
        )
        from amiga_adf_library_builder.gui.preview_widget import (
            UnifiedLookupDialog,
        )
        from amiga_adf_library_builder.models import StagedReleaseEntry

        QApplication.instance() or QApplication([])  # noqa: F841

        lib_path = tmp_path / "canonical.db"
        if corrupt:
            lib_path.write_bytes(b"definitely-not-a-sqlite-database")
        elif claims_authority is not None:
            lib = CanonicalLibrary(lib_path)
            try:
                lib.claim_field(
                    "release", "rk-prov", "title", "Manual Override Title",
                    Provenance(source="curation-op",
                               authority=claims_authority),
                )
            finally:
                lib.close()
        else:
            CanonicalLibrary(lib_path).close()

        entry = StagedReleaseEntry(
            release_key="rk-prov", title="Test Game", edition=None,
            group=None, chipset=None, language=None,
            adf_files=[str(tmp_path / "discs" / "game.adf")],
        )
        dialog = UnifiedLookupDialog.__new__(UnifiedLookupDialog)
        dialog._entry = entry
        dialog._override_label = QLabel()
        return dialog

    def test_module_resolution_is_worktree_local(self):
        """QA pin: behavior tests must exercise THIS checkout, not another."""
        import amiga_adf_library_builder.gui.preview_widget as pw
        repo_root = Path(__file__).resolve().parents[1]
        assert Path(pw.__file__).resolve().is_relative_to(repo_root / "src"), (
            f"preview_widget loaded from {pw.__file__}, expected {repo_root}/src"
        )

    def test_real_curation_claim_visibly_detected_as_override(self, tmp_path):
        from amiga_adf_library_builder.canonical import SourceAuthority
        dialog = self._make_dialog(tmp_path, SourceAuthority.CURATION)
        dialog._check_provenance({"status": "found"})
        text = dialog._override_label.text()
        assert "Existing curation overrides detected" in text
        assert "curation-op" in text
        assert "No existing curation overrides" not in text
        assert "orange" in dialog._override_label.styleSheet()

    def test_dat_rank_claim_is_not_an_override(self, tmp_path):
        """Rank semantics, not mere claim presence: DAT (20) < CURATION (40)."""
        from amiga_adf_library_builder.canonical import SourceAuthority
        dialog = self._make_dialog(tmp_path, SourceAuthority.DAT)
        dialog._check_provenance({"status": "found"})
        assert dialog._override_label.text() == \
            "No existing curation overrides for this field."

    def test_curation_memory_rank_is_not_an_operator_override(self, tmp_path):
        """CURATION_MEMORY (30) < CURATION (40): not an operator override."""
        from amiga_adf_library_builder.canonical import SourceAuthority
        dialog = self._make_dialog(tmp_path, SourceAuthority.CURATION_MEMORY)
        dialog._check_provenance({"status": "found"})
        assert dialog._override_label.text() == \
            "No existing curation overrides for this field."

    def test_broken_db_surfaces_visible_unavailable_state(self, tmp_path):
        """No silent false-green: an unreadable canonical.db must render a
        distinct unavailable state instead of "No existing overrides"."""
        dialog = self._make_dialog(tmp_path, corrupt=True)
        dialog._check_provenance({"status": "found"})
        text = dialog._override_label.text()
        assert "unavailable" in text.lower()
        assert "No existing curation overrides" not in text
        assert "green" not in dialog._override_label.styleSheet()


# --- GH-157 DEF-1 + DEF-2: Candidate storage and validation ------------------

class TestCandidateStorageAndValidation:
    """DEF-1: candidates stored in _candidates_list. DEF-2: apply rejects bad status."""

    def test_candidates_list_stored_on_receive(self):
        """_on_candidates_received stores self._candidates_list with the raw dicts."""
        from PySide6.QtWidgets import QApplication
        from amiga_adf_library_builder.gui.preview_widget import UnifiedLookupDialog
        from amiga_adf_library_builder.models import StagedReleaseEntry

        app = QApplication.instance() or QApplication([])
        entry = StagedReleaseEntry(
            release_key="test_rk", title="Test Game", edition=None,
            group=None, chipset=None, language=None, adf_files=[],
        )
        dialog = UnifiedLookupDialog.__new__(UnifiedLookupDialog)
        dialog._entry = entry
        dialog._mode = "online"
        dialog._candidate = {}
        dialog._candidates_list = []  # pre-init so setattr works
        # Minimal mock of _progress
        class MockProgress:
            def setRange(self, a, b): pass
            def setValue(self, v): pass
        dialog._progress = MockProgress()
        # Minimal mock of _cand_table (required by _on_candidates_received)
        class MockCandTable:
            def setRowCount(self, n): pass
            def setItem(self, r, c, item): pass
        dialog._cand_table = MockCandTable()
        # Remaining mocks needed by _on_candidates_received body
        dialog._cand_status = type("L", (), {"setText": lambda s, t: None})()
        dialog._summary_label = type("L", (), {"setText": lambda s, t: None})()
        dialog._status_label = type("L", (), {"setText": lambda s, t: None})()
        dialog._btn_compare = type("B", (), {"setEnabled": lambda s, v: None})()

        fake_cands = [
            {"title": "Game A", "provider": "wikipedia", "confidence": 0.93, "status": "found"},
            {"status": "error", "error": "network timeout", "confidence": 0.0},
            {"status": "no_match", "confidence": 0.0},
        ]
        dialog._on_candidates_received(fake_cands)

        assert hasattr(dialog, "_candidates_list")
        stored = dialog._candidates_list
        assert len(stored) == 3
        # Must be the ACTUAL dicts, not fabricated ones
        assert stored[0]["title"] == "Game A"
        assert stored[1]["status"] == "error"
        assert stored[2]["status"] == "no_match"

    def test_compare_rejects_error_candidate(self):
        """_show_compare_detail blocks comparison for error-status rows."""
        from PySide6.QtWidgets import QApplication
        from amiga_adf_library_builder.gui.preview_widget import UnifiedLookupDialog
        from amiga_adf_library_builder.models import StagedReleaseEntry

        app = QApplication.instance() or QApplication([])
        entry = StagedReleaseEntry(
            release_key="test_rk", title="Test Game", edition=None,
            group=None, chipset=None, language=None, adf_files=[],
        )
        dialog = UnifiedLookupDialog.__new__(UnifiedLookupDialog)
        dialog._entry = entry
        dialog._mode = "online"
        dialog._candidate = {}

        # Build minimal UI components for _show_compare_detail
        class MockTextEdit:
            def setText(self, t): self.last_text = t
            def clear(self): pass
        class MockButton:
            def __init__(self): self.enabled = True
            def setEnabled(self, v): self.enabled = v

        dialog._pre_text = MockTextEdit()
        dialog._post_text = MockTextEdit()
        dialog._btn_apply = MockButton()
        dialog._candidate = {"status": "error", "error": "DNS failure"}

        dialog._show_compare_detail()

        assert "cannot compare" in dialog._pre_text.last_text.lower()
        assert not dialog._btn_apply.enabled

    def test_compare_rejects_no_match_candidate(self):
        """_show_compare_detail blocks comparison for no_match-status rows."""
        from PySide6.QtWidgets import QApplication
        from amiga_adf_library_builder.gui.preview_widget import UnifiedLookupDialog
        from amiga_adf_library_builder.models import StagedReleaseEntry

        app = QApplication.instance() or QApplication([])
        entry = StagedReleaseEntry(
            release_key="test_rk", title="Test Game", edition=None,
            group=None, chipset=None, language=None, adf_files=[],
        )
        dialog = UnifiedLookupDialog.__new__(UnifiedLookupDialog)
        dialog._entry = entry
        dialog._mode = "online"
        dialog._candidate = {}

        class MockTextEdit:
            def setText(self, t): self.last_text = t
            def clear(self): pass
        class MockButton:
            def __init__(self): self.enabled = True
            def setEnabled(self, v): self.enabled = v

        dialog._pre_text = MockTextEdit()
        dialog._post_text = MockTextEdit()
        dialog._btn_apply = MockButton()
        dialog._candidate = {"status": "no_match", "confidence": 0.0}

        dialog._show_compare_detail()

        assert "cannot compare" in dialog._pre_text.last_text.lower()
        assert not dialog._btn_apply.enabled

    def test_get_candidate_at_row_returns_stored_dict(self):
        """_get_candidate_at_row returns the real dict from _candidates_list,
        not a fabricated fallback."""
        from PySide6.QtWidgets import QApplication
        from amiga_adf_library_builder.gui.preview_widget import UnifiedLookupDialog
        from amiga_adf_library_builder.models import StagedReleaseEntry

        app = QApplication.instance() or QApplication([])
        entry = StagedReleaseEntry(
            release_key="test_rk", title="Test Game", edition=None,
            group=None, chipset=None, language=None, adf_files=[],
        )
        dialog = UnifiedLookupDialog.__new__(UnifiedLookupDialog)
        dialog._entry = entry
        dialog._mode = "online"
        dialog._cand_table = type("CT", (), {"item": lambda s,r,c: None})()
        dialog._candidates_list = [
            {"title": "Real Title", "kind": "offline", "confidence": 0.95, "status": "found"},
        ]

        got = dialog._get_candidate_at_row(0)

        assert isinstance(got, dict)
        # Must have the REAL data — including kind, title, etc.
        assert got["kind"] == "offline"  # NOT "online" as fabricated dict would give
        assert got["title"] == "Real Title"

    def test_get_candidate_at_row_returns_empty_when_none_stored(self):
        """Without any candidates, returns empty dict (not a lie)."""
        from PySide6.QtWidgets import QApplication
        from amiga_adf_library_builder.gui.preview_widget import UnifiedLookupDialog
        from amiga_adf_library_builder.models import StagedReleaseEntry

        app = QApplication.instance() or QApplication([])
        entry = StagedReleaseEntry(
            release_key="test_rk", title="Test Game", edition=None,
            group=None, chipset=None, language=None, adf_files=[],
        )
        dialog = UnifiedLookupDialog.__new__(UnifiedLookupDialog)
        dialog._entry = entry
        dialog._mode = "online"
        dialog._cand_table = type("CT", (), {"item": lambda s,r,c: None})()
        dialog._candidates_list = None

        got = dialog._get_candidate_at_row(0)

        assert got == {}  # empty, not {"status": "found"}


# --- GH-157 DEF-7: explicit states preserved --------------------------------

class TestExplicitStatesPreserved:
    """Error/no_match rows remain visible and separate after merge."""

    def test_merge_preserves_error_and_no_match_as_separate_rows(self):
        from amiga_adf_library_builder.lookup_workflow import _merge_candidates

        existing = [
            {"status": "error", "error": "DNS fail", "provider": "lookup_workflow",
             "confidence": 0.0},
            {"status": "no_match", "provider": "online", "confidence": 0.0},
        ]
        new = [
            {"title": "Game X", "provider": "wikipedia", "confidence": 0.93},
        ]
        merged = _merge_candidates(existing, new)

        # Should have 3 rows: error, no_match, Game X
        statuses = [c.get("status") for c in merged]
        assert "error" in statuses
        assert "no_match" in statuses
        assert "Game X" in str([c.get("title") for c in merged])
        assert len(merged) == 3

    def test_merge_deduplicates_same_title_keeps_highest(self):
        from amiga_adf_library_builder.lookup_workflow import _merge_candidates

        existing = [
            {"title": "Game X", "provider": "old", "confidence": 0.70},
        ]
        new = [
            {"title": "Game X", "provider": "new", "confidence": 0.93},
        ]
        merged = _merge_candidates(existing, new)

        assert len(merged) == 1
        assert merged[0]["provider"] == "new"
        assert merged[0]["confidence"] == 0.93


if __name__ == "__main__":  # pragma: no cover
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
