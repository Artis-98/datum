"""The Bill of Materials of an assembly, as a window, the way Inventor has it.

Two tabs.  Model Data is the assembly as it is put together: what is
placed in it directly, parts and sub-assemblies, each counted.  Parts Only
is what it is made of: every part, however deep, counted right through, a
bolt in a sub-assembly used twice counted twice over.

Every column a part's dProperties hold can be shown, and changed in the
table: what is typed goes back into the part, into the open document if
it is open, into its file if not.  Which columns show is picked from a
list and kept with the assembly, so the table stays as tidy as wanted.
"""

from __future__ import annotations

import csv
import os
from typing import Dict, List, Optional, Sequence, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

from ..core import bom, units as unitlib
from . import icons
from .panels import DocumentProperties
from .theme import C

# what each property is called, as the dProperties window calls it
FIELD_LABELS: Dict[str, str] = dict(DocumentProperties.FIELDS)

# columns that are not properties: worked out, not typed
ITEM = "item"
THUMBNAIL = "thumbnail"
QTY = "qty"
MATERIAL = "material"
MASS = "mass"

COMPUTED = {ITEM: "Item", THUMBNAIL: "Thumbnail", QTY: "QTY",
            MATERIAL: "Material", MASS: "Mass"}

# the part number is a property too, but it is the row's name: always shown
PART_NUMBER = bom.PROP + "PartNumber"

DEFAULT = [ITEM, THUMBNAIL, PART_NUMBER, QTY, bom.PROP + "Description",
           MATERIAL, MASS]

MODEL_DATA = "Model Data"
PARTS_ONLY = "Parts Only"

THUMB = 44


def heading(column: str) -> str:
    if column in COMPUTED:
        return COMPUTED[column]
    name = column[len(bom.PROP):] if column.startswith(bom.PROP) else column
    return FIELD_LABELS.get(name, name)


class BomDialog(QtWidgets.QDialog):
    """An assembly's Bill of Materials: look, change, pick the columns."""

    def __init__(self, parent, assembly, session=None, library=None) -> None:
        super().__init__(parent)
        self.assembly = assembly
        self.session = session
        self.library = library
        title = os.path.splitext(os.path.basename(
            getattr(assembly, "path", "") or "Assembly"))[0]
        self.setWindowTitle("Bill of Materials: %s" % title)
        self.setWindowIcon(icons.icon("material", 24))
        self.resize(980, 560)
        # path -> {property: value}, what has been typed and not yet kept
        self.changes: Dict[str, Dict[str, str]] = {}
        self._filling = False

        self.columns: List[str] = list(getattr(assembly, "bom_columns", [])
                                       or DEFAULT)
        if PART_NUMBER not in self.columns:
            self.columns.insert(min(2, len(self.columns)), PART_NUMBER)

        layout = QtWidgets.QVBoxLayout(self)
        bar = QtWidgets.QHBoxLayout()
        self.columns_button = QtWidgets.QToolButton()
        self.columns_button.setText("Columns")
        self.columns_button.setIcon(icons.icon("params", 16))
        self.columns_button.setToolButtonStyle(
            QtCore.Qt.ToolButtonTextBesideIcon)
        self.columns_button.setToolTip("Pick which columns the table shows")
        self.columns_button.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.columns_menu = QtWidgets.QMenu(self.columns_button)
        self.columns_menu.aboutToShow.connect(self._fill_columns_menu)
        self.columns_button.setMenu(self.columns_menu)
        bar.addWidget(self.columns_button)
        self.export_button = QtWidgets.QPushButton("Export...")
        self.export_button.setToolTip("Save the table shown as a CSV file")
        self.export_button.clicked.connect(self.export)
        bar.addWidget(self.export_button)
        bar.addStretch(1)
        self.status = QtWidgets.QLabel("")
        self.status.setProperty("hint", True)
        bar.addWidget(self.status)
        layout.addLayout(bar)

        self.tabs = QtWidgets.QTabWidget()
        self.tables: Dict[str, QtWidgets.QTableWidget] = {}
        self.rows: Dict[str, List[bom.Row]] = {}
        for name in (MODEL_DATA, PARTS_ONLY):
            table = QtWidgets.QTableWidget()
            table.verticalHeader().setVisible(False)
            table.setAlternatingRowColors(True)
            table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectItems)
            table.setIconSize(QtCore.QSize(THUMB, THUMB))
            table.horizontalHeader().setStretchLastSection(True)
            table.itemChanged.connect(
                lambda item, tab=name: self._edited(tab, item))
            self.tables[name] = table
            self.tabs.addTab(table, name)
        layout.addWidget(self.tabs, 1)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.Ok).setText("Done")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._thumbnails: Dict[str, Optional[QtGui.QIcon]] = {}
        self.reload()

    # -- what is in it ------------------------------------------------------

    def reload(self) -> None:
        base = os.path.dirname(os.path.abspath(
            getattr(self.assembly, "path", "") or ".")) \
            if getattr(self.assembly, "path", "") else \
            getattr(self.assembly, "base_dir", "")
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            self.rows[MODEL_DATA] = bom.rows_for(self.assembly, base,
                                                 self.library, recurse=False)
            self.rows[PARTS_ONLY] = bom.rows_for(self.assembly, base,
                                                 self.library, recurse=True)
            self._overlay_open_documents()
            for name in (MODEL_DATA, PARTS_ONLY):
                self._fill(name)
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self.status.setText("%d at the top level, %d parts in all"
                            % (sum(r.quantity for r in self.rows[MODEL_DATA]),
                               sum(r.quantity for r in self.rows[PARTS_ONLY])))

    def _overlay_open_documents(self) -> None:
        """A part open in a tab may say more than its file: it wins."""
        if self.session is None:
            return
        for rows in self.rows.values():
            for row in rows:
                entry = self.session.by_path(row.path) if row.path else None
                document = entry.document if entry is not None else None
                held = getattr(document, "properties", None)
                if isinstance(held, dict):
                    row.extra = {str(k): str(v) for k, v in held.items()
                                 if str(v)}
                    if held.get("PartNumber"):
                        row.part_number = str(held["PartNumber"])
                    if held.get("Description"):
                        row.description = str(held["Description"])
                    material = getattr(document, "material", "")
                    if material and material != "Generic":
                        row.material = str(material)

    def available(self) -> List[str]:
        """Every column there is to show: the worked out ones, every fixed
        dProperties field, and every property any part has of its own."""
        out = [ITEM, THUMBNAIL, QTY, MATERIAL, MASS]
        out += [bom.PROP + key for key, _label in DocumentProperties.FIELDS]
        for rows in self.rows.values():
            for name in bom.property_names(rows):
                column = bom.PROP + name
                if column not in out:
                    out.append(column)
        for column in self.columns:
            if column not in out:
                out.append(column)
        return out

    def value(self, row: bom.Row, column: str) -> str:
        """What a cell says, a change typed in it first."""
        if column.startswith(bom.PROP):
            name = column[len(bom.PROP):]
            pending = self.changes.get(row.path, {})
            if name in pending:
                return pending[name]
            if name == "PartNumber":
                return row.part_number
            if name == "Description":
                return row.extra.get("Description", row.description)
            return row.extra.get(name, "")
        if column == ITEM:
            return str(row.item)
        if column == QTY:
            return str(row.quantity)
        if column == MATERIAL:
            return row.material
        if column == MASS:
            grams = bom.mass_grams(row.path, self.library)
            if grams is None:
                return ""
            unit = getattr(self.assembly, "units", "mm") or "mm"
            return unitlib.mass_text(grams, unit)
        return ""

    def _thumbnail(self, path: str) -> Optional[QtGui.QIcon]:
        if path in self._thumbnails:
            return self._thumbnails[path]
        icon = None
        try:
            from ..core import fileformat
            data = fileformat.thumbnail_of(path) if path else None
            if data:
                pixmap = QtGui.QPixmap()
                if pixmap.loadFromData(data):
                    icon = QtGui.QIcon(pixmap.scaled(
                        THUMB, THUMB, QtCore.Qt.KeepAspectRatio,
                        QtCore.Qt.SmoothTransformation))
        except Exception:
            icon = None
        self._thumbnails[path] = icon
        return icon

    def _fill(self, name: str) -> None:
        table = self.tables[name]
        rows = self.rows.get(name, [])
        self._filling = True
        table.clear()
        table.setColumnCount(len(self.columns))
        table.setHorizontalHeaderLabels([heading(c) for c in self.columns])
        table.setRowCount(len(rows))
        shows_thumbnails = THUMBNAIL in self.columns
        for r, row in enumerate(rows):
            for c, column in enumerate(self.columns):
                if column == THUMBNAIL:
                    item = QtWidgets.QTableWidgetItem()
                    icon = self._thumbnail(row.path)
                    if icon is not None:
                        item.setIcon(icon)
                    item.setFlags(QtCore.Qt.ItemIsEnabled)
                else:
                    item = QtWidgets.QTableWidgetItem(self.value(row, column))
                    if column.startswith(bom.PROP):
                        item.setFlags(QtCore.Qt.ItemIsEnabled
                                      | QtCore.Qt.ItemIsSelectable
                                      | QtCore.Qt.ItemIsEditable)
                        if column[len(bom.PROP):] in self.changes.get(
                                row.path, {}):
                            item.setForeground(QtGui.QBrush(
                                QtGui.QColor(C.accent)))
                    else:
                        item.setFlags(QtCore.Qt.ItemIsEnabled
                                      | QtCore.Qt.ItemIsSelectable)
                        item.setForeground(QtGui.QBrush(
                            QtGui.QColor(C.text_dim)))
                if column in (ITEM, QTY, MASS):
                    item.setTextAlignment(QtCore.Qt.AlignCenter)
                item.setData(QtCore.Qt.UserRole, (row.path, column))
                table.setItem(r, c, item)
            if shows_thumbnails:
                table.setRowHeight(r, THUMB + 6)
        table.resizeColumnsToContents()
        if shows_thumbnails:
            table.setColumnWidth(self.columns.index(THUMBNAIL), THUMB + 16)
        self._filling = False

    # -- changing it ----------------------------------------------------------

    def _edited(self, tab: str, item: QtWidgets.QTableWidgetItem) -> None:
        if self._filling:
            return
        held = item.data(QtCore.Qt.UserRole)
        if not held:
            return
        path, column = held
        if not path or not column.startswith(bom.PROP):
            return
        self.set_value(path, column[len(bom.PROP):], item.text())

    def set_value(self, path: str, name: str, text: str) -> None:
        """Type a property of one part, for every row of it, in both tabs."""
        self.changes.setdefault(path, {})[name] = text.strip()
        for tab in (MODEL_DATA, PARTS_ONLY):
            self._fill(tab)

    def apply(self) -> List[str]:
        """Keep what was typed: in the open document, or in the file.

        Returns the files written, which are those not open anywhere.
        """
        written = []
        for path, changes in self.changes.items():
            entry = self.session.by_path(path) if self.session else None
            document = entry.document if entry is not None else None
            if document is not None and isinstance(
                    getattr(document, "properties", None), dict):
                for name, value in changes.items():
                    if value:
                        document.properties[name] = value
                    else:
                        document.properties.pop(name, None)
                document.modified = True
                continue
            if path and os.path.exists(path):
                bom.write_properties(path, changes)
                written.append(path)
        self.changes = {}
        return written

    def accept(self) -> None:
        self.apply()
        super().accept()

    # -- columns --------------------------------------------------------------

    def _fill_columns_menu(self) -> None:
        menu = self.columns_menu
        menu.clear()
        for column in self.available():
            action = menu.addAction(heading(column))
            action.setCheckable(True)
            action.setChecked(column in self.columns)
            # the part number names the row, so it stays
            action.setEnabled(column != PART_NUMBER)
            action.toggled.connect(
                lambda on, c=column: self.show_column(c, on))
        menu.addSeparator()
        menu.addAction("Add a property column...").triggered.connect(
            self._add_property_column)
        menu.addAction("Back to the usual columns").triggered.connect(
            self._usual_columns)

    def show_column(self, column: str, visible: bool) -> None:
        if visible and column not in self.columns:
            # on the end: the order the table is in is the user's
            self.columns.append(column)
        elif not visible and column in self.columns and column != PART_NUMBER:
            self.columns.remove(column)
        self._remember_columns()
        for tab in (MODEL_DATA, PARTS_ONLY):
            self._fill(tab)

    def _add_property_column(self) -> None:
        name, ok = QtWidgets.QInputDialog.getText(
            self, "Add a property column",
            "Property name, a new one or one the parts already have:")
        name = (name or "").strip()
        if ok and name:
            self.show_column(bom.PROP + name, True)

    def _usual_columns(self) -> None:
        self.columns = list(DEFAULT)
        self._remember_columns()
        for tab in (MODEL_DATA, PARTS_ONLY):
            self._fill(tab)

    def _remember_columns(self) -> None:
        if hasattr(self.assembly, "bom_columns"):
            if self.assembly.bom_columns != self.columns:
                self.assembly.bom_columns = list(self.columns)
                self.assembly.modified = True

    # -- out ------------------------------------------------------------------

    def export(self) -> None:
        tab = self.tabs.tabText(self.tabs.currentIndex())
        start = os.path.splitext(getattr(self.assembly, "path", "") or
                                 "Bill of Materials")[0] + " BOM.csv"
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export the Bill of Materials", start, "CSV (*.csv)")
        if path:
            self.write_csv(path, tab)

    def write_csv(self, path: str, tab: str = PARTS_ONLY) -> None:
        columns = [c for c in self.columns if c != THUMBNAIL]
        with open(path, "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow([heading(c) for c in columns])
            for row in self.rows.get(tab, []):
                writer.writerow([self.value(row, c) for c in columns])
