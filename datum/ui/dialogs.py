"""Feature dialogs.

They are non-modal on purpose: the viewport must stay live so edges and faces
can be picked while the dialog is open, exactly as Inventor works.  Each
dialog edits a feature that is *already in the document*, rebuilding on every
keystroke so the preview is the real model, not an approximation.  Cancelling
restores the snapshot taken when the dialog opened.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from ..core import fileformat, kernel
from ..core.document import Document
from ..core.features import (
    CUT, INTERSECT, JOIN, NEW_BODY, OPERATION_HINTS, OPERATION_LABELS,
    OPERATIONS, ChamferFeature, ExtrudeFeature, Feature, FilletFeature,
    HoleFeature, ImportFeature, LoftFeature, MirrorFeature, MoveFeature,
    PatternFeature, PrimitiveFeature, RevolveFeature, ShellFeature,
    SketchFeature, SweepFeature, WorkPlaneFeature, CodeFeature,
)
from ..core.naming import RefSet
from . import icons
from .theme import C
from .widgets import ExpressionEdit, FormRows, SegmentedControl, SelectionField

OP_ICONS = {JOIN: "extrude", CUT: "hole", INTERSECT: "shell",
            NEW_BODY: "box"}

# the order is Inventor's, and it is the order of how often you want each:
# Join nearly always, New Body only when you mean it
OP_OPTIONS = [(op, OP_ICONS[op],
               "%s - %s" % (OPERATION_LABELS[op], OPERATION_HINTS[op]))
              for op in OPERATIONS]

# feature types that put a solid into the part
SOLID_MAKERS = (ExtrudeFeature, RevolveFeature, SweepFeature, LoftFeature,
                PrimitiveFeature, ImportFeature, CodeFeature)


class FeatureDialog(QtWidgets.QDialog):
    """Base class: snapshot, live rebuild, OK/Cancel."""

    committed = QtCore.Signal(int)
    cancelled = QtCore.Signal()

    def __init__(self, host, feature: Feature, title: str, icon_name: str,
                 is_new: bool, snapshot: Optional[str] = None) -> None:
        super().__init__(host)
        self.host = host
        self.doc: Document = host.document
        self.feature = feature
        self.is_new = is_new
        # Taken before the feature was added for a new feature, so Cancel
        # removes it again rather than leaving an empty one behind.
        self._snapshot = snapshot or self.doc.snapshot()
        self._updating = False
        self._closed = False
        # guards against re-entry: handling a pick clears the viewport
        # selection, which fires selection_changed straight back at us
        self._in_selection = False

        self.setWindowTitle(title)
        self.setWindowIcon(icons.icon(icon_name, 24))
        self.setWindowFlags(QtCore.Qt.Tool | QtCore.Qt.WindowTitleHint
                            | QtCore.Qt.WindowCloseButtonHint)
        self.setModal(False)
        self.setMinimumWidth(340)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(9)

        header = QtWidgets.QHBoxLayout()
        badge = QtWidgets.QLabel()
        badge.setPixmap(icons.pixmap(icon_name, 22, self.devicePixelRatioF()))
        header.addWidget(badge)
        caption = QtWidgets.QLabel(title)
        caption.setStyleSheet("font-size: 13px; font-weight: 600;")
        header.addWidget(caption)
        header.addStretch(1)
        root.addLayout(header)

        self.form = FormRows()
        root.addWidget(self.form)

        self.status = QtWidgets.QLabel("")
        self.status.setProperty("hint", True)
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(16)
        root.addWidget(self.status)

        root.addStretch(1)

        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        self.ok_button = QtWidgets.QPushButton("OK")
        self.ok_button.setProperty("primary", True)
        self.ok_button.setDefault(True)
        self.cancel_button = QtWidgets.QPushButton("Cancel")
        buttons.addWidget(self.ok_button)
        buttons.addWidget(self.cancel_button)
        root.addLayout(buttons)

        self.ok_button.clicked.connect(self.commit)
        self.cancel_button.clicked.connect(self.cancel)

        self.build()
        self._updating = True
        self.load()
        self._updating = False
        QtCore.QTimer.singleShot(0, self.preview)

    # -- subclass hooks -----------------------------------------------------

    def build(self) -> None:
        """Add rows to ``self.form``."""

    def load(self) -> None:
        """Copy feature state into the widgets."""

    def store(self) -> None:
        """Copy widget state into the feature."""

    def wants_selection(self) -> Optional[str]:
        """Viewport selection mode to use while this dialog is open."""
        return None

    def on_selection(self) -> None:
        """Called when the viewport selection changes."""

    # -- plumbing -----------------------------------------------------------

    def bind(self, *widgets) -> None:
        for w in widgets:
            if isinstance(w, ExpressionEdit):
                w.changed.connect(self.preview)
            elif isinstance(w, SegmentedControl):
                w.changed.connect(lambda _=None: self.preview())
            elif isinstance(w, QtWidgets.QComboBox):
                w.currentIndexChanged.connect(lambda _=0: self.preview())
            elif isinstance(w, QtWidgets.QCheckBox):
                w.toggled.connect(lambda _=False: self.preview())
            elif isinstance(w, QtWidgets.QLineEdit):
                w.textChanged.connect(lambda _="": self.preview())

    def preview(self) -> None:
        if self._updating or self._closed:
            return
        self.store()
        report = self.host.rebuild(keep_camera=True)
        error = self.feature.error
        if error:
            self.status.setText(error)
            self.status.setStyleSheet("color: %s;" % C.error)
            self.ok_button.setEnabled(False)
        elif report.errors:
            self.status.setText(report.message)
            self.status.setStyleSheet("color: %s;" % C.warn)
            self.ok_button.setEnabled(True)
        else:
            self.status.setText(self.feature.summary())
            self.status.setStyleSheet("color: %s;" % C.ok)
            self.ok_button.setEnabled(True)

    def commit(self) -> None:
        self.store()
        self.host.rebuild(keep_camera=True)
        if self.feature.error:
            QtWidgets.QMessageBox.warning(self, "Feature failed",
                                          self.feature.error)
            return
        self._closed = True
        self.committed.emit(self.feature.id)
        self.close()

    def cancel(self) -> None:
        self._closed = True
        self.doc.load_dict(json.loads(self._snapshot))
        self.host.rebuild(keep_camera=True)
        self.cancelled.emit()
        self.close()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if not self._closed:
            self.cancel()
            return
        self.host.dialog_finished(self)
        super().closeEvent(event)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() == QtCore.Qt.Key_Escape:
            self.cancel()
            return
        if (event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter)
                and self.ok_button.isEnabled()):
            self.commit()
            return
        super().keyPressEvent(event)

    # -- helpers ------------------------------------------------------------

    def expression(self, value: str, unit: str = "mm") -> ExpressionEdit:
        w = ExpressionEdit(value, self.doc.params, unit)
        self.bind(w)
        return w

    def profile_field(self) -> SelectionField:
        """A blank profile field you activate, then click regions in the view.

        Nothing is proposed and nothing is preselected: the field starts
        empty, and clicking it puts the viewport into profile-picking mode
        so any closed region in the model can be chosen, whichever sketch it
        happens to belong to.
        """
        self.profiles = SelectionField("Select profiles")
        self.profiles.pick_toggled.connect(self._toggle_profile_pick)
        self.profiles.cleared.connect(self._clear_profiles)
        self.profiles.set_count(len(self.feature.profiles), "profile")
        # armed as soon as the dialog opens: picking profiles is the first
        # thing you do, so it should not need a click to switch on. It stays
        # a toggle because later extent options need the viewport for their
        # own picking.
        QtCore.QTimer.singleShot(0, lambda: self.profiles.set_picking(True))
        return self.profiles

    def _toggle_profile_pick(self, on: bool) -> None:
        self.host.set_profile_pick(self if on else None)

    def _clear_profiles(self) -> None:
        from ..core.features import ProfileSelection

        self.feature.profiles = ProfileSelection()
        self.profiles.set_count(0, "profile")
        self.host.refresh_profile_overlay()
        self.preview()

    def on_profile_clicked(self, sketch_id: int, centre) -> None:
        """One region was clicked in the viewport - toggle it."""
        self.feature.profiles.toggle(sketch_id, centre)
        self.profiles.set_count(len(self.feature.profiles), "profile")
        self.host.refresh_profile_overlay()
        self.preview()

    def showEvent(self, event):
        super().showEvent(event)
        # keep the viewport ready for picks rather than stealing focus
        if getattr(self, "profiles", None) is not None:
            self.host.viewport.setFocus()

    def wants_profiles(self) -> bool:
        return getattr(self, "profiles", None) is not None \
            and self.profiles.picking

    # -- the Output row ----------------------------------------------------

    def makes_first_solid(self) -> bool:
        """True when this feature would put the part's first solid in it."""
        index = self.doc.index_of(self.feature.id)
        for i, other in enumerate(self.doc.features):
            if i >= index or other.suppressed:
                continue
            if isinstance(other, SOLID_MAKERS):
                return False
        return True

    def boolean_row(self) -> None:
        """Inventor's Output row: how this solid meets the ones already there.

        An empty part is not asked, because there is no question to answer -
        nothing to join to, cut from or intersect with.  It gets the body
        name instead, which is the only decision left.
        """
        if self.makes_first_solid():
            self.operation = None
            self.body_name = QtWidgets.QLineEdit(
                self.feature.body_name or "Solid1")
            self.body_name.setToolTip(
                "What to call this solid. A part can hold several.")
            self.bind(self.body_name)
            self.form.add("Body Name", self.body_name)
            return

        self.body_name = None
        self.operation = SegmentedControl(OP_OPTIONS)
        self.bind(self.operation)
        self.form.add("Boolean", self.operation)

    def load_boolean(self) -> None:
        if getattr(self, "operation", None) is not None:
            self.operation.set_value(self.feature.operation)
        elif getattr(self, "body_name", None) is not None:
            self.body_name.setText(self.feature.body_name or "Solid1")

    def store_boolean(self) -> None:
        if getattr(self, "operation", None) is not None:
            self.feature.operation = self.operation.value()
        elif getattr(self, "body_name", None) is not None:
            self.feature.body_name = self.body_name.text().strip()

    def sketch_combo(self, current: int) -> QtWidgets.QComboBox:
        combo = QtWidgets.QComboBox()
        index = self.doc.index_of(self.feature.id)
        for f in self.doc.sketch_features():
            if self.doc.index_of(f.id) < index:
                combo.addItem(icons.icon("sketch", 15), f.name, f.id)
        pos = combo.findData(current)
        if pos >= 0:
            combo.setCurrentIndex(pos)
        self.bind(combo)
        return combo

    def plane_combo(self, current: str) -> QtWidgets.QComboBox:
        combo = QtWidgets.QComboBox()
        for name in ("XY", "XZ", "YZ"):
            combo.addItem(icons.icon("plane", 15), "%s Plane" % name, name)
        pos = combo.findData(current)
        if pos >= 0:
            combo.setCurrentIndex(pos)
        self.bind(combo)
        return combo

    def feature_picker(self, kinds, selected: List[int]) -> QtWidgets.QListWidget:
        """A checklist of earlier features, for pattern / mirror parents."""
        widget = QtWidgets.QListWidget()
        widget.setMaximumHeight(110)
        index = self.doc.index_of(self.feature.id)
        for f in self.doc.features:
            if self.doc.index_of(f.id) >= index or isinstance(f, SketchFeature):
                continue
            if kinds and not isinstance(f, kinds):
                continue
            item = QtWidgets.QListWidgetItem(icons.icon(f.icon, 15), f.name)
            item.setData(QtCore.Qt.UserRole, f.id)
            item.setCheckState(QtCore.Qt.Checked if f.id in selected
                               else QtCore.Qt.Unchecked)
            widget.addItem(item)
        widget.itemChanged.connect(lambda _i: self.preview())
        return widget

    @staticmethod
    def picked_features(widget: QtWidgets.QListWidget) -> List[int]:
        out = []
        for i in range(widget.count()):
            item = widget.item(i)
            if item.checkState() == QtCore.Qt.Checked:
                out.append(int(item.data(QtCore.Qt.UserRole)))
        return out


# ==========================================================================


class ExtrudeDialog(FeatureDialog):
    def __init__(self, host, feature, is_new, snapshot=None):
        super().__init__(host, feature, "Extrude", "extrude", is_new, snapshot)

    def build(self):
        self.form.add("Profiles", self.profile_field())

        self.boolean_row()

        self.form.add_separator()

        self.extent = QtWidgets.QComboBox()
        self.extent.addItem("Distance", "distance")
        self.extent.addItem("Symmetric", "symmetric")
        self.extent.addItem("Through All", "through_all")
        self.bind(self.extent)
        self.form.add("Extents", self.extent)

        self.distance = self.expression(self.feature.distance)
        self.form.add("Distance", self.distance)

        self.taper = self.expression(self.feature.taper, "deg")
        self.form.add("Taper", self.taper)

        self.flip = QtWidgets.QCheckBox("Flip direction")
        self.bind(self.flip)
        self.form.add("", self.flip)

        self.extent.currentIndexChanged.connect(self._sync_enabled)

    def _sync_enabled(self):
        mode = self.extent.currentData()
        self.distance.setEnabled(mode != "through_all")
        self.taper.setEnabled(mode == "distance")

    def load(self):
        self.load_boolean()
        pos = self.extent.findData(self.feature.extent)
        self.extent.setCurrentIndex(max(0, pos))
        self.distance.set_text(self.feature.distance)
        self.taper.set_text(self.feature.taper)
        self.flip.setChecked(self.feature.reversed)
        self._sync_enabled()

    def store(self):
        self.store_boolean()
        self.feature.extent = self.extent.currentData()
        self.feature.distance = self.distance.text()
        self.feature.taper = self.taper.text()
        self.feature.reversed = self.flip.isChecked()


class RevolveDialog(FeatureDialog):
    def __init__(self, host, feature, is_new, snapshot=None):
        super().__init__(host, feature, "Revolve", "revolve", is_new, snapshot)

    def build(self):
        self.form.add("Profiles", self.profile_field())

        self.boolean_row()

        self.form.add_separator()

        self.axis = QtWidgets.QComboBox()
        self.axis.addItem("Sketch X axis", "X")
        self.axis.addItem("Sketch Y axis", "Y")
        self.bind(self.axis)
        self.form.add("Axis", self.axis)

        self.angle = self.expression(self.feature.angle, "deg")
        self.form.add("Angle", self.angle)

        self.flip = QtWidgets.QCheckBox("Reverse direction")
        self.bind(self.flip)
        self.form.add("", self.flip)

    def load(self):
        self.load_boolean()
        pos = self.axis.findData(self.feature.axis)
        self.axis.setCurrentIndex(max(0, pos))
        self.angle.set_text(self.feature.angle)
        self.flip.setChecked(self.feature.reversed)

    def store(self):
        self.store_boolean()
        self.feature.axis = self.axis.currentData()
        self.feature.angle = self.angle.text()
        self.feature.reversed = self.flip.isChecked()


class SweepDialog(FeatureDialog):
    def __init__(self, host, feature, is_new, snapshot=None):
        super().__init__(host, feature, "Sweep", "sweep", is_new, snapshot)

    def build(self):
        self.form.add("Profiles", self.profile_field())

        self.path = self.sketch_combo(self.feature.path_id)
        self.form.add("Path", self.path)

        hint = QtWidgets.QLabel("The path sketch supplies an open chain of "
                                "curves for the profile to follow.")
        hint.setProperty("hint", True)
        hint.setWordWrap(True)
        self.form.add("", hint)

        self.boolean_row()

    def load(self):
        pos = self.path.findData(self.feature.path_id)
        if pos >= 0:
            self.path.setCurrentIndex(pos)
        elif self.path.count() > 1:
            self.path.setCurrentIndex(self.path.count() - 1)
        self.load_boolean()

    def store(self):
        self.feature.path_id = int(self.path.currentData() or 0)
        self.store_boolean()


class LoftDialog(FeatureDialog):
    def __init__(self, host, feature, is_new, snapshot=None):
        super().__init__(host, feature, "Loft", "loft", is_new, snapshot)

    def build(self):
        self.sections = QtWidgets.QListWidget()
        self.sections.setMaximumHeight(130)
        index = self.doc.index_of(self.feature.id)
        for f in self.doc.sketch_features():
            if self.doc.index_of(f.id) >= index:
                continue
            item = QtWidgets.QListWidgetItem(icons.icon("sketch", 15), f.name)
            item.setData(QtCore.Qt.UserRole, f.id)
            item.setCheckState(QtCore.Qt.Checked
                               if f.id in self.feature.sections
                               else QtCore.Qt.Unchecked)
            self.sections.addItem(item)
        self.sections.itemChanged.connect(lambda _i: self.preview())
        self.form.add("Sections", self.sections)

        hint = QtWidgets.QLabel("Blended in list order. Put each section on "
                                "its own plane.")
        hint.setProperty("hint", True)
        hint.setWordWrap(True)
        self.form.add("", hint)

        self.ruled = QtWidgets.QCheckBox("Ruled (straight between sections)")
        self.bind(self.ruled)
        self.form.add("", self.ruled)

        self.boolean_row()

    def load(self):
        self.ruled.setChecked(self.feature.ruled)
        self.load_boolean()

    def store(self):
        self.feature.sections = self.picked_features(self.sections)
        self.feature.ruled = self.ruled.isChecked()
        self.feature.closed = True
        self.store_boolean()


class HoleDialog(FeatureDialog):
    def __init__(self, host, feature, is_new, snapshot=None):
        super().__init__(host, feature, "Hole", "hole", is_new, snapshot)

    def build(self):
        self.sketch = self.sketch_combo(self.feature.sketch_id)
        self.form.add("Centres", self.sketch)

        hint = QtWidgets.QLabel("Each circle in the sketch places one hole.")
        hint.setProperty("hint", True)
        self.form.add("", hint)

        self.form.add_separator()

        self.kind = QtWidgets.QComboBox()
        self.kind.addItem("Simple", "simple")
        self.kind.addItem("Counterbore", "counterbore")
        self.kind.addItem("Countersink", "countersink")
        self.bind(self.kind)
        self.form.add("Type", self.kind)

        self.diameter = self.expression(self.feature.diameter)
        self.form.add("Diameter", self.diameter)

        self.through = QtWidgets.QCheckBox("Through all")
        self.bind(self.through)
        self.form.add("", self.through)

        self.depth = self.expression(self.feature.depth)
        self.form.add("Depth", self.depth)

        self.form.add_separator()

        self.cb_diameter = self.expression(self.feature.cb_diameter)
        self.form.add("C'bore dia", self.cb_diameter)
        self.cb_depth = self.expression(self.feature.cb_depth)
        self.form.add("C'bore depth", self.cb_depth)
        self.cs_angle = self.expression(self.feature.cs_angle, "deg")
        self.form.add("C'sink angle", self.cs_angle)

        self.flip = QtWidgets.QCheckBox("Flip drill direction")
        self.bind(self.flip)
        self.form.add("", self.flip)

        self.kind.currentIndexChanged.connect(self._sync_enabled)
        self.through.toggled.connect(self._sync_enabled)

    def _sync_enabled(self):
        kind = self.kind.currentData()
        self.depth.setEnabled(not self.through.isChecked())
        self.cb_diameter.setEnabled(kind != "simple")
        self.cb_depth.setEnabled(kind == "counterbore")
        self.cs_angle.setEnabled(kind == "countersink")

    def load(self):
        pos = self.kind.findData(self.feature.hole_type)
        self.kind.setCurrentIndex(max(0, pos))
        self.diameter.set_text(self.feature.diameter)
        self.depth.set_text(self.feature.depth)
        self.through.setChecked(self.feature.through)
        self.cb_diameter.set_text(self.feature.cb_diameter)
        self.cb_depth.set_text(self.feature.cb_depth)
        self.cs_angle.set_text(self.feature.cs_angle)
        self.flip.setChecked(self.feature.flip)
        self._sync_enabled()

    def store(self):
        self.feature.sketch_id = int(self.sketch.currentData() or 0)
        self.feature.hole_type = self.kind.currentData()
        self.feature.diameter = self.diameter.text()
        self.feature.depth = self.depth.text()
        self.feature.through = self.through.isChecked()
        self.feature.cb_diameter = self.cb_diameter.text()
        self.feature.cb_depth = self.cb_depth.text()
        self.feature.cs_angle = self.cs_angle.text()
        self.feature.flip = self.flip.isChecked()


class EdgeOpDialog(FeatureDialog):
    """Shared base for fillet and chamfer."""

    noun = "edge"
    size_label = "Radius"

    def build(self):
        self.picker = SelectionField("Select edges")
        self.picker.pick_toggled.connect(self._toggle_pick)
        self.picker.cleared.connect(self._clear)
        self.form.add("Edges", self.picker)

        self.all_edges = QtWidgets.QCheckBox("All edges of the body")
        self.bind(self.all_edges)
        self.form.add("", self.all_edges)

        self.size = self.expression(self._size_value())
        self.form.add(self.size_label, self.size)

        self.all_edges.toggled.connect(self._sync_enabled)
        QtCore.QTimer.singleShot(0, lambda: self.picker.set_picking(True))

    def _size_value(self) -> str:
        return "2"

    def _sync_enabled(self):
        on = not self.all_edges.isChecked()
        self.picker.setEnabled(on)
        if not on:
            self.picker.set_picking(False)

    def wants_selection(self):
        return "edge" if self.picker.picking else None

    def _toggle_pick(self, on: bool):
        self.host.set_pick_mode("edge" if on else None)

    def _clear(self):
        self.feature.refs = RefSet()
        self.picker.set_count(0, self.noun)
        self.host.viewport.clear_selection()
        self.preview()

    def on_selection(self):
        """Each pick *adds* to the set, the way Inventor's fillet dialog works.

        Accumulating here rather than reading the viewport's selection is what
        makes multi-edge picking possible at all: the live preview rebuilds the
        body after every pick, which necessarily clears the OCCT selection.
        """
        if not self.picker.picking or self._in_selection:
            return
        picked = self.host.viewport.selected_edges()
        if not picked:
            return
        base = self.host.pick_shape()
        if base is None:
            return

        self._in_selection = True
        try:
            fresh = RefSet()
            fresh.capture_from(base, "edge", picked)
            for ref in fresh:
                existing = self._matching_ref(ref)
                if existing is not None:
                    self.feature.refs.refs.remove(existing)  # click again to drop
                else:
                    self.feature.refs.add(ref)

            self.picker.set_count(len(self.feature.refs), self.noun)
            self.host.viewport.clear_selection()
        finally:
            self._in_selection = False
        self.preview()

    def _matching_ref(self, ref):
        import math
        for existing in self.feature.refs:
            if (existing.kind == ref.kind
                    and math.dist(existing.centre, ref.centre) < 1e-6):
                return existing
        return None

    def load(self):
        self.picker.set_count(len(self.feature.refs), self.noun)
        self.all_edges.setChecked(self.feature.all_edges)
        self._sync_enabled()


class FilletDialog(EdgeOpDialog):
    size_label = "Radius"

    def __init__(self, host, feature, is_new, snapshot=None):
        super().__init__(host, feature, "Fillet", "fillet", is_new, snapshot)

    def _size_value(self):
        return self.feature.radius

    def load(self):
        super().load()
        self.size.set_text(self.feature.radius)

    def store(self):
        self.feature.radius = self.size.text()
        self.feature.all_edges = self.all_edges.isChecked()


class ChamferDialog(EdgeOpDialog):
    size_label = "Distance"

    def __init__(self, host, feature, is_new, snapshot=None):
        super().__init__(host, feature, "Chamfer", "chamfer", is_new, snapshot)

    def _size_value(self):
        return self.feature.distance

    def load(self):
        super().load()
        self.size.set_text(self.feature.distance)

    def store(self):
        self.feature.distance = self.size.text()
        self.feature.all_edges = self.all_edges.isChecked()


class ShellDialog(FeatureDialog):
    def __init__(self, host, feature, is_new, snapshot=None):
        super().__init__(host, feature, "Shell", "shell", is_new, snapshot)

    def build(self):
        self.picker = SelectionField("Select faces")
        self.picker.pick_toggled.connect(
            lambda on: self.host.set_pick_mode("face" if on else None))
        self.picker.cleared.connect(self._clear)
        self.form.add("Open faces", self.picker)

        hint = QtWidgets.QLabel("Faces picked here are removed, leaving the "
                                "body open.")
        hint.setProperty("hint", True)
        hint.setWordWrap(True)
        self.form.add("", hint)

        self.thickness = self.expression(self.feature.thickness)
        self.form.add("Wall", self.thickness)

        QtCore.QTimer.singleShot(0, lambda: self.picker.set_picking(True))

    def wants_selection(self):
        return "face" if self.picker.picking else None

    def _clear(self):
        self.feature.refs = RefSet()
        self.picker.set_count(0, "face")
        self.host.viewport.clear_selection()
        self.preview()

    def on_selection(self):
        # additive, for the same reason as the fillet/chamfer picker
        if not self.picker.picking or self._in_selection:
            return
        picked = self.host.viewport.selected_faces()
        if not picked:
            return
        base = self.host.pick_shape()
        if base is None:
            return

        import math
        self._in_selection = True
        try:
            fresh = RefSet()
            fresh.capture_from(base, "face", picked)
            for ref in fresh:
                match = next((e for e in self.feature.refs
                              if math.dist(e.centre, ref.centre) < 1e-6), None)
                if match is not None:
                    self.feature.refs.refs.remove(match)
                else:
                    self.feature.refs.add(ref)

            self.picker.set_count(len(self.feature.refs), "face")
            self.host.viewport.clear_selection()
        finally:
            self._in_selection = False
        self.preview()

    def load(self):
        self.picker.set_count(len(self.feature.refs), "face")
        self.thickness.set_text(self.feature.thickness)

    def store(self):
        self.feature.thickness = self.thickness.text()


class PatternDialog(FeatureDialog):
    def __init__(self, host, feature, is_new, snapshot=None):
        super().__init__(host, feature, "Pattern", "pattern", is_new, snapshot)

    def build(self):
        self.mode = QtWidgets.QComboBox()
        self.mode.addItem(icons.icon("pattern", 15), "Rectangular", "rectangular")
        self.mode.addItem(icons.icon("pattern_circ", 15), "Circular", "circular")
        self.bind(self.mode)
        self.form.add("Type", self.mode)

        self.parents = self.feature_picker(None, self.feature.parents)
        self.form.add("Features", self.parents)

        self.form.add_separator()

        self.count1 = self.expression(self.feature.count1, "")
        self.form.add("Count", self.count1)

        self.dir1 = QtWidgets.QComboBox()
        for axis in ("X", "Y", "Z"):
            self.dir1.addItem("%s axis" % axis, axis)
        self.bind(self.dir1)
        self.form.add("Direction", self.dir1)

        self.spacing1 = self.expression(self.feature.spacing1)
        self.form.add("Spacing", self.spacing1)

        self.form.add_separator()

        self.count2 = self.expression(self.feature.count2, "")
        self.form.add("Count 2", self.count2)

        self.dir2 = QtWidgets.QComboBox()
        for axis in ("X", "Y", "Z"):
            self.dir2.addItem("%s axis" % axis, axis)
        self.bind(self.dir2)
        self.form.add("Direction 2", self.dir2)

        self.spacing2 = self.expression(self.feature.spacing2)
        self.form.add("Spacing 2", self.spacing2)

        self.form.add_separator()

        self.axis = QtWidgets.QComboBox()
        for axis in ("X", "Y", "Z"):
            self.axis.addItem("%s axis" % axis, axis)
        self.bind(self.axis)
        self.form.add("Rotation axis", self.axis)

        self.full = QtWidgets.QCheckBox("Full circle")
        self.bind(self.full)
        self.form.add("", self.full)

        self.angle = self.expression(self.feature.angle, "deg")
        self.form.add("Total angle", self.angle)

        self.mode.currentIndexChanged.connect(self._sync_enabled)
        self.full.toggled.connect(self._sync_enabled)

    def _sync_enabled(self):
        rect = self.mode.currentData() == "rectangular"
        for w in (self.dir1, self.spacing1, self.count2, self.dir2,
                  self.spacing2):
            w.setEnabled(rect)
        self.axis.setEnabled(not rect)
        self.full.setEnabled(not rect)
        self.angle.setEnabled(not rect and not self.full.isChecked())

    def load(self):
        pos = self.mode.findData(self.feature.mode)
        self.mode.setCurrentIndex(max(0, pos))
        self.count1.set_text(self.feature.count1)
        self.spacing1.set_text(self.feature.spacing1)
        self.count2.set_text(self.feature.count2)
        self.spacing2.set_text(self.feature.spacing2)
        self.angle.set_text(self.feature.angle)
        self.full.setChecked(self.feature.full_circle)
        for combo, value in ((self.dir1, self.feature.dir1),
                             (self.dir2, self.feature.dir2),
                             (self.axis, self.feature.axis)):
            p = combo.findData(value)
            combo.setCurrentIndex(max(0, p))
        self._sync_enabled()

    def store(self):
        self.feature.mode = self.mode.currentData()
        self.feature.parents = self.picked_features(self.parents)
        self.feature.count1 = self.count1.text()
        self.feature.spacing1 = self.spacing1.text()
        self.feature.dir1 = self.dir1.currentData()
        self.feature.count2 = self.count2.text()
        self.feature.spacing2 = self.spacing2.text()
        self.feature.dir2 = self.dir2.currentData()
        self.feature.axis = self.axis.currentData()
        self.feature.angle = self.angle.text()
        self.feature.full_circle = self.full.isChecked()


class MirrorDialog(FeatureDialog):
    def __init__(self, host, feature, is_new, snapshot=None):
        super().__init__(host, feature, "Mirror", "mirror", is_new, snapshot)

    def build(self):
        self.scope = QtWidgets.QComboBox()
        self.scope.addItem("Whole body", "body")
        self.scope.addItem("Selected features", "features")
        self.bind(self.scope)
        self.form.add("Mirror", self.scope)

        self.parents = self.feature_picker(None, self.feature.parents)
        self.form.add("Features", self.parents)

        self.plane = self.plane_combo(self.feature.plane)
        self.form.add("About", self.plane)

        self.scope.currentIndexChanged.connect(self._sync_enabled)

    def _sync_enabled(self):
        self.parents.setEnabled(self.scope.currentData() == "features")

    def load(self):
        pos = self.scope.findData(self.feature.scope)
        self.scope.setCurrentIndex(max(0, pos))
        self._sync_enabled()

    def store(self):
        self.feature.scope = self.scope.currentData()
        self.feature.parents = self.picked_features(self.parents)
        self.feature.plane = self.plane.currentData()


class PrimitiveDialog(FeatureDialog):
    LABELS = {
        "box": ("Length X", "Length Y", "Length Z"),
        "cylinder": ("Radius", "Height", None),
        "sphere": ("Radius", None, None),
        "cone": ("Bottom radius", "Top radius", "Height"),
        "torus": ("Ring radius", "Tube radius", None),
    }

    def __init__(self, host, feature, is_new, snapshot=None):
        super().__init__(host, feature, "Primitive", "box", is_new, snapshot)

    def build(self):
        self.kind = QtWidgets.QComboBox()
        for key, label, icon in (("box", "Box", "box"),
                                 ("cylinder", "Cylinder", "cylinder"),
                                 ("sphere", "Sphere", "sphere"),
                                 ("cone", "Cone", "cylinder"),
                                 ("torus", "Torus", "sphere")):
            self.kind.addItem(icons.icon(icon, 15), label, key)
        self.bind(self.kind)
        self.form.add("Shape", self.kind)

        self.boolean_row()

        self.form.add_separator()

        self.a = self.expression(self.feature.a)
        self.form.add("A", self.a)
        self.b = self.expression(self.feature.b)
        self.form.add("B", self.b)
        self.c = self.expression(self.feature.c)
        self.form.add("C", self.c)

        self.form.add_separator()
        self.ox = self.expression(self.feature.origin[0])
        self.form.add("Origin X", self.ox)
        self.oy = self.expression(self.feature.origin[1])
        self.form.add("Origin Y", self.oy)
        self.oz = self.expression(self.feature.origin[2])
        self.form.add("Origin Z", self.oz)

        self.centred = QtWidgets.QCheckBox("Centre on the origin")
        self.bind(self.centred)
        self.form.add("", self.centred)

        self.kind.currentIndexChanged.connect(self._sync_labels)

    def _sync_labels(self):
        labels = self.LABELS.get(self.kind.currentData(),
                                 ("A", "B", "C"))
        for widget, text in zip((self.a, self.b, self.c), labels):
            widget.setEnabled(text is not None)
        # relabel the rows in place
        row_of = {id(self.a): None, id(self.b): None, id(self.c): None}
        grid = self.form.grid
        for row in range(grid.rowCount()):
            item = grid.itemAtPosition(row, 1)
            if item is None:
                continue
            w = item.widget()
            if id(w) in row_of:
                label_item = grid.itemAtPosition(row, 0)
                if label_item and label_item.widget():
                    idx = [self.a, self.b, self.c].index(w)
                    label_item.widget().setText(labels[idx] or "-")

    def load(self):
        pos = self.kind.findData(self.feature.kind)
        self.kind.setCurrentIndex(max(0, pos))
        self.load_boolean()
        self.a.set_text(self.feature.a)
        self.b.set_text(self.feature.b)
        self.c.set_text(self.feature.c)
        self.ox.set_text(self.feature.origin[0])
        self.oy.set_text(self.feature.origin[1])
        self.oz.set_text(self.feature.origin[2])
        self.centred.setChecked(self.feature.centred)
        self._sync_labels()

    def store(self):
        self.feature.kind = self.kind.currentData()
        self.store_boolean()
        self.feature.a = self.a.text()
        self.feature.b = self.b.text()
        self.feature.c = self.c.text()
        self.feature.origin = (self.ox.text(), self.oy.text(), self.oz.text())
        self.feature.centred = self.centred.isChecked()
        self.feature.name = self.feature.name


class WorkPlaneDialog(FeatureDialog):
    def __init__(self, host, feature, is_new, snapshot=None):
        super().__init__(host, feature, "Work Plane", "plane", is_new, snapshot)

    def build(self):
        row = QtWidgets.QWidget()
        line = QtWidgets.QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(6)
        self.base_label = QtWidgets.QLabel()
        line.addWidget(self.base_label, 1)
        repick = QtWidgets.QPushButton("Pick again")
        repick.setIcon(icons.icon("select", 15))
        repick.clicked.connect(self._repick)
        line.addWidget(repick)
        self.form.add("Base", row)

        # a datum plane can still be chosen from the list, for face-free models
        self.base = self.plane_combo(self.feature.base)
        self.form.add("Datum", self.base)

        self.form.add_separator()

        self.offset = self.expression(self.feature.offset)
        self.form.add("Offset", self.offset)
        self.angle = self.expression(self.feature.angle, "deg")
        self.form.add("Tilt", self.angle)

        self.flip = QtWidgets.QCheckBox("Offset the other way")
        self.bind(self.flip)
        self.form.add("", self.flip)

    def _repick(self):
        """Drop this plane and restart the pick-and-drag gesture."""
        self.cancel()
        QtCore.QTimer.singleShot(0, self.host.start_work_plane)

    def _sync_base(self):
        on_face = self.feature.face_ref is not None
        self.base_label.setText("Model face" if on_face
                                else "%s Plane" % self.feature.base)
        self.base_label.setStyleSheet("color: %s;" % (C.ok if on_face
                                                      else C.text))
        self.base.setEnabled(not on_face)

    def load(self):
        self.offset.set_text(self.feature.offset)
        self.angle.set_text(self.feature.angle)
        self.flip.setChecked(self.feature.flip)
        self._sync_base()

    def store(self):
        if self.feature.face_ref is None:
            self.feature.base = self.base.currentData()
        self.feature.offset = self.offset.text()
        self.feature.angle = self.angle.text()
        self.feature.flip = self.flip.isChecked()


class MoveDialog(FeatureDialog):
    def __init__(self, host, feature, is_new, snapshot=None):
        super().__init__(host, feature, "Move Body", "move", is_new, snapshot)

    def build(self):
        self.dx = self.expression(self.feature.dx)
        self.form.add("Move X", self.dx)
        self.dy = self.expression(self.feature.dy)
        self.form.add("Move Y", self.dy)
        self.dz = self.expression(self.feature.dz)
        self.form.add("Move Z", self.dz)
        self.form.add_separator()
        self.rx = self.expression(self.feature.rx, "deg")
        self.form.add("Rotate X", self.rx)
        self.ry = self.expression(self.feature.ry, "deg")
        self.form.add("Rotate Y", self.ry)
        self.rz = self.expression(self.feature.rz, "deg")
        self.form.add("Rotate Z", self.rz)

    def load(self):
        for widget, name in ((self.dx, "dx"), (self.dy, "dy"), (self.dz, "dz"),
                             (self.rx, "rx"), (self.ry, "ry"), (self.rz, "rz")):
            widget.set_text(getattr(self.feature, name))

    def store(self):
        for widget, name in ((self.dx, "dx"), (self.dy, "dy"), (self.dz, "dz"),
                             (self.rx, "rx"), (self.ry, "ry"), (self.rz, "rz")):
            setattr(self.feature, name, widget.text())


DIALOGS = {
    ExtrudeFeature: ExtrudeDialog,
    RevolveFeature: RevolveDialog,
    SweepFeature: SweepDialog,
    LoftFeature: LoftDialog,
    HoleFeature: HoleDialog,
    FilletFeature: FilletDialog,
    ChamferFeature: ChamferDialog,
    ShellFeature: ShellDialog,
    PatternFeature: PatternDialog,
    MirrorFeature: MirrorDialog,
    PrimitiveFeature: PrimitiveDialog,
    WorkPlaneFeature: WorkPlaneDialog,
    MoveFeature: MoveDialog,
}


def dialog_for(host, feature: Feature, is_new: bool,
               snapshot: Optional[str] = None) -> Optional[FeatureDialog]:
    cls = DIALOGS.get(type(feature))
    return cls(host, feature, is_new, snapshot) if cls else None



class NewDocumentDialog(QtWidgets.QDialog):
    """What kind of document New should make.

    New used to make a part whatever you were doing, which is wrong the
    moment there is more than one kind: somebody in an assembly pressing
    New usually wants another assembly, or a drawing of the one they are
    already in.
    """

    KINDS = (
        (fileformat.PART, "box", "Part",
         "A solid, built on a feature tree"),
        (fileformat.ASSEMBLY, "import", "Assembly",
         "Parts and sub-assemblies, placed and constrained"),
        (fileformat.DRAWING, "dimension", "Drawing",
         "Views, dimensions and a title block"),
        (fileformat.CAM, "section", "CAM Sheet",
         "Flat parts nested on stock, and the toolpaths for them"),
    )

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New")
        self.setWindowIcon(icons.icon("new", 24))
        self.setMinimumWidth(420)
        self.chosen: Optional[str] = None

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        self.list = QtWidgets.QListWidget()
        self.list.setIconSize(QtCore.QSize(28, 28))
        self.list.setSpacing(1)
        for kind, icon_name, title, hint in self.KINDS:
            item = QtWidgets.QListWidgetItem(
                icons.icon(icon_name, 28), "%s\n%s" % (title, hint))
            item.setData(QtCore.Qt.UserRole, kind)
            self.list.addItem(item)
        self.list.setCurrentRow(0)
        self.list.itemActivated.connect(lambda _i: self.accept())
        layout.addWidget(self.list, 1)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self) -> None:
        item = self.list.currentItem()
        if item is not None:
            self.chosen = str(item.data(QtCore.Qt.UserRole))
        super().accept()

    @classmethod
    def ask(cls, parent=None) -> Optional[str]:
        dialog = cls(parent)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return None
        return dialog.chosen
