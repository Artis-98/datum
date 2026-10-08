"""Choosing a material, choosing an appearance, and editing both.

Two pickers live on the top strip where Inventor keeps them, because what
a part is made of is a property of the part and not a command you run. The
browser behind them is the place to make new ones.

The split between the two is the point, and the interface has to keep
saying so: picking a material changes what the part weighs, picking an
appearance changes only what it looks like. So the appearance picker shows
"From the material" as its first entry, and that is what a part uses until
somebody deliberately overrides it.
"""

from __future__ import annotations

import os
from typing import Any, List, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from ..core import materials
from ..core.materials import Appearance, Material
from . import icons
from .theme import C

SWATCH = 46


def swatch(appearance: Appearance, size: int = SWATCH,
           radius: int = 6) -> QtGui.QPixmap:
    """A little ball of the appearance, the way a material browser shows one.

    Drawn rather than stored, so a colour change shows up immediately and
    there are no image files to keep in step with the library.
    """
    ratio = 2
    pixmap = QtGui.QPixmap(size * ratio, size * ratio)
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
    painter.scale(ratio, ratio)

    base = QtGui.QColor(appearance.colour)
    rect = QtCore.QRectF(1, 1, size - 2, size - 2)

    # the highlight tightens as the surface gets smoother, which is the
    # one cue that tells brushed from polished at this size
    spread = 0.25 + appearance.roughness * 0.75
    gradient = QtGui.QRadialGradient(
        rect.center() + QtCore.QPointF(-size * 0.17, -size * 0.2),
        size * spread)
    top = base.lighter(190 if not appearance.metallic else 165)
    gradient.setColorAt(0.0, top)
    gradient.setColorAt(0.55, base)
    gradient.setColorAt(1.0, base.darker(165))

    if appearance.opacity < 0.95:
        painter.setOpacity(0.35 + appearance.opacity * 0.6)
    painter.setBrush(QtGui.QBrush(gradient))
    painter.setPen(QtGui.QPen(base.darker(220), 1))
    painter.drawRoundedRect(rect, radius, radius)
    painter.end()
    return pixmap


class PickerCombo(QtWidgets.QComboBox):
    """A combo that shows a swatch beside every entry."""

    def __init__(self, parent=None, width: int = 168) -> None:
        super().__init__(parent)
        self.setFixedWidth(width)
        # pinned, so the strip it lives on can be a known height and the
        # text cannot end up half outside it
        self.setFixedHeight(24)
        self.setIconSize(QtCore.QSize(15, 15))
        self.setMaxVisibleItems(24)


class MaterialBar(QtWidgets.QWidget):
    """The two pickers, for the strip along the top."""

    material_changed = QtCore.Signal(str)
    appearance_changed = QtCore.Signal(str)
    browse_requested = QtCore.Signal(bool)      # True for appearances

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        self.material = PickerCombo(self)
        self.material.setToolTip("What this part is made of: its density, "
                                 "and what it would do under load")
        row.addWidget(self.material)

        browse_m = QtWidgets.QToolButton(self)
        browse_m.setIcon(icons.icon("material", 15))
        browse_m.setToolTip("Material Browser")
        browse_m.setCursor(QtCore.Qt.PointingHandCursor)
        browse_m.clicked.connect(lambda: self.browse_requested.emit(False))
        row.addWidget(browse_m)

        row.addSpacing(8)

        self.appearance = PickerCombo(self)
        self.appearance.setToolTip("How it looks. This changes nothing "
                                   "about what it weighs")
        row.addWidget(self.appearance)

        browse_a = QtWidgets.QToolButton(self)
        browse_a.setIcon(icons.icon("material", 15))
        browse_a.setToolTip("Appearance Browser")
        browse_a.setCursor(QtCore.Qt.PointingHandCursor)
        browse_a.clicked.connect(lambda: self.browse_requested.emit(True))
        row.addWidget(browse_a)

        self._quiet = False
        self.material.currentIndexChanged.connect(self._material_picked)
        self.appearance.currentIndexChanged.connect(self._appearance_picked)
        self.refresh()

    # ---------------------------------------------------------------- fill

    def refresh(self, material: str = "", appearance: str = "") -> None:
        """Rebuild both lists, keeping what is chosen."""
        library = materials.library()
        self._quiet = True
        try:
            want_m = material or self.material.currentData() or "Generic"
            self.material.clear()
            for item in sorted(library.materials.values(),
                               key=lambda m: (m.category, m.name)):
                look = library.appearance(item.appearance)
                self.material.addItem(
                    QtGui.QIcon(swatch(look, 15, 3)),
                    "%s  %.2f g/cm³" % (item.name, item.density),
                    item.name)
            if want_m and self.material.findData(want_m) < 0:
                # a material the library has never heard of, named by a file
                self.material.addItem(want_m + "  (not in the library)",
                                      want_m)
            index = self.material.findData(want_m)
            self.material.setCurrentIndex(max(0, index))

            want_a = appearance if appearance or material else \
                (self.appearance.currentData() or "")
            self.appearance.clear()
            self.appearance.addItem("From the material", "")
            for item in sorted(library.appearances.values(),
                               key=lambda a: (a.category, a.name)):
                self.appearance.addItem(QtGui.QIcon(swatch(item, 15, 3)),
                                        item.name, item.name)
            index = self.appearance.findData(want_a)
            self.appearance.setCurrentIndex(max(0, index))
        finally:
            self._quiet = False

    def set_document(self, document) -> None:
        """Show what this document is made of, or grey out if it has none."""
        has = document is not None and hasattr(document, "material")
        self.setEnabled(bool(has))
        if not has:
            return
        self.refresh(document.material, getattr(document, "appearance", ""))

    # -------------------------------------------------------------- signals

    def _material_picked(self, _index: int) -> None:
        if not self._quiet:
            self.material_changed.emit(self.material.currentData() or "")

    def _appearance_picked(self, _index: int) -> None:
        if not self._quiet:
            self.appearance_changed.emit(self.appearance.currentData() or "")


# ---------------------------------------------------------------- browsing


class MaterialEditor(QtWidgets.QDialog):
    """Make or change one material, or one appearance."""

    def __init__(self, parent, item: Any, appearance: bool,
                 read_only: bool = False) -> None:
        super().__init__(parent)
        self.item = item
        self.is_appearance = appearance
        self.read_only = read_only
        self.setWindowTitle(("Appearance" if appearance else "Material")
                            + ": " + item.name)
        self.setMinimumWidth(420)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        if read_only:
            note = QtWidgets.QLabel(
                "This one ships with DATUM, so it cannot be changed. "
                "Duplicate it to make one you can edit.")
            note.setWordWrap(True)
            note.setProperty("hint", True)
            root.addWidget(note)

        form = QtWidgets.QFormLayout()
        self.name = QtWidgets.QLineEdit(item.name)
        form.addRow("Name", self.name)
        self.category = QtWidgets.QComboBox()
        self.category.setEditable(True)
        library = materials.library()
        for category in library.categories(appearance):
            self.category.addItem(category)
        self.category.setCurrentText(item.category)
        form.addRow("Category", self.category)

        if appearance:
            self._build_appearance(form)
        else:
            self._build_material(form, library)
        root.addLayout(form)

        self.preview = QtWidgets.QLabel()
        self.preview.setAlignment(QtCore.Qt.AlignCenter)
        root.addWidget(self.preview)
        self._repaint_preview()

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        if read_only:
            for child in self.findChildren(QtWidgets.QWidget):
                if isinstance(child, (QtWidgets.QLineEdit, QtWidgets.QComboBox,
                                      QtWidgets.QDoubleSpinBox,
                                      QtWidgets.QCheckBox, QtWidgets.QSlider)):
                    child.setEnabled(False)

    # ------------------------------------------------------------ building

    def _build_appearance(self, form) -> None:
        item = self.item
        self.colour = QtWidgets.QPushButton(item.colour)
        self.colour.clicked.connect(self._pick_colour)
        self._set_colour_button(item.colour)
        form.addRow("Colour", self.colour)

        self.roughness = QtWidgets.QDoubleSpinBox()
        self.roughness.setRange(0.0, 1.0)
        self.roughness.setSingleStep(0.05)
        self.roughness.setDecimals(2)
        self.roughness.setValue(item.roughness)
        self.roughness.setToolTip("0 is a mirror, 1 is dead matte")
        self.roughness.valueChanged.connect(self._repaint_preview)
        form.addRow("Roughness", self.roughness)

        self.metallic = QtWidgets.QCheckBox("Metallic")
        self.metallic.setChecked(item.metallic)
        self.metallic.setToolTip(
            "A metal's highlight takes its own colour rather than white")
        self.metallic.toggled.connect(self._repaint_preview)
        form.addRow("", self.metallic)

        self.opacity = QtWidgets.QDoubleSpinBox()
        self.opacity.setRange(0.05, 1.0)
        self.opacity.setSingleStep(0.05)
        self.opacity.setDecimals(2)
        self.opacity.setValue(item.opacity)
        self.opacity.valueChanged.connect(self._repaint_preview)
        form.addRow("Opacity", self.opacity)

        row = QtWidgets.QHBoxLayout()
        self.texture = QtWidgets.QLineEdit(item.texture)
        self.texture.setPlaceholderText("none")
        row.addWidget(self.texture, 1)
        pick = QtWidgets.QPushButton("...")
        pick.setFixedWidth(34)
        pick.clicked.connect(self._pick_texture)
        row.addWidget(pick)
        holder = QtWidgets.QWidget()
        holder.setLayout(row)
        form.addRow("Texture", holder)

        self.texture_scale = QtWidgets.QDoubleSpinBox()
        self.texture_scale.setRange(0.1, 10000.0)
        self.texture_scale.setSuffix(" mm")
        self.texture_scale.setValue(item.texture_scale)
        self.texture_scale.setToolTip("How wide one tile of the image is")
        form.addRow("Tile size", self.texture_scale)

    def _build_material(self, form, library) -> None:
        item = self.item
        self.density = QtWidgets.QDoubleSpinBox()
        self.density.setRange(0.0, 30.0)
        self.density.setDecimals(3)
        self.density.setSingleStep(0.05)
        self.density.setSuffix(" g/cm³")
        self.density.setValue(item.density)
        self.density.setToolTip("This is what turns a volume into a mass")
        form.addRow("Density", self.density)

        self.appearance = QtWidgets.QComboBox()
        for appearance in sorted(library.appearances.values(),
                                 key=lambda a: (a.category, a.name)):
            self.appearance.addItem(QtGui.QIcon(swatch(appearance, 15, 3)),
                                    appearance.name, appearance.name)
        index = self.appearance.findData(item.appearance)
        self.appearance.setCurrentIndex(max(0, index))
        self.appearance.currentIndexChanged.connect(self._repaint_preview)
        form.addRow("Looks like", self.appearance)

        form.addRow(QtWidgets.QLabel(""))
        head = QtWidgets.QLabel("Mechanical")
        head.setProperty("hint", True)
        form.addRow(head)

        def number(label, value, suffix, top=100000.0, decimals=2):
            box = QtWidgets.QDoubleSpinBox()
            box.setRange(0.0, top)
            box.setDecimals(decimals)
            box.setSuffix(suffix)
            box.setValue(value)
            form.addRow(label, box)
            return box

        self.youngs = number("Young's modulus", item.youngs_modulus, " GPa")
        self.poisson = number("Poisson's ratio", item.poisson_ratio, "", 0.5,
                              3)
        self.yield_strength = number("Yield strength", item.yield_strength,
                                     " MPa")
        self.tensile = number("Tensile strength", item.tensile_strength,
                              " MPa")
        self.expansion = number("Thermal expansion", item.thermal_expansion,
                                " µm/m·K")

        self.notes = QtWidgets.QLineEdit(item.notes)
        form.addRow("Notes", self.notes)

    # ------------------------------------------------------------- helpers

    def _set_colour_button(self, colour: str) -> None:
        readable = "#101010" if QtGui.QColor(colour).lightness() > 140 \
            else "#f0f0f0"
        self.colour.setText(colour)
        self.colour.setStyleSheet(
            "background: %s; color: %s; border: 1px solid %s;"
            % (colour, readable, C.border_light))

    def _pick_colour(self) -> None:
        chosen = QtWidgets.QColorDialog.getColor(
            QtGui.QColor(self.colour.text()), self, "Colour")
        if chosen.isValid():
            self._set_colour_button(chosen.name())
            self._repaint_preview()

    def _pick_texture(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Texture", os.path.dirname(self.texture.text() or ""),
            "Images (*.png *.jpg *.jpeg *.bmp);;All files (*)")
        if path:
            self.texture.setText(path)

    def _repaint_preview(self) -> None:
        preview = self.harvest_appearance() if self.is_appearance else \
            materials.library().appearance(self.appearance.currentData() or "")
        self.preview.setPixmap(swatch(preview, 86, 10))

    def harvest_appearance(self) -> Appearance:
        return Appearance(
            name=self.name.text().strip() or self.item.name,
            category=self.category.currentText().strip() or "Misc",
            colour=self.colour.text().strip() or "#d5d6d7",
            roughness=self.roughness.value(),
            metallic=self.metallic.isChecked(),
            opacity=self.opacity.value(),
            texture=self.texture.text().strip(),
            texture_scale=self.texture_scale.value())

    def result_item(self):
        """The edited thing, built from the fields."""
        if self.is_appearance:
            return self.harvest_appearance()
        return Material(
            name=self.name.text().strip() or self.item.name,
            category=self.category.currentText().strip() or "Misc",
            density=self.density.value(),
            appearance=self.appearance.currentData() or "Default",
            youngs_modulus=self.youngs.value(),
            poisson_ratio=self.poisson.value(),
            yield_strength=self.yield_strength.value(),
            tensile_strength=self.tensile.value(),
            thermal_expansion=self.expansion.value(),
            notes=self.notes.text().strip())


class MaterialBrowser(QtWidgets.QDialog):
    """The library, to search through and to edit."""

    chosen = QtCore.Signal(str, bool)       # name, is_appearance

    def __init__(self, parent, appearance: bool = False,
                 current: str = "") -> None:
        super().__init__(parent)
        self.is_appearance = appearance
        self.setWindowTitle("Appearance Browser" if appearance
                            else "Material Browser")
        self.resize(620, 560)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(9)

        top = QtWidgets.QHBoxLayout()
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Search")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh)
        top.addWidget(self.search, 1)
        self.category = QtWidgets.QComboBox()
        self.category.setFixedWidth(150)
        self.category.currentIndexChanged.connect(self.refresh)
        top.addWidget(self.category)
        root.addLayout(top)

        self.list = QtWidgets.QListWidget()
        self.list.setIconSize(QtCore.QSize(SWATCH, SWATCH))
        self.list.setAlternatingRowColors(True)
        self.list.itemDoubleClicked.connect(lambda _i: self.use())
        self.list.currentItemChanged.connect(self._sync_buttons)
        root.addWidget(self.list, 1)

        buttons = QtWidgets.QHBoxLayout()
        self.new_button = QtWidgets.QPushButton("New...")
        self.new_button.clicked.connect(self.create)
        buttons.addWidget(self.new_button)
        self.duplicate_button = QtWidgets.QPushButton("Duplicate")
        self.duplicate_button.clicked.connect(self.duplicate)
        buttons.addWidget(self.duplicate_button)
        self.edit_button = QtWidgets.QPushButton("Edit...")
        self.edit_button.clicked.connect(self.edit)
        buttons.addWidget(self.edit_button)
        self.delete_button = QtWidgets.QPushButton("Delete")
        self.delete_button.clicked.connect(self.remove)
        buttons.addWidget(self.delete_button)
        buttons.addStretch(1)
        root.addLayout(buttons)

        close = QtWidgets.QDialogButtonBox()
        self.use_button = close.addButton("Use",
                                          QtWidgets.QDialogButtonBox.AcceptRole)
        self.use_button.setProperty("primary", True)
        close.addButton("Close", QtWidgets.QDialogButtonBox.RejectRole)
        close.accepted.connect(self.use)
        close.rejected.connect(self.reject)
        root.addWidget(close)

        self._fill_categories()
        self.refresh()
        if current:
            self.select(current)

    # ---------------------------------------------------------------- fill

    def _fill_categories(self) -> None:
        self.category.blockSignals(True)
        self.category.clear()
        self.category.addItem("All categories", "")
        for name in materials.library().categories(self.is_appearance):
            self.category.addItem(name, name)
        self.category.blockSignals(False)

    def refresh(self) -> None:
        library = materials.library()
        wanted = self.category.currentData() or ""
        chosen = self.current_name()
        self.list.clear()
        for item in library.search(self.search.text(), self.is_appearance):
            if wanted and item.category != wanted:
                continue
            look = item if self.is_appearance else \
                library.appearance(item.appearance)
            if self.is_appearance:
                detail = "%s %s" % (
                    item.category,
                    "metallic" if item.metallic else "%.0f%% rough"
                    % (item.roughness * 100))
            else:
                detail = "%s %.2f g/cm³ looks like %s" % (
                    item.category, item.density, item.appearance)
            entry = QtWidgets.QListWidgetItem(
                QtGui.QIcon(swatch(look)), "%s\n%s" % (item.name, detail))
            entry.setData(QtCore.Qt.UserRole, item.name)
            if library.is_builtin(item.name, self.is_appearance):
                entry.setToolTip("Ships with DATUM. Duplicate it to edit.")
            self.list.addItem(entry)
        if chosen:
            self.select(chosen)
        self._sync_buttons()

    def select(self, name: str) -> None:
        for i in range(self.list.count()):
            if self.list.item(i).data(QtCore.Qt.UserRole) == name:
                self.list.setCurrentRow(i)
                return

    def current_name(self) -> str:
        item = self.list.currentItem()
        return item.data(QtCore.Qt.UserRole) if item else ""

    def _sync_buttons(self, *_a) -> None:
        name = self.current_name()
        library = materials.library()
        built_in = bool(name) and library.is_builtin(name, self.is_appearance)
        self.duplicate_button.setEnabled(bool(name))
        self.edit_button.setEnabled(bool(name))
        self.edit_button.setText("View..." if built_in else "Edit...")
        self.delete_button.setEnabled(bool(name) and not built_in)
        self.use_button.setEnabled(bool(name))

    # -------------------------------------------------------------- actions

    def _save(self) -> None:
        library = materials.library()
        if library.path:
            library.save_user()

    def create(self) -> None:
        library = materials.library()
        blank = Appearance(library.unique_name("New Appearance", True)) \
            if self.is_appearance else Material(
                library.unique_name("New Material"))
        dialog = MaterialEditor(self, blank, self.is_appearance)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        self._store(dialog.result_item())

    def duplicate(self) -> None:
        name = self.current_name()
        if not name:
            return
        made = materials.library().duplicate(name, self.is_appearance)
        if made is None:
            return
        self._save()
        self.refresh()
        self.select(made.name)
        self.edit()

    def edit(self) -> None:
        name = self.current_name()
        if not name:
            return
        library = materials.library()
        pool = library.appearances if self.is_appearance else library.materials
        item = pool.get(name)
        if item is None:
            return
        built_in = library.is_builtin(name, self.is_appearance)
        dialog = MaterialEditor(self, item, self.is_appearance, built_in)
        if dialog.exec() != QtWidgets.QDialog.Accepted or built_in:
            return
        edited = dialog.result_item()
        if edited.name != name:
            library.remove(name, self.is_appearance)
        self._store(edited)

    def _store(self, item) -> None:
        library = materials.library()
        if self.is_appearance:
            library.put_appearance(item)
        else:
            library.put_material(item)
        self._save()
        self._fill_categories()
        self.refresh()
        self.select(item.name)

    def remove(self) -> None:
        name = self.current_name()
        if not name:
            return
        if QtWidgets.QMessageBox.question(
                self, "Delete",
                "Delete %r? Parts already using it keep the name and the "
                "density they were saved with." % name) \
                != QtWidgets.QMessageBox.Yes:
            return
        materials.library().remove(name, self.is_appearance)
        self._save()
        self.refresh()

    def use(self) -> None:
        name = self.current_name()
        if name:
            self.chosen.emit(name, self.is_appearance)
            self.accept()
