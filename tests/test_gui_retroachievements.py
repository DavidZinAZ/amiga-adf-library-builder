"""GUI adapter tests for the RetroAchievements provider (GH-13, Phase 4).

Offline, synthetic, NO live network, NO GUI (PySide6) dependency. The GUI
adapter (``RetroAchievementsProvider``) is a pure config/status/secrets object
over the core resolver, so it is tested directly, exactly like the Hasheous /
IGDB adapters.

Coverage:
  * registered in ``default_registry`` with a unique id
  * metadata declares the right capabilities + requires_secret + auth
  * default disabled; is_configured; enabled gating
  * set_field: known keys round-trip into to_config_dict; unknown key raises
  * to_config_dict mirrors RaConfig (enabled/base_url/console_name/bounds)
  * status: off -> "Turned on" path; enabled -> "Ready"
  * test_connection: Ready -> "Connection successful"
  * add_credentials / remove_credentials route the key to the SecretStore under
    the RETROACHIEVEMENTS_API_KEY-compatible secret id
  * to_config_dict -> RaConfig.from_dict round-trips (the GUI output is
    consumable by the core provider)
"""
from __future__ import annotations

from amiga_adf_library_builder import retroachievements as ra
from amiga_adf_library_builder.gui.providers import (
    ProviderCapability,
    ProviderRegistry,
    default_registry,
)


class _FakeSecretStore:
    def __init__(self):
        self.secrets = {}

    def set_secret(self, key, value):
        self.secrets[key] = value

    def delete_secret(self, key):
        self.secrets.pop(key, None)


def _ra_provider():
    from amiga_adf_library_builder.gui.providers import RetroAchievementsProvider
    return RetroAchievementsProvider()


def test_registered_in_default_registry():
    reg = default_registry()
    assert reg.get("retroachievements") is not None
    ids = [p.metadata.id for p in reg.all()]
    assert ids.count("retroachievements") == 1
    assert "retroachievements" in ids


def test_metadata_declares_capabilities_and_secret():
    p = _ra_provider()
    md = p.metadata
    assert md.id == "retroachievements"
    assert md.auth_required == "required"
    assert md.requires_secret is True
    for cap in (
        ProviderCapability.ONLINE_LOOKUP,
        ProviderCapability.HASH_RESOLUTION,
        ProviderCapability.METADATA,
        ProviderCapability.ARTWORK,
    ):
        assert cap in md.capabilities


def test_default_disabled_and_configured():
    p = _ra_provider()
    assert p.enabled() is False
    assert p.is_configured() is True  # base_url has a default
    p.set_enabled(True)
    assert p.enabled() is True
    p.set_enabled(False)
    assert p.enabled() is False


def test_set_field_known_and_unknown():
    p = _ra_provider()
    p.set_field("base_url", "https://ra.example.com/")
    p.set_field("console_name", "Amiga 500")
    p.set_field("timeout_seconds", "5.0")
    p.set_field("max_response_bytes", "500000")
    p.set_field("cache_ttl", "600")
    p.set_field("respect_rate_limit", "false")
    d = p.to_config_dict()
    assert d["base_url"] == "https://ra.example.com"  # trailing slash stripped
    assert d["console_name"] == "amiga 500"
    assert d["timeout_seconds"] == 5.0
    assert d["max_response_bytes"] == 500000
    assert d["cache_ttl"] == 600.0
    assert d["respect_rate_limit"] is False
    import pytest
    with pytest.raises(KeyError):
        p.set_field("not_a_field", "x")


def test_to_config_dict_round_trips_into_ra_config():
    p = _ra_provider()
    p.set_enabled(True)
    p.set_field("base_url", "https://ra.example.com")
    p.set_field("console_name", "Amiga")
    d = p.to_config_dict()
    cfg = ra.RaConfig.from_dict(d)
    assert cfg.enabled is True
    assert cfg.base_url == "https://ra.example.com"
    assert cfg.console_name == "amiga"


def test_status_paths():
    p = _ra_provider()
    s = p.status()
    assert s.ok is True and s.message == "Turned off"
    p.set_enabled(True)
    s = p.status()
    assert s.ok is True and s.message == "Ready" and s.configured is True


def test_connection_success_wording():
    p = _ra_provider()
    p.set_enabled(True)
    s = p.test_connection()
    assert s.ok is True and s.message == "Connection successful"
    # Off -> not "Connection successful"
    p2 = _ra_provider()
    s2 = p2.test_connection()
    assert s2.message != "Connection successful"


def test_credentials_round_trip():
    p = _ra_provider()
    store = _FakeSecretStore()
    p.add_credentials(store, api_key="ra-key-123")
    assert store.secrets.get("retroachievements_api_key") == "ra-key-123"
    p.remove_credentials(store)
    assert "retroachievements_api_key" not in store.secrets


def test_registry_config_dict_includes_ra_table():
    reg = default_registry()
    combined = reg.config_dict()
    assert "retroachievements" in combined
    assert combined["retroachievements"]["enabled"] is False
    assert combined["retroachievements"]["console_name"] == "amiga"
