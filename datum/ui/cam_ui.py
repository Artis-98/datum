"""The CAM workspace: a sheet of stock, parts on it, and the toolpath.

The viewport looks straight down at the sheet.  Parts are drawn as their own
outlines and are what you select and drag; the toolpath is drawn over them
in its own colour so the compensation is visible before anything is
exported.  Picking a cut face is the one moment the 3D body comes back, on
its own, because a face is the thing being picked.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from OCP.TopoDS import TopoDS

from ..core import cam as camcore, fileformat, kernel, sheet as sheetlib
from ..core.cam import CamDocument, CamReport, PlacedPart
from ..core.dxf import DXF_FILTER
from ..core.toolpath import INSIDE, OUTSIDE, SIDE_LABELS
from . import icons
from .cam_browser import CamBrowser
from .theme import C
from .widgets import ExpressionEdit, FormRows, SelectionField

# the toolpath has to read as something other than the part it came from,
# or there is no way to see the compensation
PATH_COLOUR = "#ffb648"
LEAD_COLOUR = "#69c26b"
PART_COLOUR = "#7f8894"
SHEET_COLOUR = "#4a525b"
ERROR_COLOUR = C.error


class SheetDialog(QtWidgets.QDialog):
    """The stock and the tool, which between them drive everything else."""

    def __init__(self, controller: "CamController") -> None:
        super().__init__(controller.host)
        self.controller = controller
        self.doc: CamDocument = controller.document
        self._snapshot = self.doc.snapshot()
        self._closed = False
        self._updating = True

        self.setWindowTitle("Sheet and Tool")
        self.setWindowIcon(icons.icon("rect", 24))
        self.setWindowFlags(QtCore.Qt.Tool | QtCore.Qt.WindowTitleHint
                            | QtCore.Qt.WindowCloseButtonHint)
        self.setModal(False)
        self.setMinimumWidth(340)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(9)

        caption = QtWidgets.QLabel("Sheet and Tool")
        caption.setStyleSheet("font-size: 13px; font-weight: 600;")
        root.addWidget(caption)

        form = FormRows()
        root.addWidget(form)

        def field(label, value, unit):
            edit = ExpressionEdit(value, self.doc.params, unit)
            edit.changed.connect(self._changed)
            form.add(label, edit)
            return edit

        self.width = field("Sheet width", self.doc.sheet_width, "mm")
        self.height = field("Sheet height", self.doc.sheet_height, "mm")
        self.thickness = field("Thickness", self.doc.sheet_thickness, "mm")
        self.margin = field("Clamp margin", self.doc.margin, "mm")
        form.add_separator()
        self.diameter = field("Tool diameter", self.doc.tool_diameter, "mm")
        self.lead = field("Lead in / out", self.doc.lead_length, "mm")

        self.plunge = QtWidgets.QCheckBox("This tool can plunge")
        self.plunge.setChecked(self.doc.tool_plunge)
        self.plunge.setToolTip(
            "A cutter that cannot plunge needs a lead in to enter the "
            "material sideways")
        self.plunge.toggled.connect(lambda _on: self._changed())
        form.add("", self.plunge)

        self.note = QtWidgets.QLabel("")
        self.note.setProperty("hint", True)
        self.note.setWordWrap(True)
        self.note.setMinimumHeight(30)
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

        self._updating = False
        self._changed()

    def _changed(self) -> None:
        if self._updating or self._closed:
            return
        doc = self.doc
        doc.sheet_width = self.width.text()
        doc.sheet_height = self.height.text()
        doc.sheet_thickness = self.thickness.text()
        doc.margin = self.margin.text()
        doc.tool_diameter = self.diameter.text()
        doc.lead_length = self.lead.text()
        doc.tool_plunge = self.plunge.isChecked()
        doc.modified = True

        report = self.controller.rebuild()
        if not self.plunge.isChecked() and doc.leads() <= 0:
            self.note.setStyleSheet("color: %s;" % C.warn)
            self.note.setText(
                "This tool cannot plunge, so give it a lead in - otherwise "
                "it has to drill its own entry hole.")
        elif report.toolpath.errors:
            self.note.setStyleSheet("color: %s;" % C.error)
            self.note.setText(report.toolpath.errors[0])
        else:
            self.note.setStyleSheet("color: %s;" % C.text_dim)
            self.note.setText(report.message)

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


class CutFaceDialog(QtWidgets.QDialog):
    """The Cut face property: what was detected, and how to override it."""

    def __init__(self, controller: "CamController",
                 placed: PlacedPart) -> None:
        super().__init__(controller.host)
        self.controller = controller
        self.doc: CamDocument = controller.document
        self.placed = placed
        self.part_id = placed.id
        self._snapshot = self.doc.snapshot()
        self._closed = False
        self._in_selection = False

        self.setWindowTitle("Cut Face")
        self.setWindowIcon(icons.icon("shell", 24))
        self.setWindowFlags(QtCore.Qt.Tool | QtCore.Qt.WindowTitleHint
                            | QtCore.Qt.WindowCloseButtonHint)
        self.setModal(False)
        self.setMinimumWidth(360)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(9)

        caption = QtWidgets.QLabel(placed.label)
        caption.setStyleSheet("font-size: 13px; font-weight: 600;")
        root.addWidget(caption)

        form = FormRows()
        root.addWidget(form)

        self.detected = QtWidgets.QLabel("")
        self.detected.setWordWrap(True)
        form.add("Face", self.detected)

        self.thickness = QtWidgets.QLabel("")
        form.add("Thickness", self.thickness)

        self.pick = SelectionField("Pick Face")
        self.pick.button.setToolTip(
            "Click a flat face on the part to cut from that one instead")
        self.pick.pick_toggled.connect(self._arm)
        self.pick.cleared.connect(self._redetect)
        self.pick.clear_btn.setToolTip("Go back to the detected face")
        form.add("", self.pick)

        self.flip = QtWidgets.QCheckBox("Flip - read the part from the back")
        self.flip.setToolTip(
            "The other face of the sheet gives a mirrored part. This matters "
            "for anything that is not symmetric, and is invisible until "
            "something comes off the machine backwards.")
        self.flip.setChecked(placed.cut_face.flip)
        self.flip.toggled.connect(self._flip_changed)
        form.add("", self.flip)

        self.note = QtWidgets.QLabel("")
        self.note.setProperty("hint", True)
        self.note.setWordWrap(True)
        self.note.setMinimumHeight(34)
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

        self._load()

    # -- state -------------------------------------------------------------

    def _current(self) -> Optional[PlacedPart]:
        return self.doc.part(self.part_id)

    def _load(self) -> None:
        placed = self._current()
        if placed is None:
            return
        cut = placed.cut_face
        if cut.error:
            self.detected.setText(cut.error)
            self.detected.setStyleSheet("color: %s;" % C.error)
        elif cut.face is not None:
            self.detected.setText(
                "%s, %.0f mm2%s"
                % ("picked by hand" if cut.manual else "detected",
                   kernel.face_area(cut.face),
                   ", flipped" if cut.flip else ""))
            self.detected.setStyleSheet("color: %s;" % C.text_dim)
        else:
            self.detected.setText("not found")
            self.detected.setStyleSheet("color: %s;" % C.warn)
        self.thickness.setText("%.3f mm" % cut.thickness)

        if cut.warning:
            self.note.setStyleSheet("color: %s;" % C.warn)
            self.note.setText(cut.warning)
        elif placed.profile is not None:
            self.note.setStyleSheet("color: %s;" % C.text_dim)
            self.note.setText(
                "%.1f x %.1f mm outline, %d opening(s)."
                % (placed.profile.width, placed.profile.height,
                   len(placed.profile.holes)))

    # -- picking -----------------------------------------------------------

    def _arm(self, on: bool) -> None:
        placed = self._current()
        if placed is None:
            return
        self.controller.set_face_picking(placed.id if on else None)
        if on:
            self.note.setStyleSheet("color: %s;" % C.text_dim)
            self.note.setText(
                "The part is shown on its own - click the flat face it should "
                "be cut from.")

    def on_selection(self) -> bool:
        """A face was clicked while this dialog had picking armed."""
        if self._in_selection or self._closed or not self.pick.picking:
            return False
        placed = self._current()
        if placed is None:
            return False
        faces = self.controller.viewport.selected_faces()
        if not faces:
            return False

        self._in_selection = True
        try:
            self.controller.viewport.clear_selection()
        finally:
            self._in_selection = False

        if not self.doc.set_cut_face(placed, faces[0]):
            self.note.setStyleSheet("color: %s;" % C.warn)
            self.note.setText("That face is not flat, so nothing can be cut "
                              "from it. Pick a flat one.")
            return True

        self.flip.blockSignals(True)
        self.flip.setChecked(False)
        self.flip.blockSignals(False)
        self.pick.set_picking(False)
        self.controller.set_face_picking(None)
        self.controller.rebuild()
        self._load()
        return True

    def _redetect(self) -> None:
        placed = self._current()
        if placed is None:
            return
        placed.cut_face.ref = None
        placed.cut_face.manual = False
        self.doc.modified = True
        self.controller.rebuild()
        self._load()

    def _flip_changed(self, on: bool) -> None:
        placed = self._current()
        if placed is None:
            return
        placed.cut_face.flip = on
        self.doc.modified = True
        self.controller.rebuild()
        self._load()

    # -- lifetime ----------------------------------------------------------

    def commit(self) -> None:
        self._closed = True
        self.controller.set_face_picking(None)
        self.controller.rebuild()
        self.close()

    def cancel(self) -> None:
        self._closed = True
        self.controller.set_face_picking(None)
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


class PartPlacementDialog(QtWidgets.QDialog):
    """Type a part's position and rotation on the sheet exactly."""

    def __init__(self, controller: "CamController",
                 placed: PlacedPart) -> None:
        super().__init__(controller.host)
        self.controller = controller
        self.doc: CamDocument = controller.document
        self.part_id = placed.id
        self._snapshot = self.doc.snapshot()
        self._closed = False

        self.setWindowTitle("Move Part")
        self.setWindowIcon(icons.icon("move", 24))
        self.setWindowFlags(QtCore.Qt.Tool | QtCore.Qt.WindowTitleHint
                            | QtCore.Qt.WindowCloseButtonHint)
        self.setModal(False)
        self.setMinimumWidth(320)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(9)

        caption = QtWidgets.QLabel(placed.label)
        caption.setStyleSheet("font-size: 13px; font-weight: 600;")
        root.addWidget(caption)

        form = FormRows()
        root.addWidget(form)

        self.x = ExpressionEdit("%.4g" % placed.position[0], self.doc.params,
                                "mm")
        self.y = ExpressionEdit("%.4g" % placed.position[1], self.doc.params,
                                "mm")
        self.angle = ExpressionEdit("%.4g" % placed.rotation, self.doc.params,
                                    "deg")
        for label, widget in (("X", self.x), ("Y", self.y),
                              ("Rotation", self.angle)):
            widget.changed.connect(self._changed)
            form.add(label, widget)

        self.mirror = QtWidgets.QCheckBox("Mirror this copy")
        self.mirror.setChecked(placed.mirror)
        self.mirror.toggled.connect(lambda _on: self._changed())
        form.add("", self.mirror)

        self.note = QtWidgets.QLabel("")
        self.note.setProperty("hint", True)
        self.note.setWordWrap(True)
        self.note.setMinimumHeight(30)
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

    def _changed(self) -> None:
        if self._closed:
            return
        self.doc.move_part(self.part_id, (self.x.value(), self.y.value()),
                           rotation=self.angle.value(),
                           mirror=self.mirror.isChecked())
        report = self.controller.rebuild()
        failed = [c for c in report.toolpath.passes
                  if c.part_id == self.part_id and c.error]
        if failed:
            self.note.setStyleSheet("color: %s;" % C.error)
            self.note.setText(failed[0].error)
        else:
            self.note.setStyleSheet("color: %s;" % C.text_dim)
            self.note.setText("Placed by hand - auto arrange is the only "
                              "thing that will move it now.")

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


class CamController(QtCore.QObject):
    """Everything the main window does while a CAM sheet is open."""

    def __init__(self, host) -> None:
        super().__init__(host)
        self.host = host
        self.browser = CamBrowser(host)
        self.dialog: Optional[QtWidgets.QDialog] = None
        self.tool: Optional[str] = None
        self.face_picking: Optional[int] = None
        self.show_paths = True
        self._drag_snapshot: Optional[str] = None
        self._dragging: int = 0
        self._wire()

    @property
    def document(self) -> Optional[CamDocument]:
        return self.host.cam

    @property
    def viewport(self):
        return self.host.viewport

    # ------------------------------------------------------------- wiring

    def _wire(self) -> None:
        b = self.browser
        b.part_selected.connect(self._part_selected)
        b.part_activated.connect(self.open_part)
        b.part_delete_requested.connect(self.delete_part)
        b.part_rename_requested.connect(self.rename_part)
        b.mirror_toggled.connect(self.toggle_mirror)
        b.suppress_toggled.connect(self.toggle_suppress)
        b.rotate_requested.connect(self.rotate_part)
        b.move_requested.connect(self.move_dialog)
        b.cut_face_requested.connect(self.cut_face_dialog)
        b.flip_requested.connect(self.toggle_flip)
        b.side_changed.connect(self.set_side)
        b.stock_requested.connect(self.sheet_dialog)
        b.add_requested.connect(self.add_part)

        v = self.viewport
        v.component_drag_started.connect(self._drag_started)
        v.component_drag_moved.connect(self._drag_moved)
        v.component_drag_finished.connect(self._drag_finished)

    # ------------------------------------------------------------ rebuild

    def rebuild(self, keep_camera: bool = True) -> CamReport:
        doc = self.document
        if doc is None:
            return CamReport()
        report = doc.rebuild()
        self.draw(keep_camera=keep_camera)
        self.browser.refresh()
        self.host.properties.update_from(doc)
        self.host.update_title()

        host = self.host
        host.status_build.setText(report.message)
        if report.missing or report.errors:
            host.status_build.setStyleSheet("color: %s;" % C.error)
        elif report.warnings:
            host.status_build.setStyleSheet("color: %s;" % C.warn)
        else:
            host.status_build.setStyleSheet("color: %s;" % C.text_dim)
        return report

    def draw(self, keep_camera: bool = True) -> None:
        """Put the sheet, the parts and the toolpath on screen."""
        doc = self.document
        if doc is None:
            return

        if self.face_picking is not None:
            self._draw_single_part(keep_camera)
            return

        selected = set(self.browser.selected_part_ids())
        items: List[Dict[str, Any]] = []
        for placed in doc.active():
            if placed.profile is None:
                continue
            wires = [sheetlib.place(loop.wire, placed.position,
                                    placed.rotation, placed.mirror)
                     for loop in placed.profile.loops if loop.wire is not None]
            if not wires:
                continue
            items.append({
                "id": placed.id,
                "shape": kernel.compound(wires),
                "colour": ERROR_COLOUR if placed.error else PART_COLOUR,
                "wire": True,
                "highlight": placed.id in selected,
            })

        self.viewport.set_shape(None, keep_camera=True)
        self.viewport.set_components(items, keep_camera=True)
        self.viewport.hide_planes()
        self.viewport.clear_overlay()

        # the stock counts towards Fit All: the sheet is the thing you want
        # to see, even when the parts on it only cover one corner
        for index, wire in enumerate(doc.sheet_outline()):
            self.viewport.draw_shape(wire, SHEET_COLOUR, 1.6,
                                     dashed=index > 0, fittable=True)

        if self.show_paths:
            self._draw_toolpath(doc)
        if not keep_camera:
            self.viewport.fit_all()
        self.viewport.redraw()

    def _draw_toolpath(self, doc: CamDocument) -> None:
        for cut in doc.last_report.toolpath.passes:
            if cut.error or cut.wire is None:
                continue
            self.viewport.draw_shape(cut.wire, PATH_COLOUR, 2.2)
            for lead in (cut.lead_in, cut.lead_out):
                if lead is not None:
                    self.viewport.draw_shape(lead, LEAD_COLOUR, 2.0,
                                             dashed=True)

    def _draw_single_part(self, keep_camera: bool) -> None:
        """Show one part's body alone, so a face can be picked off it."""
        doc = self.document
        placed = doc.part(self.face_picking) if doc else None
        self.viewport.clear_components()
        self.viewport.clear_overlay()
        self.viewport.set_shape(placed.shape if placed else None,
                                keep_camera=keep_camera)
        self.viewport.set_selection_mode("face")
        self.viewport.redraw()

    def set_face_picking(self, part_id: Optional[int]) -> None:
        self.face_picking = part_id
        if part_id is None:
            self.viewport.set_selection_mode("solid")
            self.draw(keep_camera=False)
            self.viewport.set_view("top")
        else:
            self.draw(keep_camera=False)
            self.viewport.set_view("iso")
            self.viewport.fit_all()

    # -------------------------------------------------------- adding parts

    def add_part(self) -> None:
        doc = self.document
        if doc is None:
            return
        self.close_dialogs()
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self.host, "Add Part to Sheet",
            doc.base_dir or self.host.project_folder(),
            "DATUM Part (*.pdat);;All files (*)")
        if not paths:
            return

        doc.push_undo()
        added = 0
        for path in paths:
            try:
                manifest = fileformat.peek(path)
            except fileformat.FileFormatError as exc:
                QtWidgets.QMessageBox.warning(self.host, "Add Part", str(exc))
                continue
            if manifest.type != fileformat.PART:
                QtWidgets.QMessageBox.warning(
                    self.host, "Add Part",
                    "%s is a %s. A CAM sheet cuts parts."
                    % (os.path.basename(path),
                       fileformat.TYPE_LABELS.get(manifest.type,
                                                  manifest.type)))
                continue
            doc.add_part(path)
            added += 1

        if not added:
            return
        # the profile has to exist before a part can be given a spot
        doc.rebuild()
        doc.auto_arrange(only_new=True)
        report = self.rebuild(keep_camera=False)

        self.host.status_message.setStyleSheet("")
        self.host.status_message.setText(
            "Added %d part(s). %s" % (added, report.message))
        rejected = [p for p in doc.parts if p.cut_face.error]
        if rejected:
            QtWidgets.QMessageBox.warning(
                self.host, "Not a sheet part",
                "%s\n\n%s" % (rejected[0].label, rejected[0].cut_face.error))

    def delete_part(self, part_id: int) -> None:
        doc = self.document
        placed = doc.part(part_id) if doc else None
        if placed is None:
            return
        if QtWidgets.QMessageBox.question(
                self.host, "Delete",
                "Take %s off the sheet?" % placed.label
                ) != QtWidgets.QMessageBox.Yes:
            return
        doc.push_undo()
        doc.remove_part(part_id)
        self.rebuild()

    def rename_part(self, part_id: int, name: str) -> None:
        doc = self.document
        placed = doc.part(part_id) if doc else None
        if placed is None:
            return
        doc.push_undo()
        placed.name = name
        doc.modified = True
        self.browser.refresh()
        self.host.update_title()

    def open_part(self, part_id: int) -> None:
        """Edit the part this sheet cuts, in its own tab.

        An already-open part goes to its existing tab rather than being
        loaded twice, so the sheet and the part share one document.
        """
        doc = self.document
        placed = doc.part(part_id) if doc else None
        if placed is None:
            return
        path = doc.part_path(placed)
        if path is None:
            QtWidgets.QMessageBox.warning(
                self.host, "Open Part",
                "%s cannot be found at %s."
                % (placed.label, placed.ref.path or "(no path)"))
            return
        already = self.host.session.by_path(path)
        self.host.open_path(path)
        if already is None:
            self.host.status_message.setStyleSheet("")
            self.host.status_message.setText(
                "Editing %s. Come back to the sheet and press Local Update "
                "to regenerate the toolpath." % os.path.basename(path))

    # ------------------------------------------------------------- layout

    def auto_arrange(self) -> None:
        doc = self.document
        if doc is None:
            return
        hand_placed = [p for p in doc.active() if p.manual]
        if hand_placed:
            answer = QtWidgets.QMessageBox.question(
                self.host, "Auto Arrange",
                "%d part(s) were placed by hand. Arranging will move them "
                "back onto the grid.\n\nArrange everything?"
                % len(hand_placed))
            if answer != QtWidgets.QMessageBox.Yes:
                return
        doc.push_undo()
        arranged = doc.auto_arrange()
        report = self.rebuild(keep_camera=False)
        self.host.status_message.setStyleSheet("")
        self.host.status_message.setText(
            "Arranged %d part(s) with a %.3g mm gap. %s"
            % (arranged, doc.gap(), report.message))

    def rotate_part(self, part_id: int, delta: float) -> None:
        doc = self.document
        placed = doc.part(part_id) if doc else None
        if placed is None:
            return
        doc.push_undo()
        doc.move_part(part_id, None, rotation=placed.rotation + delta)
        self.rebuild()

    def toggle_mirror(self, part_id: int) -> None:
        doc = self.document
        placed = doc.part(part_id) if doc else None
        if placed is None:
            return
        doc.push_undo()
        doc.move_part(part_id, None, mirror=not placed.mirror)
        self.rebuild()

    def toggle_suppress(self, part_id: int) -> None:
        doc = self.document
        placed = doc.part(part_id) if doc else None
        if placed is None:
            return
        doc.push_undo()
        placed.suppressed = not placed.suppressed
        self.rebuild()

    def toggle_flip(self, part_id: int) -> None:
        doc = self.document
        placed = doc.part(part_id) if doc else None
        if placed is None:
            return
        doc.push_undo()
        placed.cut_face.flip = not placed.cut_face.flip
        doc.modified = True
        self.rebuild()
        self.host.status_message.setStyleSheet("")
        self.host.status_message.setText(
            "%s is read from the %s of the sheet, so it is %s."
            % (placed.label, "back" if placed.cut_face.flip else "front",
               "mirrored" if placed.cut_face.flip else "the way it was drawn"))

    def set_side(self, part_id: int, key: str, side: str) -> None:
        doc = self.document
        placed = doc.part(part_id) if doc else None
        if placed is None:
            return
        doc.push_undo()
        placed.set_side(key, side)
        doc.modified = True
        report = self.rebuild()
        failed = next((c for c in report.toolpath.passes
                       if c.part_id == part_id and c.key == key and c.error),
                      None)
        self.host.status_message.setStyleSheet(
            "color: %s;" % C.error if failed else "")
        self.host.status_message.setText(
            failed.error if failed
            else "Cutting %s of that line." % SIDE_LABELS.get(side,
                                                              side).lower())

    # ------------------------------------------------------ dragging parts

    def begin_tool(self, mode: Optional[str]) -> None:
        self.close_dialogs()
        self.tool = mode
        self.viewport.begin_component_tool(mode)
        self.viewport.set_selection_mode("solid")
        self.host.sync_cam_tools()
        if mode is None:
            return
        self.viewport.set_view("top")
        self.host.status_message.setStyleSheet("")
        self.host.status_message.setText(
            "Drag a part to move it on the sheet. Anything you move by hand "
            "stays put when the toolpath regenerates. Esc to stop.")

    def _drag_started(self, part_id: int) -> None:
        doc = self.document
        if doc is None or self.tool is None:
            return
        self._drag_snapshot = doc.snapshot()
        self._dragging = part_id

    def _drag_moved(self, delta) -> None:
        doc = self.document
        placed = doc.part(self._dragging) if doc else None
        if placed is None or self.tool is None:
            return
        # the sheet is flat, so only what happens in its plane counts
        doc.move_part(placed.id,
                      (placed.position[0] + float(delta[0]),
                       placed.position[1] + float(delta[1])))
        self.draw(keep_camera=True)

    def _drag_finished(self) -> None:
        doc = self.document
        if doc is None or self._drag_snapshot is None:
            return
        doc._undo.append(self._drag_snapshot)
        del doc._undo[:-60]
        doc._redo.clear()
        self._drag_snapshot = None
        report = self.rebuild()
        self.host.status_message.setText(report.message)

    # ------------------------------------------------------------ dialogs

    def sheet_dialog(self) -> None:
        if self.document is None:
            return
        self.close_dialogs()
        self.document.push_undo()
        self._open(SheetDialog(self))

    def cut_face_dialog(self, part_id: int) -> None:
        doc = self.document
        placed = doc.part(part_id) if doc else None
        if placed is None:
            return
        if placed.shape is None:
            QtWidgets.QMessageBox.information(
                self.host, "Cut Face",
                "%s has no body to read a face from." % placed.label)
            return
        self.close_dialogs()
        doc.push_undo()
        self._open(CutFaceDialog(self, placed))

    def move_dialog(self, part_id: int) -> None:
        doc = self.document
        placed = doc.part(part_id) if doc else None
        if placed is None:
            return
        self.close_dialogs()
        doc.push_undo()
        self._open(PartPlacementDialog(self, placed))

    def selected_part(self) -> Optional[int]:
        picked = (self.browser.selected_part_ids()
                  or self.viewport.selected_components())
        return picked[0] if picked else None

    # ------------------------------------------------------------- export

    def export_dxf(self) -> None:
        doc = self.document
        if doc is None:
            return
        report = doc.last_report.toolpath
        if not report.cuts:
            QtWidgets.QMessageBox.information(
                self.host, "Export Toolpath",
                "There is no toolpath yet. Add a part to the sheet first.")
            return

        force = False
        if report.errors:
            answer = QtWidgets.QMessageBox.warning(
                self.host, "Export Toolpath",
                "%d cut(s) could not be generated:\n\n  %s\n\nExporting now "
                "gives an incomplete nest - parts may not be freed from the "
                "sheet. Export anyway?"
                % (len(report.errors), "\n  ".join(report.errors[:6])),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
            if answer != QtWidgets.QMessageBox.Yes:
                return
            force = True

        suggested = os.path.join(doc.base_dir or self.host.project_folder(),
                                 "%s.dxf" % doc.title)
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self.host, "Export Toolpath as DXF", suggested, DXF_FILTER)
        if not path:
            return
        try:
            written = doc.export_dxf(path, force=force)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self.host, "Export failed",
                                           str(exc))
            return
        self.host.status_message.setStyleSheet("")
        self.host.status_message.setText(
            "Exported %s - %d cut(s), already compensated for the %s. Import "
            "with MyPlasm's own offset switched off."
            % (os.path.basename(written), len(report.cuts),
               doc.tool().label()))

    def regenerate(self) -> None:
        """Re-read every part from disk, keeping the layout as it is."""
        doc = self.document
        if doc is None:
            return
        doc.library.forget()
        report = self.rebuild()
        self.host.status_message.setStyleSheet("")
        self.host.status_message.setText(
            "Regenerated from the part files. %s" % report.message)

    def toggle_paths(self, on: bool) -> None:
        self.show_paths = on
        self.draw(keep_camera=True)

    # --------------------------------------------------------- plumbing

    def _open(self, dialog: QtWidgets.QDialog) -> None:
        self.dialog = dialog
        self.host.position_dialog(dialog)
        dialog.show()
        dialog.raise_()

    def dialog_finished(self, dialog: QtWidgets.QDialog) -> None:
        if self.dialog is dialog:
            self.dialog = None
        self.set_face_picking(None)
        self.viewport.set_selection_mode("solid")
        self.viewport.clear_selection()

    def close_dialogs(self) -> None:
        if self.dialog is not None:
            dialog = self.dialog
            self.dialog = None
            dialog.close()

    def _part_selected(self, part_id: int) -> None:
        doc = self.document
        placed = doc.part(part_id) if doc else None
        if placed is None:
            return
        self.host.status_message.setStyleSheet(
            "color: %s;" % C.error if placed.error else "")
        self.host.status_message.setText(placed.summary())
        self.draw(keep_camera=True)

    def on_selection(self) -> bool:
        if isinstance(self.dialog, CutFaceDialog):
            if self.dialog.on_selection():
                return True
        if self.face_picking is not None:
            return False
        picked = self.viewport.selected_components()
        if picked:
            self.browser.select_parts(picked[:1])
            self._part_selected(picked[0])
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
        doc = self.document
        picked = self.viewport.selected_components()
        if picked and doc is not None:
            placed = doc.part(picked[0])
            if placed is not None:
                title = menu.addAction(icons.icon(placed.icon, 16),
                                       placed.label)
                title.setEnabled(False)
                menu.addSeparator()
                face = menu.addAction(icons.icon("shell", 16), "Cut Face...")
                face.triggered.connect(
                    lambda: self.cut_face_dialog(placed.id))
                move = menu.addAction(icons.icon("move", 16), "Move To...")
                move.triggered.connect(lambda: self.move_dialog(placed.id))
                rotate = menu.addAction(icons.icon("revolve", 16),
                                        "Rotate 90")
                rotate.triggered.connect(
                    lambda: self.rotate_part(placed.id, 90.0))
                mirror = menu.addAction(icons.icon("mirror", 16), "Mirror")
                mirror.setCheckable(True)
                mirror.setChecked(placed.mirror)
                mirror.triggered.connect(
                    lambda: self.toggle_mirror(placed.id))
                delete = menu.addAction(icons.icon("delete", 16), "Delete")
                delete.triggered.connect(lambda: self.delete_part(placed.id))
                menu.addSeparator()

        add = menu.addAction(icons.icon("import", 16), "Add Part...")
        add.triggered.connect(self.add_part)
        arrange = menu.addAction(icons.icon("pattern", 16), "Auto Arrange")
        arrange.setEnabled(bool(doc and doc.parts))
        arrange.triggered.connect(self.auto_arrange)
        stock = menu.addAction(icons.icon("rect", 16), "Sheet and Tool...")
        stock.triggered.connect(self.sheet_dialog)
        menu.addSeparator()
        export = menu.addAction(icons.icon("export", 16), "Export DXF...")
        export.setEnabled(bool(doc and doc.last_report.toolpath.cuts))
        export.triggered.connect(self.export_dxf)
        menu.addSeparator()
        menu.addAction(icons.icon("fit", 16), "Fit All").triggered.connect(
            self.viewport.fit_all)
        menu.addAction(icons.icon("top", 16), "Look Down").triggered.connect(
            lambda: self.viewport.set_view("top"))
