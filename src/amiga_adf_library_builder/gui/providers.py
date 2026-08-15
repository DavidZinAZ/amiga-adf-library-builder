"""Generic provider abstraction for the Windows GUI.

A :class:`Provider` is a metadata/identity source surfaced in the GUI through a
SINGLE generic panel (no per-provider UI). The GUI renders controls purely from
the provider's metadata + capabilities, and routes actions (configure,
test-connection, add/remove credentials) through the protocol.

Playmatch and Hasheous (the two optional, DISABLED-by-default online
resolvers in the core) are exposed here as generic providers. Their credentials
(api tokens / base URLs) come from the :class:`SecretStore` -- never embedded in
config or code. Each provider builds its own typed config and is enabled only
when the operator turns it on AND the core ``run_pipeline`` is told to use it.

The protocol intentionally mirrors what :func:`pipeline.run_pipeline` needs so
the GUI can construct a provider-config TOML and pass it the same way the CLI
does (``--playmatch-config`` / ``--hasheous-config`` / ``--config``).
"""

from __future__ import annotations

import abc
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

#: Auth requirement for a provider.
AuthRequired = str  # one of: "required" | "optional" | "none"


class ProviderCapability(str, Enum):
    """Coarse capability a provider advertises to the generic panel."""

    ONLINE_LOOKUP = "online_lookup"
    HASH_RESOLUTION = "hash_resolution"
    METADATA = "metadata"
    ARTWORK = "artwork"
    EXPORT = "export"


@dataclass
class ProviderStatus:
    """Status reported by a provider for display in the generic panel."""

    ok: bool
    message: str = ""
    configured: bool = False
    reachable: Optional[bool] = None


@dataclass
class ProviderMetadata:
    """Declarative description the GUI uses to render a generic provider panel.

    ``fields`` are the non-secret configuration keys the GUI may show/edit.
    Secret keys are handled separately via :meth:`Provider.add_credentials` /
    the :class:`SecretStore`; the GUI never renders secret values.
    """

    id: str
    name: str
    description: str = ""
    auth_required: AuthRequired = "none"
    fields: list["ProviderField"] = field(default_factory=list)
    capabilities: list[ProviderCapability] = field(default_factory=list)
    requires_secret: bool = False


@dataclass
class ProviderField:
    """One non-secret configuration field rendered generically in the panel."""

    key: str
    label: str
    default: str = ""
    placeholder: str = ""
    help_text: str = ""


class Provider(abc.ABC):
    """Abstract provider surfaced by the generic GUI panel."""

    #: Subclasses set this. Read by the GUI to build the panel.
    metadata: ProviderMetadata

    @abc.abstractmethod
    def status(self) -> ProviderStatus:
        """Return current status (configured / reachable / message)."""

    @abc.abstractmethod
    def is_configured(self) -> bool:
        """Whether the provider has enough (non-secret) config to be used."""

    @abc.abstractmethod
    def test_connection(self) -> ProviderStatus:
        """Probe reachability; returns a status. Never raises to the GUI."""

    @abc.abstractmethod
    def to_config_dict(self) -> dict:
        """Return the typed ``[<id>]`` TOML table for this provider."""

    @abc.abstractmethod
    def set_field(self, key: str, value: str) -> None:
        """Update a non-secret config field."""

    def enabled(self) -> bool:
        """Whether the provider is turned on. Default: configured + enabled flag."""
        return self.is_configured()

    def set_enabled(self, enabled: bool) -> None:
        """Toggle the provider on/off. Default no-op (subclasses override)."""
        # Default: enablement is implied by configuration; concrete adapters
        # track an explicit enabled flag.

    def auth_required(self) -> AuthRequired:
        return self.metadata.auth_required

    def add_credentials(self, secret_store: Any, **secrets: str) -> None:
        """Persist provider secrets into ``secret_store`` (never embedded)."""
        # Default: no secrets. Subclasses with auth override this.
        raise NotImplementedError(f"provider {self.metadata.id} has no credentials")

    def remove_credentials(self, secret_store: Any) -> None:
        """Remove provider secrets from ``secret_store``."""
        raise NotImplementedError(f"provider {self.metadata.id} has no credentials")


# --- Playmatch provider adapter ----------------------------------------------


def _playmatch_field_defaults() -> list[ProviderField]:
    return [
        ProviderField(
            key="base_url",
            label="Base URL",
            default="https://api.playmatch.example/v1",
            placeholder="https://api.playmatch.example/v1",
            help_text="Self-hosted Playmatch endpoint. Only the public sha256 is transmitted.",
        ),
        ProviderField(
            key="timeout_seconds",
            label="Timeout (s)",
            default="10.0",
            help_text="Request timeout (bounded; cannot exceed 30s by policy).",
        ),
        ProviderField(
            key="max_response_bytes",
            label="Max response bytes",
            default="1000000",
            help_text="Response size cap (DoS defense).",
        ),
        ProviderField(
            key="confidence_threshold",
            label="Confidence threshold",
            default="0.9",
            help_text="Minimum confidence to auto-accept a match.",
        ),
    ]


def _build_playmatch_config_dict(*, enabled: bool, base_url: str, timeout_seconds: str,
                                 max_response_bytes: str, confidence_threshold: str) -> dict:
    """Build a typed ``[playmatch]`` TOML table (mirrors PlaymatchConfig)."""
    return {
        "enabled": enabled,
        "base_url": base_url or "https://api.playmatch.example/v1",
        "timeout_seconds": float(timeout_seconds or 10.0),
        "max_response_bytes": int(max_response_bytes or 1_000_000),
        "confidence_threshold": float(confidence_threshold or 0.9),
    }


class PlaymatchProvider(Provider):
    """Generic GUI adapter over the core Playmatch identity resolver.

    The provider is OPTIONAL and DISABLED by default. Its api token (if the
    operator's deployment requires one) lives in the SecretStore under the key
    ``playmatch_token`` -- never in config. The base URL and bounds are
    non-secret and rendered by the generic panel.
    """

    def __init__(self) -> None:
        self.metadata = ProviderMetadata(
            id="playmatch",
            name="Playmatch",
            description=(
                "Optional ROM-hash identity resolver. Transmits only the public "
                "sha256 of each disk; disabled by default."
            ),
            auth_required="optional",
            fields=_playmatch_field_defaults(),
            capabilities=[
                ProviderCapability.ONLINE_LOOKUP,
                ProviderCapability.HASH_RESOLUTION,
                ProviderCapability.METADATA,
            ],
            requires_secret=True,
        )
        self._enabled = False
        self._base_url = "https://api.playmatch.example/v1"
        self._timeout_seconds = "10.0"
        self._max_response_bytes = "1000000"
        self._confidence_threshold = "0.9"
        self._lock = threading.RLock()

    # --- config ---------------------------------------------------------------
    def is_configured(self) -> bool:
        with self._lock:
            return bool(self._base_url and self._base_url.strip())

    def enabled(self) -> bool:
        with self._lock:
            return self._enabled and self.is_configured()

    def set_field(self, key: str, value: str) -> None:
        with self._lock:
            if key == "base_url":
                self._base_url = (value or "").rstrip("/")
            elif key == "timeout_seconds":
                self._timeout_seconds = value
            elif key == "max_response_bytes":
                self._max_response_bytes = value
            elif key == "confidence_threshold":
                self._confidence_threshold = value
            elif key == "enabled":
                self._enabled = (value == "true" or value is True)
            else:
                raise KeyError(f"unknown playmatch field: {key}")

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._enabled = bool(enabled)

    def to_config_dict(self) -> dict:
        with self._lock:
            return _build_playmatch_config_dict(
                enabled=self._enabled,
                base_url=self._base_url,
                timeout_seconds=self._timeout_seconds,
                max_response_bytes=self._max_response_bytes,
                confidence_threshold=self._confidence_threshold,
            )

    # --- status ---------------------------------------------------------------
    def status(self) -> ProviderStatus:
        with self._lock:
            if not self._enabled:
                return ProviderStatus(ok=True, message="disabled", configured=self.is_configured())
            if not self.is_configured():
                return ProviderStatus(ok=False, message="base URL not set", configured=False)
            return ProviderStatus(ok=True, message="configured", configured=True)

    def test_connection(self) -> ProviderStatus:
        # The core provider performs the real SSRF-guarded fetch lazily; the GUI
        # does not open sockets here. We report configured status only.
        return self.status()

    # --- secrets --------------------------------------------------------------
    def add_credentials(self, secret_store: Any, **secrets: str) -> None:
        token = secrets.get("token")
        if token:
            secret_store.set_secret("playmatch_token", token)

    def remove_credentials(self, secret_store: Any) -> None:
        secret_store.delete_secret("playmatch_token")


# --- Hasheous provider adapter -----------------------------------------------


def _hasheous_field_defaults() -> list[ProviderField]:
    return [
        ProviderField(
            key="base_url",
            label="Base URL",
            default="https://api.hasheous.example/v1",
            placeholder="https://api.hasheous.example/v1",
            help_text="Self-hosted Hasheous endpoint. Unauthenticated hash lookup.",
        ),
        ProviderField(
            key="timeout_seconds",
            label="Timeout (s)",
            default="10.0",
            help_text="Request timeout (bounded; cannot exceed 30s by policy).",
        ),
        ProviderField(
            key="max_response_bytes",
            label="Max response bytes",
            default="1000000",
            help_text="Response size cap (DoS defense).",
        ),
        ProviderField(
            key="confidence_threshold",
            label="Confidence threshold",
            default="0.9",
            help_text="Minimum confidence to auto-accept a match.",
        ),
        ProviderField(
            key="respect_rate_limit",
            label="Respect rate limit (429)",
            default="true",
            help_text="Honor HTTP 429 + Retry-After (ToS).",
        ),
    ]


def _build_hasheous_config_dict(*, enabled: bool, base_url: str, timeout_seconds: str,
                                max_response_bytes: str, confidence_threshold: str,
                                respect_rate_limit: str) -> dict:
    """Build a typed ``[hasheous]`` TOML table (mirrors HasheousConfig)."""
    return {
        "enabled": enabled,
        "base_url": base_url or "https://api.hasheous.example/v1",
        "timeout_seconds": float(timeout_seconds or 10.0),
        "max_response_bytes": int(max_response_bytes or 1_000_000),
        "confidence_threshold": float(confidence_threshold or 0.9),
        "respect_rate_limit": (respect_rate_limit == "true" or respect_rate_limit is True),
    }


class HasheousProvider(Provider):
    """Generic GUI adapter over the core Hasheous identity resolver.

    OPTIONAL and DISABLED by default. The live Hasheous lookup is
    unauthenticated (no API key needed), so ``auth_required`` is ``none``; the
    adapter still supports a secret token slot for self-hosted deployments that
    require one, stored under ``hasheous_token`` in the SecretStore.
    """

    def __init__(self) -> None:
        self.metadata = ProviderMetadata(
            id="hasheous",
            name="Hasheous",
            description=(
                "Optional ROM-hash identity resolver (real /Lookup/ByHash/sha256 "
                "route). Unauthenticated; disabled by default."
            ),
            auth_required="none",
            fields=_hasheous_field_defaults(),
            capabilities=[
                ProviderCapability.ONLINE_LOOKUP,
                ProviderCapability.HASH_RESOLUTION,
                ProviderCapability.METADATA,
            ],
            requires_secret=False,
        )
        self._enabled = False
        self._base_url = "https://api.hasheous.example/v1"
        self._timeout_seconds = "10.0"
        self._max_response_bytes = "1000000"
        self._confidence_threshold = "0.9"
        self._respect_rate_limit = "true"
        self._lock = threading.RLock()

    # --- config ---------------------------------------------------------------
    def is_configured(self) -> bool:
        with self._lock:
            return bool(self._base_url and self._base_url.strip())

    def enabled(self) -> bool:
        with self._lock:
            return self._enabled and self.is_configured()

    def set_field(self, key: str, value: str) -> None:
        with self._lock:
            if key == "base_url":
                self._base_url = (value or "").rstrip("/")
            elif key == "timeout_seconds":
                self._timeout_seconds = value
            elif key == "max_response_bytes":
                self._max_response_bytes = value
            elif key == "confidence_threshold":
                self._confidence_threshold = value
            elif key == "respect_rate_limit":
                self._respect_rate_limit = value
            elif key == "enabled":
                self._enabled = (value == "true" or value is True)
            else:
                raise KeyError(f"unknown hasheous field: {key}")

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._enabled = bool(enabled)

    def to_config_dict(self) -> dict:
        with self._lock:
            return _build_hasheous_config_dict(
                enabled=self._enabled,
                base_url=self._base_url,
                timeout_seconds=self._timeout_seconds,
                max_response_bytes=self._max_response_bytes,
                confidence_threshold=self._confidence_threshold,
                respect_rate_limit=self._respect_rate_limit,
            )

    # --- status ---------------------------------------------------------------
    def status(self) -> ProviderStatus:
        with self._lock:
            if not self._enabled:
                return ProviderStatus(ok=True, message="disabled", configured=self.is_configured())
            if not self.is_configured():
                return ProviderStatus(ok=False, message="base URL not set", configured=False)
            return ProviderStatus(ok=True, message="configured", configured=True)

    def test_connection(self) -> ProviderStatus:
        # Real fetch is performed lazily by the core provider under SSRF guards.
        return self.status()

    # --- secrets --------------------------------------------------------------
    def add_credentials(self, secret_store: Any, **secrets: str) -> None:
        token = secrets.get("token")
        if token:
            secret_store.set_secret("hasheous_token", token)

    def remove_credentials(self, secret_store: Any) -> None:
        secret_store.delete_secret("hasheous_token")


# --- Registry ----------------------------------------------------------------


class ProviderRegistry:
    """Holds the known providers and renders a generic panel from metadata."""

    def __init__(self) -> None:
        self._providers: dict[str, Provider] = {}
        self._order: list[str] = []
        self._lock = threading.RLock()

    def register(self, provider: Provider) -> None:
        with self._lock:
            self._providers[provider.metadata.id] = provider
            if provider.metadata.id not in self._order:
                self._order.append(provider.metadata.id)

    def get(self, provider_id: str) -> Optional[Provider]:
        with self._lock:
            return self._providers.get(provider_id)

    def all(self) -> list[Provider]:
        with self._lock:
            return [self._providers[pid] for pid in self._order]

    def config_dict(self) -> dict:
        """Assemble the combined TOML table for all enabled/known providers."""
        with self._lock:
            out: dict[str, Any] = {}
            for pid in self._order:
                p = self._providers[pid]
                out[pid] = p.to_config_dict()
            return out


def default_registry() -> ProviderRegistry:
    """Return a registry pre-loaded with the two core online resolvers."""
    reg = ProviderRegistry()
    reg.register(PlaymatchProvider())
    reg.register(HasheousProvider())
    return reg
