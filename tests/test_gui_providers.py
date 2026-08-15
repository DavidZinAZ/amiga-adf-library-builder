"""Provider abstraction tests (Issue #15).

Verifies the generic Provider protocol + the Playmatch/Hasheous adapters render
declarative metadata (so the GUI builds a generic panel), honor enable/configure
state, build typed config dicts, and never embed secrets in their config output.
"""

from __future__ import annotations

import pytest
from pathlib import Path

from amiga_adf_library_builder.gui.providers import (
    HasheousProvider,
    PlaymatchProvider,
    ProviderCapability,
    ProviderRegistry,
    default_registry,
)
from amiga_adf_library_builder.gui.secrets import SecretStore


def test_registry_has_both_core_providers():
    reg = default_registry()
    ids = [p.metadata.id for p in reg.all()]
    assert "playmatch" in ids
    assert "hasheous" in ids


def test_provider_metadata_declares_fields_and_capabilities():
    pm = PlaymatchProvider()
    assert pm.metadata.name == "Playmatch"
    assert pm.metadata.auth_required in ("required", "optional", "none")
    assert pm.metadata.capabilities  # non-empty
    assert ProviderCapability.HASH_RESOLUTION in pm.metadata.capabilities
    # Fields are declaratively described for the generic panel.
    keys = {f.key for f in pm.metadata.fields}
    assert "base_url" in keys


def test_provider_disabled_by_default():
    pm = PlaymatchProvider()
    # Disabled by default: not enabled even though a placeholder base_url exists.
    assert pm.enabled() is False
    # A fresh provider has not been turned on by the operator.
    assert pm.metadata.auth_required in ("required", "optional", "none")


def test_provider_enabled_requires_config():
    pm = PlaymatchProvider()
    pm.set_field("base_url", "https://playmatch.example/v1")
    pm.set_enabled(True)
    assert pm.is_configured() is True
    assert pm.enabled() is True

    # Disabling drops enabled but not configured.
    pm.set_enabled(False)
    assert pm.enabled() is False
    assert pm.is_configured() is True


def test_playmatch_config_dict_shape_and_types():
    pm = PlaymatchProvider()
    pm.set_field("base_url", "https://playmatch.example/v1/")
    pm.set_field("timeout_seconds", "15.0")
    pm.set_field("max_response_bytes", "2000000")
    pm.set_field("confidence_threshold", "0.8")
    pm.set_enabled(True)
    cfg = pm.to_config_dict()
    assert cfg["enabled"] is True
    assert cfg["base_url"] == "https://playmatch.example/v1"  # trailing slash stripped
    assert cfg["timeout_seconds"] == 15.0
    assert cfg["max_response_bytes"] == 2_000_000
    assert cfg["confidence_threshold"] == 0.8


def test_hasheous_config_dict_shape_and_types():
    hs = HasheousProvider()
    hs.set_field("base_url", "https://hasheous.example/v1/")
    hs.set_field("respect_rate_limit", "false")
    hs.set_enabled(True)
    cfg = hs.to_config_dict()
    assert cfg["enabled"] is True
    assert cfg["base_url"] == "https://hasheous.example/v1"
    assert cfg["respect_rate_limit"] is False


def test_provider_config_dict_never_contains_secret_value():
    pm = PlaymatchProvider()
    pm.set_field("base_url", "https://playmatch.example/v1")
    pm.set_enabled(True)
    store = SecretStore.with_vault(Path("/tmp/adfgui-novault-test.vault"), master_password="gui-test-pw")
    pm.add_credentials(store, token="super-secret-token-123")
    # The TOML table must not contain the token.
    cfg = pm.to_config_dict()
    blob = repr(cfg)
    assert "super-secret-token-123" not in blob
    # The secret lives only in the store.
    assert store.get_secret("playmatch_token") == "super-secret-token-123"
    store.delete_secret("playmatch_token")
    assert store.get_secret("playmatch_token") is None


def test_provider_unknown_field_raises():
    pm = PlaymatchProvider()
    with pytest.raises(KeyError):
        pm.set_field("not_a_real_field", "x")


def test_registry_config_dict_merges_providers():
    reg = default_registry()
    pm = reg.get("playmatch")
    assert pm is not None
    pm.set_field("base_url", "https://playmatch.example/v1")
    pm.set_enabled(True)
    combined = reg.config_dict()
    assert "playmatch" in combined
    assert "hasheous" in combined
    assert combined["playmatch"]["enabled"] is True
