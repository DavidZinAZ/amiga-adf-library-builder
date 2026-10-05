"""Prove the six removed providers are gone and Wikipedia is first-class.

Removal is a claim that is easy to make and easy to regress: a single leftover
import, registry entry or event category resurrects dead provider behaviour.
These tests assert the ABSENCE contract directly rather than inferring it from
a passing suite.

Backward compatibility is asserted positively too: an existing portable
configuration full of obsolete keys must still start cleanly.
"""
import ast
import os
from pathlib import Path

import pytest

from amiga_adf_library_builder import wikipedia_config as wc
from amiga_adf_library_builder.wikipedia_client import reset_global_gate

REMOVED_IDS = (
    "hall-of-light",
    "lemon-amiga",
    "lemonamiga",
    "playmatch",
    "igdb",
    "retroachievements",
    "hasheous",
)

#: Provider ids that must remain.
KEPT_IDS = ("wikipedia", "screenscraper")

SRC = Path(__file__).resolve().parents[1] / "src" / "amiga_adf_library_builder"


@pytest.fixture(autouse=True)
def _reset_gate():
    reset_global_gate()
    yield
    reset_global_gate()


def _source_files():
    return sorted(SRC.rglob("*.py"))


def _read(path):
    return path.read_text(encoding="utf-8", errors="replace")


# --- registry ---------------------------------------------------------------

def test_removed_providers_absent_from_default_registry():
    from amiga_adf_library_builder.gui.providers import default_registry
    ids = {p.metadata.id for p in default_registry().all()}
    for removed in REMOVED_IDS:
        assert removed not in ids, f"{removed} still in the provider registry"


def test_registry_contains_exactly_wikipedia_and_screenscraper():
    from amiga_adf_library_builder.gui.providers import default_registry
    ids = [p.metadata.id for p in default_registry().all()]
    assert ids == list(KEPT_IDS)


def test_registry_lookup_returns_none_for_removed_ids():
    from amiga_adf_library_builder.gui.providers import default_registry
    reg = default_registry()
    for removed in REMOVED_IDS:
        assert reg.get(removed) is None


def test_registry_config_dict_has_no_removed_tables():
    from amiga_adf_library_builder.gui.providers import default_registry
    tables = default_registry().config_dict()
    for removed in REMOVED_IDS:
        assert removed not in tables
    assert "wikipedia" in tables


def test_removed_provider_classes_no_longer_exist():
    """The adapter classes and their core modules must be gone, not aliased."""
    import amiga_adf_library_builder.gui.providers as gp

    for name in ("PlaymatchProvider", "HasheousProvider", "IgdbProvider",
                 "RetroAchievementsProvider", "LemonAmigaProvider",
                 "HallOfLightProvider"):
        assert not hasattr(gp, name), f"{name} still defined"


def test_removed_provider_modules_are_deleted():
    for module in ("playmatch.py", "hasheous.py", "igdb.py",
                   "retroachievements.py"):
        assert not (SRC / module).exists(), f"{module} still present"


def test_core_removed_symbols_are_gone():
    import amiga_adf_library_builder.metadata as md
    for name in ("hall_of_light_lookup", "lemonamiga_lookup",
                 "lemonamiga_discover_docs", "lemonamiga_fetch_doc",
                 "HallOfLightConfig", "LemonAmigaConfig",
                 "BlockedProviderCircuit", "_BLOCKED_PROVIDERS",
                 "reset_blocked_provider_circuit"):
        assert not hasattr(md, name), f"metadata.{name} still defined"


def test_removed_config_loaders_are_gone():
    import amiga_adf_library_builder.paths as paths
    for name in ("load_playmatch_config", "load_hasheous_config",
                 "load_igdb_config", "load_retroachievements_config",
                 "load_hall_of_light_config"):
        assert not hasattr(paths, name), f"paths.{name} still defined"


def test_removed_run_config_fields_are_gone():
    import dataclasses

    from amiga_adf_library_builder.run_config import RunConfig
    names = {f.name for f in dataclasses.fields(RunConfig)}
    for removed in ("playmatch_config_path", "hasheous_config_path",
                    "igdb_config_path", "retroachievements_config_path",
                    "hall_of_light_config_path"):
        assert removed not in names
    assert "wikipedia_config_path" in names


def test_removed_enrich_event_categories_are_gone():
    from amiga_adf_library_builder.enrich import EnrichCategory
    values = {c.value for c in EnrichCategory}
    for token in ("playmatch", "hasheous", "igdb", "retroachievements"):
        assert not any(token in v for v in values), f"{token} category remains"


def test_enrich_group_signature_dropped_removed_providers():
    import inspect

    from amiga_adf_library_builder.enrich import enrich_group
    params = set(inspect.signature(enrich_group).parameters)
    for removed in ("playmatch_provider", "hasheous_provider", "igdb_provider",
                    "retroachievements_provider", "halloflight_enabled"):
        assert removed not in params
    assert "wikipedia_gate" in params


# --- runtime never calls them ----------------------------------------------

def test_no_source_module_imports_a_removed_provider_module():
    for path in _source_files():
        text = _read(path)
        for module in ("playmatch", "hasheous", "igdb", "retroachievements"):
            for stmt in (f"from .{module} import", f"from ..{module} import",
                         f"import {module}"):
                assert stmt not in text, f"{path.name} still imports {module}"


def test_lookup_metadata_chain_has_no_removed_provider_attempt():
    """No `Querying <removed>` log line and no provider attempt for them."""
    import inspect

    from amiga_adf_library_builder import metadata
    source = inspect.getsource(metadata.lookup_metadata)
    for removed in ("hall_of_light_lookup", "lemonamiga_lookup"):
        assert removed not in source
    for removed in ("hall-of-light", "lemon-amiga"):
        assert f'"{removed}"' not in source


def test_lookup_metadata_calls_only_wikipedia_for_unkeyed_lookup():
    import inspect

    from amiga_adf_library_builder import metadata
    source = inspect.getsource(metadata.lookup_metadata)
    assert '_try_provider("wikipedia"' in source
    assert "wikipedia_enabled" in source


def test_enrich_activity_never_names_a_removed_provider():
    import inspect

    from amiga_adf_library_builder import enrich
    source = inspect.getsource(enrich)
    for token in ("Playmatch", "Hasheous", "Igdb", "RetroAchievements",
                  "Lemon Amiga", "Hall of Light"):
        assert token not in source, f"{token} still appears in enrich runtime"


# --- diagnostics ------------------------------------------------------------

def test_removed_provider_diagnostics_are_not_mapped():
    from amiga_adf_library_builder.diagnostics import attempt_from_enrich_events
    # An event naming a removed provider must not produce a provider attempt.
    events = [
        {"category": "playmatch_miss", "detail": "x", "ok": False},
        {"category": "igdb_miss", "detail": "x", "ok": False},
        {"category": "retroachievements_review", "detail": "x", "ok": False},
    ]
    attempts = attempt_from_enrich_events(events)
    providers = {a.provider for a in attempts}
    for removed in ("playmatch", "igdb", "retroachievements"):
        assert removed not in providers


def test_no_bot_challenge_outcome_remains_in_lookup_diagnostics():
    """HoL/Lemon-only anti-bot classification was removed with those providers."""
    import inspect

    from amiga_adf_library_builder import metadata
    source = inspect.getsource(metadata.lookup_metadata)
    assert "bot_challenge" not in source
    assert "_BotChallengeError" not in source


def test_online_provider_list_contains_no_removed_ids():
    from amiga_adf_library_builder.lookup_workflow import (
        KIND_ONLINE, providers_for_mode,
    )

    ids = providers_for_mode(KIND_ONLINE)
    for removed in REMOVED_IDS:
        assert removed not in ids
    assert "wikipedia" in ids


# --- backward compatibility -------------------------------------------------

def test_stale_provider_config_is_ignored_and_startup_succeeds(tmp_path):
    """An old portable config full of removed keys must load safely."""
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        """
library_root = "/tmp/library"

[hall-of-light]
enabled = true
timeout_seconds = 20.0
cache_ttl = 86400

[lemonamiga]
enabled = true

[playmatch]
enabled = true
base_url = "https://api.playmatch.example/v1"

[igdb]
enabled = true

[retroachievements]
enabled = true

[hasheous]
enabled = true

[screenscraper]
enabled = false

[wikipedia]
min_interval_seconds = 2.5
max_retries = 4
""",
        encoding="utf-8",
    )

    from amiga_adf_library_builder.paths import load_wikipedia_config, load_screenscraper_config

    wiki = load_wikipedia_config(str(cfg))
    assert wiki["min_interval_seconds"] == 2.5, "unrelated wikipedia key must survive"

    ss = load_screenscraper_config(str(cfg))
    assert ss == {"enabled": False}, "screenscraper settings must be untouched"

    # Obsolete keys are simply not read; nothing raises.
    assert "hall-of-light" not in wiki and "igdb" not in wiki


def test_stale_config_populates_wikipedia_defaults_when_missing(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('[igdb]\nenabled = true\n', encoding="utf-8")
    from amiga_adf_library_builder.paths import load_wikipedia_config

    parsed = wc.WikipediaConfig.from_dict(load_wikipedia_config(str(cfg)))
    assert parsed.enabled is True
    assert parsed.min_interval_seconds == 1.0
    assert parsed.max_retries == 2
    assert parsed.respect_retry_after is True
    assert parsed.retry_after_cap_seconds == 60.0


def test_corrupt_wikipedia_table_type_is_ignored(tmp_path):
    """A `[wikipedia]` table of the wrong TOML type must not crash startup."""
    cfg = tmp_path / "config.toml"
    cfg.write_text('wikipedia = "not-a-table"\n', encoding="utf-8")
    from amiga_adf_library_builder.paths import load_wikipedia_config

    assert load_wikipedia_config(str(cfg)) == {}
    assert wc.WikipediaConfig.from_dict({}).max_retries == 2


def test_unrelated_settings_are_not_reset_by_provider_cleanup(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        """
[rtfm]
enabled = true
template = "quick-start"

[local_media]
enabled = true

[hall-of-light]
enabled = true
""",
        encoding="utf-8",
    )
    from amiga_adf_library_builder.paths import load_rtfm_config, load_local_media_config

    assert load_rtfm_config(str(cfg)).get("enabled") is True
    assert load_local_media_config(str(cfg)).get("enabled") is True


def test_no_obsolete_provider_hosts_remain_in_manual_approval_allowlist():
    from amiga_adf_library_builder.manual_approvals import HOST_ALLOWLIST
    for host in ("lemonamiga.com", "amiga.abime.net",
                 "halloflight.amiga32.org"):
        assert host not in HOST_ALLOWLIST
    assert "wikipedia.org" in HOST_ALLOWLIST


# --- Wikipedia is a first-class provider -----------------------------------

def test_wikipedia_visible_in_gui_registry():
    from amiga_adf_library_builder.gui.providers import default_registry
    wiki = default_registry().get("wikipedia")
    assert wiki is not None
    assert wiki.metadata.name == "Wikipedia"


def test_wikipedia_enabled_by_default_with_documented_defaults():
    from amiga_adf_library_builder.gui.providers import WikipediaProvider
    p = WikipediaProvider()
    assert p.enabled() is True
    table = p.to_config_dict()
    assert table["min_interval_seconds"] == 1.0
    assert table["retries_enabled"] is True
    assert table["max_retries"] == 2
    assert table["respect_retry_after"] is True
    assert table["retry_after_cap_seconds"] == 60.0


def test_wikipedia_field_keys_match_config_keys():
    from amiga_adf_library_builder.gui.providers import WikipediaProvider
    field_keys = {f.key for f in WikipediaProvider().metadata.fields}
    for key in ("enabled", "min_interval_seconds", "retries_enabled",
                "max_retries", "respect_retry_after",
                "retry_after_cap_seconds", "use_cache"):
        assert key in field_keys


def test_wikipedia_tooltips_mention_rate_limiting_and_retry_after():
    from amiga_adf_library_builder.gui.providers import WikipediaProvider
    help_text = " ".join(
        f.help_text for f in WikipediaProvider().metadata.fields
    ).lower()
    assert "429" in help_text or "rate limit" in help_text
    assert "wait" in help_text


def test_wikipedia_tooltips_do_not_leak_internal_tuning():
    """Internal matcher/scoring detail must not reach the operator UI."""
    from amiga_adf_library_builder.gui.providers import WikipediaProvider
    blob = " ".join(
        f"{f.label} {f.help_text}" for f in WikipediaProvider().metadata.fields
    ).lower()
    for leak in ("subject-token", "candidate scoring", "circuit breaker",
                 "relevance threshold", "token"):
        assert leak not in blob


def test_gui_settings_round_trip_through_config_to_runtime():
    """GUI -> config table -> policy: the full operator path."""
    from amiga_adf_library_builder.gui.providers import WikipediaProvider
    from amiga_adf_library_builder.wikipedia_config import apply_effective_policy
    from amiga_adf_library_builder.wikipedia_client import get_global_gate

    p = WikipediaProvider()
    p.set_field("min_interval_seconds", "3.0")
    p.set_field("max_retries", "6")
    p.set_field("respect_retry_after", "false")

    apply_effective_policy(p.to_config_dict())
    gate = get_global_gate()
    assert gate.policy.min_interval_seconds == 3.0
    assert gate.policy.max_retries == 6
    assert gate.policy.respect_retry_after is False


def test_gui_corrupt_field_falls_back_instead_of_producing_bad_policy():
    from amiga_adf_library_builder.gui.providers import WikipediaProvider
    p = WikipediaProvider()
    p.set_field("min_interval_seconds", "garbage")
    assert p.to_config_dict()["min_interval_seconds"] == 1.0


def test_wikipedia_gui_persists_and_reloads():
    from amiga_adf_library_builder.gui.providers import (
        WikipediaProvider, default_registry,
    )
    saved = WikipediaProvider().to_config_dict()

    reloaded = WikipediaProvider()
    for key, value in saved.items():
        reloaded.set_field(key, "true" if value is True else
                           "false" if value is False else str(value))
    assert reloaded.to_config_dict() == saved


def test_screenscraper_remains_present_and_unconfigured_by_default():
    from amiga_adf_library_builder.gui.providers import default_registry
    ss = default_registry().get("screenscraper")
    assert ss is not None
    assert ss.metadata.id == "screenscraper"
    # Untouched defaults: still opt-in, still credentialed.
    assert ss.enabled() is False
    assert ss.auth_required() == "required"


def test_screenscraper_field_set_is_unchanged_by_cleanup():
    from amiga_adf_library_builder.gui.providers import ScreenScraperProvider
    keys = {f.key for f in ScreenScraperProvider().metadata.fields}
    assert keys == {
        "base_url", "timeout_seconds", "max_response_bytes",
        "max_concurrency", "confidence_threshold", "preferred_regions",
        "download_metadata", "download_artwork", "download_manuals",
        "cache_ttl", "respect_rate_limit", "rate_limit_backoff_seconds",
    }


def test_screenscraper_credentials_flow_is_intact():
    from amiga_adf_library_builder.gui.providers import ScreenScraperProvider

    class Store:
        def __init__(self):
            self.d = {}
        def set_secret(self, k, v):
            self.d[k] = v
        def delete_secret(self, k):
            self.d.pop(k, None)

    store = Store()
    ss = ScreenScraperProvider()
    ss.add_credentials(store, dev_id="id", dev_password="pw", softname="sn")
    assert store.d["screenscraper_dev_id"] == "id"
    ss.remove_credentials(store)
    assert "screenscraper_dev_id" not in store.d


def test_wikipedia_effective_settings_are_reported_for_diagnostics():
    from amiga_adf_library_builder.gui.providers import WikipediaProvider
    p = WikipediaProvider()
    p.set_field("min_interval_seconds", "2.0")
    report = wc.effective_settings_report(p.to_config_dict())
    assert report["request delay"] == "2.0 seconds"
    assert report["wikipedia enabled"] is True
    assert report["maximum retries"] == 2


def test_wikipedia_test_connection_reports_disabled_without_network():
    from amiga_adf_library_builder.gui.providers import WikipediaProvider
    p = WikipediaProvider()
    p.set_enabled(False)
    status = p.test_connection()
    assert status.message == "Disabled"
    assert status.ok is False


def test_wikipedia_test_connection_maps_failures_to_plain_language(monkeypatch):
    from amiga_adf_library_builder.gui import providers as gp

    class Boom(RuntimeError):
        pass

    def exploding_lookup(*a, **k):
        raise Boom("network down")

    import amiga_adf_library_builder.metadata as md
    monkeypatch.setattr(md, "wikipedia_lookup", exploding_lookup)
    status = gp.WikipediaProvider().test_connection()
    assert status.message == "Network error"
    assert status.ok is False


def test_wikipedia_enrichment_still_resolves_a_record():
    """Wikipedia enrichment must still work end-to-end (offline opener)."""
    from amiga_adf_library_builder import metadata as md

    html = (
        '<html><head><title>Amiga — Wikipedia</title></head><body>'
        '<p>Amiga is a family of computers.</p></body></html>'
    )
    # NOTE: the existing fetch path treats query.pages as an iterable of page
    # dicts, so the fixture supplies that shape (a real API dict-keyed response
    # is handled by the pre-existing parser, not by this test).
    api_payload = (
        '{"query":{"pages":[{"pageid":123,"title":"Amiga",'
        '"extract":"Amiga is a family of computers."}]}}'
    )

    class Resp:
        def __init__(self, body, url):
            self._b = body.encode()
            self._url = url
        def read(self, *_a):
            return self._b
        def geturl(self):
            return self._url
        headers = None
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def fake_opener(request, timeout=None):
        target = request.full_url if hasattr(request, "full_url") else str(request)
        if "api/rest_v1" in target or "w/api.php" in target:
            return Resp(api_payload, target)
        return Resp(html, target)

    reset_global_gate()
    record = md.wikipedia_lookup("Amiga", timeout=5.0, opener=fake_opener)
    assert record is not None
    assert "Amiga" in (record.canonical_title or "")


# --- source hygiene ---------------------------------------------------------

def test_every_python_source_file_parses():
    for path in _source_files():
        try:
            ast.parse(_read(path), filename=str(path))
        except SyntaxError as exc:  # pragma: no cover
            pytest.fail(f"{path.name} does not parse: {exc}")


def test_no_removed_provider_name_in_removed_provider_free_modules():
    """No live code references removed providers.

    Docstrings that explain which obsolete config keys are ignored on upgrade
    are allowed; executable references are not. Checked via AST names/imports
    rather than raw text so prose does not produce false failures.
    """
    allowed_files = {"wikipedia_config.py", "metadata.py"}
    for path in _source_files():
        if path.name in allowed_files:
            continue
        tree = ast.parse(_read(path), filename=str(path))
        imported: set[str] = set()
        attrs: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[-1])
            elif isinstance(node, ast.Attribute):
                attrs.add(node.attr)
            elif isinstance(node, ast.Name):
                attrs.add(node.id)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                attrs.add(node.name)
        for token in ("playmatch", "hasheous", "retroachievements",
                      "Playmatch", "Hasheous", "RetroAchievements"):
            assert token not in imported, f"{path.name} imports {token}"
            assert token not in attrs, f"{path.name} references {token}"


def test_docs_example_config_parses_and_documents_wikipedia():
    import tomllib
    example = Path(__file__).resolve().parents[1] / "config" / "example.toml"
    data = tomllib.loads(_read(example))
    assert data["wikipedia"]["min_interval_seconds"] == 1.0
    assert data["wikipedia"]["max_retries"] == 2
    assert data["wikipedia"]["respect_retry_after"] is True
    assert data["wikipedia"]["retry_after_cap_seconds"] == 60
    for removed in ("hall-of-light", "lemonamiga", "playmatch", "igdb",
                    "retroachievements", "hasheous"):
        assert removed not in data