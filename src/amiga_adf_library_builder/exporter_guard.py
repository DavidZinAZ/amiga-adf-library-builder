"""Gotek exporter guard (Phase 5) -- HARD-GATED.

This exporter MUST NOT run until the upstream Gotek requirements verification is
closed AND verified artwork dimensions are supplied. This module encodes that
gate explicitly so a future caller cannot accidentally implement/run the export
prematurely.

When the gate is open, this is where the single-level ADF/DSK tree with -N
naming would be written to a run-owned staging directory only (never the SD
card, and never ``work/staging``). The actual writer is intentionally NOT
implemented here yet; the gate function is the deliverable for this
Phase 5 boundary.

Portable paths: staging now resolves from ``PathConfig.staging_dir``
rather than a private ``work/staging`` literal under the data root. No hard-coded
host path (e.g. a shared SD-card mount) is referenced anywhere.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .paths import PathConfig


def export_gate_open(
    upstream_task_closed: bool,
    verified_artwork_width: Optional[int],
    verified_artwork_height: Optional[int],
) -> tuple[bool, str]:
    """Return (allowed, reason). Both conditions must hold.

    - upstream_task_closed: upstream Gotek requirements verification must be closed (operator-verified).
    - verified_artwork_width/height: the Gotek export design requires real dimensions.
    """
    if not upstream_task_closed:
        return False, (
            "Gotek export BLOCKED: upstream Gotek requirements verification "
            "is not closed. Export must not run before it lands."
        )
    if not verified_artwork_width or not verified_artwork_height:
        return False, (
            "Gotek export BLOCKED: verified artwork dimensions unresolved "
            "(see docs/upstream-gotek-requirements.md). Do not assume a size."
        )
    return True, "export gate open"


def export_staging_dir(cfg: PathConfig, run_id: str) -> Path:
    """Run-owned staging path: ``<staging_dir>/<run-id>/ADF|DSK``.

    Staging lives under the configured ``staging_dir`` (portable path configuration). It is never
    the shared SD-card root nor the private ``work/staging`` path.
    """
    base = Path(cfg.staging_dir) / run_id
    (base / "ADF").mkdir(parents=True, exist_ok=True)
    (base / "DSK").mkdir(parents=True, exist_ok=True)
    return base
