"""SettingsStore tests (Issue #15).

SettingsStore holds ONLY non-sensitive operator preferences. These tests verify
round-trip persistence, presets, and that the serialized form contains no secret
fields. The redaction guarantee for secrets lives in test_gui_secrets.py; this
module proves the settings file itself never serializes a secret (there is no
code path that accepts one).
"""

from __future__ import annotations

from pathlib import Path

from amiga_adf_library_builder.gui.settings import (
    Preset,
    Settings,
    SettingsStore,
)


def test_settings_round_trip(tmp_path: Path):
    path = tmp_path / "gui-settings.toml"
    store = SettingsStore(path)
    store.load()
    store.update(
        theme="dark",
        default_library_root="/data/lib",
        default_original_dir="/data/lib/original",
        online=True,
        refresh_metadata=True,
        advanced_mode=True,
    )
    # Reload from disk.
    store2 = SettingsStore(path)
    s = store2.load()
    assert s.theme == "dark"
    assert s.default_library_root == "/data/lib"
    assert s.online is True
    assert s.refresh_metadata is True
    assert s.advanced_mode is True


def test_settings_presets_round_trip(tmp_path: Path):
    path = tmp_path / "gui-settings.toml"
    store = SettingsStore(path)
    store.load()
    store.save_preset(
        Preset(
            name="my-lib",
            library_root="/data/lib",
            original_dir="/data/lib/original",
            staging_dir="/data/lib/work/staging",
            online=True,
        )
    )
    store2 = SettingsStore(path)
    s = store2.load()
    assert "my-lib" in s.presets
    p = s.presets["my-lib"]
    assert p.library_root == "/data/lib"
    assert p.online is True
    # Delete + reload.
    store2.delete_preset("my-lib")
    store3 = SettingsStore(path)
    s3 = store3.load()
    assert "my-lib" not in s3.presets


def test_settings_unknown_key_rejected(tmp_path: Path):
    store = SettingsStore(tmp_path / "gui-settings.toml")
    store.load()
    try:
        store.update(not_a_real_key="x")
        raise AssertionError("expected SettingsError")
    except Exception as exc:
        assert "unknown settings key" in str(exc)


def test_settings_file_contains_no_secret(tmp_path: Path):
    path = tmp_path / "gui-settings.toml"
    store = SettingsStore(path)
    store.load()
    store.update(
        theme="system",
        default_library_root="/data/lib",
        default_output_dir="/data/lib/output",
    )
    text = path.read_text(encoding="utf-8")
    # Defensive: a settings file must never carry secret-shaped keys.
    for forbidden in ("token", "api_key", "secret", "password", "bearer"):
        assert forbidden not in text.lower(), f"secret key leaked into settings: {forbidden}"


def test_settings_default_is_system_theme(tmp_path: Path):
    store = SettingsStore(tmp_path / "gui-settings.toml")
    s = store.load()
    assert s.theme == "system"
    assert s.presets == {}
