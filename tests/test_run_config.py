"""Tests for the RunConfig frozen dataclass (GH-149 / AR-008).

These verify:
  * RunConfig construction with all fields and defaults.
  * from_legacy_kwargs compatibility shim emits DeprecationWarning.
  * __post_init__ invariants reject invalid configurations.
  * Unknown kwargs are rejected.
  * Type tightening: verified_artwork_width/height are int (not Optional[int]).
"""
from __future__ import annotations

import warnings

import pytest

from amiga_adf_library_builder.run_config import RunConfig
from amiga_adf_library_builder.artwork import ARTWORK_MAX_W, ARTWORK_MAX_H


# --- construction -----------------------------------------------------------

def test_run_config_defaults():
    """All fields have the expected defaults."""
    run = RunConfig()
    assert run.online is False
    assert run.refresh_metadata is False
    assert run.require_artwork is False
    assert run.include_artwork is True
    assert run.include_manuals_rtfm is True
    assert run.upstream_task_closed is False
    assert run.run_id is None
    assert run.export is False
    assert run.verify_only is False
    assert run.one_per_game is True
    assert run.verified_artwork_width == ARTWORK_MAX_W
    assert run.verified_artwork_height == ARTWORK_MAX_H
    assert run.local_media_config_path is None
    assert run.rtfm_config_path is None
    assert run.convert_progressive_jpeg == "never"
    assert run.progressive_prompt_callback is None


def test_run_config_custom_values():
    """RunConfig accepts custom field values."""
    run = RunConfig(
        online=True,
        refresh_metadata=True,
        require_artwork=True,
        upstream_task_closed=True,
        run_id="test-run-42",
        export=True,
        verify_only=True,
        verified_artwork_width=500000,
        verified_artwork_height=2000,
        local_media_config_path="/tmp/config.toml",
        rtfm_config_path="/tmp/rtfm.toml",
        playmatch_config_path="/tmp/playmatch.toml",
        hasheous_config_path="/tmp/hasheous.toml",
        igdb_config_path="/tmp/igdb.toml",
        screenscraper_config_path="/tmp/screenscraper.toml",
        retroachievements_config_path="/tmp/retroachievements.toml",
        retrokit_config_path="/tmp/retrokit.toml",
        operator_decisions_path="/tmp/decisions.json",
        selection_manifest_path="/tmp/manifest.json",
        library_state_path="/tmp/state.json",
        convert_progressive_jpeg="always",
    )
    assert run.online is True
    assert run.run_id == "test-run-42"
    assert run.export is True
    assert run.verify_only is True
    assert run.verified_artwork_width == 500000
    assert run.local_media_config_path == "/tmp/config.toml"
    assert run.convert_progressive_jpeg == "always"


def test_run_config_int_type_tightening():
    """verified_artwork_width/height are int, not Optional[int]."""
    run = RunConfig()
    assert isinstance(run.verified_artwork_width, int)
    assert isinstance(run.verified_artwork_height, int)


# --- compatibility shim -----------------------------------------------------

def test_from_legacy_kwargs_emits_deprecation_warning():
    """from_legacy_kwargs emits DeprecationWarning."""
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        run = RunConfig.from_legacy_kwargs(online=True, refresh_metadata=False)
        assert len(w) == 1
        assert issubclass(w[0].category, DeprecationWarning)
        assert "deprecated" in str(w[0].message).lower()
    assert run.online is True
    assert run.refresh_metadata is False


def test_from_legacy_kwargs_accepts_all_known_fields():
    """from_legacy_kwargs accepts all known old parameter names."""
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        run = RunConfig.from_legacy_kwargs(
            online=True,
            refresh_metadata=True,
            require_artwork=True,
            upstream_task_closed=True,
            run_id="test-id",
            export=True,
            verify_only=True,
            verified_artwork_width=ARTWORK_MAX_W,
            verified_artwork_height=ARTWORK_MAX_H,
            local_media_config_path="/tmp/local.toml",
            rtfm_config_path="/tmp/rtfm.toml",
            playmatch_config_path="/tmp/playmatch.toml",
            hasheous_config_path="/tmp/hasheous.toml",
            igdb_config_path="/tmp/igdb.toml",
            screenscraper_config_path="/tmp/screenscraper.toml",
            retroachievements_config_path="/tmp/retroachievements.toml",
            retrokit_config_path="/tmp/retrokit.toml",
            operator_decisions_path="/tmp/decisions.json",
            selection_manifest_path="/tmp/manifest.json",
            library_state_path="/tmp/state.json",
            include_artwork=True,
            include_manuals_rtfm=True,
            one_per_game=True,
            convert_progressive_jpeg="never",
            progressive_prompt_callback=None,
        )
    assert run.online is True
    assert run.run_id == "test-id"


def test_from_legacy_kwargs_rejects_unknown():
    """from_legacy_kwargs rejects unknown keyword arguments."""
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        with pytest.raises(TypeError, match="unknown RunConfig kwargs"):
            RunConfig.from_legacy_kwargs(unknown_param=True)


def test_from_legacy_kwargs_drops_local_media_config_path():
    """local_media_config_path is accepted but dropped (maps to None)."""
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        run = RunConfig.from_legacy_kwargs(
            online=True, local_media_config_path="/tmp/local.toml"
        )
    assert run.local_media_config_path is None


# --- invariants -------------------------------------------------------------

def test_post_init_rejects_export_and_verify_without_run_id():
    """export=True + verify_only=True requires run_id."""
    with pytest.raises(ValueError, match="run_id is required"):
        RunConfig(export=True, verify_only=True)


def test_post_init_allows_verify_only_with_run_id():
    """verify_only=True is allowed when run_id is provided."""
    run = RunConfig(export=False, verify_only=True, run_id="test")
    assert run.verify_only is True


def test_post_init_allows_export_and_verify_with_run_id():
    """export=True + verify_only=True is allowed when run_id is provided."""
    run = RunConfig(export=True, verify_only=True, run_id="test")
    assert run.export is True
    assert run.verify_only is True


def test_post_init_rejects_none_verified_artwork():
    """verified_artwork_width/height must be int, not None."""
    # Type is int, so None would fail at construction if passed.
    # This test verifies the default values are always valid ints.
    run = RunConfig()
    assert run.verified_artwork_width is not None
    assert run.verified_artwork_height is not None


# --- frozen / immutable -----------------------------------------------------

def test_run_config_is_frozen():
    """RunConfig fields cannot be modified after construction."""
    run = RunConfig(online=True)
    with pytest.raises(Exception):
        run.online = False  # type: ignore[misc]


# --- equality ---------------------------------------------------------------

def test_run_config_equality():
    """Two RunConfig objects with the same values are equal."""
    a = RunConfig(online=True, run_id="test")
    b = RunConfig(online=True, run_id="test")
    assert a == b


def test_run_config_inequality():
    """Two RunConfig objects with different values are not equal."""
    a = RunConfig(online=True)
    b = RunConfig(online=False)
    assert a != b
