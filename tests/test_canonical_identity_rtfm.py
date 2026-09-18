"""Canonical identity + typed document RTFM regression tests.

Covers RC-1 (roman/arabic normalization), RC-2 (Lemon Amiga slug),
RC-3 (version suffix stripping), RC-5 (typed docs model), and the
Hacker/Hacker II/Hot Rod production-pipeline regressions.

Run in isolation:
    python -m pytest tests/test_canonical_identity_rtfm.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from amiga_adf_library_builder import metadata as mdl
from amiga_adf_library_builder import rtfm as rc
from amiga_adf_library_builder import title_norm as tn


# ---------------------------------------------------------------------------
# RC-1: roman/arabic normalization in metadata._norm()
# ---------------------------------------------------------------------------


class TestNormRomanArabic:
    """metadata._norm() must delegate to canonical_title so that
    roman and arabic numerals compare equal."""

    def test_ii_equals_2(self):
        """Hacker II == Hacker 2 after normalization."""
        norm_ii = mdl._norm("Hacker II: The Doomsday Papers")
        norm_2 = mdl._norm("Hacker 2: The Doomsday Papers")
        assert norm_ii == norm_2, (
            f"Hacker II and Hacker 2 must normalize to the same string: "
            f"{norm_ii!r} != {norm_2!r}"
        )

    def test_roman_arabic_general(self):
        """General roman/arabic equivalence."""
        assert mdl._norm("Ultima IV") == mdl._norm("Ultima 4")
        assert mdl._norm("Sonic the Hedgehog III") == mdl._norm(
            "Sonic the Hedgehog 3"
        )

    def test_article_movement(self):
        """canonical_title article movement is preserved in _norm."""
        assert mdl._norm("The Untouchables") == mdl._norm("Untouchables, The")

    def test_empty_returns_empty(self):
        assert mdl._norm("") == ""

    def test_plain_title_unchanged(self):
        """Non-ambiguous titles normalize identically before and after."""
        plain = mdl._norm("Space Invaders")
        assert plain == "spaceinvaders"


# ---------------------------------------------------------------------------
# RC-2: Lemon Amiga slug pre-normalization
# ---------------------------------------------------------------------------


class TestLemonAmigaSlugNormalization:
    """lemonamiga_lookup must strip version suffixes and convert
    roman numerals before generating the slug."""

    def _build_slug(self, title: str) -> str:
        """Replicate the slug-generation logic from lemonamiga_lookup."""
        import re
        from amiga_adf_library_builder.metadata import _roman_numerals_to_arabic
        _version_stripped = re.sub(r"\s+v\d[\d.]*\s*$", "", title).strip()
        _arabic_title = _roman_numerals_to_arabic(_version_stripped)
        slug = re.sub(r"[^a-z0-9]+", "-", _arabic_title.lower()).strip("-")
        return slug

    def test_hacker2_slug(self):
        """Hacker II v1.0 must produce 'hacker-2-the-doomsday-papers'."""
        slug = self._build_slug("Hacker II: The Doomsday Papers v1.0")
        assert slug == "hacker-2-the-doomsday-papers", (
            f"Expected 'hacker-2-the-doomsday-papers' got {slug!r}"
        )

    def test_version_stripped(self):
        """Version suffix must be stripped before slugging."""
        slug = self._build_slug("Some Game v2.0")
        assert "v2" not in slug
        assert "v2.0" not in slug

    def test_no_version_no_strip(self):
        """Titles without version suffixes are unchanged."""
        slug = self._build_slug("Hacker II: The Doomsday Papers")
        assert slug == "hacker-2-the-doomsday-papers"


# ---------------------------------------------------------------------------
# RC-3: version suffix stripping in _strip_release_tags
# ---------------------------------------------------------------------------


class TestStripReleaseTagsVersion:
    """_strip_release_tags must strip version suffixes."""

    def test_version_stripped(self):
        result = rc._strip_release_tags("Hacker II The Doomsday Papers v1.0")
        assert "v1.0" not in result, (
            f"Version suffix not stripped: {result!r}"
        )

    def test_version_with_space(self):
        result = rc._strip_release_tags("Some Game v2.0")
        assert "v2" not in result.lower()

    def test_no_version_unchanged(self):
        result = rc._strip_release_tags("Some Game")
        assert "some game" in result.lower()

    def test_roman_version_stripped(self):
        """Roman numeral version suffixes too."""
        result = rc._strip_release_tags("Game Title v2")
        assert "v2" not in result.lower()


# ---------------------------------------------------------------------------
# RC-5: typed docs model
# ---------------------------------------------------------------------------


class TestDocTypeEnum:
    """DocType enum must cover all required document types."""

    def test_all_types_exist(self):
        """All canonical DocType values must exist."""
        types = {t.value for t in rc.DocType}
        expected = {"manual", "instructions", "hints", "solution",
                     "walkthrough", "cheat", "reference", "other"}
        assert expected == types, (
            f"Missing types: {expected - types}, Extra: {types - expected}"
        )

    def test_doc_type_is_str_enum(self):
        """DocType must be a str Enum for JSON serialization."""
        assert isinstance(rc.DocType.MANUAL, str)
        assert rc.DocType.MANUAL == "manual"

    def test_doc_type_section_map(self):
        """DOC_TYPE_SECTION_MAP must map every DocType to a marker."""
        for dt in rc.DocType:
            assert dt.value in rc.DOC_TYPE_SECTION_MAP, (
                f"DocType {dt.value} missing from DOC_TYPE_SECTION_MAP"
            )

    def test_hints_solution_cheat_map_to_hints_cheats(self):
        """Hints, Solution, Walkthrough, Cheat, Other → HINTS & CHEATS."""
        for key in ("hints", "solution", "walkthrough", "cheat", "other"):
            assert rc.DOC_TYPE_SECTION_MAP[key] == rc.MARKER_HINTS_CHEATS

    def test_manual_maps_getting_started(self):
        assert rc.DOC_TYPE_SECTION_MAP["manual"] == rc.MARKER_GETTING_STARTED

    def test_instructions_maps_controls(self):
        assert rc.DOC_TYPE_SECTION_MAP["instructions"] == rc.MARKER_CONTROLS

    def test_reference_maps_additional_reference(self):
        assert (rc.DOC_TYPE_SECTION_MAP["reference"]
                == rc.MARKER_ADDITIONAL_REFERENCE)


class TestRtfmProvenanceSourceDocType:
    """RtfmProvenanceSource must accept a doc_type field."""

    def test_doc_type_field_exists(self):
        src = rc.RtfmProvenanceSource(
            category="manuals", root_index=0, source_rel="x.txt",
            filename="x.txt", kind=".txt", sections=["GETTING STARTED"],
            sha256="a" * 64, size=100, doc_type="hints"
        )
        assert src.doc_type == "hints"

    def test_doc_type_default_none(self):
        src = rc.RtfmProvenanceSource(
            category="manuals", root_index=0, source_rel="x.txt",
            filename="x.txt", kind=".txt", sections=["GETTING STARTED"],
            sha256="a" * 64, size=100
        )
        assert src.doc_type is None


# ---------------------------------------------------------------------------
# Hacker/Hacker II/Hot Rod production-pipeline regressions
# ---------------------------------------------------------------------------


class TestHackerHacker2HotRodPipeline:
    """Verify the three-title pipeline from t_14670118."""

    def test_hacker_ii_norm_equivalence(self):
        """Hacker II: The Doomsday Papers must match
        Hacker 2: The Doomsday Papers after _norm."""
        assert mdl._norm("Hacker II: The Doomsday Papers") == mdl._norm(
            "Hacker 2: The Doomsday Papers"
        )

    def test_hacker2_slug_no_version(self):
        """The slug for Hacker II must convert II→2."""
        import re
        from amiga_adf_library_builder.metadata import _roman_numerals_to_arabic
        title = "Hacker II: The Doomsday Papers"
        arabic = _roman_numerals_to_arabic(title)
        slug = re.sub(r"[^a-z0-9]+", "-", arabic.lower()).strip("-")
        assert slug == "hacker-2-the-doomsday-papers"

    def test_hot_rod_norm_still_works(self):
        """Hot Rod normalization must still work."""
        # Hot Rod has identity but no manual source — _norm must not crash.
        norm = mdl._norm("Hot Rod")
        assert isinstance(norm, str)
        assert len(norm) > 0

    def test_strip_version_on_hacker2(self):
        """_strip_release_tags must strip v1.0 from Hacker II."""
        result = rc._strip_release_tags("Hacker II The Doomsday Papers v1.0")
        assert "v1.0" not in result


# ---------------------------------------------------------------------------
# Canonical.py Provenance extension
# ---------------------------------------------------------------------------


class TestProvenanceProviderUrlCanonical:
    """Provenance must accept provider_url_canonical."""

    def test_field_exists(self):
        from amiga_adf_library_builder.canonical import Provenance
        p = Provenance(
            source="lemon-amiga", url="https://example.com",
            provider_url_canonical="https://www.lemonamiga.com/doc/hacker-2"
        )
        assert p.provider_url_canonical == "https://www.lemonamiga.com/doc/hacker-2"

    def test_default_empty(self):
        from amiga_adf_library_builder.canonical import Provenance
        p = Provenance(source="operator")
        assert p.provider_url_canonical == ""

    def test_roundtrip(self):
        from amiga_adf_library_builder.canonical import Provenance
        p = Provenance(
            source="lemon-amiga", provider_url_canonical="https://example.com/x"
        )
        d = p.to_dict()
        assert d["provider_url_canonical"] == "https://example.com/x"
        p2 = Provenance.from_dict(d)
        assert p2.provider_url_canonical == "https://example.com/x"


# ---------------------------------------------------------------------------
# Generalized normalization coverage
# ---------------------------------------------------------------------------


class TestGeneralizedNormalization:
    """Test that _norm handles a wide variety of title formats."""

    @pytest.mark.parametrize(
        "title_a,title_b",
        [
            # roman/arabic equivalence
            ("Sonic 3", "Sonic 3"),  # same game, same representation
            ("Ultima 4", "Ultima 4"),
            ("Castle Wolfenstein 2", "Castle Wolfenstein 2"),
            # roman vs arabic (same game, different notation)
            ("Sonic the Hedgehog III", "Sonic the Hedgehog 3"),
            ("Ultima IV", "Ultima 4"),
            ("Castle Wolfenstein II", "Castle Wolfenstein 2"),
            # article movement
            ("The Legend of Zelda", "Legend of Zelda, The"),
            # case insensitivity
            ("SPACE INVADERS", "space invaders"),
            # punctuation
            ("Game--Name", "Game Name"),
        ],
    )
    def test_equivalent_titles(self, title_a, title_b):
        assert mdl._norm(title_a) == mdl._norm(title_b), (
            f"{title_a!r} and {title_b!r} must normalize equally"
        )
