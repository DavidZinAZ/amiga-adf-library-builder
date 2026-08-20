"""Portable, centralized path configuration (portable path configuration).

A single :class:`PathConfig` resolves, normalizes (``Path.resolve()`` with
symlink handling), and validates every filesystem location the builder uses.
No module may infer the repository checkout location or reach into
host-specific paths (a hard-coded data mount, an OS user home such as
``/home/<user>/projects``, or a username).

Configuration discovery (highest precedence first):

  1. CLI option / explicit flag
  2. environment variable (``AMIGA_ADF_*``)
  3. explicit ``--config`` file
  4. XDG per-user config (``~/.config/amiga-adf-library-builder/config.toml``)
  5. optional system-wide config (``/etc/amiga-adf-library-builder/config.toml``)
  6. safe built-in defaults (no library configured)

When only ``library_root`` is set, every role directory is derived beneath it
except ``cache_dir`` (which defaults to the XDG cache, ``~/.cache/...``).
Internal working directories ``catalog/``, ``assets/``, ``review/``,
``rejected/`` remain predictable children of ``library_root`` and are NOT part
of the public schema; they are exposed as convenience properties so callers do
not reconstruct them.

Path-role validation (enforced at resolution time):
  * ``original_dir`` is treated read-only by application logic.
  * ``output_dir`` != ``original_dir``; ``output_dir`` not inside ``original_dir``.
  * ``staging_dir`` not inside ``original_dir``.
  * ``cache_dir`` not inside ``original_dir``.
  * ``quarantine_dir`` not inside ``original_dir``.
  * symlink resolution must NOT let path-role restrictions be bypassed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - 3.11+ always has tomllib
    tomllib = None  # type: ignore


# --- Environment override names ----------------------------------------------
ENV_CONFIG = "AMIGA_ADF_CONFIG"
ENV_LIBRARY_ROOT = "AMIGA_ADF_LIBRARY_ROOT"
ENV_ORIGINAL_DIR = "AMIGA_ADF_ORIGINAL_DIR"
ENV_STAGING_DIR = "AMIGA_ADF_STAGING_DIR"
ENV_OUTPUT_DIR = "AMIGA_ADF_OUTPUT_DIR"
ENV_QUARANTINE_DIR = "AMIGA_ADF_QUARANTINE_DIR"
ENV_APPROVALS_DIR = "AMIGA_ADF_APPROVALS_DIR"
ENV_REPORTS_DIR = "AMIGA_ADF_REPORTS_DIR"
ENV_LOGS_DIR = "AMIGA_ADF_LOGS_DIR"
ENV_CACHE_DIR = "AMIGA_ADF_CACHE_DIR"

ENV_TO_FIELD = {
    ENV_LIBRARY_ROOT: "library_root",
    ENV_ORIGINAL_DIR: "original_dir",
    ENV_STAGING_DIR: "staging_dir",
    ENV_OUTPUT_DIR: "output_dir",
    ENV_QUARANTINE_DIR: "quarantine_dir",
    ENV_APPROVALS_DIR: "approvals_dir",
    ENV_REPORTS_DIR: "reports_dir",
    ENV_LOGS_DIR: "logs_dir",
    ENV_CACHE_DIR: "cache_dir",
}

CONFIG_KEYS = (
    "library_root",
    "original_dir",
    "staging_dir",
    "output_dir",
    "quarantine_dir",
    "approvals_dir",
    "reports_dir",
    "logs_dir",
    "cache_dir",
)

# XDG locations.
XDG_CONFIG_REL = Path("amiga-adf-library-builder") / "config.toml"
XDG_CACHE_REL = Path("amiga-adf-library-builder")
SYSTEM_CONFIG = Path("/etc/amiga-adf-library-builder/config.toml")


class PathConfigError(ValueError):
    """Raised when path configuration is missing, unsafe, or malformed."""


def _xdg_config_path() -> Path:
    env = os.environ.get("XDG_CONFIG_HOME")
    base = Path(env) if env else Path.home() / ".config"
    return base / XDG_CONFIG_REL


def _xdg_cache_path() -> Path:
    env = os.environ.get("XDG_CACHE_HOME")
    base = Path(env) if env else Path.home() / ".cache"
    return (base / XDG_CACHE_REL).resolve()


def _resolve(value: object) -> Path:
    """Resolve ``value`` to an absolute, symlink-resolved ``Path``.

    Accepts ``str`` or ``Path``. For not-yet-existing paths (whose parents may
    not exist), ``resolve()`` still normalizes; on any failure we fall back to
    an absolute path so callers always get a usable absolute ``Path``.
    """
    p = Path(value).expanduser()  # type: ignore[arg-type]
    try:
        return p.resolve()
    except (OSError, RuntimeError):
        return p.absolute()


def _opt_resolve(value: Optional[object]) -> Optional[Path]:
    if value is None:
        return None
    return _resolve(value)


def _derive(root: Path, *parts: str) -> Path:
    return (Path(root) / Path(*parts)).resolve()


def _load_toml(path: Path) -> dict:
    if tomllib is None:  # pragma: no cover
        raise PathConfigError(
            "TOML configuration requires Python 3.11+ (tomllib). "
            "Use explicit flags or environment variables instead."
        )
    with open(path, "rb") as fh:
        return tomllib.load(fh)


@dataclass(frozen=True)
class PathConfig:
    """Frozen, fully-resolved path configuration for one library.

    All paths are ``Path.resolve()`` values at construction time. Internal
    predictable working dirs (``catalog/``, ``assets/``, ``review/``,
    ``rejected/``) live under ``library_root`` and are exposed as properties;
    they are always derived and never configurable.
    """

    library_root: Path
    original_dir: Path
    staging_dir: Path
    output_dir: Path
    quarantine_dir: Path
    approvals_dir: Path
    reports_dir: Path
    logs_dir: Path
    cache_dir: Path

    # --- predictable internal working dirs (always under library_root) -------
    @property
    def catalog_dir(self) -> Path:
        return self.library_root / "catalog"

    @property
    def assets_dir(self) -> Path:
        return self.library_root / "assets"

    @property
    def review_dir(self) -> Path:
        return self.library_root / "review"

    @property
    def rejected_dir(self) -> Path:
        return self.library_root / "rejected"

    @property
    def metadata_cache_dir(self) -> Path:
        return self.catalog_dir / "metadata-cache"

    @property
    def curated_metadata_dir(self) -> Path:
        return self.catalog_dir / "metadata-curated"

    @property
    def artwork_original_dir(self) -> Path:
        return self.assets_dir / "artwork-original"

    @property
    def artwork_processed_dir(self) -> Path:
        return self.assets_dir / "artwork-processed"

    @property
    def nfo_dir(self) -> Path:
        return self.assets_dir / "nfo"

    @property
    def rtfm_dir(self) -> Path:
        # M1: RTFM assets live under assets/rtfm, a NEW derived dir kept OUTSIDE
        # the Gotek export tree (provenance + .rtfm are builder-side artifacts).
        return self.assets_dir / "rtfm"

    def as_dict(self) -> dict:
        return {
            "library_root": str(self.library_root),
            "original_dir": str(self.original_dir),
            "staging_dir": str(self.staging_dir),
            "output_dir": str(self.output_dir),
            "quarantine_dir": str(self.quarantine_dir),
            "approvals_dir": str(self.approvals_dir),
            "reports_dir": str(self.reports_dir),
            "logs_dir": str(self.logs_dir),
            "cache_dir": str(self.cache_dir),
        }


@dataclass
class ConfigSource:
    """Records where a resolved configuration came from (for `config show`)."""

    label: str
    config_path: Optional[Path] = None
    explicit_fields: set = field(default_factory=set)


def _validate(cfg: PathConfig) -> None:
    """Enforce path-role relationships on normalized, resolved paths.

    Symlinks are already resolved by ``_resolve`` / ``_derive``, so a symlinked
    ``output_dir`` that points inside ``original_dir`` is caught here.
    """
    original = cfg.original_dir.resolve()

    pairs = [
        ("output_dir", cfg.output_dir),
        ("staging_dir", cfg.staging_dir),
        ("cache_dir", cfg.cache_dir),
        ("quarantine_dir", cfg.quarantine_dir),
    ]
    for role_name, role_path in pairs:
        resolved = role_path.resolve()
        if resolved == original:
            raise PathConfigError(
                f"{role_name} must not equal original_dir ({original}); "
                f"the original corpus is read-only."
            )
        if original in resolved.parents:
            raise PathConfigError(
                f"{role_name} ({role_path}) must not be inside original_dir "
                f"({original}); the original corpus is read-only."
            )

    root = cfg.library_root.resolve()
    if original in root.parents or root == original:
        raise PathConfigError(
            f"library_root ({cfg.library_root}) must not be inside original_dir "
            f"({original})."
        )


def _build_config(
    *,
    library_root: Path,
    explicit: dict,
    explicit_fields: set,
    source_label: str,
    config_path: Optional[Path],
) -> tuple[PathConfig, ConfigSource]:
    """Construct a PathConfig from an anchor root and explicit dir overrides."""
    root = _resolve(library_root)

    original = _opt_resolve(explicit.get("original_dir")) or _derive(root, "original")
    staging = _opt_resolve(explicit.get("staging_dir")) or _derive(root, "work", "staging")
    output = _opt_resolve(explicit.get("output_dir")) or _derive(root, "output")
    quarantine = _opt_resolve(explicit.get("quarantine_dir")) or _derive(root, "unknown")
    approvals = (
        _opt_resolve(explicit.get("approvals_dir"))
        or _derive(root, "config", "manual-approvals")
    )
    reports = _opt_resolve(explicit.get("reports_dir")) or _derive(root, "reports")
    logs = _opt_resolve(explicit.get("logs_dir")) or _derive(root, "logs")
    cache = _opt_resolve(explicit.get("cache_dir")) or _xdg_cache_path()

    cfg = PathConfig(
        library_root=root,
        original_dir=original,
        staging_dir=staging,
        output_dir=output,
        quarantine_dir=quarantine,
        approvals_dir=approvals,
        reports_dir=reports,
        logs_dir=logs,
        cache_dir=cache,
    )
    src = ConfigSource(
        label=source_label, config_path=config_path, explicit_fields=set(explicit_fields)
    )
    _validate(cfg)
    return cfg, src


# --- Config-file loading ------------------------------------------------------


def _read_config_file(path: Path) -> dict:
    if not path.is_file():
        raise PathConfigError(f"config file not found: {path}")
    try:
        data = _load_toml(path)
    except FileNotFoundError as exc:
        raise PathConfigError(f"config file not found: {path}") from exc
    except Exception as exc:  # tomllib raises TOMLDecodeError on malformed input
        raise PathConfigError(f"malformed config file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise PathConfigError(f"malformed config file {path}: top-level not a table")
    return data


def _explicit_from_file(data: dict) -> dict:
    """Pull explicit dir values from a loaded TOML dict (only known keys)."""
    out: dict = {}
    for key in CONFIG_KEYS:
        val = data.get(key)
        if val is not None:
            if not isinstance(val, str):
                raise PathConfigError(
                    f"config key {key!r} must be a string path, got "
                    f"{type(val).__name__}"
                )
            out[key] = val
    return out


def load_local_media_config(config: Optional[str] = None) -> dict:
    """Return the ``[local_media]`` TOML table from the resolved config file.

    Preserves the existing :mod:`paths` precedence chain: an explicit
    ``config`` path wins, otherwise the discovered config file (env, XDG, then
    system) is used. Returns ``{}`` when no config file is found or no
    ``[local_media]`` table is present. The caller decides whether the section
    is enabled; this function only surfaces the raw mapping.

    ``local_media.py`` provides the typed :class:`LocalMediaConfig` and the
    provider; this helper is the paths-layer entry point so the precedence logic
    stays in one module.
    """
    path = _discover_config_file(config)
    if path is None:
        return {}
    data = _read_config_file(path)
    lm = data.get("local_media")
    if not isinstance(lm, dict):
        return {}
    return dict(lm)


def load_playmatch_config(config: Optional[str] = None) -> dict:
    """Return the ``[playmatch]`` TOML table from the resolved config file.

    Mirrors :func:`load_local_media_config` EXACTLY: same precedence chain
    (explicit ``config`` > env > XDG > system), returns ``{}`` when no config
    file is found or no ``[playmatch]`` table is present. ``playmatch.py``
    provides the typed :class:`~amiga_adf_library_builder.playmatch.PlaymatchConfig`
    and the provider; this helper is the paths-layer entry point so the
    precedence logic stays in one module.

    The Playmatch provider is OPTIONAL and DISABLED by default; ``{}`` (no
    table) means disabled, so nothing in the pipeline changes.
    """
    path = _discover_config_file(config)
    if path is None:
        return {}
    data = _read_config_file(path)
    pm = data.get("playmatch")
    if not isinstance(pm, dict):
        return {}
    return dict(pm)


def load_hasheous_config(config: Optional[str] = None) -> dict:
    """Return the ``[hasheous]`` TOML table from the resolved config file.

    Mirrors :func:`load_playmatch_config` EXACTLY: same precedence chain
    (explicit ``config`` > env > XDG > system), returns ``{}`` when no config
    file is found or no ``[hasheous]`` table is present. ``hasheous.py``
    provides the typed :class:`~amiga_adf_library_builder.hasheous.HasheousConfig`
    and the provider; this helper is the paths-layer entry point so the
    precedence logic stays in one module.

    The Hasheous provider is OPTIONAL and DISABLED by default; ``{}`` (no
    table) means disabled, so nothing in the pipeline changes. The live Hasheous
    lookup is platform-scoped and requires a self-hosted/compatible endpoint;
    the bundled provider is config-driven and disabled by default (see issue
    #12 governance).
    """
    path = _discover_config_file(config)
    if path is None:
        return {}
    data = _read_config_file(path)
    hs = data.get("hasheous")
    if not isinstance(hs, dict):
        return {}
    return dict(hs)


def load_rtfm_config(config: Optional[str] = None) -> dict:
    """Return the ``[rtfm]`` TOML table from the resolved config file.

    Mirrors :func:`load_local_media_config` exactly: same precedence chain
    (explicit ``config`` > env > XDG > system), returns ``{}`` when no config
    file is found or no ``[rtfm]`` table is present. ``rtfm.py`` provides the
    typed :class:`~amiga_adf_library_builder.rtfm.RtfmConfig`; this helper is the
    paths-layer entry point so precedence logic stays in one module.

    M1 note: the ``[rtfm.online]`` table is parsed and ignored (online providers
    are deferred; ``RtfmConfig.online_enabled`` is surfaced but never used by the
    deterministic build path).
    """
    path = _discover_config_file(config)
    if path is None:
        return {}
    data = _read_config_file(path)
    rc = data.get("rtfm")
    if not isinstance(rc, dict):
        return {}
    return dict(rc)


def load_igdb_config(config: Optional[str] = None) -> dict:
    """Return the ``[igdb]`` TOML table from the resolved config file.

    Mirrors :func:`load_playmatch_config` EXACTLY: same precedence chain
    (explicit ``config`` > env > XDG > system), returns ``{}`` when no config
    file is found or no ``[igdb]`` table is present. ``igdb.py`` provides the
    typed :class:`~amiga_adf_library_builder.igdb.IgdbConfig` and the
    provider; this helper is the paths-layer entry point so the precedence logic
    stays in one module.

    The IGDB provider is OPTIONAL and DISABLED by default; ``{}`` (no
    table) means disabled, so nothing in the pipeline changes. Credentials
    (client_id, client_secret) are NEVER in config files -- they come from
    the SecretStore / environment only.
    """
    path = _discover_config_file(config)
    if path is None:
        return {}
    data = _read_config_file(path)
    igdb = data.get("igdb")
    if not isinstance(igdb, dict):
        return {}
    return dict(igdb)


# --- Discovery + precedence ---------------------------------------------------


def _discover_config_file(explicit_config: Optional[str]) -> Optional[Path]:
    """Return the highest-precedence config *file* to read (or None)."""
    if explicit_config:
        return Path(explicit_config).expanduser().resolve()
    env_cfg = os.environ.get(ENV_CONFIG)
    if env_cfg:
        return Path(env_cfg).expanduser().resolve()
    xdg = _xdg_config_path()
    if xdg.is_file():
        return xdg
    if SYSTEM_CONFIG.is_file():
        return SYSTEM_CONFIG
    return None


def _env_explicit() -> dict:
    """Collect explicit dir values from environment variables (precedence 2)."""
    out: dict = {}
    for env_name, field_name in ENV_TO_FIELD.items():
        val = os.environ.get(env_name)
        if val:
            out[field_name] = val
    return out


def _gather_explicit(
    *,
    explicit_config: Optional[str],
    cli_overrides: dict,
) -> tuple[dict, set, str, Optional[Path]]:
    """Merge file-derived + env + CLI overrides into one explicit dict.

    Precedence (highest first): CLI override > env var > discovered config file
    (explicit/XDG/system) > defaults. Returns
    ``(explicit_dict, explicit_fields, source_label, loaded_config_path)``.
    """
    explicit: dict = {}
    explicit_fields: set = set()
    source_label = "defaults"
    loaded_config_path: Optional[Path] = None

    cfg_path = _discover_config_file(explicit_config)
    file_explicit: dict = {}
    if cfg_path is not None:
        data = _read_config_file(cfg_path)
        file_explicit = _explicit_from_file(data)
        if file_explicit:
            explicit.update(file_explicit)
            explicit_fields |= set(file_explicit.keys())
        loaded_config_path = cfg_path
        source_label = "config file"

    env_explicit = _env_explicit()
    if env_explicit:
        explicit.update(env_explicit)
        explicit_fields |= set(env_explicit.keys())
        source_label = "environment" if source_label == "defaults" else "mixed"

    cli_explicit = {k: v for k, v in cli_overrides.items() if v is not None}
    if cli_explicit:
        explicit.update(cli_explicit)
        explicit_fields |= set(cli_explicit.keys())
        if source_label == "defaults":
            source_label = "cli"
        else:
            source_label = "mixed"

    return explicit, explicit_fields, source_label, loaded_config_path


def resolve_config(
    *,
    config: Optional[str] = None,
    library_root: Optional[str] = None,
    original_dir: Optional[str] = None,
    staging_dir: Optional[str] = None,
    output_dir: Optional[str] = None,
    quarantine_dir: Optional[str] = None,
    approvals_dir: Optional[str] = None,
    reports_dir: Optional[str] = None,
    logs_dir: Optional[str] = None,
    cache_dir: Optional[str] = None,
) -> tuple[PathConfig, ConfigSource]:
    """Resolve a :class:`PathConfig` honoring the full precedence chain.

    Any ``None`` argument is treated as "not provided" and falls through to the
    next precedence layer. Returns ``(PathConfig, ConfigSource)``.

    When no ``library_root`` can be determined (neither CLI, env, config file,
    XDG, nor system config), raises :class:`PathConfigError` with the standard
    "No library configuration found" message.
    """
    cli_overrides = {
        "library_root": library_root,
        "original_dir": original_dir,
        "staging_dir": staging_dir,
        "output_dir": output_dir,
        "quarantine_dir": quarantine_dir,
        "approvals_dir": approvals_dir,
        "reports_dir": reports_dir,
        "logs_dir": logs_dir,
        "cache_dir": cache_dir,
    }

    explicit, explicit_fields, source_label, loaded_config_path = _gather_explicit(
        explicit_config=config, cli_overrides=cli_overrides
    )

    # library_root precedence: CLI > env > file-derived.
    root = (
        cli_overrides.get("library_root")
        or explicit.get("library_root")
        or os.environ.get(ENV_LIBRARY_ROOT)
        or None
    )

    # If we have an explicit config file but no CLI/env root, derive from file.
    if root is None and loaded_config_path is not None:
        data = _read_config_file(loaded_config_path)
        fr = data.get("library_root")
        if fr:
            root = fr
            explicit.setdefault("library_root", fr)
            explicit_fields.add("library_root")
            if source_label == "defaults":
                source_label = "config file"

    if root is None:
        raise PathConfigError(
            "No library configuration found.\n\n"
            "Run:\n"
            "  amiga-adf-library-builder init\n\n"
            "Or provide:\n"
            "  --config /path/to/config.toml"
        )

    explicit["library_root"] = root
    explicit_fields.add("library_root")
    if source_label == "defaults":
        source_label = "cli" if library_root else "environment"

    return _build_config(
        library_root=Path(root),
        explicit=explicit,
        explicit_fields=explicit_fields,
        source_label=source_label,
        config_path=loaded_config_path,
    )


def default_cache_dir() -> Path:
    return _xdg_cache_path()


def write_config_file(
    path: Path,
    *,
    library_root: str,
    original_dir: Optional[str] = None,
    staging_dir: Optional[str] = None,
    output_dir: Optional[str] = None,
    quarantine_dir: Optional[str] = None,
    approvals_dir: Optional[str] = None,
    reports_dir: Optional[str] = None,
    logs_dir: Optional[str] = None,
    cache_dir: Optional[str] = None,
) -> None:
    """Write a deterministic TOML config file.

    Only ``library_root`` is required. Other roles, when omitted, are left out
    of the file so they remain *derived* (not pinned) on subsequent loads. The
    file is written atomically.
    """
    lines = ["# amiga-adf-library-builder configuration (portable path configuration portable paths)\n"]
    lines.append(f'library_root = {_toml_str(library_root)}\n')
    optional = {
        "original_dir": original_dir,
        "staging_dir": staging_dir,
        "output_dir": output_dir,
        "quarantine_dir": quarantine_dir,
        "approvals_dir": approvals_dir,
        "reports_dir": reports_dir,
        "logs_dir": logs_dir,
        "cache_dir": cache_dir,
    }
    for key, value in optional.items():
        if value:
            lines.append(f"{key} = {_toml_str(value)}\n")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text("".join(lines), encoding="utf-8")
    tmp.replace(target)


def _toml_str(value: str) -> str:
    """Minimal TOML string literal quoting (paths are simple)."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
