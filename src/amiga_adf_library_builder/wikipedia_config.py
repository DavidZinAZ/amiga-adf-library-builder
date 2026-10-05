"""Authoritative Wikipedia provider configuration and effective-policy wiring.

Why this module exists
----------------------
:class:`~amiga_adf_library_builder.wikipedia_client.WikipediaPolicy` already
owned the pacing numbers (min interval, retry count, Retry-After handling), and
the global gate already enforced them at runtime — but nothing fed that policy
from configuration. The numbers were dataclass defaults baked into the process,
so an operator editing a config file, or a GUI control that "saved" a value,
had no effect on the shared request gate at all. This module is the single
place that turns stored configuration into the policy the gate actually uses.

Design rules
------------
1. ONE authoritative effective configuration. :func:`effective_policy` is the
   only sanctioned path from config text to :class:`WikipediaPolicy`. Runtime
   callers must not invent their own values.
2. Fail safe, never fail loud. A missing key, a corrupt string, a negative
   number or a value outside the allowed range falls back to the known-good
   default rather than raising. A bad config file must not stop a library run.
3. No separate hard-coded runtime override. Applying the effective policy sets
   the process-wide gate, so every call site that already uses
   ``get_global_gate()`` is governed by it without touching those call sites.

Defaults are the known-good values documented in the module docstring of
``wikipedia_client``: 1.0 s floor, 2 retries, Retry-After honoured, capped at
60 s.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any, Optional

from .wikipedia_client import (
    WikipediaGate,
    WikipediaPolicy,
    get_global_gate,
    reset_global_gate,
)

__all__ = [
    "WIKIPEDIA_MIN_INTERVAL_DEFAULT",
    "WIKIPEDIA_MIN_INTERVAL_MIN",
    "WIKIPEDIA_MIN_INTERVAL_MAX",
    "WIKIPEDIA_MAX_RETRIES_DEFAULT",
    "WIKIPEDIA_MAX_RETRIES_MIN",
    "WIKIPEDIA_MAX_RETRIES_MAX",
    "WIKIPEDIA_RETRY_AFTER_CAP_DEFAULT",
    "WikipediaConfig",
    "coerce_bool",
    "effective_policy",
    "apply_effective_policy",
    "effective_settings_report",
]

#: Default/floor/ceiling for the operator-visible request delay, in seconds.
WIKIPEDIA_MIN_INTERVAL_DEFAULT = 1.0
WIKIPEDIA_MIN_INTERVAL_MIN = 0.5
WIKIPEDIA_MIN_INTERVAL_MAX = 10.0

#: Default/floor/ceiling for retries AFTER the initial attempt.
WIKIPEDIA_MAX_RETRIES_DEFAULT = 2
WIKIPEDIA_MAX_RETRIES_MIN = 0
WIKIPEDIA_MAX_RETRIES_MAX = 10

#: Default/floor/ceiling for the honoured ``Retry-After`` wait, in seconds.
WIKIPEDIA_RETRY_AFTER_CAP_DEFAULT = 60.0
WIKIPEDIA_RETRY_AFTER_CAP_MIN = 0.0
WIKIPEDIA_RETRY_AFTER_CAP_MAX = 600.0

#: Boolean config keys arrive as TOML bools, GUI strings or legacy strings.
_TRUTHY = {"true", "1", "yes", "on", "enabled"}
_FALSY = {"false", "0", "no", "off", "disabled", ""}


def coerce_bool(value: Any, default: bool) -> bool:
    """Interpret a config/GUI value as a boolean, falling back to ``default``.

    Handles TOML native bools, ints, and the string forms a GUI text box or a
    hand-edited TOML file produces. Anything unrecognised returns ``default``
    instead of raising, so a corrupt value degrades to the known-good default.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        token = value.strip().lower()
        if token in _TRUTHY:
            return True
        if token in _FALSY:
            return False
    return default


def _coerce_float(value: Any, default: float,
                  low: float, high: float) -> float:
    """Parse a float and clamp it into ``[low, high]``, or use ``default``.

    Out-of-range values are clamped rather than rejected: an operator who asks
    for a 30-second delay gets the documented ceiling instead of a run that
    refuses to start.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    # Reject NaN and +/-inf. Clamping an infinite request delay to the
    # documented ceiling would silently turn a corrupt value into a valid —
    # and extremely slow — operator setting instead of falling back.
    if not math.isfinite(number):
        return default
    return max(low, min(high, number))


def _coerce_int(value: Any, default: int, low: int, high: int) -> int:
    """Parse an int and clamp it into ``[low, high]``, or use ``default``."""
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


@dataclass(frozen=True)
class WikipediaConfig:
    """Typed, already-sanitised ``[wikipedia]`` configuration.

    Construction is the sanitisation boundary: by the time a
    ``WikipediaConfig`` exists, every field holds a usable value inside its
    documented range. ``from_dict`` is therefore the only place that has to
    worry about raw or corrupt input.
    """

    enabled: bool = True
    min_interval_seconds: float = WIKIPEDIA_MIN_INTERVAL_DEFAULT
    retries_enabled: bool = True
    max_retries: int = WIKIPEDIA_MAX_RETRIES_DEFAULT
    respect_retry_after: bool = True
    retry_after_cap_seconds: float = WIKIPEDIA_RETRY_AFTER_CAP_DEFAULT
    use_cache: bool = True

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "WikipediaConfig":
        """Build a config from raw ``[wikipedia]`` table text, sanitising it.

        An absent table (``None``/``{}``) yields the defaults: Wikipedia is the
        primary supported provider, so it is ON out of the box. Individual
        corrupt keys fall back independently, so one bad value never discards
        the rest of a valid file.
        """
        data = data or {}
        defaults = cls()
        # Cache usage is a separate key from the pacing policy but belongs to
        # the same provider toggle; an unknown provider cache setting must not
        # silently disable pacing.
        cache_raw = data.get("use_cache", data.get("cache_enabled"))
        return cls(
            enabled=coerce_bool(data.get("enabled"), defaults.enabled),
            min_interval_seconds=_coerce_float(
                data.get("min_interval_seconds",
                         data.get("request_delay_seconds")),
                defaults.min_interval_seconds,
                WIKIPEDIA_MIN_INTERVAL_MIN,
                WIKIPEDIA_MIN_INTERVAL_MAX,
            ),
            retries_enabled=coerce_bool(
                data.get("retries_enabled", data.get("retry_on_429")),
                defaults.retries_enabled,
            ),
            max_retries=_coerce_int(
                data.get("max_retries"),
                defaults.max_retries,
                WIKIPEDIA_MAX_RETRIES_MIN,
                WIKIPEDIA_MAX_RETRIES_MAX,
            ),
            respect_retry_after=coerce_bool(
                data.get("respect_retry_after", data.get("honor_retry_after")),
                defaults.respect_retry_after,
            ),
            retry_after_cap_seconds=_coerce_float(
                data.get("retry_after_cap_seconds",
                         data.get("max_retry_after_seconds")),
                defaults.retry_after_cap_seconds,
                WIKIPEDIA_RETRY_AFTER_CAP_MIN,
                WIKIPEDIA_RETRY_AFTER_CAP_MAX,
            ),
            use_cache=coerce_bool(cache_raw, defaults.use_cache),
        )

    def to_dict(self) -> dict:
        """Round-trippable ``[wikipedia]`` table."""
        return {
            "enabled": self.enabled,
            "min_interval_seconds": self.min_interval_seconds,
            "retries_enabled": self.retries_enabled,
            "max_retries": self.max_retries,
            "respect_retry_after": self.respect_retry_after,
            "retry_after_cap_seconds": self.retry_after_cap_seconds,
            "use_cache": self.use_cache,
        }

    def to_policy(self) -> WikipediaPolicy:
        """Project this config onto the runtime pacing policy.

        Only the pacing knobs the operator can set are carried over. The
        per-run budget, the consecutive-rate-limit ceiling and the backoff base
        remain library defaults: they are safety ceilings rather than operator
        tuning, and exposing them in the GUI is explicitly out of scope.
        """
        base = WikipediaPolicy()
        return replace(
            base,
            min_interval_seconds=self.min_interval_seconds,
            max_retries=self.max_retries,
            respect_retry_after=self.respect_retry_after,
            retry_after_cap_seconds=self.retry_after_cap_seconds,
            retries_enabled=self.retries_enabled,
        )


def effective_policy(config: Optional[dict]) -> WikipediaPolicy:
    """The one authoritative mapping from ``[wikipedia]`` text to policy."""
    return WikipediaConfig.from_dict(config).to_policy()


def apply_effective_policy(config: Optional[dict]) -> WikipediaConfig:
    """Install the effective config's policy on the process-wide gate.

    Called once at run start (CLI, GUI, pipeline). Because the shared gate is
    what every ``get_global_gate()`` call site already uses, this is what makes
    the GUI settings reach the real request path — no call site needs its own
    value, and there is no second hard-coded source to drift.

    Returns the sanitised config so the caller can log exactly what was
    applied rather than what was stored.
    """
    cfg = WikipediaConfig.from_dict(config)
    get_global_gate(cfg.to_policy())
    return cfg


def apply_policy_to_gate(gate: WikipediaGate,
                         config: Optional[dict]) -> WikipediaConfig:
    """Apply the effective policy to a caller-supplied gate, in place.

    Mutates rather than replacing: replacing the gate object would give the new
    gate its own request/retry counters, so the caller that owns pacing state
    would observe none of the requests it is meant to be bounding.
    """
    cfg = WikipediaConfig.from_dict(config)
    gate.policy = cfg.to_policy()
    return cfg


def effective_settings_report(config: Optional[dict] = None,
                              *, gate: Optional[WikipediaGate] = None,
                              use_gate_policy: bool = False) -> dict:
    """Report the EFFECTIVE Wikipedia settings the runtime will use.

    ``use_gate_policy`` reads the values off a live gate instead of the stored
    config. That distinction matters for the diagnostics contract: the log must
    show what the gate is actually enforcing, not merely what the GUI text
    says. When no gate is available the stored effective config is reported,
    which is what the gate is constructed from.
    """
    cfg = WikipediaConfig.from_dict(config)
    if use_gate_policy and gate is not None:
        policy = gate.policy
        enabled = cfg.enabled
        use_cache = cfg.use_cache
        source = "gate"
    else:
        policy = cfg.to_policy()
        enabled = cfg.enabled
        use_cache = cfg.use_cache
        source = "config"

    report = {
        "wikipedia enabled": enabled,
        "request delay": f"{policy.min_interval_seconds:.1f} seconds",
        "retry on 429": policy.retries_enabled,
        "maximum retries": policy.max_retries,
        "honor Retry-After": policy.respect_retry_after,
        "maximum Retry-After wait": f"{policy.retry_after_cap_seconds:.0f} seconds",
        "cache policy": "reuse cached lookups" if use_cache else "always fetch fresh",
        "source": source,
    }
    if use_gate_policy and gate is not None:
        report["requests_made"] = gate.requests_made
        report["rate_limited_events"] = gate.rate_limited_events
    return report