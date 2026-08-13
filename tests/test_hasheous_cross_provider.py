"""Committed cross-provider fail-safe regression tests for issue #12.

These assert the hash-first fail-safe posture required by issue #11/#12:
when BOTH the Playmatch and Hasheous hash-first identity providers are enabled
and each resolves the SAME precomputed sha256 to DISAGREEING authoritative
exact-hash identities, ``enrich_group`` MUST NOT silently accept either as a
winner. It MUST surface a deterministic ``*_REVIEW`` manual-review signal and
MUST NOT record both conflicting provider_ids as accepted successes.

These are the SPEC-encoded equivalents of Columbo's acceptance check #10
(``tests/test_hasheous_qa_independent.py::test_cross_provider_disagreeing_exact_hash_routes_to_review``),
promoted to a committed, always-run synthetic suite. All data is public-safe
synthetic JSON; no real network, no private corpus, no local paths.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from amiga_adf_library_builder import hasheous as hs
from amiga_adf_library_builder import playmatch as pm
from amiga_adf_library_builder import enrich as en
from amiga_adf_library_builder.models import ParsedRecord, ReleaseGroup


# --- helpers ----------------------------------------------------------------

def _make_group(title, sha256):
    rec = ParsedRecord(source_filename=f"{title}.adf", ext="adf", title=title)
    group = ReleaseGroup(
        release_key=title.lower(),
        title=title,
        edition=None, group=None, chipset=None, language=None,
        version=None, alt_marker=None, ext="adf",
        records=[rec], disks=[rec],
    )
    group.sha256 = sha256  # type: ignore[attr-defined]
    return group


def _pm_provider(opener):
    cfg = pm.PlaymatchConfig.from_dict({"enabled": True})
    prov = pm.PlaymatchProvider(cfg, Path(tempfile.mkdtemp()),
                                opener=opener, resolve=False)
    prov.discover()
    return prov


def _hs_provider(opener):
    cfg = hs.HasheousConfig.from_dict({"enabled": True})
    prov = hs.HasheousProvider(cfg, Path(tempfile.mkdtemp()),
                               opener=opener, resolve=False)
    prov.discover()
    return prov


def _enrich(group, *, playmatch_provider=None, hasheous_provider=None):
    return en.enrich_group(
        group,
        nfo_dir=Path(tempfile.mkdtemp()),
        scans={},
        artwork_original_dir=Path(tempfile.mkdtemp()),
        artwork_processed_dir=Path(tempfile.mkdtemp()),
        online=False,
        playmatch_provider=playmatch_provider,
        hasheous_provider=hasheous_provider,
    )


def _review_events(result):
    return [e for e in result.events
            if e.category in (en.EnrichCategory.PLAYMATCH_REVIEW,
                              en.EnrichCategory.HASHEOUS_REVIEW)]


# --- acceptance #10: disagreeing exact-hash identities -> manual review -----

def test_cross_provider_disagreeing_exact_hash_routes_to_review():
    """Enabling BOTH providers with DISAGREEING exact-hash identities for the
    same sha256 must yield a deterministic fail-safe ``*_REVIEW`` signal and
    MUST NOT present either conflicting provider_id as an accepted identity.
    """
    sha = "5" * 64
    group = _make_group("Disagreement Game", sha)

    def pm_opener(url, *, timeout):
        return json.dumps({"found": True, "provider_id": "PM-A",
                           "confidence": 1.0}).encode()

    def hs_opener(url, *, timeout):
        return json.dumps({"found": True, "provider_id": "HS-B",
                           "title": "Disagreement Game", "confidence": 1.0,
                           "category": "Game",
                           "external_ids": {"igdb_id": "IGDB-9"}}).encode()

    er = _enrich(group,
                 playmatch_provider=_pm_provider(pm_opener),
                 hasheous_provider=_hs_provider(hs_opener))

    # (a) a deterministic fail-safe manual-review signal is present.
    review_events = _review_events(er)
    assert review_events, (
        "cross-provider exact-hash disagreement must route to manual review; "
        f"got notes={er.notes}"
    )
    assert all(e.ok is False for e in review_events), \
        "review event must carry ok=False"
    assert any("cross-provider" in e.detail.lower() for e in review_events), \
        "review event must carry the cross-provider conflict detail"

    # (b) NEITHER conflicting provider_id is presented as an accepted success.
    # The fail-safe suppresses both per-provider success notes so no conflicting
    # id can be mistaken for an accepted identity.
    assert not any("provider_id: PM-A" in n for n in er.notes), \
        "playmatch provider_id PM-A must not be recorded as an accepted success"
    assert not any("provider_id: HS-B" in n for n in er.notes), \
        "hasheous provider_id HS-B must not be recorded as an accepted success"

    # The per-provider PLAYMATCH/HASHEOUS success events must also be suppressed.
    success_categories = {e.category for e in er.events
                          if e.category in (en.EnrichCategory.PLAYMATCH,
                                            en.EnrichCategory.HASHEOUS)}
    assert not success_categories, \
        f"conflicting per-provider success events must be suppressed, got {success_categories}"


# --- positive: agreeing identities do NOT trigger the review path -----------

def test_cross_provider_agreeing_provider_ids_no_review():
    """When BOTH providers resolve the same sha256 to the SAME provider_id, the
    cross-provider fail-safe must NOT fire; both identities are accepted as the
    (agreeing) authoritative identity.
    """
    sha = "6" * 64
    group = _make_group("Agreement Game", sha)

    def pm_opener(url, *, timeout):
        return json.dumps({"found": True, "provider_id": "AGREE",
                           "confidence": 1.0}).encode()

    def hs_opener(url, *, timeout):
        return json.dumps({"found": True, "provider_id": "AGREE",
                           "title": "Agreement Game", "confidence": 1.0,
                           "category": "Game",
                           "external_ids": {}}).encode()

    er = _enrich(group,
                 playmatch_provider=_pm_provider(pm_opener),
                 hasheous_provider=_hs_provider(hs_opener))

    assert not _review_events(er), \
        "agreeing provider_ids must NOT trigger the cross-provider review path"

    # Both per-provider success notes/events are present and accepted.
    assert any("playmatch provider_id: AGREE" in n for n in er.notes)
    assert any("hasheous provider_id: AGREE" in n for n in er.notes)
    success_categories = {e.category for e in er.events
                          if e.category in (en.EnrichCategory.PLAYMATCH,
                                            en.EnrichCategory.HASHEOUS)}
    assert success_categories == {en.EnrichCategory.PLAYMATCH,
                                  en.EnrichCategory.HASHEOUS}


# --- agreement on provider_id but disagreement on normalized external_ids ----

def test_cross_provider_agreeing_id_disagreeing_external_ids_routes_to_review():
    """The authoritative identity is provider_id PLUS normalized external_ids.
    Same provider_id but disagreeing external_ids is still a disagreement and
    must route to manual review (issue #11/#12 posture is exact-hash strict).
    """
    sha = "7" * 64
    group = _make_group("ExtDisagreement Game", sha)

    def pm_opener(url, *, timeout):
        return json.dumps({"found": True, "provider_id": "SAME",
                           "confidence": 1.0}).encode()

    def hs_opener(url, *, timeout):
        return json.dumps({"found": True, "provider_id": "SAME",
                           "title": "ExtDisagreement Game", "confidence": 1.0,
                           "category": "Game",
                           "external_ids": {"igdb_id": "IGDB-X"}}).encode()

    er = _enrich(group,
                 playmatch_provider=_pm_provider(pm_opener),
                 hasheous_provider=_hs_provider(hs_opener))

    review_events = _review_events(er)
    assert review_events, (
        "same provider_id but disagreeing external_ids must route to manual review; "
        f"got notes={er.notes}"
    )
    assert not any("provider_id: SAME" in n for n in er.notes), \
        "conflicting identity must not be recorded as an accepted success"
