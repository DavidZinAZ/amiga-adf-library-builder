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

from dataclasses import dataclass
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

    # --- build vs export -------------------------------------------------------
    # The pipeline's ``export=`` flag is driven by the chosen run mode, not a
    # raw GUI checkbox, but the GUI records the intent here.
    run_mode: str = "build"  # "build" | "export"

    # --- provider config ------------------------------------------------------
    # Optional explicit provider-config TOML path (where [playmatch]/[hasheous]
    # live). When empty, the GUI's own config file is used (same as ``--config``
    # in the CLI). Secrets are NOT here.
    provider_config_path: str = ""


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


def build_pipeline_kwargs(
    state: GuiState,
    cfg: PathConfig,
    *,
    config_path: Optional[str] = None,
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
    """
    provider_cfg = state.provider_config_path or config_path or None
    kwargs: dict[str, Any] = {
        "cfg": cfg,
        "online": bool(state.online),
        "refresh_metadata": bool(state.refresh_metadata),
        "require_artwork": bool(state.require_artwork),
        "upstream_task_closed": bool(state.export_gate_acknowledged),
        "export": (state.run_mode == "export"),
        "verify_only": bool(state.verify_only),
        # The CLI passes the resolved config file as the provider-config source
        # for all optional providers; keep that exact behavior.
        "local_media_config_path": provider_cfg,
        "rtfm_config_path": provider_cfg,
        "playmatch_config_path": provider_cfg,
        "hasheous_config_path": provider_cfg,
    }
    return kwargs
