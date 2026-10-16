"""Accessible media review with image, PDF page, and text previews."""
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QPixmap, QIcon
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QComboBox, QListWidget, QListWidgetItem, QPushButton, QTextEdit,
    QSplitter, QWidget, QFileDialog, QSpinBox, QMessageBox)


def _pdf_backend():
    try:
        import pymupdf
    except ImportError:
        import fitz as pymupdf
    return pymupdf


class MediaChoiceDialog(QDialog):
    choices_saved = Signal(dict)
    disk_choices_saved = Signal(str, dict)
    media_scope_changed = Signal(str, str)
    move_disks_requested = Signal()
    assign_media_requested = Signal(dict)
    def __init__(self, entry, preview_dir: Path, parent=None, initial_purpose=None):
        super().__init__(parent)
        self.entry = entry
        self.preview_dir = preview_dir
        self.decisions = {}
        self.next_requested = False
        self.previous_requested = False
        self.candidates = [dict(c) for c in entry.media_candidates]
        choice_sets = list(entry.media_choices.values()) + [items for choices in entry.disk_media_choices.values() for items in choices.values()]
        for items in choice_sets:
            for candidate in items:
                if not any(c.get("path") == candidate.get("path") and c.get("content") == candidate.get("content") for c in self.candidates):
                    self.candidates.append(dict(candidate))
        self.setWindowTitle(f"Review media choices — {entry.title}")
        self.resize(1050, 720)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Compare candidates with your saved choice. Use this saves your choice. Next moves to the next item."))
        self.purpose = QComboBox()
        purposes = sorted({c.get("purpose", "manual") for c in self.candidates} | set(entry.media_choices))
        if "manual" not in purposes:
            purposes.append("manual")
        if not any(p != "manual" for p in purposes):
            purposes.append("Box - Front")
        for purpose in purposes:
            self.purpose.addItem("RTFM" if purpose == "manual" else "Artwork" if purpose == "Box - Front" else f"Artwork ({purpose})", purpose)
        if initial_purpose:
            if self.purpose.findData(initial_purpose) < 0:
                self.purpose.addItem("RTFM" if initial_purpose == "manual" else "Artwork", initial_purpose)
            self.purpose.setCurrentIndex(self.purpose.findData(initial_purpose))
        self.purpose.setAccessibleName("Media purpose")
        layout.addWidget(self.purpose)
        self.scope = QComboBox()
        self.scope.addItem("Same media for the whole release", "shared")
        self.scope.addItem("Different media per disk", "per_disk")
        self.scope.setAccessibleName("Shared or disk-specific media")
        layout.addWidget(self.scope)
        self.disk = QComboBox()
        self.disk.setAccessibleName("Disk receiving this media")
        self.disk.addItems(entry.adf_files)
        layout.addWidget(self.disk)
        self.scope_note = QLabel("Disks without a custom choice inherit the shared release choice.")
        self.scope_note.setWordWrap(True)
        layout.addWidget(self.scope_note)
        move_row = QHBoxLayout()
        move_disks = QPushButton("Move disks to another release…")
        move_disks.clicked.connect(lambda: self.move_disks_requested.emit())
        move_row.addWidget(move_disks)
        assign_media = QPushButton("Assign media to another release…")
        assign_media.clicked.connect(self._assign_media)
        move_row.addWidget(assign_media)
        layout.addLayout(move_row)
        self.list = QListWidget()
        self.list.setAccessibleName("Matching candidates; check optional extra documents")
        self.list.setMinimumWidth(300)
        self.details = QLabel()
        self.details.setWordWrap(True)
        self.picture = QLabel("Select a candidate to preview")
        self.picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.picture.setMinimumSize(240, 200)
        self._full_pixmap = None
        self.text = QTextEdit()
        self.text.setReadOnly(True)
        self.text.setAccessibleName("Document preview")
        self.page = QSpinBox()
        self.page.setMinimum(1)
        self.page.setPrefix("Page ")
        self.page.setAccessibleName("PDF preview page")
        self.page.valueChanged.connect(self._preview)
        preview = QWidget()
        pv = QVBoxLayout(preview)
        pv.addWidget(self.details)
        pv.addWidget(self.page)
        pv.addWidget(self.picture, 1)
        enlarge = QPushButton("Enlarge image / PDF page…")
        enlarge.clicked.connect(self._enlarge)
        pv.addWidget(enlarge)
        pv.addWidget(self.text, 1)
        self.current = QTextEdit()
        self.current.setReadOnly(True)
        self.current.setAccessibleName("Saved choices")
        self.current.setMaximumHeight(110)
        pv.addWidget(self.current)
        compare = QPushButton("Compare with saved choice…")
        compare.clicked.connect(self._compare_saved)
        pv.addWidget(compare)
        split = QSplitter()
        split.addWidget(self.list)
        split.addWidget(preview)
        layout.addWidget(split, 1)
        row = QHBoxLayout()
        for label, handler in [("Use this", self._use), ("None of these", self._none),
                               ("Browse…", self._browse), ("Use automatic matching", self._automatic),
                               ("Preview generated RTFM", self._rtfm_preview)]:
            button = QPushButton(label)
            button.clicked.connect(handler)
            row.addWidget(button)
        layout.addLayout(row)
        layout.addWidget(QLabel("For manuals: select the primary document, then check optional reference cards, hints, or walkthroughs."))
        buttons = QHBoxLayout()
        keep = QPushButton("Close")
        keep.clicked.connect(self.reject)
        finish = QPushButton("Done")
        finish.clicked.connect(self.accept)
        previous = QPushButton("Previous")
        previous.clicked.connect(self._previous)
        buttons.addWidget(previous)
        buttons.addWidget(keep)
        buttons.addWidget(finish)
        next_button = QPushButton("Next")
        next_button.clicked.connect(self._next)
        buttons.addWidget(next_button)
        layout.addLayout(buttons)
        self.purpose.currentTextChanged.connect(self._sync_scope)
        self.scope.currentIndexChanged.connect(self._scope_changed)
        self.disk.currentIndexChanged.connect(self._populate)
        self.list.currentRowChanged.connect(self._selected)
        self._sync_scope()

    def _populate(self, *_):
        self.list.clear()
        purpose = self.purpose.currentData()
        choices = (self.entry.disk_media_choices.get(self.disk.currentText(), {})
                   if self.scope.currentData() == "per_disk" else self.entry.media_choices)
        choice = choices.get(purpose, self.entry.media_choices.get(purpose))
        if self.scope.currentData() != "per_disk":
            choice = self.decisions.get(purpose, choice)
        primary_row = 0
        for candidate in self.candidates:
            if candidate.get("purpose", "manual") != purpose:
                continue
            path = Path(candidate.get("path") or "")
            item = QListWidgetItem(f"{path.name or candidate.get('stem', 'Document')}\n"
                                   f"{candidate.get('provider') or 'Local file'} · "
                                   f"{candidate.get('method', 'operator selection')} · "
                                   f"{candidate.get('score', 1):.0%}")
            item.setData(Qt.ItemDataRole.UserRole, candidate)
            item.setToolTip(str(path))
            if purpose == "manual":
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if choice and candidate in choice[1:] else Qt.CheckState.Unchecked)
            elif path.is_file():
                pixmap = QPixmap(str(path))
                if not pixmap.isNull():
                    item.setIcon(QIcon(pixmap.scaled(96, 96, Qt.AspectRatioMode.KeepAspectRatio)))
            self.list.addItem(item)
            if choice and candidate == choice[0]:
                primary_row = self.list.count() - 1
        inherited = self.scope.currentData() == "per_disk" and purpose not in choices
        label = (f"{self.disk.currentText()}: inherited shared choice" if inherited else
                 f"{self.disk.currentText()}: custom choice" if self.scope.currentData() == "per_disk" else
                 "Shared release choice")
        self.current.setPlainText(label + "\n" + (
            "Automatic matching" if choice is None else "None" if not choice else
            "\n".join(c.get("path") or c.get("stem", "Document") for c in choice)))
        if self.list.count():
            self.list.setCurrentRow(primary_row)
        else:
            self.picture.hide()
            self.page.hide()
            self.text.setPlainText("No matching candidates. Browse to choose a file.")
            self.text.show()
            self.details.clear()
            self._full_pixmap = None

    def _candidate(self):
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _selected(self, *_):
        self.page.blockSignals(True)
        self.page.setValue(1)
        self.page.blockSignals(False)
        self._preview()

    def _preview(self, *_):
        candidate = self._candidate()
        if candidate is None:
            return
        self.picture.clear()
        self._full_pixmap = None
        self.picture.hide()
        self.text.hide()
        self.page.hide()
        path = Path(candidate.get("path") or "")
        self.details.setText("\n".join(filter(None, [str(path) if candidate.get("path") else candidate.get("source_url"),
            ", ".join(candidate.get("evidence", [])),
            f"Identical copies: {len(candidate.get('locations', []))}" if len(candidate.get('locations', [])) > 1 else ""])))
        try:
            if candidate.get("content"):
                self.text.setPlainText(candidate["content"][:200000])
                self.text.show()
            elif path.suffix.lower() == ".pdf":
                pymupdf = _pdf_backend()
                with pymupdf.open(path) as document:
                    self.page.blockSignals(True)
                    self.page.setMaximum(max(1, len(document)))
                    self.page.blockSignals(False)
                    page = document[min(self.page.value() - 1, len(document) - 1)]
                    scale = min(1.5, 1400 / max(page.rect.width, page.rect.height))
                    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
                    img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
                    self._show_image(QPixmap.fromImage(img))
                    self.page.show()
            elif path.suffix.lower() in (".txt", ".rtfm", ".rtf", ".html"):
                with path.open("rb") as stream:
                    self.text.setPlainText(stream.read(200000).decode("utf-8", errors="replace"))
                self.text.show()
            else:
                pixmap = QPixmap(str(path))
                if pixmap.isNull():
                    raise ValueError("Image cannot be decoded")
                self.details.setText(self.details.text() + f"\n{pixmap.width()} × {pixmap.height()} pixels")
                self._show_image(pixmap)
        except Exception as exc:
            self.text.setPlainText(f"Preview unavailable: {exc}")
            self.text.show()

    def _show_image(self, pixmap):
        self._full_pixmap = pixmap
        self.picture.setPixmap(pixmap.scaled(650, 430, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self.picture.show()

    def _enlarge(self):
        if self._full_pixmap is None:
            return
        from PySide6.QtWidgets import QScrollArea
        dialog = QDialog(self)
        dialog.setWindowTitle("Full preview")
        dialog.resize(950, 750)
        layout = QVBoxLayout(dialog)
        scroll = QScrollArea()
        label = QLabel()
        label.setPixmap(self._full_pixmap)
        scroll.setWidget(label)
        layout.addWidget(scroll)
        close = QPushButton("Close")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        dialog.exec()

    def _compare_saved(self):
        candidate = self._candidate()
        purpose = self.purpose.currentData()
        saved = (self.entry.disk_media_choices.get(self.disk.currentText(), {}).get(purpose, self.entry.media_choices.get(purpose))
                 if self.scope.currentData() == "per_disk" else self.entry.media_choices.get(purpose))
        if saved:
            current = saved[0]
        elif purpose in self.entry.media_choices:
            return
        else:
            path = (next(iter(self.entry.rtfm_files), None) if purpose == "manual"
                    else self.entry.artwork_front)
            current = {"path": path} if path else None
        if candidate is None or current is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Compare candidate and saved choice")
        dialog.resize(1100, 700)
        row = QHBoxLayout(dialog)
        for title, item in (("Candidate", candidate), ("Saved choice", current)):
            panel = QWidget()
            column = QVBoxLayout(panel)
            column.addWidget(QLabel(title))
            path = Path(item.get("path") or "")
            name = QLabel(str(path) if item.get("path") else item.get("stem", "Document"))
            name.setWordWrap(True)
            column.addWidget(name)
            try:
                if item.get("content") or path.suffix.lower() in (".txt", ".rtfm", ".rtf", ".html"):
                    text = QTextEdit()
                    text.setReadOnly(True)
                    if item.get("content"):
                        body = item["content"][:200000]
                    else:
                        with path.open("rb") as stream:
                            body = stream.read(200000).decode("utf-8", errors="replace")
                    text.setPlainText(body)
                    column.addWidget(text, 1)
                else:
                    if path.suffix.lower() == ".pdf":
                        pymupdf = _pdf_backend()
                        with pymupdf.open(path) as pdf:
                            page = pdf[0]
                            scale = min(1.0, 1000 / max(page.rect.width, page.rect.height))
                            pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
                            img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
                            pixmap = QPixmap.fromImage(img)
                    else:
                        pixmap = QPixmap(str(path))
                    picture = QLabel()
                    picture.setPixmap(pixmap.scaled(500, 550, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                    picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    column.addWidget(picture, 1)
            except Exception as exc:
                column.addWidget(QLabel(f"Preview unavailable: {exc}"))
            row.addWidget(panel, 1)
        dialog.exec()

    def _selection(self):
        primary = self._candidate()
        if primary is None:
            return []
        selected = [primary]
        if self.purpose.currentData() == "manual":
            for index in range(self.list.count()):
                item = self.list.item(index)
                candidate = item.data(Qt.ItemDataRole.UserRole)
                if item.checkState() == Qt.CheckState.Checked and candidate != primary:
                    selected.append(candidate)
        return selected

    def _use(self):
        if self._candidate():
            self._store_selection(self._selection())
            self._populate()

    def _sync_scope(self, *_):
        key = "manual" if self.purpose.currentData() == "manual" else "artwork"
        self.scope.blockSignals(True)
        self.scope.setCurrentIndex(1 if self.entry.media_scopes.get(key) == "per_disk" else 0)
        self.scope.blockSignals(False)
        self.disk.setVisible(self.scope.currentData() == "per_disk")
        self._populate()

    def _scope_changed(self, *_):
        key = "manual" if self.purpose.currentData() == "manual" else "artwork"
        self.media_scope_changed.emit(key, self.scope.currentData())
        self.disk.setVisible(self.scope.currentData() == "per_disk")
        self._populate()

    def _store_selection(self, selection):
        purpose = self.purpose.currentData()
        if self.scope.currentData() == "per_disk":
            if not self.disk.currentText():
                QMessageBox.information(self, "Choose a disk", "This release has no disks to receive a custom choice.")
                return
            self.disk_choices_saved.emit(self.disk.currentText(), {purpose: selection or None})
        else:
            self.decisions[purpose] = selection
            self._save_now()

    def _assign_media(self):
        candidate = self._candidate()
        if candidate:
            self.assign_media_requested.emit(dict(candidate))
            self.decisions.pop(candidate.get("purpose", "manual"), None)
            self.candidates = [dict(c) for c in self.entry.media_candidates]
            saved = list(self.entry.media_choices.values()) + [items for choices in self.entry.disk_media_choices.values() for items in choices.values()]
            for items in saved:
                for item in items:
                    if item not in self.candidates:
                        self.candidates.append(dict(item))
            self._populate()

    def _save_now(self):
        self.choices_saved.emit(dict(self.decisions))


    def _previous(self):
        if self.scope.currentData() == "per_disk" and self.disk.currentIndex() > 0:
            self.disk.setCurrentIndex(self.disk.currentIndex() - 1)
            return
        if self.purpose.currentIndex() > 0:
            self.purpose.setCurrentIndex(self.purpose.currentIndex() - 1)
        else:
            self.previous_requested = True
            self.accept()

    def _next(self):
        if self.scope.currentData() == "per_disk" and self.disk.currentIndex() + 1 < self.disk.count():
            self.disk.setCurrentIndex(self.disk.currentIndex() + 1)
            return
        index = self.purpose.currentIndex()
        for next_index in range(index + 1, self.purpose.count()):
            purpose = self.purpose.itemData(next_index)
            if purpose not in self.entry.media_choices:
                self.purpose.setCurrentIndex(next_index)
                return
        self.next_requested = True
        self.accept()

    def _none(self):
        self._store_selection([])
        self._populate()

    def _automatic(self):
        self._store_selection(None)
        self._populate()

    def _browse(self):
        purpose = self.purpose.currentData()
        mask = "Documents (*.pdf *.txt *.rtfm)" if purpose == "manual" else "Images (*.png *.jpg *.jpeg *.bmp *.gif)"
        path, _ = QFileDialog.getOpenFileName(self, "Browse for media", "", mask)
        if path:
            self.candidates.append({"path": path, "purpose": purpose, "category": "manuals" if purpose == "manual" else purpose,
                                    "score": 1.0, "method": "operator selection"})
            self._populate()
            self.list.setCurrentRow(self.list.count() - 1)

    def _rtfm_preview(self):
        if self.purpose.currentData() != "manual" or not self._selection():
            return
        from ..media_review import source_from_candidate
        from ..rtfm import build_rtfm_for_group, RtfmConfig
        group = SimpleNamespace(title=self.entry.title, release_key=self.entry.release_key, records=[], quarantine_reason=None)
        try:
            result = build_rtfm_for_group(group, cfg=RtfmConfig(enabled=True), rtfm_dir=self.preview_dir,
                                          explicit_sources=[source_from_candidate(c) for c in self._selection()])
        except (OSError, ValueError) as exc:
            QMessageBox.information(self, "RTFM preview", str(exc))
            return
        if not result.written:
            QMessageBox.information(self, "RTFM preview", "\n".join(result.notes))
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Generated RTFM preview")
        dialog.resize(750, 600)
        layout = QVBoxLayout(dialog)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText(result.rtfm_path.read_text(encoding="utf-8"))
        layout.addWidget(text)
        close = QPushButton("Close")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        dialog.exec()
