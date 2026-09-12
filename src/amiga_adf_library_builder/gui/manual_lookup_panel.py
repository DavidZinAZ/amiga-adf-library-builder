"""Qt panel for the unified manual lookup / source browser (GH-107 Slice 4).

Thin GUI over ``manual_lookup`` (service) + ``CanonicalLibrary`` (Slice 3
persistence). All precedence/explanation logic lives in the service; this
module only renders and forwards operator actions.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..canonical import CanonicalLibrary
from ..metadata_source import MetadataSourceManager
from ..manual_lookup import (
    apply_manual_override,
    build_entity_report,
    candidates_from_sources,
    disks_for,
    list_library_entities,
    releases_for,
    revert_manual_override,
)

_CANDIDATE_COLUMNS = [
    "Source", "Title", "Year", "Publisher", "Region", "SHA-1", "Record Key"
]
_CLAIM_COLUMNS = [
    "Value", "Source", "Authority", "Rank", "Confidence", "Observed",
    "Record Key", "URL", "Winner", "Manual"
]


class ManualLookupPanel(QWidget):
    """Unified manual lookup + source browser panel."""

    def __init__(self, canonical_db: Path, metadata_manager: MetadataSourceManager,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._db_path = Path(canonical_db)
        self._canon = CanonicalLibrary(self._db_path)
        self._manager = metadata_manager
        self._build_ui()
        self.refresh()

    def set_canonical_db(self, canonical_db: Path) -> None:
        """Point the panel at a (possibly different) canonical store."""
        canonical_db = Path(canonical_db)
        if canonical_db == self._db_path:
            return
        try:
            self._canon.close()
        except Exception:
            pass
        self._db_path = canonical_db
        self._canon = CanonicalLibrary(canonical_db)

    # --- UI construction -----------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Row 1: entity picker (Game -> Release -> Disk).
        sel = QHBoxLayout()
        sel.addWidget(QLabel("Game:"))
        self._game_combo = QComboBox(self)
        sel.addWidget(self._game_combo, 2)
        sel.addWidget(QLabel("Release:"))
        self._release_combo = QComboBox(self)
        sel.addWidget(self._release_combo, 2)
        sel.addWidget(QLabel("Disk:"))
        self._disk_combo = QComboBox(self)
        sel.addWidget(self._disk_combo, 2)
        self._refresh_btn = QPushButton("Reload")
        self._refresh_btn.setToolTip("Reload the canonical record picker from canonical.db")
        sel.addWidget(self._refresh_btn)
        layout.addLayout(sel)

        # Row 2: claims / precedence view for the selected entity.
        claim_box = QGroupBox("Canonical values, provenance and precedence")
        cl = QVBoxLayout(claim_box)
        self._claims_table = QTableWidget(self)
        self._claims_table.setColumnCount(1 + len(_CLAIM_COLUMNS))
        self._claims_table.setHorizontalHeaderLabels(_CLAIM_COLUMNS)
        self._claims_table.horizontalHeader().setStretchLastSection(True)
        self._claims_table.setAlternatingRowColors(True)
        self._claims_table.verticalHeader().setVisible(False)
        cl.addWidget(self._claims_table)
        self._status_label = QLabel("", self)
        cl.addWidget(self._status_label)
        layout.addWidget(claim_box, 3)

        # Row 3: manual override controls.
        ov = QHBoxLayout()
        ov.addWidget(QLabel("Field:"))
        self._field_combo = QComboBox(self)
        ov.addWidget(self._field_combo)
        ov.addWidget(QLabel("Value:"))
        self._value_edit = QLineEdit(self)
        self._value_edit.setPlaceholderText("Operator value for this field")
        ov.addWidget(self._value_edit, 1)
        self._apply_btn = QPushButton("Set / Override")
        self._apply_btn.setToolTip(
            "Record an operator curation claim (highest authority; survives provider refresh)"
        )
        ov.addWidget(self._apply_btn)
        self._revert_btn = QPushButton("Revert Override")
        self._revert_btn.setToolTip(
            "Remove only the manual claims for this field; automated claims remain"
        )
        ov.addWidget(self._revert_btn)
        layout.addLayout(ov)

        # Row 4: DAT source browser (read-only).
        src_box = QGroupBox("Metadata source browser (read-only DAT index)")
        sl = QVBoxLayout(src_box)
        brow = QHBoxLayout()
        self._query_edit = QLineEdit(self)
        self._query_edit.setPlaceholderText(
            "Search indexed DAT entries by title (exact SHA-1/MD5/CRC32 also match)"
        )
        brow.addWidget(self._query_edit, 1)
        self._search_btn = QPushButton("Search Sources")
        brow.addWidget(self._search_btn)
        self._use_query_btn = QPushButton("Use For Override")
        self._use_query_btn.setToolTip(
            "Copy the selected source row's title into the override value box"
        )
        brow.addWidget(self._use_query_btn)
        sl.addLayout(brow)
        self._candidates_table = QTableWidget(self)
        self._candidates_table.setColumnCount(len(_CANDIDATE_COLUMNS))
        self._candidates_table.setHorizontalHeaderLabels(_CANDIDATE_COLUMNS)
        self._candidates_table.horizontalHeader().setStretchLastSection(True)
        self._candidates_table.setAlternatingRowColors(True)
        self._candidates_table.verticalHeader().setVisible(False)
        sl.addWidget(self._candidates_table)
        layout.addWidget(src_box, 2)

        # Wire signals
        self._refresh_btn.clicked.connect(self.refresh)
        self._game_combo.currentIndexChanged.connect(self._on_game_selected)
        self._release_combo.currentIndexChanged.connect(self._on_release_selected)
        self._disk_combo.currentIndexChanged.connect(self._on_disk_selected)
        self._apply_btn.clicked.connect(self._on_apply_override)
        self._revert_btn.clicked.connect(self._on_revert_override)
        self._search_btn.clicked.connect(self._on_search_sources)
        self._use_query_btn.clicked.connect(self._on_use_query_value)

    # --- entity selection ------------------------------------------------------

    def refresh(self) -> None:
        """Reload all picker combos from the canonical model."""
        self._game_combo.blockSignals(True)
        self._game_combo.clear()
        self._release_combo.clear()
        self._disk_combo.clear()
        try:
            games = list_library_entities(self._canon)["games"]
        except Exception:  # unreadable DB: panel stays usable, not crashing
            games = []
        for g in games:
            self._game_combo.addItem(g["label"], g["entity_id"])
        self._game_combo.blockSignals(False)
        if self._game_combo.count():
            self._on_game_selected()

    def _on_game_selected(self) -> None:
        game_id = self._game_combo.currentData()
        self._release_combo.blockSignals(True)
        self._release_combo.clear()
        self._disk_combo.clear()
        if game_id:
            for r in releases_for(self._canon, game_id):
                self._release_combo.addItem(r["label"], r["entity_id"])
        self._release_combo.blockSignals(False)
        self._on_release_selected()

    def _on_release_selected(self) -> None:
        release_id = self._release_combo.currentData()
        self._disk_combo.blockSignals(True)
        self._disk_combo.clear()
        if release_id:
            for d in disks_for(self._canon, release_id):
                self._disk_combo.addItem(d["label"], d["entity_id"])
        self._disk_combo.blockSignals(False)
        self._on_disk_selected()

    def _on_disk_selected(self) -> None:
        self._refresh_claim_view()

    def _current_entity(self) -> tuple:
        """(entity_type, entity_id, fields) for the deepest active selection."""
        disk_id = self._disk_combo.currentData()
        release_id = self._release_combo.currentData()
        if disk_id and release_id:
            return "disk", disk_id, ["filename", "disk_number", "sha256", "size"]
        if release_id:
            game_id = self._game_combo.currentData()
            return "release", release_id, [
                "title", "edition", "region", "language", "publisher"
            ]
        game_id = self._game_combo.currentData()
        if game_id:
            return "game", game_id, ["title"]
        return "", "", []

    # --- claims / precedence view ------------------------------------------------

    def _refresh_claim_view(self) -> None:
        etype, eid, _fields = self._current_entity()
        self._claims_table.setRowCount(0)
        self._field_combo.clear()
        if not etype:
            self._status_label.setText("No canonical record selected.")
            return
        rep = build_entity_report(self._canon, etype, eid)
        rows = []
        conflicts = 0
        for f in rep.fields:
            self._field_combo.addItem(f.field_name)
            if f.conflict:
                conflicts += 1
            if not f.claims:
                rows.append((f.field_name, "(no claim recorded)", "", "", "", "", "", "", "", ""))
                continue
            for c in f.claims:
                rows.append((
                    f.field_name,
                    "" if c.value is None else str(c.value),
                    c.source,
                    c.authority,
                    str(c.authority_rank),
                    "" if c.confidence is None else f"{c.confidence:.2f}",
                    c.observed_at,
                    c.record_key,
                    c.url,
                    "YES" if c.is_winning else "",
                    "manual" if c.is_manual else "",
                ))
        self._claims_table.setRowCount(len(rows))
        self._claims_table.clearContents()
        for r, row in enumerate(rows):
            for col, val in enumerate(row):
                self._claims_table.setItem(r, col, QTableWidgetItem(str(val)))
        self._claims_table.setHorizontalHeaderLabels(
            ["Field"] + _CLAIM_COLUMNS
        )
        self._claims_table.resizeColumnsToContents()
        if conflicts:
            self._status_label.setText(
                f"{conflicts} unresolved conflict(s) — claims below, winner marked YES."
            )
        else:
            self._status_label.setText("No unresolved conflicts for this record.")

    # --- override actions --------------------------------------------------------

    def _on_apply_override(self) -> None:
        etype, eid, _ = self._current_entity()
        if not etype:
            QMessageBox.information(self, "Manual Lookup", "Select a record first.")
            return
        field_name = self._field_combo.currentText()
        value = self._value_edit.text().strip()
        if not field_name or not value:
            QMessageBox.information(
                self, "Manual Lookup", "Choose a field and enter a value."
            )
            return
        apply_manual_override(self._canon, etype, eid, field_name, value)
        self._refresh_claim_view()

    def _on_revert_override(self) -> None:
        etype, eid, _ = self._current_entity()
        if not etype:
            return
        field_name = self._field_combo.currentText()
        if not field_name:
            return
        revert_manual_override(self._canon, etype, eid, field_name)
        self._refresh_claim_view()

    # --- source browser -----------------------------------------------------------

    def _on_search_sources(self) -> None:
        query = self._query_edit.text().strip()
        self._candidates_table.setRowCount(0)
        if not query:
            return
        # Exact-hash match first (hex-validated SHA-1 / MD5 / CRC32), then title.
        def _hex(s: str) -> bool:
            return all(c in "0123456789abcdefABCDEF" for c in s)

        kwargs = {}
        if _hex(query):
            if len(query) == 40:
                kwargs = {"sha1": query}
            elif len(query) == 32:
                kwargs = {"md5": query}
            elif len(query) == 8:
                kwargs = {"crc32": query}
        cands = candidates_from_sources(self._manager, **kwargs) if kwargs \
            else candidates_from_sources(self._manager, title=query)
        if not cands:
            self._status_label.setText(
                "No matching indexed source entry for this query."
            )
            return
        self._status_label.setText("")
        self._candidates_table.setRowCount(len(cands))
        for r, cand in enumerate(cands):
            values = [
                cand.get("source_name", ""),
                cand.get("title") or "",
                cand.get("year") or "",
                cand.get("publisher") or "",
                cand.get("region") or "",
                cand.get("sha1") or "",
                cand.get("record_key", ""),
            ]
            for col, val in enumerate(values):
                self._candidates_table.setItem(r, col, QTableWidgetItem(str(val)))

    def _on_use_query_value(self) -> None:
        row = self._candidates_table.currentRow()
        if row < 0:
            return
        item = self._candidates_table.item(row, 1)
        if item is None:
            return
        self._value_edit.setText(item.text())

    def closeEvent(self, event) -> None:  # release the canonical DB promptly
        try:
            self._canon.close()
        except Exception:
            pass
        super().closeEvent(event)
