"""Secret storage for the GUI (architecturally separated from settings).

Settings (:mod:`amiga_adf_library_builder.gui.settings`) hold only non-sensitive
operator preferences. Secrets -- API tokens, passwords, keys -- live ONLY here.

* :class:`SecretBackend` is an ABC. Two backends ship: a portable AES-GCM
  :class:`PortableVaultBackend` (the MVP default) and an :class:`EnvSecretBackend`
  (reads/writes runtime environment variables).
* A Windows DPAPI backend (:class:`WinDpapiSecretBackend`) is reserved behind a
  capability check and is NEVER the only available path. It is only constructed
  on Windows with the ``cryptography`` DPAPI extension available.
* :class:`SecretStore` is the frontend the GUI uses. It never returns a secret
  value in a string that could be logged; callers pull values into memory and
  must not log them. :class:`RedactingFilter` masks any secret-shaped value that
  reaches the logging system.

NO secret is ever written to logs, diagnostics, exports, or UI error strings.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets as _secrets
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

#: Magic header for the vault file format (versioned).
_VAULT_MAGIC = b"ADF-VAULT\x00"
_VAULT_VERSION = 1

#: Character set for generated master passwords.
_MASTER_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"


class SecretError(Exception):
    """Base error for secret-store operations."""


class SecretBackendUnavailable(SecretError):
    """Raised when a requested backend cannot be constructed on this platform."""


class SecretBackend(ABC):
    """Abstract secret backend. Implementations never expose values in logs."""

    name: str = "abstract"

    @abstractmethod
    def is_available(self) -> bool:
        """Whether this backend can be used on the current platform."""

    @abstractmethod
    def set_secret(self, key: str, value: str) -> None:
        """Store ``value`` for ``key``."""

    @abstractmethod
    def get_secret(self, key: str) -> Optional[str]:
        """Return the stored value, or ``None`` if absent."""

    @abstractmethod
    def delete_secret(self, key: str) -> None:
        """Remove ``key`` if present."""

    @abstractmethod
    def list_keys(self) -> list[str]:
        """Return the list of stored secret keys (never the values)."""

    @abstractmethod
    def clear(self) -> None:
        """Remove all secrets stored by this backend."""


class PortableVaultBackend(SecretBackend):
    """AES-GCM encrypted vault file (portable, master-password unlocked).

    The vault is a single file in the app config dir. Each secret is encrypted
    under a key derived from the master password via PBKDF2-HMAC-SHA256. A vault
    with no master password set is *locked*; :meth:`unlock` must succeed before
    any read/write. The plaintext never touches disk or logs.
    """

    name = "portable-vault"

    def __init__(self, vault_path: Path, *, iterations: int = 200_000) -> None:
        self._path = Path(vault_path)
        self._iterations = iterations
        self._aesgcm: Optional[AESGCM] = None
        self._active_salt: Optional[bytes] = None
        self._lock = threading.RLock()

    # --- availability ---------------------------------------------------------
    def is_available(self) -> bool:
        return True

    # --- key derivation -------------------------------------------------------
    def _derive_key(self, password: str, salt: bytes) -> bytes:
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        from cryptography.hazmat.primitives import hashes

        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=self._iterations,
        )
        return kdf.derive(password.encode("utf-8"))

    # --- lock state -----------------------------------------------------------
    @property
    def is_unlocked(self) -> bool:
        return self._aesgcm is not None

    def unlock(self, password: str) -> None:
        """Derive the vault key from ``password`` and verify it opens the vault.

        Raises :class:`SecretError` on a wrong password or a corrupt vault.
        """
        with self._lock:
            existing_salt = self._peek_salt()
            if existing_salt is None:
                # Brand-new vault: any password initializes it. Persist the salt
                # we derive the key from so a later write uses the SAME salt.
                existing_salt = _secrets.token_bytes(16)
            key = self._derive_key(password, existing_salt)
            aesgcm = AESGCM(key)
            if self._path.is_file():
                try:
                    self._decrypt_store(aesgcm)
                except Exception as exc:  # wrong password / corrupt file
                    raise SecretError(f"vault unlock failed: {exc}") from exc
            # Remember the salt used so writes keep the key/store consistent.
            self._active_salt = existing_salt
            self._aesgcm = aesgcm

    def change_password(self, old_password: str, new_password: str) -> None:
        """Re-encrypt the entire vault under a new master password."""
        with self._lock:
            if not self.is_unlocked:
                # Need the old password to read; unlock first.
                self.unlock(old_password)
            assert self._aesgcm is not None
            store = self._decrypt_store(self._aesgcm)
            new_salt = _secrets.token_bytes(16)
            new_key = self._derive_key(new_password, new_salt)
            self._aesgcm = AESGCM(new_key)
            self._active_salt = new_salt
            self._write_store(store, salt=new_salt)

    # --- salt handling --------------------------------------------------------
    def _peek_salt(self) -> Optional[bytes]:
        if not self._path.is_file():
            return None
        try:
            raw = self._path.read_bytes()
        except OSError:
            return None
        if not raw.startswith(_VAULT_MAGIC):
            return None
        try:
            version = raw[len(_VAULT_MAGIC)]
            if version != _VAULT_VERSION:
                return None
            salt_len = raw[len(_VAULT_MAGIC) + 1]
            salt = raw[len(_VAULT_MAGIC) + 2 : len(_VAULT_MAGIC) + 2 + salt_len]
            return salt
        except IndexError:
            return None

    # --- crypto helpers -------------------------------------------------------
    def _decrypt_store(self, aesgcm: AESGCM) -> dict:
        raw = self._path.read_bytes()
        if not raw.startswith(_VAULT_MAGIC):
            raise SecretError("not a vault file")
        version = raw[len(_VAULT_MAGIC)]
        if version != _VAULT_VERSION:
            raise SecretError(f"unsupported vault version: {version}")
        salt_len = raw[len(_VAULT_MAGIC) + 1]
        off = len(_VAULT_MAGIC) + 2
        salt = raw[off : off + salt_len]
        off += salt_len
        nonce = raw[off : off + 12]
        off += 12
        ct = raw[off:]
        plaintext = aesgcm.decrypt(nonce, ct, _VAULT_MAGIC)
        return json.loads(plaintext.decode("utf-8"))

    def _write_store(self, store: dict, *, salt: bytes) -> None:
        assert self._aesgcm is not None
        plaintext = json.dumps(store, ensure_ascii=False).encode("utf-8")
        nonce = _secrets.token_bytes(12)
        ct = self._aesgcm.encrypt(nonce, plaintext, _VAULT_MAGIC)
        blob = (
            _VAULT_MAGIC
            + bytes([_VAULT_VERSION, len(salt)])
            + salt
            + nonce
            + ct
        )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_bytes(blob)
        tmp.replace(self._path)

    # --- secret ops -----------------------------------------------------------
    def _require_unlocked(self) -> AESGCM:
        if self._aesgcm is None:
            raise SecretError("vault is locked")
        return self._aesgcm

    def set_secret(self, key: str, value: str) -> None:
        with self._lock:
            aesgcm = self._require_unlocked()
            store = self._decrypt_store(aesgcm) if self._path.is_file() else {}
            store[key] = value
            salt = self._active_salt or self._peek_salt() or _secrets.token_bytes(16)
            self._active_salt = salt
            self._write_store(store, salt=salt)

    def get_secret(self, key: str) -> Optional[str]:
        with self._lock:
            if not self._path.is_file():
                return None
            aesgcm = self._require_unlocked()
            store = self._decrypt_store(aesgcm)
            return store.get(key)

    def delete_secret(self, key: str) -> None:
        with self._lock:
            if not self._path.is_file():
                return
            aesgcm = self._require_unlocked()
            store = self._decrypt_store(aesgcm)
            if key in store:
                del store[key]
                salt = self._active_salt or self._peek_salt() or _secrets.token_bytes(16)
                self._active_salt = salt
                self._write_store(store, salt=salt)

    def list_keys(self) -> list[str]:
        with self._lock:
            if not self._path.is_file():
                return []
            aesgcm = self._require_unlocked()
            store = self._decrypt_store(aesgcm)
            return list(store.keys())

    def clear(self) -> None:
        with self._lock:
            self._aesgcm = None
            if self._path.is_file():
                self._path.unlink()


def generate_master_password(length: int = 24) -> str:
    """Generate a human-typeable master password for a new vault."""
    return "".join(_secrets.choice(_MASTER_ALPHABET) for _ in range(length))


class EnvSecretBackend(SecretBackend):
    """Runtime environment-variable secret backend (no persistence).

    Secrets are read/written from ``AMIGA_ADF_SECRET_<KEY>`` environment
    variables. Useful for CI/headless runs and as a fallback backend. The
    values are never written to disk by this backend.
    """

    name = "env"

    def __init__(self, prefix: str = "AMIGA_ADF_SECRET_") -> None:
        self.prefix = prefix

    def is_available(self) -> bool:
        return True

    def _env_key(self, key: str) -> str:
        safe = "".join(c if c.isalnum() else "_" for c in key).upper()
        return f"{self.prefix}{safe}"

    def set_secret(self, key: str, value: str) -> None:
        os.environ[self._env_key(key)] = value

    def get_secret(self, key: str) -> Optional[str]:
        return os.environ.get(self._env_key(key))

    def delete_secret(self, key: str) -> None:
        os.environ.pop(self._env_key(key), None)

    def list_keys(self) -> list[str]:
        out = []
        for k in os.environ:
            if k.startswith(self.prefix):
                out.append(k[len(self.prefix):].lower())
        return out

    def clear(self) -> None:
        for k in list(os.environ):
            if k.startswith(self.prefix):
                os.environ.pop(k, None)


def win_dpapi_available() -> bool:
    """Return True only when the Windows DPAPI extension is importable."""
    if os.name != "nt":
        return False
    try:
        from cryptography.hazmat.primitives import serialization  # noqa: F401
        from cryptography.hazmat.primitives.ciphers import Cipher  # noqa: F401

        # The DPAPI helper lives in a platform-specific submodule; import lazily.
        import importlib.util

        spec = importlib.util.find_spec("cryptography.hazmat.bindings._dpaapi")
        return spec is not None
    except Exception:
        return False


class WinDpapiSecretBackend(SecretBackend):
    """Reserved Windows DPAPI backend (gated; not the only available backend).

    Construct via :meth:`create` so callers fail safe when DPAPI is unavailable.
    The GUI must still offer the portable vault and env backends regardless of
    platform, so this is never the sole path.
    """

    name = "win-dpapi"

    def __init__(self) -> None:
        if not win_dpapi_available():
            raise SecretBackendUnavailable("WinDpapiSecretBackend requires Windows DPAPI")

    @classmethod
    def create(cls) -> "WinDpapiSecretBackend":
        if not win_dpapi_available():
            raise SecretBackendUnavailable("WinDpapiSecretBackend requires Windows DPAPI")
        return cls()

    def is_available(self) -> bool:
        return win_dpapi_available()

    def set_secret(self, key: str, value: str) -> None:
        raise SecretBackendUnavailable("WinDpapiSecretBackend is reserved (not wired in MVP)")

    def get_secret(self, key: str) -> Optional[str]:
        raise SecretBackendUnavailable("WinDpapiSecretBackend is reserved (not wired in MVP)")

    def delete_secret(self, key: str) -> None:
        raise SecretBackendUnavailable("WinDpapiSecretBackend is reserved (not wired in MVP)")

    def list_keys(self) -> list[str]:
        raise SecretBackendUnavailable("WinDpapiSecretBackend is reserved (not wired in MVP)")

    def clear(self) -> None:
        raise SecretBackendUnavailable("WinDpapiSecretBackend is reserved (not wired in MVP)")


@dataclass
class _BackendHandle:
    backend: SecretBackend
    unlocked: bool = False


class SecretStore:
    """Frontend secret store used by the GUI.

    Holds a default backend (portable vault) plus optional additional backends
    (env, DPAPI-reserved). The store exposes only key-level operations; secret
    *values* are returned to callers in memory and must not be logged. Use
    :meth:`list_providers` for status display and :meth:`redact` /
    :class:`RedactingFilter` to keep secrets out of logs.
    """

    def __init__(self, default_backend: SecretBackend) -> None:
        self._default = default_backend
        self._backends: dict[str, SecretBackend] = {default_backend.name: default_backend}
        self._lock = threading.RLock()

    @classmethod
    def with_vault(cls, vault_path: Path, *, master_password: Optional[str] = None) -> "SecretStore":
        backend = PortableVaultBackend(vault_path)
        store = cls(backend)
        if master_password is not None:
            backend.unlock(master_password)
        return store

    # --- backend registry -----------------------------------------------------
    def register_backend(self, backend: SecretBackend) -> None:
        with self._lock:
            self._backends[backend.name] = backend

    def available_backends(self) -> list[str]:
        with self._lock:
            return [name for name, b in self._backends.items() if b.is_available()]

    # --- secret ops (default backend) -----------------------------------------
    def set_secret(self, key: str, value: str) -> None:
        with self._lock:
            self._default.set_secret(key, value)

    def get_secret(self, key: str) -> Optional[str]:
        with self._lock:
            return self._default.get_secret(key)

    def delete_secret(self, key: str) -> None:
        with self._lock:
            self._default.delete_secret(key)

    def list_keys(self) -> list[str]:
        with self._lock:
            return self._default.list_keys()

    @property
    def default_backend(self) -> SecretBackend:
        return self._default

    # --- vault convenience passthroughs (default backend) --------------------
    def unlock(self, password: str) -> None:
        from .secrets import PortableVaultBackend

        if not isinstance(self._default, PortableVaultBackend):
            raise SecretError("only the portable vault backend supports unlock")
        self._default.unlock(password)

    def change_password(self, old_password: str, new_password: str) -> None:
        from .secrets import PortableVaultBackend

        if not isinstance(self._default, PortableVaultBackend):
            raise SecretError("only the portable vault backend supports change_password")
        self._default.change_password(old_password, new_password)


# --- RedactingFilter + helpers -----------------------------------------------


class RedactingFilter(logging.Filter):
    """Logging filter that masks secret-shaped values before they are emitted.

    It inspects ``record.msg`` and any ``record.args`` (``%``-style) and replaces
    secret-shaped substrings with ``REDACTED``. Values are matched by key=value
    pairs (for sensitive param names) or by matching known secret strings passed
    via :meth:`add_secret` (the GUI adds resolved secret values here just before
    a risky log call, then removes them).
    """

    SENSITIVE_KEY_FRAGMENTS = (
        "token",
        "api_key",
        "apikey",
        "access_token",
        "accesskey",
        "secret",
        "client_secret",
        "private_key",
        "password",
        "passwd",
        "pwd",
        "bearer",
        "authorization",
    )

    _KV_RE = re.compile(
        r"(?P<key>[A-Za-z0-9_.-]*?(?:"
        + "|".join(SENSITIVE_KEY_FRAGMENTS)
        + r")[A-Za-z0-9_.-]*?)\s*=\s*(?P<val>[^\s&]+)",
        re.IGNORECASE,
    )
    _BEARER_RE = re.compile(
        r"(?P<pre>(?:authorization\s*[:=]\s*)?bearer\s+)(?P<tok>\S+)",
        re.IGNORECASE,
    )

    def __init__(self, name: str = "") -> None:
        super().__init__(name)
        self._exact_secrets: set[str] = set()

    def add_secret(self, value: str) -> None:
        if value:
            self._exact_secrets.add(value)

    def remove_secret(self, value: str) -> None:
        self._exact_secrets.discard(value)

    def clear_secrets(self) -> None:
        self._exact_secrets.clear()

    @classmethod
    def redact_text(cls, text: str) -> str:
        if not text:
            return text
        out = cls._KV_RE.sub(lambda m: f"{m.group('key')}=REDACTED", text)
        out = cls._BEARER_RE.sub(lambda m: f"{m.group('pre')}REDACTED", out)
        return out

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            msg = str(record.msg)
        redacted = self.redact_text(msg)
        for secret in self._exact_secrets:
            if secret and secret in redacted:
                redacted = redacted.replace(secret, "REDACTED")
        if redacted != msg:
            # Re-stamp the redacted text onto the record. We do not keep the
            # original (with secrets) anywhere reachable by later handlers.
            record.msg = redacted
            record.args = ()
        return True
