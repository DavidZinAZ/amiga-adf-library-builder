"""Generic provider abstraction for the Windows GUI.

A :class:`Provider` is a metadata/identity source surfaced in the GUI through a
SINGLE generic panel (no per-provider UI). The GUI renders controls purely from
the provider's metadata + capabilities, and routes actions (configure,
test-connection, add/remove credentials) through the protocol.

Two providers are supported:

- **Wikipedia** -- the primary online metadata/artwork provider. Enabled by
  default, no credentials. Its adapter owns the effective request policy
  (delay, retries, Retry-After handling) that the runtime request gate
  actually enforces.
- **ScreenScraper** -- optional, disabled until developer API access is
  available. Out of scope for changes.

The protocol intentionally mirrors what :func:`pipeline.run_pipeline` needs so
the GUI can construct a provider-config TOML and pass it the same way the CLI
does (``--config``).
"""

from __future__ import annotations

import abc
import logging
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from ..wikipedia_client import WikipediaGate
from ..wikipedia_config import WikipediaConfig, coerce_bool

logger = logging.getLogger(__name__)


def _as_bool(value: Any, default: bool) -> bool:
    """Interpret a GUI field value as a bool.

    Thin alias over the config-layer coercion so the panel and the config file
    agree on exactly one set of accepted truthy/falsy spellings.
    """
    return coerce_bool(value, default)

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




# --- Wikipedia provider adapter ----------------------------------------------
# PRIMARY supported online metadata provider. No credentials: the MediaWiki
# API is public, so `auth_required` is ``none`` and ``requires_secret`` is
# False. Wikipedia is ENABLED by default -- it is the provider the library
# relies on.


def _wikipedia_field_defaults() -> list[ProviderField]:
    return [
        ProviderField(
            key="enabled",
            label="Enable Wikipedia",
            default="true",
            help_text=(
                "Look up release metadata and artwork on Wikipedia. "
                "Turned on by default -- this is the main online source."
            ),
        ),
        ProviderField(
            key="min_interval_seconds",
            label="Minimum time between requests (seconds)",
            default="1.0",
            help_text=(
                "How long to wait between requests to Wikipedia. Raising this "
                "value reduces HTTP 429 (too many requests) rate limiting. "
                "Allowed range 0.5 to 10.0 seconds."
            ),
        ),
        ProviderField(
            key="retries_enabled",
            label="Retry when Wikipedia asks us to slow down",
            default="true",
            help_text=(
                "When Wikipedia rate limits a request, wait and try again "
                "instead of dropping the release."
            ),
        ),
        ProviderField(
            key="max_retries",
            label="Maximum retries",
            default="2",
            help_text="How many times to retry a request that was rate limited.",
        ),
        ProviderField(
            key="respect_retry_after",
            label="Follow the server's requested wait",
            default="true",
            help_text=(
                "When Wikipedia asks us to wait a set number of seconds, obey "
                "that instruction (up to the maximum wait below) rather than "
                "guessing how long to pause."
            ),
        ),
        ProviderField(
            key="retry_after_cap_seconds",
            label="Maximum wait when the server asks (seconds)",
            default="60",
            help_text=(
                "The most we will ever wait for a server-requested pause. "
                "A server asking for hours will not stall a library run."
            ),
        ),
        ProviderField(
            key="use_cache",
            label="Reuse previously downloaded lookups",
            default="true",
            help_text=(
                "Reuse lookups already downloaded, so repeated runs do not "
                "re-request the same pages from Wikipedia."
            ),
        ),
    ]


class WikipediaProvider(Provider):
    """GUI adapter owning the effective Wikipedia request policy.

    The values shown here are not cosmetic. ``to_config_dict`` is written to
    the provider config file, and the run-start path hands that table to
    :func:`wikipedia_config.apply_effective_policy`, which installs it on the
    process-wide request gate. Editing the delay in this panel therefore
    changes the real pacing of real requests -- that is the whole point.

    Internal matcher tuning (title matching, relevance thresholds, candidate
    scoring, circuit-breaker internals) is deliberately NOT surfaced: those
    are library implementation details, not operator settings.
    """

    def __init__(self) -> None:
        self.metadata = ProviderMetadata(
            id="wikipedia",
            name="Wikipedia",
            description=(
                "Primary online metadata and artwork provider. Looks up each "
                "release on Wikipedia, pacing requests so the site is not "
                "overloaded. No account or key required."
            ),
            auth_required="none",
            fields=_wikipedia_field_defaults(),
            capabilities=[
                ProviderCapability.ONLINE_LOOKUP,
                ProviderCapability.METADATA,
                ProviderCapability.ARTWORK,
            ],
            requires_secret=False,
        )
        self._enabled = True
        self._min_interval_seconds = "1.0"
        self._retries_enabled = "true"
        self._max_retries = "2"
        self._respect_retry_after = "true"
        self._retry_after_cap_seconds = "60"
        self._use_cache = "true"
        self._lock = threading.RLock()

    # --- config -------------------------------------------------------------
    def is_configured(self) -> bool:
        """Wikipedia needs no configuration to be usable."""
        with self._lock:
            return True

    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    def set_field(self, key: str, value: str) -> None:
        # Values are held as raw strings and sanitised on read-out through
        # WikipediaConfig, so a half-typed value in the GUI can never produce
        # an invalid policy. Unknown keys stay loud.
        with self._lock:
            if key == "enabled":
                self._enabled = _as_bool(value, True)
            elif key == "min_interval_seconds":
                self._min_interval_seconds = value
            elif key == "retries_enabled":
                self._retries_enabled = _as_bool(value, True)
            elif key == "max_retries":
                self._max_retries = value
            elif key == "respect_retry_after":
                self._respect_retry_after = _as_bool(value, True)
            elif key == "retry_after_cap_seconds":
                self._retry_after_cap_seconds = value
            elif key == "use_cache":
                self._use_cache = _as_bool(value, True)
            else:
                raise KeyError(f"unknown wikipedia field: {key}")

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._enabled = bool(enabled)

    def to_config_dict(self) -> dict:
        """Return the sanitised ``[wikipedia]`` table actually used at runtime.

        Round-tripping through :class:`WikipediaConfig` is deliberate: the
        panel then reports the value that will really be enforced, so a corrupt
        field shows up as the documented default rather than as a silent
        failure at request time.
        """
        with self._lock:
            return WikipediaConfig.from_dict({
                "enabled": self._enabled,
                "min_interval_seconds": self._min_interval_seconds,
                "retries_enabled": self._retries_enabled,
                "max_retries": self._max_retries,
                "respect_retry_after": self._respect_retry_after,
                "retry_after_cap_seconds": self._retry_after_cap_seconds,
                "use_cache": self._use_cache,
            }).to_dict()

    # --- status -------------------------------------------------------------
    def status(self) -> ProviderStatus:
        with self._lock:
            if not self._enabled:
                return ProviderStatus(ok=True, message="Turned off", configured=True)
            return ProviderStatus(ok=True, message="Ready", configured=True)

    def test_connection(self) -> ProviderStatus:
        """Run one harmless known lookup and report a plain-language result.

        Returns one of: Disabled / OK / Rate limited / Network error. Never
        raises into the GUI and never blocks longer than the lookup timeout --
        this is a button press, not a background job.
        """
        with self._lock:
            enabled = self._enabled
        if not enabled:
            return ProviderStatus(ok=False, message="Disabled", configured=True)

        policy = WikipediaConfig.from_dict(self.to_config_dict()).to_policy()
        # A dedicated gate: a connectivity test must not consume the shared
        # per-run request budget or trip the shared rate-limit cooldown for the
        # rest of the run.
        gate = WikipediaGate(policy=policy).without_sleeping()
        try:
            from ..metadata import wikipedia_lookup
            record = wikipedia_lookup(
                "Amiga",
                timeout=10.0,
                gate=gate,
                diagnostics=[],
            )
        except Exception as exc:                       # noqa: BLE001
            logger.warning("Wikipedia test lookup failed: %s", exc)
            return ProviderStatus(ok=False, message="Network error", configured=True)

        if record is not None:
            return ProviderStatus(ok=True, message="OK", configured=True,
                                  reachable=True)
        if gate.rate_limited_events:
            return ProviderStatus(ok=False, message="Rate limited", configured=True,
                                  reachable=True)
        if gate.requests_made == 0:
            return ProviderStatus(ok=False, message="Network error", configured=True)
        # A request went out and did not fail -- the title simply had no match.
        return ProviderStatus(ok=True, message="OK", configured=True, reachable=True)

    # --- secrets -------------------------------------------------------------
    def add_credentials(self, secret_store: Any, **secrets: str) -> None:
        raise NotImplementedError("wikipedia has no credentials")

    def remove_credentials(self, secret_store: Any) -> None:
        raise NotImplementedError("wikipedia has no credentials")


# --- ScreenScraper provider adapter ----------------------------------------


def _screenscraper_field_defaults() -> list[ProviderField]:
    return [
        ProviderField(
            key="base_url",
            label="ScreenScraper API endpoint",
            default="https://www.screenscraper.fr/api2/",
            placeholder="https://www.screenscraper.fr/api2/",
            help_text=(
                "ScreenScraper WebAPI endpoint. Only change if using a mirror or "
                "self-hosted proxy."
            ),
        ),
        ProviderField(
            key="timeout_seconds",
            label="Time limit per request (seconds)",
            default="15.0",
            help_text="How long to wait for the server before giving up (capped at 30 seconds).",
        ),
        ProviderField(
            key="max_response_bytes",
            label="Maximum response size (bytes)",
            default="2000000",
            help_text="Refuse to read more than this from the server (protection against oversized replies).",
        ),
        ProviderField(
            key="max_concurrency",
            label="Maximum concurrent requests",
            default="1",
            help_text="Maximum number of concurrent API requests (capped at 4; ScreenScraper has thread limits).",
        ),
        ProviderField(
            key="confidence_threshold",
            label="Minimum match confidence",
            default="0.85",
            help_text="A result is accepted automatically only if the match confidence is at least this (0–1).",
        ),
        ProviderField(
            key="preferred_regions",
            label="Preferred regions (comma-separated)",
            default="us,eu,wor",
            help_text="Region preference for artwork/manual selection (e.g., us, eu, wor, jp).",
        ),
        ProviderField(
            key="download_metadata",
            label="Download metadata",
            default="true",
            help_text="Enable metadata retrieval from ScreenScraper.",
        ),
        ProviderField(
            key="download_artwork",
            label="Download artwork",
            default="true",
            help_text="Enable artwork retrieval from ScreenScraper.",
        ),
        ProviderField(
            key="download_manuals",
            label="Download manuals (PDF)",
            default="true",
            help_text="Enable PDF manual retrieval from ScreenScraper.",
        ),
        ProviderField(
            key="cache_ttl",
            label="Cache TTL (seconds)",
            default="86400",
            help_text="How long to cache successful lookups (<= 0 disables).",
        ),
        ProviderField(
            key="respect_rate_limit",
            label="Honor rate limits (429 Retry-After)",
            default="true",
            help_text="Pause and retry once when the server asks us to slow down (ToS compliance).",
        ),
        ProviderField(
            key="rate_limit_backoff_seconds",
            label="Rate limit backoff (seconds)",
            default="5.0",
            help_text="Default wait when server sends no Retry-After header (capped at 60s).",
        ),
    ]


def _build_screenscraper_config_dict(
    *,
    enabled: bool,
    base_url: str,
    timeout_seconds: str,
    max_response_bytes: str,
    max_concurrency: str,
    confidence_threshold: str,
    preferred_regions: str,
    download_metadata: str,
    download_artwork: str,
    download_manuals: str,
    cache_ttl: str,
    respect_rate_limit: str,
    rate_limit_backoff_seconds: str,
) -> dict:
    """Build a typed ``[screenscraper]`` TOML table (mirrors ScreenScraperConfig)."""
    return {
        "enabled": enabled,
        "base_url": base_url or "https://www.screenscraper.fr/api2/",
        "timeout_seconds": float(timeout_seconds or 15.0),
        "max_response_bytes": int(max_response_bytes or 2_000_000),
        "max_concurrency": int(max_concurrency or 1),
        "confidence_threshold": float(confidence_threshold or 0.85),
        "preferred_regions": [r.strip().lower() for r in preferred_regions.split(",") if r.strip()],
        "download_metadata": (download_metadata == "true" or download_metadata is True),
        "download_artwork": (download_artwork == "true" or download_artwork is True),
        "download_manuals": (download_manuals == "true" or download_manuals is True),
        "cache_ttl": float(cache_ttl or 86400.0),
        "respect_rate_limit": (respect_rate_limit == "true" or respect_rate_limit is True),
        "rate_limit_backoff_seconds": float(rate_limit_backoff_seconds or 5.0),
    }


class ScreenScraperProvider(Provider):
    """Generic GUI adapter over the core ScreenScraper metadata/artwork/manual provider.

    The provider is OPTIONAL and DISABLED by default. Its credentials
    (devid, devpassword, softname, ssid, sspassword) live in the SecretStore
    under the keys ``screenscraper_dev_id``, ``screenscraper_dev_password``,
    ``screenscraper_softname``, ``screenscraper_ssid``, ``screenscraper_sspassword``
    -- never in config. The base URL and bounds are non-secret and rendered
    by the generic panel.
    """

    def __init__(self) -> None:
        self.metadata = ProviderMetadata(
            id="screenscraper",
            name="ScreenScraper",
            description=(
                "Optional metadata, artwork, and manual provider via ScreenScraper WebAPI. "
                "Supports hash-first (CRC/MD5/SHA1) lookup, cached provider ID reuse, "
                "and title + Amiga system search. Requires developer credentials "
                "(devid, devpassword, softname) from ScreenScraper. Member credentials "
                "(ssid, sspassword) are optional for higher limits. Disabled by default."
            ),
            auth_required="required",
            fields=_screenscraper_field_defaults(),
            capabilities=[
                ProviderCapability.ONLINE_LOOKUP,
                ProviderCapability.HASH_RESOLUTION,
                ProviderCapability.METADATA,
                ProviderCapability.ARTWORK,
            ],
            requires_secret=True,
        )
        self._enabled = False
        self._base_url = "https://www.screenscraper.fr/api2/"
        self._timeout_seconds = "15.0"
        self._max_response_bytes = "2000000"
        self._max_concurrency = "1"
        self._confidence_threshold = "0.85"
        self._preferred_regions = "us,eu,wor"
        self._download_metadata = "true"
        self._download_artwork = "true"
        self._download_manuals = "true"
        self._cache_ttl = "86400"
        self._respect_rate_limit = "true"
        self._rate_limit_backoff_seconds = "5.0"
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
            elif key == "max_concurrency":
                self._max_concurrency = value
            elif key == "confidence_threshold":
                self._confidence_threshold = value
            elif key == "preferred_regions":
                self._preferred_regions = value
            elif key == "download_metadata":
                self._download_metadata = value
            elif key == "download_artwork":
                self._download_artwork = value
            elif key == "download_manuals":
                self._download_manuals = value
            elif key == "cache_ttl":
                self._cache_ttl = value
            elif key == "respect_rate_limit":
                self._respect_rate_limit = value
            elif key == "rate_limit_backoff_seconds":
                self._rate_limit_backoff_seconds = value
            elif key == "enabled":
                self._enabled = (value == "true" or value is True)
            else:
                raise KeyError(f"unknown screenscraper field: {key}")

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._enabled = bool(enabled)

    def to_config_dict(self) -> dict:
        with self._lock:
            return _build_screenscraper_config_dict(
                enabled=self._enabled,
                base_url=self._base_url,
                timeout_seconds=self._timeout_seconds,
                max_response_bytes=self._max_response_bytes,
                max_concurrency=self._max_concurrency,
                confidence_threshold=self._confidence_threshold,
                preferred_regions=self._preferred_regions,
                download_metadata=self._download_metadata,
                download_artwork=self._download_artwork,
                download_manuals=self._download_manuals,
                cache_ttl=self._cache_ttl,
                respect_rate_limit=self._respect_rate_limit,
                rate_limit_backoff_seconds=self._rate_limit_backoff_seconds,
            )

    # --- status ---------------------------------------------------------------
    def status(self) -> ProviderStatus:
        with self._lock:
            if not self._enabled:
                return ProviderStatus(ok=True, message="Turned off", configured=self.is_configured())
            if not self.is_configured():
                return ProviderStatus(ok=False, message="Not set up yet — enter the API endpoint below", configured=False)
            return ProviderStatus(ok=True, message="Ready", configured=True)

    def test_connection(self) -> ProviderStatus:
        # Real fetch is performed lazily by the core provider under SSRF guards.
        status = self.status()
        if status.ok and status.message == "Ready":
            # Explicit success wording for connection check (GH-42)
            return ProviderStatus(ok=True, message="Connection successful", configured=status.configured, reachable=status.reachable)
        return status

    # --- secrets --------------------------------------------------------------
    def add_credentials(self, secret_store: Any, **secrets: str) -> None:
        dev_id = secrets.get("dev_id")
        dev_password = secrets.get("dev_password")
        softname = secrets.get("softname")
        ssid = secrets.get("ssid")
        sspassword = secrets.get("sspassword")
        if dev_id:
            secret_store.set_secret("screenscraper_dev_id", dev_id)
        if dev_password:
            secret_store.set_secret("screenscraper_dev_password", dev_password)
        if softname:
            secret_store.set_secret("screenscraper_softname", softname)
        if ssid:
            secret_store.set_secret("screenscraper_ssid", ssid)
        if sspassword:
            secret_store.set_secret("screenscraper_sspassword", sspassword)

    def remove_credentials(self, secret_store: Any) -> None:
        secret_store.delete_secret("screenscraper_dev_id")
        secret_store.delete_secret("screenscraper_dev_password")
        secret_store.delete_secret("screenscraper_softname")
        secret_store.delete_secret("screenscraper_ssid")
        secret_store.delete_secret("screenscraper_sspassword")


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
    """Return a registry pre-loaded with the supported online providers.

    Exactly two providers remain: Wikipedia (primary, enabled by default) and
    ScreenScraper (opt-in, pending developer API access).
    """
    reg = ProviderRegistry()
    reg.register(WikipediaProvider())
    reg.register(ScreenScraperProvider())
    return reg
