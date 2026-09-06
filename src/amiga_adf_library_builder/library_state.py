"""Staged library state persistence for the Library Preview & Curation workspace.

This module handles loading and saving the curation state independently of
originals and export destination. Uses schema-versioned JSON with atomic
temp+rename writes and safe missing/malformed-file handling.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .models import StagedLibrary, StagedReleaseEntry, StagedState, StagedChange, CurationAction


# Current schema version for the curation state file
CURATION_STATE_SCHEMA_VERSION = 1


@dataclass
class CurationStateMeta:
    """Metadata about the curation state file."""
    schema_version: int = CURATION_STATE_SCHEMA_VERSION
    created_at: str = ""
    updated_at: str = ""
    source_workspace: str = ""  # Identifier of the workspace this state belongs to
    entry_count: int = 0

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "source_workspace": self.source_workspace,
            "entry_count": self.entry_count,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CurationStateMeta":
        return cls(
            schema_version=d.get("schema_version", CURATION_STATE_SCHEMA_VERSION),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
            source_workspace=d.get("source_workspace", ""),
            entry_count=d.get("entry_count", 0),
        )


@dataclass
class CurationStateFile:
    """Complete curation state file structure."""
    meta: CurationStateMeta = field(default_factory=CurationStateMeta)
    library: StagedLibrary = field(default_factory=StagedLibrary)

    def to_dict(self) -> dict:
        return {
            "meta": self.meta.to_dict(),
            "library": self.library.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CurationStateFile":
        return cls(
            meta=CurationStateMeta.from_dict(d.get("meta", {})),
            library=StagedLibrary.from_dict(d.get("library", {})),
        )


class CurationStateManager:
    """Manages persistence of the staged library curation state."""

    def __init__(self, state_path: Path):
        """
        Initialize the curation state manager.

        Args:
            state_path: Path to the .library_state.json file
        """
        self.state_path = Path(state_path)
        self._current_state: Optional[CurationStateFile] = None

    def _generate_timestamp(self) -> str:
        """Generate ISO timestamp."""
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())

    def _validate_schema(self, data: dict) -> bool:
        """Validate the loaded data against expected schema."""
        if not isinstance(data, dict):
            return False
        if "meta" not in data or "library" not in data:
            return False
        meta = data.get("meta", {})
        if not isinstance(meta, dict):
            return False
        schema_version = meta.get("schema_version", 0)
        if not isinstance(schema_version, int) or schema_version != CURATION_STATE_SCHEMA_VERSION:
            return False
        return True

    def load(self) -> StagedLibrary:
        """
        Load the curation state from disk.

        Returns:
            StagedLibrary instance (empty if no valid state file exists)
        """
        if not self.state_path.exists():
            # No state file - return empty library
            self._current_state = CurationStateFile(
                meta=CurationStateMeta(
                    created_at=self._generate_timestamp(),
                    updated_at=self._generate_timestamp(),
                    source_workspace=self.state_path.stem,
                    entry_count=0,
                ),
                library=StagedLibrary(),
            )
            return self._current_state.library

        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            # Malformed file - return empty library
            self._current_state = CurationStateFile(
                meta=CurationStateMeta(
                    created_at=self._generate_timestamp(),
                    updated_at=self._generate_timestamp(),
                    source_workspace=self.state_path.stem,
                ),
                library=StagedLibrary(),
            )
            return self._current_state.library

        # Validate schema
        if not self._validate_schema(data):
            # Schema mismatch or invalid - return empty library
            self._current_state = CurationStateFile(
                meta=CurationStateMeta(
                    created_at=self._generate_timestamp(),
                    updated_at=self._generate_timestamp(),
                    source_workspace=self.state_path.stem,
                ),
                library=StagedLibrary(),
            )
            return self._current_state.library

        try:
            self._current_state = CurationStateFile.from_dict(data)
            return self._current_state.library
        except (KeyError, TypeError, ValueError):
            # Deserialization failed - return empty library
            self._current_state = CurationStateFile(
                meta=CurationStateMeta(
                    created_at=self._generate_timestamp(),
                    updated_at=self._generate_timestamp(),
                    source_workspace=self.state_path.stem,
                ),
                library=StagedLibrary(),
            )
            return self._current_state.library

    def save(self, library: StagedLibrary) -> bool:
        """
        Save the curation state to disk atomically.

        Args:
            library: The staged library to save

        Returns:
            True if save succeeded, False otherwise
        """
        now = self._generate_timestamp()

        if self._current_state is None:
            meta = CurationStateMeta(
                created_at=now,
                updated_at=now,
                source_workspace=self.state_path.stem,
                entry_count=len(library.releases),
            )
        else:
            meta = CurationStateMeta(
                schema_version=self._current_state.meta.schema_version,
                created_at=self._current_state.meta.created_at or now,
                updated_at=now,
                source_workspace=self.state_path.stem,
                entry_count=len(library.releases),
            )

        state_file = CurationStateFile(meta=meta, library=library)

        # Atomic write: write to temp file then rename
        temp_file = self.state_path.with_suffix(".json.tmp")
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(state_file.to_dict(), f, indent=2, ensure_ascii=False)
            # Atomic rename
            temp_file.replace(self.state_path)
            self._current_state = state_file
            return True
        except (OSError, TypeError, ValueError):
            # Clean up temp file on failure
            try:
                if temp_file.exists():
                    temp_file.unlink()
            except OSError:
                pass
            return False

    def export_changes_patch(self, library: StagedLibrary) -> dict:
        """
        Export the decision log as a JSON changes patch for re-application.

        Returns:
            Dict containing the changes patch with metadata
        """
        all_changes = []
        for entry in library.releases.values():
            for action in entry.actions:
                change_data = action.to_dict()
                change_data["release_key"] = entry.release_key
                change_data["title"] = entry.title
                all_changes.append(change_data)

        return {
            "patch_format_version": 1,
            "generated_at": self._generate_timestamp(),
            "source_state": str(self.state_path),
            "total_releases": len(library.releases),
            "total_changes": len(all_changes),
            "changes": all_changes,
        }

    def get_current_state(self) -> Optional[CurationStateFile]:
        """Get the currently loaded state file (including metadata)."""
        return self._current_state

    def clear(self) -> bool:
        """
        Clear the curation state file.

        Returns:
            True if cleared successfully, False otherwise
        """
        try:
            if self.state_path.exists():
                self.state_path.unlink()
            self._current_state = None
            return True
        except OSError:
            return False


def create_curation_state_manager(state_path: Path) -> CurationStateManager:
    """Factory function to create a CurationStateManager."""
    return CurationStateManager(state_path)