"""The drawing workspace: placing views, dimensioning them, getting output.

The counterpart to ``assembly_ui`` and ``cam_ui``.  The document says what
the drawing is, ``core.views`` works out what it looks like, ``sheetpaint``
draws it, and this decides what happens when a button is pressed.
"""

from __future__ import annotations

import math
import os
from typing import Any, Dict, List, Optional

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
from .theme import C


class DrawingController(QtCore.QObject):
    """Everything the main window does while a drawing is open."""

    def __init__(self, host) -> None:
        super().__init__(host)
        self.host = host
        self.browser = DrawingBrowser(host)
        self.generator = viewgen.Generator()
        self._properties: Dict[int, Dict[str, str]] = {}
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
        canvas.view_activated.connect(self.open_model)
        canvas.context_requested.connect(self._canvas_menu)

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
        """Bring a model onto the sheet, laid out the way a drawing is."""
        doc = self.document
        sheet = doc.active() if doc else None
        if sheet is None:
            return
        start = doc.base_dir or self.host.project_folder()
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self.host, "Place a model on the sheet", start,
            "Parts and assemblies (*.pdat *.adat);;"
            "DATUM Part (*.pdat);;DATUM Assembly (*.adat);;All files (*)")
        if not path:
            return
        if not doc.path:
            QtWidgets.QMessageBox.information(
                self.host, "Save first",
                "Save the drawing before placing a view, so its reference to "
                "the model can be stored relative to it.")
            if not self.host.save_document():
                return

        doc.push_undo()
        shape = None
        try:
            shape, _model = viewgen.load_model(path, self.generator.library)
        except Exception:
            shape = None
        span = (100.0, 100.0, 100.0)
        if shape is not None:
            box = kernel.bounding_box(shape)
            span = (box[3] - box[0], box[4] - box[1], box[5] - box[2])

        border = doc.borders.get(sheet.border)
        frame = (border.frame(*sheet.extent()) if border
                 else (10.0, 10.0) + sheet.extent())
        block = doc.title_blocks.get(sheet.title_block)
        reserve = (block.width, block.height) if block else (0.0, 0.0)
        plan = dwg.three_view_layout(frame, span, reserve=reserve)

        base = View(kind=BASE, orientation="front", scale=plan["scale"],
                    x=plan["base"][0], y=plan["base"][1], display=WITH_HIDDEN)
        base.ref = fileformat.ComponentRef(
            path=fileformat.relative_path(path, doc.base_dir),
            name=os.path.basename(path),
            label=os.path.splitext(os.path.basename(path))[0])
        doc.add_view(sheet, base)

        for key, name in (("below", "Top"), ("side", "Left")):
            doc.add_view(sheet, View(kind=PROJECTED, parent=base.id,
                                     x=plan[key][0], y=plan[key][1]))
        doc.add_view(sheet, View(kind=PROJECTED, parent=base.id,
                                 display="visible", scale=plan["iso_scale"],
                                 x=plan["iso"][0], y=plan["iso"][1]))
        self.rebuild(keep_view=False)
        self.host.status_message.setText(
            "Placed %s with three projections." % os.path.basename(path))

    def add_projected(self) -> None:
        """A projection of the selected view, put where there is room."""
        doc, sheet, view = self._selected_view()
        if view is None:
            self._complain("Select a view first, then add a projection of it.")
            return
        doc.push_undo()
        box = view.projection.box if view.projection else (-20, -20, 20, 20)
        gap = 18.0
        child = View(kind=PROJECTED, parent=view.id,
                     x=view.x, y=view.y - (box[3] - box[1]) - gap)
        doc.add_view(sheet, child)
        self.rebuild()
        self.canvas.select([child.id])

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
            self.rebuild(force=True)

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
        doc = self.document
        if doc is None:
            return
        doc.modified = True
        # a projected view's direction comes from where it sits, so moving
        # one can change what it shows
        self.rebuild(force=True)

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

        self.scale = QtWidgets.QComboBox()
        self.scale.setEditable(True)
        self.scale.addItem("From parent" if view.parent else "1:1", 0.0)
        for value in dwg.SCALES:
            self.scale.addItem(dwg.scale_text(value), value)
        index = self.scale.findData(view.scale)
        self.scale.setCurrentIndex(index if index >= 0 else 0)
        form.addRow("Scale", self.scale)

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
        data = self.scale.currentData()
        view.scale = float(data) if data is not None else view.scale
        view.display = self.display.currentData() or ""
        if view.kind == SECTION:
            view.hatch = self.hatch.isChecked()
            view.hatch_pattern = self.pattern.currentData() or ""
        view.label_visible = self.show_label.isChecked()
        view.scale_visible = self.show_scale.isChecked()
        self.doc.modified = True
