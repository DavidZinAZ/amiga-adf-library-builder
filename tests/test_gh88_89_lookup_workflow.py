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


if __name__ == "__main__":  # pragma: no cover
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
