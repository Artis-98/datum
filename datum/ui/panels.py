"""Parameter table, properties readout and other side panels."""

from __future__ import annotations

from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

from ..core.document import Document
from ..core.params import ExpressionError
from . import icons
from .theme import MONO_STACK, C

COLUMNS = ("Name", "Expression", "Value", "Unit", "Comment")


class ParametersDialog(QtWidgets.QDialog):
    """Edit the document's named parameters."""

    changed = QtCore.Signal()

    def __init__(self, doc: Document, parent=None) -> None:
        super().__init__(parent)
        self.doc = doc
        self._loading = False

        self.setWindowTitle("Parameters")
        self.setWindowIcon(icons.icon("params", 24))
        self.resize(660, 420)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(9)

        bar = QtWidgets.QHBoxLayout()
        self.add_btn = QtWidgets.QPushButton("Add")
        self.add_btn.setIcon(icons.icon("new", 15))
        self.del_btn = QtWidgets.QPushButton("Delete")
        self.del_btn.setIcon(icons.icon("delete", 15))
        bar.addWidget(self.add_btn)
        bar.addWidget(self.del_btn)
        bar.addStretch(1)
        hint = QtWidgets.QLabel(
            "Expressions may reference other parameters, e.g. width / 2 - wall")
        hint.setProperty("hint", True)
        bar.addWidget(hint)
        root.addLayout(bar)

        self.table = QtWidgets.QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QtWidgets.QHeaderView.Stretch)
        root.addWidget(self.table, 1)

        self.message = QtWidgets.QLabel("")
        self.message.setProperty("hint", True)
        root.addWidget(self.message)

        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        close = QtWidgets.QPushButton("Done")
        close.setProperty("primary", True)
        buttons.addWidget(close)
        root.addLayout(buttons)

        self.add_btn.clicked.connect(self._add)
        self.del_btn.clicked.connect(self._delete)
        close.clicked.connect(self.accept)
        self.table.itemChanged.connect(self._item_changed)

        self.reload()

    # ----------------------------------------------------------------------

    def reload(self) -> None:
        self._loading = True
        self.table.setRowCount(0)
        errors = []
        for param in self.doc.params:
            row = self.table.rowCount()
            self.table.insertRow(row)

            name = QtWidgets.QTableWidgetItem(param.name)
            expression = QtWidgets.QTableWidgetItem(param.expression)
            value = QtWidgets.QTableWidgetItem(
                "-" if param.error else "%.6g" % param.value)
            value.setFlags(value.flags() & ~QtCore.Qt.ItemIsEditable)
            unit = QtWidgets.QTableWidgetItem(param.unit)
            comment = QtWidgets.QTableWidgetItem(param.comment)

            if param.error:
                for item in (name, expression, value):
                    item.setForeground(QtGui.QBrush(QtGui.QColor(C.error)))
                expression.setToolTip(param.error)
                errors.append("%s: %s" % (param.name, param.error))
            else:
                value.setForeground(QtGui.QBrush(QtGui.QColor(C.ok)))

            for col, item in enumerate((name, expression, value, unit, comment)):
                self.table.setItem(row, col, item)

        self.message.setText("; ".join(errors))
        self.message.setStyleSheet("color: %s;" % (C.error if errors
                                                   else C.text_dim))
        self._loading = False

    def _add(self) -> None:
        base = "d"
        i = 1
        while "%s%d" % (base, i) in self.doc.params:
            i += 1
        self.doc.params.add("%s%d" % (base, i), "10")
        self.reload()
        self.changed.emit()
        self.table.setCurrentCell(self.table.rowCount() - 1, 0)

    def _delete(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        name = self.table.item(row, 0).text()
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
        names = self.doc.params.names()
        if row >= len(names):
            return
        name = names[row]
        param = self.doc.params.get(name)
        if param is None:
            return
        try:
            if col == 0:
                self.doc.params.rename(name, item.text().strip())
            elif col == 1:
                self.doc.params.set_expression(name, item.text())
            elif col == 3:
                param.unit = item.text().strip() or "mm"
            elif col == 4:
                param.comment = item.text()
        except ExpressionError as exc:
            QtWidgets.QMessageBox.warning(self, "Parameter", str(exc))
        self.reload()
        self.changed.emit()


class PropertiesPanel(QtWidgets.QWidget):
    """Mass properties and bounding box of the current body."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)

        material = QtWidgets.QHBoxLayout()
        material.addWidget(QtWidgets.QLabel("Material"))
        self.material = QtWidgets.QComboBox()
        for name, density in (("Generic", 1.0), ("ABS", 1.04), ("PLA", 1.24),
                              ("PETG", 1.27), ("Nylon", 1.14),
                              ("Aluminium 6061", 2.70), ("Steel", 7.85),
                              ("Stainless 304", 8.00), ("Brass", 8.50),
                              ("Titanium", 4.51)):
            self.material.addItem(name, density)
        material.addWidget(self.material, 1)
        layout.addLayout(material)

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
        index = self.material.findText(doc.material)
        if index >= 0:
            self.material.setCurrentIndex(index)
        doc.density = float(self.material.currentData() or 1.0)
        doc.material = self.material.currentText()

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
        rows = [
            ("Bounding box", "%.2f x %.2f x %.2f mm" % (bx, by, bz)),
            ("Volume", "%.3f cm3" % (props["volume_mm3"] / 1000.0)),
            ("Surface area", "%.2f cm2" % (props["area_mm2"] / 100.0)),
            ("Mass", "%.2f g" % props["mass_g"]),
            ("Centre of mass", "%.2f, %.2f, %.2f" % (cx, cy, cz)),
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

        points = []
        for shape in shapes:
            kind = shape.ShapeType()
            centre = kernel.shape_centre(shape)
            points.append(centre)
            if kind == TopAbs_FACE:
                face = TopoDS.Face_s(shape)
                lines.append("Face   area %.3f mm2" % kernel.face_area(face))
            elif kind == TopAbs_EDGE:
                edge = TopoDS.Edge_s(shape)
                lines.append("Edge   length %.4f mm" % kernel.edge_length(edge))
            else:
                lines.append("Vertex")
            lines.append("       at %.3f, %.3f, %.3f" % centre)

        if len(points) == 2:
            import math
            d = math.dist(points[0], points[1])
            delta = tuple(points[1][i] - points[0][i] for i in range(3))
            lines.append("")
            lines.append("Distance   %.4f mm" % d)
            lines.append("dX %.3f   dY %.3f   dZ %.3f" % delta)

        self.readout.setPlainText("\n".join(lines))

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self.host.set_pick_mode(None)
        self.host.measure_dialog = None
        super().closeEvent(event)
