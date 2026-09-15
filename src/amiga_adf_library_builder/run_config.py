"""Typed behavioral config for pipeline.run_pipeline.

RunConfig consolidates the per-invocation toggles and config-path
parameters that previously lived as keyword-only parameters on
``run_pipeline``. It holds NO filesystem host paths — those live in
:class:`PathConfig`. Construction is the caller's responsibility
(CLI, tests, or future GUI).

Complex runtime controls (``cancel_event``, ``activity``,
``progressive_prompt_callback``) remain as direct keyword arguments to
``run_pipeline`` and are NOT part of RunConfig.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass
from threading import Event
from typing import Any, Callable, Optional

from .artwork import ARTWORK_MAX_H, ARTWORK_MAX_W
from .local_media import LocalMediaConfig


@dataclass(frozen=True)
class RunConfig:
    """One run's behavioral and configuration-boundary state.

    Host paths live in :class:`PathConfig`; this object carries only
    per-invocation toggles and provider/config-path references.
    """

    # --- runtime toggles -------------------------------------------------
    online: bool = False
    refresh_metadata: bool = False
    require_artwork: bool = False
    include_artwork: bool = True
    include_manuals_rtfm: bool = True
    upstream_task_closed: bool = False
    run_id: Optional[str] = None
    export: bool = False
    verify_only: bool = False
    one_per_game: bool = True
    verified_artwork_width: int = ARTWORK_MAX_W
    verified_artwork_height: int = ARTWORK_MAX_H

    # --- config-path references -----------------------------------------
    local_media_config_path: Optional[str] = None
    rtfm_config_path: Optional[str] = None
    playmatch_config_path: Optional[str] = None
    hasheous_config_path: Optional[str] = None
    igdb_config_path: Optional[str] = None
    screenscraper_config_path: Optional[str] = None
    retroachievements_config_path: Optional[str] = None
    retrokit_config_path: Optional[str] = None
    operator_decisions_path: Optional[str] = None
    selection_manifest_path: Optional[str] = None
    library_state_path: Optional[str] = None

    # --- GUI equivalence / progressive JPEG (GH-102) ---------------------
    convert_progressive_jpeg: str = "never"
    progressive_prompt_callback: Optional[Callable[[str, str], bool]] = None

    # -- compatibility shim (one release cycle) -------------------------
    @classmethod
    def from_legacy_kwargs(cls, **kwargs) -> "RunConfig":
        """Accept the old keyword-only parameter set, emit DeprecationWarning.

        This shim provides one release cycle for out-of-tree callers
        that previously passed the 10+ keyword-only arguments directly
        to :func:`run_pipeline`. After the shim is removed (0.3.0),
        all callers must construct :class:`RunConfig` directly.
        """
        warnings.warn(
            "run_pipeline(**kwargs) is deprecated; pass a RunConfig instead. "
            "See GH-149.",
            DeprecationWarning,
            stacklevel=2,
        )
        known = {
            "online", "refresh_metadata", "require_artwork",
            "upstream_task_closed", "run_id", "export", "verify_only",
            "verified_artwork_width", "verified_artwork_height",
            "local_media_config_path", "rtfm_config_path",
            "playmatch_config_path", "hasheous_config_path",
            "igdb_config_path", "screenscraper_config_path",
            "retroachievements_config_path", "retrokit_config_path",
            "operator_decisions_path", "selection_manifest_path",
            "library_state_path", "include_artwork",
            "include_manuals_rtfm", "one_per_game",
            "convert_progressive_jpeg", "progressive_prompt_callback",
            "local_media_config",
        }
        unknown = set(kwargs) - known
        if unknown:
            raise TypeError(f"unknown RunConfig kwargs: {sorted(unknown)}")
        # local_media_config_path maps to local_media_config=None in the
        # shim; PathConfig resolution should load it properly.
        kwargs.pop("local_media_config_path", None)
        kwargs.pop("local_media_config", None)
        # verified_artwork_width/height must be int (not Optional[int])
        # in RunConfig; convert if needed
        if "verified_artwork_width" in kwargs and kwargs["verified_artwork_width"] is None:
            kwargs["verified_artwork_width"] = ARTWORK_MAX_W
        if "verified_artwork_height" in kwargs and kwargs["verified_artwork_height"] is None:
            kwargs["verified_artwork_height"] = ARTWORK_MAX_H
        return cls(**kwargs)

    # -- invariants ------------------------------------------------------
    def __post_init__(self) -> None:
        """Validate RunConfig construction invariants."""
        if self.export and self.verify_only and self.run_id is None:
            raise ValueError(
                "run_id is required when both export=True and verify_only=True"
            )
