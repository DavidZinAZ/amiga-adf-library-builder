"""GH-167 — Artwork preview regression tests (RC-A).

Tests that _load_artwork_preview shows explicit failure states
and handles missing artwork correctly.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from amiga_adf_library_builder.models import StagedReleaseEntry


# ---------------------------------------------------------------------------
# _load_artwork_preview shows explicit failure states
# ---------------------------------------------------------------------------

class TestArtworkPreviewFailureStates:
    def test_no_artwork_has_empty_pixmap(self):
        """When no artwork path exists, pixmap is null."""
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])

        from amiga_adf_library_builder.gui.preview_widget import (
            PreviewWidget,
            PreviewWidgetState,
        )
        from amiga_adf_library_builder.models import StagedLibrary

        widget = PreviewWidget()
        widget._state = PreviewWidgetState()
        widget._state.current_library = StagedLibrary()
        entry = StagedReleaseEntry(
            release_key="test|1",
            title="Test",
            edition="",
            group="",
            chipset=None,
        )
        widget._load_artwork_preview(entry)
        assert widget._artwork_preview.pixmap().isNull()

    def test_missing_file_has_empty_pixmap(self):
        """When the artwork file doesn't exist, pixmap is null."""
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])

        from amiga_adf_library_builder.gui.preview_widget import (
            PreviewWidget,
            PreviewWidgetState,
        )
        from amiga_adf_library_builder.models import StagedLibrary

        widget = PreviewWidget()
        widget._state = PreviewWidgetState()
        widget._state.current_library = StagedLibrary()
        entry = StagedReleaseEntry(
            release_key="test|1",
            title="Test",
            edition="",
            group="",
            chipset=None,
            artwork_front="/nonexistent/path.jpg",
        )
        widget._load_artwork_preview(entry)
        assert widget._artwork_preview.pixmap().isNull()

    def test_existing_image_renders(self):
        """When the artwork file exists and is valid, it renders."""
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])

        from amiga_adf_library_builder.gui.preview_widget import (
            PreviewWidget,
            PreviewWidgetState,
        )
        from amiga_adf_library_builder.models import StagedLibrary
        import tempfile

        widget = PreviewWidget()
        widget._state = PreviewWidgetState()
        widget._state.current_library = StagedLibrary()

        with tempfile.NamedTemporaryFile(
            suffix=".jpg", delete=False
        ) as f:
            f.write(b"\xff\xd8\xff\xe0\x00\x10JFIF")
            tmp_path = f.name

        try:
            entry = StagedReleaseEntry(
                release_key="test|1",
                title="Test",
                edition="",
                group="",
                chipset=None,
                artwork_front=tmp_path,
            )
            widget._load_artwork_preview(entry)
            assert widget._artwork_preview.pixmap() is not None
        finally:
            os.unlink(tmp_path)

    def test_operator_selection_not_overwritten(self):
        """_load_artwork_preview doesn't change entry.artwork_front."""
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])

        from amiga_adf_library_builder.gui.preview_widget import (
            PreviewWidget,
            PreviewWidgetState,
        )
        from amiga_adf_library_builder.models import StagedLibrary

        widget = PreviewWidget()
        widget._state = PreviewWidgetState()
        widget._state.current_library = StagedLibrary()
        entry = StagedReleaseEntry(
            release_key="test|1",
            title="Test",
            edition="",
            group="",
            chipset=None,
            artwork_front="/operator/selected.jpg",
        )
        original_front = entry.artwork_front
        widget._load_artwork_preview(entry)
        assert entry.artwork_front == original_front

    def test_stale_no_artwork_text_not_present(self):
        """Old 'No artwork available' text is replaced."""
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])

        from amiga_adf_library_builder.gui.preview_widget import (
            PreviewWidget,
            PreviewWidgetState,
        )
        from amiga_adf_library_builder.models import StagedLibrary

        widget = PreviewWidget()
        widget._state = PreviewWidgetState()
        widget._state.current_library = StagedLibrary()
        entry = StagedReleaseEntry(
            release_key="test|1",
            title="Test",
            edition="",
            group="",
            chipset=None,
        )
        widget._load_artwork_preview(entry)
        text = widget._artwork_preview.text()
        assert "No artwork available" not in text
