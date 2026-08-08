from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

from . import pipeline
from . import artwork as artwork_mod
from .enrich import VERIFIED_ARTWORK_WIDTH, VERIFIED_ARTWORK_HEIGHT
from .exporter_guard import export_gate_open
from .initializer import ensure_managed_directories
from .paths import PathConfig, PathConfigError, resolve_config, write_config_file


def _approved_filenames_for_keys(original_dir: Path, release_keys: list[str]) -> list[str]:
    """Derive the approved source-filename inventory from the original/ corpus.

    For each release key (base or full), every scanned file whose release-key
    base matches the requested key is included. Returns the sorted unique list
    of filenames. If the original/ directory is absent, returns [] (no hash
    binding will be performed by the caller).
    """
    original_dir = Path(original_dir)
    if not original_dir.is_dir():
        return []
    from .parser import parse_filename

    wanted = {str(k).split("|")[0].lower() for k in release_keys}
    seen: set[str] = set()
    out: list[str] = []
    for f in sorted(original_dir.iterdir()):
        if not f.is_file():
            continue
        try:
            rec = parse_filename(f.name)
        except Exception:
            continue
        base = rec.release_key.split("|")[0].lower()
        if base in wanted:
            if f.name not in seen:
                seen.add(f.name)
                out.append(f.name)
    return sorted(out)


# --- shared path-resolution flags --------------------------------------------
def _add_config_args(parser: argparse.ArgumentParser) -> None:
    """Add the portable path-resolution flags shared by every command."""
    parser.add_argument("--config", type=str, default=None,
                        help="explicit config file (TOML)")
    parser.add_argument("--library-root", type=str, default=None,
                        help="library root directory")
    parser.add_argument("--original-dir", type=str, default=None,
                        help="original (read-only) corpus directory")
    parser.add_argument("--staging-dir", type=str, default=None,
                        help="Gotek export staging directory")
    parser.add_argument("--output-dir", type=str, default=None,
                        help="generated output directory")
    parser.add_argument("--quarantine-dir", type=str, default=None,
                        help="quarantine (unknown/) directory")
    parser.add_argument("--approvals-dir", type=str, default=None,
                        help="manual-approval records directory")
    parser.add_argument("--reports-dir", type=str, default=None,
                        help="reports directory")
    parser.add_argument("--logs-dir", type=str, default=None,
                        help="logs directory")
    parser.add_argument("--cache-dir", type=str, default=None,
                        help="cache directory (default: XDG cache)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="amiga-adf-library-builder")
    commands = parser.add_subparsers(dest="command", required=True)

    # --- init ----------------------------------------------------------------
    init_cmd = commands.add_parser(
        "init",
        help="create a portable library configuration (interactive or --flags)",
    )
    init_cmd.add_argument("--config", type=str, default=None,
                         help="config file to write (default: interactive / XDG)")
    init_cmd.add_argument("--library-root", type=str, default=None,
                         help="library root directory")
    init_cmd.add_argument("--original-dir", type=str, default=None,
                         help="original (read-only) corpus directory")
    init_cmd.add_argument("--output-dir", type=str, default=None,
                         help="generated output directory")
    init_cmd.add_argument("--staging-dir", type=str, default=None,
                         help="Gotek export staging directory")
    init_cmd.add_argument("--quarantine-dir", type=str, default=None,
                         help="quarantine (unknown/) directory")
    init_cmd.add_argument("--approvals-dir", type=str, default=None,
                         help="manual-approval records directory")
    init_cmd.add_argument("--reports-dir", type=str, default=None,
                         help="reports directory")
    init_cmd.add_argument("--logs-dir", type=str, default=None,
                         help="logs directory")
    init_cmd.add_argument("--cache-dir", type=str, default=None,
                         help="cache directory")
    init_cmd.add_argument("--system", action="store_true",
                         help="write to the system-wide config (root required)")
    init_cmd.add_argument("--original-readonly", action="store_true",
                         help="confirm the original corpus is read-only")
    init_cmd.add_argument("--no-input", action="store_true",
                         help="fail rather than prompt (non-interactive mode)")

    # --- config --------------------------------------------------------------
    config_cmd = commands.add_parser("config", help="inspect/validate configuration")
    config_sub = config_cmd.add_subparsers(dest="config_command", required=True)
    show_cmd = config_sub.add_parser("show", help="print resolved configuration")
    _add_config_args(show_cmd)
    validate_cmd = config_sub.add_parser("validate", help="validate configuration safety")
    _add_config_args(validate_cmd)

    # --- normal commands (no path prompts) -----------------------------------
    scan_cmd = commands.add_parser("scan", help="dry-run intake scan")
    _add_config_args(scan_cmd)
    scan_cmd.add_argument("--online", action="store_true")

    build_cmd = commands.add_parser(
        "build", help="scan, parse, group, enrich (offline NFO), quarantine"
    )
    _add_config_args(build_cmd)
    build_cmd.add_argument("--online", action="store_true")
    build_cmd.add_argument("--refresh-metadata", action="store_true",
                          help="ignore metadata cache and query providers again")
    build_cmd.add_argument(
        "--export-gate-acknowledged", action="store_true",
        help="Operator confirms the Gotek export safety gate is satisfied.",
    )
    build_cmd.add_argument("--json", action="store_true", help="emit JSON result")

    export_cmd = commands.add_parser(
        "export",
        help="Phase 5 Gotek export to a run-owned staging tree (never the SD card)",
    )
    _add_config_args(export_cmd)
    export_cmd.add_argument("--online", action="store_true")
    export_cmd.add_argument("--refresh-metadata", action="store_true",
                          help="ignore metadata cache and query providers again")
    export_cmd.add_argument("--require-artwork", action="store_true",
                          help="preflight all accepted releases and refuse staging writes when any JPG is missing")
    export_cmd.add_argument(
        "--export-gate-acknowledged", action="store_true",
        help="Operator confirms the Gotek export safety gate is satisfied. "
             "Required to open the export gate.",
    )
    export_cmd.add_argument(
        "--verify-only", action="store_true",
        help="detect conflicts / report what would change without writing",
    )
    export_cmd.add_argument(
        "--run-id", type=str, default=None,
        help="explicit run identifier for the staging directory "
             "(default: a unique generated id). Use the same value across a "
             "write and its verify-only pass so conflicts are detected against "
             "the identical staging tree.",
    )
    export_cmd.add_argument("--json", action="store_true", help="emit JSON result")

    gate_cmd = commands.add_parser(
        "verify-export-gate",
        help="report whether Gotek export is permitted (no side effects)",
    )
    gate_cmd.add_argument("--export-gate-acknowledged", action="store_true")

    # --- manual-approval feature: manual-approval workflow ----------------------------------
    q_cmd = commands.add_parser(
        "list-quarantine",
        help="list groups currently quarantined for the special-only condition",
    )
    _add_config_args(q_cmd)

    approve_cmd = commands.add_parser(
        "approve",
        help="create a manual approval record for one or more release keys",
    )
    _add_config_args(approve_cmd)
    approve_cmd.add_argument(
        "--release-key", action="append", dest="release_keys", required=True,
        help="release key (base or full pipe-padded form); repeatable to merge",
    )
    approve_cmd.add_argument("--title", required=True, help="approved canonical title")
    approve_cmd.add_argument("--folder", required=True, help="approved Gotek folder")
    approve_cmd.add_argument(
        "--source-url", action="append", dest="source_urls", default=[],
        help="authoritative source URL; repeatable. Pairs with --role by position.",
    )
    approve_cmd.add_argument(
        "--role", action="append", dest="roles", default=[],
        help="role for the preceding --source-url (metadata|artwork|reference)",
    )
    approve_cmd.add_argument("--reason", default="", help="operator reason text")
    approve_cmd.add_argument(
        "--allow-incomplete", action="store_true",
        help="approve a special-only / incomplete set (special-only release key)",
    )
    approve_cmd.add_argument(
        "--merge-release-key", action="append", dest="merge_release_keys", default=[],
        help="additional release key covered by the SAME record (merge); "
             "repeatable. Equivalent to a second --release-key.",
    )

    list_cmd = commands.add_parser(
        "list-approvals", help="list all loaded approval records"
    )
    _add_config_args(list_cmd)

    inspect_cmd = commands.add_parser(
        "inspect-approval", help="show one approval record by id"
    )
    _add_config_args(inspect_cmd)
    inspect_cmd.add_argument("--approval-id", required=True)

    revoke_cmd = commands.add_parser(
        "revoke", help="revoke an approval record (history retained, never deleted)"
    )
    _add_config_args(revoke_cmd)
    revoke_cmd.add_argument("--approval-id", required=True)
    revoke_cmd.add_argument("--reason", required=True, help="revocation reason")
    revoke_cmd.add_argument("--by", default="operator", help="who revoked it")

    return parser


# --- path resolution ----------------------------------------------------------


def _resolve_cfg(args: argparse.Namespace) -> PathConfig:
    cfg, _src = _resolve_cfg_with_source(args)
    return cfg


def _resolve_cfg_with_source(args: argparse.Namespace):
    """Resolve config and also return the descriptive config-source label.

    The label (e.g. "explicit config file", "environment variables", or
    "defaults") is recorded in the per-run log so operators can trace which
    configuration a given run used.
    """
    try:
        cfg, src = resolve_config(
            config=getattr(args, "config", None),
            library_root=getattr(args, "library_root", None),
            original_dir=getattr(args, "original_dir", None),
            staging_dir=getattr(args, "staging_dir", None),
            output_dir=getattr(args, "output_dir", None),
            quarantine_dir=getattr(args, "quarantine_dir", None),
            approvals_dir=getattr(args, "approvals_dir", None),
            reports_dir=getattr(args, "reports_dir", None),
            logs_dir=getattr(args, "logs_dir", None),
            cache_dir=getattr(args, "cache_dir", None),
        )
    except PathConfigError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    return cfg, src


def _emit(
    result: dict,
    as_json: bool,
    *,
    cfg=None,
    config_label: Optional[str] = None,
    argv=None,
    command: Optional[str] = None,
    started_at: Optional[str] = None,
) -> int:
    if as_json:
        print(json.dumps(result, indent=2))
    else:
        print(f"run-id: {result['run_id']}")
        print(f"files scanned   : {result['files_scanned']}")
        print(f"records parsed  : {result['records_parsed']}")
        print(f"groups          : {result['groups']}")
        print(f"catalog +scan   : {result['catalog_new_scan']}")
        print(f"catalog +parse  : {result['catalog_new_parse']}")
        print(f"nfo written     : {len(result['nfo_written'])}")
        print(f"artwork resized : {len(result['artwork_resized'])}")
        print(f"artwork missing : {len(result.get('artwork_missing', []))}")
        print(f"review routed   : {len(result['review_routed'])}")
        print(f"unknown routed  : {len(result['unknown_routed'])}")
        print(f"export gate     : {'OPEN' if result['export_gate_open'] else 'BLOCKED'}")
        print(f"  reason        : {result['export_gate_reason']}")
        print(
            f"original preserved: {'YES' if result['original_preserved'] else 'NO ' + str(result['original_problems'])}"
        )
    export_result = result.get("export")
    rc = 4 if (export_result and (export_result.get("errors") or export_result.get("conflicts"))) else 0

    # structured logging: persist a per-run diagnostic log under logs_dir. This must never
    # break a run, so failures are swallowed (warning on stderr) by write_run_log.
    if cfg is not None:
        from .logging_utils import write_run_log

        _argv = list(sys.argv[1:]) if argv is None else list(argv)
        write_run_log(
            logs_dir=cfg.logs_dir,
            run_id=result.get("run_id") or "unknown",
            config_label=config_label or "(unknown)",
            cfg=cfg,
            argv=_argv,
            command=command,
            result=result,
            started_at=started_at or "",
            return_code=rc,
        )
    return rc


# --- init ---------------------------------------------------------------------


def _run_init(args: argparse.Namespace) -> int:
    from pathlib import Path as _P
    import os

    library_root = getattr(args, "library_root", None)
    if not library_root:
        if getattr(args, "no_input", False):
            print("error: --library-root is required in non-interactive mode", file=sys.stderr)
            return 2
        library_root = input("Library root directory: ").strip()
        if not library_root:
            print("error: library root is required", file=sys.stderr)
            return 2

    no_input = getattr(args, "no_input", False)

    # Track which role dirs the operator explicitly set (flags or interactive).
    explicit_keys: set[str] = set()
    overrides = {
        "original_dir": getattr(args, "original_dir", None),
        "output_dir": getattr(args, "output_dir", None),
        "staging_dir": getattr(args, "staging_dir", None),
        "quarantine_dir": getattr(args, "quarantine_dir", None),
        "approvals_dir": getattr(args, "approvals_dir", None),
        "reports_dir": getattr(args, "reports_dir", None),
        "logs_dir": getattr(args, "logs_dir", None),
        "cache_dir": getattr(args, "cache_dir", None),
    }
    for k, v in list(overrides.items()):
        if v:
            explicit_keys.add(k)

    if not no_input:
        for human, key in (
            ("Original (read-only) directory", "original_dir"),
            ("Generated output directory", "output_dir"),
            ("Gotek export staging directory", "staging_dir"),
        ):
            cur = overrides.get(key)
            prompt = f"{human} [{('derived under library root' if not cur else cur)}]: "
            val = input(prompt).strip()
            if val:
                overrides[key] = val
                explicit_keys.add(key)
        confirm_ro = input("Confirm original corpus is read-only [y/N]: ").strip().lower()
        original_readonly = confirm_ro == "y"
    else:
        original_readonly = bool(getattr(args, "original_readonly", False))

    # Determine the target config path.
    if getattr(args, "config", None):
        out_path = _P(args.config).expanduser()
    elif getattr(args, "system", False):
        out_path = _P("/etc/amiga-adf-library-builder/config.toml")
    else:
        xdg_home = os.environ.get("XDG_CONFIG_HOME")
        base = _P(xdg_home) if xdg_home else _P.home() / ".config"
        out_path = base / "amiga-adf-library-builder" / "config.toml"

    # Validate the resulting config before writing it. We resolve the prospective
    # overrides WITHOUT referencing the (not-yet-written) config file, so the
    # loader never tries to read a nonexistent path.
    try:
        cfg, _src = resolve_config(
            config=None,
            library_root=library_root,
            **{k: v for k, v in overrides.items() if v},
        )
    except PathConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not original_readonly:
        print(
            "WARNING: original corpus will be treated read-only by the application "
            "regardless; confirm it is not mutated by other processes.",
            file=sys.stderr,
        )

    # Only pin role dirs the operator explicitly set; leave the rest derived.
    written = {k: str(getattr(cfg, k)) for k in explicit_keys}
    write_config_file(
        out_path,
        library_root=str(cfg.library_root),
        **written,
    )
    print(f"wrote config: {out_path}")
    print("resolved paths:")
    for k, v in cfg.as_dict().items():
        marker = " (explicit)" if k in explicit_keys else " (derived)"
        print(f"  {k}: {v}{marker}")
    return 0


def _run_config_show(args: argparse.Namespace) -> int:
    from .paths import _xdg_cache_path

    cfg = _resolve_cfg(args)
    print("config source   : (resolved)")
    print(f"library_root    : {cfg.library_root}")
    print(f"original_dir    : {cfg.original_dir}")
    print(f"staging_dir     : {cfg.staging_dir}")
    print(f"output_dir      : {cfg.output_dir}")
    print(f"quarantine_dir  : {cfg.quarantine_dir}")
    print(f"approvals_dir   : {cfg.approvals_dir}")
    print(f"reports_dir     : {cfg.reports_dir}")
    print(f"logs_dir        : {cfg.logs_dir}")
    print(f"cache_dir       : {cfg.cache_dir}")
    # original_dir writability probe (read-only by contract).
    writable = os_access_writable(cfg.original_dir)
    print(f"original_writable: {writable}")
    # Overlap / containment warnings.
    for name in ("output_dir", "staging_dir", "cache_dir", "quarantine_dir"):
        p = getattr(cfg, name)
        if p.resolve() == cfg.original_dir.resolve():
            print(f"WARNING: {name} equals original_dir")
        if cfg.original_dir.resolve() in p.resolve().parents:
            print(f"WARNING: {name} is inside original_dir")
    return 0


def _run_config_validate(args: argparse.Namespace) -> int:
    try:
        cfg = _resolve_cfg(args)
    except SystemExit:
        return 1
    # _resolve_cfg already validates role relationships; deeper checks here.
    problems = []
    # original_dir should exist and be readable for a real library.
    if not cfg.original_dir.is_dir():
        problems.append(f"original_dir does not exist: {cfg.original_dir}")
    # writable dirs: report unwritable ones that we may need to create.
    for name in ("output_dir", "staging_dir", "reports_dir", "logs_dir", "cache_dir", "quarantine_dir"):
        p = getattr(cfg, name)
        if p.exists() and not os_access_writable(p):
            problems.append(f"{name} exists but is not writable: {p}")
    if problems:
        for prob in problems:
            print(f"INVALID: {prob}", file=sys.stderr)
        return 1
    print("configuration valid")
    return 0


def os_access_writable(p: Path) -> bool:
    import os

    p = Path(p)
    if p.exists():
        return os.access(p, os.W_OK)
    parent = p.parent
    while not parent.exists():
        parent = parent.parent
    return os.access(parent, os.W_OK)


# --- main ---------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "init":
        return _run_init(args)

    if args.command == "config":
        if args.config_command == "show":
            return _run_config_show(args)
        if args.config_command == "validate":
            return _run_config_validate(args)
        return 2

    if args.command == "scan":
        cfg = _resolve_cfg(args)
        ensure_managed_directories(cfg)
        if not cfg.original_dir.is_dir():
            print(f"error: original_dir does not exist: {cfg.original_dir}", file=sys.stderr)
            return 1
        print(f"dry-run source: {cfg.original_dir}")
        print(f"online permitted: {bool(args.online)}")
        print("bootstrap only: scanner implementation has not started")
        return 0

    if args.command == "build":
        cfg, _src = _resolve_cfg_with_source(args)
        ensure_managed_directories(cfg)
        if not cfg.original_dir.is_dir():
            print(f"error: original_dir does not exist: {cfg.original_dir}", file=sys.stderr)
            return 1
        started_at = datetime.now(timezone.utc).isoformat()
        result = pipeline.run_pipeline(
            cfg=cfg,
            online=bool(args.online),
            refresh_metadata=bool(args.refresh_metadata),
            upstream_task_closed=bool(args.export_gate_acknowledged),
            local_media_config_path=getattr(args, "config", None),
        )
        return _emit(
            result,
            as_json=bool(args.json),
            cfg=cfg,
            config_label=_src,
            command="build",
            started_at=started_at,
        )

    if args.command == "export":
        cfg, _src = _resolve_cfg_with_source(args)
        ensure_managed_directories(cfg)
        if not cfg.original_dir.is_dir():
            print(f"error: original_dir does not exist: {cfg.original_dir}", file=sys.stderr)
            return 1
        started_at = datetime.now(timezone.utc).isoformat()
        try:
            result = pipeline.run_pipeline(
                cfg=cfg,
                online=bool(args.online),
                refresh_metadata=bool(args.refresh_metadata),
                require_artwork=bool(args.require_artwork),
                upstream_task_closed=bool(args.export_gate_acknowledged),
                export=True,
                verify_only=bool(args.verify_only),
                run_id=args.run_id,
                verified_artwork_width=artwork_mod.ARTWORK_MAX_W,
                verified_artwork_height=artwork_mod.ARTWORK_MAX_H,
                local_media_config_path=getattr(args, "config", None),
            )
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        return _emit(
            result,
            as_json=bool(args.json),
            cfg=cfg,
            config_label=_src,
            command="export",
            started_at=started_at,
        )

    if args.command == "verify-export-gate":
        open_, reason = export_gate_open(
            bool(getattr(args, "export_gate_acknowledged", False)),
            VERIFIED_ARTWORK_WIDTH,
            VERIFIED_ARTWORK_HEIGHT,
        )
        print(f"export gate: {'OPEN' if open_ else 'BLOCKED'}")
        print(f"reason     : {reason}")
        print(
            f"verified artwork dims: {VERIFIED_ARTWORK_WIDTH}x{VERIFIED_ARTWORK_HEIGHT}"
        )
        return 0 if open_ else 3

    # --- manual-approval feature manual-approval workflow ----------------------------------
    if args.command == "list-quarantine":
        from . import scanner as _scanner, grouper as _grouper
        from .parser import parse_filename
        from .manual_approvals import load_approvals

        cfg = _resolve_cfg(args)
        ensure_managed_directories(cfg)
        if not cfg.original_dir.is_dir():
            print(f"error: original_dir does not exist: {cfg.original_dir}", file=sys.stderr)
            return 1
        scans = _scanner.scan_intake(cfg.original_dir)
        groups = _grouper.group_records([parse_filename(s.filename) for s in scans])
        approvals = load_approvals(cfg.library_root)
        shown = 0
        for g in groups:
            if g.quarantine_reason is None:
                continue
            covered = bool(
                approvals.get(g.release_key)
                or approvals.get(g.release_key.split("|")[0].lower())
            )
            shown += 1
            print(f"- {g.release_key}  [{g.title}]")
            print(f"    reason: {g.quarantine_reason}")
            print(f"    approved: {'yes' if covered else 'no'}")
        if shown == 0:
            print("no quarantined groups")
        return 0

    if args.command == "approve":
        from . import manual_approvals as ma

        cfg = _resolve_cfg(args)
        ensure_managed_directories(cfg)
        # Pair URLs with roles by position (repeatable, ordered).
        urls = list(args.source_urls)
        roles = list(args.roles)
        source_urls = []
        for i, url in enumerate(urls):
            role = roles[i] if i < len(roles) else "reference"
            source_urls.append({"url": url, "role": role})
        release_keys = list(args.release_keys) + list(args.merge_release_keys)
        # Default source filenames derived from the original/ corpus for the keys.
        approved_filenames = _approved_filenames_for_keys(cfg.original_dir, release_keys)
        try:
            rec = ma.write_approval_record(
                config_dir=cfg.approvals_dir.parent,
                release_keys=release_keys,
                canonical_title=args.title,
                approved_folder=args.folder,
                approved_source_filenames=approved_filenames,
                original_dir=cfg.original_dir if approved_filenames else None,
                source_urls=source_urls,
                operator_reason=args.reason,
                incomplete_set_override=bool(args.allow_incomplete),
            )
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"approved: {rec.approval_id}")
        print(f"  release_keys: {', '.join(rec.release_keys)}")
        print(f"  title       : {rec.canonical_title}")
        print(f"  folder      : {rec.approved_folder}")
        if approved_filenames:
            print(f"  source files: {', '.join(approved_filenames)}")
            print(f"  sha256 bound: yes")
        if source_urls:
            for su in source_urls:
                print(f"  source url  : ({su['role']}) {su['url']}")
        return 0

    if args.command == "list-approvals":
        from . import manual_approvals as ma

        cfg = _resolve_cfg(args)
        ensure_managed_directories(cfg)
        loaded = ma.load_approval_records(cfg.library_root)
        if not loaded.records:
            print("no approval records")
            return 0
        for rec in loaded.records:
            print(
                f"- {rec.approval_id}  status={rec.status}  keys={', '.join(rec.release_keys)}"
            )
            print(f"    title : {rec.canonical_title}")
            print(f"    folder: {rec.approved_folder}")
            if rec.superseded_by:
                print(f"    superseded_by: {rec.superseded_by}")
        if loaded.invalid_url_records:
            print("\nWARNING: records with disallowed URLs (skipped, not applied):")
            for rec, url, reason in loaded.invalid_url_records:
                print(f"  - {rec.approval_id}: {url} ({reason})")
        return 0

    if args.command == "inspect-approval":
        from . import manual_approvals as ma

        cfg = _resolve_cfg(args)
        ensure_managed_directories(cfg)
        loaded = ma.load_approval_records(cfg.library_root)
        rec = next(
            (r for r in loaded.records if r.approval_id == args.approval_id), None
        )
        if rec is None:
            print(f"error: no approval record with id {args.approval_id}", file=sys.stderr)
            return 2
        print(json.dumps(rec.to_dict(), indent=2, ensure_ascii=False))
        return 0

    if args.command == "revoke":
        from . import manual_approvals as ma

        cfg = _resolve_cfg(args)
        ensure_managed_directories(cfg)
        rec = ma.revoke_approval(
            config_dir=cfg.approvals_dir.parent,
            approval_id=args.approval_id,
            reason=args.reason,
            by=args.by,
        )
        if rec is None:
            print(f"error: no approval record with id {args.approval_id}", file=sys.stderr)
            return 2
        print(f"revoked: {rec.approval_id} (status={rec.status})")
        print(f"  reason: {rec.revocation_reason}")
        print("  history retained; file not deleted.")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
