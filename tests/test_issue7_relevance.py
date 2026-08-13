"""Issue #7 — online metadata relevance validation layer (synthetic fixtures).

These tests use ONLY synthetic/fake openers and public-safe example titles.
They never reference the private corpus or real production titles. They verify
that irrelevant online candidates are rejected and never cached/returned, that
rejection falls through to the next provider and then to offline/local, that
curated/cached authoritative paths are unchanged, and that acceptance records
the relevance decision in the metadata provenance.
"""
import io
import json
from pathlib import Path

import pytest

from amiga_adf_library_builder.metadata import (
    MetadataRecord,
    RelevanceDecision,
    lookup_metadata,
    validate_metadata_relevance,
    wikipedia_lookup,
)


class _Headers:
    def get_content_type(self):
        return "application/json"


class _Response(io.BytesIO):
    headers = _Headers()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _wiki_opener_from_pages(pages):
    """Build a fake opener returning the given Wikipedia API page list."""
    def opener(request, timeout=0):
        payload = {"query": {"pages": pages}}
        return _Response(json.dumps(payload).encode())
    return opener


def _record(canonical_title, description="", platforms=None, year="",
            publisher="", developer=""):
    return MetadataRecord(
        canonical_title=canonical_title,
        description=description,
        platforms=list(platforms or []),
        year=year, publisher=publisher, developer=developer,
        provider="wikipedia", provider_id="x",
        source_url="https://en.wikipedia.org/wiki/" + canonical_title.replace(" ", "_"),
    )


# ---------------------------------------------------------------------------
# Unit tests for validate_metadata_relevance (deterministic, no network)
# ---------------------------------------------------------------------------

def test_person_biography_is_rejected():
    rec = _record("Johannes Compuson", "Johannes Compuson was a composer and musician born in 1950.")
    decision = validate_metadata_relevance("Example Galaxy Raiders", rec)
    assert decision.category == "rejected"
    assert decision.reason == "person_page"
    assert "entity_type_person" in decision.evidence


def test_different_game_with_platform_mismatch_is_rejected():
    # Query "Example Moon Patrol"; a *different* game "Example Moon Raiders"
    # (similarly named, but a distinct title) that reports a non-Amiga platform
    # is rejected as a different game (platform-negative signal).
    rec = _record("Example Moon Raiders", "Example Moon Raiders is a shooter video game.",
                  platforms=["PC"])
    decision = validate_metadata_relevance("Example Moon Patrol", rec)
    assert decision.category == "rejected"
    assert decision.reason in ("different_game", "low_title_similarity")


def test_disambiguation_page_is_not_accepted():
    rec = _record("Example Dragon", "Example Dragon may refer to several different games in the series.",
                  platforms=["Amiga"])
    decision = validate_metadata_relevance("Example Dragon", rec)
    assert decision.category in ("rejected", "review")
    assert decision.reason == "series_disambiguation"
    assert "disambiguation_page" in decision.evidence


def test_exact_high_confidence_game_is_accepted():
    rec = _record("Example Solar Miner", "Example Solar Miner is a strategy video game released for the Amiga.",
                  platforms=["Amiga"])
    decision = validate_metadata_relevance("Example Solar Miner", rec)
    assert decision.category == "accepted"
    assert decision.confidence >= 0.90
    assert "exact_canonical_title" in decision.evidence
    assert "platform_amiga_match" in decision.evidence


def test_deterministic_identical_input():
    rec = _record("Example Solar Miner", "Example Solar Miner is a strategy video game for the Amiga.",
                  platforms=["Amiga"])
    a = validate_metadata_relevance("Example Solar Miner", rec)
    b = validate_metadata_relevance("Example Solar Miner", rec)
    assert a == b


# ---------------------------------------------------------------------------
# Integration: lookup_metadata rejection + fall-through + no-cache
# ---------------------------------------------------------------------------

def _wiki_one(title, description="", platforms=None, year=""):
    return [{
        "pageid": 1, "title": title,
        "extract": description,
        "fullurl": "https://en.wikipedia.org/wiki/" + title.replace(" ", "_"),
        "original": {"source": "https://example.invalid/" + title.replace(" ", "_") + ".jpg"},
    }]


def test_rejected_person_candidate_falls_through_and_not_cached(tmp_path: Path):
    cache = tmp_path / "cache"
    curated = tmp_path / "curated"
    # First (and only) provider is wikipedia; it returns a composer bio page.
    opener = _wiki_opener_from_pages(_wiki_one(
        "Example Galaxy Raiders",
        "Example Galaxy Raiders is a composer and musician who wrote soundtracks."))
    record, provider, events = lookup_metadata(
        "Example Galaxy Raiders", cache_dir=cache, curated_dir=curated, opener=opener)
    # Rejected -> falls through to offline/local -> not-found, never cached.
    assert record is None
    assert provider == "not-found"
    assert any(e["category"] == "rejected" for e in events)
    assert not (cache / "example-galaxy-raiders.json").exists()


def test_rejection_falls_through_to_good_second_provider(tmp_path: Path):
    cache = tmp_path / "cache"
    curated = tmp_path / "curated"
    # rawg (Amiga-filtered) returns a WRONG-but-Amiga game with low title
    # similarity; our validator rejects it and we fall through to wikipedia,
    # which returns the correct game.
    bad = _record("Zeta Blaster", "Zeta Blaster is a shooter video game for the Amiga.",
                  platforms=["Amiga"])

    class _RawgHeaders:
        def get_content_type(self): return "application/json"
    class _RawgResponse(io.BytesIO):
        headers = _RawgHeaders()
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def geturl(self): return self.url

    calls = {"n": 0}

    def opener(request, timeout=0):
        url = request.full_url
        if "api.rawg.io" in url:
            if "/games/" in url:
                # detail endpoint returns the bad (different, non-Amiga) game
                payload = {"name": bad.canonical_title, "description": bad.description,
                           "platforms": [{"platform": {"name": p}} for p in bad.platforms],
                           "released": "1990-01-01", "background_image": "https://example.invalid/bad.jpg"}
            else:
                # search endpoint returns a result pointing at the bad game id
                payload = {"results": [{"id": 999, "name": bad.canonical_title,
                                        "slug": "example-moon-raiders"}]}
            r = _RawgResponse(json.dumps(payload).encode()); r.url = url; return r
        # wikipedia returns the good game
        pages = _wiki_one("Example Moon Patrol",
                          "Example Moon Patrol is a shooter video game for the Amiga.",
                          platforms=["Amiga"])
        return _Response(json.dumps({"query": {"pages": pages}}).encode())

    import os
    env_key = os.environ.get("RAWG_API_KEY")
    os.environ["RAWG_API_KEY"] = "test-key"
    try:
        record, provider, events = lookup_metadata(
            "Example Moon Patrol", cache_dir=cache, curated_dir=curated, opener=opener)
    finally:
        if env_key is None:
            os.environ.pop("RAWG_API_KEY", None)
        else:
            os.environ["RAWG_API_KEY"] = env_key

    assert record is not None
    assert record.canonical_title == "Example Moon Patrol"
    # The bad rawg candidate must have been rejected (fall-through evidence).
    assert any(e["provider"] == "rawg" and e["category"] == "rejected" for e in events)
    assert record.relevance_category == "accepted"


def test_both_providers_rejected_falls_back_offline(tmp_path: Path):
    cache = tmp_path / "cache"
    curated = tmp_path / "curated"
    # Both providers return a composer bio page.
    def opener(request, timeout=0):
        pages = _wiki_one("Example Star Fighter",
                          "Example Star Fighter was a composer who wrote soundtracks.")
        return _Response(json.dumps({"query": {"pages": pages}}).encode())

    import os
    env_key = os.environ.get("RAWG_API_KEY")
    os.environ["RAWG_API_KEY"] = "test-key"
    try:
        record, provider, events = lookup_metadata(
            "Example Star Fighter", cache_dir=cache, curated_dir=curated, opener=opener)
    finally:
        if env_key is None:
            os.environ.pop("RAWG_API_KEY", None)
        else:
            os.environ["RAWG_API_KEY"] = env_key

    assert record is None
    assert provider == "not-found"
    assert not (cache / "example-star-fighter.json").exists()


# ---------------------------------------------------------------------------
# Provenance: accepted record carries relevance fields; surfaced in JSON/text
# ---------------------------------------------------------------------------

def test_accepted_relevance_recorded_in_provenance(tmp_path: Path):
    from amiga_adf_library_builder.enrich import build_provenance_text, _build_provenance_record
    from amiga_adf_library_builder.grouper import group_records
    from amiga_adf_library_builder.parser import parse_filename

    recs = [parse_filename("Example - Solar Miner (Disk 1 of 2).adf")]
    group = group_records(recs)[0]

    rec = _record("Example Solar Miner", "Example Solar Miner is a strategy video game for the Amiga.",
                  platforms=["Amiga"])
    decision = validate_metadata_relevance("Example Solar Miner", rec)
    rec.relevance_category = decision.category
    rec.relevance_confidence = decision.confidence
    rec.relevance_evidence = list(decision.evidence)

    txt = build_provenance_text(group, {}, rec, mode="online")
    assert "Relevance: accepted" in txt
    assert "exact_canonical_title" in txt

    js = _build_provenance_record(group, {}, rec, mode="online", approval_sources=[])
    mp = js["metadata_provenance"]
    assert mp["relevance_category"] == "accepted"
    assert mp["relevance_confidence"] == decision.confidence
    assert "exact_canonical_title" in mp["relevance_evidence"]


# ---------------------------------------------------------------------------
# Invariant: curated path is authoritative and never validated/rejected
# ---------------------------------------------------------------------------

def test_curated_record_never_validated(tmp_path: Path):
    cache = tmp_path / "cache"
    curated = tmp_path / "curated"
    curated.mkdir()
    (curated / "example-castle-quest.json").write_text(json.dumps({
        "canonical_title": "Example Castle Quest",
        "description": "Historical strategy game.",
        "source_url": "https://amiga.abime.net/802",
        "provider": "curated",
        "confidence": 1.0,
    }))
    # Even a malicious/irrelevant wikipedia opener must NOT override curated.
    opener = _wiki_opener_from_pages(_wiki_one(
        "Example Castle Quest", "Example Castle Quest was a composer and musician."))
    record, provider, events = lookup_metadata(
        "Example Castle Quest", cache_dir=cache, curated_dir=curated, opener=opener)
    assert record is not None
    assert provider.startswith("curated")
    # Curated path produces no relevance evaluation events.
    assert events == []


def test_cached_record_is_trusted_and_not_revalidated(tmp_path: Path):
    cache = tmp_path / "cache"
    curated = tmp_path / "curated"
    cache.mkdir()
    # Pre-seed a cached record; offline (no opener needed) must reuse it.
    cached_rec = MetadataRecord(canonical_title="Example Cached Game",
                                 description="Cached.", provider="wikipedia",
                                 confidence=0.9)
    from amiga_adf_library_builder.metadata import save_cached
    save_cached(cache, "Example Cached Game", cached_rec)
    record, provider, events = lookup_metadata(
        "Example Cached Game", cache_dir=cache, curated_dir=curated)
    assert record is not None
    assert provider == "cache"
    assert events == []


def test_enrich_emits_relevance_rejected_event(tmp_path: Path):
    # Verify the enrichment glue surfaces online relevance rejection as a
    # structured EnrichEvent (METADATA_RELEVANCE_REJECTED), without depending
    # on live network: monkeypatch the now-3-tuple lookup_metadata return.
    from amiga_adf_library_builder import metadata as metadata_mod
    from amiga_adf_library_builder import enrich as enrich_mod
    from amiga_adf_library_builder.enrich import enrich_group, EnrichCategory
    from amiga_adf_library_builder.grouper import group_records
    from amiga_adf_library_builder.parser import parse_filename

    recs = [parse_filename("Example - Galaxy Raiders (Disk 1 of 2).adf")]
    group = group_records(recs)[0]

    class _Scan:
        path = tmp_path / "x.adf"
        filename = recs[0].source_filename
        size = 1
        sha256 = "a"
        scanned_at = "t"

    orig = enrich_mod.lookup_metadata

    def fake_lookup(title, *, cache_dir, curated_dir, refresh=False, timeout=20.0,
                    group=None, opener=None):
        return (None, "not-found", [{
            "provider": "wikipedia",
            "canonical_title": "Example Galaxy Raiders",
            "category": "rejected",
            "confidence": 0.1,
            "reason": "person_page",
            "evidence": ["entity_type_person"],
        }])

    enrich_mod.lookup_metadata = fake_lookup
    try:
        res = enrich_group(group, nfo_dir=tmp_path / "nfo",
                           scans={recs[0].source_filename: _Scan()},
                           artwork_original_dir=tmp_path / "art",
                           artwork_processed_dir=tmp_path / "proc",
                           metadata_cache_dir=tmp_path / "mc",
                           curated_metadata_dir=tmp_path / "cd",
                           online=True, refresh=False)
    finally:
        enrich_mod.lookup_metadata = orig

    rejected = [e for e in res.events
                if e.category == EnrichCategory.METADATA_RELEVANCE_REJECTED]
    assert rejected, "expected a METADATA_RELEVANCE_REJECTED event"
    assert rejected[0].error == "person_page"
    # Fall-through is non-blocking: no metadata cache file is written for the
    # rejected candidate, and the offline metadata_path is not populated.
    assert res.metadata_path is None
    assert not (tmp_path / "mc" / "example-galaxy-raiders.json").exists()
