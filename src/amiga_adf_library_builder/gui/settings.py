"""Non-sensitive GUI settings store (portable TOML).

SettingsStore holds ONLY non-sensitive, operator preference data: theme,
default paths, run modes, window geometry, and named presets. It MUST NEVER
hold secrets (API tokens, passwords, keys) -- those live in
:mod:`amiga_adf_library_builder.gui.secrets`. Config import/export through this
module is therefore safe: no secret can ever be serialized here.

The serialized form is a single ``[gui]`` table plus optional ``[presets.<name>]``
tables. Only known keys are read and written (defensive against malformed files)
so a corrupt or partial file cannot crash the GUI.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - 3.11+ always has tomllib
    tomllib = None  # type: ignore

import tomli_w  # type: ignore

#: Allowed top-level keys on the ``[gui]`` table. Anything else is ignored.
SETTINGS_KEYS = (
    "theme",
    "default_library_root",
    "default_original_dir",
    "default_staging_dir",
    "default_output_dir",
    "online",
    "refresh_metadata",
    "require_artwork",
    "verify_only",
    "export_gate_acknowledged",
    "advanced_mode",
    "window_geometry",
)


@dataclass
class Settings:
    """Plain dataclass of non-sensitive GUI settings (no secrets)."""

    theme: str = "system"
    default_library_root: str = ""
    default_original_dir: str = ""
    default_staging_dir: str = ""
    default_output_dir: str = ""
    online: bool = False
    refresh_metadata: bool = False
    require_artwork: bool = False
    verify_only: bool = False
    export_gate_acknowledged: bool = False
    advanced_mode: bool = False
    window_geometry: str = ""
    presets: dict[str, "Preset"] = field(default_factory=dict)

    def as_dict(self) -> dict:
        out: dict[str, Any] = {
            "theme": self.theme,
            "default_library_root": self.default_library_root,
            "default_original_dir": self.default_original_dir,
            "default_staging_dir": self.default_staging_dir,
            "default_output_dir": self.default_output_dir,
            "online": self.online,
            "refresh_metadata": self.refresh_metadata,
            "require_artwork": self.require_artwork,
            "verify_only": self.verify_only,
            "export_gate_acknowledged": self.export_gate_acknowledged,
            "advanced_mode": self.advanced_mode,
            "window_geometry": self.window_geometry,
        }
        if self.presets:
            out["presets"] = {name: p.as_dict() for name, p in self.presets.items()}
        return out

    @classmethod
    def from_dict(cls, data: dict) -> "Settings":
        gui = data.get("gui") if isinstance(data.get("gui"), dict) else {}
        if not isinstance(gui, dict):
            gui = {}
        s = cls()
        s.theme = str(gui.get("theme", "system"))
        s.default_library_root = str(gui.get("default_library_root", ""))
        s.default_original_dir = str(gui.get("default_original_dir", ""))
        s.default_staging_dir = str(gui.get("default_staging_dir", ""))
        s.default_output_dir = str(gui.get("default_output_dir", ""))
        s.online = bool(gui.get("online", False))
        s.refresh_metadata = bool(gui.get("refresh_metadata", False))
        s.require_artwork = bool(gui.get("require_artwork", False))
        s.verify_only = bool(gui.get("verify_only", False))
        s.export_gate_acknowledged = bool(gui.get("export_gate_acknowledged", False))
        s.advanced_mode = bool(gui.get("advanced_mode", False))
        s.window_geometry = str(gui.get("window_geometry", ""))
        raw_presets = gui.get("presets")
        if isinstance(raw_presets, dict):
            for name, val in raw_presets.items():
                if isinstance(val, dict):
                    s.presets[str(name)] = Preset.from_dict(val)
        return s


@dataclass
class Preset:
    """A named, saved non-sensitive settings profile (no secrets)."""

    name: str = ""
    library_root: str = ""
    original_dir: str = ""
    staging_dir: str = ""
    output_dir: str = ""
    online: bool = False
    refresh_metadata: bool = False
    require_artwork: bool = False
    verify_only: bool = False
    export_gate_acknowledged: bool = False
    advanced_mode: bool = False

    def as_dict(self) -> dict:
        return {
            "library_root": self.library_root,
            "original_dir": self.original_dir,
            "staging_dir": self.staging_dir,
            "output_dir": self.output_dir,
            "online": self.online,
            "refresh_metadata": self.refresh_metadata,
            "require_artwork": self.require_artwork,
            "verify_only": self.verify_only,
            "export_gate_acknowledged": self.export_gate_acknowledged,
            "advanced_mode": self.advanced_mode,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Preset":
        return cls(
            library_root=str(data.get("library_root", "")),
            original_dir=str(data.get("original_dir", "")),
            staging_dir=str(data.get("staging_dir", "")),
            output_dir=str(data.get("output_dir", "")),
            online=bool(data.get("online", False)),
            refresh_metadata=bool(data.get("refresh_metadata", False)),
            require_artwork=bool(data.get("require_artwork", False)),
            verify_only=bool(data.get("verify_only", False)),
            export_gate_acknowledged=bool(data.get("export_gate_acknowledged", False)),
            advanced_mode=bool(data.get("advanced_mode", False)),
        )


class SettingsError(Exception):
    """Raised when settings cannot be loaded or saved."""


class SettingsStore:
    """Thread-safe, non-sensitive settings store with persistence + presets.

    Instantiate with a ``settings_path`` (typically
    ``PortablePaths.settings_file()``). The store loads on demand and writes
    atomically. Secrets are NEVER serialized; there is no code path here that
    accepts a secret.
    """

    def __init__(self, settings_path: Path) -> None:
        self._path = Path(settings_path)
        self._lock = threading.RLock()
        self._settings = Settings()

    # --- load / save ----------------------------------------------------------
    def load(self) -> Settings:
        with self._lock:
            if not self._path.is_file():
                self._settings = Settings()
                return self._settings
            try:
                with open(self._path, "rb") as fh:
                    data = tomllib.load(fh) if tomllib else {}
            except (OSError, ValueError) as exc:
                raise SettingsError(f"cannot read settings {self._path}: {exc}") from exc
            if not isinstance(data, dict):
                raise SettingsError(f"malformed settings file: {self._path}")
            self._settings = Settings.from_dict(data)
            return self._settings

    def save(self) -> None:
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"gui": self._settings.as_dict()}
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            try:
                with open(tmp, "wb") as fh:
                    tomli_w.dump(payload, fh)
                tmp.replace(self._path)
            except OSError as exc:
                raise SettingsError(f"cannot write settings {self._path}: {exc}") from exc

    # --- accessors ------------------------------------------------------------
    def get(self) -> Settings:
        with self._lock:
            return self._settings

    def update(self, **changes: Any) -> None:
        """Update one or more top-level settings keys and persist."""
        with self._lock:
            for key, value in changes.items():
                if key not in SETTINGS_KEYS and key != "presets":
                    raise SettingsError(f"unknown settings key: {key}")
                setattr(self._settings, key, value)
            self.save()

    # --- presets --------------------------------------------------------------
    def save_preset(self, preset: Preset) -> None:
        with self._lock:
            if not preset.name:
                raise SettingsError("preset name is required")
            self._settings.presets[preset.name] = preset
            self.save()

    def delete_preset(self, name: str) -> None:
        with self._lock:
            self._settings.presets.pop(name, None)
            self.save()

    def apply_preset(self, name: str) -> Preset:
        with self._lock:
            preset = self._settings.presets.get(name)
            if preset is None:
                raise SettingsError(f"no such preset: {name}")
            return preset
