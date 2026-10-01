"""Drawing a sheet, for whatever it is going onto.

One routine paints a sheet, and it is given a QPainter.  On screen that
painter belongs to a widget; for a PDF it belongs to a QPdfWriter, for an
SVG to a QSvgGenerator, and for paper to a QPrinter.  Because it is the
same routine every time, what comes out of the printer is what was on the
screen - and the PDF is real vector output at true size, not a picture of
the window.

Everything here works in sheet millimetres with Y upwards, the way paper
does.  ``Layout`` holds the one transform that turns that into whatever the
painter underneath is using.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from PySide6 import QtCore, QtGui

from ..core import drawing as dwg
from ..core import hlr
from .theme import C
from ..core.drawing import (
    ANGULAR, Annotation, BALLOON, DETAIL, DIAMETER, DIMENSIONS,
    DrawingDocument, LEADER, NOTE, PartsList, RADIUS, SECTION, Sheet, View,
)

PAPER = "#ffffff"
# The chrome's accent is a light silver, which is invisible on paper.  What
# is picked on a sheet gets a colour of its own, dark enough to read white.
SELECTED = C.paper_selected
PAPER_EDGE = "#b8bec6"
SICK = "#c8501e"
STALE = "#b08000"

# A hairline still has to be visible on screen when the sheet is zoomed
# out, so every pen is given a floor in device pixels.
MIN_PEN = 0.8


@dataclass
class Layout:
    """Where the sheet sits on the device, and how big."""

    scale: float = 1.0          # device units per sheet millimetre
    offset_x: float = 0.0       # device units
    offset_y: float = 0.0
    height: float = 297.0       # sheet height, for the Y flip

    def to_device(self, x: float, y: float) -> Tuple[float, float]:
        return (self.offset_x + x * self.scale,
                self.offset_y + (self.height - y) * self.scale)

    def to_sheet(self, x: float, y: float) -> Tuple[float, float]:
        return ((x - self.offset_x) / self.scale,
                self.height - (y - self.offset_y) / self.scale)

    def pen_width(self, millimetres: float) -> float:
        return max(MIN_PEN, millimetres * self.scale)


def fit(sheet_width: float, sheet_height: float,
        device_width: float, device_height: float,
        margin: float = 12.0) -> Layout:
    """A layout that puts the whole sheet on the device with room round it."""
    usable_w = max(1.0, device_width - margin * 2)
    usable_h = max(1.0, device_height - margin * 2)
    scale = min(usable_w / max(sheet_width, 1e-6),
                usable_h / max(sheet_height, 1e-6))
    width, height = sheet_width * scale, sheet_height * scale
    return Layout(scale=scale,
                  offset_x=(device_width - width) / 2.0,
                  offset_y=(device_height - height) / 2.0,
                  height=sheet_height)


def exact(sheet_height: float, scale: float = 1.0) -> Layout:
    """A layout at true size, for print and PDF: one millimetre is one."""
    return Layout(scale=scale, offset_x=0.0, offset_y=0.0,
                  height=sheet_height)


# --------------------------------------------------------------- the painting


def paint(painter: QtGui.QPainter, doc: DrawingDocument, sheet: Sheet,
          layout: Layout, model_properties: Optional[Dict[str, str]] = None,
          with_paper: bool = True, selection: Sequence[int] = (),
          on_screen: bool = False) -> None:
    """Draw one sheet, whole.

    ``model_properties`` may be a dict or a function of the sheet.  The
    canvas has several sheets to hand and resolves each one's model lazily;
    an exporter usually has just the one.  Taking either saves every caller
    from knowing which kind the next one wants.
    """
    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
    painter.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
    if callable(model_properties):
        model_properties = model_properties(sheet)
    width, height = sheet.extent()

    if with_paper:
        _paper(painter, layout, width, height)
    _border(painter, doc, sheet, layout, width, height)
    _title_block(painter, doc, sheet, layout, width, height, model_properties)

    for view in sheet.views:
        _view(painter, doc, sheet, view, layout,
              selected=view.id in selection, on_screen=on_screen)
    for note in sheet.annotations:
        _annotation(painter, doc, sheet, note, layout,
                    selected=note.id in selection)
    for table in sheet.parts_lists:
        _parts_list(painter, doc, sheet, table, layout,
                    selected=table.id in selection)


def _pen(layout: Layout, style: dwg.Style, dashed: bool = False,
         colour: Optional[str] = None) -> QtGui.QPen:
    pen = QtGui.QPen(QtGui.QColor(colour or style.colour))
    pen.setWidthF(layout.pen_width(style.line_width))
    pen.setCapStyle(QtCore.Qt.RoundCap)
    pen.setJoinStyle(QtCore.Qt.RoundJoin)
    if dashed:
        # dashes are given in multiples of the pen width, so they keep their
        # proportions at any zoom
        pen.setDashPattern([4.0, 3.0])
    return pen


def _paper(painter: QtGui.QPainter, layout: Layout,
           width: float, height: float) -> None:
    x0, y0 = layout.to_device(0.0, height)
    x1, y1 = layout.to_device(width, 0.0)
    rect = QtCore.QRectF(x0, y0, x1 - x0, y1 - y0)
    painter.fillRect(rect, QtGui.QColor(PAPER))
    pen = QtGui.QPen(QtGui.QColor(PAPER_EDGE))
    pen.setWidthF(1.0)
    painter.setPen(pen)
    painter.setBrush(QtCore.Qt.NoBrush)
    painter.drawRect(rect)


def _border(painter: QtGui.QPainter, doc: DrawingDocument, sheet: Sheet,
            layout: Layout, width: float, height: float) -> None:
    border = doc.borders.get(sheet.border)
    if border is None:
        return
    style = doc.styles.get("border", dwg.Style())
    painter.setPen(_pen(layout, style))
    painter.setBrush(QtCore.Qt.NoBrush)

    x0, y0, x1, y1 = border.frame(width, height)
    a = layout.to_device(x0, y1)
    b = layout.to_device(x1, y0)
    painter.drawRect(QtCore.QRectF(a[0], a[1], b[0] - a[0], b[1] - a[1]))

    if not border.zones:
        return
    # zone letters up the sides and numbers along the top and bottom, the
    # way a big sheet is referred to over the phone
    columns = max(1, int((x1 - x0) // 105) or 1)
    rows = max(1, int((y1 - y0) // 105) or 1)
    for i in range(columns):
        cx = x0 + (x1 - x0) * (i + 0.5) / columns
        for cy, anchor in ((y1 + border.zone_size / 2.0, 0),
                           (y0 - border.zone_size / 2.0, 1)):
            _text(painter, layout, str(i + 1), cx, cy, 2.5, align="centre")
    for j in range(rows):
        cy = y0 + (y1 - y0) * (j + 0.5) / rows
        letter = chr(ord("A") + j)
        for cx in (x0 - border.zone_size / 2.0, x1 + border.zone_size / 2.0):
            _text(painter, layout, letter, cx, cy, 2.5, align="centre")


def _title_block(painter: QtGui.QPainter, doc: DrawingDocument, sheet: Sheet,
                 layout: Layout, width: float, height: float,
                 model_properties: Optional[Dict[str, str]]) -> None:
    block = doc.title_blocks.get(sheet.title_block)
    if block is None:
        return
    border = doc.borders.get(sheet.border)
    x1, y0 = ((border.frame(width, height)[2], border.frame(width, height)[1])
              if border else (width - 10.0, 10.0))
    origin = (x1 - block.width, y0)

    style = doc.styles.get("border", dwg.Style())
    painter.setPen(_pen(layout, style))
    for line in block.lines:
        a = layout.to_device(origin[0] + line[0], origin[1] + line[1])
        b = layout.to_device(origin[0] + line[2], origin[1] + line[3])
        painter.drawLine(QtCore.QPointF(*a), QtCore.QPointF(*b))

    values = dwg.properties_for(doc, sheet, model_properties)
    text_style = doc.styles.get("text", dwg.Style())
    painter.setPen(QtGui.QPen(QtGui.QColor(text_style.colour)))
    for item in block.fields:
        content = dwg.resolve_field(item, values, sheet)
        if not content:
            continue
        _text(painter, layout, content,
              origin[0] + item.x, origin[1] + item.y,
              item.height, bold=item.bold, align=item.align)


def _em_for(font: QtGui.QFont, cap_height: float) -> float:
    """The font size whose capitals stand ``cap_height`` device units tall.

    A drawing specifies lettering by the height of a capital, which is not
    the font's size - it is roughly seven tenths of it, and exactly what
    depends on the typeface.  Measuring once and correcting beats guessing.
    """
    probe = QtGui.QFont(font)
    probe.setPixelSize(100)
    measured = QtGui.QFontMetricsF(probe).capHeight()
    ratio = (measured / 100.0) if measured > 1e-6 else 0.7
    return max(1.0, cap_height / ratio)


def _text(painter: QtGui.QPainter, layout: Layout, text: str,
          x: float, y: float, height_mm: float, bold: bool = False,
          align: str = "left", colour: Optional[str] = None) -> None:
    """Text placed by its baseline-left corner, in sheet millimetres."""
    if not text:
        return
    device = layout.to_device(x, y)
    font = painter.font()
    # Pixels, not points.  A point size is interpreted against the paint
    # device's own DPI, so the same call gives one size on a 96 dpi screen
    # and twelve times that in a 1200 dpi PDF - the drawing would print with
    # the lettering swallowing the sheet.  A pixel size is what the layout
    # already speaks in.
    font.setPixelSize(max(1, int(round(_em_for(font, height_mm
                                              * layout.scale)))))
    font.setBold(bold)
    painter.setFont(font)
    if colour:
        painter.setPen(QtGui.QPen(QtGui.QColor(colour)))

    metrics = QtGui.QFontMetricsF(font)
    dx = 0.0
    if align == "centre":
        dx = -metrics.horizontalAdvance(text) / 2.0
    elif align == "right":
        dx = -metrics.horizontalAdvance(text)
    painter.drawText(QtCore.QPointF(device[0] + dx, device[1]), text)


# ------------------------------------------------------------------- views


KIND_STYLES = {
    hlr.VISIBLE: ("visible", False),
    hlr.OUTLINE: ("outline", False),
    hlr.SMOOTH: ("smooth", False),
    hlr.HIDDEN: ("hidden", True),
}

# hidden first so a visible line drawn over one wins
DRAW_ORDER = (hlr.HIDDEN, hlr.SMOOTH, hlr.OUTLINE, hlr.VISIBLE)


def _view(painter: QtGui.QPainter, doc: DrawingDocument, sheet: Sheet,
          view: View, layout: Layout, selected: bool = False,
          on_screen: bool = False) -> None:
    projection = view.projection
    if projection is None or not projection.lines:
        if on_screen:
            _placeholder(painter, doc, view, layout)
        return

    # the fill goes down first, so every edge is drawn over it rather than
    # half-buried by it
    _hatch(painter, doc, view, layout)

    for kind in DRAW_ORDER:
        lines = projection.of_kind(kind)
        if not lines:
            continue
        style_name, dashed = KIND_STYLES.get(kind, ("visible", False))
        style = doc.styles.get(style_name, dwg.Style())
        colour = STALE if view.stale else None
        painter.setPen(_pen(layout, style, dashed, colour))
        for line in lines:
            path = QtGui.QPainterPath()
            first = layout.to_device(view.x + line.points[0][0],
                                     view.y + line.points[0][1])
            path.moveTo(QtCore.QPointF(*first))
            for x, y in line.points[1:]:
                path.lineTo(QtCore.QPointF(*layout.to_device(view.x + x,
                                                             view.y + y)))
            painter.drawPath(path)

    if view.kind == SECTION and len(view.cut) >= 4:
        _cut_line(painter, doc, sheet, view, layout)
    if view.kind == DETAIL and len(view.centre) >= 2:
        _detail_circle(painter, doc, sheet, view, layout)

    _view_label(painter, doc, sheet, view, layout)
    if selected and on_screen:
        _highlight(painter, view, layout)


def _hatch(painter: QtGui.QPainter, doc: DrawingDocument, view: View,
           layout: Layout) -> None:
    """Fill the faces a section cut through.

    Where the lines go is worked out in ``drawing.hatch_lines`` rather than
    here, so that the screen, the PDF and the DXF all fill a face with the
    same lines.  A solid fill is the one exception: there is nothing to work
    out, so the loops go straight to the brush.
    """
    projection = view.projection
    loops = projection.of_kind(hlr.CUT) if projection else []
    if not loops:
        return
    pattern = dwg.hatch_patterns().get(projection.hatch or "steel")
    if pattern is None or (not pattern.angles and not pattern.solid):
        return

    style = doc.styles.get("hatch", dwg.Style())
    colour = QtGui.QColor(STALE if view.stale else style.colour)

    if pattern.solid:
        path = QtGui.QPainterPath()
        path.setFillRule(QtCore.Qt.OddEvenFill)
        for loop in loops:
            piece = QtGui.QPainterPath()
            piece.moveTo(QtCore.QPointF(*layout.to_device(
                view.x + loop.points[0][0], view.y + loop.points[0][1])))
            for x, y in loop.points[1:]:
                piece.lineTo(QtCore.QPointF(*layout.to_device(view.x + x,
                                                              view.y + y)))
            piece.closeSubpath()
            path.addPath(piece)
        if not path.isEmpty():
            painter.fillPath(path, QtGui.QBrush(colour))
        return

    painter.setPen(_pen(layout, style, colour=colour.name()))
    for a, b in dwg.hatch_lines([loop.points for loop in loops], pattern):
        painter.drawLine(
            QtCore.QPointF(*layout.to_device(view.x + a[0], view.y + a[1])),
            QtCore.QPointF(*layout.to_device(view.x + b[0], view.y + b[1])))


def _placeholder(painter: QtGui.QPainter, doc: DrawingDocument, view: View,
                 layout: Layout) -> None:
    """A view that could not be drawn still has to be findable and movable."""
    pen = QtGui.QPen(QtGui.QColor(SICK))
    pen.setWidthF(1.2)
    pen.setStyle(QtCore.Qt.DashLine)
    painter.setPen(pen)
    painter.setBrush(QtCore.Qt.NoBrush)
    half = 20.0
    a = layout.to_device(view.x - half, view.y + half)
    b = layout.to_device(view.x + half, view.y - half)
    painter.drawRect(QtCore.QRectF(a[0], a[1], b[0] - a[0], b[1] - a[1]))
    _text(painter, layout, view.error or "not drawn",
          view.x, view.y, 3.0, align="centre", colour=SICK)


def _highlight(painter: QtGui.QPainter, view: View, layout: Layout) -> None:
    box = view.projection.box if view.projection else (-20, -20, 20, 20)
    pen = QtGui.QPen(QtGui.QColor(SELECTED))
    pen.setWidthF(1.4)
    pen.setStyle(QtCore.Qt.DashLine)
    painter.setPen(pen)
    painter.setBrush(QtCore.Qt.NoBrush)
    pad = 3.0
    a = layout.to_device(view.x + box[0] - pad, view.y + box[3] + pad)
    b = layout.to_device(view.x + box[2] + pad, view.y + box[1] - pad)
    painter.drawRect(QtCore.QRectF(a[0], a[1], b[0] - a[0], b[1] - a[1]))


def _view_label(painter: QtGui.QPainter, doc: DrawingDocument, sheet: Sheet,
                view: View, layout: Layout) -> None:
    if not view.label_visible and not view.scale_visible:
        return
    box = view.projection.box if view.projection else (0, 0, 0, 0)
    style = doc.styles.get("text", dwg.Style())
    # A label belongs under the view, and so does a dimension dragged down
    # there.  Whoever is lower wins the space and the label steps below it,
    # rather than the two being written over each other.
    y = min(view.y + box[1], _lowest(sheet, view) + view.y) - 6.0
    if view.label_visible and view.kind in (SECTION, DETAIL):
        _text(painter, layout, view.label, view.x, y, 5.0, bold=True,
              align="centre", colour=style.colour)
        y -= 6.0
    elif view.label_visible and view.name:
        _text(painter, layout, view.name, view.x, y, 3.5, align="centre",
              colour=style.colour)
        y -= 5.0
    if view.scale_visible:
        scale = doc.view_scale(sheet, view)
        _text(painter, layout, dwg.scale_text(scale), view.x, y, 3.0,
              align="centre", colour=style.colour)


def _lowest(sheet: Sheet, view: View) -> float:
    """How far below a view its own annotations reach, in view millimetres."""
    box = view.projection.box if view.projection else (0.0, 0.0, 0.0, 0.0)
    floor = box[1]
    for note in sheet.annotations_for(view.id):
        offset = (list(note.offset) + [0.0, 0.0])[:2]
        for point in note.points:
            if len(point) >= 2:
                floor = min(floor, point[1] + offset[1])
    return floor


def _cut_line(painter: QtGui.QPainter, doc: DrawingDocument, sheet: Sheet,
              view: View, layout: Layout) -> None:
    """The A-A line drawn across the parent, with its arrows."""
    parent = sheet.view(view.parent)
    if parent is None:
        return
    style = doc.styles.get("section", dwg.Style())
    pen = _pen(layout, style)
    pen.setDashPattern([10.0, 3.0, 2.0, 3.0])
    painter.setPen(pen)

    x1, y1, x2, y2 = view.cut[:4]
    a = layout.to_device(parent.x + x1, parent.y + y1)
    b = layout.to_device(parent.x + x2, parent.y + y2)
    painter.drawLine(QtCore.QPointF(*a), QtCore.QPointF(*b))

    painter.setPen(_pen(layout, style))
    for point, sign in ((a, 1.0), (b, -1.0)):
        _arrow_head(painter, point, (b[0] - a[0]) * sign, (b[1] - a[1]) * sign,
                    layout.pen_width(style.arrow_size) * 1.6)
    for point in (a, b):
        pass
    if view.letter:
        for point in (a, b):
            sheet_point = layout.to_sheet(point[0], point[1])
            _text(painter, layout, view.letter,
                  sheet_point[0], sheet_point[1] + 4.0, 5.0, bold=True,
                  align="centre", colour=style.colour)


def _detail_circle(painter: QtGui.QPainter, doc: DrawingDocument, sheet: Sheet,
                   view: View, layout: Layout) -> None:
    parent = sheet.view(view.parent)
    if parent is None:
        return
    style = doc.styles.get("section", dwg.Style())
    pen = _pen(layout, style)
    pen.setStyle(QtCore.Qt.DashLine)
    painter.setPen(pen)
    painter.setBrush(QtCore.Qt.NoBrush)
    centre = layout.to_device(parent.x + view.centre[0],
                              parent.y + view.centre[1])
    radius = view.radius * layout.scale
    painter.drawEllipse(QtCore.QPointF(*centre), radius, radius)
    if view.letter:
        _text(painter, layout, view.letter,
              parent.x + view.centre[0],
              parent.y + view.centre[1] + view.radius + 3.0,
              4.0, bold=True, align="centre", colour=style.colour)


def _balloon(painter, doc, sheet, view, note, layout, at, colour) -> None:
    """An item number in a bubble, on a leader pointing at the part.

    The number comes from the sheet's parts list rather than from the
    balloon, so the two cannot drift apart: renumber the list and every
    balloon on the sheet says the new number on the next repaint.
    """
    if len(note.points) < 1:
        return
    style = doc.styles.get("text", dwg.Style())
    tip = note.points[0]
    ox, oy = (note.offset + [0.0, 0.0])[:2]
    seat = (tip[0] + ox, tip[1] + oy)

    number = str(note.text or doc.balloon_number(sheet, note) or "?")
    # wide enough for the number it actually holds, so "12" does not sit
    # squeezed against the bubble the way a fixed radius makes it
    radius = max(3.6, 2.0 + 1.5 * len(number))

    a, b = at(tip), at(seat)
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    painter.setPen(_pen(layout, doc.styles.get("dimension", dwg.Style()),
                        colour=colour))
    painter.setBrush(QtCore.Qt.NoBrush)
    if length > 1e-6:
        # stop the leader at the bubble rather than running under it
        stop = radius * layout.scale
        if length > stop:
            painter.drawLine(
                QtCore.QPointF(a[0], a[1]),
                QtCore.QPointF(b[0] - dx / length * stop,
                               b[1] - dy / length * stop))
        _arrow_head(painter, a, -dx, -dy,
                    layout.pen_width(style.arrow_size) * 1.2)

    centre = QtCore.QPointF(*at(seat))
    device_r = radius * layout.scale
    painter.setBrush(QtGui.QBrush(QtGui.QColor(PAPER)))
    if note.shape == "hex":
        points = [QtCore.QPointF(
            centre.x() + device_r * math.cos(math.radians(30 + 60 * i)),
            centre.y() + device_r * math.sin(math.radians(30 + 60 * i)))
            for i in range(6)]
        painter.drawPolygon(QtGui.QPolygonF(points))
    elif note.shape == "square":
        painter.drawRect(QtCore.QRectF(centre.x() - device_r,
                                       centre.y() - device_r,
                                       device_r * 2, device_r * 2))
    else:
        painter.drawEllipse(centre, device_r, device_r)
    painter.setBrush(QtCore.Qt.NoBrush)

    # sat on the bubble's middle, not its baseline, so the digits look
    # centred in it rather than resting on the bottom of the circle
    _text(painter, layout, number, view.x + seat[0],
          view.y + seat[1] - style.text_height * 0.36, style.text_height,
          bold=True, align="centre", colour=colour)


def _parts_list(painter: QtGui.QPainter, doc: DrawingDocument, sheet: Sheet,
                table: PartsList, layout: Layout,
                selected: bool = False) -> None:
    """The table of what the assembly is made of.

    Drawn from the bottom up with the heading on top, the way a parts list
    sits above a title block: adding a part pushes the table upwards into
    empty paper instead of down through the block.
    """
    rows = list(table.rows)
    style = doc.styles.get("border", dwg.Style())
    text_style = doc.styles.get("text", dwg.Style())
    colour = SELECTED if selected else style.colour
    x0, y0 = table.origin()
    lines = len(rows) + (1 if table.heading else 0)
    if lines <= 0:
        return
    width = table.width()
    height = lines * table.row_height

    painter.setPen(_pen(layout, style, colour=colour))
    painter.setBrush(QtCore.Qt.NoBrush)
    a = layout.to_device(x0, y0 + height)
    b = layout.to_device(x0 + width, y0)
    painter.drawRect(QtCore.QRectF(a[0], a[1], b[0] - a[0], b[1] - a[1]))

    thin = doc.styles.get("dimension", dwg.Style())
    painter.setPen(_pen(layout, thin, colour=colour))
    for i in range(1, lines):
        y = y0 + i * table.row_height
        painter.drawLine(QtCore.QPointF(*layout.to_device(x0, y)),
                         QtCore.QPointF(*layout.to_device(x0 + width, y)))
    x = x0
    for column in table.columns[:-1]:
        x += table.column_width(column)
        painter.drawLine(QtCore.QPointF(*layout.to_device(x, y0)),
                         QtCore.QPointF(*layout.to_device(x, y0 + height)))

    # the first row sits at the bottom, the heading at the top
    for index, row in enumerate(rows):
        y = y0 + (len(rows) - 1 - index) * table.row_height
        _row_text(painter, layout, table, row, x0, y, table.text_height,
                  text_style.colour)
    if table.heading:
        y = y0 + len(rows) * table.row_height
        _row_text(painter, layout, table, None, x0, y, table.text_height,
                  text_style.colour, bold=True)
    if table.error and not rows:
        _text(painter, layout, table.error, x0 + 1.6, y0 + height / 2.0,
              table.text_height, colour=SICK)


def _row_text(painter, layout, table, row, x0: float, y: float,
              height: float, colour: str, bold: bool = False) -> None:
    """One line of a parts list, each value in its own column."""
    from ..core import bom as bom_module

    x = x0
    baseline = y + (table.row_height - height) / 2.0 + height * 0.15
    for column in table.columns:
        span = table.column_width(column)
        text = (bom_module.heading(column) if row is None
                else row.value(column))
        # numbers in the middle of their box, words against the left of it
        if column in ("item", "qty"):
            _text(painter, layout, text, x + span / 2.0, baseline, height,
                  bold=bold, align="centre", colour=colour)
        else:
            _text(painter, layout, _elide(text, span - 3.0, height),
                  x + 1.6, baseline, height, bold=bold, align="left",
                  colour=colour)
        x += span


def _elide(text: str, width_mm: float, height_mm: float) -> str:
    """Cut text that will not fit its column, rather than let it run over."""
    if not text:
        return ""
    # a rough average character width for the font, which is enough: the
    # point is to keep a long description out of the next column, not to
    # fill the box to the last hundredth of a millimetre
    per = height_mm * 0.58
    room = max(1, int(width_mm / per))
    return text if len(text) <= room else text[:max(1, room - 1)] + "…"


def _arrow_head(painter: QtGui.QPainter, tip: Sequence[float],
                dx: float, dy: float, size: float) -> None:
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return
    ux, uy = dx / length, dy / length
    back = (tip[0] - ux * size, tip[1] - uy * size)
    side = (-uy * size * 0.32, ux * size * 0.32)
    head = QtGui.QPolygonF([
        QtCore.QPointF(tip[0], tip[1]),
        QtCore.QPointF(back[0] + side[0], back[1] + side[1]),
        QtCore.QPointF(back[0] - side[0], back[1] - side[1]),
    ])
    painter.setBrush(QtGui.QBrush(painter.pen().color()))
    painter.drawPolygon(head)
    painter.setBrush(QtCore.Qt.NoBrush)


# ------------------------------------------------------------- annotations


def _annotation(painter: QtGui.QPainter, doc: DrawingDocument, sheet: Sheet,
                note: Annotation, layout: Layout,
                selected: bool = False) -> None:
    view = sheet.view(note.view)
    if view is None:
        return
    style = doc.styles.get("dimension", dwg.Style())
    colour = SICK if note.sick else (SELECTED if selected
                                     else style.colour)

    def at(point: Sequence[float]) -> Tuple[float, float]:
        return layout.to_device(view.x + point[0], view.y + point[1])

    painter.setPen(_pen(layout, style, colour=colour))
    painter.setBrush(QtCore.Qt.NoBrush)

    if note.kind in (dwg.CENTRE_MARK, dwg.CENTRELINE):
        _centre_mark(painter, doc, note, layout, at, colour)
        return
    if note.kind in (NOTE, LEADER):
        _note(painter, doc, view, note, layout, at, colour)
        return
    if note.kind == BALLOON:
        _balloon(painter, doc, sheet, view, note, layout, at, colour)
        return
    if note.kind in (RADIUS, DIAMETER):
        _radial(painter, doc, sheet, view, note, layout, at, colour)
        return
    if note.kind == ANGULAR:
        _angular(painter, doc, sheet, view, note, layout, at, colour)
        return
    _linear(painter, doc, sheet, view, note, layout, at, colour)


def _linear(painter, doc, sheet, view, note, layout, at, colour) -> None:
    """A distance, with witness lines back to the points it measures."""
    if len(note.points) < 2:
        return
    style = doc.styles.get("dimension", dwg.Style())
    p1, p2 = note.points[0], note.points[1]
    ox, oy = (note.offset + [0.0, 0.0])[:2]

    if note.kind == dwg.LINEAR:
        # a linear dimension is read along one axis; which one is whichever
        # the two points are further apart on
        if abs(p2[0] - p1[0]) >= abs(p2[1] - p1[1]):
            a, b = (p1[0], p1[1] + oy), (p2[0], p1[1] + oy)
            witness = ((p1[0], p1[1]), (p2[0], p2[1]))
        else:
            a, b = (p1[0] + ox, p1[1]), (p1[0] + ox, p2[1])
            witness = ((p1[0], p1[1]), (p2[0], p2[1]))
    else:
        dx, dy = p2[0] - p1[0], p2[1] - p1[1]
        length = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / length, dx / length
        push = oy
        a = (p1[0] + nx * push, p1[1] + ny * push)
        b = (p2[0] + nx * push, p2[1] + ny * push)
        witness = ((p1[0], p1[1]), (p2[0], p2[1]))

    painter.drawLine(QtCore.QPointF(*at(a)), QtCore.QPointF(*at(b)))
    for start, end in zip(witness, (a, b)):
        painter.drawLine(QtCore.QPointF(*at(start)), QtCore.QPointF(*at(end)))

    size = layout.pen_width(style.arrow_size) * 1.4
    da = at(a)
    db = at(b)
    _arrow_head(painter, da, da[0] - db[0], da[1] - db[1], size)
    _arrow_head(painter, db, db[0] - da[0], db[1] - da[1], size)

    scale = doc.view_scale(sheet, view)
    mid = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
    # the points are in the view's own millimetres; text is placed on the
    # sheet, so it has to be carried across like every line above it
    _text(painter, layout, note.caption(scale),
          view.x + mid[0], view.y + mid[1] + 1.2,
          style.text_height, align="centre", colour=colour)


def _radial(painter, doc, sheet, view, note, layout, at, colour) -> None:
    if len(note.points) < 2:
        return
    style = doc.styles.get("dimension", dwg.Style())
    centre, rim = note.points[0], note.points[1]
    ox, oy = (note.offset + [0.0, 0.0])[:2]
    tip = at(rim)
    tail = at((rim[0] + ox, rim[1] + oy))
    painter.drawLine(QtCore.QPointF(*tip), QtCore.QPointF(*tail))
    _arrow_head(painter, tip, tip[0] - tail[0], tip[1] - tail[1],
                layout.pen_width(style.arrow_size) * 1.4)
    scale = doc.view_scale(sheet, view)
    _text(painter, layout, note.caption(scale),
          view.x + rim[0] + ox, view.y + rim[1] + oy + 1.2,
          style.text_height, align="left", colour=colour)


def _angular(painter, doc, sheet, view, note, layout, at, colour) -> None:
    if len(note.points) < 3:
        return
    style = doc.styles.get("dimension", dwg.Style())
    a, vertex, b = note.points[0], note.points[1], note.points[2]
    radius = max(6.0, (note.offset + [0.0, 12.0])[1])
    start = math.atan2(a[1] - vertex[1], a[0] - vertex[0])
    end = math.atan2(b[1] - vertex[1], b[0] - vertex[0])
    for point in (a, b):
        painter.drawLine(QtCore.QPointF(*at(vertex)), QtCore.QPointF(*at(point)))

    steps = 24
    path = QtGui.QPainterPath()
    sweep = end - start
    while sweep > math.pi:
        sweep -= 2 * math.pi
    while sweep < -math.pi:
        sweep += 2 * math.pi
    for i in range(steps + 1):
        angle = start + sweep * i / steps
        point = at((vertex[0] + math.cos(angle) * radius,
                    vertex[1] + math.sin(angle) * radius))
        if i == 0:
            path.moveTo(QtCore.QPointF(*point))
        else:
            path.lineTo(QtCore.QPointF(*point))
    painter.drawPath(path)

    mid = start + sweep / 2.0
    _text(painter, layout, note.caption(1.0),
          view.x + vertex[0] + math.cos(mid) * (radius + 3.0),
          view.y + vertex[1] + math.sin(mid) * (radius + 3.0),
          style.text_height, align="centre", colour=colour)


def _centre_mark(painter, doc, note, layout, at, colour) -> None:
    if not note.points:
        return
    style = doc.styles.get("centre", dwg.Style())
    pen = _pen(layout, style, colour=colour)
    pen.setDashPattern([8.0, 2.0, 1.5, 2.0])
    painter.setPen(pen)
    centre = note.points[0]
    reach = max(3.0, note.value or 6.0)
    for dx, dy in ((reach, 0.0), (0.0, reach)):
        painter.drawLine(
            QtCore.QPointF(*at((centre[0] - dx, centre[1] - dy))),
            QtCore.QPointF(*at((centre[0] + dx, centre[1] + dy))))


def _note(painter, doc, view, note, layout, at, colour) -> None:
    style = doc.styles.get("text", dwg.Style())
    if not note.points:
        return
    anchor = note.points[0]
    ox, oy = (note.offset + [0.0, 0.0])[:2]
    if note.kind == LEADER and len(note.points) >= 1:
        painter.drawLine(QtCore.QPointF(*at(anchor)),
                         QtCore.QPointF(*at((anchor[0] + ox, anchor[1] + oy))))
        tip = at(anchor)
        tail = at((anchor[0] + ox, anchor[1] + oy))
        _arrow_head(painter, tip, tip[0] - tail[0], tip[1] - tail[1],
                    layout.pen_width(style.arrow_size) * 1.4)
    _text(painter, layout, note.text or "", view.x + anchor[0] + ox,
          view.y + anchor[1] + oy + 1.2, style.text_height, align="left",
          colour=colour)
