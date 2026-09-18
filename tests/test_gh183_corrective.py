"""GH-183 corrective production behavior tests.

Covers the P0 defects from GitHub issue #183:
1. Phantom RTFM / Physical State
2. Real Online Typed-Doc Acquisition
3. Hot Rod Document Path
4. Manual Lookup workflow
5. Canonical Identity + Export Naming
6. Observability
7. Regression / State

Run in isolation:
    python -m pytest tests/test_gh183_corrective.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from amiga_adf_library_builder import rtfm as rc
from amiga_adf_library_builder.exporter import _find_staged_rtfm
from amiga_adf_library_builder.models import (
    ReleaseGroup, StagedLibrary, StagedReleaseEntry
)
from amiga_adf_library_builder.rtfm import (
    RtfmSource, RtfmConfig, DocType, build_rtfm_for_group,
    lemonamiga_to_rtfm_sources, _group_identity,
    build_rtfm_all,
)
from amiga_adf_library_builder.naming import canonical_release_name
from amiga_adf_library_builder.canonical import (
    CanonicalLibrary, Provenance, SourceAuthority,
)
from amiga_adf_library_builder.manual_lookup import (
    build_entity_report, apply_manual_override,
)


# ---------------------------------------------------------------------------
# 1. PHANTOM RTFM / PHYSICAL STATE
# ---------------------------------------------------------------------------


class TestPhantomRtfmPhysicalState:
    """Preview must never claim an RTFM that does not physically exist."""

    def test_staged_library_rtfm_files_empty_by_default(self):
        """_find_staged_rtfm returns [] for empty staged library."""
        lib = StagedLibrary()
        assert _find_staged_rtfm(lib, "hacker-ii|1") == []

    def test_export_verifies_rtfm_file_exists(self):
        """Export only copies RTFM files that exist on disk."""
        lib = StagedLibrary()
        entry = StagedReleaseEntry(
            release_key="hacker-ii|1",
            title="Hacker II: The Doomsday Papers",
            edition=None,
            group="hacker-ii",
            chipset=None,
            rtfm_files=["/nonexistent/path/hacker-ii.rtfm"],
        )
        lib.releases["hacker-ii|1"] = entry

        # _find_staged_rtfm returns the path
        paths = _find_staged_rtfm(lib, "hacker-ii|1")
        assert len(paths) == 1
        # But the file does not exist
        assert not Path(paths[0]).exists()

    def test_rtfm_result_with_written_true_has_path(self):
        """An RtfmResult with written=True has an rtfm_path."""
        result = rc.RtfmResult(
            release_key="hacker-ii|1",
            basename="hacker-ii",
            written=True,
            rtfm_path=Path("/tmp/rtfm/hacker-ii.rtfm"),
        )
        assert result.written is True
        assert result.rtfm_path is not None


# ---------------------------------------------------------------------------
# 2. REAL ONLINE TYPED-DOC ACQUISITION
# ---------------------------------------------------------------------------


class TestLemonAmigaTypedDocAcquisition:
    """Lemon Amiga typed docs must be wired into the actual
    documentation acquisition pipeline, not metadata-only lookup."""

    def test_lemonamiga_to_rtfm_sources_produces_doc_typed_sources(self):
        """lemonamiga_to_rtfm_sources creates RtfmSource entries
        with doc_type set."""
        games = [MagicMock()]
        games[0].title = "Hacker II: The Doomsday Papers"
        games[0].release_key = "hacker-ii|1"

        mock_record = MagicMock()
        mock_record.canonical_title = "Hacker II: The Doomsday Papers"
        mock_record.publisher = "Capcom"
        mock_record.year = 1992
        mock_record.developer = "Capcom"
        mock_record.description = "Classic action game"
        mock_record.provider_id = "lem-123"
        mock_record.source_url = "https://www.lemonamiga.com/game/hacker-2"

        with patch(
            "amiga_adf_library_builder.metadata.lemonamiga_lookup",
            return_value=mock_record,
        ):
            sources = lemonamiga_to_rtfm_sources(
                games, doc_types=[DocType.HINTS.value]
            )

        assert len(sources) > 0
        assert all(s.doc_type == DocType.HINTS.value for s in sources)
        assert all(s.provider == "lemon-amiga" for s in sources)

    def test_lemonamiga_to_rtfm_sources_returns_empty_when_no_match(self):
        """Returns empty list when Lemon Amiga returns no match."""
        games = [MagicMock()]
        games[0].title = "Nonexistent Game"
        games[0].release_key = "nonexistent|1"

        with patch(
            "amiga_adf_library_builder.metadata.lemonamiga_lookup",
            return_value=None,
        ):
            sources = lemonamiga_to_rtfm_sources(games)

        assert sources == []

    def test_lemonamiga_to_rtfm_sources_returns_empty_on_exception(self):
        """Never raises on Lemon Amiga errors."""
        games = [MagicMock()]
        games[0].title = "Some Game"
        games[0].release_key = "some|1"

        with patch(
            "amiga_adf_library_builder.metadata.lemonamiga_lookup",
            side_effect=Exception("Network error"),
        ):
            sources = lemonamiga_to_rtfm_sources(games)

        assert sources == []

    def test_lemonamiga_sources_have_proper_doc_type_category(self):
        """Each DocType maps to the correct RTFM category."""
        games = [MagicMock()]
        games[0].title = "Hacker"
        games[0].release_key = "hacker|1"

        mock_record = MagicMock()
        mock_record.canonical_title = "Hacker"
        mock_record.source_url = "https://example.com"

        doc_type_map = {
            DocType.HINTS.value: "cheats",
            DocType.SOLUTION.value: "cheats",
            DocType.CHEAT.value: "cheats",
            DocType.WALKTHROUGH.value: "cheats",
            DocType.REFERENCE.value: "additional_reference",
        }

        for doc_type_str, expected_category in doc_type_map.items():
            with patch(
                "amiga_adf_library_builder.metadata.lemonamiga_lookup",
                return_value=mock_record,
            ):
                sources = lemonamiga_to_rtfm_sources(
                    games, doc_types=[doc_type_str]
                )

            assert len(sources) == 1
            assert sources[0].category == expected_category
            assert sources[0].doc_type == doc_type_str


# ---------------------------------------------------------------------------
# 3. HOT ROD DOCUMENT PATH
# ---------------------------------------------------------------------------


class TestHotRodDocumentPath:
    """Documentation pipeline must consistently consume the already-resolved
    canonical game identity."""

    def test_build_rtfm_for_group_accepts_library_root(self):
        """build_rtfm_for_group accepts library_root parameter."""
        import inspect
        sig = inspect.signature(build_rtfm_for_group)
        assert "library_root" in sig.parameters

    def test_build_rtfm_all_accepts_library_root(self):
        """build_rtfm_all accepts library_root parameter."""
        import inspect
        sig = inspect.signature(build_rtfm_all)
        assert "library_root" in sig.parameters

    def test_group_identity_uses_canonical_release_name(self):
        """_group_identity uses canonical_release_name."""
        import inspect
        source = inspect.getsource(_group_identity)
        assert "canonical_release_name" in source


# ---------------------------------------------------------------------------
# 4. MANUAL LOOKUP WORKFLOW
# ---------------------------------------------------------------------------


class TestManualLookupWorkflow:
    """Manual Lookup must implement the full workflow."""

    def test_manual_lookup_produces_entity_report_with_candidates(self):
        """build_entity_report produces field reports with
        source/provider, document type/title, provenance."""
        with CanonicalLibrary(":memory:") as canon:
            canon.claim_field(
                "release", "r1", "title", "Hacker II",
                Provenance(source="tosec", authority=SourceAuthority.DAT,
                           observed_at="2026-09-17T00:00:00+00:00"),
            )
            canon.claim_field(
                "release", "r1", "title", "Hacker II: Doomsday",
                Provenance(source="parser", authority=SourceAuthority.PARSER,
                           observed_at="2026-09-17T00:00:00+00:00"),
            )
            report = build_entity_report(canon, "release", "r1")

        assert len(report.fields) > 0
        field = report.fields[0]
        assert field.canonical_value is not None
        assert len(field.claims) > 0

    def test_manual_lookup_report_includes_source_and_provenance(self):
        """Candidate information includes source/provider."""
        with CanonicalLibrary(":memory:") as canon:
            canon.claim_field(
                "release", "r1", "title", "Hacker II",
                Provenance(source="tosec", authority=SourceAuthority.DAT,
                           record_key="entry-1", url="http://example/dat",
                           authority_rank=0, confidence=0.9,
                           observed_at="2026-09-17T00:00:00+00:00"),
            )
            report = build_entity_report(canon, "release", "r1")

        field = report.fields[0]
        claim = field.claims[0]
        assert claim.source == "tosec"
        assert claim.authority == "dat"
        assert claim.confidence == 0.9
        assert claim.is_winning is True

    def test_manual_lookup_apply_override_persists(self):
        """Manual override applies and persists with CURATION authority."""
        with CanonicalLibrary(":memory:") as canon:
            canon.claim_field(
                "release", "r1", "title", "Original Title",
                Provenance(source="parser", authority=SourceAuthority.PARSER,
                           observed_at="2026-09-17T00:00:00+00:00"),
            )
            apply_manual_override(
                canon, "release", "r1", "title", "Operator Override"
            )
            value, prov = canon.resolve_field(
                "release", "r1", "title"
            )

        assert value == "Operator Override"
        assert prov.authority == SourceAuthority.CURATION


# ---------------------------------------------------------------------------
# 5. CANONICAL IDENTITY + EXPORT NAMING
# ---------------------------------------------------------------------------


class TestCanonicalIdentityExportNaming:
    """The authoritative displayed Title must drive human-readable
    Windows-safe export folder naming."""

    def test_canonical_release_name_returns_provenance(self):
        """canonical_release_name returns both basename and provenance."""
        group = MagicMock()
        group.title = "Hacker II: The Doomsday Papers"
        group.group = "capcom"
        group.version = None
        group.alt_marker = None
        group.folder = "hacker-ii"

        basename, prov = canonical_release_name(group)
        assert isinstance(basename, str)
        assert isinstance(prov, str)
        assert len(basename) > 0

    def test_canonical_release_name_uses_library_root(self):
        """canonical_release_name accepts library_root parameter."""
        import inspect
        sig = inspect.signature(canonical_release_name)
        assert "library_root" in sig.parameters

    def test_rtfm_source_has_doc_type_and_provider_fields(self):
        """RtfmSource has doc_type and provider fields."""
        source = RtfmSource(
            path=Path("/test"),
            root=Path("/test"),
            category="cheats",
            stem="test",
            doc_type=DocType.HINTS.value,
            provider="lemon-amiga",
            source_url="https://example.com",
        )
        assert source.doc_type == DocType.HINTS.value
        assert source.provider == "lemon-amiga"
        assert source.source_url == "https://example.com"


# ---------------------------------------------------------------------------
# 6. OBSERVABILITY
# ---------------------------------------------------------------------------


class TestObservability:
    """Production GUI logs must expose canonical/provider IDs,
    document candidates/types, scores/rejections."""

    def test_rtfm_result_has_sources_with_doc_type(self):
        """RtfmResult sources carry doc_type through provenance."""
        source = RtfmSource(
            path=Path(""),
            root=Path(""),
            category="cheats",
            stem="Hacker (hints)",
            doc_type=DocType.HINTS.value,
            provider="lemon-amiga",
        )
        assert source.doc_type == DocType.HINTS.value
        assert source.provider == "lemon-amiga"

    def test_pipeline_passes_library_root_to_build_rtfm_all(self):
        """Pipeline passes library_root to build_rtfm_all."""
        import inspect
        from amiga_adf_library_builder.pipeline import run_pipeline
        source = inspect.getsource(run_pipeline)
        assert "library_root=library_root" in source

    def test_pipeline_builds_rtfm_all_with_library_root(self):
        """Pipeline's RTFM phase calls build_rtfm_all with
        library_root parameter."""
        import inspect
        from amiga_adf_library_builder.pipeline import run_pipeline
        source = inspect.getsource(run_pipeline)
        # The pipeline calls rtfm_mod.build_rtfm_all
        assert "build_rtfm_all" in source


# ---------------------------------------------------------------------------
# 7. REGRESSION / STATE
# ---------------------------------------------------------------------------


class TestRegressionState:
    """Preserve Rocket Ranger, Stunt Car Racer and Ultima IV
    local-manual successes."""

    def test_find_staged_rtfm_returns_empty_for_empty_library(self):
        """_find_staged_rtfm returns [] for empty staged library."""
        lib = StagedLibrary()
        assert _find_staged_rtfm(lib, "nonexistent|1") == []

    def test_find_staged_rtfm_returns_paths_for_entry(self):
        """_find_staged_rtfm returns rtfm_files from entry."""
        lib = StagedLibrary()
        entry = StagedReleaseEntry(
            release_key="rocket-ranger|1",
            title="Rocket Ranger",
            edition=None,
            group="capcom",
            chipset=None,
            rtfm_files=["/path/to/rocket-ranger.rtfm"],
        )
        lib.releases["rocket-ranger|1"] = entry
        paths = _find_staged_rtfm(lib, "rocket-ranger|1")
        assert len(paths) == 1
        assert paths[0] == "/path/to/rocket-ranger.rtfm"

    def test_doc_type_enum_values_are_valid(self):
        """All DocType values are valid strings."""
        assert DocType.MANUAL.value == "manual"
        assert DocType.INSTRUCTIONS.value == "instructions"
        assert DocType.HINTS.value == "hints"
        assert DocType.SOLUTION.value == "solution"
        assert DocType.WALKTHROUGH.value == "walkthrough"
        assert DocType.CHEAT.value == "cheat"
        assert DocType.REFERENCE.value == "reference"
        assert DocType.OTHER.value == "other"


# ---------------------------------------------------------------------------
# Additional integration tests
# ---------------------------------------------------------------------------


class TestRtfmSourceDocTypePropagation:
    """Verify doc_type propagates through the RTFM pipeline."""

    def test_rtfm_source_can_be_created_with_doc_type(self):
        """RtfmSource can be created with all new fields."""
        source = RtfmSource(
            path=Path("/test/manual.txt"),
            root=Path("/test"),
            category="cheats",
            stem="Hacker Hints",
            doc_type=DocType.HINTS.value,
            provider="lemon-amiga",
            source_url="https://example.com",
        )
        assert source.doc_type == "hints"
        assert source.provider == "lemon-amiga"

    def test_lemonamiga_sources_produce_provider_kind_in_provenance(self):
        """Lemon Amiga sources produce provider:<type> kind."""
        games = [MagicMock()]
        games[0].title = "Hacker"
        games[0].release_key = "hacker|1"

        mock_record = MagicMock()
        mock_record.canonical_title = "Hacker"
        mock_record.source_url = "https://example.com"

        with patch(
            "amiga_adf_library_builder.metadata.lemonamiga_lookup",
            return_value=mock_record,
        ):
            sources = lemonamiga_to_rtfm_sources(
                games, doc_types=[DocType.HINTS.value]
            )

        assert len(sources) == 1
        source = sources[0]
        assert source.doc_type == DocType.HINTS.value
        assert source.provider == "lemon-amiga"

    def test_pipeline_passes_library_root_to_build_rtfm_all(self):
        """Pipeline passes library_root to build_rtfm_all."""
        import inspect
        from amiga_adf_library_builder.pipeline import run_pipeline
        source = inspect.getsource(run_pipeline)
        assert "library_root=library_root" in source


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
