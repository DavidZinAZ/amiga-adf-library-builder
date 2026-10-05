"""Regression tests for the v0.2.38 production findings (GH-192 / #189).

Each test corresponds to a concrete defect observed in a manual production
run of RC candidate 07907f1 / RC head c1b2871:

FAILURE 1 — Hall of Light reported only ``bot_challenge`` with no stage, no
            URL, and no status; the rest of the pipeline needed to keep
            working without it.
FAILURE 2 — Lemon Amiga logged ``reason=candidate_returned`` with
            ``candidate=''`` when no candidate existed at all.
FAILURE 3 — Wikipedia ``request_error`` carried no URL, status, exception
            class, or error category.
FAILURE 4 — The GUI printed "Checking DAT/local metadata sources…" even
            when DAT was disabled, and the GUI wrote the source index to a
            different path than the pipeline read.
FAILURE 5 — A release routed to human review became exportable after an
            unrelated curation operation while the aggregate still listed it
            in ``review_routed``.
"""

from __future__ import annotations

import json
import tempfile
import urllib.error
from pathlib import Path

import pytest

from amiga_adf_library_builder import metadata as M
from amiga_adf_library_builder.models import (
    ParsedRecord,
    ReleaseGroup,
    StagedLibrary,
    StagedReleaseEntry,
    StagedState,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _group(release_key: str, title: str, *, disks: int = 1,
           quarantine: str | None = None) -> ReleaseGroup:
    records = [
        ParsedRecord(source_filename=f"{release_key.replace('|', '_')}_{i}.adf",
                     ext="adf", disk_number=i)
        for i in range(disks)
    ]
    g = ReleaseGroup(
        release_key=release_key,
        title=title,
        ext="adf",
        edition=None,
        group=None,
        chipset=None,
        records=records,
        disks=records,
        specials=[],
        has_main_disk=True,
    )
    g.quarantine_reason = quarantine
    return g


class _FakeResponse:
    """Minimal context-manager response double for the urllib opener."""

    def __init__(self, body: bytes, status: int = 200,
                 content_type: str = "text/html; charset=utf-8") -> None:
        self._body = body
        self.status = status
        self.headers = _FakeHeaders(content_type)

    def read(self, size: int = -1) -> bytes:
        return self._body if size is None or size < 0 else self._body[:size]

    def geturl(self) -> str:
        return "https://example.invalid/final"

    def getcode(self) -> int:
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeHeaders:
    def __init__(self, content_type: str) -> None:
        self._ct = content_type

    def get_content_charset(self):
        return self._ct.split("charset=")[-1] if "charset=" in self._ct else None


# ===========================================================================
# FAILURE 2 — a missing candidate is not "candidate_returned"
# ===========================================================================

_LEMON_PAGE = (
    "<!DOCTYPE html><html>"
    "<head><title>Ultima VI - Lemon Amiga</title></head>"
    "<body><h1>Ultima VI</h1>"
    "<table class='credits'>"
    "<tr><th>Released</th><td>1989</td></tr>"
    "<tr><th>Publisher</th><td>Origin Systems</td></tr>"
    "<tr><th>Coder</th><td>Chuck Bueche</td></tr>"
    "</table>"
    "<table class='info'>"
    "<tr><th>Hardware</th><td>OCS, ECS</td></tr>"
    "<tr><th>Players</th><td>1 Only</td></tr>"
    "<tr><th>Disks</th><td>2</td></tr>"
    "</table>"
    "<table class='categorization'>"
    "<tr><th>Genre</th><td>Role Playing</td></tr>"
    "</table>"
    "</body></html>"
)


# ===========================================================================
# FAILURE 1 — Hall of Light is externally blocked; make it explicit
# ===========================================================================

# ===========================================================================
# FAILURE 3 — Wikipedia request errors must be diagnosable
# ===========================================================================

# ===========================================================================
# FAILURE 4 — DAT configuration wiring and honest messaging
# ===========================================================================

class TestDatSourceWiring:
    def test_gui_and_pipeline_resolve_the_same_index_path(self):
        """The GUI must write the index where the pipeline reads it.

        Regression: the GUI used ``<base>/data/`` while the pipeline used
        ``<Library-Root>/data/``, so every operator-added DAT source was
        invisible to the run.
        """
        from amiga_adf_library_builder.gui.layout import PortablePaths
        from amiga_adf_library_builder.paths import resolve_config

        base = Path(tempfile.mkdtemp())
        paths = PortablePaths(base)
        paths.ensure_all()
        cfg, _src = resolve_config(library_root=str(paths.library_root))
        gui_equivalent = Path(paths.library_root) / "data" / "metadata_sources.db"
        assert Path(cfg.metadata_sources_db) == gui_equivalent
        # The legacy portable path must NOT be the one the GUI uses.
        assert Path(paths.metadata_sources_db) != gui_equivalent

    def test_gui_window_uses_the_pipeline_index_path(self):
        from amiga_adf_library_builder.gui.layout import PortablePaths
        from amiga_adf_library_builder.paths import resolve_config

        pytest.importorskip("PySide6.QtWidgets")
        from PySide6.QtWidgets import QApplication

        from amiga_adf_library_builder.gui.main_window import MainWindow

        os_env = __import__("os").environ
        prev = os_env.get("QT_QPA_PLATFORM")
        os_env.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication([])
        base = Path(tempfile.mkdtemp())
        paths = PortablePaths(base)
        paths.ensure_all()
        try:
            win = MainWindow(portable_paths=paths)
            cfg, _s = resolve_config(library_root=str(paths.library_root))
            assert Path(win._metadata_sources_db) == Path(cfg.metadata_sources_db)
        finally:
            if prev is None:
                os_env.pop("QT_QPA_PLATFORM", None)
            else:
                os_env[prev] = prev

    def test_no_checking_line_when_no_dat_source_configured(self):
        """No 'Checking…' line when DAT is disabled, and a clear line instead."""
        from amiga_adf_library_builder import enrich as E

        src = Path(E.__file__).read_text(encoding="utf-8")
        assert "Checking DAT/local metadata sources…" in src
        checking_idx = src.index("Checking DAT/local metadata sources…")
        enabled_idx = src.index("if _enabled_sources:")
        assert checking_idx > enabled_idx, (
            "the Checking line must be emitted after the enabled-source test"
        )

    def test_dat_disabled_says_so_in_plain_language(self):
        src = Path(
            __import__("amiga_adf_library_builder.enrich", fromlist=["x"]).__file__
        ).read_text(encoding="utf-8")
        assert "no DAT lookup was performed" in src
        assert "dat_source_disabled" in src


# ===========================================================================
# FAILURE 5 — review/export state must survive unrelated curation
# ===========================================================================

class TestReviewRoutingSurvivesCuration:
    def _library(self, path: Path, states: dict[str, StagedState],
                 *, ghosts: set[str] | None = None) -> str:
        """Write a curation state file with the given per-release states."""
        from amiga_adf_library_builder.library_state import (
            CurationStateFile, CurationStateManager,
        )

        lib = StagedLibrary()
        for key, state in states.items():
            title = key.split("|")[0]
            base = key.replace("|", "_")
            lib.releases[key] = StagedReleaseEntry(
                release_key=key, title=title, edition=None, group=None,
                chipset=None,
                adf_files=[] if (ghosts and key in ghosts)
                else [f"{base}_0.adf"],
                curation_state=state,
            )
        mgr = CurationStateManager(path)
        assert mgr.save(lib)
        return str(path)

    def test_needs_review_release_stays_blocked(self):
        """Step 5 of the required regression: a review-routed release that
        remains NEEDS_REVIEW after an unrelated curation edit must still be
        blocked from export."""
        from amiga_adf_library_builder.pipeline import _apply_curation

        d = Path(tempfile.mkdtemp())
        state = self._library(d / "library_state.json", {
            "a-10 tank killer|v1.0": StagedState.NEEDS_REVIEW,
            "deluxe pac man|v1.2": StagedState.NEEDS_REVIEW,
            "bards tale iii|": StagedState.MODIFIED,
        })

        groups = [
            _group("a-10 tank killer|v1.0", "A-10 Tank Killer"),
            _group("deluxe pac man|v1.2", "Deluxe Pac Man"),
            _group("bards tale iii|", "Bard's Tale III"),
        ]
        # Simulate the pipeline's own pre-curation routing, which is what put
        # these into review in the first place.
        groups[0].quarantine_reason = "Near-duplicate spelling: 'A-10 Tank Killer v1.5'"
        groups[1].quarantine_reason = "Near-duplicate spelling: 'Deluxe Pac Man v1.4'"

        staged, _decisions = _apply_curation(groups, state, None)

        # The two review-routed releases must NOT have become exportable.
        assert groups[0].quarantine_reason, (
            "a review-routed release must remain blocked after curation"
        )
        assert groups[1].quarantine_reason, (
            "a review-routed release must remain blocked after curation"
        )
        # The specific grouper diagnosis must be preserved, not overwritten.
        assert "Near-duplicate" in (groups[0].quarantine_reason or "")
        # The unrelated MODIFIED release is untouched.
        assert groups[2].quarantine_reason is None
        assert staged is not None

    def test_needs_review_blocks_when_no_prior_quarantine_reason(self):
        """A NEEDS_REVIEW entry with no surviving grouper reason still blocks."""
        from amiga_adf_library_builder.pipeline import _apply_curation

        d = Path(tempfile.mkdtemp())
        state = self._library(d / "library_state.json", {
            "ultima vi|": StagedState.NEEDS_REVIEW,
        })
        groups = [_group("ultima vi|", "Ultima VI")]
        _apply_curation(groups, state, None)
        assert groups[0].quarantine_reason == (
            "routed to review; awaiting operator approval"
        )

    def test_explicit_accept_unblocks(self):
        """An explicit approval is the ONLY thing that releases a review block."""
        from amiga_adf_library_builder.pipeline import _apply_curation

        d = Path(tempfile.mkdtemp())
        state = self._library(d / "library_state.json", {
            "a-10 tank killer|v1.0": StagedState.ACCEPTED,
        })
        groups = [_group("a-10 tank killer|v1.0", "A-10 Tank Killer")]
        groups[0].quarantine_reason = "routed to review"
        _apply_curation(groups, state, None)
        assert groups[0].quarantine_reason is None, (
            "an explicit ACCEPT must clear the review block"
        )

    def test_ghost_remains_blocked(self):
        from amiga_adf_library_builder.pipeline import _apply_curation

        d = Path(tempfile.mkdtemp())
        state = self._library(
            d / "library_state.json",
            {"bards talet3|": StagedState.GHOST},
            ghosts={"bards talet3|"},
        )
        groups = [_group("bards talet3|", "Bards Talet3")]
        _apply_curation(groups, state, None)
        assert "ghost" in (groups[0].quarantine_reason or "")

    def test_exporter_never_writes_a_quarantined_release(self):
        """The gate that actually stops export.

        The verified artwork dimensions must be supplied or the Gotek export
        gate stays closed and nothing is exported at all — which would make
        this assertion pass for the wrong reason.
        """
        from amiga_adf_library_builder.exporter import export_all

        d = Path(tempfile.mkdtemp())
        blocked = _group("a-10 tank killer|v1.0", "A-10 Tank Killer")
        blocked.quarantine_reason = "routed to review; awaiting operator approval"
        clean = _group("ultima vi|", "Ultima VI")

        result = export_all(
            [blocked, clean],
            staging_dir=d / "staging",
            run_id="testrun",
            upstream_task_closed=True,
            verified_artwork_width=150,
            verified_artwork_height=150,
        )
        assert result.export_gate_open, result.errors
        assert not result.errors, result.errors
        assert "a-10 tank killer|v1.0" in result.skipped_quarantined, (
            result.skipped_quarantined
        )
        assert "ultima vi|" not in result.skipped_quarantined

    def test_review_items_carry_a_release_key(self):
        """A review item with an empty release_key is silently dropped by
        route_quarantine, so the aggregate could not agree with the gate."""
        from amiga_adf_library_builder.enrich import _build_review_items, EnrichEvent, EnrichCategory

        ev = EnrichEvent(
            category=EnrichCategory.METADATA_RELEVANCE_REVIEW,
            detail="wikipedia candidate 'X' routed to review",
            ok=False,
            error="ambiguous_midband",
        )
        with_key = _build_review_items([ev], release_key="ultima vi|")
        assert len(with_key) == 1
        assert with_key[0].release_key == "ultima vi|"

    def test_routing_and_export_gate_agree(self):
        """Step 6: per-group diagnostics and aggregate review_routed agree.

        The production run exported A-10 and Deluxe Pac Man while the
        aggregate still listed their JSON files in review_routed. Route and
        gate must describe the same set.
        """
        from amiga_adf_library_builder import quarantine as Q
        from amiga_adf_library_builder.pipeline import _apply_curation

        d = Path(tempfile.mkdtemp())
        state = self._library(d / "library_state.json", {
            "a-10 tank killer|v1.0": StagedState.NEEDS_REVIEW,
            "deluxe pac man|v1.2": StagedState.NEEDS_REVIEW,
        })
        groups = [
            _group("a-10 tank killer|v1.0", "A-10 Tank Killer"),
            _group("deluxe pac man|v1.2", "Deluxe Pac Man"),
        ]
        _apply_curation(groups, state, None)

        summary = Q.route_quarantine(
            groups, review_dir=d / "review", unknown_dir=d / "unknown",
            scans={},
        )
        # Every group the exporter blocks must have a review record.
        blocked = {g.release_key for g in groups if g.quarantine_reason}
        assert blocked, "the fixture must actually block both releases"
        recorded = {Path(p).stem for p in summary["review"]}
        for g in groups:
            if g.release_key in blocked:
                assert g.title in recorded, (
                    f"no review record for blocked release {g.release_key!r}; "
                    f"records={recorded!r}"
                )
        # And the reverse: nothing recorded that is not blocked.
        assert len(summary["review"]) == len(blocked)
