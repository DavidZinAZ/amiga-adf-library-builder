"""Library Preview & Curation workspace widget (GH-80).

This widget provides a full interactive workspace for reviewing and curating
the staged library. It loads a library state file (.library_state.json)
independently of the originals and export destination.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

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
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from amiga_adf_library_builder.library_state import (
    CurationStateFile,
    CurationStateManager,
    CURATION_STATE_SCHEMA_VERSION,
)
from amiga_adf_library_builder.models import (
    CurationAction,
    StagedChange,
    StagedLibrary,
    StagedReleaseEntry,
    StagedState,
)


class PreviewWorker(QThread):
    """Worker thread for background lookup/matching operations."""

    lookup_completed = Signal(str, list)  # release_key, candidates
    lookup_failed = Signal(str, str)  # release_key, error

    def __init__(self, release_key: str, query: str, provider: str, parent=None):
        super().__init__(parent)
        self.release_key = release_key
        self.query = query
        self.provider = provider

    def run(self) -> None:
        # This would integrate with the existing provider architecture
        # For now, emit a placeholder
        self.lookup_failed.emit(self.release_key, "Provider integration not yet implemented")


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
        self._filter_combo.addItems([
            "All",
            "Pending",
            "Accepted",
            "Rejected",
            "Modified",
            "Needs Review",
            "Missing Artwork",
            "Missing RTFM",
            "Unmatched",
            "Excluded",
        ])
        self._filter_combo.setToolTip("Filter releases by curation state")
        self._filter_combo.currentTextChanged.connect(self._apply_filter)
        filter_bar.addWidget(QLabel("Filter:"))
        filter_bar.addWidget(self._filter_combo)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search release key, title, group…")
        self._search.setToolTip("Filter by release key, title, or group name")
        self._search.textChanged.connect(self._apply_filter)
        filter_bar.addWidget(self._search)

        # Multi-select toggle
        self._multi_select_btn = QToolButton()
        self._multi_select_btn.setText("Multi-Select")
        self._multi_select_btn.setCheckable(True)
        self._multi_select_btn.setToolTip("Toggle multi-select mode for bulk operations")
        self._multi_select_btn.toggled.connect(self._on_multi_select_toggled)
        filter_bar.addWidget(self._multi_select_btn)

        return filter_bar

    def _build_table_view(self) -> QWidget:
        """Build the releases table view."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels([
            "State", "Release Key", "Title", "Edition", "Group", "Confidence"
        ])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setSortingEnabled(True)
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

        self._detail_edition = QLabel("")
        self._detail_edition.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        detail_layout.addRow("Edition:", self._detail_edition)

        self._detail_group = QLabel("")
        self._detail_group.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
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
            state_item.setData(Qt.ItemDataRole.UserRole, entry.curation_state.value)
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

            self._table.setItem(row, 1, QTableWidgetItem(entry.release_key))
            self._table.setItem(row, 2, QTableWidgetItem(entry.title))
            self._table.setItem(row, 3, QTableWidgetItem(entry.edition or ""))
            self._table.setItem(row, 4, QTableWidgetItem(entry.group or ""))
            self._table.setItem(row, 5, QTableWidgetItem(f"{entry.confidence:.2f}" if entry.confidence > 0 else ""))

        self._apply_filter()
        self._update_summary()

    def _apply_filter(self) -> None:
        """Apply the current filter and search to the table."""
        filter_text = self._filter_combo.currentText()
        search_text = self._search.text().strip().lower()

        for row in range(self._table.rowCount()):
            show = True

            # State filter
            if filter_text != "All":
                state_item = self._table.item(row, 0)
                if state_item and state_item.text() != filter_text:
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
            release_key = self._state.row_to_release_key.get(row)
            if release_key and self._state.current_library:
                entry = self._state.current_library.releases.get(release_key)
                if entry:
                    self._show_detail(entry)
                    self._state.selected_release_key = release_key

    def _on_multi_select_toggled(self, checked: bool) -> None:
        """Toggle multi-select mode."""
        if checked:
            self._table.setSelectionMode(QAbstractItemView.MultiSelection)
        else:
            self._table.setSelectionMode(QAbstractItemView.SingleSelection)

    def _show_context_menu(self, pos) -> None:
        """Show context menu for the table."""
        if self._state.current_library is None:
            return

        index = self._table.indexAt(pos)
        if not index.isValid():
            return

        # Select the row if not already selected
        self._table.selectRow(index.row())

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

        menu.addSeparator()

        # Show source
        show_source = QAction("Show Source", self)
        show_source.triggered.connect(self._on_show_source)
        menu.addAction(show_source)

        # Explain match
        explain_action = QAction("Explain Match / Provenance", self)
        explain_action.triggered.connect(self._on_explain_match)
        menu.addAction(explain_action)

        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _show_detail(self, entry: StagedReleaseEntry) -> None:
        """Show detail for the selected release entry."""
        self._detail_key.setText(entry.release_key)
        self._detail_title.setText(entry.title)
        self._detail_edition.setText(entry.edition or "(none)")
        self._detail_group.setText(entry.group or "(none)")
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
            release_key = self._state.row_to_release_key.get(row)
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
            # Parse previous folder from details: "Moved from folder: <old> to folder: <new>"
            # or "Created folder and moved from folder: <old> to folder: <new>"
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
            # Parse new folder from details: "Moved from folder: <old> to folder: <new>"
            # or "Created folder and moved from folder: <old> to folder: <new>"
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
            release_key = self._state.row_to_release_key.get(row)
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
                    release_key = self._state.row_to_release_key.get(row)
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

    def _on_lookup(self, mode: str = "online") -> None:
        """Perform online/offline lookup for the selected release."""
        if self._state.current_library is None or not self._state.selected_release_key:
            return

        entry = self._state.current_library.releases.get(self._state.selected_release_key)
        if not entry:
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(f"{mode.capitalize()} Lookup: {entry.title}")
        dialog.resize(500, 400)
        layout = QVBoxLayout(dialog)

        # Search query
        form = QFormLayout()
        query_edit = QLineEdit(entry.title)
        query_edit.selectAll()
        form.addRow("Search Query:", query_edit)
        layout.addLayout(form)

        # Results area
        results_label = QLabel("Results:")
        layout.addWidget(results_label)

        results_list = QTextEdit()
        results_list.setReadOnly(True)
        results_list.setFontFamily("monospace")
        layout.addWidget(results_list)

        # Placeholder - would integrate with actual provider
        results_list.setText(f"[{mode.capitalize()} lookup not yet implemented]\n"
                             f"Would search for: {query_edit.text()}\n"
                             f"Provider: Hall of Light / Launchbox / etc.")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        dialog.exec()

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