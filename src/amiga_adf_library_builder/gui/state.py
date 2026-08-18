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

The CLI additionally passes ``--config`` as the provider-config file (which is
also where ``[playmatch]`` / ``[hasheous]`` live); the GUI passes the same file
path, or an explicit provider config path, to ``run_pipeline``.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..paths import PathConfig, PathConfigError, resolve_config


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

    # --- run-mode toggles -----------------------------------------------------
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

    # --- build vs export -------------------------------------------------------
    # The pipeline's ``export=`` flag is driven by the chosen run mode, not a
    # raw GUI checkbox, but the GUI records the intent here.
    run_mode: str = "build"  # "build" | "export"

    # --- provider config ------------------------------------------------------
    # Optional explicit provider-config TOML path (where [playmatch]/[hasheous]
    # live). When empty, the GUI's own config file is used (same as ``--config``
    # in the CLI). Secrets are NOT here.
    provider_config_path: str = ""

    # --- (GH-33) LaunchBox local folder mappings -------------------------------
    # GUI-only LOCAL mappings (no network): image/media roots with an explicit
    # LaunchBox asset type, plus manual-document roots (PDF/TXT). Each media
    # root is {"path": str, "asset_type": str}. Empty = no GUI mappings, which
    # keeps the pipeline behavior byte-for-byte identical to the CLI
    # (CLI<->GUI equivalence preserved).
    launchbox_media_roots: list[dict] = field(default_factory=list)
    launchbox_manual_roots: list[str] = field(default_factory=list)


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


# --- (GH-33) LaunchBox GUI mappings -> provider config -------------------------


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


def build_pipeline_kwargs(
    state: GuiState,
    cfg: PathConfig,
    *,
    config_path: Optional[str] = None,
    activity: Optional[Any] = None,
    cache_dir: Optional[os.PathLike] = None,
) -> dict:
    """Build the ``run_pipeline`` keyword arguments from GUI state (CLI-equivalent).

    The mapping mirrors ``cli.py`` ``build`` / ``export`` exactly:

      * online                 -> online
      * refresh_metadata       -> refresh_metadata
      * require_artwork        -> require_artwork        (export only, harmless on build)
      * export_gate_acknowledged -> upstream_task_closed
      * verify_only            -> verify_only            (export only)
      * export=                -> (state.run_mode == "export")
      * provider config paths  -> playmatch/hasheous/rtfm/local_media config paths

    Provider config paths: the GUI passes the same file the CLI would
    (``--config``), unless an explicit provider config path is set. The core
    resolves ``[playmatch]``/``[hasheous]``/``[rtfm]``/``[local_media]`` from it.

    (GH-33) The ``local_media_config_path`` is resolved through
    :func:`resolve_local_media_config_path`: when the GUI holds LaunchBox local
    mappings, a GUI-managed merged ``[local_media]`` config is written (into
    ``cache_dir`` when provided, else the system temp dir) and passed instead;
    with no GUI mappings the original provider-config path is used unchanged.

    ``activity`` (issue #21): optional live-log callback forwarded to the
    pipeline as a plain-language activity hook. Omitted (absent) when ``None``,
    so CLI<->GUI equivalence and non-GUI callers are unchanged.
    """
    provider_cfg = state.provider_config_path or config_path or None
    local_media_cfg = resolve_local_media_config_path(
        state, config_path=config_path, cache_dir=cache_dir
    )
    kwargs: dict[str, Any] = {
        "cfg": cfg,
        "online": bool(state.online),
        "refresh_metadata": bool(state.refresh_metadata),
        "require_artwork": bool(state.require_artwork),
        "upstream_task_closed": bool(state.export_gate_acknowledged),
        # (GH-24) Independent metadata selection, forwarded verbatim.
        "include_artwork": bool(state.include_artwork),
        "include_manuals_rtfm": bool(state.include_manuals_rtfm),
        "export": (state.run_mode == "export"),
        "verify_only": bool(state.verify_only),
        # (GH-33) GUI LaunchBox mappings take precedence for local media;
        # otherwise identical to the CLI's provider-config behavior.
        "local_media_config_path": local_media_cfg,
        "rtfm_config_path": provider_cfg,
        "playmatch_config_path": provider_cfg,
        "hasheous_config_path": provider_cfg,
    }
    if activity is not None:
        kwargs["activity"] = activity
    return kwargs
