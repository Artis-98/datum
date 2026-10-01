"""Preferences: the window for the things that belong to the person.

Inventor calls this Application Options, and the distinction it draws is
the one that matters: a document holds what is true about the design, and
this holds what is true about whoever is looking at it.  Your name belongs
in the first kind, because a part that says who drew it is stating a fact
about the part.  The colour of the background belongs in the second, and
has no business travelling inside a file somebody else will open.
"""

from __future__ import annotations

from typing import Dict

from PySide6 import QtCore, QtGui, QtWidgets

from ..core import prefs as core_prefs
from . import icons
from .theme import C, apply_colours, as_hex, shipped


class ColourButton(QtWidgets.QPushButton):
    """A swatch that opens the colour picker."""

    picked = QtCore.Signal(str)

    def __init__(self, colour: str, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(52, 22)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self._colour = colour
        self._paint()
        self.clicked.connect(self._choose)

    def colour(self) -> str:
        return self._colour

    def set_colour(self, colour: str) -> None:
        self._colour = colour
        self._paint()

    def _paint(self) -> None:
        pixmap = QtGui.QPixmap(38, 14)
        pixmap.fill(QtGui.QColor(self._colour))
        painter = QtGui.QPainter(pixmap)
        painter.setPen(QtGui.QColor(C.border_light))
        painter.drawRect(0, 0, 37, 13)
        painter.end()
        self.setIcon(QtGui.QIcon(pixmap))
        self.setIconSize(pixmap.size())

    def _choose(self) -> None:
        chosen = QtWidgets.QColorDialog.getColor(
            QtGui.QColor(self._colour), self, "Choose colour")
        if chosen.isValid():
            self.set_colour(chosen.name())
            self.picked.emit(self._colour)


class PreferencesDialog(QtWidgets.QDialog):
    """Who you are, and how you want it to look."""

    # display changes are live, so the window behind can repaint itself
    display_changed = QtCore.Signal()

    COLOURS = (
        ("bg_top", "Background, top"),
        ("bg_bottom", "Background, bottom"),
        ("sketch_line", "Sketch: fully constrained"),
        ("sketch_free", "Sketch: still free to move"),
        ("sketch_construction", "Sketch: construction"),
    )

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.prefs = core_prefs.prefs()
        self.setWindowTitle("Preferences")
        self.setWindowIcon(icons.icon("edit", 24))
        self.setMinimumWidth(520)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(9)

        tabs = QtWidgets.QTabWidget()
        tabs.addTab(self._general_page(), "General")
        tabs.addTab(self._display_page(), "Display")
        root.addWidget(tabs, 1)

        where = QtWidgets.QLabel("Kept in %s" % core_prefs.config_path())
        where.setProperty("hint", True)
        where.setWordWrap(True)
        root.addWidget(where)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
            | QtWidgets.QDialogButtonBox.Apply)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QtWidgets.QDialogButtonBox.Apply).clicked.connect(
            self.apply)
        root.addWidget(buttons)

    # -- pages --------------------------------------------------------------

    def _general_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(page)
        form.setContentsMargins(12, 12, 12, 12)
        form.setSpacing(8)

        note = QtWidgets.QLabel(
            "Stamped onto every new part, assembly and drawing, where a "
            "title block and a parts list can ask for it. Documents that "
            "already say who drew them are left alone.")
        note.setWordWrap(True)
        note.setProperty("hint", True)
        form.addRow(note)

        self.name_edit = QtWidgets.QLineEdit(self.prefs.name)
        self.name_edit.setPlaceholderText("the name that appears as Designer")
        form.addRow("Name", self.name_edit)

        self.initials_edit = QtWidgets.QLineEdit(self.prefs.initials)
        self.initials_edit.setMaxLength(6)
        self.initials_edit.setPlaceholderText("for revision tables")
        form.addRow("Initials", self.initials_edit)

        self.company_edit = QtWidgets.QLineEdit(self.prefs.company)
        self.company_edit.setPlaceholderText("appears in the title block")
        form.addRow("Company", self.company_edit)

        from ..core import units as unitlib
        self.units_combo = QtWidgets.QComboBox()
        for key, label in unitlib.LABELS.items():
            self.units_combo.addItem("%s  (%s)" % (label, key), key)
        self.units_combo.setCurrentIndex(
            max(0, self.units_combo.findData(self.prefs.units)))
        self.units_combo.setToolTip(
            "What new parts and assemblies are drawn in. A document's own "
            "units are changed from the unit in the status bar.")
        form.addRow("Units for new documents", self.units_combo)
        return page

    def _display_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(page)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)

        note = QtWidgets.QLabel(
            "Yours, not the document's: these travel with the application "
            "and never inside a file.")
        note.setWordWrap(True)
        note.setProperty("hint", True)
        outer.addWidget(note)

        form = QtWidgets.QFormLayout()
        form.setSpacing(8)
        self.colour_buttons: Dict[str, ColourButton] = {}
        for key, label in self.COLOURS:
            button = ColourButton(as_hex(getattr(C, key)))
            button.picked.connect(lambda _c: self.apply())
            self.colour_buttons[key] = button
            form.addRow(label, button)
        self.orbit_combo = QtWidgets.QComboBox()
        self.orbit_combo.addItem("Free", core_prefs.ORBIT_FREE)
        self.orbit_combo.addItem("Turntable, Z always up",
                                 core_prefs.ORBIT_TURNTABLE)
        self.orbit_combo.setToolTip(
            "Free turns about any axis. Turntable spins about Z and tilts, "
            "and never lays the model on its side.")
        self.orbit_combo.setCurrentIndex(
            max(0, self.orbit_combo.findData(self.prefs.orbit)))
        form.addRow("Orbit", self.orbit_combo)
        outer.addLayout(form)

        outer.addStretch(1)
        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        reset = QtWidgets.QPushButton("Reset to defaults")
        reset.clicked.connect(self.reset_colours)
        row.addWidget(reset)
        outer.addLayout(row)
        return page

    # -- saving -------------------------------------------------------------

    def reset_colours(self) -> None:
        for key, button in self.colour_buttons.items():
            button.set_colour(as_hex(shipped(key)))
        self.apply()

    def gather(self) -> None:
        self.prefs.name = self.name_edit.text().strip()
        self.prefs.initials = self.initials_edit.text().strip()
        self.prefs.company = self.company_edit.text().strip()
        self.prefs.units = self.units_combo.currentData() or "mm"
        self.prefs.orbit = (self.orbit_combo.currentData()
                            or core_prefs.ORBIT_FREE)
        # only what differs from the shipped palette is written, so a
        # retuned default reaches everybody who never touched it
        self.prefs.colours = {
            key: button.colour() for key, button in self.colour_buttons.items()
            if button.colour().lower() != as_hex(shipped(key)).lower()}

    def apply(self) -> None:
        self.gather()
        self.prefs.save()
        apply_colours(self.prefs.colours)
        self.display_changed.emit()

    def accept(self) -> None:
        self.apply()
        super().accept()


def open_dialog(parent, on_display_change=None) -> None:
    """Show Preferences, applying anything it changes as it is changed."""
    dialog = PreferencesDialog(parent)
    if on_display_change is not None:
        dialog.display_changed.connect(on_display_change)
    dialog.exec()
