import io, json
from pathlib import Path

from amiga_adf_library_builder.metadata import wikipedia_lookup, lookup_metadata


class _Headers:
    def get_content_type(self):
        return "application/json"


class _Response(io.BytesIO):
    headers = _Headers()
    def __enter__(self): return self
    def __exit__(self, *args): return False


def _opener(request, timeout=0):
    payload = {
        "query": {"pages": [{
            "pageid": 42,
            "title": "Example: Space Tactics",
            "extract": "Example: Space Tactics is a strategy video game released for the Amiga.",
            "fullurl": "https://example.invalid/ufo",
            "original": {"source": "https://example.invalid/ufo.jpg"},
        }]}
    }
    return _Response(json.dumps(payload).encode())


def _matching_opener(request, timeout=0):
    """Serve an article that is genuinely about "Example Castle Quest".

    GH-192 online-usability pass: the previous fixture answered every query
    with "Example: Space Tactics", which the old fuzzy SequenceMatcher floor
    accepted purely because both titles shared the word "Example". The
    conservative subject test correctly rejects that as a different game, so
    the supplement fixture must now describe the requested title.
    """
    payload = {
        "query": {"pages": [{
            "pageid": 43,
            "title": "Example Castle Quest",
            "extract": "Example Castle Quest is a strategy video game released for the Amiga.",
            "fullurl": "https://example.invalid/castle",
            "original": {"source": "https://example.invalid/castle.jpg"},
        }]}
    }
    return _Response(json.dumps(payload).encode())


def test_wikipedia_provider_returns_provenance_and_artwork():
    record = wikipedia_lookup("Example Space Tactics", opener=_opener)
    assert record is not None
    assert record.provider == "wikipedia"
    assert record.source_url.endswith("/ufo")
    assert record.artwork_url.endswith("ufo.jpg")
    assert "Amiga" in record.description


def test_curated_record_is_cached(tmp_path: Path):
    curated = tmp_path / "curated"
    cache = tmp_path / "cache"
    curated.mkdir()
    (curated / "example-castle-quest.json").write_text(json.dumps({
        "canonical_title": "Example Castle Quest",
        "description": "Historical strategy game.",
        "source_url": "https://amiga.abime.net/802",
        "provider": "curated",
        "confidence": 1.0,
    }))
    record, provider, _ = lookup_metadata("Example Castle Quest", cache_dir=cache, curated_dir=curated, opener=_matching_opener)
    assert record is not None
    assert provider.startswith("curated")
    assert (cache / "example-castle-quest.json").exists()
    assert record.artwork_url.endswith("castle.jpg")  # missing art was supplemented


def test_curated_record_is_not_supplemented_with_a_different_game(tmp_path: Path):
    """A Wikipedia article about a DIFFERENT game must not become the artwork.

    GH-192 online-usability pass. ``_opener`` answers with "Example: Space
    Tactics", a different work from "Example Castle Quest" that the previous
    fuzzy similarity floor accepted because both titles shared the word
    "Example". Writing the wrong game's cover art into a preservation record is
    a correctness failure, not a cosmetic one, so the supplement is refused.
    """
    curated = tmp_path / "curated"
    cache = tmp_path / "cache"
    curated.mkdir()
    (curated / "example-castle-quest.json").write_text(json.dumps({
        "canonical_title": "Example Castle Quest",
        "description": "Historical strategy game.",
        "source_url": "https://amiga.abime.net/802",
        "provider": "curated",
        "confidence": 1.0,
    }))
    record, provider, _ = lookup_metadata(
        "Example Castle Quest", cache_dir=cache, curated_dir=curated,
        opener=_opener)
    assert record is not None
    assert record.artwork_url == ""
    assert record.description == "Historical strategy game."  # curated prose kept
