"""GUI state <-> core config equivalence layer.

This module is the bridge that guarantees CLI<->GUI equivalence: given the same
operator inputs, :func:`build_path_config_from_gui_state` produces the SAME
:class:`~amiga_adf_library_builder.paths.PathConfig` and the same
``run_pipeline`` keyword arguments the CLI would produce from the analogous
flags.

The authoritative mapping (from ``cli.py``):

    --library-root            -> library_root
    --original-dir           -> original_dir        (else derived under root)
    --staging-dir            -> staging_dir         (else derived under root)
    --output-dir             -> output_dir          (else derived under root)
    --online                 -> online
    --refresh-metadata       -> refresh_metadata
    --require-artwork        -> require_artwork
    --verify-only            -> verify_only
    --export-gate-acknowledged -> upstream_task_closed
    --1g1r / --no-1g1r      -> one_per_game
    --operator-decisions     -> operator_decisions_path
    --selection-manifest     -> selection_manifest_path

The CLI additionally passes ``--config`` as the provider-config file (which is
also where ``[playmatch]`` / ``[hasheous]`` live); the GUI passes the same file
path, or an explicit provider config path, to ``run_pipeline``.
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from ..paths import PathConfig, PathConfigError, discover_default_config_path, resolve_config
from ..run_config import RunConfig


@dataclass
class GuiState:
    """Plain, serializable representation of the operator's GUI inputs.

    All path fields are strings (what the GUI widgets hold). ``None``/empty
    means "not set -> derive" -- exactly like the CLI's default behaviour, so
    the equivalence holds.
    """

    # --- path inputs (empty => derive) ---------------------------------------
    library_root: str = ""
    original_dir: str = ""
    staging_dir: str = ""
    output_dir: str = ""
    # The CLI also supports --quarantine-dir/--cache-dir; the GUI exposes the
    # common ones and leaves the rest derived (matches CLI defaults).
    quarantine_dir: str = ""
    cache_dir: str = ""

    # --- run-mode toggles ----------------------------------------------------
    online: bool = False
    refresh_metadata: bool = False
    require_artwork: bool = False
    verify_only: bool = False
    export_gate_acknowledged: bool = False
    # (GH-24) Independent selection of the two optional metadata types. Both
    # default ON, so a state that never sets them behaves exactly as before.
    # (Distinct from ``require_artwork``, which gates the export, not the
    # artwork search.)
    include_artwork: bool = True
    include_manuals_rtfm: bool = True

    # --- build vs export -----------------------------------------------------
    # The pipeline's ``export=`` flag is driven by the chosen run mode, not a
    # raw GUI checkbox, but the GUI records the intent here.
    run_mode: str = "build"  # "build" | "export"

    # --- (GH-102) Progressive JPEG conversion policy -------------------------
    convert_progressive_jpeg: str = "never"
    # Per-image progressive-conversion prompt callback. None disables prompting.
    progressive_prompt_callback: Optional[Callable[[str, str], bool]] = field(default=None)

    # --- provider config -----------------------------------------------------
    # Optional explicit provider-config TOML path (where [playmatch]/[hasheous]
    # live). When empty, the GUI's own config file is used (same as ``--config``
    # in the CLI). Secrets are NOT here.
    provider_config_path: str = ""

    # --- (GH-33) LaunchBox local folder mappings -----------------------------
    # GUI-only LOCAL mappings (no network): image/media roots with an explicit
    # LaunchBox asset type, plus manual-document roots (PDF/TXT). Each media
    # root is {"path": str, "asset_type": str}. Empty = no GUI mappings, which
    # keeps the pipeline behavior byte-for-byte identical to the CLI
    # (CLI<->GUI equivalence preserved).
    launchbox_media_roots: list[dict] = field(default_factory=list)
    launchbox_manual_roots: list[str] = field(default_factory=list)
    # --- (GH-173) RTFM control plane -------------------------------------
    # GUI-authored RTFM settings materialized to gui-rtfm.toml by
    # resolve_rtfm_config_path(). Mirrors Settings fields for the
    # same settings.
    rtfm_enabled: bool = False
    rtfm_template: str = "controls-first"
    rtfm_manual_roots: list[str] = field(default_factory=list)
    rtfm_instruction_roots: list[str] = field(default_factory=list)
    rtfm_cheat_roots: list[str] = field(default_factory=list)
    rtfm_max_bytes: int = 15360
    retrokit_manuals_enabled: bool = False

    # --- (GH-107 Slice 6) 1G1R selection controls -----------------------------
    one_per_game: bool = True
    operator_decisions_path: str = ""
    selection_manifest_path: str = ""
    # --- (GH-136) Curation state path -------------------------------------
    # Path to the library_state_<run_id>.json file produced by the build
    # run. When set, the pipeline applies accepted/rejected curation
    # decisions before 1G1R selection and export.
    library_state_path: str = ""


def build_path_config_from_gui_state(
    state: GuiState,
    *,
    config_path: Optional[str] = None,
) -> PathConfig:
    """Build a :class:`PathConfig` from GUI state (CLI-equivalent).

    ``config_path``, when provided, stands in for the CLI's ``--config`` file
    and is consulted for any un-set path role. ``library_root`` is required
    (mirrors the CLI's "No library configuration found" contract).

    The precedence produced here is identical to the CLI: explicit GUI field >
    discovered/config file > derived.
    """
    if not state.library_root or not state.library_root.strip():
        raise PathConfigError("library_root is required")

    cfg, _src = resolve_config(
        config=config_path or None,
        library_root=state.library_root or None,
        original_dir=state.original_dir or None,
        staging_dir=state.staging_dir or None,
        output_dir=state.output_dir or None,
        quarantine_dir=state.quarantine_dir or None,
        cache_dir=state.cache_dir or None,
    )
    return cfg


# --- (GH-33) LaunchBox GUI mappings -> provider config ---------------------


def _launchbox_mappings(state: GuiState) -> tuple[list[dict], list[str]]:
    """Return cleaned (media_roots, manual_roots) from GUI state."""
    media: list[dict] = []
    for entry in state.launchbox_media_roots or []:
        if isinstance(entry, dict):
            path = str(entry.get("path") or "").strip()
            if path:
                media.append(
                    {
                        "path": path,
                        "asset_type": str(entry.get("asset_type") or "").strip(),
                    }
                )
        elif isinstance(entry, str) and entry.strip():
            media.append({"path": entry.strip(), "asset_type": ""})
    manuals: list[str] = []
    for entry in state.launchbox_manual_roots or []:
        if isinstance(entry, str) and entry.strip():
            manuals.append(entry.strip())
        elif isinstance(entry, dict):
            path = str(entry.get("path") or "").strip()
            if path:
                manuals.append(path)
    return media, manuals


def resolve_local_media_config_path(
    state: GuiState,
    *,
    config_path: Optional[str] = None,
    cache_dir: Optional[os.PathLike] = None,
) -> Optional[str]:
    """Resolve the provider-config path the pipeline should use.

    (GH-33) When the GUI holds LaunchBox local mappings, the operator's
    provider config (if any) is merged with the GUI's ``[local_media]``
    ``media_roots`` / ``manual_roots`` into a DETERMINISTIC GUI-managed file
    (default: ``<cache_dir>/gui-local-media.toml``, where ``cache_dir`` is the
    app's own managed directory — never the read-only original corpus). The
    merged file is rewritten atomically on every run, so it always reflects
    the current GUI mappings. Without GUI mappings this returns the unchanged
    provider-config path, preserving CLI<->GUI equivalence exactly.

    No secrets, no network: the merged file holds local path mappings only.
    """
    media, manuals = _launchbox_mappings(state)
    if not media and not manuals:
        return state.provider_config_path or config_path or None
    base_cfg = state.provider_config_path or config_path or None
    if base_cfg:
        data: dict = {}
        try:
            import tomllib

            p = Path(base_cfg)
            if p.is_file():
                with open(p, "rb") as fh:
                    data = tomllib.load(fh)
        except Exception:
            # Unreadable/absent operator config: start from an empty document;
            # the operator's other provider tables are simply not carried.
            data = {}
    else:
        data = {}
    table = data.get("local_media")
    if not isinstance(table, dict):
        table = {}
    table = dict(table)
    table["enabled"] = True
    table["media_roots"] = media
    table["manual_roots"] = manuals
    data["local_media"] = table

    import tomli_w

    cache_dir = Path(cache_dir) if cache_dir else Path(tempfile.gettempdir())
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / "gui-local-media.toml"
    tmp = target.with_suffix(target.suffix + ".tmp")
    with open(tmp, "wb") as fh:
        tomli_w.dump(data, fh)
    tmp.replace(target)
    return str(target)


def _rtfm_settings_materialized(state: GuiState) -> bool:
    """Return True when any RTFM control-plane setting is non-default."""
    if state.rtfm_enabled or state.rtfm_manual_roots or state.rtfm_instruction_roots or state.rtfm_cheat_roots:
        return True
    if state.launchbox_manual_roots:
        return True
    if state.rtfm_template != "controls-first" or state.rtfm_max_bytes != 15360 or state.retrokit_manuals_enabled:
        return True
    return False


def resolve_rtfm_config_path(
    state: GuiState,
    *,
    cache_dir: Optional[os.PathLike] = None,
) -> Optional[str]:
    """Resolve the RTFM config path the pipeline should use.

    (GH-173) When the GUI holds RTFM control-plane settings, they are
    materialized into a DETERMINISTIC GUI-managed file
    (default: ``<cache_dir>/gui-rtfm.toml``). The managed file holds a
    ``[rtfm]`` table mirroring the operator's GUI choices, including
    roots, template, and RetroKit manual enablement. Without RTFM
    settings this returns ``None``, preserving the existing CLI-only
    ``[rtfm]`` semantics exactly.

    No secrets, no network: the managed file holds only operator
    preferences and local path roots.
    """
    if not _rtfm_settings_materialized(state):
        # No GUI-specific RTFM settings; fall back to the provider
        # config so CLI-only [rtfm] semantics are preserved exactly.
        return state.provider_config_path or None
    import tomli_w

    cache_dir = Path(cache_dir) if cache_dir else Path(tempfile.gettempdir())
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / "gui-rtfm.toml"
    data: dict = {}
    # Read existing provider config for [rtfm] defaults if present.
    base_cfg = state.provider_config_path
    if base_cfg:
        try:
            import tomllib
            p = Path(base_cfg)
            if p.is_file():
                with open(p, "rb") as fh:
                    data = tomllib.load(fh)
        except Exception:
            data = {}
    rtfm_table = data.get("rtfm")
    if not isinstance(rtfm_table, dict):
        rtfm_table = {}
    # When manual roots are present (LaunchBox or explicit RTFM roots),
    # RTFM must be enabled regardless of the standalone rtfm_enabled
    # toggle. The operator configured roots because they want RTFM —
    # the toggle is only for explicit opt-out when no roots are present.
    # This fixes GH-176: 1381 discovered manuals but RTFM disabled.
    _has_manual_roots = bool(
        state.launchbox_manual_roots or state.rtfm_manual_roots
        or state.rtfm_instruction_roots or state.rtfm_cheat_roots
    )
    rtfm_table["enabled"] = bool(state.rtfm_enabled) or _has_manual_roots
    rtfm_table["template"] = str(state.rtfm_template)
    # Merge GUI manual roots (launchbox + explicit RTFM roots)
    # into the single canonical store, mirroring resolve_local_media_config_path.
    all_manual_roots = list(state.launchbox_manual_roots) + list(state.rtfm_manual_roots)
    all_instruction_roots = list(state.rtfm_instruction_roots)
    all_cheat_roots = list(state.rtfm_cheat_roots)
    if all_manual_roots:
        rtfm_table["local"] = {"manuals": all_manual_roots}
    if all_instruction_roots:
        rtfm_table.setdefault("local", {})["instructions"] = all_instruction_roots
    if all_cheat_roots:
        rtfm_table.setdefault("local", {})["cheats"] = all_cheat_roots
    rtfm_table["max_bytes"] = int(state.rtfm_max_bytes)
    online_table = rtfm_table.get("online") or {}
    online_table["enabled"] = bool(state.retrokit_manuals_enabled)
    rtfm_table["online"] = online_table
    data["rtfm"] = rtfm_table

    tmp = target.with_suffix(target.suffix + ".tmp")
    with open(tmp, "wb") as fh:
        tomli_w.dump(data, fh)
    tmp.replace(target)
    return str(target)


def build_pipeline_kwargs(
    state: GuiState,
    cfg: PathConfig,
    *,
    config_path: Optional[str] = None,
    activity: Optional[Any] = None,
    cache_dir: Optional[os.PathLike] = None,
) -> tuple[RunConfig, dict[str, Any]]:
    """Build the ``run_pipeline`` arguments from GUI state (CLI-equivalent).

    Returns ``(run_config, extra_kwargs)`` where ``run_config`` is a
    :class:`RunConfig` and ``extra_kwargs`` contains ``cfg``, ``activity``,
    and any remaining non-RunConfig keyword arguments.
    """
    from ..artwork import ARTWORK_MAX_W, ARTWORK_MAX_H
    provider_cfg = state.provider_config_path or config_path or None
    if provider_cfg is None:
        # (GH-76) Fallback: when the operator has not explicitly selected a
        # provider-config path (e.g. the packaged Windows GUI launched by
        # double-clicking with no ``--config``/provider-config path), attempt
        # to discover a default config file via the standard precedence chain
        # (``AMIGA_ADF_CONFIG`` env > XDG > system). If no config file is
        # discoverable, ``provider_cfg`` stays ``None`` and the existing
        # "no config => skip RTFM" semantics are preserved unchanged.
        discovered = discover_default_config_path()
        if discovered is not None:
            provider_cfg = str(discovered)
    local_media_cfg = resolve_local_media_config_path(
        state, config_path=config_path, cache_dir=cache_dir
    )
    # (GH-173) Resolve the RTFM config path: when the GUI holds
    # RTFM control-plane settings they are materialized to
    # gui-rtfm.toml; otherwise fall back to the discovered default
    # config (preserving CLI-only [rtfm] semantics).
    rtfm_config_path = resolve_rtfm_config_path(
        state, cache_dir=cache_dir
    )
    if rtfm_config_path is None:
        discovered = discover_default_config_path()
        if discovered is not None:
            rtfm_config_path = str(discovered)
    # (GH-173) When RetroKit manuals are enabled via GUI, the
    # retrokit_config_path must point at the resolved RTFM config
    # so the [retrokit_manuals] table is accessible.
    rk_config_path = rtfm_config_path if state.retrokit_manuals_enabled else provider_cfg
    run_config = RunConfig(
        online=bool(state.online),
        refresh_metadata=bool(state.refresh_metadata),
        require_artwork=bool(state.require_artwork),
        upstream_task_closed=bool(state.export_gate_acknowledged),
        # (GH-24) Independent metadata selection, forwarded verbatim.
        include_artwork=bool(state.include_artwork),
        include_manuals_rtfm=bool(state.include_manuals_rtfm),
        export=(state.run_mode == "export"),
        verify_only=bool(state.verify_only),
        # (GH-107 Slice 6) 1G1R selection controls
        one_per_game=bool(state.one_per_game),
        operator_decisions_path=state.operator_decisions_path or None,
        selection_manifest_path=state.selection_manifest_path or None,
        # (GH-136) Curation state path — threaded to run_pipeline for
        # applying accepted/rejected curation decisions before 1G1R.
        library_state_path=state.library_state_path or None,
        # CLI-equivalent verified artwork dimensions for the exporter gate.
        verified_artwork_width=ARTWORK_MAX_W,
        verified_artwork_height=ARTWORK_MAX_H,
        # (GH-33) GUI LaunchBox mappings take precedence for local media;
        # otherwise identical to the CLI's provider-config behavior.
        local_media_config_path=local_media_cfg,
        # (GH-173) RTFM config path resolved from GUI settings;
        # falls back to discovered default config (CLI-only [rtfm]).
        rtfm_config_path=rtfm_config_path,
        playmatch_config_path=provider_cfg,
        hasheous_config_path=provider_cfg,
        retrokit_config_path=rk_config_path,
        # (GH-102) Progressive JPEG conversion policy.
        convert_progressive_jpeg=str(getattr(state, "convert_progressive_jpeg", "never")),
        # (GH-102) Per-image progressive-conversion prompt callback.
        progressive_prompt_callback=getattr(state, "progressive_prompt_callback", None),
    )
    extra: dict[str, Any] = {"cfg": cfg}
    if activity is not None:
        extra["activity"] = activity
    return run_config, extra