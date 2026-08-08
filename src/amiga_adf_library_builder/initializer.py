"""Managed-directory bootstrap for a resolved :class:`PathConfig`.

Replaces the old ``DEFAULT_DATA_ROOT`` / ``DEFAULT_SD_ROOT`` host-specific
defaults (portable path configuration). All paths now come from
``amiga_adf_library_builder.paths.PathConfig``; this module only creates the
managed subdirectories the builder needs under ``library_root``. ``original/`` is never created or written here as a
readable corpus input; callers treat it read-only.

manual-approval feature (manual-approvals) compatibility: this module does NOT create the
``config/manual-approvals`` directory itself; the approval workflow owns that
layout (see ``manual_approvals.write_approval_record``). It is derived from
``PathConfig.approvals_dir`` and remains byte-compatible with existing records.
"""

from __future__ import annotations

from pathlib import Path

from .paths import PathConfig

# Managed subdirectories under library_root (besides original/, which is the
# operator-supplied read-only corpus root and must already exist as input).
DATA_DIRECTORIES = (
    "catalog",
    "catalog/metadata-cache",
    "catalog/metadata-curated",
    "assets/artwork-original",
    "assets/artwork-processed",
    "assets/nfo",
    "review",
    "unknown",
    "rejected",
    "reports",
    "logs",
    "work/staging",
)


def ensure_managed_directories(cfg: PathConfig) -> list[Path]:
    """Create missing managed directories without replacing existing content.

    Operates on a resolved :class:`PathConfig`. ``original_dir`` is NOT created
    here; the operator provides the read-only corpus. If ``original_dir`` is
    missing the caller must decide how to obtain it (migration / copy), never
    this bootstrap.

    Returns the list of paths actually created. Raises ``NotADirectoryError`` if
    a managed path exists but is not a directory.
    """
    created: list[Path] = []

    for relative in DATA_DIRECTORIES:
        path = cfg.library_root / relative
        if not path.exists():
            path.mkdir(parents=True, exist_ok=False)
            created.append(path)
        elif not path.is_dir():
            raise NotADirectoryError(f"managed path is not a directory: {path}")


    return created
