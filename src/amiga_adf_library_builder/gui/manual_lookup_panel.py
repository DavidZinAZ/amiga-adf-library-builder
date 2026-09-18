"""Qt panel for the unified manual lookup / source browser (GH-107 Slice 4).

Thin GUI over ``manual_lookup`` (service) + ``CanonicalLibrary`` (Slice 3
persistence). All precedence/explanation logic lives in the service; this
module only renders and forwards operator actions.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Any

from PySide6.QtCore import Qt
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

from ..canonical import CanonicalLibrary, SourceAuthority, Provenance
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
from ..rtfm import DocType

_CANDIDATE_COLUMNS = [
    "Source", "Title", "Year", "Publisher", "Region", "SHA-1", "Record Key"
]
_CLAIM_COLUMNS = [
    "Value", "Source", "Authority", "Rank", "Confidence", "Observed",
    "Record Key", "URL", "Winner", "Manual"
]
_DOC_COLUMNS = [
    "Provider/Source", "Doc Type", "Title", "Status",
    "Match/Reason", "Source URL", "Selected"
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
        # (GH-170 RC-D) Record as authoritative identity button.
        self._claim_btn = QPushButton("Record as Authoritative Identity")
        self._claim_btn.setToolTip(
            "Write the selected DAT record's game/release/disk identity "
            "as a CURATION-authority claim into canonical.db"
        )
        ov.addWidget(self._claim_btn)
        layout.addLayout(ov)

        # Row 4: DAT source browser (read-only) with lookup integration.
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
        self._lookup_btn = QPushButton("Unified Lookup…")
        self._lookup_btn.setToolTip(
            "Open unified lookup dialog for this entity with online + offline candidates"
        )
        brow.addWidget(self._lookup_btn)
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

        # Row 5: Typed-document search section.
        doc_box = QGroupBox("Typed-document search (Lemon Amiga)")
        dl = QVBoxLayout(doc_box)
        brow2 = QHBoxLayout()
        self._doc_query = QLineEdit(self)
        self._doc_query.setPlaceholderText(
            "Game title to search for typed docs (Hints/Solution/Cheat/Manual)"
        )
        brow2.addWidget(self._doc_query, 1)
        self._doc_search_btn = QPushButton("Search Typed Docs")
        self._doc_search_btn.setToolTip(
            "Discover and fetch real typed documents from Lemon Amiga"
        )
        brow2.addWidget(self._doc_search_btn)
        self._doc_apply_btn = QPushButton("Apply Selection")
        self._doc_apply_btn.setToolTip(
            "Persist selected documents as operator override"
        )
        brow2.addWidget(self._doc_apply_btn)
        self._doc_refresh_btn = QPushButton("Refresh")
        dl.addLayout(brow2)
        self._doc_table = QTableWidget(self)
        self._doc_table.setColumnCount(len(_DOC_COLUMNS))
        self._doc_table.setHorizontalHeaderLabels(_DOC_COLUMNS)
        self._doc_table.horizontalHeader().setStretchLastSection(True)
        self._doc_table.setAlternatingRowColors(True)
        self._doc_table.verticalHeader().setVisible(False)
        self._doc_table.setSelectionBehavior(
            QTableWidget.SelectRows
        )
        dl.addWidget(self._doc_table)
        self._doc_status = QLabel("", self)
        dl.addWidget(self._doc_status)
        layout.addWidget(doc_box, 2)

        # Wire signals
        self._refresh_btn.clicked.connect(self.refresh)
        self._game_combo.currentIndexChanged.connect(self._on_game_selected)
        self._release_combo.currentIndexChanged.connect(self._on_release_selected)
        self._disk_combo.currentIndexChanged.connect(self._on_disk_selected)
        self._apply_btn.clicked.connect(self._on_apply_override)
        self._revert_btn.clicked.connect(self._on_revert_override)
        self._claim_btn.clicked.connect(self._on_claim_authoritative)
        self._search_btn.clicked.connect(self._on_search_sources)
        self._use_query_btn.clicked.connect(self._on_use_query_value)
        self._lookup_btn.clicked.connect(self._on_unified_lookup)
        self._doc_search_btn.clicked.connect(self._on_doc_search)
        self._doc_apply_btn.clicked.connect(self._on_doc_override)

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

    # --- authoritative identity (GH-170 RC-D) --------------------------------

    def _on_claim_authoritative(self) -> None:
        """Record the selected DAT record as authoritative identity."""
        etype, eid, fields = self._current_entity()
        if not etype or not eid:
            self._status_label.setText("No canonical record selected.")
            return
        # Claim title, year, publisher, region for releases; game title for games.
        claim_fields = ["title", "year", "publisher", "region", "language"]
        if etype == "game":
            claim_fields = ["title"]
        elif etype == "disk":
            claim_fields = ["filename", "disk_number", "sha256"]
        provenance = Provenance(
            source="manual_lookup",
            record_key=eid,
            url="",
            authority=SourceAuthority.CURATION,
            authority_rank=SourceAuthority.CURATION.value,
            confidence=1.0,
        )
        claimed = 0
        for field in claim_fields:
            # Read the current value from the entity report.
            try:
                value, _ = self._canon.resolve_field(etype, eid, field)
            except Exception:
                continue
            if value is None or value == "":
                continue
            self._canon.claim_field(etype, eid, field, value, provenance)
            claimed += 1
        if claimed:
            self._status_label.setText(
                f"Recorded {claimed} authoritative claim(s) for "
                f"{etype} {eid} (CURATION authority)."
            )
        else:
            self._status_label.setText("No claimable fields found.")
        self._refresh_claim_view()

    # --- source browser ---------------------------------------------------------

    def _on_search_sources(self) -> None:
        query = self._query_edit.text().strip()
        self._candidates_table.setRowCount(0)
        # (GH-170 RC-F) Report explicit states before/at search.
        try:
            sources = self._manager.list_sources()
        except Exception as exc:
            self._status_label.setText(
                f"Source index unavailable: {exc}"
            )
            return
        if not sources:
            self._status_label.setText(
                "No metadata sources indexed. Add sources via the Options tab."
            )
            return
        # Check if any enabled sources exist.
        enabled_sources = [s for s in sources if s.enabled]
        if not enabled_sources:
            self._status_label.setText(
                f"All {len(sources)} source(s) are disabled. Enable at least one."
            )
            return
        if not query:
            self._status_label.setText(
                f"{len(sources)} source(s) indexed ({len(enabled_sources)} enabled). Enter a query."
            )
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
                f"Zero results for query '{query}' across {len(enabled_sources)} enabled source(s). "
                "Try a different query or check source index status."
            )
            return
        self._status_label.setText(
            f"{len(cands)} result(s) from {len(enabled_sources)} enabled source(s)."
        )
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

    # --- unified lookup integration (GH-157) -----------------------------------

    def _on_unified_lookup(self) -> None:
        """Open a quick query dialog and run candidates_from_sources + online lookup.

        This connects the previously siloed ManualLookupPanel into the shared
        unified lookup workflow. If a query is already entered in the source
        browser, it reuses that; otherwise prompts for a new one.
        """
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QVBoxLayout, QLabel

        query = self._query_edit.text().strip()
        if not query:
            # Quick prompt
            dlg = QDialog(self)
            dlg.setWindowTitle("Unified Lookup Query")
            layout = QVBoxLayout(dlg)
            lbl = QLabel("Enter search query:")
            edit = QLineEdit()
            edit.setPlaceholderText("Title, hash, or keyword…")
            layout.addWidget(lbl)
            layout.addWidget(edit)
            btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            layout.addWidget(btns)
            btns.accepted.connect(dlg.accept)
            btns.rejected.connect(dlg.reject)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            query = edit.text().strip()

        if not query:
            return

        # Run DAT index search immediately (offline, fast)
        kwargs = {}
        def _hex(s: str) -> bool:
            return all(c in "0123456789abcdefABCDEF" for c in s)

        if _hex(query):
            if len(query) == 40:
                kwargs["sha1"] = query
            elif len(query) == 32:
                kwargs["md5"] = query
            elif len(query) == 8:
                kwargs["crc32"] = query
        else:
            kwargs["title"] = query

        dat_error: Optional[str] = None
        try:
            cands = candidates_from_sources(self._manager, **kwargs) if kwargs else []
        except Exception as exc:
            dat_error = f"DAT search error: {exc}"
            cands = []

        # Update candidate table with DAT results
        self._candidates_table.setRowCount(0)
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

        # --- DEF-6 FIX: surface online candidates in the status / table ------
        online_ok = False
        online_result = None
        online_candidates: list[dict] = []
        try:
            ctx_provider = getattr(self, "_lookup_ctx_provider", None)
            if ctx_provider:
                ctx = ctx_provider()
                if ctx:
                    from ..lookup_workflow import run_lookup, MODE_ONLINE
                    ctx.query = query
                    online_result = run_lookup(MODE_ONLINE, ctx, collect_candidates=True)
                    online_ok = True
                    if hasattr(online_result, "candidates"):
                        online_candidates = online_result.candidates
        except Exception as online_exc:
            online_ok = False
            dat_error = dat_error or ""
            dat_error += f"; online lookup failed: {online_exc}"

        if online_ok and online_result:
            # Merge online candidates into the visible candidate table (after
            # any DAT rows).  This prevents the unified lookup from fetching
            # online results and silently throwing them away.
            if hasattr(online_result, "candidates") and online_result.candidates:
                all_cands = cands + online_result.candidates
                self._candidates_table.setRowCount(len(all_cands))
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
                n_dat = len(cands)
                for idx, oc in enumerate(online_result.candidates):
                    values = [
                        oc.get("provider") or "online",
                        oc.get("title") or "",
                        str(oc.get("year") or ""),
                        oc.get("publisher") or "",
                        oc.get("region") or "",
                        oc.get("sha1") or "",
                        oc.get("record_key") or "",
                    ]
                    for col, val in enumerate(values):
                        self._candidates_table.setItem(n_dat + idx, col, QTableWidgetItem(str(val)))
            else:
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

        if dat_error:
            self._status_label.setText(dat_error)
        elif online_ok and online_result:
            n_on = len(online_candidates)
            self._status_label.setText(
                f"DAT results: {len(cands)} | Online: {n_on} candidate(s)"
            )
        else:
            self._status_label.setText(f"DAT results: {len(cands)}")
        # ---------------------------------------------------------------------

    def _on_doc_search(self) -> None:
        """Search Lemon Amiga for typed documents for the selected game."""
        title = self._doc_query.text().strip()
        if not title:
            # Try to get the title from the current game selection
            game_id = self._game_combo.currentData()
            if game_id:
                from ..manual_lookup import _game_label
                title = _game_label(self._canon, game_id)
        if not title:
            self._doc_status.setText("No game title selected for doc search.")
            return

        from ..metadata import lemonamiga_discover_docs
        from ..rtfm import DocType

        try:
            discovered = lemonamiga_discover_docs(title, timeout=20.0)
        except Exception as exc:
            self._doc_status.setText(f"Doc search failed: {exc}")
            discovered = []

        self._doc_table.setRowCount(0)
        if not discovered:
            self._doc_status.setText(
                f"No typed documents discovered for '{title}' on Lemon Amiga. "
                "The game may not have Hints/Solution/Cheat available."
            )
            return

        # Map doc type to human-readable labels
        type_labels = {
            DocType.HINTS.value: "Hints",
            DocType.SOLUTION.value: "Solution",
            DocType.CHEAT.value: "Cheat",
            DocType.WALKTHROUGH.value: "Walkthrough",
            DocType.MANUAL.value: "Manual",
            DocType.INSTRUCTIONS.value: "Instructions",
            DocType.REFERENCE.value: "Reference",
        }

        self._doc_table.setRowCount(len(discovered))
        for r, doc_info in enumerate(discovered):
            doc_type = doc_info.get("type", "")
            doc_url = doc_info.get("url", "")
            label = type_labels.get(doc_type, doc_type.capitalize())
            self._doc_table.setItem(r, 0, QTableWidgetItem("lemon-amiga"))
            self._doc_table.setItem(r, 1, QTableWidgetItem(label))
            self._doc_table.setItem(r, 2, QTableWidgetItem(title))
            self._doc_table.setItem(r, 3, QTableWidgetItem("Discovered"))
            self._doc_table.setItem(r, 4, QTableWidgetItem("Available on provider"))
            self._doc_table.setItem(r, 5, QTableWidgetItem(doc_url))
            check = QTableWidgetItem()
            check.setFlags(check.flags() | Qt.ItemIsUserCheckable)
            check.setCheckState(Qt.CheckedState.Checked)
            self._doc_table.setItem(r, 6, check)

        n_docs = len(discovered)
        self._doc_status.setText(
            f"Found {n_docs} typed document(s) for '{title}'. "
            "Select which to associate with this release."
        )

    def _on_doc_override(self) -> None:
        """Persist the operator's doc selection as a manual override."""
        rows = self._doc_table.rowCount()
        if rows == 0:
            self._doc_status.setText("No documents to apply.")
            return

        release_id = self._release_combo.currentData()
        if not release_id:
            self._doc_status.setText("No release selected for doc override.")
            return

        selected_docs = []
        for r in range(rows):
            check_item = self._doc_table.item(r, 6)
            if check_item and check_item.checkState() == Qt.CheckedState.Checked:
                doc_type = self._doc_table.item(r, 1).text()
                doc_url = self._doc_table.item(r, 5).text()
                selected_docs.append((doc_type, doc_url))

        if not selected_docs:
            self._doc_status.setText(
                "No documents selected for override. "
                "Check the rows you want to associate."
            )
            return

        # Persist the association as a manual override
        # Store the doc association in the canonical library
        for doc_type, doc_url in selected_docs:
            try:
                self._canon.claim_field(
                    "release", release_id,
                    f"doc_{doc_type}",
                    doc_url,
                    Provenance(
                        source="operator",
                        authority=SourceAuthority.CURATION,
                        record_key=f"doc:{doc_type}",
                        url=doc_url,
                    )
                )
            except Exception:
                pass

        self._canon._conn.commit()
        self._doc_status.setText(
            f"Applied {len(selected_docs)} document override(s) for release."
        )

    def closeEvent(self, event) -> None:  # release the canonical DB promptly
        try:
            self._canon.close()
        except Exception:
            pass
        super().closeEvent(event)
