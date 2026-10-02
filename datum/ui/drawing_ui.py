"""The drawing workspace: placing views, dimensioning them, getting output.

The counterpart to ``assembly_ui`` and ``cam_ui``.  The document says what
the drawing is, ``core.views`` works out what it looks like, ``sheetpaint``
draws it, and this decides what happens when a button is pressed.
"""

from __future__ import annotations

import math
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

from PySide6 import QtCore, QtGui, QtPrintSupport, QtWidgets

from ..core import bom, drawing as dwg, fileformat, hlr, kernel
from ..core import templates
from ..core import views as viewgen
from ..core.drawing import (
    Annotation, BASE, DETAIL, DIAMETER, DrawingDocument, LINEAR, PROJECTED,
    RADIUS, SECTION, Sheet, View, WITH_HIDDEN,
)
from . import drawingexport, icons
from .drawing_browser import DrawingBrowser
from .sheetcube import OrientationCube
from .theme import C


class DrawingController(QtCore.QObject):
    """Everything the main window does while a drawing is open."""

    def __init__(self, host) -> None:
        super().__init__(host)
        self.host = host
        self.browser = DrawingBrowser(host)
        self.generator = viewgen.Generator()
        self._properties: Dict[int, Dict[str, str]] = {}
        # views being placed with the cursor, and the model boxes their
        # previews are sized from
        self._placer: Optional["ViewPlacer"] = None
        self._boxes: Dict[str, Any] = {}
        self._wire()

    @property
    def document(self) -> Optional[DrawingDocument]:
        return getattr(self.host, "drawing", None)

    @property
    def canvas(self):
        return self.host.sheet_canvas

    def _wire(self) -> None:
        b = self.browser
        b.sheet_activated.connect(self.activate_sheet)
        b.view_selected.connect(self._view_selected)
        b.view_activated.connect(self.open_model)
        b.context_requested.connect(self._browser_menu)

        canvas = self.canvas
        canvas.changed.connect(self._canvas_changed)
        canvas.selection_changed.connect(self._canvas_selection)
        # a view double-clicked opens its box again, as in Inventor; the
        # model it shows is a right-click away
        canvas.view_activated.connect(self.edit_view)
        canvas.context_requested.connect(self._canvas_menu)
        canvas.placing_finished.connect(self._placing_finished)

    # --------------------------------------------------------------- rebuild

    def rebuild(self, force: bool = False, keep_view: bool = True):
        doc = self.document
        if doc is None:
            return None
        report = self.generator.rebuild(doc, force=force)
        self._refresh_properties()
        self.canvas.model_properties = self._sheet_properties
        self.canvas.set_document(doc, keep_view=keep_view)
        self.browser.set_document(doc)
        self.host.properties.update_from(doc)
        self.host.update_title()

        host = self.host
        host.status_build.setText(report.message)
        if report.missing or report.errors:
            host.status_build.setStyleSheet("color: %s;" % C.error)
        elif report.stale:
            host.status_build.setStyleSheet("color: %s;" % C.warn)
        else:
            host.status_build.setStyleSheet("color: %s;" % C.text_dim)
        if hasattr(host, "drawing_update_button"):
            host.drawing_update_button.setEnabled(bool(report.stale))
        return report

    def _refresh_properties(self) -> None:
        """Resolve {Model.*} once per sheet, from its first base view."""
        doc = self.document
        self._properties = {}
        if doc is None:
            return
        for sheet in doc.sheets:
            base = sheet.base_view()
            if base is None or not base.ref.path:
                continue
            path = base.ref.resolve(doc.base_dir) if doc.base_dir else None
            if path is None:
                continue
            try:
                shape, model = viewgen.load_model(path, self.generator.library)
            except Exception:
                shape, model = None, None
            self._properties[sheet.id] = viewgen.model_properties(
                path, shape, model)

    def _sheet_properties(self, sheet: Sheet) -> Dict[str, str]:
        return self._properties.get(sheet.id, {})

    # ---------------------------------------------------------------- sheets

    def activate_sheet(self, sheet_id: int) -> None:
        doc = self.document
        if doc is None or doc.sheet(sheet_id) is None:
            return
        doc.active_sheet = sheet_id
        self.canvas.set_document(doc, keep_view=False)
        self.browser.refresh()

    def new_sheet(self) -> None:
        doc = self.document
        if doc is None:
            return
        doc.push_undo()
        current = doc.active()
        sheet = doc.add_sheet(current.size if current else "A3",
                              current.orientation if current else dwg.LANDSCAPE)
        doc.active_sheet = sheet.id
        self.rebuild(keep_view=False)
        self.host.status_message.setText("%s added." % sheet.name)

    def delete_sheet(self, sheet_id: Optional[int] = None) -> None:
        doc = self.document
        if doc is None:
            return
        sheet_id = sheet_id or doc.active_sheet
        sheet = doc.sheet(sheet_id)
        if sheet is None:
            return
        if len(doc.sheets) <= 1:
            QtWidgets.QMessageBox.information(
                self.host, "Delete sheet",
                "A drawing keeps at least one sheet.")
            return
        answer = QtWidgets.QMessageBox.question(
            self.host, "Delete sheet",
            "Delete %s and everything on it?" % sheet.name,
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel)
        if answer != QtWidgets.QMessageBox.Yes:
            return
        doc.push_undo()
        doc.remove_sheet(sheet_id)
        self.rebuild(keep_view=False)

    def duplicate_sheet(self, sheet_id: Optional[int] = None) -> None:
        doc = self.document
        if doc is None:
            return
        doc.push_undo()
        copied = doc.duplicate_sheet(sheet_id or doc.active_sheet)
        if copied is not None:
            doc.active_sheet = copied.id
            self.rebuild(force=False, keep_view=False)

    def rename_sheet(self, sheet_id: Optional[int] = None) -> None:
        doc = self.document
        sheet = doc.sheet(sheet_id or doc.active_sheet) if doc else None
        if sheet is None:
            return
        name, ok = QtWidgets.QInputDialog.getText(
            self.host, "Rename sheet", "Name:",
            QtWidgets.QLineEdit.Normal, sheet.name)
        if ok and name.strip():
            doc.push_undo()
            sheet.name = name.strip()
            doc.modified = True
            self.rebuild()

    def sheet_setup(self) -> None:
        doc = self.document
        sheet = doc.active() if doc else None
        if sheet is None:
            return
        dialog = SheetDialog(self.host, doc, sheet)
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            doc.push_undo()
            dialog.apply()
            self.rebuild(keep_view=False)

    # ----------------------------------------------------------------- views

    def place_base_view(self) -> None:
        """Inventor's Base View: set the view up on the sheet, then OK.

        The Drawing View box offers the parts and assemblies open in tabs,
        the last one worked on first, or any file from a folder, with its
        style, scale and name.  The view is on the sheet straight away,
        drawn, with a view cube beside it to point it by.  It can be
        dragged anywhere, and a click beside it, above or below it, or off
        a corner adds a projection, which can be dragged too.  Nothing is
        made until OK; Cancel or Esc leaves the sheet as it was.
        """
        doc = self.document
        sheet = doc.active() if doc else None
        if sheet is None:
            return
        self.end_placement()
        if not doc.path:
            QtWidgets.QMessageBox.information(
                self.host, "Save first",
                "Save the drawing before placing a view, so its reference to "
                "the model can be stored relative to it.")
            if not self.host.save_document():
                return
        dialog = BaseViewDialog(self.host, self, doc, sheet)
        self._start_placer(BaseViewSession(self, sheet, dialog))
        dialog.move(self.canvas.mapToGlobal(QtCore.QPoint(16, 16)))
        dialog.show()
        self.host.status_message.setStyleSheet("")
        self.host.status_message.setText(
            "Point the view with the cube and drag it where it goes. Click "
            "round it for projections, then OK.")

    def edit_view(self, view_id: int) -> None:
        """A view double-clicked: its box again, to change it.

        A base view gets the Drawing View box and the cube, as when it was
        placed, with its projections there to drag; any other view gets
        its own smaller box, its scale the base view's unless it is given
        one of its own.
        """
        doc, sheet, view = self._selected_view(view_id)
        if view is None:
            return
        if view.kind != BASE:
            self.view_properties(view.id)
            return
        self.end_placement()
        dialog = BaseViewDialog(self.host, self, doc, sheet, editing=view)
        self._start_placer(BaseViewSession(self, sheet, dialog,
                                           editing=view))
        dialog.move(self.canvas.mapToGlobal(QtCore.QPoint(16, 16)))
        dialog.show()
        self.host.status_message.setStyleSheet("")
        self.host.status_message.setText(
            "Change %s with the box and the cube, drag it or its "
            "projections, then OK." % view.name)

    def apply_view_edit(self, view: View, settings: Dict[str, Any],
                        direction, up, x: float, y: float,
                        children: Sequence[Sequence[float]],
                        child_ids: Sequence[int]) -> None:
        """OK on a base view being edited: change it, and its projections."""
        doc = self.document
        sheet = doc.active() if doc else None
        if sheet is None:
            return
        doc.push_undo()
        path = settings.get("path") or ""
        model = doc.view_model(sheet, view)
        current = (model.ref.resolve(doc.base_dir)
                   if model is not None and model.ref.path else None)
        if path and (current is None or os.path.normcase(
                os.path.abspath(path)) != os.path.normcase(
                os.path.abspath(current))):
            view.ref = fileformat.ComponentRef(
                path=fileformat.relative_path(path, doc.base_dir),
                name=os.path.basename(path),
                label=os.path.splitext(os.path.basename(path))[0])
        view.display = settings.get("display") or WITH_HIDDEN
        view.scale = float(settings.get("scale") or view.scale)
        view.name = settings.get("name") or view.name
        view.direction = [float(c) for c in direction]
        view.up = [float(c) for c in up]
        view.orientation = viewgen.named_orientation(direction) or "front"

        shift = (x - view.x, y - view.y)
        view.x, view.y = x, y
        kept = {i for i in child_ids if i}
        for child in list(sheet.children_of(view.id)):
            if child.kind == PROJECTED and child.id not in kept:
                # dropped in the box, with whatever came from it
                doc.remove_view(sheet, child.id, children=True)
        moved_with_own = set()
        made = set()
        for (cx, cy), cid in zip(children, child_ids):
            if cid:
                child = sheet.view(cid)
                if child is None:
                    continue
                step = (cx - child.x, cy - child.y)
                child.x, child.y = cx, cy
                for below in _descendants(sheet, child.id):
                    below.x += step[0]
                    below.y += step[1]
                    moved_with_own.add(below.id)
                moved_with_own.add(child.id)
            else:
                diagonal = abs(cx - x) > 1e-6 and abs(cy - y) > 1e-6
                made.add(doc.add_view(sheet, View(
                    kind=PROJECTED, parent=view.id, x=float(cx),
                    y=float(cy), display="visible" if diagonal else "")).id)
        # sections and details of it, and anything else not in the box,
        # go where it went
        for below in _descendants(sheet, view.id):
            if below.id not in moved_with_own and below.id not in made:
                below.x += shift[0]
                below.y += shift[1]
        # everything that comes from it is drawn again, and nothing else
        for again in [view] + _descendants(sheet, view.id):
            again.projection = None
        doc.modified = True
        self.rebuild()

    def add_projected(self) -> None:
        """Projections of the selected view, placed with the cursor."""
        doc, sheet, view = self._selected_view()
        if view is None:
            self._complain("Select a view first, then add a projection of it.")
            return
        self.end_placement()
        self._start_placer(ViewPlacer(self, sheet, parent=view.id))
        self.host.status_message.setStyleSheet("")
        self.host.status_message.setText(
            "Click beside, above or below %s for a projection, off a corner "
            "for an isometric. Right-click or Esc when done." % view.label)

    # -- placing views with the cursor ---------------------------------------

    def _start_placer(self, placer) -> None:
        self._placer = placer
        self.canvas.set_placer(placer)
        self.canvas.setFocus()

    def _placing_finished(self) -> None:
        placer = self._placer
        if isinstance(placer, BaseViewSession):
            # Esc in the middle of setting a base view up is Cancel
            placer.cancel()
            self.host.status_message.setStyleSheet("")
            self.host.status_message.setText("Base view cancelled.")
            return
        placed = len(placer.placed) if placer else 0
        self.end_placement()
        self.host.status_message.setStyleSheet("")
        self.host.status_message.setText(
            "Placed %d view(s)." % placed if placed else "No view placed.")

    def end_placement(self) -> None:
        """Stop placing views, keeping whatever has been made."""
        placer = getattr(self, "_placer", None)
        self._placer = None
        if placer is not None:
            placer.close()
        if self.canvas.placer is not None:
            self.canvas.set_placer(None)

    def open_models(self) -> List[Any]:
        """The parts and assemblies open in tabs, the last one used first.

        Only saved ones: a view refers to its model by file.
        """
        from ..core import fileformat as ff

        entries = [e for e in self.host.session
                   if e.doc_type in (ff.PART, ff.ASSEMBLY) and e.path]
        last = getattr(self.host, "last_model_entry", None)
        if last in entries:
            entries.remove(last)
            entries.insert(0, last)
        return entries

    def model_box(self, path: str):
        """A model's bounding box, worked out once per file."""
        boxes = self._boxes
        stamp = os.path.getmtime(path) if os.path.exists(path) else 0.0
        held = boxes.get(path)
        if held is not None and held[0] == stamp:
            return held[1]
        box = None
        try:
            shape, _model = viewgen.load_model(path, self.generator.library)
            if shape is not None:
                box = tuple(kernel.bounding_box(shape))
        except Exception:
            box = None
        boxes[path] = (stamp, box)
        return box

    def fit_scale(self, path: str, sheet: Sheet) -> float:
        """The standard scale a front view of this model and two
        projections of it fit the sheet at, the way Inventor picks one."""
        box = self.model_box(path)
        if box is None:
            return 1.0
        direction, up = hlr.ORIENTATIONS["front"]
        across, upward = viewgen.outline(box, direction, up)
        depth = viewgen.outline(box, up, direction)[1]
        doc = self.document
        border = doc.borders.get(sheet.border) if doc else None
        frame = (border.frame(*sheet.extent()) if border
                 else (10.0, 10.0) + sheet.extent())
        block = doc.title_blocks.get(sheet.title_block) if doc else None
        reserve = (block.width, block.height) if block else (0.0, 0.0)
        return dwg.three_view_layout(frame, (across, depth, upward),
                                     reserve=reserve)["scale"]

    def create_views(self, settings: Dict[str, Any], direction, up,
                     x: float, y: float,
                     children: Sequence[Sequence[float]] = ()
                     ) -> Optional[View]:
        """The base view as it was set up, and its projections, made."""
        doc = self.document
        sheet = doc.active() if doc else None
        path = settings.get("path") or ""
        if sheet is None or not path:
            return None
        doc.push_undo()
        base = View(kind=BASE,
                    orientation=viewgen.named_orientation(direction)
                    or "front",
                    direction=[float(c) for c in direction],
                    up=[float(c) for c in up],
                    scale=float(settings.get("scale") or 1.0), x=x, y=y,
                    display=settings.get("display") or WITH_HIDDEN,
                    name=settings.get("name", ""))
        base.ref = fileformat.ComponentRef(
            path=fileformat.relative_path(path, doc.base_dir),
            name=os.path.basename(path),
            label=os.path.splitext(os.path.basename(path))[0])
        doc.add_view(sheet, base)
        for cx, cy in children:
            diagonal = abs(cx - x) > 1e-6 and abs(cy - y) > 1e-6
            doc.add_view(sheet, View(
                kind=PROJECTED, parent=base.id, x=float(cx), y=float(cy),
                # an isometric with its hidden lines is a tangle
                display="visible" if diagonal else ""))
        self.rebuild()
        self.host.status_message.setStyleSheet("")
        self.host.status_message.setText(
            "%s placed%s." % (base.name, " with %d projection(s)"
                              % len(children) if children else ""))
        return base

    def create_projected(self, parent_id: int, x: float,
                         y: float) -> Optional[View]:
        """A projection of a view, here: its direction is where it sits."""
        doc = self.document
        sheet = doc.active() if doc else None
        parent = sheet.view(parent_id) if sheet else None
        if parent is None:
            return None
        doc.push_undo()
        diagonal = abs(x - parent.x) > 1e-6 and abs(y - parent.y) > 1e-6
        child = View(kind=PROJECTED, parent=parent.id, x=x, y=y,
                     # an isometric with its hidden lines is a tangle
                     display="visible" if diagonal else "")
        doc.add_view(sheet, child)
        self.rebuild()
        return child

    def add_section(self) -> None:
        """Cut the selected view across its middle and look at the cut."""
        doc, sheet, view = self._selected_view()
        if view is None:
            self._complain("Select the view to cut, then add a section.")
            return
        box = view.projection.box if view.projection else (-20, -20, 20, 20)
        doc.push_undo()
        letter = doc.next_letter()
        section = View(kind=SECTION, parent=view.id, letter=letter,
                       cut=[box[0] - 4.0, 0.0, box[2] + 4.0, 0.0],
                       x=view.x, y=view.y - (box[3] - box[1]) - 30.0)
        doc.add_view(sheet, section)
        report = self.rebuild()
        if section.error:
            self._complain("That cut did not work: %s" % section.error)
        else:
            self.canvas.select([section.id])
            self.host.status_message.setText(
                "Section %s-%s added. Drag its cut line on the parent."
                % (letter, letter))

    def add_detail(self) -> None:
        doc, sheet, view = self._selected_view()
        if view is None:
            self._complain("Select a view first, then add a detail of it.")
            return
        box = view.projection.box if view.projection else (-20, -20, 20, 20)
        doc.push_undo()
        letter = doc.next_letter()
        radius = max(6.0, min(box[2] - box[0], box[3] - box[1]) * 0.22)
        detail = View(kind=DETAIL, parent=view.id, letter=letter,
                      centre=[(box[0] + box[2]) / 2.0,
                              (box[1] + box[3]) / 2.0],
                      radius=radius,
                      scale=doc.view_scale(sheet, view) * 2.0,
                      x=view.x + (box[2] - box[0]) / 2.0 + radius * 2.0 + 30.0,
                      y=view.y)
        doc.add_view(sheet, detail)
        self.rebuild()
        if detail.error:
            self._complain("That detail did not work: %s" % detail.error)
        else:
            self.canvas.select([detail.id])

    def delete_view(self, view_id: Optional[int] = None) -> None:
        doc, sheet, view = self._selected_view(view_id)
        if view is None:
            return
        children = sheet.children_of(view.id)
        take_children = True
        if children:
            answer = QtWidgets.QMessageBox.question(
                self.host, "Delete view",
                "%s has %d view(s) derived from it.\n\nDelete those as well?"
                % (view.label, len(children)),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
                | QtWidgets.QMessageBox.Cancel)
            if answer == QtWidgets.QMessageBox.Cancel:
                return
            take_children = answer == QtWidgets.QMessageBox.Yes
        doc.push_undo()
        gone = doc.remove_view(sheet, view.id, children=take_children)
        self.rebuild()
        self.host.status_message.setText("Removed %d view(s)." % len(gone))

    def view_properties(self, view_id: Optional[int] = None) -> None:
        doc, sheet, view = self._selected_view(view_id)
        if view is None:
            return
        dialog = ViewDialog(self.host, doc, sheet, view)
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            doc.push_undo()
            dialog.apply()
            # drawn again: the view and what comes from it, nothing else
            for again in [view] + _descendants(sheet, view.id):
                again.projection = None
            self.rebuild()

    def open_model(self, view_id: int) -> None:
        """Open the part or assembly a view is drawn from."""
        doc, sheet, view = self._selected_view(view_id)
        if view is None:
            return
        origin = doc.view_model(sheet, view)
        if origin is None or not origin.ref.path:
            return
        path = origin.ref.resolve(doc.base_dir)
        if path is None:
            self._complain("%s cannot be found." % origin.ref.name)
            return
        self.host.open_path(path)

    # ----------------------------------------------------------- dimensions

    def add_dimension(self, kind: str = LINEAR) -> None:
        """Dimension the selected view's extent, as a starting point."""
        doc, sheet, view = self._selected_view()
        if view is None or view.projection is None:
            self._complain("Select a view that has been drawn.")
            return
        box = view.projection.box
        doc.push_undo()
        if kind in (RADIUS, DIAMETER):
            radius = min(box[2] - box[0], box[3] - box[1]) / 4.0
            note = Annotation(kind=kind, view=view.id,
                              points=[[0.0, 0.0], [radius, 0.0]],
                              offset=[radius, radius])
        else:
            note = Annotation(kind=kind, view=view.id,
                              points=[[box[0], box[1]], [box[2], box[1]]],
                              offset=[0.0, -14.0])
            self._anchor(doc, sheet, view, note)
        doc.add_annotation(sheet, note)
        self.rebuild()
        self.canvas.select([], [note.id])
        self.host.status_message.setText(
            "%s dimension added - drag it to place it."
            % dwg.ANNOTATION_LABELS.get(kind, "Linear"))

    def _anchor(self, doc, sheet, view, note) -> None:
        """Snap a dimension's ends to model vertices, and remember which.

        An anchored dimension is a statement about the part: change the part
        and it follows and keeps reading the truth.  One that finds nothing
        to snap to still works, it is just a note on the paper - so a miss
        is not an error, it only means less.
        """
        shape = self.generator.model_of(doc, sheet, view)
        if shape is None:
            return
        try:
            direction, up = self.generator.orientation(doc, sheet, view)
        except Exception:
            return
        scale = doc.view_scale(sheet, view)
        middle = view.projection.centre if view.projection else (0.0, 0.0)
        reach = max(4.0, min(view.projection.width, view.projection.height)
                    * 0.25) if view.projection else 6.0

        points, anchors = [], []
        for point in note.points:
            moved, ref = viewgen.snap(shape, direction, up, point, middle,
                                      scale, reach)
            points.append(moved)
            anchors.append(ref)
        if any(a is not None for a in anchors):
            note.points = points
            note.anchors = anchors

    def add_note(self) -> None:
        doc, sheet, view = self._selected_view()
        if view is None:
            self._complain("Select a view to attach the text to.")
            return
        text, ok = QtWidgets.QInputDialog.getText(
            self.host, "Text", "Text:", QtWidgets.QLineEdit.Normal, "")
        if not ok or not text.strip():
            return
        doc.push_undo()
        note = Annotation(kind=dwg.NOTE, view=view.id, points=[[0.0, 0.0]],
                          offset=[10.0, 10.0], text=text.strip())
        doc.add_annotation(sheet, note)
        self.rebuild()

    def add_centre_mark(self) -> None:
        doc, sheet, view = self._selected_view()
        if view is None:
            return
        doc.push_undo()
        note = Annotation(kind=dwg.CENTRE_MARK, view=view.id,
                          points=[[0.0, 0.0]], value=6.0)
        doc.add_annotation(sheet, note)
        self.rebuild()

    # -------------------------------------------------- balloons and lists

    def add_balloon(self) -> None:
        """One balloon on the selected view, for the part under the middle.

        A single balloon is placed rather than asked for, because picking a
        part on a drawing means picking one of its lines, and the line an
        assembly shows is shared by whichever component happens to be behind
        it.  Drag it where it belongs; Auto Balloon does the whole set.
        """
        doc, sheet, view = self._selected_view()
        if view is None or view.projection is None:
            self._complain("Select a view that has been drawn.")
            return
        targets = self.generator.balloon_targets(doc, sheet, view)
        if not targets:
            self._complain("%s is not drawn from an assembly, so there is "
                           "nothing to balloon." % view.label)
            return
        doc.push_undo()
        target = targets[0]
        seat = (target[1][0] + 14.0, target[1][1] + 14.0)
        point = viewgen.nearest_corner(target, seat)
        note = Annotation(kind=dwg.BALLOON, view=view.id,
                          points=[[point[0], point[1]]],
                          offset=[seat[0] - point[0], seat[1] - point[1]],
                          component=target[0])
        doc.add_annotation(sheet, note)
        self.rebuild()
        self.canvas.select([], [note.id])
        self.host.status_message.setText(
            "Balloon added - drag it to place it.")

    def auto_balloon(self) -> None:
        """A balloon on every component of the selected view's assembly.

        The bubbles are pushed out from the middle of the view and then
        spread around it, so they land clear of the drawing rather than on
        top of the thing they are pointing at.
        """
        doc, sheet, view = self._selected_view()
        if view is None or view.projection is None:
            self._complain("Select a view that has been drawn.")
            return
        targets = self.generator.balloon_targets(doc, sheet, view)
        if not targets:
            self._complain("%s is not drawn from an assembly, so there is "
                           "nothing to balloon." % view.label)
            return

        doc.push_undo()
        existing = {a.component for a in sheet.annotations_for(view.id)
                    if a.kind == dwg.BALLOON}
        box = view.projection.box
        cx = (box[0] + box[2]) / 2.0
        cy = (box[1] + box[3]) / 2.0
        ring = max(box[2] - box[0], box[3] - box[1]) * 0.62 + 16.0

        added = 0
        for index, target in enumerate(targets):
            path = target[0]
            if path in existing:
                continue
            # spread them evenly round the view, starting at the top left,
            # which is where a drawing usually has room
            angle = math.pi * 0.75 - 2.0 * math.pi * index / max(
                len(targets), 1)
            seat = (cx + math.cos(angle) * ring, cy + math.sin(angle) * ring)
            point = viewgen.nearest_corner(target, seat)
            note = Annotation(
                kind=dwg.BALLOON, view=view.id,
                points=[[point[0], point[1]]],
                offset=[seat[0] - point[0], seat[1] - point[1]],
                component=path)
            doc.add_annotation(sheet, note)
            added += 1
        self.rebuild()
        self.host.status_message.setText(
            "%d balloon(s) added." % added if added
            else "Every component already has a balloon.")

    def add_parts_list(self) -> None:
        """A parts list for the sheet's assembly, above the title block."""
        doc = self.document
        sheet = doc.active() if doc else None
        if sheet is None:
            return
        view = sheet.view(self.canvas.selected_views[-1]) \
            if self.canvas.selected_views else sheet.base_view()
        if view is None:
            self._complain("The sheet has no view to list.")
            return

        doc.push_undo()
        table = dwg.PartsList(view=doc.view_model(sheet, view).id
                              if doc.view_model(sheet, view) else view.id)
        width, height = sheet.extent()
        border = doc.borders.get(sheet.border)
        block = doc.title_blocks.get(sheet.title_block)
        if border is not None:
            frame = border.frame(width, height)
            table.x, table.y = frame[2], frame[1]
            if block is not None:
                table.y += block.height
        else:
            table.x, table.y = width - 10.0, 10.0
        doc.add_parts_list(sheet, table)
        self.rebuild(force=True)
        if table.error:
            self._complain("The parts list is empty: %s" % table.error)
        else:
            self.canvas.select([], [table.id])
            self.host.status_message.setText(
                "Parts list added - %d item(s)." % len(table.rows))

    def parts_list_properties(self, list_id: int) -> None:
        doc = self.document
        sheet = doc.active() if doc else None
        table = sheet.parts_list(list_id) if sheet else None
        if table is None:
            return
        dialog = PartsListDialog(self.host, table)
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            doc.push_undo()
            dialog.apply()
            self.rebuild(force=True)

    def delete_parts_list(self, list_id: int) -> None:
        doc = self.document
        sheet = doc.active() if doc else None
        if sheet is None or sheet.parts_list(list_id) is None:
            return
        doc.push_undo()
        doc.remove_parts_list(sheet, list_id)
        self.rebuild()

    def delete_annotation(self, note_id: int) -> None:
        doc = self.document
        sheet = doc.active() if doc else None
        if sheet is None:
            return
        doc.push_undo()
        doc.remove_annotation(sheet, note_id)
        self.rebuild()

    def edit_annotation(self, note_id: int) -> None:
        doc = self.document
        sheet = doc.active() if doc else None
        note = next((a for a in sheet.annotations if a.id == note_id),
                    None) if sheet else None
        if note is None:
            return
        view = sheet.view(note.view)
        current = note.text or note.caption(doc.view_scale(sheet, view)
                                            if view else 1.0)
        text, ok = QtWidgets.QInputDialog.getText(
            self.host, "Dimension text",
            "Text (leave empty to use the measured value):",
            QtWidgets.QLineEdit.Normal, note.text)
        if not ok:
            return
        doc.push_undo()
        note.text = text.strip()
        doc.modified = True
        self.rebuild()

    # --------------------------------------------------------------- output

    def export_pdf(self, all_sheets: bool = True) -> None:
        doc = self.document
        if doc is None or not doc.sheets:
            return
        suggested = os.path.join(doc.base_dir or self.host.project_folder(),
                                 "%s.pdf" % doc.title)
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self.host, "Export PDF", suggested, drawingexport.PDF_FILTER)
        if not path:
            return
        sheets = None if all_sheets else [doc.active()]
        try:
            written = drawingexport.to_pdf(doc, path, sheets,
                                           self._sheet_properties)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self.host, "Export failed",
                                           str(exc))
            return
        self.host.status_message.setStyleSheet("")
        self.host.status_message.setText("Exported %s"
                                         % os.path.basename(written))

    def export_svg(self) -> None:
        doc = self.document
        sheet = doc.active() if doc else None
        if sheet is None:
            return
        suggested = os.path.join(doc.base_dir or self.host.project_folder(),
                                 "%s.svg" % doc.title)
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self.host, "Export SVG", suggested, drawingexport.SVG_FILTER)
        if not path:
            return
        try:
            written = drawingexport.to_svg(doc, sheet, path,
                                           self._sheet_properties)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self.host, "Export failed", str(exc))
            return
        self.host.status_message.setText("Exported %s"
                                         % os.path.basename(written))

    def export_dxf(self) -> None:
        from ..core.dxf import DXF_FILTER

        doc = self.document
        sheet = doc.active() if doc else None
        if sheet is None:
            return
        suggested = os.path.join(doc.base_dir or self.host.project_folder(),
                                 "%s.dxf" % doc.title)
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self.host, "Export DXF", suggested, DXF_FILTER)
        if not path:
            return
        try:
            written = drawingexport.to_dxf(doc, sheet, path,
                                           self._sheet_properties)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self.host, "Export failed", str(exc))
            return
        self.host.status_message.setText("Exported %s"
                                         % os.path.basename(written))

    def print_drawing(self) -> None:
        doc = self.document
        if doc is None or not doc.sheets:
            return
        printer = QtPrintSupport.QPrinter(QtPrintSupport.QPrinter.HighResolution)
        sheet = doc.active()
        width, height = sheet.extent()
        printer.setPageSize(QtGui.QPageSize(
            QtCore.QSizeF(width, height), QtGui.QPageSize.Millimeter, "Sheet",
            QtGui.QPageSize.ExactMatch))
        printer.setPageMargins(QtCore.QMarginsF(0, 0, 0, 0),
                               QtGui.QPageLayout.Millimeter)
        dialog = QtPrintSupport.QPrintDialog(printer, self.host)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        drawingexport.to_printer(doc, printer, None, self._sheet_properties)
        self.host.status_message.setText("Sent to %s" % printer.printerName())

    def thumbnail(self) -> Optional[bytes]:
        """A picture of the first sheet, for the file and the recent list."""
        doc = self.document
        if doc is None or not doc.sheets:
            return None
        try:
            pixmap = self.canvas.grab()
            if pixmap.isNull():
                return None
            scaled = pixmap.scaled(fileformat.THUMBNAIL_SIZE,
                                   fileformat.THUMBNAIL_SIZE,
                                   QtCore.Qt.KeepAspectRatio,
                                   QtCore.Qt.SmoothTransformation)
            buffer = QtCore.QBuffer()
            buffer.open(QtCore.QIODevice.WriteOnly)
            scaled.save(buffer, "PNG")
            return bytes(buffer.data())
        except Exception:
            return None

    # -------------------------------------------------------------- helpers

    def _selected_view(self, view_id: Optional[int] = None):
        doc = self.document
        sheet = doc.active() if doc else None
        if sheet is None:
            return (doc, None, None)
        if view_id is None:
            chosen = (self.canvas.selected_views[-1]
                      if self.canvas.selected_views else None)
            if chosen is None:
                kind, ident = self.browser.selected_kind()
                chosen = int(ident) if kind == "view" and ident else None
            view_id = chosen
        return (doc, sheet, sheet.view(view_id) if view_id else None)

    def _complain(self, message: str) -> None:
        self.host.status_message.setStyleSheet("color: %s;" % C.warn)
        self.host.status_message.setText(message)

    def _canvas_changed(self) -> None:
        """Something on the sheet was dragged: draw again only what turned.

        Moving a view used to rebuild the whole drawing, every view of
        every sheet drawn afresh, twice over, after every drag; a view slid
        along its line has not changed what it shows, and should just be
        where it was put.  The one thing a move can change is which way a
        projected view looks, when it is dragged across to the other side
        of its parent, and only those views, and what comes from them, are
        drawn again.
        """
        doc = self.document
        sheet = doc.active() if doc else None
        if sheet is None:
            return
        doc.modified = True
        turned = self.generator.turned(doc, sheet)
        if turned:
            self.generator.redraw(doc, sheet, turned)
        self.canvas.update()
        self.host.update_title()

    def _canvas_selection(self) -> None:
        if self.canvas.selected_views:
            self.browser.select_view(self.canvas.selected_views[-1])

    def _view_selected(self, view_id: int) -> None:
        self.canvas.select([view_id])

    def _canvas_menu(self, pos: QtCore.QPoint) -> None:
        menu = QtWidgets.QMenu(self.host)
        self.context_menu(menu)
        menu.exec(self.canvas.mapToGlobal(pos))

    def _browser_menu(self, pos: QtCore.QPoint) -> None:
        menu = QtWidgets.QMenu(self.host)
        kind, ident = self.browser.selected_kind()
        if kind == "sheet":
            menu.addAction("Activate",
                           lambda: self.activate_sheet(int(ident)))
            menu.addAction("Rename...", lambda: self.rename_sheet(int(ident)))
            menu.addAction("Duplicate", lambda: self.duplicate_sheet(int(ident)))
            menu.addAction("Sheet Setup...", self.sheet_setup)
            menu.addSeparator()
            menu.addAction("Delete Sheet", lambda: self.delete_sheet(int(ident)))
        elif kind == "view":
            menu.addAction("Properties...",
                           lambda: self.view_properties(int(ident)))
            menu.addAction("Open Model", lambda: self.open_model(int(ident)))
            menu.addSeparator()
            menu.addAction("Delete View", lambda: self.delete_view(int(ident)))
        elif kind == "note":
            menu.addAction("Edit Text...",
                           lambda: self.edit_annotation(int(ident)))
            menu.addAction("Delete",
                           lambda: self.delete_annotation(int(ident)))
        elif kind == "parts_list":
            menu.addAction("Properties...",
                           lambda: self.parts_list_properties(int(ident)))
            menu.addAction("Delete",
                           lambda: self.delete_parts_list(int(ident)))
        else:
            self.context_menu(menu)
        menu.exec(self.browser.mapToGlobal(pos))

    def context_menu(self, menu: QtWidgets.QMenu) -> None:
        """What the sheet offers on a right-click."""
        doc, sheet, view = self._selected_view()
        notes = self.canvas.selected_annotations
        table = sheet.parts_list(notes[-1]) if (notes and sheet) else None
        if table is not None:
            menu.addAction("Parts List Properties...",
                           lambda: self.parts_list_properties(table.id))
            menu.addAction("Delete Parts List",
                           lambda: self.delete_parts_list(table.id))
            menu.addSeparator()
        elif notes:
            menu.addAction("Edit Text...",
                           lambda: self.edit_annotation(notes[-1]))
            menu.addAction("Delete Dimension",
                           lambda: self.delete_annotation(notes[-1]))
            menu.addSeparator()
        if view is not None:
            menu.addAction("Projected View", self.add_projected)
            menu.addAction("Section View", self.add_section)
            menu.addAction("Detail View", self.add_detail)
            menu.addSeparator()
            menu.addAction("Dimension", lambda: self.add_dimension(LINEAR))
            menu.addAction("Centre Mark", self.add_centre_mark)
            menu.addAction("Text...", self.add_note)
            menu.addSeparator()
            menu.addAction("Balloon", self.add_balloon)
            menu.addAction("Auto Balloon", self.auto_balloon)
            menu.addAction("Parts List", self.add_parts_list)
            menu.addSeparator()
            menu.addAction("View Properties...", self.view_properties)
            menu.addAction("Open Model", lambda: self.open_model(view.id))
            menu.addAction("Delete View", self.delete_view)
            menu.addSeparator()
        menu.addAction("Place Base View...", self.place_base_view)
        menu.addAction("Sheet Setup...", self.sheet_setup)
        menu.addAction("Fit", self.canvas.fit)

    def on_escape(self) -> bool:
        if self._placer is not None:
            self.end_placement()
            return True
        if self.canvas.selected_views or self.canvas.selected_annotations:
            self.canvas.select([], [])
            return True
        return False

    def delete_selected(self) -> bool:
        if self.canvas.selected_annotations:
            # the canvas keeps parts lists in the same selection as
            # annotations, so work out which one this id belongs to
            chosen = self.canvas.selected_annotations[-1]
            sheet = self.document.active() if self.document else None
            if sheet is not None and sheet.parts_list(chosen) is not None:
                self.delete_parts_list(chosen)
            else:
                self.delete_annotation(chosen)
            return True
        if self.canvas.selected_views:
            self.delete_view(self.canvas.selected_views[-1])
            return True
        return False


    # ------------------------------------------------------------ templates

    def save_as_template(self) -> None:
        """Keep this drawing as a starting point for the next one."""
        doc = self.document
        if doc is None:
            return
        folder = self.host.project_folder()
        if not folder:
            self._complain("There is no project to save the template into.")
            return
        name, ok = QtWidgets.QInputDialog.getText(
            self.host, "Save as Template", "Template name:",
            QtWidgets.QLineEdit.Normal,
            doc.properties.get("Title") or doc.title)
        if not ok:
            return
        try:
            written = templates.save_as_template(doc, name, folder)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self.host, "Save as Template",
                                          str(exc))
            return
        self.host.status_message.setStyleSheet("")
        self.host.status_message.setText(
            "Template saved as %s" % os.path.basename(written))


class PartsListDialog(QtWidgets.QDialog):
    """Which columns a parts list shows, and how deep it counts."""

    def __init__(self, parent, table) -> None:
        super().__init__(parent)
        self.table = table
        self.setWindowTitle("Parts List")
        self.setMinimumWidth(340)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(9)

        form = QtWidgets.QFormLayout()
        self.corner = QtWidgets.QComboBox()
        for key, label in (("bottom-right", "Bottom right"),
                           ("bottom-left", "Bottom left"),
                           ("top-right", "Top right"),
                           ("top-left", "Top left")):
            self.corner.addItem(label, key)
        self.corner.setCurrentIndex(max(0, self.corner.findData(table.corner)))
        form.addRow("Placed by its", self.corner)

        self.row_height = QtWidgets.QDoubleSpinBox()
        self.row_height.setRange(4.0, 25.0)
        self.row_height.setSuffix(" mm")
        self.row_height.setValue(table.row_height)
        form.addRow("Row height", self.row_height)

        self.text_height = QtWidgets.QDoubleSpinBox()
        self.text_height.setRange(1.5, 10.0)
        self.text_height.setSuffix(" mm")
        self.text_height.setValue(table.text_height)
        form.addRow("Text height", self.text_height)
        root.addLayout(form)

        self.heading = QtWidgets.QCheckBox("Show the column headings")
        self.heading.setChecked(table.heading)
        root.addWidget(self.heading)
        self.recurse = QtWidgets.QCheckBox(
            "Count the parts inside sub-assemblies")
        self.recurse.setToolTip(
            "Off, a sub-assembly is one line; on, its parts are listed "
            "instead of it")
        self.recurse.setChecked(table.recurse)
        root.addWidget(self.recurse)

        root.addWidget(QtWidgets.QLabel("Columns"))
        self.columns = QtWidgets.QListWidget()
        self.columns.setMaximumHeight(150)
        # then every custom property the listed parts carry, so a Vendor
        # typed into a part's Properties can become a column here
        choices = list(bom.COLUMNS.items())
        names = bom.property_names(table.rows)
        names += [c[len(bom.PROP):] for c in table.columns
                  if c.startswith(bom.PROP)
                  and c[len(bom.PROP):] not in names]
        for name in names:
            key = bom.PROP + name
            if any(key == k for k, _ in choices):
                continue
            # the fixed ones already have a column of their own
            if name in ("PartNumber", "Description", "Part Number"):
                continue
            choices.append((key, bom.heading(key)))
        for key, label in choices:
            item = QtWidgets.QListWidgetItem(label)
            item.setData(QtCore.Qt.UserRole, key)
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.Checked if key in table.columns
                               else QtCore.Qt.Unchecked)
            self.columns.addItem(item)
        root.addWidget(self.columns)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def apply(self) -> None:
        table = self.table
        table.corner = self.corner.currentData() or table.corner
        table.row_height = self.row_height.value()
        table.text_height = self.text_height.value()
        table.heading = self.heading.isChecked()
        table.recurse = self.recurse.isChecked()
        chosen = []
        for i in range(self.columns.count()):
            item = self.columns.item(i)
            if item.checkState() == QtCore.Qt.Checked:
                chosen.append(item.data(QtCore.Qt.UserRole))
        # a table with no columns is a rectangle, which helps nobody
        table.columns = chosen or list(bom.DEFAULT_COLUMNS)


class TemplateDialog(QtWidgets.QDialog):
    """Which template a new drawing starts from."""

    def __init__(self, parent, project_dir: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("New Drawing")
        self.setMinimumWidth(380)
        self.project_dir = project_dir

        layout = QtWidgets.QVBoxLayout(self)
        note = QtWidgets.QLabel(
            "A template is an ordinary drawing kept in the project's "
            "Templates folder. Starting from one copies its sheets, border, "
            "title block and styles.")
        note.setWordWrap(True)
        note.setProperty("hint", True)
        layout.addWidget(note)

        self.list = QtWidgets.QListWidget()
        self.list.itemDoubleClicked.connect(lambda _i: self.accept())
        layout.addWidget(self.list, 1)

        templates.ensure_builtins()
        for template in templates.available(project_dir):
            item = QtWidgets.QListWidgetItem(template.label)
            item.setData(QtCore.Qt.UserRole, template.path)
            item.setIcon(icons.icon("dimension", 20))
            self.list.addItem(item)
        blank = QtWidgets.QListWidgetItem("Blank A3")
        blank.setData(QtCore.Qt.UserRole, "")
        self.list.insertItem(0, blank)
        # A3 is the sheet most things get drawn on, so it is the one that
        # should already be chosen rather than whatever sorts first
        self.list.setCurrentRow(1 if self.list.count() > 1 else 0)
        for row in range(self.list.count()):
            if self.list.item(row).text().startswith("ISO A3"):
                self.list.setCurrentRow(row)
                break

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def chosen(self):
        item = self.list.currentItem()
        path = item.data(QtCore.Qt.UserRole) if item else ""
        if not path:
            return None
        return next((t for t in templates.available(self.project_dir)
                     if t.path == path), None)


# ------------------------------------------------------------------ dialogs


class SheetDialog(QtWidgets.QDialog):
    """Size, orientation, border and title block for one sheet."""

    def __init__(self, parent, doc: DrawingDocument, sheet: Sheet) -> None:
        super().__init__(parent)
        self.doc = doc
        self.sheet = sheet
        self.setWindowTitle("Sheet Setup")
        self.setMinimumWidth(340)

        form = QtWidgets.QFormLayout(self)
        self.name = QtWidgets.QLineEdit(sheet.name)
        form.addRow("Name", self.name)

        self.size = QtWidgets.QComboBox()
        self.size.addItems(list(dwg.SHEET_ORDER))
        if sheet.size in dwg.SHEET_ORDER:
            self.size.setCurrentText(sheet.size)
        form.addRow("Size", self.size)

        self.orientation = QtWidgets.QComboBox()
        self.orientation.addItems(["Landscape", "Portrait"])
        self.orientation.setCurrentIndex(
            1 if sheet.orientation == dwg.PORTRAIT else 0)
        form.addRow("Orientation", self.orientation)

        self.border = QtWidgets.QComboBox()
        self.border.addItem("(none)", "")
        for name in sorted(doc.borders):
            self.border.addItem(name, name)
        self.border.setCurrentIndex(max(0, self.border.findData(sheet.border)))
        form.addRow("Border", self.border)

        self.block = QtWidgets.QComboBox()
        self.block.addItem("(none)", "")
        for name in sorted(doc.title_blocks):
            self.block.addItem(name, name)
        self.block.setCurrentIndex(
            max(0, self.block.findData(sheet.title_block)))
        form.addRow("Title block", self.block)

        self.standard = QtWidgets.QComboBox()
        self.standard.addItems(sorted(dwg.STANDARDS))
        self.standard.setCurrentText(doc.standard)
        self.standard.setToolTip(
            "ISO puts projected views on the far side (first angle); "
            "ANSI puts them on the near side (third angle).")
        form.addRow("Projection", self.standard)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def apply(self) -> None:
        self.sheet.name = self.name.text().strip() or self.sheet.name
        self.sheet.size = self.size.currentText()
        self.sheet.orientation = (dwg.PORTRAIT
                                  if self.orientation.currentIndex()
                                  else dwg.LANDSCAPE)
        self.sheet.border = self.border.currentData() or ""
        self.sheet.title_block = self.block.currentData() or ""
        self.doc.standard = self.standard.currentText()
        self.doc.modified = True


class ViewDialog(QtWidgets.QDialog):
    """Orientation, scale and display style for one view."""

    def __init__(self, parent, doc: DrawingDocument, sheet: Sheet,
                 view: View) -> None:
        super().__init__(parent)
        self.doc = doc
        self.sheet = sheet
        self.view = view
        self.setWindowTitle(view.label)
        self.setMinimumWidth(320)

        form = QtWidgets.QFormLayout(self)
        self.name = QtWidgets.QLineEdit(view.name)
        form.addRow("Name", self.name)

        self.orientation = QtWidgets.QComboBox()
        for key, label in sorted(hlr.ORIENTATION_LABELS.items()):
            self.orientation.addItem(label, key)
        self.orientation.setCurrentIndex(
            max(0, self.orientation.findData(view.orientation)))
        self.orientation.setEnabled(view.kind == BASE)
        form.addRow("Orientation", self.orientation)

        # A view taken from another has that one's scale, locked, unless
        # it is given one of its own: the box says which, as Inventor's
        self.scale = QtWidgets.QComboBox()
        self.scale.setEditable(True)
        for value in dwg.SCALES:
            self.scale.addItem(dwg.scale_text(value), value)
        self.scale.setEditText(dwg.scale_text(doc.view_scale(sheet, view)))
        form.addRow("Scale", self.scale)
        self.own_scale = QtWidgets.QCheckBox("Its own scale, not the base "
                                             "view's")
        if view.parent:
            self.own_scale.setChecked(view.scale > 0)
            self.scale.setEnabled(view.scale > 0)
            self.own_scale.toggled.connect(self.scale.setEnabled)
            form.addRow("", self.own_scale)

        self.display = QtWidgets.QComboBox()
        self.display.addItem("From parent", "")
        for key, label in dwg.DISPLAY_LABELS.items():
            self.display.addItem(label, key)
        self.display.setCurrentIndex(
            max(0, self.display.findData(view.display)))
        form.addRow("Display", self.display)

        # only a section has cut faces, so only a section is asked
        self.hatch = QtWidgets.QCheckBox("Hatch the cut faces")
        self.hatch.setChecked(view.hatch)
        self.pattern = QtWidgets.QComboBox()
        self.pattern.addItem("From the material", "")
        patterns = dwg.hatch_patterns()
        for key in dwg.HATCH_ORDER:
            self.pattern.addItem(patterns[key].name, key)
        self.pattern.setCurrentIndex(
            max(0, self.pattern.findData(view.hatch_pattern)))
        self.hatch.toggled.connect(self.pattern.setEnabled)
        self.pattern.setEnabled(view.hatch)
        if view.kind == SECTION:
            form.addRow("", self.hatch)
            form.addRow("Pattern", self.pattern)

        self.show_label = QtWidgets.QCheckBox("Show the label")
        self.show_label.setChecked(view.label_visible)
        form.addRow("", self.show_label)
        self.show_scale = QtWidgets.QCheckBox("Show the scale")
        self.show_scale.setChecked(view.scale_visible)
        form.addRow("", self.show_scale)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def apply(self) -> None:
        view = self.view
        view.name = self.name.text().strip() or view.name
        if view.kind == BASE:
            view.orientation = self.orientation.currentData() or "front"
            view.direction = []
            view.up = []
        typed = dwg.parse_scale(self.scale.currentText())
        if view.parent and not self.own_scale.isChecked():
            view.scale = 0.0
        elif typed:
            view.scale = typed
        view.display = self.display.currentData() or ""
        if view.kind == SECTION:
            view.hatch = self.hatch.isChecked()
            view.hatch_pattern = self.pattern.currentData() or ""
        view.label_visible = self.show_label.isChecked()
        view.scale_visible = self.show_scale.isChecked()
        self.doc.modified = True


class BaseViewDialog(QtWidgets.QDialog):
    """Inventor's Drawing View box: which model, how it is drawn, how big.

    Modeless, so the sheet behind it stays live while it is open: the view
    it describes is there, drawn, and pointed with the cube beside it.  It
    stays open until OK makes the views or Cancel drops them.
    """

    changed = QtCore.Signal()
    place_requested = QtCore.Signal()

    def __init__(self, parent, controller: DrawingController,
                 doc: DrawingDocument, sheet: Sheet,
                 editing: Optional[View] = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.doc = doc
        self.sheet = sheet
        self.editing = editing
        self.setWindowTitle("Drawing View (%s)" % editing.name
                            if editing is not None else "Drawing View")
        self.setModal(False)
        self.setMinimumWidth(380)
        # a scale picked or typed is the user's; until then it follows the
        # model, the way Inventor's does.  A view being edited has one.
        self._scale_chosen = editing is not None

        form = QtWidgets.QFormLayout(self)
        row = QtWidgets.QHBoxLayout()
        self.file = QtWidgets.QComboBox()
        self.file.setMinimumWidth(240)
        for entry in controller.open_models():
            self.file.addItem(entry.label, os.path.abspath(entry.path))
        self.browse = QtWidgets.QToolButton()
        self.browse.setIcon(icons.icon("open", 16))
        self.browse.setToolTip("Pick a part or assembly from a folder")
        row.addWidget(self.file, 1)
        row.addWidget(self.browse)
        form.addRow("File", row)

        self.style = QtWidgets.QComboBox()
        for key, label in dwg.DISPLAY_LABELS.items():
            self.style.addItem(label, key)
        self.style.setCurrentIndex(max(0, self.style.findData(WITH_HIDDEN)))
        form.addRow("Style", self.style)

        self.scale = QtWidgets.QComboBox()
        self.scale.setEditable(True)
        for value in dwg.SCALES:
            self.scale.addItem(dwg.scale_text(value), value)
        self._set_scale(1.0)
        form.addRow("Scale", self.scale)

        self.name = QtWidgets.QLineEdit(doc.unique_view_name(sheet, BASE))
        form.addRow("View identifier", self.name)

        hint = QtWidgets.QLabel(
            "Point the view with the cube beside it, and drag it where it "
            "goes. Click beside, above or below it for projections, off a "
            "corner for an isometric; drag those too, right-click one to "
            "drop it.")
        hint.setWordWrap(True)
        hint.setProperty("hint", True)
        form.addRow(hint)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.place_requested)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

        self.browse.clicked.connect(self._browse)
        self.file.currentIndexChanged.connect(lambda _i: self._model_changed())
        self.style.currentIndexChanged.connect(lambda _i: self.changed.emit())
        self.scale.editTextChanged.connect(self._scale_edited)
        self.name.textChanged.connect(lambda _t: self.changed.emit())
        if editing is not None:
            self._show_view(editing)
        self._model_changed()

    def _show_view(self, view: View) -> None:
        """Fill the box in from a view on the sheet, to change it."""
        model = self.doc.view_model(self.sheet, view)
        resolved = (model.ref.resolve(self.doc.base_dir)
                    if model is not None and model.ref.path else None)
        if resolved:
            self.choose_file(resolved)
        self.style.setCurrentIndex(max(0, self.style.findData(
            self.doc.view_display(self.sheet, view))))
        self._set_scale(self.doc.view_scale(self.sheet, view))
        self.name.setText(view.name)

    # -- reading it -----------------------------------------------------------

    def path(self) -> str:
        return self.file.currentData() or ""

    def scale_value(self) -> float:
        return dwg.parse_scale(self.scale.currentText()) or 1.0

    def settings(self) -> Dict[str, Any]:
        return {"path": self.path(),
                "display": self.style.currentData() or WITH_HIDDEN,
                "scale": self.scale_value(),
                "name": self.name.text().strip()}

    # -- keeping up -----------------------------------------------------------

    def choose_file(self, path: str) -> None:
        """Offer a file, from a folder, and pick it."""
        path = os.path.abspath(path)
        index = self.file.findData(path)
        if index < 0:
            self.file.addItem(os.path.basename(path), path)
            index = self.file.count() - 1
        self.file.setCurrentIndex(index)

    def _browse(self) -> None:
        start = self.doc.base_dir or self.controller.host.project_folder()
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Pick a part or assembly", start,
            "Parts and assemblies (*.pdat *.adat);;"
            "DATUM Part (*.pdat);;DATUM Assembly (*.adat);;All files (*)")
        if path:
            self.choose_file(path)

    def _model_changed(self) -> None:
        path = self.path()
        if path and not self._scale_chosen:
            self._set_scale(self.controller.fit_scale(path, self.sheet))
        self.changed.emit()

    def _set_scale(self, value: float) -> None:
        was = self.scale.blockSignals(True)
        text = dwg.scale_text(value)
        index = self.scale.findText(text)
        if index >= 0:
            self.scale.setCurrentIndex(index)
        else:
            self.scale.setEditText(text)
        self.scale.blockSignals(was)

    def _scale_edited(self, _text: str) -> None:
        self._scale_chosen = True
        self.changed.emit()

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        # Enter is OK here as in any dialog, but OK is place_requested,
        # not accept, so it is wired by hand
        if event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            self.place_requested.emit()
            return
        super().keyPressEvent(event)


class PreviewProjections(QtCore.QObject):
    """Views worked out for a preview, in the background where possible.

    Hidden line removal on a big model takes seconds, and a preview that
    froze the window every time the cube was clicked would be worse than
    none.  So each is asked of a worker and shown when it comes back;
    until then the view shows as the box it will fill.
    """

    ready = QtCore.Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._done: Dict[Any, hlr.Projection] = {}
        self._pending: Dict[Any, Any] = {}
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._poll)
        self.library = None

    def get(self, path: str, direction, up,
            hidden: bool) -> Optional[hlr.Projection]:
        stamp = os.path.getmtime(path) if os.path.exists(path) else 0.0
        key = (os.path.abspath(path), stamp,
               tuple(round(float(c), 9) for c in direction),
               tuple(round(float(c), 9) for c in up), bool(hidden))
        if key not in self._done and key not in self._pending:
            self._start(key, path, direction, up, hidden)
        return self._done.get(key)

    @property
    def busy(self) -> bool:
        return bool(self._pending)

    def wait(self, seconds: float = 60.0) -> None:
        """Until every preview asked for is back.  For tests and shots."""
        import time
        end = time.monotonic() + seconds
        while self._pending and time.monotonic() < end:
            self._poll()
            QtWidgets.QApplication.processEvents()
            time.sleep(0.02)

    def _start(self, key, path, direction, up, hidden) -> None:
        from ..core import rules, workers

        helpers = workers.pool()
        if helpers is not None:
            try:
                self._pending[key] = helpers.submit(
                    "project", trusted=sorted(rules.trusted_paths()),
                    path=os.path.abspath(path), direction=list(direction),
                    up=list(up), hidden=bool(hidden))
                self._timer.start()
                return
            except Exception:
                pass
        # no workers to hand it to: worked out here, now
        try:
            if self.library is None:
                from ..core.parts import PartLibrary
                self.library = PartLibrary()
            shape, _model = viewgen.load_model(path, self.library)
            self._done[key] = hlr.project(shape, tuple(direction), tuple(up),
                                          hidden=hidden)
        except Exception as exc:
            self._done[key] = hlr.Projection(error=str(exc))

    def _poll(self) -> None:
        arrived = False
        for key, future in list(self._pending.items()):
            if not future.done():
                continue
            del self._pending[key]
            try:
                self._done[key] = hlr.Projection.from_dict(future.result())
            except Exception as exc:
                self._done[key] = hlr.Projection(error=str(exc))
            arrived = True
        if not self._pending:
            self._timer.stop()
        if arrived:
            self.ready.emit()

    def close(self) -> None:
        self._timer.stop()
        self._pending.clear()


PREVIEW = "#3d5a80"
PREVIEW_HIDDEN = "#8fa3ba"


class BaseViewSession(QtCore.QObject):
    """A base view and its projections, set up on the sheet before they are
    made, the way Inventor's Drawing View box works.

    The base view starts in the middle of the sheet, drawn, with the view
    cube beside it.  Clicking the cube points the view; dragging the view
    moves it, and its projections with it.  A click beside the view, above
    or below it adds a projection there, level or in line with it, and a
    click off a corner an isometric; each can be dragged along its line,
    an isometric anywhere, and a right-click on one drops it.  OK makes
    them all, Cancel or Esc none.
    """

    def __init__(self, controller: DrawingController, sheet: Sheet,
                 dialog: BaseViewDialog,
                 editing: Optional[View] = None) -> None:
        super().__init__(controller)
        self.controller = controller
        self.sheet = sheet
        self.dialog: Optional[BaseViewDialog] = dialog
        self.editing = editing
        self.direction, self.up = hlr.ORIENTATIONS["front"]
        self.x, self.y = self._middle()
        self.children: List[List[float]] = []
        # which view on the sheet each child is, 0 for one not made yet
        self.child_ids: List[int] = []
        # views on the sheet left out while their preview stands in
        self.hidden: set = set()
        if editing is not None:
            doc = controller.document
            pd, pu = controller.generator.orientation(doc, sheet, editing)
            self.direction = tuple(float(c) for c in pd)
            self.up = tuple(float(c) for c in pu)
            self.x, self.y = editing.x, editing.y
            for child in sheet.children_of(editing.id):
                if child.kind == PROJECTED:
                    self.children.append([child.x, child.y])
                    self.child_ids.append(child.id)
            self.hidden = {editing.id} | set(self.child_ids)
        self.cube = OrientationCube()
        self.cube.set_view(self.direction, self.up)
        self.cursor: Optional[Tuple[float, float]] = None
        self.placed: List[int] = []
        self._press = None
        self._dragged = False
        self._last_scale = dialog.scale_value()
        self._paths: Dict[int, Tuple[QtGui.QPainterPath,
                                     QtGui.QPainterPath]] = {}
        self.projections = PreviewProjections(self)
        self.projections.ready.connect(self._refresh)
        dialog.changed.connect(self._settings_changed)
        dialog.place_requested.connect(self.commit)
        dialog.finished.connect(self._dialog_closed)

    def _middle(self) -> Tuple[float, float]:
        doc = self.controller.document
        border = doc.borders.get(self.sheet.border) if doc else None
        width, height = self.sheet.extent()
        frame = (border.frame(width, height) if border
                 else (0.0, 0.0, width, height))
        return ((frame[0] + frame[2]) / 2.0, (frame[1] + frame[3]) / 2.0)

    # -- the views as they stand ----------------------------------------------

    def settings(self) -> Dict[str, Any]:
        return self.dialog.settings() if self.dialog is not None else {}

    def _scale(self) -> float:
        return float(self.settings().get("scale") or 1.0)

    def set_orientation(self, direction, up) -> None:
        """Point the base view; its projections turn with it."""
        self.direction = tuple(float(c) for c in direction)
        self.up = tuple(float(c) for c in up)
        self.cube.set_view(self.direction, self.up)
        self._refresh()

    def _child_view(self, cx: float, cy: float):
        doc = self.controller.document
        dx, dy = cx - self.x, cy - self.y
        if abs(dx) > 1e-6 and abs(dy) > 1e-6:
            direction, up = viewgen.iso_orientation(self.direction, self.up,
                                                    dx, dy)
            return direction, up, "Isometric", True
        direction, up = viewgen.projected_orientation(
            self.direction, self.up, dx, dy, doc.angle if doc else
            dwg.FIRST_ANGLE)
        name = viewgen.named_orientation(direction)
        return (direction, up,
                hlr.ORIENTATION_LABELS.get(name, "Projected View"), False)

    def views(self) -> List[Dict[str, Any]]:
        """Every view being set up: where, which way, and its lines."""
        settings = self.settings()
        path = settings.get("path") or ""
        hidden = settings.get("display") == WITH_HIDDEN
        out = [{"key": "base", "x": self.x, "y": self.y,
                "direction": self.direction, "up": self.up,
                "hidden": hidden,
                "label": "%s (%s)" % (settings.get("name") or "Base View",
                                      dwg.scale_text(self._scale()))}]
        for i, (cx, cy) in enumerate(self.children):
            direction, up, label, diagonal = self._child_view(cx, cy)
            out.append({"key": i, "x": cx, "y": cy, "direction": direction,
                        "up": up, "hidden": hidden and not diagonal,
                        "label": label})
        for view in out:
            view["projection"] = (self.projections.get(
                path, view["direction"], view["up"], view["hidden"])
                if path else None)
        return out

    def _size(self, view: Dict[str, Any]) -> Tuple[float, float]:
        scale = self._scale()
        projection = view.get("projection")
        if projection is not None and projection.ok and projection.box:
            box = projection.box
            return ((box[2] - box[0]) * scale, (box[3] - box[1]) * scale)
        path = self.settings().get("path") or ""
        box = self.controller.model_box(path) if path else None
        if box is None:
            return (40.0, 30.0)
        across, upward = viewgen.outline(box, view["direction"], view["up"])
        return (across * scale, upward * scale)

    def _box(self, view: Dict[str, Any]):
        return _box_at((view["x"], view["y"]), self._size(view))

    def _view_at(self, point) -> Optional[Any]:
        if point is None:
            return None
        for view in reversed(self.views()):
            x0, y0, x1, y1 = self._box(view)
            if x0 - 2.0 <= point[0] <= x1 + 2.0 and \
                    y0 - 2.0 <= point[1] <= y1 + 2.0:
                return view["key"]
        return None

    def _spot(self, point):
        base = self.views()[0]
        return dwg.projected_spot((self.x, self.y), self._size(base), point)

    # -- the canvas asks ------------------------------------------------------

    def press(self, point, device: QtCore.QPointF) -> None:
        element = self.cube.element_at(device)
        if element or self.cube.contains(device):
            # the cube's panel is the cube's: a click on it never lands on
            # the sheet behind
            view = self.cube.view_for(element) if element else None
            if view is not None:
                self.set_orientation(*view)
            self._press = None
            return
        self._press = (self._view_at(point), (point[0], point[1]),
                       (self.x, self.y), [list(c) for c in self.children])
        self._dragged = False

    def move(self, point, device: QtCore.QPointF, held: bool) -> None:
        self.cursor = (point[0], point[1])
        if self._press is not None and held:
            key, start, base_at, children_at = self._press
            dx, dy = point[0] - start[0], point[1] - start[1]
            if abs(dx) + abs(dy) > 1e-9:
                self._dragged = True
            if key == "base":
                self.x, self.y = base_at[0] + dx, base_at[1] + dy
                self.children = [[c[0] + dx, c[1] + dy] for c in children_at]
            elif isinstance(key, int) and key < len(self.children):
                cx, cy = children_at[key]
                level = abs(cy - base_at[1]) <= 1e-6
                in_line = abs(cx - base_at[0]) <= 1e-6
                if level:
                    self.children[key] = [cx + dx, cy]
                elif in_line:
                    self.children[key] = [cx, cy + dy]
                else:
                    self.children[key] = [cx + dx, cy + dy]
            return
        self.cube.hover = self.cube.element_at(device)

    def release(self, point, device: QtCore.QPointF) -> None:
        press, self._press = self._press, None
        if press is None or press[0] is not None or self._dragged:
            return
        spot = self._spot(point)
        if spot is not None:
            self.children.append([spot[0], spot[1]])
            self.child_ids.append(0)

    def right_click(self, point, device: QtCore.QPointF) -> bool:
        key = self._view_at(point)
        if isinstance(key, int) and key < len(self.children):
            del self.children[key]
            del self.child_ids[key]
        return True

    def cursor_shape(self, point, device: QtCore.QPointF):
        if self.cube.element_at(device):
            return QtCore.Qt.PointingHandCursor
        if self.cube.contains(device):
            return QtCore.Qt.ArrowCursor
        if self._view_at(point) is not None:
            return QtCore.Qt.SizeAllCursor
        return QtCore.Qt.CrossCursor

    # -- painting ---------------------------------------------------------------

    def paint(self, painter: QtGui.QPainter, layout) -> None:
        views = self.views()
        for view in views:
            self._paint_view(painter, layout, view)
        over = self._view_at(self.cursor)
        on_cube = (self.cursor is not None and self.cube.contains(
            QtCore.QPointF(*layout.to_device(*self.cursor))))
        if self.cursor is not None and over is None and self._press is None                 and not on_cube:
            spot = self._spot(self.cursor)
            if spot is not None:
                direction, up, label, _diagonal = self._child_view(*spot)
                ghost = {"x": spot[0], "y": spot[1], "direction": direction,
                         "up": up, "projection": None}
                paint_boxes(painter, layout,
                            [(self._box(ghost), label)])
        # Off the view's top right corner, clear of where a side view or a
        # top view goes.  Measured from the model's whole size, which no
        # turn changes, not from the view as it is now: a cube that jumped
        # every time it was clicked would leave the cursor off the arrow
        # that was being clicked through the faces.
        reach_mm = self._radius()
        corner_x, corner_y = layout.to_device(self.x + reach_mm,
                                              self.y + reach_mm)
        canvas = self.controller.canvas
        reach = OrientationCube.SIZE + 40.0
        centre = QtCore.QPointF(
            max(reach, min(canvas.width() - reach, corner_x + reach * 0.6)),
            max(reach, min(canvas.height() - reach, corner_y - reach * 0.6)))
        self.cube.draw(painter, centre)

    def _radius(self) -> float:
        """Half the model's diagonal on the sheet: as big as any view of
        it can be, whichever way it is turned."""
        path = self.settings().get("path") or ""
        box = self.controller.model_box(path) if path else None
        if box is None:
            return 20.0
        diagonal = math.sqrt(sum((box[i + 3] - box[i]) ** 2
                                 for i in range(3)))
        return diagonal / 2.0 * self._scale()

    def _paint_view(self, painter: QtGui.QPainter, layout,
                    view: Dict[str, Any]) -> None:
        box = self._box(view)
        projection = view.get("projection")
        scale = self._scale()
        if projection is not None and projection.ok:
            visible, hidden = self._path_for(projection)
            s = layout.scale
            painter.save()
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
            painter.setTransform(QtGui.QTransform(
                scale * s, 0.0, 0.0, -scale * s,
                layout.offset_x + view["x"] * s,
                layout.offset_y + (layout.height - view["y"]) * s))
            painter.setBrush(QtCore.Qt.NoBrush)
            pen = QtGui.QPen(QtGui.QColor(PREVIEW), 1.3)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.drawPath(visible)
            pen = QtGui.QPen(QtGui.QColor(PREVIEW_HIDDEN), 1.0,
                             QtCore.Qt.DashLine)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.drawPath(hidden)
            painter.restore()
            label = view["label"]
        else:
            label = (view["label"] + "  (drawing...)" if projection is None
                     else view["label"])
        # the box it fills, faint, so it is plain it can be taken hold of
        paint_boxes(painter, layout, [(box, label)], faint=True)

    def _path_for(self, projection: hlr.Projection):
        held = self._paths.get(id(projection))
        if held is not None and held[0] is projection:
            return held[1], held[2]
        visible = QtGui.QPainterPath()
        hidden = QtGui.QPainterPath()
        for line in projection.lines:
            if len(line.points) < 2:
                continue
            path = hidden if line.kind == hlr.HIDDEN else visible
            first = line.points[0]
            path.moveTo(first[0], first[1])
            for x, y in line.points[1:]:
                path.lineTo(x, y)
        self._paths[id(projection)] = (projection, visible, hidden)
        return visible, hidden

    # -- keeping up, and ending ------------------------------------------------

    def _settings_changed(self) -> None:
        # a new scale spreads the projections out, or in, with it, so they
        # keep the gaps they were given instead of landing on each other
        scale = self._scale()
        if self._last_scale and abs(scale - self._last_scale) > 1e-12:
            ratio = scale / self._last_scale
            self.children = [[self.x + (c[0] - self.x) * ratio,
                              self.y + (c[1] - self.y) * ratio]
                             for c in self.children]
        self._last_scale = scale
        self._refresh()

    def _refresh(self) -> None:
        self.controller.canvas.update()

    def commit(self) -> None:
        """OK: make the base view and its projections, as set up."""
        settings = self.settings()
        if not settings.get("path"):
            self.controller._complain(
                "Pick a part or assembly to draw first.")
            return
        if self.editing is not None:
            self.controller.apply_view_edit(
                self.editing, settings, self.direction, self.up, self.x,
                self.y, [tuple(c) for c in self.children], self.child_ids)
            self.controller.end_placement()
            return
        base = self.controller.create_views(
            settings, self.direction, self.up, self.x, self.y,
            [tuple(c) for c in self.children])
        if base is None:
            return
        self.placed = [base.id] + [v.id for v in
                                   self.sheet.children_of(base.id)]
        self.controller.end_placement()

    def cancel(self) -> None:
        self.controller.end_placement()

    def _dialog_closed(self, _result: int) -> None:
        # closed by its own Cancel or its X, not by OK
        if self.dialog is not None:
            self.dialog = None
            self.controller.end_placement()

    def close(self) -> None:
        dialog, self.dialog = self.dialog, None
        self.projections.close()
        if dialog is not None:
            dialog.close()


class ViewPlacer:
    """Projections of a view on the sheet, placed with the cursor.

    Projected with a view picked: each click places one, level beside it a
    side view, in line above or below a top or bottom view, off a corner
    an isometric, each shown as the box it will fill before it is placed.
    A right-click or Esc ends it.
    """

    def __init__(self, controller: DrawingController, sheet: Sheet,
                 parent: int) -> None:
        self.controller = controller
        self.sheet = sheet
        self.parent = parent
        self.cursor: Optional[Tuple[float, float]] = None
        self.placed: List[int] = []

    def hover(self, point: Sequence[float]) -> None:
        self.cursor = (float(point[0]), float(point[1]))

    def click(self, point: Sequence[float]) -> None:
        self.hover(point)
        spot = self.spot()
        if spot is None:
            return
        view = self.controller.create_projected(self.parent, *spot[0])
        if view is not None:
            self.placed.append(view.id)

    # the canvas's side of it
    def press(self, point, device) -> None:
        self.click(point)

    def move(self, point, device, held: bool) -> None:
        self.hover(point)

    def release(self, point, device) -> None:
        pass

    def right_click(self, point, device) -> bool:
        return False

    def cursor_shape(self, point, device):
        return QtCore.Qt.CrossCursor

    def paint(self, painter: QtGui.QPainter, layout) -> None:
        paint_boxes(painter, layout, self.preview())

    def preview(self) -> List[Tuple[Tuple[float, float, float, float], str]]:
        """The box the next projection will fill, named."""
        spot = self.spot()
        if spot is None:
            return []
        (x, y), direction, up, label = spot
        return [(_box_at((x, y), self._child_size(direction, up)), label)]

    def spot(self):
        """Where the next projection goes, which way it looks, and its name.

        ((x, y), direction, up, label), or None over the parent itself.
        """
        doc = self.controller.document
        parent = self.sheet.view(self.parent) if self.parent else None
        if doc is None or parent is None or self.cursor is None:
            return None
        if parent.projection is not None and parent.projection.box:
            box = parent.projection.box
            size = (box[2] - box[0], box[3] - box[1])
        else:
            pd, pu = self.controller.generator.orientation(doc, self.sheet,
                                                           parent)
            size = self._child_size(pd, pu)
        spot = dwg.projected_spot((parent.x, parent.y), size, self.cursor)
        if spot is None:
            return None
        dx, dy = spot[0] - parent.x, spot[1] - parent.y
        pd, pu = self.controller.generator.orientation(doc, self.sheet, parent)
        if abs(dx) > 1e-6 and abs(dy) > 1e-6:
            direction, up = viewgen.iso_orientation(pd, pu, dx, dy)
            return (spot, direction, up, "Isometric")
        direction, up = viewgen.projected_orientation(pd, pu, dx, dy,
                                                      doc.angle)
        name = viewgen.named_orientation(direction)
        return (spot, direction, up,
                hlr.ORIENTATION_LABELS.get(name, "Projected View"))

    def _child_size(self, direction, up):
        doc = self.controller.document
        parent = self.sheet.view(self.parent)
        model = doc.view_model(self.sheet, parent) if parent else None
        path = (model.ref.resolve(doc.base_dir)
                if model is not None and model.ref.path else None)
        box = self.controller.model_box(path) if path else None
        if box is None:
            return (40.0, 40.0)
        across, upward = viewgen.outline(box, direction, up)
        scale = doc.view_scale(self.sheet, parent)
        return (across * scale, upward * scale)

    def close(self) -> None:
        pass


def paint_boxes(painter: QtGui.QPainter, layout, boxes,
                faint: bool = False) -> None:
    """Boxes on the sheet, in sheet millimetres, each with its name under it:
    where a view will land, or the room a view being set up takes."""
    from . import sheetpaint

    colour = QtGui.QColor(sheetpaint.SELECTED)
    painter.save()
    for box, label in boxes:
        x0, y0 = layout.to_device(box[0], box[3])
        x1, y1 = layout.to_device(box[2], box[1])
        rect = QtCore.QRectF(QtCore.QPointF(x0, y0),
                             QtCore.QPointF(x1, y1)).normalized()
        pen = QtGui.QPen(colour, 1.0 if faint else 1.6, QtCore.Qt.DashLine)
        if faint:
            pen.setColor(QtGui.QColor("#a9b4c1"))
        painter.setPen(pen)
        fill = QtGui.QColor(colour)
        fill.setAlpha(0 if faint else 22)
        painter.setBrush(fill)
        painter.drawRect(rect)
        painter.setPen(colour)
        painter.drawText(rect.adjusted(-80, 0, 80, 18),
                         QtCore.Qt.AlignHCenter | QtCore.Qt.AlignBottom,
                         label)
    painter.restore()


def _box_at(centre: Sequence[float], size: Sequence[float]
            ) -> Tuple[float, float, float, float]:
    half_w, half_h = size[0] / 2.0, size[1] / 2.0
    return (centre[0] - half_w, centre[1] - half_h,
            centre[0] + half_w, centre[1] + half_h)


def _descendants(sheet: Sheet, view_id: int) -> List[View]:
    """Every view that comes from this one, however indirectly."""
    out: List[View] = []
    queue = [view_id]
    while queue:
        current = queue.pop()
        for child in sheet.children_of(current):
            out.append(child)
            queue.append(child.id)
    return out
