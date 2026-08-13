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
    record, provider, _ = lookup_metadata("Example Castle Quest", cache_dir=cache, curated_dir=curated, opener=_opener)
    assert record is not None
    assert provider.startswith("curated")
    assert (cache / "example-castle-quest.json").exists()
    assert record.artwork_url.endswith("ufo.jpg")  # missing art was supplemented


def test_amiga_page_artwork_discovery_prefers_cover_metadata():
    from amiga_adf_library_builder.metadata import discover_artwork_from_page

    class H:
        def get_content_charset(self): return "utf-8"
    class R(io.BytesIO):
        headers = H()
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def geturl(self): return "https://www.lemonamiga.com/game/example-galactic-bureau"
    def opener(request, timeout=0):
        html = b'''<html><head><meta property="og:image" content="/media/covers/example-galactic-bureau-front.jpg"></head>
        <body><img src="/theme/logo.png" alt="logo"></body></html>'''
        return R(html)

    found = discover_artwork_from_page("https://www.lemonamiga.com/game/example-galactic-bureau", "E.X.A.M.P.L.E. II", opener=opener)
    assert found == ("https://www.lemonamiga.com/media/covers/example-galactic-bureau-front.jpg", "lemon-amiga")


def test_curated_record_uses_amiga_specific_artwork_page_when_wikipedia_has_no_image(tmp_path: Path):
    curated = tmp_path / "curated"
    cache = tmp_path / "cache"
    curated.mkdir()
    (curated / "example-castle-quest.json").write_text(json.dumps({
        "canonical_title": "Example Castle Quest",
        "description": "Historical strategy game.",
        "source_url": "https://amiga.abime.net/802",
        "artwork_page_urls": ["https://www.lemonamiga.com/game/example-castle-quest"],
        "provider": "curated",
        "confidence": 1.0,
    }))

    class H:
        def get_content_type(self): return "application/json"
        def get_content_charset(self): return "utf-8"
    class R(io.BytesIO):
        headers = H()
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def geturl(self): return self.url
    def opener(request, timeout=0):
        url = request.full_url
        if "w/api.php" in url:
            payload = {"query": {"pages": [{"pageid": 99, "title": "Example Castle Quest", "extract": "Amiga video game", "fullurl": "https://en.wikipedia.org/wiki/Joan",}]}}
            r = R(json.dumps(payload).encode()); r.url = url; return r
        html = b'<meta property="og:image" content="https://www.lemonamiga.com/media/covers/joan-front.jpg">'
        r = R(html); r.url = url; return r

    record, provider, _ = lookup_metadata("Example Castle Quest", cache_dir=cache, curated_dir=curated, opener=opener)
    assert record is not None
    assert record.artwork_url.endswith("joan-front.jpg")
    assert record.artwork_source_url.endswith("example-castle-quest")
    assert record.artwork_provider == "lemon-amiga"
    assert "lemon-amiga-artwork" in provider
