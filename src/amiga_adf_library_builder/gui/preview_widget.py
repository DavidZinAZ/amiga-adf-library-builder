"""Library Preview & Curation workspace widget (GH-80).

This widget provides a full interactive workspace for reviewing and curating
the staged library. It loads a library state file (.library_state.json)
independently of the originals and export destination.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import (
    QItemSelectionModel,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
    QThread,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QAction,
    QDesktopServices,
    QIcon,
    QKeySequence,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from amiga_adf_library_builder.library_state import (
    CurationStateFile,
    CurationStateManager,
    CURATION_STATE_SCHEMA_VERSION,
)
from amiga_adf_library_builder.lookup_workflow import (
    LookupContext,
    LookupResult,
    providers_for_mode,
    run_lookup,
)
from amiga_adf_library_builder.models import (
    CurationAction,
    StagedChange,
    StagedLibrary,
    StagedReleaseEntry,
    StagedState,
)


class PreviewWorker(QThread):
    """Worker thread for background lookup/matching operations.

    Routes the lookup through the SHARED workflow
    (:func:`amiga_adf_library_builder.lookup_workflow.run_lookup`) so Online and
    Offline lookups share one implementation with explicit provider routing.
    The provider list is derived from the mode via
    :func:`lookup_workflow.providers_for_mode` -- never hardcoded -- so an
    offline lookup can never list or contact an online provider (GH-89).
    """

    lookup_completed = Signal(str, list)  # release_key, candidate dicts
    lookup_failed = Signal(str, str)  # release_key, error

    def __init__(self, release_key: str, query: str, provider: str,
                 title: str = "", disk_stems: Optional[list[str]] = None,
                 context: Optional[LookupContext] = None, parent=None):
        super().__init__(parent)
        self.release_key = release_key
        self.query = query
        self.provider = provider
        self._title = title
        self._disk_stems = list(disk_stems or [])
        self._context = context

    def _build_context(self) -> LookupContext:
        if self._context is not None:
            return self._context
        return LookupContext(
            query=self.query,
            release_key=self.release_key,
            title=self._title,
            disk_stems=list(self._disk_stems),
        )

    def run(self) -> None:
        # Real shared lookup workflow: routes online/offline explicitly and
        # runs the provider-backed search (no placeholder).
        ctx = self._build_context()
        try:
            result = run_lookup(self.provider, ctx)
        except Exception as exc:  # never let a worker exception kill the thread
            self.lookup_failed.emit(self.release_key, f"lookup crashed: {exc}")
            return
        if result.status == "error":
            self.lookup_failed.emit(self.release_key, result.error)
            return
        self.lookup_completed.emit(self.release_key, [self._candidate_payload(result)])

    @staticmethod
    def _candidate_payload(result: LookupResult) -> dict:
        """One JSON-serializable candidate dict for the result table."""
        payload = {
            "mode": result.mode,
            "kind": result.kind,
            "provider_ids": list(result.provider_ids),
            "status": result.status,
            "consulted": list(result.consulted),
            "local_source_state": list(result.local_source_state),
            "confidence": result.confidence,
            "title": None,
            "provider": None,
            "year": None,
            "developer": None,
            "publisher": None,
            "description": None,
            "artwork_url": None,
            "local_cached_path": None,
            "local_category": None,
            "local_outcome": None,
            "local_review_reason": None,
        }
        if result.record is not None:
            rec = result.record
            payload.update(
                title=rec.canonical_title,
                provider=rec.provider,
                year=rec.year,
                developer=rec.developer,
                publisher=rec.publisher,
                description=rec.description[:400] if rec.description else None,
                artwork_url=rec.artwork_url or None,
            )
        if result.local_result is not None:
            lr = result.local_result
            payload.update(
                local_cached_path=str(lr.cached_path) if lr.cached_path else None,
                local_category=lr.category,
                local_outcome=lr.outcome,
                local_review_reason=lr.manual_review_reason,
            )
        return payload


@dataclass
class PreviewWidgetState:
    """UI state for the preview widget."""
    current_library: Optional[StagedLibrary] = None
    state_manager: Optional[CurationStateManager] = None
    current_state_path: Optional[Path] = None
    row_to_release_key: dict[int, str] = None
    selected_release_key: Optional[str] = None

    def __post_init__(self):
        if self.row_to_release_key is None:
            self.row_to_release_key = {}


class PreviewWidget(QWidget):
    """Main widget for the Library Preview & Curation workspace."""

    state_changed = Signal()
    status_message = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = PreviewWidgetState()
        self._undo_stack: list[tuple[str, StagedChange]] = []  # (release_key, action)
        self._redo_stack: list[tuple[str, StagedChange]] = []
        # Injectable provider of the lookup execution context (cache dirs,
        # config path). The main window wires this to the app's resolved
        # paths; tests may supply a temp-dir context. Without it the widget
        # falls back to resolving the app paths itself (GH-88/89).
        self._lookup_context_provider: Optional[Callable[[], Optional[LookupContext]]] = None
        self._lookup_worker: Optional[PreviewWorker] = None
        self._build_ui()
        self._connect_signals()

    def _build_ui(self) -> None:
        """Build the complete UI for the preview workspace."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # --- Top toolbar ---
        toolbar = self._build_toolbar()
        layout.addLayout(toolbar)

        # --- Filter/Search bar ---
        filter_bar = self._build_filter_bar()
        layout.addLayout(filter_bar)

        # --- Main content: split view ---
        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)

        # Left: releases table
        left_widget = self._build_table_view()
        splitter.addWidget(left_widget)

        # Right: detail/preview pane
        right_widget = self._build_detail_pane()
        splitter.addWidget(right_widget)

        splitter.setSizes([600, 500])

        # --- Summary/status bar ---
        self._summary_label = QLabel("No library state loaded.")
        self._summary_label.setStyleSheet("color: #666; font-size: 11px; padding: 4px;")
        layout.addWidget(self._summary_label)

    def _build_toolbar(self) -> QHBoxLayout:
        """Build the top toolbar with load/save/export actions."""
        toolbar = QHBoxLayout()

        self._load_btn = QPushButton("Load State…")
        self._load_btn.setToolTip("Load a .library_state.json file into this workspace")
        self._load_btn.clicked.connect(self._on_load_state)
        toolbar.addWidget(self._load_btn)

        self._save_btn = QPushButton("Save State…")
        self._save_btn.setToolTip("Save the current curation state to a .library_state.json file")
        self._save_btn.clicked.connect(self._on_save_state)
        toolbar.addWidget(self._save_btn)

        self._export_changes_btn = QPushButton("Export Changes…")
        self._export_changes_btn.setToolTip("Export the decision log as a JSON changes patch for re-application")
        self._export_changes_btn.clicked.connect(self._on_export_changes)
        toolbar.addWidget(self._export_changes_btn)

        toolbar.addStretch(1)

        # Undo/Redo
        self._undo_btn = QPushButton("Undo")
        self._undo_btn.setToolTip("Undo last curation action (Ctrl+Z)")
        self._undo_btn.clicked.connect(self._on_undo)
        self._undo_btn.setEnabled(False)
        toolbar.addWidget(self._undo_btn)

        self._redo_btn = QPushButton("Redo")
        self._redo_btn.setToolTip("Redo last undone action (Ctrl+Y)")
        self._redo_btn.clicked.connect(self._on_redo)
        self._redo_btn.setEnabled(False)
        toolbar.addWidget(self._redo_btn)

        return toolbar

    def _build_filter_bar(self) -> QHBoxLayout:
        """Build the filter and search bar."""
        filter_bar = QHBoxLayout()

        self._filter_combo = QComboBox()
        # Store canonical state values as item data for reliable filtering.
        # Display text can be user-friendly; item data is the exact StagedState value.
        self._filter_combo.addItem("All", "all")
        self._filter_combo.addItem("Pending", StagedState.PENDING.value)
        self._filter_combo.addItem("Accepted", StagedState.ACCEPTED.value)
        self._filter_combo.addItem("Rejected", StagedState.REJECTED.value)
        self._filter_combo.addItem("Modified", StagedState.MODIFIED.value)
        self._filter_combo.addItem("Needs Review", StagedState.NEEDS_REVIEW.value)
        # Additional filter criteria (not direct StagedState values) - use sentinel values
        self._filter_combo.addItem("Missing Artwork", "missing_artwork")
        self._filter_combo.addItem("Missing RTFM", "missing_rtfm")
        self._filter_combo.addItem("Unmatched", "unmatched")
        self._filter_combo.addItem("Excluded", "excluded")
        self._filter_combo.setToolTip("Filter releases by curation state")
        self._filter_combo.currentIndexChanged.connect(self._apply_filter)
        filter_bar.addWidget(QLabel("Filter:"))
        filter_bar.addWidget(self._filter_combo)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search release key, title, group…")
        self._search.setToolTip("Filter by release key, title, or group name")
        self._search.textChanged.connect(self._apply_filter)
        filter_bar.addWidget(self._search)

        # (GH-99, defect 6) The redundant "Multi-Select" toggle is removed:
        # the table now uses standard Windows-style Ctrl/Shift multi-selection
        # (ExtendedSelection) always on, so the toggle no longer provides any
        # useful behavior.

        return filter_bar

    def _build_table_view(self) -> QWidget:
        """Build the releases table view."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        self._table = QTableWidget(0, 7)
        self._table.setHorizontalHeaderLabels([
            "State", "Release Key", "Title", "Edition", "Group", "ADFs", "Confidence"
        ])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        # (GH-99, defect 5) Standard Windows-style multi-select: Ctrl+Click
        # toggles individual rows, Shift+Click extends a contiguous range.
        # ExtendedSelection provides exactly this; the redundant Multi-Select
        # toggle is removed (defect 6).
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setSortingEnabled(False)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_context_menu)
        self._table.selectionModel().selectionChanged.connect(self._on_selection_changed)

        layout.addWidget(self._table)

        # Table action buttons
        table_btns = QHBoxLayout()

        self._accept_btn = QPushButton("Accept")
        self._accept_btn.setToolTip("Mark selected release(s) as Accepted (include in export)")
        self._accept_btn.clicked.connect(lambda: self._set_selected_state(StagedState.ACCEPTED))
        self._accept_btn.setEnabled(False)
        table_btns.addWidget(self._accept_btn)

        self._reject_btn = QPushButton("Reject")
        self._reject_btn.setToolTip("Mark selected release(s) as Rejected (exclude from export)")
        self._reject_btn.clicked.connect(lambda: self._set_selected_state(StagedState.REJECTED))
        self._reject_btn.setEnabled(False)
        table_btns.addWidget(self._reject_btn)

        self._pending_btn = QPushButton("Set Pending")
        self._pending_btn.setToolTip("Reset selected release(s) to Pending review")
        self._pending_btn.clicked.connect(lambda: self._set_selected_state(StagedState.PENDING))
        self._pending_btn.setEnabled(False)
        table_btns.addWidget(self._pending_btn)

        self._modified_btn = QPushButton("Mark Modified")
        self._modified_btn.setToolTip("Mark selected release(s) as Modified (custom changes applied)")
        self._modified_btn.clicked.connect(lambda: self._set_selected_state(StagedState.MODIFIED))
        self._modified_btn.setEnabled(False)
        table_btns.addWidget(self._modified_btn)

        table_btns.addStretch(1)
        layout.addLayout(table_btns)

        return widget

    def _build_detail_pane(self) -> QWidget:
        """Build the detail/preview pane."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        # Release detail group
        detail_box = QGroupBox("Release Detail")
        detail_layout = QFormLayout(detail_box)

        self._detail_key = QLabel("")
        self._detail_key.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._detail_key.setWordWrap(True)
        detail_layout.addRow("Release Key:", self._detail_key)

        self._detail_title = QLabel("")
        self._detail_title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._detail_title.setWordWrap(True)
        detail_layout.addRow("Title:", self._detail_title)

        # (GH-99, defect 4) Edition and Group are editable in the staged
        # curation state. Edits update the staged entry only (METADATA_EDIT
        # decision-log entry); they never write final export files.
        self._detail_edition = QLineEdit()
        self._detail_edition.setPlaceholderText("(none)")
        self._detail_edition.setToolTip("Edit the staged Edition (staged state only)")
        self._detail_edition.textChanged.connect(self._on_detail_edition_changed)
        detail_layout.addRow("Edition:", self._detail_edition)

        self._detail_group = QLineEdit()
        self._detail_group.setPlaceholderText("(none)")
        self._detail_group.setToolTip("Edit the staged Group (staged state only)")
        self._detail_group.textChanged.connect(self._on_detail_group_changed)
        detail_layout.addRow("Group:", self._detail_group)

        self._detail_state = QLabel("")
        detail_layout.addRow("Curation State:", self._detail_state)

        self._detail_confidence = QLabel("")
        detail_layout.addRow("Confidence:", self._detail_confidence)

        self._detail_adf_count = QLabel("")
        detail_layout.addRow("ADF Files:", self._detail_adf_count)

        self._detail_artwork = QLabel("")
        self._detail_artwork.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._detail_artwork.setWordWrap(True)
        detail_layout.addRow("Artwork:", self._detail_artwork)

        self._detail_rtfm = QLabel("")
        self._detail_rtfm.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._detail_rtfm.setWordWrap(True)
        detail_layout.addRow("RTFM/Manual:", self._detail_rtfm)

        self._detail_provenance = QLabel("")
        self._detail_provenance.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._detail_provenance.setWordWrap(True)
        detail_layout.addRow("Provenance:", self._detail_provenance)

        self._detail_notes = QTextEdit()
        self._detail_notes.setReadOnly(True)
        self._detail_notes.setMaximumHeight(100)
        detail_layout.addRow("Notes:", self._detail_notes)

        layout.addWidget(detail_box)

        # Artwork preview
        artwork_box = QGroupBox("Artwork Preview")
        artwork_layout = QVBoxLayout(artwork_box)
        self._artwork_preview = QLabel("No artwork selected")
        self._artwork_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._artwork_preview.setMinimumHeight(200)
        self._artwork_preview.setStyleSheet("background: #f0f0f0; border: 1px solid #ccc;")
        artwork_layout.addWidget(self._artwork_preview)
        layout.addWidget(artwork_box)

        # Actions history
        history_box = QGroupBox("Curation Actions (Decision Log)")
        history_layout = QVBoxLayout(history_box)
        self._actions_list = QTextEdit()
        self._actions_list.setReadOnly(True)
        self._actions_list.setFontFamily("monospace")
        history_layout.addWidget(self._actions_list)
        layout.addWidget(history_box)

        # Detail action buttons
        detail_btns = QHBoxLayout()

        self._add_note_btn = QPushButton("Add Note…")
        self._add_note_btn.setToolTip("Add a curation note to the selected release")
        self._add_note_btn.clicked.connect(self._on_add_note)
        self._add_note_btn.setEnabled(False)
        detail_btns.addWidget(self._add_note_btn)

        self._view_adf_btn = QPushButton("View ADF List…")
        self._view_adf_btn.setToolTip("Show the full list of ADF files for this release")
        self._view_adf_btn.clicked.connect(self._on_view_adf_list)
        self._view_adf_btn.setEnabled(False)
        detail_btns.addWidget(self._view_adf_btn)

        self._rename_btn = QPushButton("Rename…")
        self._rename_btn.setToolTip("Rename the selected release")
        self._rename_btn.clicked.connect(self._on_rename)
        self._rename_btn.setEnabled(False)
        detail_btns.addWidget(self._rename_btn)

        self._lookup_btn = QPushButton("Lookup…")
        self._lookup_btn.setToolTip("Perform online/offline lookup for this release")
        self._lookup_btn.clicked.connect(self._on_lookup)
        self._lookup_btn.setEnabled(False)
        detail_btns.addWidget(self._lookup_btn)

        detail_btns.addStretch(1)
        layout.addLayout(detail_btns)

        layout.addStretch(1)

        return widget

    def _connect_signals(self) -> None:
        """Connect internal signals."""
        pass

    # --- State management ---

    def _on_load_state(self) -> None:
        """Load a .library_state.json file into the workspace."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Load Library State",
            "",
            "Library State (*.library_state.json);;All files (*.*)"
        )
        if not file_path:
            return

        try:
            self._state.state_manager = CurationStateManager(Path(file_path))
            self._state.current_library = self._state.state_manager.load()
            self._state.current_state_path = Path(file_path)
            self._refresh_table()
            self._update_summary()
            self.status_message.emit(f"Loaded: {file_path} ({len(self._state.current_library.releases)} releases)")
        except Exception as exc:
            QMessageBox.critical(self, "Load State Failed", f"Could not load library state: {exc}")
            self._summary_label.setText("Load failed.")

    def _on_save_state(self) -> None:
        """Save the current curation state to a .library_state.json file."""
        if self._state.state_manager is None or self._state.current_library is None:
            QMessageBox.information(self, "Save State", "No library state loaded to save.")
            return

        default_path = str(self._state.state_manager.state_path) if self._state.state_manager.state_path else ""
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Library State",
            default_path,
            "Library State (*.library_state.json);;All files (*.*)"
        )
        if not file_path:
            return

        try:
            self._state.state_manager.state_path = Path(file_path)
            self._state.state_manager.save(self._state.current_library)
            self._state.current_state_path = Path(file_path)
            self._update_summary()
            self.status_message.emit(f"Saved: {file_path}")
        except Exception as exc:
            QMessageBox.critical(self, "Save State Failed", f"Could not save library state: {exc}")

    def _on_export_changes(self) -> None:
        """Export the decision log as a JSON changes patch."""
        if self._state.state_manager is None or self._state.current_library is None:
            QMessageBox.information(self, "Export Changes", "No library state loaded.")
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Changes Patch",
            "",
            "Changes Patch (*.changes.json);;All files (*.*)"
        )
        if not file_path:
            return

        try:
            patch_data = self._state.state_manager.export_changes_patch(self._state.current_library)
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(patch_data, f, indent=2, ensure_ascii=False)
            self.status_message.emit(f"Exported changes: {file_path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export Failed", f"Could not export changes: {exc}")

    def _refresh_table(self) -> None:
        """Refresh the releases table from the current library."""
        if self._state.current_library is None:
            self._table.setRowCount(0)
            self._state.row_to_release_key.clear()
            return

        releases = list(self._state.current_library.releases.values())
        self._table.setRowCount(len(releases))
        self._state.row_to_release_key.clear()

        for row, entry in enumerate(releases):
            self._state.row_to_release_key[row] = entry.release_key

            # State column with color coding
            state_item = QTableWidgetItem(entry.curation_state.value)
            state_item.setData(Qt.ItemDataRole.UserRole, entry.release_key)
            state_item.setData(Qt.ItemDataRole.UserRole + 1, entry.curation_state.value)
            # Color by state
            if entry.curation_state == StagedState.ACCEPTED:
                state_item.setBackground(Qt.GlobalColor.green)
                state_item.setForeground(Qt.GlobalColor.white)
            elif entry.curation_state == StagedState.REJECTED:
                state_item.setBackground(Qt.GlobalColor.red)
                state_item.setForeground(Qt.GlobalColor.white)
            elif entry.curation_state == StagedState.MODIFIED:
                state_item.setBackground(Qt.GlobalColor.yellow)
                state_item.setForeground(Qt.GlobalColor.black)
            elif entry.curation_state == StagedState.NEEDS_REVIEW:
                state_item.setBackground(Qt.GlobalColor.cyan)
                state_item.setForeground(Qt.GlobalColor.black)
            self._table.setItem(row, 0, state_item)

            key_item = QTableWidgetItem(entry.release_key)
            key_item.setData(Qt.ItemDataRole.UserRole, entry.release_key)
            self._table.setItem(row, 1, key_item)

            title_item = QTableWidgetItem(entry.title)
            title_item.setData(Qt.ItemDataRole.UserRole, entry.release_key)
            self._table.setItem(row, 2, title_item)
            self._table.setItem(row, 3, QTableWidgetItem(entry.edition or ""))
            self._table.setItem(row, 4, QTableWidgetItem(entry.group or ""))
            # (GH-99, defect 1) ADFs count column: after a Merge/Move ADFs, the
            # refreshed table shows the new membership count so the screen
            # visibly reflects the reconciliation.
            adf_count_item = QTableWidgetItem(str(len(entry.adf_files)))
            adf_count_item.setData(Qt.ItemDataRole.UserRole, entry.release_key)
            adf_count_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 5, adf_count_item)
            self._table.setItem(row, 6, QTableWidgetItem(f"{entry.confidence:.2f}" if entry.confidence > 0 else ""))

        self._apply_filter()
        self._update_summary()

    def _apply_filter(self) -> None:
        """Apply the current filter and search to the table."""
        # Use item data (canonical state value) for reliable filtering.
        # Display text is user-friendly; item data matches the stored state values.
        filter_data = self._filter_combo.currentData()
        search_text = self._search.text().strip().lower()

        for row in range(self._table.rowCount()):
            show = True

            # State filter
            if filter_data != "all":
                state_item = self._table.item(row, 0)
                if state_item:
                    # The canonical state value is stored in UserRole+1
                    row_state = state_item.data(Qt.ItemDataRole.UserRole + 1)
                    if row_state != filter_data:
                        show = False

            # Search filter
            if show and search_text:
                key_item = self._table.item(row, 1)
                title_item = self._table.item(row, 2)
                group_item = self._table.item(row, 4)
                key_match = key_item and search_text in key_item.text().lower()
                title_match = title_item and search_text in title_item.text().lower()
                group_match = group_item and search_text in group_item.text().lower()
                if not (key_match or title_match or group_match):
                    show = False

            self._table.setRowHidden(row, not show)

    def _on_selection_changed(self) -> None:
        """Handle selection change in the releases table."""
        selected = self._table.selectionModel().selectedRows()
        has_selection = len(selected) > 0

        self._accept_btn.setEnabled(has_selection)
        self._reject_btn.setEnabled(has_selection)
        self._pending_btn.setEnabled(has_selection)
        self._modified_btn.setEnabled(has_selection)
        self._add_note_btn.setEnabled(has_selection)
        self._view_adf_btn.setEnabled(has_selection)
        self._rename_btn.setEnabled(has_selection)
        self._lookup_btn.setEnabled(has_selection)

        # Update undo/redo state
        self._undo_btn.setEnabled(len(self._undo_stack) > 0)
        self._redo_btn.setEnabled(len(self._redo_stack) > 0)

        if has_selection:
            row = selected[0].row()
            # Resolve release_key from item UserRole data (stable identity)
            # rather than row_to_release_key[row] which breaks after sorting.
            release_key = None
            for col in range(self._table.columnCount()):
                item = self._table.item(row, col)
                if item is not None:
                    release_key = item.data(Qt.ItemDataRole.UserRole)
                    if release_key:
                        break
            if release_key and self._state.current_library:
                entry = self._state.current_library.releases.get(release_key)
                if entry:
                    self._show_detail(entry)
                    self._state.selected_release_key = release_key

    # --- GH-99 defect 4: staged Edition/Group editing ---

    @staticmethod
    def _edit_field(entry: "StagedReleaseEntry", field: str, new_value: str) -> None:
        """Apply one staged Edition/Group edit.

        ``new_value`` is the trimmed field text. An empty value clears the
        field (staged state only — no export files are written).
        """
        if field == "edition":
            entry.edition = new_value or None
        else:
            entry.group = new_value or None

    def _record_metadata_edit(
        self, entry: "StagedReleaseEntry", field: str, old_value: Optional[str]
    ) -> None:
        """Append one METADATA_EDIT decision-log entry for an Edition/Group
        edit and record it for undo/redo.

        ``old_value`` must be captured BEFORE the edit is applied. The
        payload carries pre/post snapshots of exactly the edited field so
        undo/redo is precise and never disturbs other metadata.
        """
        new_value = entry.edition if field == "edition" else entry.group
        action = StagedChange(
            action=CurationAction.METADATA_EDIT,
            timestamp=datetime.now(timezone.utc).isoformat(),
            details=(
                f"{field.capitalize()} changed from "
                f"{old_value!r} to {new_value!r}"
            ),
            payload=json.dumps(
                {
                    "kind": "field_edit",
                    "field": field,
                    "pre": {field: old_value},
                    "post": {field: new_value},
                }
            ),
        )
        entry.actions.append(action)
        self._record_action_for_undo(entry.release_key, action)

    def _on_detail_edition_changed(self, text: str) -> None:
        """Apply a staged Edition edit from the detail pane (defect 4).

        Edits update staged curation state only and never write final export
        files. The change is recorded in the decision log (METADATA_EDIT)
        and is undoable.
        """
        entry = self._entry_for_detail_edit()
        if entry is None:
            return
        old_value = entry.edition
        self._edit_field(entry, "edition", text)
        self._record_metadata_edit(entry, "edition", old_value)
        self._refresh_table()
        self.state_changed.emit()

    def _on_detail_group_changed(self, text: str) -> None:
        """Apply a staged Group edit from the detail pane (defect 4).

        Same semantics as the Edition edit: staged state only, decision-log
        entry, undoable, no export side effect.
        """
        entry = self._entry_for_detail_edit()
        if entry is None:
            return
        old_value = entry.group
        self._edit_field(entry, "group", text)
        self._record_metadata_edit(entry, "group", old_value)
        self._refresh_table()
        self.state_changed.emit()

    def _entry_for_detail_edit(self) -> Optional["StagedReleaseEntry"]:
        """Resolve the entry the detail pane is currently showing.

        Uses the stable release_key identity (selected row, then last shown)
        so the edit always lands on the release the operator is looking at.
        """
        if self._state.current_library is None:
            return None
        release_key = self._state.selected_release_key
        if release_key is None:
            # Detail pane populated but no selection recorded: resolve from
            # the selected row via UserRole identity.
            selected = self._table.selectionModel().selectedRows()
            if selected:
                row = selected[0].row()
                for col in range(self._table.columnCount()):
                    item = self._table.item(row, col)
                    if item is not None:
                        release_key = item.data(Qt.ItemDataRole.UserRole)
                        if release_key:
                            break
        if release_key is None:
            return None
        return self._state.current_library.releases.get(release_key)

    def _show_context_menu(self, pos) -> None:
        """Show context menu for the table."""
        if self._state.current_library is None:
            return

        index = self._table.indexAt(pos)
        if not index.isValid():
            return

        # Select the row if not already selected
        self._table.selectRow(index.row())

        menu = self._build_context_menu()
        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _build_context_menu(self) -> QMenu:
        """Build the table's context menu (pure construction, testable).

        Kept separate from :meth:`_show_context_menu` so tests can inspect
        the built menu (and its actions) without running a modal
        ``QMenu.exec`` -- which the offscreen Qt platform cannot display.
        """
        menu = QMenu(self)

        # State actions
        state_menu = menu.addMenu("Set State")
        for state in [StagedState.PENDING, StagedState.ACCEPTED, StagedState.REJECTED, StagedState.MODIFIED]:
            action = QAction(state.value, self)
            action.triggered.connect(lambda checked, s=state: self._set_selected_state(s))
            state_menu.addAction(action)

        menu.addSeparator()

        # Rename
        rename_action = QAction("Rename…", self)
        rename_action.triggered.connect(self._on_rename)
        menu.addAction(rename_action)

        # Move to folder
        move_action = QAction("Move to Folder…", self)
        move_action.triggered.connect(self._on_move_to_folder)
        menu.addAction(move_action)

        # Create folder and move
        create_folder_action = QAction("Create Folder & Move…", self)
        create_folder_action.triggered.connect(self._on_create_folder_and_move)
        menu.addAction(create_folder_action)

        menu.addSeparator()

        # Move ADF(s) to Release...
        move_adfs_action = QAction("Move ADF(s) to Release…", self)
        move_adfs_action.triggered.connect(self._on_move_adfs_to_release)
        menu.addAction(move_adfs_action)

        # Merge Release Into...
        merge_release_action = QAction("Merge Release Into…", self)
        merge_release_action.triggered.connect(self._on_merge_release_into)
        menu.addAction(merge_release_action)

        menu.addSeparator()

        # Lookup actions
        lookup_menu = menu.addMenu("Lookup")
        online_lookup = QAction("Online Lookup…", self)
        online_lookup.triggered.connect(lambda: self._on_lookup("online"))
        lookup_menu.addAction(online_lookup)

        offline_lookup = QAction("Offline/Local Lookup…", self)
        offline_lookup.triggered.connect(lambda: self._on_lookup("offline"))
        lookup_menu.addAction(offline_lookup)

        alternate_lookup = QAction("Alternate Search…", self)
        alternate_lookup.triggered.connect(self._on_alternate_lookup)
        lookup_menu.addAction(alternate_lookup)

        menu.addSeparator()

        # Match actions
        match_menu = menu.addMenu("Match")
        accept_match = QAction("Accept Match", self)
        accept_match.triggered.connect(self._on_accept_match)
        match_menu.addAction(accept_match)

        reject_match = QAction("Reject Match", self)
        reject_match.triggered.connect(self._on_reject_match)
        match_menu.addAction(reject_match)

        metadata_only = QAction("Accept Metadata Only", self)
        metadata_only.triggered.connect(self._on_accept_metadata_only)
        match_menu.addAction(metadata_only)

        keep_filename = QAction("Keep Current Filename", self)
        keep_filename.triggered.connect(self._on_keep_filename)
        match_menu.addAction(keep_filename)

        menu.addSeparator()

        # Artwork/Manual
        artwork_menu = menu.addMenu("Artwork / Manual")
        select_artwork = QAction("Select Artwork…", self)
        select_artwork.triggered.connect(self._on_select_artwork)
        artwork_menu.addAction(select_artwork)

        select_manual = QAction("Select RTFM/Manual…", self)
        select_manual.triggered.connect(self._on_select_manual)
        artwork_menu.addAction(select_manual)

        menu.addSeparator()

        # Exclude/Restore
        exclude_action = QAction("Exclude from Export", self)
        exclude_action.triggered.connect(lambda: self._set_selected_state(StagedState.REJECTED))
        menu.addAction(exclude_action)

        restore_action = QAction("Restore to Export", self)
        restore_action.triggered.connect(lambda: self._set_selected_state(StagedState.PENDING))
        menu.addAction(restore_action)

        # (GH-99, defect 8) Review: actionable workflow for needs_review rows.
        # Explains why review is required, shows the staged candidate, and
        # resolves to an accepted/rejected decision — never a dead end.
        review_action = QAction("Review…", self)
        review_action.triggered.connect(lambda: self._on_review_selected())
        menu.addAction(review_action)

        menu.addSeparator()

        # Show source
        show_source = QAction("Show Source", self)
        show_source.triggered.connect(self._on_show_source)
        menu.addAction(show_source)

        # Explain match
        explain_action = QAction("Explain Match / Provenance", self)
        explain_action.triggered.connect(self._on_explain_match)
        menu.addAction(explain_action)

        return menu

    def _show_detail(self, entry: StagedReleaseEntry) -> None:
        """Show detail for the selected release entry."""
        self._detail_key.setText(entry.release_key)
        self._detail_title.setText(entry.title)
        # (GH-99, defect 4) Edition/Group are editable QLineEdits. Block their
        # textChanged signals while populating so a detail refresh never creates
        # a spurious METADATA_EDIT undo entry.
        self._detail_edition.blockSignals(True)
        self._detail_group.blockSignals(True)
        self._detail_edition.setText(entry.edition or "")
        self._detail_group.setText(entry.group or "")
        self._detail_edition.blockSignals(False)
        self._detail_group.blockSignals(False)
        self._detail_state.setText(entry.curation_state.value)
        self._detail_confidence.setText(f"{entry.confidence:.2f}" if entry.confidence > 0 else "(none)")
        self._detail_adf_count.setText(str(len(entry.adf_files)))

        artwork_str = ""
        if entry.artwork_front:
            artwork_str += f"Front: {entry.artwork_front}"
        if entry.artwork_back:
            if artwork_str:
                artwork_str += "; "
            artwork_str += f"Back: {entry.artwork_back}"
        if entry.artwork_spine:
            if artwork_str:
                artwork_str += "; "
            artwork_str += f"Spine: {entry.artwork_spine}"
        if entry.artwork_other:
            if artwork_str:
                artwork_str += "; "
            artwork_str += f"Other: {', '.join(entry.artwork_other)}"
        self._detail_artwork.setText(artwork_str or "(none)")

        rtfm_str = ""
        if entry.rtfm_files:
            rtfm_str = ", ".join(entry.rtfm_files)
        self._detail_rtfm.setText(rtfm_str or "(none)")

        # Provenance
        prov_parts = []
        if entry.metadata_source:
            prov_parts.append(f"Metadata: {entry.metadata_source}")
        if entry.match_confidence:
            prov_parts.append(f"Match: {entry.match_confidence:.2f}")
        if entry.locked_fields:
            prov_parts.append(f"Locked: {', '.join(entry.locked_fields)}")
        self._detail_provenance.setText("; ".join(prov_parts) if prov_parts else "(none)")

        self._detail_notes.setText(entry.notes or "")

        # Show action history
        actions_text = ""
        for action in entry.actions:
            actions_text += f"[{action.timestamp}] {action.action.value}: {action.details}\n"
        self._actions_list.setText(actions_text or "(no actions recorded)")

        # Load artwork preview (default to front/active screenshot)
        self._load_artwork_preview(entry)

    def _load_artwork_preview(self, entry: StagedReleaseEntry) -> None:
        """Load artwork preview, defaulting to active screenshot."""
        # Try front artwork first (active screenshot), then others
        artwork_path = entry.artwork_front or (entry.artwork_other[0] if entry.artwork_other else None)

        if artwork_path and Path(artwork_path).exists():
            pixmap = QPixmap(artwork_path)
            if not pixmap.isNull():
                # Scale to fit preview area
                scaled = pixmap.scaled(
                    self._artwork_preview.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
                self._artwork_preview.setPixmap(scaled)
                return

        self._artwork_preview.setText("No artwork available")
        self._artwork_preview.setPixmap(QPixmap())

    def _set_selected_state(self, state: StagedState) -> None:
        """Set the curation state for the selected release(s)."""
        if self._state.current_library is None:
            return

        selected = self._table.selectionModel().selectedRows()
        for idx in selected:
            row = idx.row()
            # Resolve release_key from item UserRole data (stable identity)
            release_key = None
            for col in range(self._table.columnCount()):
                item = self._table.item(row, col)
                if item is not None:
                    release_key = item.data(Qt.ItemDataRole.UserRole)
                    if release_key:
                        break
            if release_key:
                entry = self._state.current_library.releases.get(release_key)
                if entry:
                    old_state = entry.curation_state
                    entry.curation_state = state
                    action = StagedChange(
                        action=CurationAction.STATE_CHANGE,
                        timestamp=__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
                        details=f"State changed from {old_state.value} to {state.value}",
                    )
                    entry.actions.append(action)
                    self._record_action_for_undo(release_key, action)

        self._refresh_table()
        self._update_summary()
        self.state_changed.emit()

    def _record_action_for_undo(self, release_key: str, action: StagedChange) -> None:
        """Record an action for undo/redo."""
        self._undo_stack.append((release_key, action))
        self._redo_stack.clear()  # Clear redo stack on new action
        self._undo_btn.setEnabled(True)
        self._redo_btn.setEnabled(False)

    # --- GH-99 defect 8: needs_review -> Review -> resolve workflow ---

    def _on_review_selected(self) -> None:
        """Open the Review dialog for the selected release(s) (defect 8).

        The dialog explains WHY review is required (line-oriented, from the
        staged decision log), shows the staged candidate, and offers
        Approve / Reject. Both decisions update the staged curation state and
        are recorded as REVIEW_RESOLVED decision-log entries — a
        needs_review row is never a dead end.
        """
        if self._state.current_library is None:
            return

        selected = self._table.selectionModel().selectedRows()
        if not selected:
            return

        # Resolve the primary entry via stable UserRole identity (first row).
        row = selected[0].row()
        release_key = None
        for col in range(self._table.columnCount()):
            item = self._table.item(row, col)
            if item is not None:
                release_key = item.data(Qt.ItemDataRole.UserRole)
                if release_key:
                    break
        if not release_key:
            return
        entry = self._state.current_library.releases.get(release_key)
        if entry is None:
            return

        reasons = self._review_reasons(entry)
        candidate = self._review_candidate(entry)

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Review: {entry.title or entry.release_key}")
        dialog.resize(560, 420)
        layout = QVBoxLayout(dialog)

        if entry.curation_state == StagedState.NEEDS_REVIEW:
            layout.addWidget(QLabel("This release requires review before it can be accepted:"))
        else:
            layout.addWidget(QLabel(f"Reviewing release (current state: {entry.curation_state.value}):"))

        if reasons:
            why_edit = QTextEdit()
            why_edit.setReadOnly(True)
            why_edit.setPlainText("\n".join(reasons))
            layout.addWidget(QLabel("Why review is required:"))
            layout.addWidget(why_edit)
        else:
            layout.addWidget(QLabel("No specific review reason recorded in the decision log."))

        layout.addWidget(QLabel("Staged candidate:"))
        cand_edit = QTextEdit()
        cand_edit.setReadOnly(True)
        cand_edit.setPlainText(candidate)
        layout.addWidget(cand_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Yes | QDialogButtonBox.StandardButton.No
            | QDialogButtonBox.StandardButton.Cancel
        )
        yes_btn = buttons.button(QDialogButtonBox.StandardButton.Yes)
        no_btn = buttons.button(QDialogButtonBox.StandardButton.No)
        cancel_btn = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        yes_btn.setText("Approve")
        no_btn.setText("Reject")
        cancel_btn.setText("Cancel")
        yes_btn.setToolTip("Accept the staged release as-is (state: accepted)")
        no_btn.setToolTip("Reject this release from export (state: rejected)")

        decision: dict[str, Optional[str]] = {"value": None}

        def _choose(kind: str) -> None:
            decision["value"] = kind
            dialog.accept()

        yes_btn.clicked.connect(lambda: _choose("approve"))
        no_btn.clicked.connect(lambda: _choose("reject"))
        cancel_btn.clicked.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        kind = decision["value"]
        if kind == "approve":
            new_state = StagedState.ACCEPTED
            decision_word = "approved"
        elif kind == "reject":
            new_state = StagedState.REJECTED
            decision_word = "rejected"
        else:
            # Cancel or no explicit button: treat as no-op (state unchanged).
            return

        old_state = entry.curation_state
        entry.curation_state = new_state
        action = StagedChange(
            action=CurationAction.REVIEW_RESOLVED,
            timestamp=datetime.now(timezone.utc).isoformat(),
            details=(
                f"Review {decision_word}: state changed from {old_state.value} "
                f"to {new_state.value}"
            ),
        )
        entry.actions.append(action)
        self._record_action_for_undo(release_key, action)
        self._refresh_table()
        self._show_detail(entry)
        self.state_changed.emit()

    @staticmethod
    def _review_reasons(entry: "StagedReleaseEntry") -> list[str]:
        """Line-oriented reasons why this release needs review.

        Derived from the staged decision log: the most recent review-relevant
        actions (lookup applications that produced needs_review, match
        rejections, and state changes into needs_review) plus any review
        reason text captured in the entry's notes.
        """
        reasons: list[str] = []
        # Most recent review-relevant actions first (newest last in list).
        review_relevant = (
            CurationAction.ACCEPT_MATCH,
            CurationAction.REJECT_MATCH,
            CurationAction.METADATA_EDIT,
            CurationAction.STATE_CHANGE,
            CurationAction.REVIEW_RESOLVED,
        )
        for action in reversed(list(entry.actions)):
            if action.action in review_relevant:
                detail = action.details.strip()
                if detail and detail not in reasons:
                    reasons.append(detail)
        # Cap the list so the dialog stays readable.
        return reasons[:8]

    @staticmethod
    def _review_candidate(entry: "StagedReleaseEntry") -> str:
        """Render the staged candidate for operator approval (line-oriented)."""
        lines = [
            f"Title: {entry.title or '(none)'}",
            f"Release key: {entry.release_key}",
            f"Edition: {entry.edition or '(none)'}",
            f"Group: {entry.group or '(none)'}",
            f"Chipset: {entry.chipset or '(none)'}",
            f"Language: {entry.language or '(none)'}",
            f"Version: {entry.version or '(none)'}",
            f"Confidence: {entry.confidence:.2f}" if entry.confidence > 0 else "Confidence: (none)",
            f"Metadata source: {entry.metadata_source or '(none)'}",
            f"Match confidence: {entry.match_confidence:.2f}" if entry.match_confidence else "Match confidence: (none)",
            f"ADF files: {len(entry.adf_files)}",
            f"Folder: {entry.folder or '(derived)'}",
            f"State: {entry.curation_state.value}",
        ]
        if entry.notes:
            lines.append("Notes:")
            lines.append(entry.notes)
        return "\n".join(lines)

    def _on_undo(self) -> None:
        """Undo the last curation action."""
        if not self._undo_stack or self._state.current_library is None:
            return

        release_key, action = self._undo_stack.pop()
        entry = self._state.current_library.releases.get(release_key)
        if not entry:
            return

        # Reverse the action
        if action.action == CurationAction.STATE_CHANGE:
            # Parse old state from details
            try:
                old_state_str = action.details.split("from ")[1].split(" to ")[0]
                old_state = StagedState(old_state_str)
                entry.curation_state = old_state
            except (IndexError, ValueError):
                pass
        elif action.action == CurationAction.NOTE_ADDED:
            entry.notes = None
        elif action.action == CurationAction.RENAME:
            # Parse old title from details: "Renamed from 'old' to 'new'"
            try:
                old_title = action.details.split("from '")[1].split("' to ")[0]
                entry.title = old_title
            except (IndexError, ValueError):
                pass
        elif action.action == CurationAction.MOVE:
            # ADF move actions carry a payload (src/dst keys + file lists);
            # folder-move actions do not. Dispatch on payload first so the
            # two kinds of MOVE are never conflated.
            payload = None
            if action.payload:
                try:
                    payload = json.loads(action.payload)
                except (json.JSONDecodeError, TypeError):
                    payload = None
            if payload is not None and payload.get("moved_files") is not None:
                # ADF move: restore exact membership + source state.
                src_key = payload.get("src_key")
                dst_key = payload.get("dst_key")
                src_entry = self._state.current_library.releases.get(src_key)
                if src_entry:
                    src_entry.adf_files = list(payload.get("src_files_before", []))
                    if "src_curation_state" in payload:
                        try:
                            src_entry.curation_state = StagedState(payload["src_curation_state"])
                        except ValueError:
                            pass
                dst_entry = self._state.current_library.releases.get(dst_key)
                if dst_entry:
                    dst_entry.adf_files = list(payload.get("dst_files_before", []))
            else:
                # Folder move: parse previous folder from details.
                try:
                    if "from folder: " in action.details:
                        previous_folder = action.details.split("from folder: ")[1].split(" to folder: ")[0]
                        entry.folder = previous_folder if previous_folder != "None" else None
                    else:
                        # Legacy format: just clear the folder
                        entry.folder = None
                except (IndexError, ValueError):
                    entry.folder = None
        elif action.action == CurationAction.KEEP_FILENAME:
            # Remove filename from locked_fields
            if "filename" in (entry.locked_fields or []):
                entry.locked_fields.remove("filename")
        elif action.action == CurationAction.ACCEPT_MATCH:
            # Remove the locks added by accept match (if they were added by us)
            # Only remove if they weren't there before - we can't know, so we leave them
            # This is a limitation - ideally we'd track which locks we added
            pass
        elif action.action == CurationAction.ACCEPT_METADATA_ONLY:
            # Remove filename from locked_fields
            if "filename" in (entry.locked_fields or []):
                entry.locked_fields.remove("filename")
        elif action.action == CurationAction.MERGE:
            # Use payload for reliable undo of merge operations
            if action.payload:
                try:
                    payload = json.loads(action.payload)
                    src_key = payload.get("src_key")
                    dst_key = payload.get("dst_key")
                    src_files_before = payload.get("src_files_before", [])
                    dst_files_before = payload.get("dst_files_before", [])
                    src_curation_state = payload.get("src_curation_state", "pending")
                    
                    # Restore source
                    src_entry = self._state.current_library.releases.get(src_key)
                    if src_entry:
                        src_entry.adf_files = src_files_before
                        src_entry.curation_state = StagedState(src_curation_state)
                    
                    # Restore destination
                    dst_entry = self._state.current_library.releases.get(dst_key)
                    if dst_entry:
                        dst_entry.adf_files = dst_files_before
                except (json.JSONDecodeError, KeyError, ValueError):
                    pass
        elif action.action == CurationAction.METADATA_EDIT:
            # Two kinds share this action (GH-99):
            #  * field_edit: staged Edition/Group edit — restore the pre-edit
            #    value of exactly that field from the payload.
            #  * lookup apply: restore the pre-apply snapshot (title, source,
            #    confidence, state, artwork) from the payload.
            if action.payload:
                try:
                    payload = json.loads(action.payload)
                    if payload.get("kind") == "field_edit":
                        field = payload.get("field")
                        if field in ("edition", "group"):
                            setattr(entry, field, (payload.get("pre") or {}).get(field))
                    else:
                        self._restore_metadata_snapshot(entry, payload.get("pre") or {})
                except (json.JSONDecodeError, TypeError):
                    pass
        elif action.action == CurationAction.REVIEW_RESOLVED:
            # Parse old state from details:
            # "Review approved: state changed from needs_review to accepted"
            try:
                old_state = StagedState(action.details.split(" from ")[1].split(" to ")[0])
                entry.curation_state = old_state
            except (IndexError, ValueError):
                pass
        # Add more undo cases as needed

        # Move to redo stack
        self._redo_stack.append((release_key, action))
        self._refresh_table()
        self._update_summary()
        self._undo_btn.setEnabled(len(self._undo_stack) > 0)
        self._redo_btn.setEnabled(len(self._redo_stack) > 0)
        self.state_changed.emit()

    def _on_redo(self) -> None:
        """Redo the last undone action."""
        if not self._redo_stack or self._state.current_library is None:
            return

        release_key, action = self._redo_stack.pop()
        entry = self._state.current_library.releases.get(release_key)
        if not entry:
            return

        # Re-apply the action
        if action.action == CurationAction.STATE_CHANGE:
            try:
                new_state_str = action.details.split("to ")[1]
                new_state = StagedState(new_state_str)
                entry.curation_state = new_state
            except (IndexError, ValueError):
                pass
        elif action.action == CurationAction.NOTE_ADDED:
            # Note was removed, re-add it
            pass
        elif action.action == CurationAction.RENAME:
            # Parse new title from details: "Renamed from 'old' to 'new'"
            try:
                new_title = action.details.split("to '")[1].split("'")[0]
                entry.title = new_title
            except (IndexError, ValueError):
                pass
        elif action.action == CurationAction.MOVE:
            # ADF move actions carry a payload (src/dst keys + file lists);
            # folder-move actions do not. Dispatch on payload first so the
            # two kinds of MOVE are never conflated.
            payload = None
            if action.payload:
                try:
                    payload = json.loads(action.payload)
                except (json.JSONDecodeError, TypeError):
                    payload = None
            if payload is not None and payload.get("moved_files") is not None:
                # ADF move: re-apply exact membership + post-move state.
                src_key = payload.get("src_key")
                dst_key = payload.get("dst_key")
                src_entry = self._state.current_library.releases.get(src_key)
                if src_entry:
                    src_entry.adf_files = list(payload.get("src_files_after", []))
                    if "src_state_after" in payload:
                        try:
                            src_entry.curation_state = StagedState(payload["src_state_after"])
                        except ValueError:
                            pass
                dst_entry = self._state.current_library.releases.get(dst_key)
                if dst_entry:
                    dst_entry.adf_files = list(payload.get("dst_files_after", []))
            else:
                # Folder move: parse new folder from details.
                try:
                    if "to folder: " in action.details:
                        new_folder = action.details.split("to folder: ")[1]
                        entry.folder = new_folder
                    else:
                        # Legacy format: no folder to restore
                        pass
                except (IndexError, ValueError):
                    pass
        elif action.action == CurationAction.KEEP_FILENAME:
            # Add filename to locked_fields
            entry.locked_fields = entry.locked_fields or []
            if "filename" not in entry.locked_fields:
                entry.locked_fields.append("filename")
        elif action.action == CurationAction.ACCEPT_MATCH:
            # Re-add locks
            entry.locked_fields = entry.locked_fields or []
            if "title" not in entry.locked_fields:
                entry.locked_fields.append("title")
            if "folder" not in entry.locked_fields:
                entry.locked_fields.append("folder")
            if "group" not in entry.locked_fields:
                entry.locked_fields.append("group")
        elif action.action == CurationAction.ACCEPT_METADATA_ONLY:
            # Add filename lock
            entry.locked_fields = entry.locked_fields or []
            if "filename" not in entry.locked_fields:
                entry.locked_fields.append("filename")
        elif action.action == CurationAction.MERGE:
            # Use payload for reliable redo of merge operations
            if action.payload:
                try:
                    payload = json.loads(action.payload)
                    src_key = payload.get("src_key")
                    dst_key = payload.get("dst_key")
                    src_files_before = payload.get("src_files_before", [])
                    dst_files_before = payload.get("dst_files_before", [])
                    src_files_after = payload.get("src_files_after", [])
                    dst_files_after = payload.get("dst_files_after", [])
                    src_state_after = payload.get("src_state_after", "needs_review")

                    # Re-apply merge: restore exact post-merge membership + state.
                    src_entry = self._state.current_library.releases.get(src_key)
                    dst_entry = self._state.current_library.releases.get(dst_key)
                    if src_entry and dst_entry:
                        src_entry.adf_files = src_files_after
                        src_entry.curation_state = StagedState(src_state_after)
                        dst_entry.adf_files = dst_files_after
                except (json.JSONDecodeError, KeyError, ValueError):
                    pass
        elif action.action == CurationAction.METADATA_EDIT:
            # Re-apply (GH-99): field_edit re-applies the post-edit value of
            # that one field; lookup apply re-applies the post-apply snapshot.
            if action.payload:
                try:
                    payload = json.loads(action.payload)
                    if payload.get("kind") == "field_edit":
                        field = payload.get("field")
                        if field in ("edition", "group"):
                            setattr(entry, field, (payload.get("post") or {}).get(field))
                    else:
                        self._restore_metadata_snapshot(entry, payload.get("post") or {})
                except (json.JSONDecodeError, TypeError):
                    pass
        elif action.action == CurationAction.REVIEW_RESOLVED:
            # Parse new state from details:
            # "Review approved: state changed from needs_review to accepted"
            try:
                new_state = StagedState(action.details.split(" to ")[1])
                entry.curation_state = new_state
            except (IndexError, ValueError):
                pass

        # Move back to undo stack
        self._undo_stack.append((release_key, action))
        self._refresh_table()
        self._update_summary()
        self._undo_btn.setEnabled(len(self._undo_stack) > 0)
        self._redo_btn.setEnabled(len(self._redo_stack) > 0)
        self.state_changed.emit()

    def _on_add_note(self) -> None:
        """Add a note to the selected release."""
        if self._state.current_library is None or not self._state.selected_release_key:
            return

        entry = self._state.current_library.releases.get(self._state.selected_release_key)
        if not entry:
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Add Curation Note")
        dialog.resize(400, 200)
        layout = QVBoxLayout(dialog)

        note_edit = QTextEdit()
        note_edit.setPlainText(entry.notes or "")
        layout.addWidget(QLabel("Note:"))
        layout.addWidget(note_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_note = note_edit.toPlainText().strip()
            old_note = entry.notes
            entry.notes = new_note if new_note else None
            action = StagedChange(
                action=CurationAction.NOTE_ADDED,
                timestamp=__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
                details=f"Note {'added' if new_note else 'removed'}: {new_note[:50] if new_note else old_note[:50]}",
            )
            entry.actions.append(action)
            self._record_action_for_undo(self._state.selected_release_key, action)
            self._refresh_table()

    def _on_view_adf_list(self) -> None:
        """Show the full ADF file list for the selected release."""
        if self._state.current_library is None or not self._state.selected_release_key:
            return

        entry = self._state.current_library.releases.get(self._state.selected_release_key)
        if not entry:
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(f"ADF Files: {entry.title}")
        dialog.resize(500, 400)
        layout = QVBoxLayout(dialog)

        adf_list = QTextEdit()
        adf_list.setReadOnly(True)
        adf_list.setFontFamily("monospace")
        for adf in entry.adf_files:
            adf_list.append(adf)
        layout.addWidget(adf_list)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)

        dialog.exec()

    def _on_rename(self) -> None:
        """Rename the selected release."""
        if self._state.current_library is None or not self._state.selected_release_key:
            return

        entry = self._state.current_library.releases.get(self._state.selected_release_key)
        if not entry:
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Rename Release")
        dialog.resize(400, 150)
        layout = QVBoxLayout(dialog)

        form = QFormLayout()
        name_edit = QLineEdit(entry.title)
        name_edit.selectAll()
        form.addRow("New Title:", name_edit)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_title = name_edit.text().strip()
            if new_title and new_title != entry.title:
                # Check if title field is locked
                if "title" in (entry.locked_fields or []):
                    QMessageBox.warning(
                        self,
                        "Field Locked",
                        f"Cannot rename release: title field is locked.",
                    )
                    return
                old_title = entry.title
                entry.title = new_title
                action = StagedChange(
                    action=CurationAction.RENAME,
                    timestamp=__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
                    details=f"Renamed from '{old_title}' to '{new_title}'",
                )
                entry.actions.append(action)
                self._record_action_for_undo(self._state.selected_release_key, action)
                self._refresh_table()
                self._show_detail(entry)

    def _on_move_to_folder(self) -> None:
        """Move selected release(s) to a folder."""
        if self._state.current_library is None:
            return

        selected = self._table.selectionModel().selectedRows()
        if not selected:
            return

        folder, _ = QFileDialog.getExistingDirectory(self, "Select Destination Folder", "")
        if not folder:
            return

        for idx in selected:
            row = idx.row()
            # Resolve release_key from item UserRole data (stable identity)
            release_key = None
            for col in range(self._table.columnCount()):
                item = self._table.item(row, col)
                if item is not None:
                    release_key = item.data(Qt.ItemDataRole.UserRole)
                    if release_key:
                        break
            if release_key:
                entry = self._state.current_library.releases.get(release_key)
                if entry:
                    # Check if folder field is locked
                    if "folder" in (entry.locked_fields or []):
                        QMessageBox.warning(
                            self,
                            "Field Locked",
                            f"Cannot move release '{entry.title}': folder field is locked.",
                        )
                        continue
                    previous_folder = entry.folder
                    entry.folder = folder
                    action = StagedChange(
                        action=CurationAction.MOVE,
                        timestamp=__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
                        details=f"Moved from folder: {previous_folder} to folder: {folder}",
                    )
                    entry.actions.append(action)
                    self._record_action_for_undo(release_key, action)

        self._refresh_table()

    def _on_create_folder_and_move(self) -> None:
        """Create a new folder and move selected release(s) to it."""
        if self._state.current_library is None:
            return

        selected = self._table.selectionModel().selectedRows()
        if not selected:
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Create Folder & Move")
        dialog.resize(400, 120)
        layout = QVBoxLayout(dialog)

        form = QFormLayout()
        folder_edit = QLineEdit()
        form.addRow("New Folder Name:", folder_edit)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            folder = folder_edit.text().strip()
            if folder:
                for idx in selected:
                    row = idx.row()
                    release_key = None
                    for col in range(self._table.columnCount()):
                        item = self._table.item(row, col)
                        if item is not None:
                            release_key = item.data(Qt.ItemDataRole.UserRole)
                            if release_key:
                                break
                    if release_key:
                        entry = self._state.current_library.releases.get(release_key)
                        if entry:
                            # Check if folder field is locked
                            if "folder" in (entry.locked_fields or []):
                                QMessageBox.warning(
                                    self,
                                    "Field Locked",
                                    f"Cannot move release '{entry.title}': folder field is locked.",
                                )
                                continue
                            previous_folder = entry.folder
                            entry.folder = folder
                            action = StagedChange(
                                action=CurationAction.MOVE,
                                timestamp=__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
                                details=f"Created folder and moved from folder: {previous_folder} to folder: {folder}",
                            )
                            entry.actions.append(action)
                            self._record_action_for_undo(release_key, action)

                self._refresh_table()

    # --- Lookup (GH-88 + GH-89 shared online/offline implementation) ---------

    def _resolve_lookup_context(self, entry: "StagedReleaseEntry") -> Optional[LookupContext]:
        """Build the execution context for a lookup on ``entry``.

        Prefers the injected :attr:`_lookup_context_provider` (the main window
        wires this to the app's resolved paths/config so the lookup reads the
        same metadata cache and ``[local_media]`` table the pipeline used).
        Falls back to resolving the app paths directly.
        """
        base: Optional[LookupContext] = None
        if self._lookup_context_provider is not None:
            try:
                base = self._lookup_context_provider()
            except Exception:
                base = None
        if base is None:
            try:
                from ..paths import resolve_config

                paths_cfg, source = resolve_config()
                base = LookupContext(
                    cache_dir=paths_cfg.metadata_cache_dir,
                    curated_dir=paths_cfg.curated_metadata_dir,
                    config_path=source.config_path,
                )
            except Exception:
                base = LookupContext()
        base.query = entry.title or ""
        base.release_key = entry.release_key
        base.title = entry.title or ""
        base.disk_stems = [Path(f).stem for f in (entry.adf_files or [])]
        return base

    def _start_lookup(
        self,
        entry: "StagedReleaseEntry",
        mode: str,
        query: str,
        worker_result: dict,
        worker_error: dict,
    ) -> bool:
        """Start a background lookup for ``entry`` (no-op if one is running)."""
        if self._lookup_worker is not None and self._lookup_worker.isRunning():
            return False
        ctx = self._resolve_lookup_context(entry)
        if ctx is not None and query:
            ctx.query = query
        worker = PreviewWorker(
            entry.release_key,
            query,
            mode,
            title=entry.title or "",
            disk_stems=[Path(f).stem for f in (entry.adf_files or [])],
            context=ctx,
            parent=self,
        )
        worker.lookup_completed.connect(
            lambda key, candidates, wr=worker_result: wr.update(
                candidates=candidates, key=key
            )
        )
        worker.lookup_failed.connect(
            lambda key, error, we=worker_error: we.update(key=key, error=error)
        )
        self._lookup_worker = worker
        worker.start()
        return True

    def _on_lookup(self, mode: str = "online") -> None:
        """Perform a real online/offline lookup for the selected release.

        Online and Offline lookups share ONE implementation
        (:mod:`amiga_adf_library_builder.lookup_workflow`); the mode only
        selects the provider class. The provider list shown here comes from
        :func:`lookup_workflow.providers_for_mode`, so an offline lookup never
        lists an online provider (GH-89) and no placeholder text remains.
        """
        if self._state.current_library is None or not self._state.selected_release_key:
            return
        entry = self._state.current_library.releases.get(self._state.selected_release_key)
        if not entry:
            return

        dialog = QDialog(self)
        mode_label = "Online" if mode in ("online", "alternate") else "Offline/Local"
        dialog.setWindowTitle(f"{mode_label} Lookup: {entry.title}")
        dialog.resize(560, 460)
        layout = QVBoxLayout(dialog)

        # Search query (alternate search lets the operator override it).
        form = QFormLayout()
        query_edit = QLineEdit(entry.title or "")
        query_edit.selectAll()
        form.addRow("Search Query:", query_edit)
        layout.addLayout(form)

        # Providers consulted -- derived from the shared classification, never
        # hardcoded, so online and offline never share a provider list.
        provider_lines = [
            f"  - {pid}" for pid in providers_for_mode(mode)
        ]
        provider_text = (
            "Local media sources (read-only, no network):\n"
            if mode == "offline"
            else "Online provider chain (network required):\n"
        ) + "\n".join(provider_lines)
        provider_label = QLabel(provider_text)
        provider_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(provider_label)

        status_label = QLabel("Starting lookup…")
        layout.addWidget(status_label)

        results_label = QLabel("Results:")
        layout.addWidget(results_label)
        results_list = QTextEdit()
        results_list.setReadOnly(True)
        results_list.setFontFamily("monospace")
        layout.addWidget(results_list)

        apply_btn = QPushButton("Apply to release")
        apply_btn.setEnabled(False)
        apply_btn.setToolTip(
            "Apply this lookup result to the selected release "
            "(staged curation state only)"
        )
        layout.addWidget(apply_btn)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)

        worker_result: dict = {}
        worker_error: dict = {}

        def _format_candidates(candidates: list[dict]) -> str:
            lines = []
            for cand in candidates:
                lines.append(f"Status: {cand.get('status', 'unknown')}")
                if cand.get("title"):
                    lines.append(f"Title: {cand.get('title')}")
                if cand.get("provider"):
                    lines.append(f"Provider: {cand.get('provider')}")
                if cand.get("year"):
                    lines.append(f"Year: {cand.get('year')}")
                if cand.get("developer"):
                    lines.append(f"Developer: {cand.get('developer')}")
                if cand.get("publisher"):
                    lines.append(f"Publisher: {cand.get('publisher')}")
                if cand.get("description"):
                    lines.append(f"Description: {cand.get('description')}")
                if cand.get("artwork_url"):
                    lines.append(f"Artwork URL: {cand.get('artwork_url')}")
                if cand.get("local_cached_path"):
                    lines.append(f"Local artwork: {cand.get('local_cached_path')}")
                if cand.get("local_category"):
                    lines.append(f"Category: {cand.get('local_category')}")
                if cand.get("local_outcome"):
                    lines.append(f"Outcome: {cand.get('local_outcome')}")
                if cand.get("local_review_reason"):
                    lines.append(f"Review reason: {cand.get('local_review_reason')}")
                lines.append(f"Confidence: {cand.get('confidence', 0.0):.2f}")
                consulted = cand.get("consulted") or []
                if consulted:
                    lines.append("Consulted: " + ", ".join(consulted))
                local_state = cand.get("local_source_state") or []
                if local_state:
                    lines.append("Local source state:")
                    lines.extend(f"  {line}" for line in local_state)
                lines.append("")
            return "\n".join(lines) if lines else "(no results)"

        def _on_worker_finished() -> None:
            if worker_result:
                status_label.setText(f"Lookup complete: {worker_result.get('key', '')}")
                results_list.setText(_format_candidates(worker_result.get("candidates", [])))
                apply_btn.setEnabled(
                    bool(worker_result) and worker_result.get("candidates", [{}])[0].get("status") in ("found", "needs_review")
                )
            elif worker_error:
                status_label.setText("Lookup failed.")
                results_list.setText(
                    f"Lookup failed: {worker_error.get('error', 'unknown error')}\n"
                )
            else:
                status_label.setText("Lookup complete (no results).")

        def _on_apply() -> None:
            candidates = worker_result.get("candidates") or []
            if not candidates:
                return
            try:
                self._apply_lookup_candidate(entry, mode, candidates[0])
            except ValueError as exc:
                QMessageBox.warning(self, "Apply Lookup", str(exc))
                return
            apply_btn.setEnabled(False)
            status_label.setText("Result applied to staged curation state.")

        apply_btn.clicked.connect(_on_apply)

        if not self._start_lookup(entry, mode, query_edit.text().strip(), worker_result, worker_error):
            status_label.setText("A lookup is already running; please wait for it to finish.")
            results_list.setText("(lookup in progress in another dialog)")
            apply_btn.setEnabled(False)

        dialog.finished.connect(lambda _code: None)
        # The worker updates the dialog when it finishes; the dialog stays
        # modal-less so the user can read results as they arrive.
        worker = self._lookup_worker
        if worker is not None:
            worker.finished.connect(_on_worker_finished)

        dialog.show()
        # Keep a reference so the dialog (and its worker) are not garbage
        # collected mid-lookup; released on close.
        dialog.finished.connect(lambda _code: self._release_lookup_worker())

    def _release_lookup_worker(self) -> None:
        worker = self._lookup_worker
        if worker is not None and not worker.isRunning():
            worker.deleteLater()
            self._lookup_worker = None

    # --- Apply lookup result (staged curation state only) --------------------

    @staticmethod
    def _metadata_snapshot(entry: "StagedReleaseEntry") -> dict:
        """Snapshot of exactly the fields a lookup apply may change."""
        return {
            "title": entry.title,
            "metadata_source": entry.metadata_source,
            "match_confidence": entry.match_confidence,
            "confidence": entry.confidence,
            "artwork_front": entry.artwork_front,
            "curation_state": entry.curation_state.value,
        }

    @staticmethod
    def _restore_metadata_snapshot(entry: "StagedReleaseEntry", snap: dict) -> None:
        """Restore one pre/post snapshot onto ``entry`` (undo/redo support)."""
        entry.title = snap.get("title")
        entry.metadata_source = snap.get("metadata_source")
        entry.match_confidence = snap.get("match_confidence")
        entry.confidence = snap.get("confidence", 0.0) or 0.0
        entry.artwork_front = snap.get("artwork_front")
        try:
            entry.curation_state = StagedState(snap.get("curation_state") or "pending")
        except (TypeError, ValueError):
            pass

    def _apply_lookup_candidate(
        self, entry: "StagedReleaseEntry", mode: str, candidate: dict
    ) -> None:
        """Apply one lookup candidate to the staged entry.

        Mutates STAGED curation state only: the in-memory release entry and
        its decision-log action. No export artifacts are written and no ADF /
        original files are touched -- that happens only at an explicit Export.
        Release identity (release_key, edition, group, adf_files, folder) is
        never changed by a lookup apply.
        """
        status = candidate.get("status")
        if status not in ("found", "needs_review"):
            raise ValueError(
                f"lookup candidate has no applicable result (status: {status!r})"
            )
        kind = candidate.get("kind", "online")

        pre = self._metadata_snapshot(entry)
        if kind == "online":
            # Real provider-backed record: apply the canonical metadata
            # fields. The artwork URL is surfaced in the dialog but is NOT
            # written into path fields (artwork selection is a separate
            # curation action).
            entry.title = candidate.get("title") or entry.title
            entry.metadata_source = candidate.get("provider") or entry.metadata_source
            entry.match_confidence = candidate.get("confidence") or entry.match_confidence
            detail = (
                f"Online lookup applied: {entry.title!r} "
                f"(provider: {entry.metadata_source}, "
                f"conf {entry.match_confidence:.2f})"
            )
        else:
            # Offline: local LaunchBox media source. Never references an
            # online provider. auto_match has already cached the artwork
            # (LocalMediaProvider guarantee); needs_review only flags it.
            entry.metadata_source = "local_media"
            entry.match_confidence = candidate.get("confidence") or entry.match_confidence
            if candidate.get("local_cached_path"):
                entry.artwork_front = candidate["local_cached_path"]
            reason = candidate.get("local_review_reason") or ""
            detail = (
                f"Offline lookup applied: local media "
                f"{candidate.get('local_outcome') or 'match'} "
                f"(conf {entry.match_confidence:.2f})"
                + (f"; review: {reason}" if reason else "")
            )
        entry.confidence = entry.match_confidence or entry.confidence
        entry.curation_state = (
            StagedState.NEEDS_REVIEW if status == "needs_review" else StagedState.MODIFIED
        )
        post = self._metadata_snapshot(entry)

        action = StagedChange(
            action=CurationAction.METADATA_EDIT,
            timestamp=datetime.now(timezone.utc).isoformat(),
            details=detail,
            payload=json.dumps({"kind": kind, "pre": pre, "post": post}),
        )
        entry.actions.append(action)
        self._record_action_for_undo(entry.release_key, action)
        self._refresh_table()
        self._update_summary()
        self.state_changed.emit()

    def _on_alternate_lookup(self) -> None:
        """Perform alternate search with custom query."""
        self._on_lookup("alternate")

    def _on_accept_match(self) -> None:
        """Accept the matched metadata for selected release."""
        if self._state.current_library is None or not self._state.selected_release_key:
            return

        entry = self._state.current_library.releases.get(self._state.selected_release_key)
        if not entry:
            return

        entry.curation_state = StagedState.ACCEPTED
        # Only add to locked_fields if not already locked
        entry.locked_fields = entry.locked_fields or []
        if "title" not in entry.locked_fields:
            entry.locked_fields.append("title")
        if "folder" not in entry.locked_fields:
            entry.locked_fields.append("folder")
        if "group" not in entry.locked_fields:
            entry.locked_fields.append("group")
        action = StagedChange(
            action=CurationAction.ACCEPT_MATCH,
            timestamp=__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
            details="Accepted metadata match",
        )
        entry.actions.append(action)
        self._record_action_for_undo(self._state.selected_release_key, action)
        self._refresh_table()
        self._show_detail(entry)

    def _on_reject_match(self) -> None:
        """Reject the matched metadata for selected release."""
        if self._state.current_library is None or not self._state.selected_release_key:
            return

        entry = self._state.current_library.releases.get(self._state.selected_release_key)
        if not entry:
            return

        entry.curation_state = StagedState.NEEDS_REVIEW
        action = StagedChange(
            action=CurationAction.REJECT_MATCH,
            timestamp=__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
            details="Rejected metadata match",
        )
        entry.actions.append(action)
        self._record_action_for_undo(self._state.selected_release_key, action)
        self._refresh_table()
        self._show_detail(entry)

    def _on_accept_metadata_only(self) -> None:
        """Accept metadata only, keep current filename."""
        if self._state.current_library is None or not self._state.selected_release_key:
            return

        entry = self._state.current_library.releases.get(self._state.selected_release_key)
        if not entry:
            return

        entry.curation_state = StagedState.MODIFIED
        entry.locked_fields = entry.locked_fields or []
        # Preserve existing locked fields; only add filename lock for this action
        if "filename" not in entry.locked_fields:
            entry.locked_fields.append("filename")
        action = StagedChange(
            action=CurationAction.ACCEPT_METADATA_ONLY,
            timestamp=__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
            details="Accepted metadata only, kept current filename",
        )
        entry.actions.append(action)
        self._record_action_for_undo(self._state.selected_release_key, action)
        self._refresh_table()
        self._show_detail(entry)

    def _on_keep_filename(self) -> None:
        """Keep current filename, reject rename."""
        if self._state.current_library is None or not self._state.selected_release_key:
            return

        entry = self._state.current_library.releases.get(self._state.selected_release_key)
        if not entry:
            return

        entry.locked_fields = (entry.locked_fields or []) + ["filename"]
        action = StagedChange(
            action=CurationAction.KEEP_FILENAME,
            timestamp=__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
            details="Locked filename to prevent automatic rename",
        )
        entry.actions.append(action)
        self._record_action_for_undo(self._state.selected_release_key, action)
        self._show_detail(entry)

    def _on_select_artwork(self) -> None:
        """Select artwork for the selected release."""
        if self._state.current_library is None or not self._state.selected_release_key:
            return

        entry = self._state.current_library.releases.get(self._state.selected_release_key)
        if not entry:
            return

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Artwork",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.gif);;All files (*.*)"
        )
        if file_path:
            entry.artwork_front = file_path
            action = StagedChange(
                action=CurationAction.ARTWORK_SELECTED,
                timestamp=__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
                details=f"Selected front artwork: {file_path}",
            )
            entry.actions.append(action)
            self._record_action_for_undo(self._state.selected_release_key, action)
            self._show_detail(entry)

    def _on_select_manual(self) -> None:
        """Select RTFM/manual for the selected release."""
        if self._state.current_library is None or not self._state.selected_release_key:
            return

        entry = self._state.current_library.releases.get(self._state.selected_release_key)
        if not entry:
            return

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select RTFM/Manual",
            "",
            "Documents (*.pdf *.txt *.rtf *.html);;All files (*.*)"
        )
        if file_path:
            entry.rtfm_files = entry.rtfm_files or []
            entry.rtfm_files.append(file_path)
            action = StagedChange(
                action=CurationAction.MANUAL_SELECTED,
                timestamp=__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
                details=f"Selected RTFM/manual: {file_path}",
            )
            entry.actions.append(action)
            self._record_action_for_undo(self._state.selected_release_key, action)
            self._show_detail(entry)

    def _on_show_source(self) -> None:
        """Show the source ADF file location."""
        if self._state.current_library is None or not self._state.selected_release_key:
            return

        entry = self._state.current_library.releases.get(self._state.selected_release_key)
        if not entry or not entry.adf_files:
            QMessageBox.information(self, "Show Source", "No source files available.")
            return

        # Open the folder containing the first ADF file
        source_path = Path(entry.adf_files[0]).parent
        if source_path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(source_path)))
        else:
            QMessageBox.information(self, "Show Source", f"Source folder not found: {source_path}")

    def _on_explain_match(self) -> None:
        """Explain the match/provenance for the selected release."""
        if self._state.current_library is None or not self._state.selected_release_key:
            return

        entry = self._state.current_library.releases.get(self._state.selected_release_key)
        if not entry:
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Match Explanation: {entry.title}")
        dialog.resize(500, 400)
        layout = QVBoxLayout(dialog)

        explanation = QTextEdit()
        explanation.setReadOnly(True)
        explanation.setFontFamily("monospace")

        text = f"Release: {entry.title}\n"
        text += f"Release Key: {entry.release_key}\n"
        text += f"Group: {entry.group or '(none)'}\n"
        text += f"Edition: {entry.edition or '(none)'}\n"
        text += f"Curation State: {entry.curation_state.value}\n"
        text += f"Confidence: {entry.confidence:.2f}\n"
        text += f"Metadata Source: {entry.metadata_source or '(none)'}\n"
        text += f"Match Confidence: {entry.match_confidence:.2f if entry.match_confidence else '(none)'}\n"
        text += f"Locked Fields: {', '.join(entry.locked_fields) if entry.locked_fields else '(none)'}\n\n"
        text += "Actions History:\n"
        for action in entry.actions:
            text += f"  [{action.timestamp}] {action.action.value}: {action.details}\n"

        explanation.setText(text)
        layout.addWidget(explanation)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)

        dialog.exec()

    def _on_move_adfs_to_release(self) -> None:
        """Move explicitly selected ADF(s) from source release to destination release."""
        if self._state.current_library is None or not self._state.selected_release_key:
            return

        src_entry = self._state.current_library.releases.get(self._state.selected_release_key)
        if not src_entry or not src_entry.adf_files:
            QMessageBox.information(self, "Move ADF(s)", "No ADF files available in the selected release.")
            return

        # Build destination picker dialog with a real selectable ADF list
        dialog = QDialog(self)
        dialog.setWindowTitle("Move ADF(s) to Release")
        dialog.resize(520, 480)
        layout = QVBoxLayout(dialog)

        # Source info
        layout.addWidget(QLabel(f"Source: {src_entry.title} ({src_entry.release_key})"))
        layout.addWidget(QLabel(f"ADF files in source: {len(src_entry.adf_files)}"))

        # ADF selection (real selectable list — only checked items are moved)
        layout.addWidget(QLabel("Select ADF(s) to move (only checked items are moved):"))
        adf_box = QVBoxLayout()
        adf_checkboxes: list[QCheckBox] = []
        for adf in src_entry.adf_files:
            cb = QCheckBox(adf)
            cb.setChecked(True)
            adf_box.addWidget(cb)
            adf_checkboxes.append(cb)
        select_all = QCheckBox("Select all")
        select_all.setChecked(True)

        def sync_select_all(_checked: bool) -> None:
            if select_all.isChecked():
                for cb in adf_checkboxes:
                    cb.setChecked(True)

        def sync_checkboxes() -> None:
            select_all.setChecked(all(cb.isChecked() for cb in adf_checkboxes))

        for cb in adf_checkboxes:
            cb.toggled.connect(sync_checkboxes)
        select_all.toggled.connect(sync_select_all)
        adf_box.addWidget(select_all)
        adf_scroll = QScrollArea()
        adf_scroll.setWidgetResizable(True)
        adf_scroll_container = QWidget()
        adf_scroll_container.setLayout(adf_box)
        adf_scroll.setWidget(adf_scroll_container)
        adf_scroll.setMaximumHeight(180)
        layout.addWidget(adf_scroll)

        # Destination picker
        layout.addWidget(QLabel("Destination Release:"))
        dest_combo = QComboBox()
        dest_combo.setEditable(True)
        dest_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)

        # Populate with other releases (self-target protection: source excluded)
        for entry in self._state.current_library.releases.values():
            if entry.release_key != src_entry.release_key:
                dest_combo.addItem(f"{entry.title} ({entry.release_key}) [{len(entry.adf_files)} ADFs] [{entry.curation_state.value}]", entry.release_key)

        if dest_combo.count() == 0:
            QMessageBox.information(self, "Move ADF(s)", "No other releases available as destination.")
            return

        # Add search/filter
        search_edit = QLineEdit()
        search_edit.setPlaceholderText("Search destination by title, key, group...")
        layout.addWidget(search_edit)

        def filter_dest():
            search_text = search_edit.text().lower()
            for i in range(dest_combo.count()):
                text = dest_combo.itemText(i).lower()
                dest_combo.setItemHidden(i, search_text not in text)

        search_edit.textChanged.connect(filter_dest)
        layout.addWidget(dest_combo)

        # Preview area
        layout.addWidget(QLabel("Preview (destination after move):"))
        preview = QTextEdit()
        preview.setReadOnly(True)
        preview.setFontFamily("monospace")
        preview.setMaximumHeight(100)
        layout.addWidget(preview)

        def selected_adfs() -> list[str]:
            return [cb.text() for cb in adf_checkboxes if cb.isChecked()]

        def update_preview():
            idx = dest_combo.currentIndex()
            if idx >= 0:
                dst_key = dest_combo.itemData(idx)
                dst_entry = self._state.current_library.releases.get(dst_key)
                if dst_entry:
                    moved = selected_adfs()
                    text = f"Destination: {dst_entry.title} ({dst_entry.release_key})\n"
                    text += f"Selected ADFs: {len(moved)} of {len(src_entry.adf_files)}\n"
                    text += "ADF list after move:\n"
                    combined = list(dst_entry.adf_files) + moved
                    for i, adf in enumerate(combined):
                        text += f"  {i+1}. {adf}\n"
                    preview.setText(text)

        dest_combo.currentIndexChanged.connect(update_preview)
        for cb in adf_checkboxes:
            cb.toggled.connect(update_preview)
        update_preview()

        # Guard: empty selection must not execute
        ok_btn = QDialogButtonBox.StandardButton.Ok
        buttons = QDialogButtonBox(ok_btn | QDialogButtonBox.StandardButton.Cancel)
        ok_button = buttons.button(ok_btn)

        def guard_empty_selection() -> None:
            enabled = bool(selected_adfs())
            if ok_button is not None:
                ok_button.setEnabled(enabled)

        for cb in adf_checkboxes:
            cb.toggled.connect(guard_empty_selection)
        select_all.toggled.connect(lambda _c: guard_empty_selection())
        guard_empty_selection()

        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        dst_key = dest_combo.currentData()
        if not dst_key:
            return

        dst_entry = self._state.current_library.releases.get(dst_key)
        if not dst_entry:
            QMessageBox.critical(self, "Move ADF(s)", "Destination release not found.")
            return

        # Move ONLY the explicitly selected ADFs, preserving source order.
        selected = selected_adfs()
        if not selected:
            QMessageBox.information(self, "Move ADF(s)", "No ADFs selected. Nothing was moved.")
            return

        self._apply_move_adfs(src_entry.release_key, dst_key, selected)

    def _apply_move_adfs(self, src_key: str, dst_key: str, selected: list[str]) -> None:
        """Execute an ADF move for exactly the given selected filenames.

        Records a single coherent MOVE entry per side plus the undo payload.
        Raises ValueError on empty selection, self-target, missing source, or
        destination duplicates (duplicate prevention).
        """
        if self._state.current_library is None:
            raise ValueError("No staged library loaded")
        if not selected:
            raise ValueError("No ADF files selected to move")

        src_entry = self._state.current_library.releases.get(src_key)
        dst_entry = self._state.current_library.releases.get(dst_key)
        if not src_entry:
            raise ValueError(f"Source release not found: {src_key}")
        if not dst_entry:
            raise ValueError(f"Destination release not found: {dst_key}")
        if src_key == dst_key:
            raise ValueError("Source and destination release cannot be the same")

        # move_adfs validates membership, duplicate prevention, and order, and
        # records the decision-log entries (one MOVE per side, with the full
        # undo payload) on the model side.
        self._state.current_library.move_adfs(src_key, dst_key, list(selected))

        # Record the source's logged MOVE entry for undo exactly once. The
        # model already appended it (with the full payload); do NOT append a
        # duplicate entry — that was the duplicate-logging defect.
        src_moves = [a for a in src_entry.actions if a.action == CurationAction.MOVE]
        if not src_moves:
            raise ValueError("move_adfs did not record a MOVE action")
        self._record_action_for_undo(src_key, src_moves[-1])

        self._refresh_table()
        self._show_detail(dst_entry)  # Show destination detail after move
        self.state_changed.emit()
        self.status_message.emit(f"Moved {len(selected)} ADF(s) from {src_key} to {dst_key}")

    def _on_merge_release_into(self) -> None:
        """Merge entire source release into destination release."""
        if self._state.current_library is None or not self._state.selected_release_key:
            return

        src_entry = self._state.current_library.releases.get(self._state.selected_release_key)
        if not src_entry:
            return

        # Build destination picker dialog
        dialog = QDialog(self)
        dialog.setWindowTitle("Merge Release Into")
        dialog.resize(500, 400)
        layout = QVBoxLayout(dialog)

        # Source info
        layout.addWidget(QLabel(f"Source: {src_entry.title} ({src_entry.release_key})"))
        layout.addWidget(QLabel(f"ADF files in source: {len(src_entry.adf_files)}"))
        if src_entry.adf_files:
            adf_list = QTextEdit()
            adf_list.setReadOnly(True)
            adf_list.setFontFamily("monospace")
            for i, adf in enumerate(src_entry.adf_files):
                adf_list.append(f"  {i+1}. {adf}")
            adf_list.setMaximumHeight(150)
            layout.addWidget(adf_list)

        # Destination picker
        layout.addWidget(QLabel("Destination Release:"))
        dest_combo = QComboBox()
        dest_combo.setEditable(True)
        dest_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)

        for entry in self._state.current_library.releases.values():
            if entry.release_key != src_entry.release_key:
                dest_combo.addItem(f"{entry.title} ({entry.release_key}) [{len(entry.adf_files)} ADFs] [{entry.curation_state.value}]", entry.release_key)

        if dest_combo.count() == 0:
            QMessageBox.information(self, "Merge Release", "No other releases available as destination.")
            return

        search_edit = QLineEdit()
        search_edit.setPlaceholderText("Search destination by title, key, group...")
        layout.addWidget(search_edit)

        def filter_dest():
            search_text = search_edit.text().lower()
            for i in range(dest_combo.count()):
                text = dest_combo.itemText(i).lower()
                dest_combo.setItemHidden(i, search_text not in text)

        search_edit.textChanged.connect(filter_dest)
        layout.addWidget(dest_combo)

        # Merge summary preview
        layout.addWidget(QLabel("Merge Summary:"))
        preview = QTextEdit()
        preview.setReadOnly(True)
        preview.setFontFamily("monospace")
        preview.setMaximumHeight(150)
        layout.addWidget(preview)

        def update_preview():
            idx = dest_combo.currentIndex()
            if idx >= 0:
                dst_key = dest_combo.itemData(idx)
                dst_entry = self._state.current_library.releases.get(dst_key)
                if dst_entry:
                    text = f"Destination: {dst_entry.title} ({dst_entry.release_key})\n"
                    text += f"Destination ADFs: {len(dst_entry.adf_files)}\n"
                    text += f"Source ADFs: {len(src_entry.adf_files)}\n"
                    text += f"Combined ADFs: {len(dst_entry.adf_files) + len(src_entry.adf_files)}\n\n"
                    text += "Metadata promotion (destination wins, blank fields from source):\n"
                    if not dst_entry.title and src_entry.title:
                        text += f"  Title: '{src_entry.title}' (promoted from source)\n"
                    if not dst_entry.edition and src_entry.edition:
                        text += f"  Edition: '{src_entry.edition}' (promoted from source)\n"
                    if not dst_entry.group and src_entry.group:
                        text += f"  Group: '{src_entry.group}' (promoted from source)\n"
                    if not dst_entry.chipset and src_entry.chipset:
                        text += f"  Chipset: '{src_entry.chipset}' (promoted from source)\n"
                    if not dst_entry.language and src_entry.language:
                        text += f"  Language: '{src_entry.language}' (promoted from source)\n"
                    if not dst_entry.version and src_entry.version:
                        text += f"  Version: '{src_entry.version}' (promoted from source)\n"
                    if not dst_entry.alt_marker and src_entry.alt_marker:
                        text += f"  Alt Marker: '{src_entry.alt_marker}' (promoted from source)\n"
                    if dst_entry.title and not src_entry.title:
                        text += f"  Title: '{dst_entry.title}' (kept from destination)\n"
                    preview.setText(text)

        dest_combo.currentIndexChanged.connect(update_preview)
        update_preview()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        dst_key = dest_combo.currentData()
        if not dst_key:
            return

        dst_entry = self._state.current_library.releases.get(dst_key)
        if not dst_entry:
            QMessageBox.critical(self, "Merge Release", "Destination release not found.")
            return

        try:
            self._apply_merge_release(src_entry.release_key, dst_entry.release_key)
        except ValueError as e:
            QMessageBox.critical(self, "Merge Release Failed", str(e))

    def _apply_merge_release(self, src_key: str, dst_key: str) -> None:
        """Merge the entire source release into the destination release.

        Delegates to the model (which records one MERGE entry per side with
        the full undo payload) and records the source's logged MERGE entry
        for undo exactly once. Raises ValueError on self-target, missing
        releases, or destination duplicates.
        """
        if self._state.current_library is None:
            raise ValueError("No staged library loaded")

        src_entry = self._state.current_library.releases.get(src_key)
        dst_entry = self._state.current_library.releases.get(dst_key)
        if not src_entry:
            raise ValueError(f"Source release not found: {src_key}")
        if not dst_entry:
            raise ValueError(f"Destination release not found: {dst_key}")
        if src_key == dst_key:
            raise ValueError("Source and destination release cannot be the same")

        moved_count = len(src_entry.adf_files)
        src_title = src_entry.title
        dst_title = dst_entry.title

        # merge_release moves all ADFs, promotes metadata, empties the source
        # (NEEDS_REVIEW), and records the decision-log entries (one MERGE per
        # side, with the full undo payload) on the model side.
        self._state.current_library.merge_release(src_key, dst_key)

        # Record the source's logged MERGE entry for undo exactly once. The
        # model already appended it (with the full payload); do NOT append a
        # duplicate entry — that was the duplicate-logging defect.
        src_merges = [a for a in src_entry.actions if a.action == CurationAction.MERGE]
        if not src_merges:
            raise ValueError("merge_release did not record a MERGE action")
        self._record_action_for_undo(src_key, src_merges[-1])

        self._refresh_table()
        self._show_detail(dst_entry)  # Show destination detail after merge
        self.state_changed.emit()
        self.status_message.emit(f"Merged release {src_key} into {dst_key} ({moved_count} ADFs)")

    def _update_summary(self) -> None:
        """Update the status label with summary counts."""
        if self._state.current_library is None:
            self._summary_label.setText("No library state loaded.")
            return

        counts = {state: 0 for state in StagedState}
        for entry in self._state.current_library.releases.values():
            counts[entry.curation_state] += 1

        total = len(self._state.current_library.releases)
        summary = (
            f"Total: {total} | "
            f"Accepted: {counts[StagedState.ACCEPTED]} | "
            f"Pending: {counts[StagedState.PENDING]} | "
            f"Rejected: {counts[StagedState.REJECTED]} | "
            f"Modified: {counts[StagedState.MODIFIED]} | "
            f"Needs Review: {counts[StagedState.NEEDS_REVIEW]}"
        )
        self._summary_label.setText(summary)

    # --- Public API ---

    def auto_save_state(self) -> bool:
        """(GH-99 defect 3) Atomically re-save the loaded staged library.

        Returns True when the current state was persisted to disk. Returns
        False when there is nothing to save (no loaded state) or the write
        failed. The main window calls this whenever ``state_changed`` fires
        so curation decisions survive across runs: the pipeline's
        carry_over restores them on the next build, keyed by release_key.
        """
        if self._state.current_library is None:
            return False
        manager = self._state.state_manager
        path = self._state.current_state_path
        if manager is None or path is None:
            return False
        try:
            return manager.save(self._state.current_library)
        except OSError as exc:
            self.status_message.emit(f"State save failed: {exc}")
            return False

    def load_state_file(self, path: Path) -> bool:
        """Load a state file programmatically."""
        try:
            self._state.state_manager = CurationStateManager(path)
            self._state.current_library = self._state.state_manager.load()
            self._state.current_state_path = path
            self._refresh_table()
            self._update_summary()
            return True
        except Exception:
            return False

    def get_current_library(self) -> Optional[StagedLibrary]:
        """Get the currently loaded staged library."""
        return self._state.current_library


# For backward compatibility with main_window.py integration
from PySide6.QtCore import QUrl