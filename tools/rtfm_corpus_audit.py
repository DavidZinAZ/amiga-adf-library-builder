#!/usr/bin/env python3
"""Bounded real-corpus RTFM acceptance audit (GitHub issue #4).

Read-only against the operator's manual roots — this tool NEVER writes to or
mutates any source root. It builds every matched manual into a TEMPORARY
``rtfm`` output dir (default: a fresh temp dir, removed on exit) and reports the
issue #4 acceptance metrics:

  * prior oversize baseline  — number of matched manuals whose NATURAL render
    (pre-condensation) exceeded the configured review target (<= 16000 hard
    cap). This is exactly the set the OLD behavior would have routed to review.
  * how many of those now generate a valid <= target .rtfm (auto-condensed).
  * how many still route to review and why.
  * total RTFMs generated.
  * any new defects or matching issues discovered (e.g. a written file that
    somehow exceeded the cap, or a routed title with no provenance).

The tool reuses the EXACT production code path (``rtfm.build_rtfm_for_group`` /
``rtfm.discover_sources`` / ``rtfm.RtfmConfig``), so the numbers reflect real
runtime behavior.

Usage
-----
  # Audit the operator's private manual roots (configured in <config.toml>):
  python tools/rtfm_corpus_audit.py --config /path/to/rtfm.toml

  # Keep the temporary .rtfm output for inspection:
  python tools/rtfm_corpus_audit.py --config rtfm.toml --out-dir /tmp/rtfm-audit

  # Demonstrate function on a SYNTHETIC oversize corpus (no private data, safe
  # to run anywhere):
  python tools/rtfm_corpus_audit.py --demo

Exit code is 0 when the audit completes; 2 on usage/configuration error.

NOTE: this tool only reads manual roots and writes a throwaway output dir. It
never touches the operator's ADF corpus, Gotek export, or original sources.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

# Run as a module from the repo root so the canonical package is importable.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from amiga_adf_library_builder import rtfm as rc  # noqa: E402
from amiga_adf_library_builder.paths import load_rtfm_config  # noqa: E402


@dataclass
class _PseudoGroup:
    """Minimal release-group stand-in: a matched manual title.

    ``build_rtfm_for_group`` only needs ``.title``, ``.release_key`` (falls back
    to title), and ``.quarantine_reason`` (None here — we are not testing
    quarantine routing). ``_group_identity`` falls back to the title when
    ``release_basename`` is unavailable, which is what we want.
    """

    title: str
    release_key: str
    quarantine_reason: None = None


def _natural_bytes(title: str, matched, template: str, max_bytes: int) -> int:
    """Natural (pre-condensation) render size for the matched sources.

    Mirrors exactly the OLD route-on-oversize decision: the OLD code routed to
    review when ``len(natural_text) > max_bytes``. Returns that natural size.
    """
    sections, _prov, _order, _skipped = rc._compose_sections(matched, _PseudoGroup(title, title))
    order = rc._order_sections(template, None, sections)
    if not order:
        # No content -> old code routed "no manual content"; count as 0 bytes.
        return 0
    text = rc._assemble_rtfm(title, sections, order)
    return len(text.encode("utf-8"))


def _demo_corpus(root: Path) -> None:
    """Write a small SYNTHETIC oversize corpus under ``root`` for --demo."""
    (root / "manuals").mkdir(parents=True, exist_ok=True)
    (root / "instructions").mkdir(parents=True, exist_ok=True)
    (root / "cheats").mkdir(parents=True, exist_ok=True)

    # 1) Normal small manual -> always valid.
    (root / "instructions" / "Tiny Pilot.txt").write_text(
        "Left/Right: move\nFire: button 1\n", encoding="utf-8"
    )

    # 2) Oversize multi-section manual with huge low-value NOTES + ADDITIONAL
    #    REFERENCE -> NEW code should condense it to a valid .rtfm.
    filler = ("Story of the realm. " * 60 + "\n") * 60
    big = (
        "[CONTROLS]\nLeft/Right: move\nUp: jump\nFire: button 1\n\n"
        "[GETTING STARTED]\nInsert disk 1.\nPress Start.\n\n"
        "[HOW TO PLAY]\nReach the exit.\n\n"
        "[NOTES]\n" + filler +
        "[HINTS & CHEATS]\nLevel skip: KANGAROO\n\n"
        "[ADDITIONAL REFERENCE]\n" + filler
    )
    (root / "manuals" / "Oversize Saga.rtfm").write_text(big, encoding="utf-8")

    # 3) Single giant CONTROLS line -> irreducible; must still route to review.
    (root / "instructions" / "Unreducible Boss.txt").write_text(
        "Controls: " + ("Z" * 20000) + "\n", encoding="utf-8"
    )


def run_audit(cfg, out_dir: Path, *, verbose: bool = False) -> dict:
    """Run the bounded audit. Returns a metrics dict and prints a report."""
    out_dir.mkdir(parents=True, exist_ok=True)
    sources = rc.discover_sources(cfg)
    if not sources:
        print("[warn] no manual sources discovered under the configured roots.")

    # Group by source stem (the identity the matcher uses against a game title).
    stems: dict[str, list] = {}
    for s in sources:
        stems.setdefault(s.stem, []).append(s)

    total_generated = 0
    baseline_oversize = 0
    now_valid = 0
    still_review = 0
    still_review_reasons: list[str] = []
    defects: list[str] = []

    per_title: list[dict] = []

    for stem, matched in sorted(stems.items()):
        group = _PseudoGroup(stem, stem)
        natural = _natural_bytes(stem, matched, cfg.template, cfg.max_bytes)
        if natural > cfg.max_bytes:
            baseline_oversize += 1

        res = rc.build_rtfm_for_group(group, cfg=cfg, rtfm_dir=out_dir, sources=sources)

        was_baseline = natural > cfg.max_bytes
        condensed = any("condensation" in n for n in res.notes)
        rec = {
            "title": stem,
            "natural_bytes": natural,
            "baseline_oversize": was_baseline,
            "written": res.written,
            "routed_for_review": res.routed_for_review,
            "output_bytes": res.bytes,
            "condensed": condensed,
            "review_reason": res.review_reason,
        }
        per_title.append(rec)

        if res.written:
            total_generated += 1
            if was_baseline:
                now_valid += 1
            # Hard invariant: never emit > max_bytes.
            if res.bytes > cfg.max_bytes or res.bytes > rc.MAX_RTFM_BYTES:
                defects.append(
                    f"{stem}: wrote {res.bytes} bytes (> target {cfg.max_bytes})"
                )
        if res.routed_for_review:
            if was_baseline:
                still_review += 1
                still_review_reasons.append(f"{stem}: {res.review_reason}")
            if res.provenance_path is None or not res.provenance_path.exists():
                defects.append(f"{stem}: routed for review without provenance")

        if verbose:
            print(
                f"  {stem:24s} natural={natural:6d} "
                f"{'OVER' if was_baseline else 'ok  '} -> "
                f"{'written' if res.written else 'REVIEW'} "
                f"({res.bytes if res.written else '-'}b)"
                f"{' [condensed]' if condensed else ''}"
            )

    metrics = {
        "prior_oversize_baseline": baseline_oversize,
        "now_valid_le_target": now_valid,
        "still_routed_for_review": still_review,
        "total_rtfms_generated": total_generated,
        "matched_titles": len(stems),
        "defects": defects,
        "still_review_reasons": still_review_reasons,
        "per_title": per_title,
    }
    return metrics


def _print_report(m: dict) -> None:
    print("=" * 68)
    print("RTFM issue #4 — auto-condense acceptance audit")
    print("=" * 68)
    print(f"matched titles (distinct manuals) : {m['matched_titles']}")
    print(f"total RTFMs generated (valid)     : {m['total_rtfms_generated']}")
    print(f"prior oversize baseline (> target): {m['prior_oversize_baseline']}")
    print(f"  -> now auto-condensed to valid  : {m['now_valid_le_target']}")
    print(f"  -> still routed for review       : {m['still_routed_for_review']}")
    if m["still_review_reasons"]:
        print("-" * 68)
        print("still-routed reasons:")
        for r in m["still_review_reasons"]:
            print(f"  - {r}")
    if m["defects"]:
        print("-" * 68)
        print("DEFECTS (must be empty):")
        for d in m["defects"]:
            print(f"  ! {d}")
    else:
        print("defects                          : none")
    print("=" * 68)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="path to an [rtfm] config TOML (operator roots)")
    ap.add_argument("--out-dir", help="output dir for the temporary .rtfm files "
                                      "(default: fresh temp dir, removed on exit)")
    ap.add_argument("--demo", action="store_true",
                    help="run against a synthetic oversize corpus (no private data)")
    ap.add_argument("--verbose", action="store_true", help="per-title detail")
    args = ap.parse_args(argv)

    if not args.config and not args.demo:
        ap.error("provide --config <rtfm.toml> or --demo")
        return 2

    keep = args.out_dir is not None
    tmp_root = None
    if keep:
        out_dir = Path(args.out_dir).resolve()
    else:
        tmp_root = tempfile.mkdtemp(prefix="rtfm-audit-")
        out_dir = Path(tmp_root)

    try:
        if args.demo:
            demo = Path(tempfile.mkdtemp(prefix="rtfm-demo-"))
            _demo_corpus(demo)
            cfg = rc.RtfmConfig(
                enabled=True,
                template=rc.DEFAULT_TEMPLATE,
                manuals_roots=(str(demo / "manuals"),),
                instructions_roots=(str(demo / "instructions"),),
                cheats_roots=(str(demo / "cheats"),),
                max_bytes=rc.DEFAULT_RTFM_REVIEW_TARGET,
            )
            print(f"[demo] synthetic oversize corpus at {demo}")
        else:
            cfg_dict = load_rtfm_config(args.config)
            if not cfg_dict:
                print(f"[error] no [rtfm] table found in {args.config}", file=sys.stderr)
                return 2
            cfg = rc.RtfmConfig.from_dict(cfg_dict)
            if not cfg.enabled:
                print(f"[error] [rtfm] enabled=false in {args.config}", file=sys.stderr)
                return 2

        print(f"output dir (temporary): {out_dir}")
        print(f"review target (max_bytes): {cfg.max_bytes}  (hard cap {rc.MAX_RTFM_BYTES})")
        metrics = run_audit(cfg, out_dir, verbose=args.verbose)
        _print_report(metrics)

        # Machine-readable companion for the operator's records.
        report_path = out_dir / "rtfm-audit-report.json"
        report_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
        print(f"report JSON: {report_path}")
        return 0
    finally:
        if tmp_root is not None:
            import shutil
            shutil.rmtree(tmp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
