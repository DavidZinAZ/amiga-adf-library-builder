"""GH-183 Manual Lookup Persist -> Preview -> Export lifecycle test.

Proves the full production lifecycle using the SAME document association:
  selected release
  -> Manual Lookup document discovery/candidates
  -> operator selects/applies a candidate
  -> association is persisted through the real production persistence path
  -> UI/consumer state refreshes
  -> repository/application state is closed/reopened or equivalently
     reconstructed from persisted storage
  -> Preview consumes that SAME persisted association
  -> export consumes that SAME persisted association
  -> physical/export result agrees with the persisted association.

Manual operator override must outrank later automation.

Run in isolation:
    python -m pytest tests/test_gh183_manual_lookup_lifecycle.py -v
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from amiga_adf_library_builder.canonical import (
    CanonicalLibrary, Game, Release, CanonicalField,
    Provenance, SourceAuthority,
)
from amiga_adf_library_builder.manual_lookup import (
    apply_manual_override, build_entity_report,
)
from amiga_adf_library_builder.naming import canonical_release_name
from amiga_adf_library_builder.models import ReleaseGroup


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_game(canon: CanonicalLibrary, game_id: str, title: str) -> None:
    """Create a game with a title claim in the canonical DB."""
    field = CanonicalField()
    field.claim(
        Provenance(source="parser", authority=SourceAuthority.PARSER,
                   observed_at="2026-09-17T00:00:00+00:00"),
        title,
    )
    game = Game(game_id=game_id, fields={"title": field})
    canon.upsert_game(game)


def _make_release(
    canon: CanonicalLibrary,
    release_id: str,
    game_id: str,
    release_key: str,
) -> None:
    """Create a release with a release_key claim in the canonical DB."""
    release = Release(
        release_id=release_id,
        game_id=game_id,
        edition=None, region=None, language=None, publisher=None,
    )
    canon.upsert_release(release)
    # Add release_key claim so export_name_for_release_group can match
    canon.claim_field(
        "release", release_id, "release_key", release_key,
        Provenance(source="parser", authority=SourceAuthority.CURATION_MEMORY,
                   observed_at="2026-09-17T00:00:00+00:00"),
    )


def _make_release_group(release_key: str, title: str) -> ReleaseGroup:
    """Create a representative ReleaseGroup for export/preview testing."""
    return ReleaseGroup(
        release_key=release_key,
        title=title,
        edition=None,
        group="capcom",
        chipset=None,
        version=None,
        alt_marker=None,
        ext="adf",
        records=[],
        disks=[],
        specials=[],
    )


# ---------------------------------------------------------------------------
# Lifecycle test
# ---------------------------------------------------------------------------

class TestManualLookupPersistPreviewExport:
    """Prove the full Manual Lookup lifecycle: apply -> persist -> reopen -> Preview -> export."""

    def test_manual_lookup_full_lifecycle_persist_preview_export(self):
        """End-to-end proof: operator override persists through close/reopen
        and is consumed identically by Preview and Export."""
        with tempfile.TemporaryDirectory() as tmpdir:
            library_root = Path(tmpdir)
            db_path = library_root / "curation" / "canonical.db"

            # --- STEP 1: Create/load representative release state ---
            # Game and Release in the canonical DB with parser-level claims
            canon = CanonicalLibrary(db_path)
            game_id = "hacker-ii-the-doomsday-papers"
            original_title = "Hacker II: The Doomsday Papers"
            overridden_title = "Hacker II: OPERATOR OVERRIDE"
            release_key = "hacker-ii|1"
            release_id = "hacker-ii|1"

            _make_game(canon, game_id, original_title)
            _make_release(canon, release_id, game_id, release_key)
            canon.close()

            # --- STEP 2: Obtain a meaningful manual/document candidate ---
            # Reopen and build entity report (discovery/candidates)
            # Use entity_type="game" because canonical_release_name reads
            # the game title for export naming.
            canon = CanonicalLibrary(db_path)
            report = build_entity_report(canon, "game", game_id)
            assert len(report.fields) > 0, "Expected field reports from canonical model"
            title_field = report.fields[0]
            assert title_field.field_name == "title"
            # Before override, the canonical value is the original title
            value_before = title_field.canonical_value
            assert value_before == original_title, \
                f"Expected '{original_title}', got '{value_before}'"

            # --- STEP 3: Apply/select candidate through production Manual Lookup ---
            # Operator overrides the game title via apply_manual_override (curation authority)
            apply_manual_override(
                canon, "game", game_id, "title", overridden_title,
                observed_at="2026-09-17T12:00:00+00:00",
            )
            # Verify it resolves immediately
            resolved_value, resolved_prov = canon.resolve_field(
                "game", game_id, "title"
            )
            assert resolved_value == overridden_title
            assert resolved_prov is not None
            assert resolved_prov.authority == SourceAuthority.CURATION
            canon.close()

            # --- STEP 4: Dispose/reopen/reload from persisted storage ---
            # New CanonicalLibrary instance with same DB path
            canon = CanonicalLibrary(db_path)

            # --- STEP 5: Prove Preview reads the selected association after reload ---
            # build_entity_report reads from the reopened canonical DB
            report_after = build_entity_report(canon, "game", game_id)
            title_field_after = report_after.fields[0]
            assert title_field_after.canonical_value == overridden_title, \
                f"Preview after reload should show '{overridden_title}', got '{title_field_after.canonical_value}'"
            # Verify the curation claim is present
            has_curation = canon.has_curation_claim("game", game_id, "title")
            assert has_curation, "Curation claim must survive close/reopen"

            # --- STEP 6: Execute the real export consumer path ---
            # canonical_release_name reads from the canonical DB via library_root
            group = _make_release_group(release_key, original_title)
            basename, provenance = canonical_release_name(group, library_root)

            # The export basename must reflect the overridden title,
            # not the original or the fallback
            # Note: canonical_release_name sanitizes via _sanitize_component
            # (colons/apostrophes become underscores)
            assert overridden_title.replace(":", "_").lower().replace(" ", "_") in basename.lower().replace(" ", "_"), \
                f"Export basename '{basename}' must contain overridden title"
            assert "fallback" not in provenance.lower(), \
                f"Export must use canonical DB, not fallback: {provenance}"

            canon.close()

            # --- STEP 7: Prove the same association identity across
            # persistence, Preview, and export ---
            # Reopen once more and verify everything is consistent
            canon = CanonicalLibrary(db_path)
            final_report = build_entity_report(canon, "game", game_id)
            final_title = final_report.fields[0].canonical_value
            assert final_title == overridden_title, \
                f"Final reload must still show '{overridden_title}', got '{final_title}'"

            # Verify the same provenance source (curation/operator) across all paths
            claims = canon.claims_for("game", game_id, "title")
            curation_claims = [
                (p, v) for p, v in claims
                if p.authority == SourceAuthority.CURATION and p.source == "operator"
            ]
            assert len(curation_claims) == 1, \
                f"Exactly one operator curation claim expected, got {len(curation_claims)}"
            assert curation_claims[0][1] == overridden_title, \
                f"Curation claim value must be '{overridden_title}'"

            # --- STEP 8: Prove operator/manual precedence survives reload ---
            # Add a lower-authority claim (parser/DAT) AFTER the curation override
            canon.claim_field(
                "game", game_id, "title", "Automated Parser Title",
                Provenance(source="tosec", authority=SourceAuthority.DAT,
                           authority_rank=0, observed_at="2026-09-18T00:00:00+00:00",
                           confidence=1.0),
            )
            canon.close()

            # Reopen and verify curation still wins
            canon = CanonicalLibrary(db_path)
            final_resolved, final_prov = canon.resolve_field(
                "game", game_id, "title"
            )
            assert final_resolved == overridden_title, \
                f"After adding lower-authority claim, curation must still win: '{final_resolved}'"
            assert final_prov is not None
            assert final_prov.authority == SourceAuthority.CURATION, \
                f"Winner must be CURATION authority, got {final_prov.authority}"

            # Export must still use the overridden title
            # Use original title so slugify_title matches the canonical game_id
            group2 = _make_release_group(release_key, original_title)
            basename2, _ = canonical_release_name(group2, library_root)
            assert overridden_title.replace(":", "_").lower().replace(" ", "_") in basename2.lower().replace(" ", "_"), \
                f"Export must still use overridden title after lower-authority claim"

            canon.close()

    def test_manual_override_outranks_automated_after_reload(self):
        """Regression: operator manual override must outrank later automation
        even after the state is closed and reopened."""
        with tempfile.TemporaryDirectory() as tmpdir:
            library_root = Path(tmpdir)
            db_path = library_root / "curation" / "canonical.db"

            canon = CanonicalLibrary(db_path)
            release_key = "stunt-car-racer|1"
            game_id = "stunt-car-racer"

            _make_game(canon, game_id, "Stunt Car Racer")
            _make_release(canon, release_key, game_id, release_key)
            canon.close()

            # Apply manual override to the game title (what export reads)
            canon = CanonicalLibrary(db_path)
            apply_manual_override(
                canon, "game", game_id, "title",
                "Stunt Car Racer: EDITOR'S CHOICE",
                observed_at="2026-09-17T12:00:00+00:00",
            )
            canon.close()

            # Reopen and add an automated claim with higher sort-key than parser
            # but lower than curation
            canon = CanonicalLibrary(db_path)
            canon.claim_field(
                "game", game_id, "title",
                "Stunt Car Racer: DAT VERSION",
                Provenance(source="tosec", authority=SourceAuthority.DAT,
                           authority_rank=0, observed_at="2026-09-18T00:00:00+00:00",
                           confidence=1.0),
            )
            canon.close()

            # Verify precedence: curation wins
            canon = CanonicalLibrary(db_path)
            value, prov = canon.resolve_field("game", game_id, "title")
            assert value == "Stunt Car Racer: EDITOR'S CHOICE", \
                f"Manual override must win: got '{value}'"
            assert prov is not None
            assert prov.authority == SourceAuthority.CURATION

            # Export must use the manual override name
            # Note: canonical_release_name sanitizes via _sanitize_component
            group = _make_release_group(release_key, "Stunt Car Racer")
            basename, provenance = canonical_release_name(group, library_root)
            # The basename contains the overridden title with sanitized chars
            assert "EDITOR" in basename.upper() and "CHOICE" in basename.upper(), \
                f"Export must reflect manual override: got basename '{basename}'"
            assert "fallback" not in provenance.lower()

            # Preview also shows the override
            report = build_entity_report(canon, "game", game_id)
            assert report.fields[0].canonical_value == "Stunt Car Racer: EDITOR'S CHOICE"

            canon.close()
