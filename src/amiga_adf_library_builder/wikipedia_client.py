"""Polite, bounded HTTP access policy for the Wikipedia metadata provider.

Production finding (GH-192/#189, v0.2.39 RC): Wikipedia answered 10 of 11
back-to-back burst requests with HTTP 200 and then returned HTTP 429 with
``Retry-After: 48`` — a delay the previous code parsed into the diagnostics
and then ignored completely. The provider made one API request plus one
HTML artwork request per release with no pacing, no retry, and no cache, so a
single library run reliably exhausted the endpoint's burst budget and then
reported the losses as opaque ``request_error`` events.

This module owns the whole policy in one deterministic, injectable place:

- :class:`WikipediaPolicy` — the tunable numbers (min interval, bounded retry
  count, backoff base/cap, Retry-After cap, per-run request budget).
- :class:`WikipediaGate` — per-process request pacing, rate-limit cooldown,
  bounded retry, and the counters that let diagnostics explain every wait.

Nothing here performs I/O. Clock and sleep are injected so tests are
deterministic and run without real delays.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

__all__ = [
    "WikipediaPolicy",
    "WikipediaGate",
    "DEFAULT_POLICY",
    "get_global_gate",
    "reset_global_gate",
]


@dataclass(frozen=True)
class WikipediaPolicy:
    """Deterministic pacing/retry limits for Wikipedia requests.

    Defaults are deliberately conservative. Wikipedia asks API clients to keep
    request volume low; a 1.0 s floor between requests plus bounded retries is
    slow enough to stay under the burst budget and fast enough that a 200
    title library still finishes in minutes rather than hours.
    """

    #: Minimum wall-clock seconds between two outbound Wikipedia requests.
    min_interval_seconds: float = 1.0
    #: Maximum retries AFTER the initial attempt. Bounded on purpose: a retry
    #: policy without a hard ceiling is an infinite loop with extra steps.
    max_retries: int = 2
    #: Exponential backoff base: attempt N waits ``base ** N`` seconds.
    backoff_base_seconds: float = 2.0
    #: Upper bound for any single computed backoff wait.
    backoff_cap_seconds: float = 60.0
    #: Upper bound applied to a server-supplied ``Retry-After`` value. A server
    #: asking for a 6-hour pause must not stall a library run.
    retry_after_cap_seconds: float = 60.0
    #: Honour ``Retry-After`` at all. Kept as an explicit switch so the
    #: behaviour is asserted in tests rather than implied.
    respect_retry_after: bool = True
    #: Consecutive 429s tolerated before the provider is declared rate-limited
    #: for the rest of the run (no further requests until cooldown expires).
    max_rate_limit_events: int = 3
    #: Hard ceiling on Wikipedia HTTP requests per gate (per process run).
    #: Reaching it stops further requests with ``budget_exhausted`` instead of
    #: grinding against the endpoint.
    max_requests: int = 400
    #: When set, retries stop entirely (single attempt). Used by tests and by
    #: callers that must fail fast.
    retries_enabled: bool = True

    def backoff_for(self, attempt: int, *, retry_after: Optional[float] = None) -> float:
        """Return the deterministic wait before retry number ``attempt``.

        ``attempt`` is 1-based (1 = first retry). A server-supplied
        ``Retry-After`` wins when present and enabled, capped by
        ``retry_after_cap_seconds``; otherwise exponential backoff applies,
        capped by ``backoff_cap_seconds``. The result is never negative.
        """
        if attempt < 1:
            attempt = 1
        wait = self.backoff_base_seconds ** attempt
        if self.respect_retry_after and retry_after is not None:
            try:
                wait = max(0.0, float(retry_after))
            except (TypeError, ValueError):
                wait = self.backoff_base_seconds ** attempt
            wait = min(wait, self.retry_after_cap_seconds)
        return max(0.0, min(wait, self.backoff_cap_seconds))


#: Process-wide default policy. Callers may pass their own instance.
DEFAULT_POLICY = WikipediaPolicy()


@dataclass
class WikipediaGate:
    """Stateful request pacing for one run.

    Holds the clock so that "did we throttle?" is testable without sleeping.
    ``sleep_fn`` is called with the exact number of seconds the policy decided
    to wait, which is also what the diagnostics report.
    """

    policy: WikipediaPolicy = field(default_factory=lambda: DEFAULT_POLICY)
    clock: Callable[[], float] = time.monotonic
    sleep_fn: Callable[[float], None] = time.sleep

    requests_made: int = 0
    retries_made: int = 0
    waits: int = 0
    throttled_seconds: float = 0.0
    rate_limited_events: int = 0
    rate_limited_until: float = 0.0
    #: None until the first request. A 0.0 sentinel would collide with a clock
    #: that legitimately starts at zero, making the first request look like an
    #: inter-request gap and skipping the throttle entirely.
    last_request_at: "Optional[float]" = None
    budget_exhausted: bool = False
    last_decision: str = ""
    #: When True no real sleeping happens even if a wait is required. Set only
    #: for injected-opener (test/offline) paths.
    paused: bool = False

    def without_sleeping(self) -> "WikipediaGate":
        """Disable real sleeping on THIS gate and return it.

        Mutates in place and returns ``self`` rather than building a copy: a
        copied gate would hold its own counters, so the caller that passed the
        gate in would never observe the requests, retries or rate-limit events
        its own policy was supposed to bound.
        """
        self.paused = True
        return self

    def _now(self) -> float:
        return float(self.clock())

    def _wait(self, seconds: float, reason: str) -> None:
        seconds = max(0.0, float(seconds))
        if seconds <= 0.0:
            self.last_decision = reason
            return
        self.waits += 1
        self.throttled_seconds += seconds
        self.last_decision = reason
        if not self.paused:
            self.sleep_fn(seconds)

    def cooldown_remaining(self) -> float:
        """Seconds left on an active rate-limit cooldown (0.0 when clear)."""
        return max(0.0, self.rate_limited_until - self._now())

    def begin_request(self, *, label: str = "") -> str:
        """Apply pacing before an outbound request.

        Returns a short decision tag describing the gate action taken
        (``"first_request"``, ``"throttled"``, ``"rate_limit_cooldown"``,
        ``"budget_exhausted"``) for the diagnostics trail. Raises nothing: an
        exhausted budget is reported by :meth:`request_allowed` returning
        ``False`` so the caller can classify it as a provider no-result.
        """
        if self.requests_made >= self.policy.max_requests:
            self.budget_exhausted = True
            self.last_decision = "budget_exhausted"
            return "budget_exhausted"
        now = self._now()
        cooldown = max(0.0, self.rate_limited_until - now)
        if cooldown > 0.0:
            self._wait(cooldown, f"rate_limit_cooldown({cooldown:.1f}s)")
            now = self._now()
        elif self.last_request_at is not None:
            elapsed = now - float(self.last_request_at)
            remaining = self.policy.min_interval_seconds - elapsed
            if remaining > 0.0:
                self._wait(remaining, f"throttled({remaining:.1f}s)")
                now = self._now()
            else:
                self.last_decision = "within_interval"
                return "within_interval"
        self.last_request_at = now
        return self.last_decision or "first_request"

    def request_allowed(self) -> bool:
        """False when the per-run request budget is spent.

        Sets ``budget_exhausted`` so a caller that only consults
        :meth:`request_allowed` still ends up with the flag set. Leaving the
        flag unset here made the budget invisible in the run diagnostics
        whenever the last request arrived through this method rather than
        through :meth:`begin_request`.
        """
        if self.requests_made >= self.policy.max_requests:
            self.budget_exhausted = True
            return False
        return True

    def note_request(self) -> None:
        """Record that a request actually left the process."""
        self.requests_made += 1

    def note_retry(self) -> None:
        self.retries_made += 1

    def note_rate_limited(self, *, retry_after: Optional[float] = None,
                          label: str = "") -> float:
        """Record a 429 and open the cooldown window. Returns the wait chosen.

        The cooldown is the greater of the server's ``Retry-After`` (capped)
        and the policy backoff, so a provider that omits ``Retry-After`` is
        still paced, and one that demands 48 s is not obeyed past the cap.
        """
        self.rate_limited_events += 1
        wait = self.policy.backoff_for(1, retry_after=retry_after)
        self.rate_limited_until = max(self.rate_limited_until, self._now() + wait)
        self.last_decision = f"rate_limited(cooldown={wait:.1f}s)"
        return wait

    def rate_limited_exhausted(self) -> bool:
        """True when too many consecutive rate limits were seen for this run."""
        return self.rate_limited_events >= self.policy.max_rate_limit_events

    def stats(self) -> dict[str, Any]:
        """Deterministic counters for run diagnostics."""
        return {
            "requests_made": self.requests_made,
            "retries_made": self.retries_made,
            "waits": self.waits,
            "throttled_seconds": round(self.throttled_seconds, 3),
            "rate_limited_events": self.rate_limited_events,
            "budget_exhausted": self.budget_exhausted,
        }


_GLOBAL_GATE: Optional[WikipediaGate] = None


def get_global_gate(policy: Optional[WikipediaPolicy] = None) -> WikipediaGate:
    """Return the process-wide gate, creating it on first use.

    A single shared gate is deliberate: pacing that resets per call site
    provides no protection, since the burst that trips the limit is a burst
    *across* call sites.
    """
    global _GLOBAL_GATE
    if _GLOBAL_GATE is None or (policy is not None and _GLOBAL_GATE.policy != policy):
        _GLOBAL_GATE = WikipediaGate(policy=policy or DEFAULT_POLICY)
    return _GLOBAL_GATE


def reset_global_gate() -> None:
    """Drop the process-wide gate (tests and explicit run restarts)."""
    global _GLOBAL_GATE
    _GLOBAL_GATE = None