"""The assembly workspace: placing components and constraining them.

This is the counterpart to the part modeller's feature dialogs.  The same
rules apply: dialogs are non-modal so the viewport stays live and geometry
can be picked while one is open, every change rebuilds the real assembly
rather than a preview of it, and Cancel restores the snapshot taken when the
dialog opened.
"""

from __future__ import annotations

import json
import math
import os
from typing import Any, Dict, List, Optional, Sequence

from PySide6 import QtCore, QtGui, QtWidgets

from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
from OCP.TopLoc import TopLoc_Location

from ..core import constraints3d, fileformat, kernel
from ..core.assembly import (
    AssemblyConstraint, AssemblyDocument, AssemblyReport, Attachment,
    Occurrence,
)
from ..core.constraints3d import (
    ANGLE, FLIP_LABELS, FLUSH, KIND_HINTS, KIND_LABELS, MATE, OFFSET_LABELS,
    SOLUTION_HINTS, SOLUTIONS, TYPES, type_of,
)
from . import icons
from .assembly_browser import AssemblyBrowser
from .theme import C
from .widgets import ExpressionEdit, FormRows, SegmentedControl, SelectionField

KIND_ICONS = {
    "mate": "c_coincident",
    "flush": "c_parallel",
    "angle": "dimension",
    "tangent": "c_tangent",
    "insert": "c_concentric",
}

# how far apart newly placed components are set down, as a fraction of what
# is already there - dropping everything on the origin just buries it
PLACE_GAP = 20.0

# first pick, then second.  Two colours rather than one because when the
# arrows end up nose to nose there is otherwise no telling them apart.
ARROW_COLOURS = (C.ok, C.accent)


class ConstraintDialog(QtWidgets.QDialog):
    """Inventor's Place Constraint: a kind, two picks, an offset."""

    committed = QtCore.Signal(int)
    cancelled = QtCore.Signal()

    def __init__(self, controller: "AssemblyController",
                 constraint: Optional[AssemblyConstraint] = None,
                 kind: str = MATE) -> None:
        super().__init__(controller.host)
        self.controller = controller
        self.host = controller.host
        self.doc: AssemblyDocument = controller.document
        self.is_new = constraint is None
        self._snapshot = self.doc.snapshot()
        self._closed = False
        # handling a pick clears the viewport selection, which fires the
        # selection signal straight back at us
        self._in_selection = False
        self._live = False              # is the constraint in the document?

        self.constraint = constraint or AssemblyConstraint(kind=kind)
        if constraint is not None:
            self._live = True

        self.setWindowTitle("Place Constraint")
        self.setWindowIcon(icons.icon("c_coincident", 24))
        self.setWindowFlags(QtCore.Qt.Tool | QtCore.Qt.WindowTitleHint
                            | QtCore.Qt.WindowCloseButtonHint)
        self.setModal(False)
        self.setMinimumWidth(360)

        self._build()
        self._load()
        QtCore.QTimer.singleShot(0, lambda: self.arm(self.first))

    # ------------------------------------------------------------- building

    def _build(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(9)

        header = QtWidgets.QHBoxLayout()
        badge = QtWidgets.QLabel()
        badge.setPixmap(icons.pixmap("c_coincident", 22,
                                     self.devicePixelRatioF()))
        header.addWidget(badge)
        caption = QtWidgets.QLabel("Place Constraint")
        caption.setStyleSheet("font-size: 13px; font-weight: 600;")
        header.addWidget(caption)
        header.addStretch(1)
        root.addLayout(header)

        self.form = FormRows()
        root.addWidget(self.form)

        self.kind = SegmentedControl(
            [(k, KIND_ICONS[k], "%s - %s" % (KIND_LABELS[k], KIND_HINTS[k]))
             for k in TYPES])
        self.kind.changed.connect(self._kind_changed)
        self.form.add("Type", self.kind)

        # Flush is not a type of its own, it is the other answer to a mate,
        # and putting it here rather than in the row above is what makes the
        # arrows in the viewport mean something
        self.solution = SegmentedControl(
            [(s, KIND_ICONS[s], "%s - %s" % (KIND_LABELS[s],
                                             SOLUTION_HINTS[s]))
             for s in SOLUTIONS])
        self.solution.changed.connect(self._solution_changed)
        self.solution_row = self.form.add("Solution", self.solution)

        self.first = SelectionField("First")
        self.first.pick_toggled.connect(
            lambda on: self.arm(self.first if on else None))
        self.first.cleared.connect(lambda: self._clear(0))
        self.form.add("", self.first)

        self.second = SelectionField("Second")
        self.second.pick_toggled.connect(
            lambda on: self.arm(self.second if on else None))
        self.second.cleared.connect(lambda: self._clear(1))
        self.form.add("", self.second)

        self.offset = ExpressionEdit("0", self.doc.params, "mm")
        self.offset.changed.connect(self._changed)
        self.offset_row = self.form.add("Offset", self.offset)

        self.flip = QtWidgets.QCheckBox("Flip solution")
        self.flip.toggled.connect(lambda _on: self._changed())
        self.form.add("", self.flip)

        self.status = QtWidgets.QLabel("")
        self.status.setProperty("hint", True)
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(30)
        root.addWidget(self.status)
        root.addStretch(1)

        buttons = QtWidgets.QHBoxLayout()
        self.apply_button = QtWidgets.QPushButton("Apply")
        self.apply_button.setToolTip(
            "Keep this constraint and start another of the same kind")
        buttons.addWidget(self.apply_button)
        buttons.addStretch(1)
        self.ok_button = QtWidgets.QPushButton("OK")
        self.ok_button.setProperty("primary", True)
        self.ok_button.setDefault(True)
        self.cancel_button = QtWidgets.QPushButton("Cancel")
        buttons.addWidget(self.ok_button)
        buttons.addWidget(self.cancel_button)
        root.addLayout(buttons)

        self.apply_button.clicked.connect(self.apply_and_continue)
        self.ok_button.clicked.connect(self.commit)
        self.cancel_button.clicked.connect(self.cancel)

    def _load(self) -> None:
        self.kind.set_value(type_of(self.constraint.kind))
        self.solution.set_value(
            self.constraint.kind if self.constraint.kind in SOLUTIONS else MATE)
        self.offset.set_text(self.constraint.offset or "0")
        self.flip.setChecked(self.constraint.flip)
        self._sync_kind()
        self._sync_fields()

    # ---------------------------------------------------------- kind & state

    def _kind_changed(self, chosen: str) -> None:
        # changing away from Mate and back keeps whichever solution was last
        # picked, the way any other remembered choice behaves
        self.constraint.kind = (self.solution.value() if chosen == MATE
                                else chosen)
        self._sync_kind()
        self._changed()

    def _solution_changed(self, solution: str) -> None:
        self.constraint.kind = solution
        self._sync_kind()
        self._changed()

    def _sync_kind(self) -> None:
        kind = self.constraint.kind
        self.offset.unit = "deg" if kind == ANGLE else "mm"
        self.offset._validate()
        self.form.set_label(self.offset, OFFSET_LABELS.get(kind, "Offset"))

        self.form.set_visible(self.solution, kind in SOLUTIONS)

        labels = FLIP_LABELS.get(kind)
        self.flip.setVisible(labels is not None)
        if labels:
            self.flip.setText("%s  (unticked: %s)" % (labels[1], labels[0]))
        self._show_arrows()

    def _sync_fields(self) -> None:
        for field, attachment in ((self.first, self.constraint.a),
                                  (self.second, self.constraint.b)):
            if attachment and attachment.valid:
                occurrence = self.doc.occurrence(attachment.occurrence)
                field.count.setText("%s  %s" % (
                    occurrence.label if occurrence else "?",
                    attachment.frame.label))
                field.count.setStyleSheet("color: %s;" % C.ok)
            else:
                field.set_count(0, "pick")
        self._show_arrows()

    @property
    def complete(self) -> bool:
        return bool(self.constraint.a and self.constraint.a.valid
                    and self.constraint.b and self.constraint.b.valid)

    # ----------------------------------------------------------- picking

    def arm(self, field: Optional[SelectionField]) -> None:
        self._armed = field
        for candidate in (self.first, self.second):
            candidate.button.blockSignals(True)
            candidate.set_picking(candidate is field)
            candidate.button.blockSignals(False)
        self.controller.set_picking(field is not None)
        if field is not None:
            self.status.setStyleSheet("color: %s;" % C.text_dim)
            self.status.setText("Click a face or an edge on a component. %s"
                                % self._hint())

    def _hint(self) -> str:
        """What the current solution will do, said in terms of the arrows."""
        kind = self.constraint.kind
        if kind not in SOLUTIONS:
            return KIND_HINTS.get(kind, "")
        if self.constraint.a and self.constraint.a.valid:
            return ("The green arrow is which way the first face looks; "
                    + ("the next face will be turned to meet it."
                       if kind == MATE else
                       "the next face will be turned the same way."))
        return SOLUTION_HINTS[kind]

    def on_selection(self) -> bool:
        """A viewport pick while this dialog is open.  True if we used it."""
        field = getattr(self, "_armed", None)
        if field is None or self._in_selection or self._closed:
            return False
        picked = self.host.viewport.selected_component_shapes()
        if not picked:
            return False

        occurrence_id, shape = picked[0]
        occurrence = self.doc.occurrence(occurrence_id)
        if occurrence is None or occurrence.shape is None:
            return False

        kind = ("edge" if shape.ShapeType() == TopAbs_EDGE
                else "face" if shape.ShapeType() == TopAbs_FACE else None)
        if kind is None:
            return False

        # the picked sub-shape is drawn where the component sits; dropping
        # the location recovers the face exactly as the part stores it, which
        # costs nothing because the placed body shares its geometry
        local = shape.Located(TopLoc_Location())
        attachment = self.doc.attach(occurrence, kind, local)

        self._in_selection = True
        try:
            self.host.viewport.clear_selection()
        finally:
            self._in_selection = False

        if attachment is None:
            self.status.setStyleSheet("color: %s;" % C.warn)
            self.status.setText(
                "Nothing can be mated to that - pick a flat or round face, a "
                "circular edge or a straight one.")
            return True

        other = (self.constraint.b if field is self.first
                 else self.constraint.a)
        if (other and other.valid
                and other.occurrence == occurrence_id):
            self.status.setStyleSheet("color: %s;" % C.warn)
            self.status.setText(
                "Both picks are on the same component - a constraint holds "
                "two different ones together.")
            return True

        if field is self.first:
            self.constraint.a = attachment
        else:
            self.constraint.b = attachment
        self._sync_fields()

        if field is self.first and not (self.constraint.b
                                        and self.constraint.b.valid):
            self.arm(self.second)
        else:
            self.arm(None)
        self._changed()
        return True

    def _clear(self, which: int) -> None:
        if which == 0:
            self.constraint.a = Attachment()
            self.arm(self.first)
        else:
            self.constraint.b = Attachment()
            self.arm(self.second)
        self._sync_fields()
        self._changed()

    # -------------------------------------------------- which way faces look

    def _show_arrows(self) -> None:
        """An arrow out of each picked face, along the way it looks.

        With one face picked this already answers the question the Solution
        buttons ask: mate turns the next face towards this arrow, flush
        turns it the same way.  Both arrows then show what the solver is
        about to do before OK is pressed.
        """
        if self._closed:
            return
        arrows = []
        for attachment in (self.constraint.a, self.constraint.b):
            if attachment and attachment.valid:
                arrows.append((attachment, ARROW_COLOURS[len(arrows)]))
        self.controller.show_arrows(arrows)

    # ------------------------------------------------------------- applying

    def _changed(self) -> None:
        if self._closed:
            return
        # the Type row says Mate for both solutions, so the Solution row is
        # what decides which of the two kinds this actually is
        chosen = self.kind.value()
        self.constraint.kind = (self.solution.value() if chosen == MATE
                                else chosen)
        self.constraint.offset = self.offset.text() or "0"
        self.constraint.flip = self.flip.isChecked()

        if not self.complete:
            self.ok_button.setEnabled(False)
            self.apply_button.setEnabled(False)
            return

        if not constraints3d.compatible(self.constraint.a.frame,
                                        self.constraint.b.frame,
                                        self.constraint.kind):
            self.status.setStyleSheet("color: %s;" % C.error)
            self.status.setText(constraints3d.describe(
                self.constraint.kind, self.constraint.a.frame,
                self.constraint.b.frame))
            self.ok_button.setEnabled(False)
            self.apply_button.setEnabled(False)
            return

        if not self._live:
            self.doc.add_constraint(self.constraint)
            self._live = True

        report = self.controller.rebuild()
        self.ok_button.setEnabled(True)
        self.apply_button.setEnabled(True)
        if self.constraint.error:
            self.status.setStyleSheet("color: %s;" % C.error)
            self.status.setText(self.constraint.error)
        else:
            self.status.setStyleSheet("color: %s;" % C.ok)
            self.status.setText("%s.  %s" % (
                constraints3d.describe(self.constraint.kind,
                                       self.constraint.a.frame,
                                       self.constraint.b.frame),
                report.solver))

    def apply_and_continue(self) -> None:
        """Keep this one and open a fresh constraint of the same kind."""
        if not self.complete:
            return
        self._changed()
        committed = self.constraint
        self._snapshot = self.doc.snapshot()
        self.constraint = AssemblyConstraint(kind=committed.kind)
        self._live = False
        self.is_new = True
        self.offset.set_text("0")
        self._sync_fields()
        self.arm(self.first)
        self.controller.browser.refresh()
        self.status.setStyleSheet("color: %s;" % C.ok)
        self.status.setText("%s placed. Pick the next pair."
                            % committed.name)

    def commit(self) -> None:
        if self.complete:
            self._changed()
        self._closed = True
        self.committed.emit(self.constraint.id)
        self.close()

    def cancel(self) -> None:
        self._closed = True
        self.doc.load_dict(json.loads(self._snapshot))
        self.controller.rebuild()
        self.cancelled.emit()
        self.close()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if not self._closed:
            self.cancel()
            return
        self.arm(None)
        self.controller.clear_arrows()
        self.controller.dialog_finished(self)
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


class PlacementDialog(QtWidgets.QDialog):
    """Type a component's position and rotation in exactly."""

    def __init__(self, controller: "AssemblyController",
                 occurrence: Occurrence) -> None:
        super().__init__(controller.host)
        self.controller = controller
        self.doc = controller.document
        self.occurrence = occurrence
        self._snapshot = self.doc.snapshot()
        self._closed = False

        self.setWindowTitle("Move Component")
        self.setWindowIcon(icons.icon("move", 24))
        self.setWindowFlags(QtCore.Qt.Tool | QtCore.Qt.WindowTitleHint
                            | QtCore.Qt.WindowCloseButtonHint)
        self.setModal(False)
        self.setMinimumWidth(320)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(9)

        caption = QtWidgets.QLabel(occurrence.label)
        caption.setStyleSheet("font-size: 13px; font-weight: 600;")
        root.addWidget(caption)

        form = FormRows()
        root.addWidget(form)

        position = occurrence.placement.position
        rotation = [math.degrees(v) for v in occurrence.placement.rotation]
        self.fields: List[ExpressionEdit] = []
        for label, value, unit in (("X", position[0], "mm"),
                                   ("Y", position[1], "mm"),
                                   ("Z", position[2], "mm"),
                                   ("Turn about X", rotation[0], "deg"),
                                   ("Turn about Y", rotation[1], "deg"),
                                   ("Turn about Z", rotation[2], "deg")):
            edit = ExpressionEdit("%.4g" % value, self.doc.params, unit)
            edit.changed.connect(self._changed)
            form.add(label, edit)
            self.fields.append(edit)

        self.note = QtWidgets.QLabel("")
        self.note.setProperty("hint", True)
        self.note.setWordWrap(True)
        root.addWidget(self.note)
        root.addStretch(1)

        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        ok = QtWidgets.QPushButton("OK")
        ok.setProperty("primary", True)
        ok.setDefault(True)
        cancel = QtWidgets.QPushButton("Cancel")
        buttons.addWidget(ok)
        buttons.addWidget(cancel)
        root.addLayout(buttons)
        ok.clicked.connect(self.commit)
        cancel.clicked.connect(self.cancel)

        if occurrence.grounded:
            self.note.setText(
                "This component is grounded, so the solver will leave it "
                "exactly where you put it.")

    def _changed(self) -> None:
        if self._closed:
            return
        values = [f.value() for f in self.fields]
        self.occurrence.placement.position = values[0:3]
        self.occurrence.placement.rotation = [math.radians(v)
                                              for v in values[3:6]]
        report = self.controller.rebuild()
        if not self.occurrence.grounded and report.dof:
            self.note.setText(
                "The solver may move it again - ground it to pin it down.")

    def commit(self) -> None:
        self._changed()
        self._closed = True
        self.close()

    def cancel(self) -> None:
        self._closed = True
        self.doc.load_dict(json.loads(self._snapshot))
        self.controller.rebuild()
        self.close()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if not self._closed:
            self.cancel()
            return
        self.controller.dialog_finished(self)
        super().closeEvent(event)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() == QtCore.Qt.Key_Escape:
            self.cancel()
            return
        super().keyPressEvent(event)


class AssemblyController(QtCore.QObject):
    """Everything the main window does while an assembly is open."""

    def __init__(self, host) -> None:
        super().__init__(host)
        self.host = host
        self.browser = AssemblyBrowser(host)
        self.dialog: Optional[QtWidgets.QDialog] = None
        self.tool: Optional[str] = None
        self._drag_snapshot: Optional[str] = None
        self._dragging: int = 0
        self._isolated: Optional[int] = None
        # (attachment, colour) pairs drawn as normal arrows while a
        # constraint is being placed
        self._arrows: List[Any] = []
        self._wire()

    @property
    def document(self) -> Optional[AssemblyDocument]:
        return self.host.assembly

    @property
    def viewport(self):
        return self.host.viewport

    # ------------------------------------------------------------ wiring

    def _wire(self) -> None:
        b = self.browser
        b.occurrence_selected.connect(self._occurrence_selected)
        b.occurrence_activated.connect(self.open_component)
        b.occurrence_delete_requested.connect(self.delete_occurrence)
        b.occurrence_rename_requested.connect(self.rename_occurrence)
        b.ground_toggled.connect(self.toggle_ground)
        b.visibility_toggled.connect(self.toggle_visibility)
        b.suppress_toggled.connect(self.toggle_suppress)
        b.isolate_requested.connect(self.isolate)
        b.replace_requested.connect(self.replace_component)
        b.constraint_activated.connect(self.edit_constraint)
        b.constraint_selected.connect(self._constraint_selected)
        b.constraint_delete_requested.connect(self.delete_constraint)
        b.constraint_suppress_toggled.connect(self.toggle_constraint_suppress)
        b.plane_visibility_toggled.connect(self.toggle_plane)
        b.place_requested.connect(self.place_component)

        v = self.viewport
        v.component_drag_started.connect(self._drag_started)
        v.component_drag_moved.connect(self._drag_moved)
        v.component_drag_finished.connect(self._drag_finished)
        # the viewport asks this before letting a drag start with no tool
        v.component_freely_movable = self._free_drag_allowed

    # ------------------------------------------------------------ rebuild

    def rebuild(self, keep_camera: bool = True) -> AssemblyReport:
        doc = self.document
        if doc is None:
            return AssemblyReport()
        report = doc.rebuild()
        self.show_components(keep_camera=keep_camera)
        self.browser.refresh()
        self.host.properties.update_from(doc)
        self.host.update_title()

        host = self.host
        host.status_build.setText(report.message)
        if report.missing or report.errors:
            host.status_build.setStyleSheet("color: %s;" % C.error)
        elif report.dof:
            host.status_build.setStyleSheet("color: %s;" % C.warn)
        else:
            host.status_build.setStyleSheet("color: %s;" % C.text_dim)
        return report

    def show_components(self, keep_camera: bool = True) -> None:
        doc = self.document
        if doc is None:
            return
        selected = set(self.browser.selected_occurrence_ids())
        items: List[Dict[str, Any]] = []
        for index, occurrence in enumerate(doc.occurrences):
            if occurrence.suppressed or not occurrence.visible:
                continue
            placed = occurrence.placed()
            if placed is None:
                continue
            items.append({
                "id": occurrence.id,
                "shape": placed,
                "colour": self.viewport.component_colour(index),
                "highlight": occurrence.id in selected,
            })
        self.viewport.set_shape(None, keep_camera=True)
        self.viewport.set_components(items, keep_camera=keep_camera)
        self.viewport.show_planes(self.visible_planes(), self._plane_size())
        # the components were just rebuilt underneath them, so the arrows
        # have to be put back where the faces now are
        self._draw_arrows()

    # ------------------------------------------------- which way a face looks

    def show_arrows(self, items: Sequence[Any]) -> None:
        """Draw a normal arrow on each of ``(attachment, colour)``.

        This is the thing that makes Mate and Flush legible before the
        second pick: an arrow stands out of each chosen face along the way
        it looks, so two arrows pointing at each other are about to mate and
        two pointing the same way are about to go flush.
        """
        self._arrows = list(items)
        self._draw_arrows()

    def clear_arrows(self) -> None:
        self._arrows = []
        self.viewport.clear_preview()
        self.viewport.redraw()

    def _draw_arrows(self) -> None:
        doc = self.document
        self.viewport.clear_preview()
        if doc is None or not self._arrows:
            return
        for attachment, colour in self._arrows:
            frame = doc.world_frame(attachment)
            if frame is None:
                continue
            self.viewport.draw_arrow(frame.origin, frame.direction, colour)
        self.viewport.redraw()

    def visible_planes(self) -> Dict[str, Any]:
        from ..core.sketch import STANDARD_PLANES

        return {key: plane for key, plane in STANDARD_PLANES.items()
                if key not in self.browser.hidden_planes}

    def _plane_size(self) -> float:
        doc = self.document
        if doc is None or doc.shape is None:
            return 45.0
        xmin, ymin, zmin, xmax, ymax, zmax = kernel.bounding_box(doc.shape)
        return max(25.0, max(xmax - xmin, ymax - ymin, zmax - zmin) * 0.7)

    def toggle_plane(self, key: str) -> None:
        if key in self.browser.hidden_planes:
            self.browser.hidden_planes.discard(key)
        else:
            self.browser.hidden_planes.add(key)
        self.browser.refresh()
        self.viewport.show_planes(self.visible_planes(), self._plane_size())

    # ------------------------------------------------------ placing parts

    def place_component(self) -> None:
        """Bring one or more files into the assembly."""
        doc = self.document
        if doc is None:
            return
        self.take_over()
        start = doc.base_dir or self.host.project_folder()
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self.host, "Place Component", start,
            "Parts and assemblies (*.pdat *.adat);;"
            "DATUM Part (*.pdat);;DATUM Assembly (*.adat);;All files (*)")
        if not paths:
            return

        target = os.path.normcase(os.path.abspath(doc.path)) if doc.path else ""
        doc.push_undo()
        arrivals: List[Occurrence] = []
        for path in paths:
            if target and os.path.normcase(os.path.abspath(path)) == target:
                QtWidgets.QMessageBox.warning(
                    self.host, "Place Component",
                    "An assembly cannot place itself.")
                continue
            try:
                fileformat.peek(path)
            except fileformat.FileFormatError as exc:
                QtWidgets.QMessageBox.warning(self.host, "Place Component",
                                              str(exc))
                continue
            arrivals.append(doc.place(path))

        if not arrivals:
            return
        # the bodies have to be loaded before anything can be set down beside
        # anything else, so this first rebuild is what measures them
        doc.rebuild()
        self._spread(arrivals)
        report = self.rebuild(keep_camera=False)

        self.host.status_message.setStyleSheet("")
        self.host.status_message.setText(
            "Placed %d component(s). %s" % (
                len(arrivals),
                "The first one is grounded; constrain the rest to it."
                if len(doc.occurrences) == len(arrivals)
                else "Use Constrain to tie them together."))
        if report.missing:
            QtWidgets.QMessageBox.warning(self.host, "Place Component",
                                          report.message)

    def _spread(self, arrivals: List[Occurrence]) -> None:
        """Set new components down in a row rather than inside each other.

        Inventor drops a component where you click; there is no click here,
        so the next free spot along X is the honest equivalent.  Anything
        already constrained is left alone - the solver owns its position.
        """
        doc = self.document
        if doc is None:
            return
        edge: Optional[float] = None
        for occurrence in doc.occurrences:
            if occurrence in arrivals or occurrence.shape is None:
                continue
            placed = occurrence.placed()
            if placed is None:
                continue
            try:
                xmax = kernel.bounding_box(placed)[3]
            except Exception:
                continue
            edge = xmax if edge is None else max(edge, xmax)

        for occurrence in arrivals:
            if occurrence.shape is None:
                continue
            try:
                xmin, _ymin, _zmin, xmax, _a, _b = kernel.bounding_box(
                    occurrence.shape)
            except Exception:
                continue
            if edge is None:
                edge = xmax          # the first one stays on the origin
                continue
            shift = edge + PLACE_GAP - xmin
            occurrence.placement.position = [shift, 0.0, 0.0]
            edge = shift + xmax

    def replace_component(self, occurrence_id: int) -> None:
        doc = self.document
        occurrence = doc.occurrence(occurrence_id) if doc else None
        if occurrence is None:
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self.host, "Replace Component",
            doc.base_dir or self.host.project_folder(),
            "Parts and assemblies (*.pdat *.adat);;All files (*)")
        if not path:
            return
        doc.push_undo()
        base = doc.base_dir
        occurrence.ref.path = (fileformat.relative_path(path, base) if base
                               else path)
        occurrence.ref.name = os.path.basename(path)
        report = self.rebuild()
        self.host.status_message.setText(
            "Replaced %s with %s. %s"
            % (occurrence.label, os.path.basename(path),
               "Its constraints may need re-picking."
               if any(c.error for c in doc.constraints_on(occurrence_id))
               else ""))

    # -------------------------------------------------------- constraints

    def constrain(self, kind: str = MATE) -> None:
        doc = self.document
        if doc is None:
            return
        if len(doc.occurrences) < 2:
            QtWidgets.QMessageBox.information(
                self.host, "Constrain",
                "A constraint holds two components together. Place at least "
                "two first.")
            return
        self.take_over()
        doc.push_undo()
        self._open(ConstraintDialog(self, kind=kind))

    def edit_constraint(self, constraint_id: int) -> None:
        doc = self.document
        constraint = doc.constraint(constraint_id) if doc else None
        if constraint is None:
            return
        self.close_dialogs()
        doc.push_undo()
        self._open(ConstraintDialog(self, constraint=constraint))

    def delete_constraint(self, constraint_id: int) -> None:
        doc = self.document
        constraint = doc.constraint(constraint_id) if doc else None
        if constraint is None:
            return
        doc.push_undo()
        doc.remove_constraint(constraint_id)
        self.rebuild()
        self.host.status_message.setText("Deleted %s." % constraint.name)

    def toggle_constraint_suppress(self, constraint_id: int) -> None:
        doc = self.document
        constraint = doc.constraint(constraint_id) if doc else None
        if constraint is None:
            return
        doc.push_undo()
        constraint.suppressed = not constraint.suppressed
        self.rebuild()

    # -------------------------------------------------------- occurrences

    def delete_occurrence(self, occurrence_id: int) -> None:
        doc = self.document
        occurrence = doc.occurrence(occurrence_id) if doc else None
        if occurrence is None:
            return
        held = doc.constraints_on(occurrence_id)
        text = "Delete %s?" % occurrence.label
        if held:
            text += ("\n\nThese relationships go with it:\n  %s"
                     % "\n  ".join(c.name for c in held))
        if QtWidgets.QMessageBox.question(
                self.host, "Delete", text) != QtWidgets.QMessageBox.Yes:
            return
        doc.push_undo()
        doc.remove_occurrence(occurrence_id)
        self.rebuild()

    def rename_occurrence(self, occurrence_id: int, name: str) -> None:
        doc = self.document
        occurrence = doc.occurrence(occurrence_id) if doc else None
        if occurrence is None:
            return
        doc.push_undo()
        occurrence.name = name
        doc.modified = True
        self.browser.refresh()
        self.host.update_title()

    def toggle_ground(self, occurrence_id: int) -> None:
        doc = self.document
        occurrence = doc.occurrence(occurrence_id) if doc else None
        if occurrence is None:
            return
        doc.push_undo()
        occurrence.grounded = not occurrence.grounded
        report = self.rebuild()
        self.host.status_message.setStyleSheet("")
        self.host.status_message.setText(
            "%s is %s.  %s" % (occurrence.label,
                               "grounded" if occurrence.grounded
                               else "free to move",
                               report.solver))

    def toggle_visibility(self, occurrence_id: int) -> None:
        doc = self.document
        occurrence = doc.occurrence(occurrence_id) if doc else None
        if occurrence is None:
            return
        occurrence.visible = not occurrence.visible
        doc.modified = True
        self._isolated = None
        self.rebuild()

    def isolate(self, occurrence_id: int) -> None:
        """Show one component alone, or put everything back."""
        doc = self.document
        if doc is None:
            return
        restore = self._isolated == occurrence_id
        for occurrence in doc.occurrences:
            occurrence.visible = restore or occurrence.id == occurrence_id
        self._isolated = None if restore else occurrence_id
        self.rebuild()
        self.host.status_message.setText(
            "Showing everything." if restore
            else "Isolated - right-click and Isolate again to restore.")

    def toggle_suppress(self, occurrence_id: int) -> None:
        doc = self.document
        occurrence = doc.occurrence(occurrence_id) if doc else None
        if occurrence is None:
            return
        doc.push_undo()
        occurrence.suppressed = not occurrence.suppressed
        self.rebuild()

    def open_component(self, occurrence_id: int) -> None:
        """Edit the file a component places.

        If it is already open in a tab this goes to that tab rather than
        loading a second copy - which is the whole point of keeping every
        document in one window.  If it is not open, it opens in a new tab.
        """
        doc = self.document
        occurrence = doc.occurrence(occurrence_id) if doc else None
        if occurrence is None:
            return
        path = doc.component_path(occurrence)
        if path is None:
            QtWidgets.QMessageBox.warning(
                self.host, "Open Part",
                "%s cannot be found at %s."
                % (occurrence.label, occurrence.ref.path or "(no path)"))
            return
        already = self.host.session.by_path(path)
        self.host.open_path(path)
        if already is None:
            self.host.status_message.setStyleSheet("")
            self.host.status_message.setText(
                "Editing %s. Come back to the assembly and press Local "
                "Update to take your changes." % os.path.basename(path))

    def _occurrence_selected(self, occurrence_id: int) -> None:
        doc = self.document
        occurrence = doc.occurrence(occurrence_id) if doc else None
        if occurrence is None:
            return
        self.host.status_message.setStyleSheet(
            "color: %s;" % C.error if occurrence.error else "")
        self.host.status_message.setText(occurrence.summary())
        self.show_components(keep_camera=True)

    def _constraint_selected(self, constraint_id: int) -> None:
        doc = self.document
        constraint = doc.constraint(constraint_id) if doc else None
        if constraint is None:
            return
        self.host.status_message.setStyleSheet(
            "color: %s;" % C.error if constraint.error else "")
        self.host.status_message.setText("%s  -  %s" % (constraint.name,
                                                        constraint.summary()))

    # -------------------------------------------------- free move / rotate

    def begin_tool(self, mode: Optional[str]) -> None:
        """Arm dragging components around by hand."""
        self.close_dialogs()
        self.tool = mode
        self.viewport.begin_component_tool(mode)
        self.viewport.set_selection_mode("solid" if mode else "assembly")
        self.host.sync_assembly_tools()
        if mode is None:
            self.host.status_message.setText("")
            return
        self.host.status_message.setStyleSheet("")
        self.host.status_message.setText(
            "Free %s: drag a component in the view. Grounded components and "
            "constrained ones snap back when the solver runs. Esc to stop."
            % ("rotate" if mode == "rotate" else "move"))

    def _drag_started(self, occurrence_id: int) -> None:
        doc = self.document
        if doc is None:
            return
        self._drag_snapshot = doc.snapshot()
        self._dragging = occurrence_id

    def _free_drag_allowed(self, occurrence_id: int) -> bool:
        """What the viewport asks before dragging with no tool armed."""
        return self.tool is None and self.freely_movable(occurrence_id)

    def _drag_moved(self, delta) -> None:
        doc = self.document
        occurrence = doc.occurrence(self._dragging) if doc else None
        if occurrence is None:
            return
        if self.tool == "rotate":
            placed = occurrence.placed()
            centre = kernel.shape_centre(placed) if placed is not None else None
            occurrence.placement = occurrence.placement.rotated(delta, centre)
        else:
            occurrence.placement = occurrence.placement.translated(delta)
        doc.modified = True
        # move the body only; a full solve on every mouse move would fight
        # the drag, and the constraints are re-solved on release
        self.show_components(keep_camera=True)

    def _drag_finished(self) -> None:
        doc = self.document
        if doc is None or self._drag_snapshot is None:
            return
        doc._undo.append(self._drag_snapshot)
        del doc._undo[:-60]
        doc._redo.clear()
        self._drag_snapshot = None
        report = self.rebuild()
        self.host.status_message.setText(report.solver or report.message)

    # --------------------------------------------------------- plumbing

    def freely_movable(self, occurrence_id: int) -> bool:
        """Whether nothing is holding this component in place.

        Grounded, suppressed, or named by any live constraint means no: the
        solver would only drag it back, and a part that springs home the
        instant you let go feels broken rather than constrained.  Free Move
        still overrides all of this, which is the point of it.
        """
        doc = self.document
        occurrence = doc.occurrence(occurrence_id) if doc else None
        if occurrence is None or occurrence.grounded or occurrence.suppressed:
            return False
        for constraint in doc.constraints:
            if getattr(constraint, "suppressed", False):
                continue
            for attachment in (constraint.a, constraint.b):
                if getattr(attachment, "occurrence", None) == occurrence_id:
                    return False
        return True

    def take_over(self) -> None:
        """Start something new, and stop whatever was already running.

        Free Move and the constraint dialog both want the viewport's
        picking, and each command only ever cancelled the other half.
        Opening Constrain with Free Move still armed left the dialog unable
        to select anything, which reads as the constraint being broken
        rather than as two tools fighting.
        """
        self.close_dialogs()
        if self.tool is not None:
            self.begin_tool(None)

    def set_picking(self, on: bool) -> None:
        self.viewport.set_selection_mode("assembly" if on else "solid")
        if not on:
            self.viewport.clear_selection()

    def _open(self, dialog: QtWidgets.QDialog) -> None:
        self.dialog = dialog
        self.host.position_dialog(dialog)
        dialog.show()
        dialog.raise_()

    def dialog_finished(self, dialog: QtWidgets.QDialog) -> None:
        if self.dialog is dialog:
            self.dialog = None
        self.viewport.set_selection_mode("solid")
        self.viewport.clear_selection()

    def close_dialogs(self) -> None:
        if self.dialog is not None:
            dialog = self.dialog
            self.dialog = None
            dialog.close()

    def on_selection(self) -> bool:
        if isinstance(self.dialog, ConstraintDialog):
            if self.dialog.on_selection():
                return True
        picked = self.viewport.selected_components()
        if picked:
            self.browser.select_occurrences(picked[:1])
            self._occurrence_selected(picked[0])
        return False

    def on_escape(self) -> bool:
        if self.dialog is not None:
            getattr(self.dialog, "cancel", self.dialog.close)()
            return True
        if self.tool is not None:
            self.begin_tool(None)
            self.host.status_message.setText("Cancelled.")
            return True
        return False

    def context_menu(self, menu: QtWidgets.QMenu) -> None:
        """Right-click in the viewport while an assembly is open."""
        picked = self.viewport.selected_components()
        doc = self.document
        if picked and doc is not None:
            occurrence = doc.occurrence(picked[0])
            if occurrence is not None:
                title = menu.addAction(icons.icon(occurrence.icon, 16),
                                       occurrence.label)
                title.setEnabled(False)
                menu.addSeparator()
                ground = menu.addAction("Grounded")
                ground.setCheckable(True)
                ground.setChecked(occurrence.grounded)
                ground.triggered.connect(
                    lambda: self.toggle_ground(occurrence.id))
                move = menu.addAction(icons.icon("move", 16), "Move...")
                move.triggered.connect(
                    lambda: self.place_dialog(occurrence.id))
                visible = menu.addAction("Visible")
                visible.setCheckable(True)
                visible.setChecked(occurrence.visible)
                visible.triggered.connect(
                    lambda: self.toggle_visibility(occurrence.id))
                suppress = menu.addAction("Suppressed")
                suppress.setCheckable(True)
                suppress.setChecked(occurrence.suppressed)
                suppress.triggered.connect(
                    lambda: self.toggle_suppress(occurrence.id))
                isolate = menu.addAction("Isolate")
                isolate.triggered.connect(lambda: self.isolate(occurrence.id))
                open_part = menu.addAction(icons.icon("open", 16), "Open Part")
                open_part.triggered.connect(
                    lambda: self.open_component(occurrence.id))
                delete = menu.addAction(icons.icon("delete", 16), "Delete")
                delete.triggered.connect(
                    lambda: self.delete_occurrence(occurrence.id))
                menu.addSeparator()

        place = menu.addAction(icons.icon("import", 16), "Place Component...")
        place.triggered.connect(self.place_component)
        constrain = menu.addAction(icons.icon("c_coincident", 16),
                                   "Constrain...")
        constrain.setEnabled(doc is not None and len(doc.occurrences) >= 2)
        constrain.triggered.connect(lambda: self.constrain(MATE))
        menu.addSeparator()
        menu.addAction(icons.icon("fit", 16), "Fit All").triggered.connect(
            self.viewport.fit_all)
        menu.addAction(icons.icon("iso", 16), "Home View").triggered.connect(
            lambda: self.viewport.set_view("iso"))

    def place_dialog(self, occurrence_id: int) -> None:
        doc = self.document
        occurrence = doc.occurrence(occurrence_id) if doc else None
        if occurrence is None:
            return
        self.take_over()
        doc.push_undo()
        self._open(PlacementDialog(self, occurrence))

    def move_selected(self) -> None:
        picked = (self.browser.selected_occurrence_ids()
                  or self.viewport.selected_components())
        if picked:
            self.place_dialog(picked[0])
            return
        doc = self.document
        if doc and doc.occurrences:
            QtWidgets.QMessageBox.information(
                self.host, "Move Component",
                "Select a component first, in the browser or the view.")
