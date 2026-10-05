"""Provider abstraction tests (Issue #15).

Verifies the generic Provider protocol renders declarative metadata (so the GUI
builds a generic panel), honors enable/configure state, builds typed config
dicts, and never embeds secrets in config output.

The registry now contains exactly two providers -- Wikipedia (primary, enabled
by default, no credentials) and ScreenScraper (opt-in, credentialed) -- so these
tests exercise the protocol against those two instead of the removed providers.
"""

from __future__ import annotations

import pytest
from pathlib import Path

from amiga_adf_library_builder.gui.providers import (
    ProviderCapability,
    ProviderRegistry,
    ScreenScraperProvider,
    WikipediaProvider,
    default_registry,
)
from amiga_adf_library_builder.gui.secrets import SecretStore


def test_registry_has_wikipedia_and_screenscraper():
    reg = default_registry()
    ids = [p.metadata.id for p in reg.all()]
    assert ids == ["wikipedia", "screenscraper"]


def test_wikipedia_metadata_declares_fields_and_capabilities():
    wiki = WikipediaProvider()
    assert wiki.metadata.name == "Wikipedia"
    assert wiki.metadata.auth_required == "none"
    assert ProviderCapability.ONLINE_LOOKUP in wiki.metadata.capabilities
    assert ProviderCapability.METADATA in wiki.metadata.capabilities
    # Fields are declaratively described for the generic panel.
    keys = {f.key for f in wiki.metadata.fields}
    assert "min_interval_seconds" in keys
    assert "max_retries" in keys


def test_wikipedia_enabled_by_default():
    """Wikipedia is the primary supported provider: on out of the box."""
    wiki = WikipediaProvider()
    assert wiki.enabled() is True
    # ...and needs no configuration at all.
    assert wiki.is_configured() is True


def test_wikipedia_toggle_round_trips():
    wiki = WikipediaProvider()
    wiki.set_enabled(False)
    assert wiki.enabled() is False
    assert wiki.is_configured() is True
    wiki.set_enabled(True)
    assert wiki.enabled() is True


def test_wikipedia_config_dict_shape_and_types():
    wiki = WikipediaProvider()
    wiki.set_field("min_interval_seconds", "2.5")
    wiki.set_field("max_retries", "4")
    wiki.set_field("respect_retry_after", "false")
    cfg = wiki.to_config_dict()
    assert cfg["enabled"] is True
    assert cfg["min_interval_seconds"] == 2.5
    assert cfg["max_retries"] == 4
    assert cfg["respect_retry_after"] is False


def test_screenscraper_metadata_declares_fields_and_capabilities():
    ss = ScreenScraperProvider()
    assert ss.metadata.name == "ScreenScraper"
    assert ss.metadata.auth_required == "required"
    assert ProviderCapability.HASH_RESOLUTION in ss.metadata.capabilities
    assert "base_url" in {f.key for f in ss.metadata.fields}


def test_screenscraper_disabled_by_default_and_requires_config():
    ss = ScreenScraperProvider()
    assert ss.enabled() is False
    ss.set_field("base_url", "https://www.screenscraper.fr/api2/")
    ss.set_enabled(True)
    assert ss.is_configured() is True
    assert ss.enabled() is True
    ss.set_enabled(False)
    assert ss.enabled() is False
    assert ss.is_configured() is True


def test_screenscraper_config_dict_shape_and_types():
    ss = ScreenScraperProvider()
    ss.set_field("base_url", "https://www.screenscraper.fr/api2/")
    ss.set_field("timeout_seconds", "15.0")
    ss.set_field("respect_rate_limit", "false")
    ss.set_enabled(True)
    cfg = ss.to_config_dict()
    assert cfg["enabled"] is True
    assert cfg["timeout_seconds"] == 15.0
    assert cfg["respect_rate_limit"] is False


def test_provider_config_dict_never_contains_secret_value(tmp_path):
    """Secrets live in the vault, never in the TOML table."""
    ss = ScreenScraperProvider()
    ss.set_enabled(True)
    store = SecretStore.with_vault(tmp_path / "novault-test.vault",
                                   master_password="gui-test-pw")
    ss.add_credentials(store, dev_id="super-secret-token-123")
    cfg = ss.to_config_dict()
    assert "super-secret-token-123" not in repr(cfg)
    assert store.get_secret("screenscraper_dev_id") == "super-secret-token-123"
    store.delete_secret("screenscraper_dev_id")
    assert store.get_secret("screenscraper_dev_id") is None


def test_provider_unknown_field_raises():
    wiki = WikipediaProvider()
    with pytest.raises(KeyError):
        wiki.set_field("not_a_real_field", "x")


def test_registry_config_dict_merges_providers():
    reg = default_registry()
    wiki = reg.get("wikipedia")
    assert wiki is not None
    wiki.set_field("min_interval_seconds", "3.0")
    combined = reg.config_dict()
    assert "wikipedia" in combined
    assert "screenscraper" in combined
    assert combined["wikipedia"]["min_interval_seconds"] == 3.0
    assert combined["wikipedia"]["enabled"] is True


def test_registry_register_appends_and_dedupes():
    reg = ProviderRegistry()
    wiki = WikipediaProvider()
    reg.register(wiki)
    reg.register(wiki)
    assert [p.metadata.id for p in reg.all()] == ["wikipedia"]