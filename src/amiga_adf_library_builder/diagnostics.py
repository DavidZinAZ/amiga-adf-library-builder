"""Provider-attempt diagnostics (GH-44).

P1 observability: when online artwork / manual retrieval returns zero assets,
the operator must be told, per provider, what was tried and why nothing came
back. Previously every enabled provider swallowed its transport failures
(timeout, HTTP 4xx/5xx, malformed response, missing API key) into a bare
``found=False`` and there was no run-level roll-up, so a provider outage was
indistinguishable from "genuinely not found" and the Diagnostics tab showed
only prose.

This module is PURE (no I/O, no provider imports) so it can be unit-tested in
isolation and reused by the pipeline and the GUI. It provides:

* :class:`ProviderAttempt` -- one structured record per (provider, release).
* :func:`classify_zero_result` -- maps an attempt to a zero-result reason
  taxonomy (deterministic, ordered).
* :func:`attempt_from_enrich_events` -- derives per-release attempts from the
  already-emitted :class:`enrich.EnrichEvent` records (the single source of
  truth per release), without touching provider modules.
* :func:`aggregate_provider_attempts` -- rolls per-release attempts up into one
  run-level summary per provider (attempts / matched / no_match / error /
  review) plus a run-level zero-asset reason taxonomy.
* :func:`render_provider_diagnostics` -- deterministic human-readable lines for
  the Diagnostics tab / per-run log.

Security: every free-text field is passed through
:func:`amiga_adf_library_builder.logging_utils.redact` at render time so a
provider error string can never leak a secret value into the UI or logs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .logging_utils import redact

# --- Zero-result reason taxonomy -------------------------------------------
# Ordered, deterministic. classify_zero_result returns the FIRST matching tag.
# The order matters: a transport error outranks a bare miss (the provider
# never really answered), a manual-review routing outranks both (the operator
# must act), and only then do the genuine "nothing to find" reasons apply.
REASON_TRANSPORT_ERROR = "transport_error"        # timeout / HTTP 4xx-5xx / malformed
REASON_REJECTED = "rejected_by_reviewer"          # downstream reviewer rejected the candidate
REASON_NEEDS_REVIEW = "needs_manual_review"       # ambiguous / conflicting / low confidence
REASON_NOT_FOUND = "not_found"                    # provider answered: nothing there
REASON_NO_LOOKUP_SIGNAL = "no_lookup_signal"      # e.g. no hash and no title to query
REASON_NO_ARTWORK_URL = "no_artwork_url"          # metadata present but no image URL
REASON_ARTWORK_DOWNLOAD_FAILED = "artwork_download_failed"
REASON_ARTWORK_INVALID_IMAGE = "artwork_INVALID_IMAGE".lower()
REASON_ARTWORK_RESIZE_FAILED = "artwork_resize_failed"
REASON_LOCAL_NO_MASTER = "local_no_master"        # offline: no cached/local master
REASON_SELECTION_DISABLED = "selection_disabled"  # operator turned the type off
REASON_OK = "ok"                                   # at least one asset attached


@dataclass
class ProviderAttempt:
    """One structured diagnostic record for one (provider, release) attempt.

    ``provider`` is the normalized provider id (e.g. ``playmatch``, ``igdb``,
    ``metadata-online``, ``artwork-online``). ``outcome`` is one of
    ``matched`` / ``no_match`` / ``error`` / ``review``. ``error`` is a
    sanitized, human-readable provider error (already bounded; redacted again
    at render time). ``detail`` carries the lookup method / provider id /
    confidence the provider reported. ``assets`` is the count of usable assets
    the attempt produced (0 on a miss/error). ``rejected`` marks a candidate
    the provider produced but a downstream reviewer rejected -- it is NOT a
    provider failure and classifies as ``rejected_by_reviewer``.
    """

    provider: str
    title: str = ""
    release_key: str = ""
    outcome: str = "no_match"          # matched | no_match | error | review
    matched: bool = False
    assets: int = 0
    match_method: str = ""
    provider_id: Optional[str] = None
    confidence: float = 0.0
    detail: str = ""
    error: Optional[str] = None
    rejected: bool = False             # downstream reviewer rejection, not a provider fault
    reason: str = ""                    # zero-result taxonomy tag ("" when matched)

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "title": self.title,
            "release_key": self.release_key,
            "outcome": self.outcome,
            "matched": bool(self.matched),
            "assets": int(self.assets),
            "match_method": self.match_method,
            "provider_id": self.provider_id,
            "confidence": float(self.confidence),
            "detail": self.detail,
            "error": self.error,
            "rejected": bool(self.rejected),
            "reason": self.reason,
        }


def classify_zero_result(attempt: ProviderAttempt) -> str:
    """Return the deterministic zero-result reason tag for ``attempt``.

    Pure and ordered: downstream reviewer rejection > transport error >
    manual review > no lookup signal > artwork pipeline failures > bare
    not-found. When the attempt matched with at least one asset the tag is
    :data:`REASON_OK`.
    """
    if attempt.matched and attempt.assets > 0:
        return REASON_OK
    if attempt.rejected:
        return REASON_REJECTED
    if (attempt.error or "").strip():
        return REASON_TRANSPORT_ERROR
    if attempt.outcome == "review":
        return REASON_NEEDS_REVIEW
    detail = (attempt.detail or "").lower()
    if "no lookup signal" in detail or "no hash" in detail or "no title" in detail:
        return REASON_NO_LOOKUP_SIGNAL
    if "no artwork url" in detail:
        return REASON_NO_ARTWORK_URL
    if "download" in detail and "fail" in detail:
        return REASON_ARTWORK_DOWNLOAD_FAILED
    if "invalid image" in detail:
        return REASON_ARTWORK_INVALID_IMAGE
    if "resize" in detail and "fail" in detail:
        return REASON_ARTWORK_RESIZE_FAILED
    if "no master" in detail or "no cached" in detail or "offline" in detail:
        return REASON_LOCAL_NO_MASTER
    if "selection disabled" in detail or "disabled by operator" in detail:
        return REASON_SELECTION_DISABLED
    return REASON_NOT_FOUND


def attempt_from_enrich_events(
    events,
    *,
    title: str = "",
    release_key: str = "",
) -> list[ProviderAttempt]:
    """Derive per-release :class:`ProviderAttempt` records from EnrichEvents.

    The events are the single source of truth per release (emitted by
    ``enrich_group``). We map the identity-provider categories
    (playmatch/hasheous/igdb/screenscraper/retroachievements + their
    ``*_MISS`` / ``*_REVIEW`` variants) and the online metadata/artwork
    pipeline onto provider attempts. Pure: ``events`` may be EnrichEvent
    objects or their ``.to_dict()`` form.
    """

    def _cat(e) -> str:
        return e["category"] if isinstance(e, dict) else e.category

    def _d(e, key, default=None):
        if isinstance(e, dict):
            return e.get(key, default)
        return getattr(e, key, default)

    # Identity providers: category prefix -> normalized provider id.
    _IDENTITY = (
        ("playmatch", "playmatch"),
        ("hasheous", "hasheous"),
        ("igdb", "igdb"),
        ("screenscraper", "screenscraper"),
        ("retroachievements", "retroachievements"),
    )

    attempts: list[ProviderAttempt] = []
    seen_identity: dict[str, ProviderAttempt] = {}
    # metadata-online / artwork-online are single-attempt providers (the online
    # lookup pipeline), so they are added at most once each.
    meta_attempt: Optional[ProviderAttempt] = None
    art_attempt: Optional[ProviderAttempt] = None

    for e in events:
        cat = _cat(e)
        detail = _d(e, "detail", "") or ""
        err = _d(e, "error", None)
        ok = _d(e, "ok", True)
        url = _d(e, "url", None)

        provider = None
        for prefix, pid in _IDENTITY:
            if cat == prefix or cat.startswith(prefix + "_"):
                provider = pid
                break

        if provider is not None:
            # success event (bare category, ok=True) vs miss/review variant.
            if cat == provider and ok:
                attempt = ProviderAttempt(
                    provider=provider,
                    title=title,
                    release_key=release_key,
                    outcome="matched",
                    matched=True,
                    assets=1,
                    detail=detail,
                )
                _parse_match_detail(detail, attempt)
            elif cat.endswith("_review") or cat == provider + "_review":
                attempt = ProviderAttempt(
                    provider=provider,
                    title=title,
                    release_key=release_key,
                    outcome="review",
                    matched=False,
                    detail=detail,
                    error=err,
                )
            else:  # *_miss
                attempt = ProviderAttempt(
                    provider=provider,
                    title=title,
                    release_key=release_key,
                    outcome="error" if err else "no_match",
                    matched=False,
                    detail=detail,
                    error=err,
                )
            # Keep the strongest signal per provider (matched > review > error
            # > no_match). Deterministic precedence.
            _merge_identity(seen_identity, provider, attempt)
            continue

        # Online metadata pipeline.
        if cat in ("metadata_lookup", "metadata_not_found",
                   "metadata_relevance_rejected", "metadata_relevance_review"):
            if cat == "metadata_lookup" and "result=hit" in detail:
                meta_attempt = ProviderAttempt(
                    provider="metadata-online",
                    title=title,
                    release_key=release_key,
                    outcome="matched",
                    matched=True,
                    assets=1,
                    detail=detail,
                    provider_id=(url or ""),
                )
            elif cat == "metadata_not_found":
                meta_attempt = ProviderAttempt(
                    provider="metadata-online",
                    title=title,
                    release_key=release_key,
                    outcome="error" if err else "no_match",
                    matched=False,
                    detail=detail,
                    error=err,
                )
            elif cat == "metadata_relevance_review":
                meta_attempt = ProviderAttempt(
                    provider="metadata-online",
                    title=title,
                    release_key=release_key,
                    outcome="review",
                    matched=False,
                    detail=detail,
                    error=err,
                )
            elif cat == "metadata_relevance_rejected":
                # A rejection is a downstream rejection of a candidate, not a
                # provider failure. It supersedes any earlier "hit" for this
                # release: the candidate was reviewed and refused, so nothing
                # was attached and the reason is rejected_by_reviewer.
                meta_attempt = ProviderAttempt(
                    provider="metadata-online",
                    title=title,
                    release_key=release_key,
                    outcome="no_match",
                    matched=False,
                    detail=detail,
                    error=err,
                    rejected=True,
                )
            continue

        # Online artwork pipeline.
        if cat in ("artwork_lookup", "artwork_generated",
                   "artwork_download_failed", "artwork_url_not_found",
                   "artwork_invalid_image", "artwork_resize_failed",
                   "artwork_skipped"):
            if cat == "artwork_generated":
                art_attempt = ProviderAttempt(
                    provider="artwork-online",
                    title=title,
                    release_key=release_key,
                    outcome="matched",
                    matched=True,
                    assets=1,
                    detail=detail,
                )
            elif cat == "artwork_download_failed":
                art_attempt = ProviderAttempt(
                    provider="artwork-online",
                    title=title,
                    release_key=release_key,
                    outcome="error",
                    matched=False,
                    detail=detail,
                    error=err,
                )
            elif cat == "artwork_url_not_found":
                art_attempt = ProviderAttempt(
                    provider="artwork-online",
                    title=title,
                    release_key=release_key,
                    outcome="no_match",
                    matched=False,
                    detail="no artwork url: " + detail,
                    error=err,
                )
            elif cat == "artwork_invalid_image":
                art_attempt = ProviderAttempt(
                    provider="artwork-online",
                    title=title,
                    release_key=release_key,
                    outcome="error",
                    matched=False,
                    detail="invalid image: " + detail,
                    error=err,
                )
            elif cat == "artwork_resize_failed":
                art_attempt = ProviderAttempt(
                    provider="artwork-online",
                    title=title,
                    release_key=release_key,
                    outcome="error",
                    matched=False,
                    detail="resize failed: " + detail,
                    error=err,
                )
            elif cat == "artwork_skipped":
                # Distinguish operator-disabled vs local-missing vs online-no-url.
                if "disabled by operator" in detail:
                    reason = REASON_SELECTION_DISABLED
                    detail = "selection disabled: " + detail
                elif "no artwork master" in detail:
                    reason = REASON_LOCAL_NO_MASTER
                    detail = "no master: " + detail
                else:
                    reason = REASON_NOT_FOUND
                    detail = detail
                art_attempt = ProviderAttempt(
                    provider="artwork-online",
                    title=title,
                    release_key=release_key,
                    outcome="no_match",
                    matched=False,
                    detail=detail,
                    reason=reason,
                )
            # artwork_lookup (in-progress) and cache_hit/refresh add nothing.

    for provider, attempt in seen_identity.items():
        attempts.append(attempt)
    if meta_attempt is not None:
        attempts.append(meta_attempt)
    if art_attempt is not None:
        attempts.append(art_attempt)

    # Assign zero-result reasons to any attempt still lacking one.
    for a in attempts:
        if not a.reason:
            a.reason = classify_zero_result(a)
    return attempts


def _parse_match_detail(detail: str, attempt: ProviderAttempt) -> None:
    """Populate match_method / provider_id / confidence from a success detail.

    The detail strings produced by ``enrich_group`` use a stable shape:
    ``resolved via <method> conf=<x> provider_id=<id>`` (identity providers)
    or ``result=hit provider=<id>`` (metadata). We parse the parts we can and
    leave the rest as-is; nothing here is load-bearing.
    """
    import re
    m = re.search(r"resolved via (\S+)", detail)
    if m:
        attempt.match_method = m.group(1)
    m = re.search(r"provider_id=(\S+)", detail)
    if m:
        attempt.provider_id = m.group(1)
    m = re.search(r"conf=([0-9.]+)", detail)
    if m:
        try:
            attempt.confidence = float(m.group(1))
        except ValueError:
            pass
    m = re.search(r"result=hit provider=(\S+)", detail)
    if m:
        attempt.provider_id = m.group(1)


def _merge_identity(
    seen: dict[str, ProviderAttempt],
    provider: str,
    attempt: ProviderAttempt,
) -> None:
    """Keep the strongest per-provider signal (matched > review > error > no_match)."""
    rank = {"matched": 3, "review": 2, "error": 1, "no_match": 0}
    existing = seen.get(provider)
    if existing is None or rank.get(attempt.outcome, 0) > rank.get(existing.outcome, 0):
        seen[provider] = attempt


@dataclass
class ProviderSummary:
    """Run-level roll-up for one provider."""

    provider: str
    enabled: bool = True
    attempts: int = 0
    matched: int = 0
    no_match: int = 0
    error: int = 0
    review: int = 0
    assets_total: int = 0
    reasons: dict = field(default_factory=dict)  # reason tag -> count
    error_samples: list = field(default_factory=list)  # bounded sanitized errors
    note: str = ""  # e.g. "disabled: missing API key"

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "enabled": bool(self.enabled),
            "attempts": int(self.attempts),
            "matched": int(self.matched),
            "no_match": int(self.no_match),
            "error": int(self.error),
            "review": int(self.review),
            "assets_total": int(self.assets_total),
            "reasons": dict(self.reasons),
            "error_samples": list(self.error_samples),
            "note": self.note,
        }


def aggregate_provider_attempts(
    attempts: list,
    *,
    providers: Optional[list] = None,
) -> dict:
    """Roll per-release attempts up into a run-level summary.

    ``attempts`` is a flat list of :class:`ProviderAttempt` (or dicts) across
    all releases. ``providers`` (optional) is the canonical ordered list of
    provider ids to report, each as a :class:`ProviderSummary` or a
    ``(provider, enabled, note)`` tuple; providers with no attempts still get a
    summary so the operator sees every enabled provider. Returns a dict:

        {
          "providers": [ProviderSummary, ...],
          "zero_asset_releases": {release_key: [reason, ...], ...},
          "reason_taxonomy": {tag: count, ...},   # run-level zero-asset reasons
          "totals": {"attempts": n, "matched": n, "error": n, "review": n,
                     "assets": n},
        }

    Pure and deterministic (iteration order preserved, no wall-clock).
    """
    # Normalize to ProviderAttempt.
    norm: list[ProviderAttempt] = []
    for a in attempts:
        if isinstance(a, ProviderAttempt):
            norm.append(a)
        elif isinstance(a, dict):
            norm.append(ProviderAttempt(**{k: a.get(k) for k in (
                "provider", "title", "release_key", "outcome", "matched",
                "assets", "match_method", "provider_id", "confidence",
                "detail", "error", "reason"
            ) if a.get(k) is not None or k in ("provider",)}))
        else:
            continue

    # Build provider order: explicit list first, then any extra providers seen.
    order: list[str] = []
    provider_notes: dict[str, tuple[bool, str]] = {}
    if providers:
        for p in providers:
            if isinstance(p, ProviderSummary):
                order.append(p.provider)
                provider_notes[p.provider] = (p.enabled, p.note)
            elif isinstance(p, (tuple, list)):
                pid = p[0]
                enabled = p[1] if len(p) > 1 else True
                note = p[2] if len(p) > 2 else ""
                order.append(pid)
                provider_notes[pid] = (bool(enabled), note)
            else:
                order.append(str(p))
    for a in norm:
        if a.provider not in order:
            order.append(a.provider)

    summaries: dict[str, ProviderSummary] = {}
    for pid in order:
        enabled, note = provider_notes.get(pid, (True, ""))
        summaries[pid] = ProviderSummary(
            provider=pid, enabled=enabled, note=note,
        )

    zero_asset: dict[str, list[str]] = {}
    taxonomy: dict[str, int] = {}
    tot = {"attempts": 0, "matched": 0, "error": 0, "review": 0, "assets": 0}

    for a in norm:
        s = summaries.get(a.provider)
        if s is None:  # unknown provider id; fold into a catch-all.
            s = summaries.setdefault(
                a.provider, ProviderSummary(provider=a.provider)
            )
        s.attempts += 1
        tot["attempts"] += 1
        if a.outcome == "matched":
            s.matched += 1
            s.assets_total += max(0, a.assets)
            tot["matched"] += 1
            tot["assets"] += max(0, a.assets)
        elif a.outcome == "error":
            s.error += 1
            tot["error"] += 1
        elif a.outcome == "review":
            s.review += 1
            tot["review"] += 1
        else:
            s.no_match += 1
        # Zero-result reason (only meaningful when nothing was attached).
        reason = a.reason or classify_zero_result(a)
        if reason != REASON_OK:
            s.reasons[reason] = s.reasons.get(reason, 0) + 1
            taxonomy[reason] = taxonomy.get(reason, 0) + 1
            if a.release_key and reason:
                zero_asset.setdefault(a.release_key, []).append(
                    f"{a.provider}:{reason}"
                )
            # Keep a bounded, sanitized sample of error text (first 5 per
            # provider) so the operator sees *why* without a flood.
            if a.outcome == "error" and a.error and len(s.error_samples) < 5:
                s.error_samples.append(_sanitize_error(a.error))

    return {
        "providers": [summaries[pid] for pid in order],
        "zero_asset_releases": zero_asset,
        "reason_taxonomy": taxonomy,
        "totals": tot,
    }


def _sanitize_error(err: str, max_len: int = 200) -> str:
    """Bound + redact a provider error string for safe display.

    Never raises; always returns a single-line, length-capped, redacted string.
    """
    try:
        text = " ".join(str(err).split())
    except Exception:
        text = "unknown error"
    if len(text) > max_len:
        text = text[: max_len - 3] + "..."
    try:
        return redact(text)
    except Exception:
        return text


def render_provider_diagnostics(agg: dict, *, max_releases: int = 10) -> list[str]:
    """Render a deterministic, human-readable Diagnostics block.

    Produces one line per provider (with its run-level outcome) and a bounded
    list of zero-asset releases with their reason taxonomy. Every free-text
    value is redacted. Deterministic: input order is preserved, no timestamps.
    """
    lines: list[str] = []
    lines.append("Provider diagnostics (run-level):")
    providers = agg.get("providers", [])
    if not providers:
        lines.append("  (no online providers attempted this run)")
    for s in providers:
        if not isinstance(s, ProviderSummary):
            s = ProviderSummary(**s) if isinstance(s, dict) else s
        if not s.enabled:
            note = _sanitize_error(s.note) if s.note else "disabled"
            lines.append(f"  - {s.provider}: DISABLED ({note})")
            continue
        bits = [
            f"attempts={s.attempts}",
            f"matched={s.matched}",
        ]
        if s.no_match:
            bits.append(f"no_match={s.no_match}")
        if s.error:
            bits.append(f"error={s.error}")
        if s.review:
            bits.append(f"review={s.review}")
        if s.assets_total:
            bits.append(f"assets={s.assets_total}")
        lines.append(f"  - {s.provider}: " + " ".join(bits))
        if s.error_samples:
            lines.append(f"      errors: {s.error_samples[0]}")

    tax = agg.get("reason_taxonomy", {})
    if tax:
        tax_str = ", ".join(f"{k}={v}" for k, v in sorted(tax.items()))
        lines.append(f"  zero-result reasons: {tax_str}")

    zero = agg.get("zero_asset_releases", {})
    if zero:
        lines.append(f"  releases with no assets ({len(zero)}):")
        for i, (key, reasons) in enumerate(zero.items()):
            if i >= max_releases:
                lines.append(f"    ... and {len(zero) - max_releases} more")
                break
            lines.append(f"    - {key}: {', '.join(reasons)}")
    else:
        lines.append("  no zero-asset releases: every release got at least one asset")
    return lines
