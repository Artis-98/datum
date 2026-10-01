"""Small reusable input widgets."""

from __future__ import annotations

from typing import Callable, List, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from ..core import units as unitlib
from ..core.params import ExpressionError, ParameterTable, evaluate
from . import icons
from .theme import C


class DocumentView:
    """What an expression box asks of a document that is not a part.

    Its parameters to evaluate against, an assembly's with its parts'
    values as well, and the units a bare number is typed in.
    """

    def __init__(self, document) -> None:
        self._document = document

    def scope(self):
        out = dict(getattr(self._document, "component_values", {}) or {})
        out.update(self._document.params.scope())
        return out

    @property
    def units(self) -> str:
        return getattr(self._document, "units", "mm") or "mm"


class ExpressionEdit(QtWidgets.QWidget):
    """A text field holding a parametric expression, with a live readout.

    ``unit`` says what the field holds: "mm" a length, "deg" an angle, ""
    a plain number.  A length is shown and typed in the document's units
    (``params.units``), and what is typed is stored with its units written
    in; see core.units.
    """

    changed = QtCore.Signal()

    def __init__(self, value: str = "0", params: Optional[ParameterTable] = None,
                 unit: str = "mm", parent=None) -> None:
        super().__init__(parent)
        self.params = params
        self.unit = unit
        value = self._for_display(value)

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        self.edit = QtWidgets.QLineEdit(str(value))
        self.edit.setMinimumWidth(96)
        layout.addWidget(self.edit, 1)

        self.readout = QtWidgets.QLabel("")
        self.readout.setProperty("hint", True)
        self.readout.setMinimumWidth(74)
        self.readout.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        layout.addWidget(self.readout)

        self.edit.textChanged.connect(self._validate)
        self._validate()

    def _scope(self):
        return self.params.scope() if self.params is not None else {}

    @property
    def _length(self) -> bool:
        return self.unit == "mm"

    @property
    def doc_unit(self) -> str:
        return unitlib.known(getattr(self.params, "units", "mm") or "mm")

    def _for_display(self, stored: str) -> str:
        if not self._length:
            return str(stored)
        return unitlib.for_display(str(stored), self.doc_unit)

    def _validate(self) -> None:
        text = self.text()
        try:
            value = evaluate(text, self._scope())
            if self._length:
                self.readout.setText("= %s" % unitlib.length_text(
                    value, self.doc_unit))
            else:
                self.readout.setText("= %.4g %s" % (value, self.unit))
            self.readout.setStyleSheet("color: %s;" % C.text_dim)
            self.edit.setProperty("invalid", False)
            self._valid = True
        except ExpressionError as exc:
            self.readout.setText(str(exc)[:22])
            self.readout.setStyleSheet("color: %s;" % C.error)
            self.edit.setProperty("invalid", True)
            self._valid = False
        self.edit.style().unpolish(self.edit)
        self.edit.style().polish(self.edit)
        self.changed.emit()

    @property
    def valid(self) -> bool:
        return getattr(self, "_valid", False)

    def text(self) -> str:
        """What to store: in a length field, with the units written in."""
        typed = self.edit.text().strip()
        if not self._length:
            return typed
        return unitlib.for_storage(typed, self.doc_unit)

    def value(self, default: float = 0.0) -> float:
        try:
            return evaluate(self.text(), self._scope())
        except ExpressionError:
            return default

    def set_text(self, text: str) -> None:
        self.edit.setText(self._for_display(str(text)))

    def set_params(self, params: ParameterTable) -> None:
        self.params = params
        self._validate()

    def focus(self) -> None:
        self.edit.setFocus()
        self.edit.selectAll()


class SegmentedControl(QtWidgets.QWidget):
    """A row of mutually exclusive icon buttons, like Inventor's op selector."""

    changed = QtCore.Signal(str)

    def __init__(self, options, parent=None) -> None:
        """``options`` is a list of (key, icon name, tooltip)."""
        super().__init__(parent)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self._group = QtWidgets.QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons = {}

        for key, icon_name, tip in options:
            btn = QtWidgets.QToolButton(self)
            btn.setObjectName("RibbonButton")
            btn.setIcon(icons.icon(icon_name, 20))
            btn.setIconSize(QtCore.QSize(20, 20))
            btn.setCheckable(True)
            btn.setToolTip(tip)
            btn.setFixedSize(30, 26)
            btn.setCursor(QtCore.Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, k=key: self.changed.emit(k))
            self._group.addButton(btn)
            layout.addWidget(btn)
            self._buttons[key] = btn

        layout.addStretch(1)
        if options:
            self._buttons[options[0][0]].setChecked(True)
            self._current = options[0][0]

    def value(self) -> str:
        for key, btn in self._buttons.items():
            if btn.isChecked():
                return key
        return self._current

    def set_value(self, key: str) -> None:
        btn = self._buttons.get(key)
        if btn is not None:
            btn.setChecked(True)


class SelectionField(QtWidgets.QWidget):
    """A button that puts the viewport into a pick mode and counts the picks."""

    pick_toggled = QtCore.Signal(bool)
    cleared = QtCore.Signal()

    def __init__(self, label: str, parent=None) -> None:
        super().__init__(parent)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        self.button = QtWidgets.QPushButton(label)
        self.button.setCheckable(True)
        self.button.setIcon(icons.icon("select", 15))
        self.button.setMinimumWidth(112)
        layout.addWidget(self.button)

        self.count = QtWidgets.QLabel("none")
        self.count.setProperty("hint", True)
        layout.addWidget(self.count, 1)

        self.clear_btn = QtWidgets.QToolButton()
        self.clear_btn.setIcon(icons.icon("cancel", 14))
        self.clear_btn.setToolTip("Clear selection")
        self.clear_btn.setAutoRaise(True)
        layout.addWidget(self.clear_btn)

        self.button.toggled.connect(self.pick_toggled.emit)
        self.clear_btn.clicked.connect(self.cleared.emit)

    def set_count(self, n: int, noun: str = "edge") -> None:
        if n == 0:
            self.count.setText("none selected")
            self.count.setStyleSheet("color: %s;" % C.text_dim)
        else:
            self.count.setText("%d %s%s" % (n, noun, "" if n == 1 else "s"))
            self.count.setStyleSheet("color: %s;" % C.ok)

    @property
    def picking(self) -> bool:
        return self.button.isChecked()

    def set_picking(self, on: bool) -> None:
        self.button.setChecked(on)


class FormRows(QtWidgets.QWidget):
    """A tidy two-column label/field form."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.grid = QtWidgets.QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(10)
        self.grid.setVerticalSpacing(7)
        self.grid.setColumnStretch(1, 1)
        self._row = 0
        self._labels: "dict[int, QtWidgets.QLabel]" = {}

    def add(self, label: str, widget: QtWidgets.QWidget) -> QtWidgets.QWidget:
        if label:
            lbl = QtWidgets.QLabel(label)
            lbl.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            lbl.setProperty("hint", True)
            self.grid.addWidget(lbl, self._row, 0)
            self.grid.addWidget(widget, self._row, 1)
            self._labels[id(widget)] = lbl
        else:
            self.grid.addWidget(widget, self._row, 0, 1, 2)
        self._row += 1
        return widget

    def set_label(self, widget: QtWidgets.QWidget, text: str) -> None:
        """Retitle a row whose meaning changes with the mode it is in."""
        label = self._labels.get(id(widget))
        if label is not None:
            label.setText(text)

    def set_visible(self, widget: QtWidgets.QWidget, on: bool) -> None:
        """Show or hide a row, label and all, so no orphan caption is left."""
        widget.setVisible(on)
        label = self._labels.get(id(widget))
        if label is not None:
            label.setVisible(on)

    def add_separator(self) -> None:
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setStyleSheet("color: %s;" % C.border)
        self.grid.addWidget(line, self._row, 0, 1, 2)
        self._row += 1
