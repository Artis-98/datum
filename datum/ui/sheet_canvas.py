"""The sheet on screen: pan, zoom, pick and drag.

Paper, not a scene, so this is a plain widget with a QPainter rather than
the 3D viewport.  It owns no geometry: every frame it asks ``sheetpaint`` to
draw the document as it stands, which is the same call the PDF and the
printer make.  What it adds is the part that only makes sense on a screen -
knowing what is under the cursor, and moving it.
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

from ..core import drawing as dwg
from ..core.drawing import Annotation, DrawingDocument, Sheet, View
from . import sheetpaint
from .theme import C

# how near the cursor has to be, in screen pixels
PICK_PIXELS = 9.0
ZOOM_STEP = 1.15
MIN_SCALE, MAX_SCALE = 0.05, 40.0


class SheetCanvas(QtWidgets.QWidget):
    """Shows one sheet of a drawing, and lets things on it be moved."""

    selection_changed = QtCore.Signal()
    changed = QtCore.Signal()               # something was moved
    view_activated = QtCore.Signal(int)     # double-clicked
    context_requested = QtCore.Signal(QtCore.QPoint)
    placing_finished = QtCore.Signal()      # right-click or Esc while placing
    annotation_activated = QtCore.Signal(int)   # a dimension double-clicked
    grip_moved = QtCore.Signal(int, int)        # a dimension's leg, moved

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("SheetCanvas")
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setAutoFillBackground(True)

        self.doc: Optional[DrawingDocument] = None
        self.model_properties = None
        self.selected_views: List[int] = []
        self.selected_annotations: List[int] = []

        self._scale = 1.0
        self._pan = [0.0, 0.0]
        self._fitted = False
        self._drag_view: Optional[int] = None
        self._drag_note: Optional[int] = None
        self._drag_table: Optional[int] = None
        self._drag_from: Optional[Tuple[float, float]] = None
        self._origin: Optional[Tuple[float, float]] = None
        self._panning = False
        self._moved = False
        self._last_pos = QtCore.QPoint()
        # views being placed with the cursor: the left button places, the
        # right one or Esc stops; see drawing_ui.ViewPlacer
        self.placer = None
        # a selected dimension's legs can be dragged to other points: the
        # grip being dragged, and what finds a point to drop it on
        self._drag_grip: Optional[Tuple[int, int]] = None
        self.grip_snap = None

    def set_placer(self, placer) -> None:
        """Hand the mouse to something placing views, or take it back.

        It is asked to ``paint`` itself over the sheet, and told of each
        ``press``, ``move`` and ``release`` in sheet millimetres and in
        pixels; a ``right_click`` it does not want ends it.
        """
        self.placer = placer
        self.setCursor(QtCore.Qt.CrossCursor if placer is not None
                       else QtCore.Qt.ArrowCursor)
        self.update()

    # ------------------------------------------------------------ document

    def set_document(self, doc: Optional[DrawingDocument],
                     keep_view: bool = False) -> None:
        self.doc = doc
        if not keep_view:
            self._fitted = False
        self.selected_views = []
        self.selected_annotations = []
        self.update()

    def sheet(self) -> Optional[Sheet]:
        return self.doc.active() if self.doc is not None else None

    # -------------------------------------------------------------- layout

    def layout(self) -> sheetpaint.Layout:
        sheet = self.sheet()
        if sheet is None:
            return sheetpaint.Layout(1.0, 0.0, 0.0, 297.0)
        width, height = sheet.extent()
        if not self._fitted:
            self.fit()
        base = sheetpaint.fit(width, height, self.width(), self.height())
        return sheetpaint.Layout(
            scale=base.scale * self._scale,
            offset_x=base.offset_x + self._pan[0]
            - (base.scale * (self._scale - 1.0)) * width / 2.0,
            offset_y=base.offset_y + self._pan[1]
            - (base.scale * (self._scale - 1.0)) * height / 2.0,
            height=height)

    def fit(self) -> None:
        self._scale = 1.0
        self._pan = [0.0, 0.0]
        self._fitted = True
        self.update()

    def zoom(self, factor: float, at: Optional[QtCore.QPoint] = None) -> None:
        before = self.layout()
        anchor = at or QtCore.QPoint(self.width() // 2, self.height() // 2)
        sheet_point = before.to_sheet(anchor.x(), anchor.y())
        self._scale = max(MIN_SCALE, min(MAX_SCALE, self._scale * factor))
        after = self.layout()
        moved = after.to_device(*sheet_point)
        self._pan[0] += anchor.x() - moved[0]
        self._pan[1] += anchor.y() - moved[1]
        self.update()

    # -------------------------------------------------------------- painting

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor(C.window))
        sheet = self.sheet()
        if self.doc is None or sheet is None:
            painter.setPen(QtGui.QColor(C.text_dim))
            painter.drawText(self.rect(), QtCore.Qt.AlignCenter,
                             "No sheet")
            painter.end()
            return
        sheetpaint.paint(painter, self.doc, sheet, self.layout(),
                         model_properties=self.model_properties,
                         with_paper=True,
                         selection=self.selected_views
                         + self.selected_annotations,
                         on_screen=True,
                         hidden=getattr(self.placer, "hidden", ()) or ())
        if self.placer is not None:
            self.placer.paint(painter, self.layout())
        else:
            self._paint_grips(painter)
        painter.end()

    # -- a dimension's legs ---------------------------------------------------

    GRIP_KINDS = (dwg.LINEAR, dwg.ALIGNED, dwg.ANGULAR, dwg.ORDINATE)

    def _grip_note(self) -> Optional[Annotation]:
        """The one dimension selected, if its legs can be taken hold of."""
        sheet = self.sheet()
        if sheet is None or len(self.selected_annotations) != 1:
            return None
        note = next((a for a in sheet.annotations
                     if a.id == self.selected_annotations[0]), None)
        if note is None or note.kind not in self.GRIP_KINDS \
                or len(note.points) < 2:
            return None
        return note

    def _grips(self):
        note = self._grip_note()
        sheet = self.sheet()
        view = sheet.view(note.view) if note is not None else None
        if view is None:
            return []
        layout = self.layout()
        return [(note, index, QtCore.QPointF(*layout.to_device(
            view.x + p[0], view.y + p[1])))
            for index, p in enumerate(note.points)]

    def _paint_grips(self, painter: QtGui.QPainter) -> None:
        painter.save()
        painter.setPen(QtGui.QPen(QtGui.QColor(sheetpaint.SELECTED), 1.0))
        painter.setBrush(QtGui.QColor("#ffffff"))
        for _note, _index, at in self._grips():
            painter.drawRect(QtCore.QRectF(at.x() - 3.5, at.y() - 3.5,
                                           7.0, 7.0))
        painter.restore()

    def _grip_at(self, pos: QtCore.QPoint) -> Optional[Tuple[int, int]]:
        for note, index, at in self._grips():
            if abs(at.x() - pos.x()) <= 6 and abs(at.y() - pos.y()) <= 6:
                return (note.id, index)
        return None

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self.update()

    # -------------------------------------------------------------- picking

    def view_at(self, x: float, y: float) -> Optional[int]:
        """The topmost view whose lines pass near this screen point."""
        sheet = self.sheet()
        if sheet is None:
            return None
        layout = self.layout()
        point = layout.to_sheet(x, y)
        tolerance = PICK_PIXELS / max(layout.scale, 1e-9)

        best, best_distance = None, tolerance
        for view in reversed(sheet.views):
            local = (point[0] - view.x, point[1] - view.y)
            projection = view.projection
            if projection is None:
                if abs(local[0]) < 20.0 and abs(local[1]) < 20.0:
                    return view.id
                continue
            box = projection.box
            # a quick reject on the box, so a big sheet stays responsive
            if not (box[0] - tolerance <= local[0] <= box[2] + tolerance
                    and box[1] - tolerance <= local[1] <= box[3] + tolerance):
                continue
            for line in projection.lines:
                for a, b in zip(line.points, line.points[1:]):
                    distance = _point_to_segment(local, a, b)
                    if distance < best_distance:
                        best, best_distance = view.id, distance
        if best is not None:
            return best
        # Nothing drawn right under the cursor: the view whose box it is
        # in, the smallest if several, so a view can be taken hold of
        # anywhere inside it, as in Inventor, not only on its lines
        smallest = None
        for view in sheet.views:
            projection = view.projection
            if projection is None or not projection.box:
                continue
            box = projection.box
            local = (point[0] - view.x, point[1] - view.y)
            if box[0] <= local[0] <= box[2] and box[1] <= local[1] <= box[3]:
                area = (box[2] - box[0]) * (box[3] - box[1])
                if smallest is None or area < smallest[0]:
                    smallest = (area, view.id)
        return smallest[1] if smallest else None

    def annotation_at(self, x: float, y: float) -> Optional[int]:
        sheet = self.sheet()
        if sheet is None:
            return None
        layout = self.layout()
        point = layout.to_sheet(x, y)
        tolerance = PICK_PIXELS / max(layout.scale, 1e-9)
        for note in reversed(sheet.annotations):
            view = sheet.view(note.view)
            if view is None or not note.points:
                continue
            # taken hold of by its text, wherever that was put
            label = dwg.label_at(note)
            where = (view.x + label[0], view.y + label[1])
            if math.hypot(where[0] - point[0],
                          where[1] - point[1]) < tolerance * 2.2:
                return note.id
        return None

    def _move_grip(self, pos: QtCore.QPoint) -> None:
        sheet = self.sheet()
        note_id, index = self._drag_grip
        note = next((a for a in sheet.annotations if a.id == note_id),
                    None) if sheet else None
        view = sheet.view(note.view) if note is not None else None
        if view is None or index >= len(note.points):
            return
        point = self.layout().to_sheet(pos.x(), pos.y())
        landed = self.grip_snap(view.id, point) if self.grip_snap else None
        local = landed if landed is not None else (point[0] - view.x,
                                                   point[1] - view.y)
        if list(local) != list(note.points[index]):
            note.points[index] = [float(local[0]), float(local[1])]
            self._moved = True
        self.update()

    def parts_list_at(self, x: float, y: float) -> Optional[int]:
        """The parts list under this screen point, if any.

        Picked on the whole table rather than on its lines: a table is a
        block of text and grabbing it anywhere is what anybody expects.
        """
        sheet = self.sheet()
        if sheet is None:
            return None
        point = self.layout().to_sheet(x, y)
        for table in reversed(sheet.parts_lists):
            box = table.box()
            if (box[0] <= point[0] <= box[2]
                    and box[1] <= point[1] <= box[3]):
                return table.id
        return None

    # --------------------------------------------------------------- mouse

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        self.setFocus()
        pos = event.position().toPoint()
        self._last_pos = pos

        if event.button() in (QtCore.Qt.MiddleButton,):
            self._panning = True
            return
        if self.placer is not None:
            # placing views: the placer has the mouse
            placer = self.placer
            point = self.layout().to_sheet(pos.x(), pos.y())
            device = QtCore.QPointF(pos)
            if event.button() == QtCore.Qt.LeftButton:
                placer.press(point, device)
            elif event.button() == QtCore.Qt.RightButton:
                if not placer.right_click(point, device):
                    self.placing_finished.emit()
            self.update()
            return
        if event.button() == QtCore.Qt.RightButton:
            self.context_requested.emit(pos)
            return
        if event.button() != QtCore.Qt.LeftButton:
            return

        sheet = self.sheet()
        if sheet is None:
            return
        layout = self.layout()
        grip = self._grip_at(pos)
        if grip is not None:
            # a leg of the selected dimension, to be dropped on another point
            self._drag_grip = grip
            self._moved = False
            return
        note_id = self.annotation_at(pos.x(), pos.y())
        table_id = None if note_id is not None else self.parts_list_at(
            pos.x(), pos.y())
        view_id = None if (note_id is not None or table_id is not None) \
            else self.view_at(pos.x(), pos.y())

        additive = bool(event.modifiers() & QtCore.Qt.ControlModifier)
        if not additive:
            self.selected_views = []
            self.selected_annotations = []

        if table_id is not None:
            self.selected_annotations.append(table_id)
            table = sheet.parts_list(table_id)
            self._drag_table = table_id
            self._origin = (table.x, table.y)
        elif note_id is not None:
            self.selected_annotations.append(note_id)
            note = next(a for a in sheet.annotations if a.id == note_id)
            self._drag_note = note_id
            self._origin = tuple(note.offset[:2])
        elif view_id is not None:
            self.selected_views.append(view_id)
            view = sheet.view(view_id)
            self._drag_view = view_id
            self._origin = (view.x, view.y)
        self._drag_from = layout.to_sheet(pos.x(), pos.y())
        self._moved = False
        self.selection_changed.emit()
        self.update()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        pos = event.position().toPoint()
        if self._panning:
            self._pan[0] += pos.x() - self._last_pos.x()
            self._pan[1] += pos.y() - self._last_pos.y()
            self._last_pos = pos
            self.update()
            return
        self._last_pos = pos

        if self._drag_grip is not None:
            self._move_grip(pos)
            return
        if self.placer is not None:
            point = self.layout().to_sheet(pos.x(), pos.y())
            device = QtCore.QPointF(pos)
            held = bool(event.buttons() & QtCore.Qt.LeftButton)
            self.placer.move(point, device, held)
            if self.placer is not None:
                self.setCursor(self.placer.cursor_shape(point, device))
            self.update()
            return
        if self._drag_from is None:
            return
        sheet = self.sheet()
        if sheet is None:
            return
        now = self.layout().to_sheet(pos.x(), pos.y())
        dx = now[0] - self._drag_from[0]
        dy = now[1] - self._drag_from[1]
        if dx or dy:
            self._moved = True

        if self._drag_view is not None and self._origin is not None:
            view = sheet.view(self._drag_view)
            if view is not None:
                self._move_view(sheet, view, dx, dy)
                self.update()
        elif self._drag_note is not None and self._origin is not None:
            note = next((a for a in sheet.annotations
                         if a.id == self._drag_note), None)
            if note is not None:
                note.offset = [self._origin[0] + dx, self._origin[1] + dy]
                self.update()
        elif self._drag_table is not None and self._origin is not None:
            table = sheet.parts_list(self._drag_table)
            if table is not None:
                table.x = self._origin[0] + dx
                table.y = self._origin[1] + dy
                self.update()

    def _move_view(self, sheet: Sheet, view: View,
                   dx: float, dy: float) -> None:
        """Move a view, keeping it in line with whatever it is aligned to.

        A projected view is only meaningful directly beside or under its
        parent: let it wander off the axis and it stops being the view it
        says it is.  So it slides along its one axis, and the parent takes
        its children with it.
        """
        start = self._origin
        if view.kind == dwg.PROJECTED and view.parent:
            parent = sheet.view(view.parent)
            if parent is not None:
                along_x = abs(start[0] - parent.x) >= abs(start[1] - parent.y)
                diagonal = (abs(start[0] - parent.x) > 1e-6
                            and abs(start[1] - parent.y) > 1e-6)
                if not diagonal:
                    if along_x:
                        view.x, view.y = start[0] + dx, parent.y
                    else:
                        view.x, view.y = parent.x, start[1] + dy
                    return
        moved_x, moved_y = start[0] + dx, start[1] + dy
        shift = (moved_x - view.x, moved_y - view.y)
        view.x, view.y = moved_x, moved_y
        for child in _descendants(sheet, view.id):
            child.x += shift[0]
            child.y += shift[1]

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MiddleButton:
            self._panning = False
            return
        if self.placer is not None:
            if event.button() == QtCore.Qt.LeftButton:
                pos = event.position().toPoint()
                self.placer.release(self.layout().to_sheet(pos.x(), pos.y()),
                                    QtCore.QPointF(pos))
                self.update()
            return
        if self._drag_grip is not None:
            grip, self._drag_grip = self._drag_grip, None
            if self._moved:
                self.grip_moved.emit(*grip)
            return
        # a click that picks something moves nothing, and is not a change
        grabbed = (self._drag_view is not None or self._drag_note is not None
                   or self._drag_table is not None)
        dragged = self._drag_from is not None and grabbed and self._moved
        self._drag_view = None
        self._drag_note = None
        self._drag_table = None
        self._drag_from = None
        self._origin = None
        if dragged:
            self.changed.emit()

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent) -> None:
        # the wheel clicked twice fits the sheet, as Inventor's does
        if event.button() == QtCore.Qt.MiddleButton:
            self.fit()
            return
        if self.placer is not None:
            # the second of two quick clicks is still a click: the cube's
            # arrow pressed four times turns the view four times
            if event.button() == QtCore.Qt.LeftButton:
                pos = event.position().toPoint()
                self.placer.press(self.layout().to_sheet(pos.x(), pos.y()),
                                  QtCore.QPointF(pos))
                self.update()
            return
        pos = event.position().toPoint()
        note_id = self.annotation_at(pos.x(), pos.y())
        if note_id is not None:
            self.annotation_activated.emit(note_id)
            return
        view_id = self.view_at(pos.x(), pos.y())
        if view_id is not None:
            self.view_activated.emit(view_id)

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        steps = event.angleDelta().y() / 120.0
        if abs(steps) < 1e-6:
            return
        self.zoom(ZOOM_STEP ** steps, event.position().toPoint())

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() == QtCore.Qt.Key_Escape and self.placer is not None:
            self.placing_finished.emit()
            return
        if event.key() == QtCore.Qt.Key_Escape:
            self.selected_views = []
            self.selected_annotations = []
            self.selection_changed.emit()
            self.update()
            return
        super().keyPressEvent(event)

    # -------------------------------------------------------------- helpers

    def select(self, view_ids: Sequence[int] = (),
               annotation_ids: Sequence[int] = ()) -> None:
        self.selected_views = list(view_ids)
        self.selected_annotations = list(annotation_ids)
        self.selection_changed.emit()
        self.update()

    def grab_sheet(self) -> QtGui.QPixmap:
        return self.grab()


def _descendants(sheet: Sheet, view_id: int) -> List[View]:
    out: List[View] = []
    queue = [view_id]
    while queue:
        current = queue.pop()
        for child in sheet.children_of(current):
            out.append(child)
            queue.append(child.id)
    return out


def _point_to_segment(point: Sequence[float], a: Sequence[float],
                      b: Sequence[float]) -> float:
    px, py = point[0] - a[0], point[1] - a[1]
    bx, by = b[0] - a[0], b[1] - a[1]
    length = bx * bx + by * by
    if length < 1e-18:
        return math.hypot(px, py)
    t = max(0.0, min(1.0, (px * bx + py * by) / length))
    return math.hypot(px - bx * t, py - by * t)
