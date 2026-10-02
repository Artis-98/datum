"""Parameter table, properties readout and other side panels."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

from ..core import units as unitlib
from ..core.document import Document
from ..core.params import ExpressionError
from . import icons
from .theme import MONO_STACK, C

COLUMNS = ("Name", "Used by", "Unit", "Expression", "Value", "Comment")
NAME, USED, UNIT, EXPRESSION, VALUE, COMMENT = range(len(COLUMNS))

HEADER, MODEL, USER, PART = "header", "model", "user", "part"


class ParametersDialog(QtWidgets.QDialog):
    """Every parameter of the document, the way Inventor lays them out.

    Model parameters first: each sketch dimension and each value a feature
    was given, named d1, d2 and so on across the part.  Then the user
    parameters somebody added by name.  Both halves can be renamed and
    rewritten in place, and a change lands where the value lives, on the
    sketch or the feature, so the table and the model never disagree.

    Opened on an assembly, with ``components`` (assembly_params), the
    assembly's own parameters come first and then every part's, each named
    for its part: Box_d2.  Changing one changes the part; writing one in
    terms of another part or of the assembly makes the assembly drive it.
    """

    changed = QtCore.Signal()
    # the part documents a change from an assembly reached
    parts_changed = QtCore.Signal(list)

    def __init__(self, doc, parent=None, components=None) -> None:
        super().__init__(parent)
        self.doc = doc
        self.components = components
        self._loading = False
        # what each table row stands for: (kind, name), and the unit its
        # numbers are shown and typed in, with what sort of number it is
        self._rows: List[Tuple[str, str]] = []
        self._units: List[Tuple[str, Optional[str]]] = []

        self.setWindowTitle("Parameters")
        self.setWindowIcon(icons.icon("params", 24))
        self.resize(820, 480)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(9)

        bar = QtWidgets.QHBoxLayout()
        self.add_btn = QtWidgets.QPushButton("Add")
        self.add_btn.setIcon(icons.icon("new", 15))
        self.add_btn.setToolTip("Add a user parameter")
        self.del_btn = QtWidgets.QPushButton("Delete")
        self.del_btn.setIcon(icons.icon("delete", 15))
        self.del_btn.setToolTip("Delete the selected user parameter")
        bar.addWidget(self.add_btn)
        bar.addWidget(self.del_btn)
        self.release_btn = QtWidgets.QPushButton("Release")
        self.release_btn.setToolTip(
            "Stop the assembly driving the selected part parameter; the part "
            "keeps the value it has")
        self.release_btn.setVisible(components is not None)
        bar.addWidget(self.release_btn)
        bar.addStretch(1)
        hint = QtWidgets.QLabel(
            "Any parameter can be written in terms of another, "
            "e.g. width / 2 - wall or d3 * 2")
        hint.setProperty("hint", True)
        bar.addWidget(hint)
        root.addLayout(bar)

        self.table = QtWidgets.QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        header = self.table.horizontalHeader()
        for col in (NAME, USED, UNIT, VALUE):
            header.setSectionResizeMode(
                col, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(EXPRESSION, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(COMMENT, QtWidgets.QHeaderView.Stretch)
        root.addWidget(self.table, 1)

        self.message = QtWidgets.QLabel("")
        self.message.setProperty("hint", True)
        self.message.setWordWrap(True)
        root.addWidget(self.message)

        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        close = QtWidgets.QPushButton("Done")
        close.setProperty("primary", True)
        buttons.addWidget(close)
        root.addLayout(buttons)

        self.add_btn.clicked.connect(self._add)
        self.del_btn.clicked.connect(self._delete)
        self.release_btn.clicked.connect(self._release)
        close.clicked.connect(self.accept)
        self.table.itemChanged.connect(self._item_changed)
        self.table.currentCellChanged.connect(
            lambda *_a: self._sync_buttons())

        self.reload()

    # ----------------------------------------------------------------------

    @property
    def _has_model(self) -> bool:
        return hasattr(self.doc, "model_parameters")

    def _model(self) -> List[Any]:
        return self.doc.model_parameters() if self._has_model else []

    def _taken(self) -> set:
        if self._has_model:
            from ..core.modelparams import taken_names
            return taken_names(self.doc)
        return set(self.doc.params.names())

    def _consumers(self, name: str) -> List[str]:
        if not self._has_model:
            return []
        from ..core.modelparams import consumers
        return consumers(self.doc, name)

    @property
    def doc_unit(self) -> str:
        return unitlib.known(getattr(self.doc, "units", "mm") or "mm")

    def _unit_of(self, unit: str, own: bool = False
                 ) -> Tuple[str, Optional[str]]:
        """(the unit to show, the sort of number) for a row's unit.

        A model parameter's "mm" means a length, shown in the document's
        units.  A user parameter's unit is its own (``own``).
        """
        if not unit:
            return "", None
        if unit == "mm" and not own:
            return self.doc_unit, unitlib.LENGTH
        if unit in unitlib.LENGTHS:
            return unit, unitlib.LENGTH
        if unit == "deg":
            return "deg", unitlib.ANGLE
        return unit, None

    def reload(self) -> None:
        self._loading = True
        self.table.setRowCount(0)
        self._rows = []
        self._units = []
        errors = []

        model = self._model()
        if self._has_model:
            self._add_header("Model Parameters", len(model))
            for m in model:
                used = [m.owner] + [f for f in self._consumers(m.name)
                                    if f != m.owner]
                self._add_row(MODEL, m.name, ", ".join(used), m.unit,
                              m.expression, m.value, m.error, m.comment,
                              reference=m.reference, label=m.label)
                if m.error:
                    errors.append("%s: %s" % (m.name, m.error))

        if self.components is not None:
            self.components.refresh_assembly()
        self.doc.params.evaluate_all()
        users = list(self.doc.params)
        if self._has_model:
            self._add_header("User Parameters", len(users))
        elif self.components is not None:
            self._add_header("Assembly Parameters", len(users))
        for param in users:
            self._add_row(USER, param.name,
                          ", ".join(self._consumers(param.name)), param.unit,
                          param.expression, param.value, param.error,
                          param.comment)
            if param.error:
                errors.append("%s: %s" % (param.name, param.error))

        if self.components is not None:
            errors += self._add_parts()

        self.message.setText("; ".join(errors))
        self.message.setStyleSheet("color: %s;" % (C.error if errors
                                                   else C.text_dim))
        self._loading = False
        self._sync_buttons()

    def _add_parts(self) -> List[str]:
        """A section for each part the assembly places."""
        errors: List[str] = []
        _scope, driver_errors = self.components.scope()
        by_part: Dict[str, List[Any]] = {}
        order: List[Any] = []
        for row in self.components.rows():
            if row.component.prefix not in by_part:
                by_part[row.component.prefix] = []
                order.append(row.component)
            by_part[row.component.prefix].append(row)
        for component in order:
            rows = by_part[component.prefix]
            self._add_header("%s  (%s)" % (component.prefix,
                                           os.path.basename(component.path)),
                             len(rows))
            for row in rows:
                error = driver_errors.get(row.qualified, "") or row.error
                comment = ("driven by the assembly" if row.driven else "")
                self._add_row(PART, row.qualified, row.owner, row.unit,
                              row.driven or row.expression, row.value, error,
                              comment, reference=row.reference)
                if row.driven:
                    r = self.table.rowCount() - 1
                    for col in range(len(COLUMNS)):
                        item = self.table.item(r, col)
                        if item is not None:
                            item.setForeground(QtGui.QBrush(QtGui.QColor(
                                C.error if error else C.sketch_free)))
                if error:
                    errors.append("%s: %s" % (row.qualified, error))
        return errors

    def _add_header(self, title: str, count: int) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        item = QtWidgets.QTableWidgetItem("%s  (%d)" % (title, count))
        font = item.font()
        font.setBold(True)
        item.setFont(font)
        item.setFlags(QtCore.Qt.ItemIsEnabled)
        item.setForeground(QtGui.QBrush(QtGui.QColor(C.text_dim)))
        self.table.setItem(row, 0, item)
        self.table.setSpan(row, 0, 1, len(COLUMNS))
        self._rows.append((HEADER, title))
        self._units.append(("", None))

    def _add_row(self, kind: str, name: str, used: str, unit: str,
                 expression: str, value: float, error: str, comment: str,
                 reference: bool = False, label: str = "") -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        shown_unit, sort = self._unit_of(unit, own=kind == USER)
        if sort == unitlib.LENGTH:
            expression = unitlib.for_display(expression, shown_unit)
            value = unitlib.to_unit(value, shown_unit)
        items = [QtWidgets.QTableWidgetItem(text) for text in (
            name, used, shown_unit,
            "(measured)" if reference else expression,
            "-" if error else "%.6g" % value, comment)]
        locked = QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable
        # what is worked out is never typed into, and a model parameter's
        # unit is the dimension's own
        for col in (USED, VALUE):
            items[col].setFlags(locked)
        if kind == MODEL:
            items[UNIT].setFlags(locked)
        if kind == PART:
            # a part's parameter is renamed and described in the part
            for col in (NAME, UNIT, COMMENT):
                items[col].setFlags(locked)
            items[USED].setToolTip("%s, %s" % (used.split(", ")[0], label))
        if reference:
            items[EXPRESSION].setFlags(locked)
            items[EXPRESSION].setToolTip(
                "A driven dimension: it measures the sketch, so it is read "
                "here and set nowhere")
            for item in items:
                f = item.font()
                f.setItalic(True)
                item.setFont(f)
        if error:
            for col in (NAME, EXPRESSION, VALUE):
                items[col].setForeground(QtGui.QBrush(QtGui.QColor(C.error)))
            items[EXPRESSION].setToolTip(error)
        else:
            items[VALUE].setForeground(QtGui.QBrush(QtGui.QColor(C.ok)))
        for col, item in enumerate(items):
            self.table.setItem(row, col, item)
        self._rows.append((kind, name))
        self._units.append((shown_unit, sort))

    def row_of(self, name: str) -> int:
        """The table row a parameter is on, or -1."""
        for row, (kind, held) in enumerate(self._rows):
            if kind != HEADER and held == name:
                return row
        return -1

    def _selected(self) -> Tuple[str, str]:
        row = self.table.currentRow()
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return (HEADER, "")

    def _sync_buttons(self) -> None:
        kind, name = self._selected()
        self.del_btn.setEnabled(kind == USER)
        self.release_btn.setEnabled(
            kind == PART and self.components is not None
            and name in (getattr(self.doc, "drivers", {}) or {}))

    def _release(self) -> None:
        kind, name = self._selected()
        if kind != PART or self.components is None:
            return
        self.components.release(name)
        self.reload()
        self.changed.emit()

    def _add(self) -> None:
        if self._has_model:
            from ..core.modelparams import fresh_name
            name = fresh_name(self._taken())
        else:
            i = 1
            while "d%d" % i in self.doc.params:
                i += 1
            name = "d%d" % i
        # a new one is ten of whatever the document is in
        self.doc.params.add(name, unitlib.for_storage("10", self.doc_unit),
                            unit=self.doc_unit)
        self.reload()
        self.changed.emit()
        self.table.setCurrentCell(self.row_of(name), NAME)

    def _delete(self) -> None:
        kind, name = self._selected()
        if kind != USER:
            return
        if self._has_model:
            from ..core.modelparams import users_of
            dependents = users_of(self.doc, name) + [
                f for f in self._consumers(name)]
        else:
            dependents = self.doc.params.dependents(name)
        if dependents:
            answer = QtWidgets.QMessageBox.question(
                self, "Delete parameter",
                "%s is used by %s.\n\nDelete it anyway?"
                % (name, ", ".join(dependents)))
            if answer != QtWidgets.QMessageBox.Yes:
                return
        self.doc.params.remove(name)
        self.reload()
        self.changed.emit()

    def _item_changed(self, item: QtWidgets.QTableWidgetItem) -> None:
        if self._loading:
            return
        row, col = item.row(), item.column()
        if not 0 <= row < len(self._rows):
            return
        kind, name = self._rows[row]
        if kind == HEADER:
            return
        text = item.text()
        shown_unit, sort = self._units[row]
        if col == EXPRESSION and sort == unitlib.LENGTH:
            # typed in the unit shown, kept with that unit written in
            text = unitlib.for_storage(text, shown_unit)
        if kind == PART:
            self._part_changed(name, col, text)
            return
        try:
            if col == NAME:
                if self._has_model:
                    self.doc.rename_parameter(name, text.strip())
                else:
                    self.doc.params.rename(name, text.strip())
            elif col == EXPRESSION:
                if kind == USER:
                    self.doc.params.set_expression(name, text)
                else:
                    self.doc.set_parameter(name, text)
            elif col == UNIT and kind == USER:
                self.doc.params[name].unit = text.strip() or "mm"
            elif col == COMMENT:
                if kind == USER:
                    self.doc.params[name].comment = text
                else:
                    from ..core.modelparams import find
                    found = find(self.doc, name)
                    if found is not None:
                        found.set_comment(text)
        except ExpressionError as exc:
            QtWidgets.QMessageBox.warning(self, "Parameter", str(exc))
        if hasattr(self.doc, "modified"):
            self.doc.modified = True
        changed = []
        if self.components is not None:
            # an assembly parameter may be what a driver reads
            try:
                changed = self.components.apply()
            except ExpressionError:
                changed = []
        self.reload()
        if changed:
            self.parts_changed.emit(changed)
        self.changed.emit()

    def _part_changed(self, name: str, col: int, text: str) -> None:
        if col != EXPRESSION or self.components is None:
            self.reload()
            return
        try:
            changed = self.components.set(name, text)
        except ExpressionError as exc:
            QtWidgets.QMessageBox.warning(self, "Parameter", str(exc))
            changed = []
        self.reload()
        if changed:
            self.parts_changed.emit(changed)
        self.changed.emit()


class DocumentProperties(QtWidgets.QWidget):
    """What the document says about itself, and what it is called.

    Inventor calls these iProperties, and they are the other half of the
    Preferences window: your name is set once, in Preferences, and lands
    here on every new document, where it can be changed for this one
    document without changing who you are.  A title block and a parts list
    read these fields, which is the whole reason they exist.

    Below the fixed fields sit the custom ones, dProperties: any name, any
    value, kept in the same file.  A parts list can show one as a column
    and a title block can ask for one as {Model.Name}, so a supplier or a
    stock number typed once turns up everywhere the part does.
    """

    changed = QtCore.Signal()
    # a different unit picked: the window asks how before anything changes
    units_requested = QtCore.Signal(str)

    FIELDS = (
        ("Title", "Title"),
        ("PartNumber", "Part Number"),
        ("Designer", "Designer"),
        ("Company", "Company"),
        ("Revision", "Revision"),
        ("Description", "Description"),
        ("Project", "Project"),
        ("StockNumber", "Stock Number"),
        ("Vendor", "Vendor"),
        ("EstimatedCost", "Estimated Cost"),
        ("Comments", "Comments"),
    )

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.document = None
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 4)
        root.setSpacing(6)
        form = QtWidgets.QFormLayout()
        form.setSpacing(6)
        root.addLayout(form)

        # This document's own unit.  Preferences sets the one new documents
        # start in; this is where one part is told otherwise, years later.
        self.units_combo = QtWidgets.QComboBox()
        for key, label in unitlib.LABELS.items():
            self.units_combo.addItem("%s  (%s)" % (label, key), key)
        self.units_combo.setToolTip(
            "What this document's lengths are in. Changing it asks whether "
            "to convert, so the part keeps its size, or to keep the "
            "numbers, so the part is rescaled.")
        self.units_combo.activated.connect(self._units_picked)
        form.addRow("Units", self.units_combo)

        self.edits = {}
        for key, label in self.FIELDS:
            edit = QtWidgets.QLineEdit()
            edit.editingFinished.connect(self._write)
            self.edits[key] = edit
            form.addRow(label, edit)

        heading = QtWidgets.QLabel("Custom")
        heading.setProperty("hint", True)
        root.addWidget(heading)
        self.custom = QtWidgets.QTableWidget(0, 2)
        self.custom.setHorizontalHeaderLabels(("Name", "Value"))
        self.custom.verticalHeader().setVisible(False)
        self.custom.horizontalHeader().setStretchLastSection(True)
        self.custom.setMinimumHeight(110)
        self.custom.itemChanged.connect(lambda _item: self._write())
        root.addWidget(self.custom)
        row = QtWidgets.QHBoxLayout()
        self.add_button = QtWidgets.QPushButton("Add")
        self.add_button.clicked.connect(self.add_custom)
        self.remove_button = QtWidgets.QPushButton("Remove")
        self.remove_button.clicked.connect(self.remove_custom)
        row.addWidget(self.add_button)
        row.addWidget(self.remove_button)
        row.addStretch(1)
        root.addLayout(row)

    def update_from(self, doc) -> None:
        self.document = doc if hasattr(doc, "properties") else None
        held = getattr(self.document, "properties", None) or {}
        self.setEnabled(self.document is not None)
        self.show_units(getattr(self.document, "units", "mm"))
        for key, edit in self.edits.items():
            was = edit.blockSignals(True)
            edit.setText(str(held.get(key, "")))
            edit.blockSignals(was)
        was = self.custom.blockSignals(True)
        self.custom.setRowCount(0)
        for name, value in held.items():
            if name in self.edits:
                continue
            self._append(name, str(value))
        self.custom.blockSignals(was)

    def show_units(self, unit: str) -> None:
        """Point the units box at a unit without asking anything."""
        index = self.units_combo.findData(unitlib.known(unit or "mm"))
        was = self.units_combo.blockSignals(True)
        self.units_combo.setCurrentIndex(max(0, index))
        self.units_combo.blockSignals(was)

    def _units_picked(self, index: int) -> None:
        unit = self.units_combo.itemData(index)
        current = unitlib.known(getattr(self.document, "units", "mm") or "mm")
        if self.document is None or unit == current:
            return
        # put back until the change has really been made: a cancelled
        # question must not leave the box saying something untrue
        self.show_units(current)
        self.units_requested.emit(unit)

    def _append(self, name: str, value: str) -> int:
        row = self.custom.rowCount()
        self.custom.insertRow(row)
        self.custom.setItem(row, 0, QtWidgets.QTableWidgetItem(name))
        self.custom.setItem(row, 1, QtWidgets.QTableWidgetItem(value))
        return row

    def add_custom(self) -> None:
        """A new row, named so it is unique, ready to be renamed."""
        taken = set(self.edits) | set(self._custom_values())
        n = 1
        while "Property%d" % n in taken:
            n += 1
        was = self.custom.blockSignals(True)
        row = self._append("Property%d" % n, "")
        self.custom.blockSignals(was)
        self._write()
        self.custom.setCurrentCell(row, 0)
        self.custom.editItem(self.custom.item(row, 0))

    def remove_custom(self) -> None:
        rows = sorted({i.row() for i in self.custom.selectedIndexes()},
                      reverse=True)
        if not rows and self.custom.currentRow() >= 0:
            rows = [self.custom.currentRow()]
        for row in rows:
            self.custom.removeRow(row)
        self._write()

    def _custom_values(self) -> Dict[str, str]:
        """The custom rows, by name.  A blank name is a row half typed."""
        out: Dict[str, str] = {}
        for row in range(self.custom.rowCount()):
            name_item = self.custom.item(row, 0)
            value_item = self.custom.item(row, 1)
            name = name_item.text().strip() if name_item else ""
            # a custom one may not hide behind a fixed field's name, or
            # typing in the form above would quietly overwrite it
            if not name or name in self.edits:
                continue
            out[name] = value_item.text() if value_item else ""
        return out

    def _write(self) -> None:
        if self.document is None:
            return
        held = {}
        for key, edit in self.edits.items():
            text = edit.text().strip()
            if text:
                held[key] = text
        held.update(self._custom_values())
        if held != self.document.properties:
            self.document.properties = held
            self.document.modified = True
            self.changed.emit()


class PropertiesPanel(QtWidgets.QWidget):
    """Mass properties and bounding box of the current body."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)

        # Material used to be picked here, from a short hard-coded list,
        # and update_from wrote it back onto the document every time the
        # panel refreshed.  That fought the real picker on the top strip
        # and quietly reset any material this list had never heard of.
        # It reads the material now, and never writes one.
        self.material_label = QtWidgets.QLabel("")
        self.material_label.setProperty("hint", True)
        layout.addWidget(self.material_label)

        self.table = QtWidgets.QTableWidget(0, 2)
        self.table.horizontalHeader().setVisible(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QtWidgets.QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(
            1, QtWidgets.QHeaderView.Stretch)
        layout.addWidget(self.table, 1)

    def update_from(self, doc: Document) -> None:
        """Show this document's numbers, or remember to when it is seen.

        Every rebuild calls this, and working out volume, area and centre
        of mass is not free on a heavy body: on a 48 part assembly it was
        most of what a rebuild cost, for a panel that is hidden until
        somebody asks for it. So while it is hidden it only notes which
        document it should describe, and does the sums when it is shown.
        """
        self._pending = doc
        if not self.isVisible():
            self._stale = True
            return
        self._stale = False
        self._fill(doc)

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        if getattr(self, "_stale", False):
            self._stale = False
            self._fill(getattr(self, "_pending", None))

    def _fill(self, doc) -> None:
        if doc is None:
            self.material_label.setText("")
            self.table.setRowCount(0)
            return
        look = getattr(doc, "appearance", "")
        self.material_label.setText(
            "%s  ·  %.2f g/cm³%s"
            % (getattr(doc, "material", "Generic"),
               getattr(doc, "density", 1.0),
               ("  ·  %s" % look) if look else ""))

        # a document with no solid - a CAM sheet - says what it does have
        # instead of reporting a volume of nothing
        rows = getattr(doc, "property_rows", None)
        if rows is not None:
            self.table.setRowCount(0)
            for label, value in rows():
                self._row(label, value)
            return

        props = doc.mass_properties()
        self.table.setRowCount(0)
        if not props:
            self._row("Body", "none")
            return

        bx, by, bz = props["bbox"]
        cx, cy, cz = props["centre"]
        # in the document's own units; a millimetre part keeps the
        # centimetres a workshop reads volumes and areas in
        unit = unitlib.known(getattr(doc, "units", "mm") or "mm")
        size = lambda v: unitlib.fmt(unitlib.to_unit(v, unit), 5)  # noqa
        if unit == "mm":
            volume = "%.3f cm3" % (props["volume_mm3"] / 1000.0)
            area = "%.2f cm2" % (props["area_mm2"] / 100.0)
        else:
            volume = unitlib.volume_text(props["volume_mm3"], unit, 5)
            area = unitlib.area_text(props["area_mm2"], unit, 5)
        rows = [
            ("Bounding box", "%s x %s x %s %s" % (size(bx), size(by),
                                                  size(bz), unit)),
            ("Volume", volume),
            ("Surface area", area),
            ("Mass", unitlib.mass_text(props["mass_g"], unit, 5)),
            ("Centre of mass", "%s, %s, %s %s" % (size(cx), size(cy),
                                                  size(cz), unit)),
            ("Faces", str(props["faces"])),
            ("Edges", str(props["edges"])),
        ]
        for label, value in rows:
            self._row(label, value)

    def _row(self, label: str, value: str) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        left = QtWidgets.QTableWidgetItem(label)
        left.setForeground(QtGui.QBrush(QtGui.QColor(C.text_dim)))
        right = QtWidgets.QTableWidgetItem(value)
        self.table.setItem(row, 0, left)
        self.table.setItem(row, 1, right)


class SpaceMouseDialog(QtWidgets.QDialog):
    """Tune the 3Dconnexion puck: speeds, deadzone and axis directions."""

    AXIS_LABELS = (
        ("tx", "Slide left / right"),
        ("ty", "Push / pull"),
        ("tz", "Slide up / down"),
        ("rx", "Tilt forward / back"),
        ("ry", "Tilt left / right"),
        ("rz", "Twist"),
    )

    def __init__(self, mouse, parent=None) -> None:
        super().__init__(parent)
        self.mouse = mouse
        # widgets are wired up as they are built, so hold off writing settings
        # back until every one of them exists
        self._ready = False
        self.setWindowTitle("SpaceMouse")
        self.setWindowIcon(icons.icon("spacemouse", 24))
        self.setMinimumWidth(360)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(9)

        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.enabled = QtWidgets.QCheckBox("Use the SpaceMouse to navigate")
        self.enabled.setChecked(mouse.settings.enabled)
        self.enabled.toggled.connect(self._apply)
        layout.addWidget(self.enabled)

        form = QtWidgets.QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(6)
        self.sliders = {}
        for row, (key, label, lo, hi) in enumerate((
                ("sensitivity", "Overall speed", 10, 300),
                ("pan_speed", "Pan", 10, 300),
                ("zoom_speed", "Zoom", 10, 300),
                ("rotate_speed", "Orbit", 10, 300),
                ("deadzone", "Dead zone", 0, 40))):
            form.addWidget(QtWidgets.QLabel(label), row, 0)
            slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
            slider.setRange(lo, hi)
            slider.setValue(int(getattr(mouse.settings, key) * 100))
            readout = QtWidgets.QLabel()
            slider.valueChanged.connect(
                lambda v, k=key, r=readout: self._slider_changed(k, v, r))
            self._slider_changed(key, slider.value(), readout)
            form.addWidget(slider, row, 1)
            form.addWidget(readout, row, 2)
            self.sliders[key] = slider
        layout.addLayout(form)

        self.dominant = QtWidgets.QCheckBox(
            "Dominant axis only (one motion at a time)")
        self.dominant.setChecked(mouse.settings.dominant_axis)
        self.dominant.toggled.connect(self._apply)
        layout.addWidget(self.dominant)

        box = QtWidgets.QGroupBox("Reverse an axis")
        grid = QtWidgets.QGridLayout(box)
        grid.setVerticalSpacing(3)
        self.inverts = {}
        for i, (axis, label) in enumerate(self.AXIS_LABELS):
            cb = QtWidgets.QCheckBox(label)
            cb.setChecked(mouse.settings.invert.get(axis, False))
            cb.toggled.connect(self._apply)
            grid.addWidget(cb, i % 3, i // 3)
            self.inverts[axis] = cb
        layout.addWidget(box)

        self.live = QtWidgets.QLabel("Move the puck to see live values.")
        self.live.setProperty("hint", True)
        self.live.setStyleSheet("font-family: %s;" % MONO_STACK)
        layout.addWidget(self.live)

        row = QtWidgets.QHBoxLayout()
        reset = QtWidgets.QPushButton("Reset")
        reset.clicked.connect(self._reset)
        row.addWidget(reset)
        row.addStretch(1)
        close = QtWidgets.QPushButton("Done")
        close.setProperty("primary", True)
        close.clicked.connect(self.accept)
        row.addWidget(close)
        layout.addLayout(row)

        mouse.moved.connect(self._show_motion)
        mouse.connected.connect(lambda _n: self.refresh_status())
        mouse.disconnected.connect(self.refresh_status)

        # "connected" and "actually sending" are different problems, and a
        # wireless receiver can sit in the first state forever, so the status
        # keeps reporting what has arrived rather than only what was opened
        self._tick = QtCore.QTimer(self)
        self._tick.setInterval(500)
        self._tick.timeout.connect(self.refresh_status)
        self._tick.start()
        self.refresh_status()

        self._ready = True

    def _slider_changed(self, key: str, value: int,
                        readout: QtWidgets.QLabel) -> None:
        readout.setText("%.2f" % (value / 100.0))
        readout.setMinimumWidth(34)
        self._apply()

    def _apply(self) -> None:
        if not self._ready:
            return
        s = self.mouse.settings
        s.enabled = self.enabled.isChecked()
        s.dominant_axis = self.dominant.isChecked()
        for key, slider in self.sliders.items():
            setattr(s, key, slider.value() / 100.0)
        for axis, cb in self.inverts.items():
            s.invert[axis] = cb.isChecked()

    def _reset(self) -> None:
        from .spacemouse import SpaceMouseSettings

        defaults = SpaceMouseSettings()
        for key, slider in self.sliders.items():
            slider.setValue(int(getattr(defaults, key) * 100))
        self.dominant.setChecked(defaults.dominant_axis)
        for axis, cb in self.inverts.items():
            cb.setChecked(False)

    def refresh_status(self) -> None:
        from . import spacemouse

        if not spacemouse.available():
            self.status.setText("The hidapi package is not installed, so the "
                                "SpaceMouse cannot be read.")
            self.status.setStyleSheet("color: %s;" % C.error)
        elif self.mouse.is_connected:
            self.status.setText("Connected: %s\n%s"
                                % (self.mouse.summary(), self.mouse.traffic()))
            self.status.setStyleSheet(
                "color: %s;" % (C.ok if self.mouse.frames else C.warn))
        else:
            self.status.setText("No SpaceMouse found. It will connect on its "
                                "own once plugged in.")
            self.status.setStyleSheet("color: %s;" % C.warn)

    def _show_motion(self, *values) -> None:
        self.live.setText("  ".join(
            "%s %+.2f" % (name, v)
            for name, v in zip(("Tx", "Ty", "Tz", "Rx", "Ry", "Rz"), values)))


class MeasureDialog(QtWidgets.QDialog):
    """Reads out whatever is selected in the viewport."""

    def __init__(self, host, parent=None) -> None:
        super().__init__(parent or host)
        self.host = host
        self.setWindowTitle("Measure")
        self.setWindowIcon(icons.icon("measure", 24))
        self.setWindowFlags(QtCore.Qt.Tool | QtCore.Qt.WindowTitleHint
                            | QtCore.Qt.WindowCloseButtonHint)
        self.setMinimumWidth(300)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)

        self.mode = QtWidgets.QComboBox()
        self.mode.addItem("Faces", "face")
        self.mode.addItem("Edges", "edge")
        self.mode.addItem("Vertices", "vertex")
        layout.addWidget(self.mode)

        self.readout = QtWidgets.QPlainTextEdit()
        self.readout.setReadOnly(True)
        self.readout.setMinimumHeight(150)
        layout.addWidget(self.readout, 1)

        close = QtWidgets.QPushButton("Close")
        close.clicked.connect(self.close)
        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close)
        layout.addLayout(row)

        self.mode.currentIndexChanged.connect(self._mode_changed)
        self._mode_changed()

    def _mode_changed(self) -> None:
        self.host.set_pick_mode(self.mode.currentData())
        self.refresh()

    def refresh(self) -> None:
        from ..core import kernel

        vp = self.host.viewport
        shapes = vp.selected_shapes()
        if not shapes:
            self.readout.setPlainText("Select geometry in the view.")
            return

        lines = []
        from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_VERTEX
        from OCP.TopoDS import TopoDS

        # in the units of whatever is being measured
        doc = getattr(self.host, "active_document", None)
        unit = unitlib.known(getattr(doc, "units", "mm") or "mm")

        def length(v: float) -> str:
            return unitlib.length_text(v, unit, 6)

        def at(p) -> str:
            return ", ".join(unitlib.fmt(unitlib.to_unit(c, unit), 6)
                             for c in p)

        points = []
        for shape in shapes:
            kind = shape.ShapeType()
            centre = kernel.shape_centre(shape)
            points.append(centre)
            if kind == TopAbs_FACE:
                face = TopoDS.Face_s(shape)
                lines.append("Face   area %s" % unitlib.area_text(
                    kernel.face_area(face), unit, 6))
            elif kind == TopAbs_EDGE:
                edge = TopoDS.Edge_s(shape)
                lines.append("Edge   length %s"
                             % length(kernel.edge_length(edge)))
            else:
                lines.append("Vertex")
            lines.append("       at %s" % at(centre))

        if len(points) == 2:
            import math
            d = math.dist(points[0], points[1])
            delta = tuple(points[1][i] - points[0][i] for i in range(3))
            lines.append("")
            lines.append("Distance   %s" % length(d))
            lines.append("dX %s   dY %s   dZ %s" % tuple(
                unitlib.fmt(unitlib.to_unit(c, unit), 6) for c in delta))

        self.readout.setPlainText("\n".join(lines))

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self.host.set_pick_mode(None)
        self.host.measure_dialog = None
        super().closeEvent(event)
