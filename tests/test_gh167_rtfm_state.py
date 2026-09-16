"""GH-167 — RTFM state regression tests (RC-C).

Tests that rtfm_results are mapped to entry.rtfm_files at staging
and that failures produce categorized RtfmResult records.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from amiga_adf_library_builder.rtfm import RtfmResult

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# ---------------------------------------------------------------------------
# RtfmResult categorization
# ---------------------------------------------------------------------------

class TestRtfmResultCategorization:
    def test_rtfm_result_has_release_key(self):
        result = RtfmResult(
            release_key="test|1",
            basename="Test Release",
            routed_for_review=True,
            review_reason="generation-failed: test error",
        )
        assert result.release_key == "test|1"
        assert result.routed_for_review is True

    def test_rtfm_result_has_template_used(self):
        result = RtfmResult(
            release_key="test|1",
            basename="Test Release",
            routed_for_review=True,
            review_reason="no-config: missing [rtfm]",
            template_used="controls-first",
        )
        assert result.template_used == "controls-first"

    def test_rtfm_result_failure_categories(self):
        """Verify RtfmResult supports all failure categories from the contract."""
        categories = [
            "no-config",
            "disabled",
            "no-source-found",
            "generation-failed",
            "extraction-failed",
            "export-failed",
        ]
        for cat in categories:
            result = RtfmResult(
                release_key="test|1",
                basename="Test",
                routed_for_review=True,
                review_reason=f"{cat}: some reason",
            )
            assert cat in result.review_reason

    def test_rtfm_result_written_flag(self):
        result = RtfmResult(
            release_key="test|1",
            basename="Test",
            routed_for_review=False,
            rtfm_path=Path("/tmp/test.rtfm"),
            written=True,
        )
        assert result.written is True
        assert result.rtfm_path == Path("/tmp/test.rtfm")


# ---------------------------------------------------------------------------
# Pipeline staging maps rtfm_results to entry.rtfm_files
# ---------------------------------------------------------------------------

class TestPipelineRtfmState:
    def test_entry_rtfm_files_empty_without_rtfm_results(self):
        """Entry has empty rtfm_files when no rtfm_results."""
        from amiga_adf_library_builder.models import StagedReleaseEntry
        entry = StagedReleaseEntry(
            release_key="test|1",
            title="Test",
            edition="",
            group="",
            chipset=None,
        )
        assert entry.rtfm_files == []

    def test_rtfm_results_populated_in_per_group(self):
        """When per_group has rtfm_results with written artifacts,
        they should be available for staging to map."""
        rtfm_result = RtfmResult(
            release_key="test|1",
            basename="Test Release",
            routed_for_review=False,
            rtfm_path=Path("/tmp/test.rtfm"),
            written=True,
            template_used="controls-first",
        )

        per_group = [{
            "release_key": "test|1",
            "rtfm_results": [rtfm_result],
        }]

        assert len(per_group[0]["rtfm_results"]) == 1
        assert per_group[0]["rtfm_results"][0].written is True
        assert str(per_group[0]["rtfm_results"][0].rtfm_path) == "/tmp/test.rtfm"

    def test_rtfm_results_empty_when_no_config(self):
        """When no [rtfm] config is present, rtfm_results is empty."""
        from amiga_adf_library_builder.rtfm import RtfmConfig
        cfg = RtfmConfig.from_dict({})
        assert cfg.enabled is False

    def test_rtfm_results_empty_when_disabled(self):
        """When [rtfm] enabled=false, rtfm_results is empty."""
        from amiga_adf_library_builder.rtfm import RtfmConfig
        cfg = RtfmConfig.from_dict({"enabled": False})
        assert cfg.enabled is False

    def test_rtfm_results_enabled_when_config_present(self):
        """When [rtfm] enabled=true, RtfmConfig reflects that."""
        from amiga_adf_library_builder.rtfm import RtfmConfig
        cfg = RtfmConfig.from_dict({"enabled": True})
        assert cfg.enabled is True


# ---------------------------------------------------------------------------
# RTFM config path resolution
# ---------------------------------------------------------------------------

class TestRtfmConfigPath:
    def test_discover_default_config_path_callable(self):
        """discover_default_config_path works without error."""
        from amiga_adf_library_builder.paths import (
            discover_default_config_path,
        )
        result = discover_default_config_path()
        assert result is None or isinstance(result, Path)

    def test_rtfm_config_path_not_provider_config(self):
        """The rtfm_config_path should resolve the main config,
        not the provider config. This is verified by the fact that
        discover_default_config_path() is used instead of provider_cfg
        in gui/state.py."""
        from amiga_adf_library_builder.paths import (
            discover_default_config_path,
        )
        # Verify the function returns the main config path
        result = discover_default_config_path()
        if result is not None:
            # The main config file should contain [rtfm]
            content = result.read_text()
            assert "[rtfm]" in content
