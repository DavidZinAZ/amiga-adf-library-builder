"""SecretStore + SecretBackend tests (Issue #15).

Covers:
  * PortableVaultBackend AES-GCM encrypt/decrypt round-trip (value is recoverable).
  * A vault is locked until unlocked; locked reads raise.
  * Wrong password fails to unlock (no corruption / no silent success).
  * EnvSecretBackend round-trip.
  * DPAPI backend is reserved and unavailable on this platform (not the only path).
  * RedactingFilter masks secret-shaped values (key=value + Bearer + exact secret).
  * No secret value is persisted in plaintext on disk.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from amiga_adf_library_builder.gui.secrets import (
    EnvSecretBackend,
    PortableVaultBackend,
    RedactingFilter,
    SecretError,
    SecretStore,
    generate_master_password,
    win_dpapi_available,
)


def test_vault_round_trip(tmp_path: Path):
    vault = tmp_path / "secrets.vault"
    store = SecretStore.with_vault(vault, master_password="correct horse battery")
    store.set_secret("playmatch_token", "abc123secret")
    store.set_secret("hasheous_token", "xyz789secret")
    assert store.get_secret("playmatch_token") == "abc123secret"
    assert store.get_secret("hasheous_token") == "xyz789secret"
    assert set(store.list_keys()) == {"playmatch_token", "hasheous_token"}


def test_vault_persists_across_unlock(tmp_path: Path):
    vault = tmp_path / "secrets.vault"
    s1 = SecretStore.with_vault(vault, master_password="pw")
    s1.set_secret("k", "v")
    # New process-like handle: fresh backend, unlock with same password.
    s2 = SecretStore.with_vault(vault, master_password="pw")
    assert s2.get_secret("k") == "v"


def test_vault_locked_read_raises(tmp_path: Path):
    vault = tmp_path / "secrets.vault"
    # Persist a secret first (unlocked write).
    s1 = SecretStore.with_vault(vault, master_password="pw")
    s1.set_secret("k", "v")
    # A fresh backend with NO unlock must refuse reads (locked).
    b = PortableVaultBackend(vault)
    assert b.is_unlocked is False
    with pytest.raises(SecretError):
        b.get_secret("k")


def test_vault_wrong_password_fails(tmp_path: Path):
    vault = tmp_path / "secrets.vault"
    s1 = SecretStore.with_vault(vault, master_password="right-pw")
    s1.set_secret("k", "v")
    # Wrong password must NOT unlock / decrypt.
    s2 = SecretStore.with_vault(vault)
    with pytest.raises(SecretError):
        s2.unlock("wrong-pw")


def test_vault_change_password(tmp_path: Path):
    vault = tmp_path / "secrets.vault"
    s1 = SecretStore.with_vault(vault, master_password="old")
    s1.set_secret("k", "v")
    s1.change_password("old", "new")
    # Old password no longer works; new works.
    s_wrong = SecretStore.with_vault(vault)
    with pytest.raises(SecretError):
        s_wrong.unlock("old")
    s_new = SecretStore.with_vault(vault, master_password="new")
    assert s_new.get_secret("k") == "v"


def test_vault_plaintext_never_on_disk(tmp_path: Path):
    vault = tmp_path / "secrets.vault"
    s = SecretStore.with_vault(vault, master_password="pw")
    s.set_secret("playmatch_token", "SUPERSECRETVALUE123")
    blob = vault.read_bytes()
    assert b"SUPERSECRETVALUE123" not in blob


def test_env_backend_round_trip():
    b = EnvSecretBackend(prefix="ADFGUI_TEST_")
    b.clear()
    b.set_secret("k", "v")
    assert b.get_secret("k") == "v"
    assert "k" in b.list_keys()
    b.delete_secret("k")
    assert b.get_secret("k") is None
    b.clear()


def test_dpapi_reserved_and_unavailable_here():
    # On Linux CI the DPAPI backend must report unavailable (reserved, gated).
    assert win_dpapi_available() is False
    # Constructing it must fail safe (never the only path).
    with pytest.raises(Exception):
        # Either the class raises in __init__ or create() raises; both are fine.
        from amiga_adf_library_builder.gui.secrets import WinDpapiSecretBackend

        WinDpapiSecretBackend.create()


def test_redacting_filter_masks_key_value():
    f = RedactingFilter()
    masked = f.redact_text("GET /x?token=SECRETV&id=42")
    assert "SECRETV" not in masked
    assert "token=REDACTED" in masked
    assert "id=42" in masked  # non-sensitive preserved


def test_redacting_filter_masks_bearer():
    f = RedactingFilter()
    masked = f.redact_text("Authorization: Bearer abcdef123456")
    assert "abcdef123456" not in masked
    assert "Bearer REDACTED" in masked


def test_redacting_filter_masks_exact_secret():
    f = RedactingFilter()
    f.add_secret("hunter2verysecret")
    import logging

    rec = logging.LogRecord(
        "amiga_adf_gui", logging.INFO, __file__, 1,
        "password check hunter2verysecret done", None, None,
    )
    assert f.filter(rec) is True
    assert "hunter2verysecret" not in rec.getMessage()
    assert "REDACTED" in rec.getMessage()
    f.remove_secret("hunter2verysecret")


def test_redacting_filter_applied_to_log_record():
    f = RedactingFilter()
    rec = logging.LogRecord(
        "amiga_adf_gui", logging.INFO, __file__, 1, "token=TOPSECRET", None, None
    )
    assert f.filter(rec) is True
    # The record's message must now be redacted; the original is not retained.
    assert "TOPSECRET" not in rec.getMessage()
    assert "REDACTED" in rec.getMessage()


def test_generate_master_password_length_and_charset():
    pw = generate_master_password(20)
    assert len(pw) == 20
    # No ambiguous characters (e.g. 'l', '1', '0', 'O') to keep it typeable.
    assert "1" not in pw and "0" not in pw and "O" not in pw and "l" not in pw
