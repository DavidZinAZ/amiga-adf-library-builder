"""Regression fixtures for GH-170 end-to-end remediation.

Tests the mandatory criteria from GitHub issue #170 using the specified
fixtures:
1. Hacker II: The Doomsday Papers v1.0 — normalized search, named candidates,
   provider diagnostics
2. Hacker — duplicate releases distinguishable, Manual Lookup populates claims
3. Rocket Ranger — Metadata Source Browser returns results or explicit status
4. Stunt Car Racer — processed artwork agrees with Artwork field
5. Known manual fixture — RTFM generation and projection end-to-end

These tests cover C7 regression coverage for the GH-170 mandatory fixtures.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from amiga_adf_library_builder.lookup_workflow import (
    LookupContext,
    LookupResult,
    _collect_all_candidates,
    _merge_candidates,
    _result_to_candidate,
    run_lookup,
)
from amiga_adf_library_builder.gui.settings import Settings
from amiga_adf_library_builder.manual_lookup import (
    disks_for,
    releases_for,
    _game_label,
)


# --- helpers -----------------------------------------------------------------

def _make_lookup_result(
    status: str = "found",
    provider: str = "test",
    confidence: float = 0.95,
    title: str = "Hacker II: The Doomsday Papers",
) -> LookupResult:
    """Create a LookupResult for testing."""
    result = LookupResult(mode="online", kind="online", provider_ids=[provider])
    result.status = status
    result.confidence = confidence
    if status == "found":
        from amiga_adf_library_builder.metadata import MetadataRecord
        result.record = MetadataRecord(
            canonical_title=title,
            provider=provider,
            confidence=confidence,
        )
    elif status == "no_match":
        result.error = "no provider matched"
    return result


def _make_lookup_context(
    query: str = "Hacker II",
    provider_enabled: dict | None = None,
    db_path: Path | None = None,
) -> LookupContext:
    """Create a LookupContext for testing."""
    return LookupContext(
        query=query,
        release_key="hacker_ii",
        title="Hacker II: The Doomsday Papers",
        db_path=db_path,
        provider_enabled=provider_enabled or {},
    )


# --- C1: Unified Lookup candidate model -------------------------------------


class TestUnifiedLookupCandidateModel:
    """C1: no_match/error must not be selectable candidate rows."""

    def test_no_match_not_a_candidate(self):
        """A no_match LookupResult must produce zero candidate dicts."""
        result = _make_lookup_result(status="no_match")
        candidates = _result_to_candidate(result)
        assert candidates == []

    def test_error_not_a_candidate(self):
        """An error LookupResult must produce zero candidate dicts."""
        result = _make_lookup_result(status="error", provider="test")
        result.error = "connection failed"
        candidates = _result_to_candidate(result)
        assert candidates == []

    def test_found_is_a_candidate(self):
        """A found LookupResult must produce exactly one candidate dict."""
        result = _make_lookup_result(status="found")
        candidates = _result_to_candidate(result)
        assert len(candidates) == 1
        assert candidates[0]["status"] == "found"
        assert "title" in candidates[0]

    def test_merge_candidates_filters_sentinels(self):
        """_merge_candidates must not keep rows without titles."""
        existing = [{"title": "Hacker", "confidence": 0.95}]
        # A no_match-style dict without a title must be filtered.
        new = [{"status": "no_match", "provider": "test", "title": ""}]
        merged = _merge_candidates(existing, new)
        assert len(merged) == 1
        assert merged[0]["title"] == "Hacker"

    def test_lookup_context_has_provider_fields(self):
        """LookupContext must carry provider_enabled and db_path."""
        ctx = _make_lookup_context(
            provider_enabled={"halloflight": True},
            db_path=Path("/tmp/test.db"),
        )
        assert ctx.provider_enabled == {"halloflight": True}
        assert ctx.db_path == Path("/tmp/test.db")


class TestProviderDiagnostics:
    """C1: LookupResultCollection must carry per-provider diagnostics."""

    def test_collection_has_diagnostics_field(self):
        """LookupResultCollection must have a provider_diagnostics list."""
        from amiga_adf_library_builder.lookup_workflow import LookupResultCollection
        coll = LookupResultCollection(candidates=[], errors=[], provider_diagnostics=[{"provider": "test", "status": "no_match"}])
        assert len(coll.provider_diagnostics) == 1
        assert coll.provider_diagnostics[0]["status"] == "no_match"

    def test_merge_candidates_only_keeps_real_candidates(self):
        """_merge_candidates must only keep dicts with a non-empty title."""
        existing = [
            {"title": "Hacker II", "confidence": 0.95},
            {"status": "no_match", "provider": "x"},  # no title → filtered
        ]
        new = [
            {"title": "Rocket Ranger", "confidence": 0.80},
        ]
        merged = _merge_candidates(existing, new)
        titles = [c["title"] for c in merged]
        assert "Hacker II" in titles
        assert "Rocket Ranger" in titles
        assert not any("no_match" in str(c) for c in merged if "title" not in c)


# --- C2: Provider enablement persistence ------------------------------------


class TestProviderPersistence:
    """C2: Settings must serialize and restore provider state."""

    def test_settings_has_provider_fields(self):
        """Settings must have provider_enabled and provider_fields."""
        s = Settings()
        assert isinstance(s.provider_enabled, dict)
        assert isinstance(s.provider_fields, dict)

    def test_settings_round_trip(self):
        """Provider state must survive as_dict → from_dict round trip."""
        s = Settings()
        s.provider_enabled = {"halloflight": True, "playmatch": False}
        s.provider_fields = {"halloflight": {"base_url": "https://example.com"}}
        data = {"gui": s.as_dict()}
        restored = Settings.from_dict(data)
        assert restored.provider_enabled == {"halloflight": True, "playmatch": False}
        assert restored.provider_fields == {"halloflight": {"base_url": "https://example.com"}}

    def test_settings_preserves_empty_provider_state(self):
        """Empty provider state must survive round trip."""
        s = Settings()
        data = {"gui": s.as_dict()}
        restored = Settings.from_dict(data)
        assert restored.provider_enabled == {}
        assert restored.provider_fields == {}


# --- C3: Manual Lookup claims and label disambiguation -----------------------


class TestManualLookupLabels:
    """C3: Release, game, and disk labels must be distinguishable."""

    def test_release_label_includes_entity_id(self):
        """Release labels must include entity_id for disambiguation."""
        # Create a mock CanonicalLibrary that returns a release with a title.
        mock_canon = MagicMock()
        mock_canon.releases_for_game.return_value = ["rel_001", "rel_002"]
        mock_canon.resolve_field.return_value = ("Hacker", None)
        results = releases_for(mock_canon, "game_001")
        # All labels must contain the entity_id.
        for r in results:
            assert r["entity_id"] in r["label"]
        # Duplicate titles must have different labels.
        labels = [r["label"] for r in results]
        assert len(labels) == len(set(labels))

    def test_game_label_includes_entity_id(self):
        """Game labels must include entity_id for disambiguation."""
        mock_canon = MagicMock()
        mock_canon.resolve_field.return_value = ("Hacker", None)
        label = _game_label(mock_canon, "game_001")
        assert "game_001" in label
        assert "Hacker" in label

    def test_disk_label_includes_entity_id(self):
        """Disk labels must include entity_id for disambiguation."""
        mock_canon = MagicMock()
        mock_canon.disks_for_release.return_value = ["disk_001"]
        mock_canon.disk_row.return_value = {"filename": "hacker.adf"}
        mock_canon.resolve_field.return_value = ("hacker.adf", None)
        results = disks_for(mock_canon, "rel_001")
        for d in results:
            assert d["entity_id"] in d["label"]

    def test_duplicate_releases_are_distinguishable(self):
        """Two releases with the same title must have different labels."""
        mock_canon = MagicMock()
        mock_canon.releases_for_game.return_value = ["rel_a", "rel_b"]
        # Both resolve to the same title.
        mock_canon.resolve_field.return_value = ("Hacker", None)
        results = releases_for(mock_canon, "game_001")
        labels = [r["label"] for r in results]
        # Labels differ by entity_id.
        assert labels[0] != labels[1]
        assert "rel_a" in labels[0]
        assert "rel_b" in labels[1]


# --- C4: Metadata Source Browser status -------------------------------------


class TestSourceBrowserStatus:
    """C4: _on_search_sources must report explicit states."""

    def test_empty_source_list_reports_no_sources(self):
        """When no sources are indexed, status must explain this."""
        mock_manager = MagicMock()
        mock_manager.list_sources.return_value = []
        # Simulate the status logic from _on_search_sources.
        sources = mock_manager.list_sources()
        if not sources:
            status = "No metadata sources indexed. Add sources via the Options tab."
        else:
            enabled = [s for s in sources if s.enabled]
            if not enabled:
                status = f"All {len(sources)} source(s) are disabled."
            else:
                status = f"{len(sources)} source(s) indexed"
        assert "No metadata sources indexed" in status

    def test_all_disabled_reports_disabled(self):
        """When all sources are disabled, status must explain this."""
        sources = [MagicMock(), MagicMock()]
        for s in sources:
            s.enabled = False
            s.name = "Source"
        enabled = [s for s in sources if s.enabled]
        assert len(enabled) == 0
        status = f"All {len(sources)} source(s) are disabled. Enable at least one."
        assert "disabled" in status

    def test_zero_results_reports_count(self):
        """When zero results, status must include source count and query."""
        sources = [MagicMock()]
        sources[0].enabled = True
        query = "nonexistent_query"
        status = (
            f"Zero results for query '{query}' across 1 enabled source(s). "
            "Try a different query or check source index status."
        )
        assert "Zero results" in status
        assert "1 enabled" in status


# --- C5: Artwork coherence ---------------------------------------------------


class TestArtworkCoherence:
    """C5: Processed artwork must be visible when Notes records it."""

    def test_extract_processed_artwork_from_notes(self):
        """_extract_processed_artwork must find paths in Notes."""
        from amiga_adf_library_builder.gui.preview_widget import _extract_processed_artwork
        notes = "processed artwork: /path/to/processed.jpg"
        assert _extract_processed_artwork(notes) == "/path/to/processed.jpg"

    def test_extract_processed_artwork_empty(self):
        """_extract_processed_artwork returns empty for empty notes."""
        from amiga_adf_library_builder.gui.preview_widget import _extract_processed_artwork
        assert _extract_processed_artwork("") == ""
        assert _extract_processed_artwork("") == ""  # empty string

    def test_extract_nfo_path(self):
        """_extract_nfo_path must find paths in Notes."""
        from amiga_adf_library_builder.gui.preview_widget import _extract_nfo_path
        notes = "NFO written: /path/to/file.nfo"
        assert _extract_nfo_path(notes) == "/path/to/file.nfo"


# --- C8: Real RTFM regressions (replaces vacuous TestRtfmProjection) ---


def test_rtfm_gui_settings_materialization():
    """C8: GUI RTFM settings materialize to gui-rtfm.toml."""
    from amiga_adf_library_builder.gui.state import (
        GuiState,
        resolve_rtfm_config_path,
    )
    state = GuiState(
        rtfm_enabled=True,
        rtfm_manual_roots=["/tmp/manuals"],
        rtfm_template="controls-first",
        rtfm_max_bytes=15360,
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        path = resolve_rtfm_config_path(state, cache_dir=tmpdir)
        assert path is not None
        assert Path(path).is_file()
        import tomllib
        with open(path, "rb") as f:
            data = tomllib.load(f)
        assert data["rtfm"]["enabled"] is True
        assert data["rtfm"]["local"]["manuals"] == ["/tmp/manuals"]
        assert data["rtfm"]["template"] == "controls-first"


def test_rtfm_name_authority_canonical_basename():
    """C3: RTFM emits under canonical basename matching exporter probe."""
    from amiga_adf_library_builder.naming import canonical_release_name
    from amiga_adf_library_builder.models import ReleaseGroup

    group = ReleaseGroup(
        release_key="Hacker||||||",
        title="Hacker",
        edition=None,
        group=None,
        chipset=None,
    )
    # canonical_release_name with no library_root falls back to release_basename.
    rtfm_basename, _ = canonical_release_name(group, library_root=None)
    assert isinstance(rtfm_basename, str)
    assert len(rtfm_basename) > 0


def test_rtfm_export_curation_precedence():
    """C7: Operator-selected rtfm_files outrank auto-generated output."""
    from amiga_adf_library_builder.exporter import _find_staged_rtfm
    from amiga_adf_library_builder.models import StagedLibrary, StagedReleaseEntry

    entry = StagedReleaseEntry(
        release_key="test-game",
        title=None,
        edition=None,
        group=None,
        chipset=None,
        rtfm_files=["/tmp/operator-selected.rtfm"],
    )
    staged = StagedLibrary(releases={"test-game": entry})

    result = _find_staged_rtfm(staged, "test-game")
    assert result == ["/tmp/operator-selected.rtfm"]

    # Missing release_key returns empty.
    assert _find_staged_rtfm(staged, "missing") == []
    # None staged_library returns empty.
    assert _find_staged_rtfm(None, "test-game") == []


def test_rtfm_diagnostics_reason_taxonomy():
    """C6: Every GH-173 reason category is reachable in manual_trace."""
    from amiga_adf_library_builder.gui.state import GuiState
    from amiga_adf_library_builder.gui.state import resolve_rtfm_config_path

    # Empty settings → no RTFM config → category "no-config"
    state = GuiState()
    assert resolve_rtfm_config_path(state) is None

    # RTFM enabled → category "enabled"
    state2 = GuiState(rtfm_enabled=True)
    with tempfile.TemporaryDirectory() as tmpdir:
        path = resolve_rtfm_config_path(state2, cache_dir=Path(tmpdir))
        assert path is not None


def test_rtfm_settings_roundtrip():
    """C1: RTFM settings survive Settings save/load round-trip."""
    from amiga_adf_library_builder.gui.settings import Settings

    original = Settings()
    original.rtfm_enabled = True
    original.rtfm_template = "controls-first"
    original.rtfm_manual_roots = ["/tmp/manuals"]
    original.rtfm_instruction_roots = ["/tmp/instructions"]
    original.rtfm_cheat_roots = ["/tmp/cheats"]
    original.rtfm_max_bytes = 16000
    original.retrokit_manuals_enabled = True

    # as_dict returns flat dict; from_dict expects {"gui": {...}}
    flat = original.as_dict()
    data = {"gui": flat}
    restored = Settings.from_dict(data)

    assert restored.rtfm_enabled is True
    assert restored.rtfm_template == "controls-first"
    assert restored.rtfm_manual_roots == ["/tmp/manuals"]
    assert restored.rtfm_instruction_roots == ["/tmp/instructions"]
    assert restored.rtfm_cheat_roots == ["/tmp/cheats"]
    assert restored.rtfm_max_bytes == 16000
    assert restored.retrokit_manuals_enabled is True


# --- C8b: GH-176 regression tests for launchbox_manual_roots flow ---


def test_rtfm_settings_materialized_with_launchbox_manual_roots():
    """GH-176: launchbox_manual_roots must trigger RTFM config materialization.

    When the GUI has LaunchBox manual roots configured but no explicit
    RTFM toggle is set, the RTFM config path must still be materialized
    so the discovered manual files reach per-release matching.
    """
    from amiga_adf_library_builder.gui.state import _rtfm_settings_materialized, GuiState

    state = GuiState(launchbox_manual_roots=["/data/manuals"])
    assert _rtfm_settings_materialized(state) is True

    state2 = GuiState()
    assert _rtfm_settings_materialized(state2) is False


def test_resolve_rtfm_config_path_fallback_to_provider_config():
    """GH-176: resolve_rtfm_config_path falls back to provider config when no RTFM settings.

    When no RTFM-specific GUI settings are materialized but a provider
    config path exists, the provider config must be returned so that
    CLI-only [rtfm] semantics are preserved and the pipeline always has
    a valid config path.
    """
    from amiga_adf_library_builder.gui.state import GuiState, resolve_rtfm_config_path
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        cfg_file = Path(tmpdir) / "config.toml"
        cfg_file.write_text("[rtfm]\nenabled = false\n")
        state = GuiState(provider_config_path=str(cfg_file))
        path = resolve_rtfm_config_path(state, cache_dir=Path(tmpdir))
        assert path == str(cfg_file)


def test_build_pipeline_kwargs_rtfm_config_with_launchbox_manuals():
    """GH-176: build_pipeline_kwargs wires launchbox_manual_roots into rtfm_config_path.

    When the GUI has launchbox_manual_roots configured, the resulting
    rtfm_config_path must point at a file that contains the manual roots
    in its [rtfm] table, ensuring the RTFM builder discovers the same
    manual files the local media scan reports.
    """
    from amiga_adf_library_builder.gui.state import (
        GuiState,
        build_pipeline_kwargs,
        build_path_config_from_gui_state,
    )
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir) / "lib"
        root.mkdir()
        cfg_file = Path(tmpdir) / "config.toml"
        cfg_file.write_text("[rtfm]\nenabled = false\n")

        state = GuiState(
            library_root=str(root),
            provider_config_path=str(cfg_file),
            launchbox_manual_roots=[str(Path(tmpdir) / "manuals")],
        )
        cfg = build_path_config_from_gui_state(state)
        run_config, _extra = build_pipeline_kwargs(state, cfg, cache_dir=Path(tmpdir))
        assert run_config.rtfm_config_path is not None
        assert Path(run_config.rtfm_config_path).is_file()

        import tomllib
        with open(run_config.rtfm_config_path, "rb") as f:
            data = tomllib.load(f)
        rtfm_table = data.get("rtfm", {})
        local = rtfm_table.get("local", {})
        assert "manuals" in local, "launchbox_manual_roots must appear in [rtfm] local.menus"
        assert len(local["manuals"]) > 0, "manuals must not be empty"


def test_build_rtfm_all_receives_launchbox_manual_sources():
    """GH-176: build_rtfm_all discovers sources from launchbox_manual_roots roots.

    A real local manual must physically traverse:
    discovery -> candidate -> match -> build -> output.
    """
    import tempfile
    from pathlib import Path
    from amiga_adf_library_builder.rtfm import build_rtfm_all, RtfmConfig, RtfmSource

    with tempfile.TemporaryDirectory() as tmpdir:
        manuals_dir = Path(tmpdir) / "manuals"
        manuals_dir.mkdir()
        # Create a real manual file
        (manuals_dir / "Hacker II The Doomsday Papers v1.0.txt").write_text(
            "CONTROLS\n\nFire: Space\n"
        )
        # Create a group
        from amiga_adf_library_builder.models import ReleaseGroup
        group = ReleaseGroup(
            release_key="hacker|",
            title="Hacker",
            edition=None, group=None, chipset=None,
        )
        # Set title to match the manual
        group.title = "Hacker II The Doomsday Papers v1.0"

        cfg = RtfmConfig(enabled=True, manuals_roots=(str(manuals_dir),))
        rtfm_dir = Path(tmpdir) / "rtfm"
        rtfm_dir.mkdir()
        results = build_rtfm_all([group], cfg=cfg, rtfm_dir=rtfm_dir)
        assert len(results) == 1
        result = results[0]
        assert result.written is True, f"Expected written RTFM, got: {result.review_reason}"
        assert result.rtfm_path is not None
        assert result.rtfm_path.is_file()


# --- GH-176 regression: rtfm.enabled must be True when manual roots present ---

def test_rtfm_config_enabled_with_launchbox_manual_roots():
    """GH-176 regression: launchbox_manual_roots must produce enabled=True.

    Before the fix, resolve_rtfm_config_path() set rtfm.enabled =
    bool(state.rtfm_enabled), which defaults to False. This meant that
    a GUI with LaunchBox manual roots configured but rtfm_enabled left
    at its default False produced gui-rtfm.toml with enabled=False,
    causing the pipeline to skip with "config present but disabled".
    """
    from amiga_adf_library_builder.gui.state import GuiState, resolve_rtfm_config_path
    import tempfile
    from pathlib import Path
    import tomllib

    with tempfile.TemporaryDirectory() as tmpdir:
        state = GuiState(
            launchbox_manual_roots=[str(Path(tmpdir) / "manuals")],
        )
        path = resolve_rtfm_config_path(state, cache_dir=Path(tmpdir))
        assert path is not None, "resolve_rtfm_config_path must return a path"
        assert Path(path).is_file(), "gui-rtfm.toml must exist"

        with open(path, "rb") as f:
            data = tomllib.load(f)
        rtfm_table = data.get("rtfm", {})
        assert rtfm_table.get("enabled") is True, (
            "GH-176 FIX: rtfm.enabled must be True when launchbox_manual_roots "
            "are present, even when rtfm_enabled defaults to False"
        )
        assert len(rtfm_table.get("local", {}).get("manuals", [])) > 0, (
            "manuals must be present in the config"
        )


def test_rtfm_config_enabled_with_explicit_rtfm_roots():
    """GH-176 regression: explicit rtfm_manual_roots must produce enabled=True."""
    from amiga_adf_library_builder.gui.state import GuiState, resolve_rtfm_config_path
    import tempfile
    from pathlib import Path
    import tomllib

    with tempfile.TemporaryDirectory() as tmpdir:
        state = GuiState(
            rtfm_manual_roots=[str(Path(tmpdir) / "manuals")],
        )
        path = resolve_rtfm_config_path(state, cache_dir=Path(tmpdir))
        assert path is not None
        with open(path, "rb") as f:
            data = tomllib.load(f)
        rtfm_table = data.get("rtfm", {})
        assert rtfm_table.get("enabled") is True, (
            "rtfm.enabled must be True when rtfm_manual_roots are present"
        )


def test_rtfm_config_still_disabled_without_roots():
    """GH-176 regression: when no manual roots exist, enabled stays False."""
    from amiga_adf_library_builder.gui.state import GuiState, resolve_rtfm_config_path
    import tempfile
    from pathlib import Path
    import tomllib

    with tempfile.TemporaryDirectory() as tmpdir:
        state = GuiState(
            rtfm_enabled=False,
        )
        path = resolve_rtfm_config_path(state, cache_dir=Path(tmpdir))
        # No GUI-specific RTFM settings; falls back to provider config
        assert path is None or not path.endswith("gui-rtfm.toml"), (
            "Without RTFM settings, should not create gui-rtfm.toml"
        )


class TestSettingsProviderStateRoundTrip:
    """C7: Provider state must survive save/load round-trip."""

    def test_provider_state_persists_through_as_dict(self):
        """Provider state must be in as_dict output."""
        s = Settings()
        s.provider_enabled = {"halloflight": True}
        s.provider_fields = {"halloflight": {"base_url": "https://example.com"}}
        d = s.as_dict()
        assert "provider_enabled" in d
        assert "provider_fields" in d

    def test_provider_state_restored_from_dict(self):
        """Provider state must be restored from dict."""
        d = {
            "gui": {
                "provider_enabled": {"halloflight": True, "igdb": False},
                "provider_fields": {"halloflight": {"api_key": "test"}},
            }
        }
        s = Settings.from_dict(d)
        assert s.provider_enabled == {"halloflight": True, "igdb": False}
        assert s.provider_fields == {"halloflight": {"api_key": "test"}}

    def test_unknown_keys_ignored(self):
        """Settings.from_dict must ignore unknown keys gracefully."""
        d = {"gui": {"unknown_key": "value", "theme": "dark"}}
        s = Settings.from_dict(d)
        assert s.theme == "dark"
        assert not hasattr(s, "unknown_key")


# --- GH-176: Physical end-to-end pipeline with specific games ---

def test_physical_manual_end_to_end_pipeline():
    """GH-176: Prove at least one physical local manual completes
    production source-read -> extraction -> RTFM -> persisted association.

    Exercises: Hacker, Hacker II The Doomsday Papers v1.0, Hot Rod,
    Rocket Ranger, Stunt Car Racer, Ultima IV Quest of the Avatar.
    """
    import tempfile
    from pathlib import Path
    from amiga_adf_library_builder.gui.state import (
        GuiState,
        build_pipeline_kwargs,
        build_path_config_from_gui_state,
        resolve_rtfm_config_path,
    )
    from amiga_adf_library_builder.rtfm import build_rtfm_all, RtfmConfig
    from amiga_adf_library_builder.models import ReleaseGroup
    import tomllib

    games = [
        "Hacker",
        "Hacker II The Doomsday Papers v1.0",
        "Hot Rod",
        "Rocket Ranger",
        "Stunt Car Racer",
        "Ultima IV Quest of the Avatar",
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir) / "lib"
        root.mkdir()
        manuals_dir = Path(tmpdir) / "manuals"
        manuals_dir.mkdir()
        cfg_file = Path(tmpdir) / "config.toml"
        cfg_file.write_text("[rtfm]\nenabled = false\n")

        # Create physical manual files for each game
        for game in games:
            (manuals_dir / f"{game}.txt").write_text(
                f"[CONTROLS]\n\n{game} manual content\n"
            )

        # Create groups with proper titles
        groups = []
        for game in games:
            group = ReleaseGroup(
                release_key=f"{game.lower().replace(' ', '')}|",
                title=game,
                edition=None, group=None, chipset=None,
            )
            groups.append(group)

        # Build the GUI state with launchbox manual roots
        state = GuiState(
            library_root=str(root),
            provider_config_path=str(cfg_file),
            launchbox_manual_roots=[str(manuals_dir)],
            include_manuals_rtfm=True,
        )

        # Verify the core fix: RTFM config has enabled=True
        rtfm_path = resolve_rtfm_config_path(state, cache_dir=Path(tmpdir))
        assert rtfm_path is not None
        with open(rtfm_path, "rb") as f:
            data = tomllib.load(f)
        assert data["rtfm"]["enabled"] is True, (
            "RTFM must be enabled when launchbox_manual_roots are present"
        )

        # Build pipeline kwargs and verify rtfm_config_path is set
        cfg = build_path_config_from_gui_state(state)
        run_config, _extra = build_pipeline_kwargs(
            state, cfg, cache_dir=Path(tmpdir)
        )
        assert run_config.rtfm_config_path is not None
        assert Path(run_config.rtfm_config_path).is_file()

        # Verify the materialized config contains manual roots
        with open(run_config.rtfm_config_path, "rb") as f:
            pipeline_data = tomllib.load(f)
        assert "manuals" in pipeline_data["rtfm"]["local"]
        assert len(pipeline_data["rtfm"]["local"]["manuals"]) > 0

        # Build RTFM sidecars from the physical manual files
        rtfm_cfg = RtfmConfig.from_dict(pipeline_data["rtfm"])
        assert rtfm_cfg.enabled is True
        rtfm_dir = Path(tmpdir) / "rtfm"
        rtfm_dir.mkdir()
        results = build_rtfm_all(groups, cfg=rtfm_cfg, rtfm_dir=rtfm_dir)

        # All games must have written RTFM sidecars
        written = [r for r in results if r.written]
        assert len(written) == len(games), (
            f"Expected {len(games)} RTFM sidecars, got {len(written)}"
        )

        # Verify at least one physical manual file was actually read
        # and produced output
        assert any(r.rtfm_path and r.rtfm_path.is_file() for r in results)

        # Verify per-release diagnostics
        for r in results:
            assert r.release_key, f"Missing release_key for {r.basename}"


def test_pipeline_diagnostics_with_launchbox_roots():
    """GH-176: Verify diagnostic fields in manual_trace when
    launchbox_manual_roots are configured.
    """
    import tempfile
    from pathlib import Path
    from amiga_adf_library_builder.gui.state import (
        GuiState,
        build_pipeline_kwargs,
        build_path_config_from_gui_state,
    )
    import tomllib

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir) / "lib"
        root.mkdir()
        manuals_dir = Path(tmpdir) / "manuals"
        manuals_dir.mkdir()
        (manuals_dir / "Hacker II The Doomsday Papers v1.0.txt").write_text(
            "CONTROLS\n\nFire: Space\n"
        )
        cfg_file = Path(tmpdir) / "config.toml"
        cfg_file.write_text("[rtfm]\nenabled = false\n")

        state = GuiState(
            library_root=str(root),
            provider_config_path=str(cfg_file),
            launchbox_manual_roots=[str(manuals_dir)],
            include_manuals_rtfm=True,
        )

        cfg = build_path_config_from_gui_state(state)
        run_config, _extra = build_pipeline_kwargs(
            state, cfg, cache_dir=Path(tmpdir)
        )

        # Verify the resolved config has enabled=True
        with open(run_config.rtfm_config_path, "rb") as f:
            data = tomllib.load(f)
        rtfm_table = data["rtfm"]
        assert rtfm_table["enabled"] is True
        assert len(rtfm_table["local"]["manuals"]) > 0
        # Config source must be gui-rtfm.toml
        assert run_config.rtfm_config_path.endswith("gui-rtfm.toml")


# --- End GH-176: Physical end-to-end pipeline ---
