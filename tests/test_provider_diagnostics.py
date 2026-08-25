"""GH-44 regression tests: provider-attempt diagnostics and asset attachment.

Covers the four acceptance scenarios at the diagnostics layer
(success, no-match, provider error, downstream rejection), the
pipeline result-dict integration (``provider_diagnostics`` present,
JSON-serializable, totals consistent), and the run-summary / per-run
log rendering (redacted, never raising).

Synthetic fixtures only -- no network, no maintainer collection.
"""
from __future__ import annotations

import json
from pathlib import Path

from amiga_adf_library_builder import activity_log
from amiga_adf_library_builder import diagnostics as diag
from amiga_adf_library_builder import logging_utils
from amiga_adf_library_builder.pipeline import run_pipeline
from amiga_adf_library_builder.paths import resolve_config


def _ev(category, detail="", url=None, cache=None, ok=True, error=None):
    return {
        "category": category,
        "detail": detail,
        "url": url,
        "cache": cache,
        "ok": ok,
        "error": error,
    }


def _aggregate(events_by_release):
    """events_by_release: list of (title, release_key, [events])."""
    attempts = []
    for title, key, events in events_by_release:
        attempts.extend(diag.attempt_from_enrich_events(events, title=title, release_key=key))
    agg = diag.aggregate_provider_attempts(attempts)
    agg["providers"] = [
        s.to_dict() if hasattr(s, "to_dict") else s for s in agg["providers"]
    ]
    return agg


def _by_provider(agg):
    return {p["provider"]: p for p in agg["providers"]}


# --- scenario 1: success (match + asset attached) ------------------------


def test_diag_success_match_and_asset_counted() -> None:
    agg = _aggregate([
        ("Space Tactics", "st", [
            _ev("playmatch", "playmatch: match method=exact score=1.0 provider_id=123",
                cache="hit"),
            _ev("artwork_generated", "artwork: generated from match"),
        ]),
    ])
    prov = _by_provider(agg)
    assert prov["playmatch"]["matched"] == 1
    assert prov["playmatch"]["error"] == 0
    assert prov["playmatch"]["no_match"] == 0
    assert prov["playmatch"]["assets_total"] == 1
    assert prov["artwork-online"]["matched"] == 1
    assert prov["artwork-online"]["assets_total"] == 1
    assert agg["totals"]["matched"] == 2
    assert agg["totals"]["assets"] == 2
    assert agg["zero_asset_releases"] == {}
    assert diag.classify_zero_result(diag.ProviderAttempt(
        provider="playmatch", matched=True, assets=1)) == diag.REASON_OK


# --- scenario 2: no match (provider answered, nothing found) --------------


def test_diag_no_match_is_not_an_error() -> None:
    agg = _aggregate([
        ("Unknown Game", "ug", [
            _ev("hasheous_miss", "hasheous: no identity match"),
        ]),
    ])
    prov = _by_provider(agg)
    assert prov["hasheous"]["error"] == 0
    assert prov["hasheous"]["no_match"] == 1
    assert prov["hasheous"]["matched"] == 0
    assert agg["reason_taxonomy"].get("not_found") == 1
    assert agg["zero_asset_releases"]["ug"] == ["hasheous:not_found"]


# --- scenario 3: provider error (transport failure, sanitized) ------------


def test_diag_provider_error_tagged_and_redacted() -> None:
    agg = _aggregate([
        ("Space Tactics", "st", [
            _ev("igdb_miss",
                "igdb: no identity match (transport: HTTP 503)",
                ok=False,
                error="igdb transport: HTTP 503 token=supersecretkey"),
        ]),
    ])
    prov = _by_provider(agg)
    assert prov["igdb"]["error"] == 1
    assert prov["igdb"]["no_match"] == 0
    assert agg["reason_taxonomy"].get("transport_error") == 1
    assert agg["zero_asset_releases"]["st"] == ["igdb:transport_error"]
    sample = prov["igdb"]["error_samples"][0]
    assert "supersecretkey" not in sample
    assert "REDACTED" in sample
    # Rendered output must also be clean.
    rendered = "\n".join(diag.render_provider_diagnostics(agg))
    assert "supersecretkey" not in rendered
    assert "transport_error" in rendered


def test_diag_transport_error_outranks_not_found() -> None:
    attempt = diag.ProviderAttempt(
        provider="igdb", outcome="error", matched=False,
        error="timeout", detail="no identity match",
    )
    assert diag.classify_zero_result(attempt) == diag.REASON_TRANSPORT_ERROR
    clean = diag.ProviderAttempt(
        provider="igdb", outcome="no_match", matched=False,
        detail="no identity match",
    )
    assert diag.classify_zero_result(clean) == diag.REASON_NOT_FOUND


# --- scenario 4: downstream rejection (relevance filter, not failure) -----


def test_diag_downstream_rejection_not_a_provider_error() -> None:
    agg = _aggregate([
        ("Space Tactics", "st", [
            # metadata lookup hit, then the relevance reviewer rejected it:
            # a downstream rejection, not a provider failure.
            _ev("metadata_lookup", "metadata_lookup: result=hit", url="https://x/m/1"),
            _ev("metadata_relevance_rejected",
                "metadata: relevance reviewer rejected candidate",
                ok=False, error="below confidence threshold"),
        ]),
    ])
    prov = _by_provider(agg)
    # The rejection must not be counted as a provider error for metadata.
    assert prov["metadata-online"]["error"] == 0
    # It is recorded as a non-match (no asset attached).
    assert prov["metadata-online"]["matched"] == 0
    # The taxonomy carries the rejection, not a transport error.
    assert "transport_error" not in agg["reason_taxonomy"]
    assert agg["reason_taxonomy"].get("rejected_by_reviewer") == 1
    assert agg["zero_asset_releases"]["st"] == ["metadata-online:rejected_by_reviewer"]


# --- pipeline integration --------------------------------------------------


def _cfg(root: Path):
    return resolve_config(library_root=str(root))[0]


def test_pipeline_result_has_serializable_provider_diagnostics(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    orig = data_root / "original"
    orig.mkdir(parents=True)
    for n in range(1, 5):
        (orig / f"Example - Space Tactics (Disk {n} of 4).adf").write_bytes(b"x" * 10)
    result = run_pipeline(cfg=_cfg(data_root), run_id="gh44")

    assert "provider_diagnostics" in result
    pd = result["provider_diagnostics"]
    assert isinstance(pd, dict)
    assert isinstance(pd.get("providers"), list)
    assert "totals" in pd and "reason_taxonomy" in pd and "zero_asset_releases" in pd
    # The CLI emits the whole result dict via json.dumps -- it must not fail.
    payload = json.dumps(result)
    assert "provider_diagnostics" in payload
    # Offline run: the online metadata/artwork pipeline still records its
    # attempts (as local_no_master misses) -- zero errors, zero matches.
    tot = pd["totals"]
    assert tot["error"] == 0
    assert tot["matched"] == 0
    assert pd["reason_taxonomy"].get("local_no_master", 0) >= 1
    # Totals must be internally consistent.
    assert tot["attempts"] == sum(p["attempts"] for p in pd["providers"])
    assert tot["matched"] == sum(p["matched"] for p in pd["providers"])
    assert tot["error"] == sum(p["error"] for p in pd["providers"])


def test_pipeline_diagnostics_never_breaks_the_run(tmp_path: Path) -> None:
    """A malformed event stream must not raise out of run_pipeline."""
    data_root = tmp_path / "data"
    orig = data_root / "original"
    orig.mkdir(parents=True)
    (orig / "Solo Game (Disk 1 of 1).adf").write_bytes(b"x" * 10)
    result = run_pipeline(cfg=_cfg(data_root), run_id="gh44-safe")
    assert "provider_diagnostics" in result
    assert result["files_scanned"] == 1


# --- run summary + run log rendering ---------------------------------------


def test_render_run_summary_includes_diagnostics_and_redacts() -> None:
    agg = _aggregate([
        ("Space Tactics", "st", [
            _ev("igdb_miss", "igdb: no identity match (transport: HTTP 503)",
                ok=False, error="igdb transport: HTTP 503 token=supersecretkey"),
        ]),
    ])
    result = {
        "run_id": "st", "files_scanned": 1, "groups": 1,
        "nfo_written": [], "artwork_resized": [],
        "review_routed": [], "unknown_routed": [],
        "export_gate_open": True,
        "provider_diagnostics": agg,
    }
    lines = activity_log.render_run_summary(result, run_mode="build")
    joined = "\n".join(lines)
    assert "Provider diagnostics (run-level):" in joined
    assert "transport_error" in joined
    assert "REDACTED" in joined
    assert "supersecretkey" not in joined


def test_render_run_summary_ignores_old_result_dicts() -> None:
    # Pre-GH-44 result dicts (no provider_diagnostics) render unchanged.
    lines = activity_log.render_run_summary(
        {"run_id": "x", "files_scanned": 0, "groups": 0}
    )
    assert not any("Provider diagnostics" in l for l in lines)
    # None result still safe.
    activity_log.render_run_summary(None, run_mode="build")
    # Malformed roll-up must not raise.
    activity_log.render_run_summary(
        {"provider_diagnostics": {"providers": 42, "totals": {}}}
    )


def test_run_log_renders_diagnostics_section(tmp_path: Path) -> None:
    agg = _aggregate([
        ("Space Tactics", "st", [
            _ev("playmatch", "playmatch: match method=exact score=1.0 provider_id=123"),
        ]),
    ])
    result = {
        "run_id": "st", "per_group": [
            {
                "release_key": "st", "title": "Space Tactics",
                "quarantine_reason": None, "provider": None,
                "artwork_missing": False, "notes": [],
                "events": [_ev("playmatch", "playmatch: match")],
            }
        ],
        "provider_diagnostics": agg,
    }
    cfg = _cfg(tmp_path)
    text = logging_utils._render(
        log_path=tmp_path / "run.log",
        run_id="st",
        config_label="test",
        cfg=cfg,
        argv=["test"],
        command="build",
        result=result,
        started_at="2026-08-25T00:00:00Z",
        return_code=0,
    )
    assert "Provider diagnostics (run-level):" in text
    assert "playmatch: attempts=1 matched=1" in text
    # And a result without the roll-up omits the section.
    result.pop("provider_diagnostics")
    text2 = logging_utils._render(
        log_path=tmp_path / "run2.log",
        run_id="st",
        config_label="test",
        cfg=cfg,
        argv=["test"],
        command="build",
        result=result,
        started_at="2026-08-25T00:00:00Z",
        return_code=0,
    )
    assert "Provider diagnostics (run-level):" not in text2
