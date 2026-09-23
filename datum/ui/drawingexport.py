"""Getting a sheet off the screen: PDF, SVG, DXF and the printer.

Three of these four are the same drawing routine handed a different
QPainter, which is the whole reason a sheet is painted rather than rendered
through the 3D viewport.  What comes out is vector, at true size, and the
same as what was on screen.

DXF is the exception: it has no painter, so the lines are written out
directly.  It is also the only one that loses something - a DXF carries
geometry and text, not pen weights or paper.
"""

from __future__ import annotations

import os
from typing import Callable, List, Optional, Sequence, Union

from PySide6 import QtCore, QtGui, QtPrintSupport
from PySide6.QtSvg import QSvgGenerator

from ..core import drawing as dwg
from ..core import hlr
from ..core.drawing import DrawingDocument, Sheet
from . import sheetpaint

PDF_FILTER = "PDF document (*.pdf)"
SVG_FILTER = "SVG drawing (*.svg)"

MM_PER_INCH = 25.4
PDF_DPI = 1200.0      # fine enough that nothing is quantised out of place

Properties = Union[None, dict, Callable[[Sheet], dict]]


def _sheets(doc: DrawingDocument,
            which: Optional[Sequence[Sheet]]) -> List[Sheet]:
    return list(which) if which is not None else list(doc.sheets)


def _props(properties: Properties, sheet: Sheet):
    """Per-sheet model properties, however the caller chose to supply them."""
    if properties is None:
        return None
    if callable(properties):
        return properties(sheet)
    return properties


def _page_size(writer: QtGui.QPdfWriter, width: float, height: float) -> None:
    size = QtGui.QPageSize(QtCore.QSizeF(width, height),
                           QtGui.QPageSize.Millimeter, "Sheet",
                           QtGui.QPageSize.ExactMatch)
    writer.setPageSize(size)
    writer.setPageMargins(QtCore.QMarginsF(0, 0, 0, 0),
                          QtGui.QPageLayout.Millimeter)


def to_pdf(doc: DrawingDocument, path: str,
           sheets: Optional[Sequence[Sheet]] = None,
           properties: Properties = None) -> str:
    """Every sheet, one page each, at true size."""
    pages = _sheets(doc, sheets)
    if not pages:
        raise ValueError("there are no sheets to export")

    writer = QtGui.QPdfWriter(path)
    writer.setResolution(int(PDF_DPI))
    writer.setTitle(doc.properties.get("Title") or doc.title)
    writer.setCreator("DATUM")

    painter = QtGui.QPainter()
    started = False
    try:
        for index, sheet in enumerate(pages):
            width, height = sheet.extent()
            _page_size(writer, width, height)
            if not started:
                if not painter.begin(writer):
                    raise ValueError("could not write %s"
                                     % os.path.basename(path))
                started = True
            elif not writer.newPage():
                break
            scale = PDF_DPI / MM_PER_INCH
            sheetpaint.paint(painter, doc, sheet,
                             sheetpaint.exact(height, scale),
                             model_properties=_props(properties, sheet),
                             with_paper=False)
    finally:
        if started:
            painter.end()
    return path


def to_svg(doc: DrawingDocument, sheet: Sheet, path: str,
           properties: Properties = None) -> str:
    """One sheet, as vector, at true size."""
    width, height = sheet.extent()
    scale = 96.0 / MM_PER_INCH          # SVG's notional 96 dpi
    generator = QSvgGenerator()
    generator.setFileName(path)
    generator.setSize(QtCore.QSize(int(width * scale), int(height * scale)))
    generator.setViewBox(QtCore.QRectF(0, 0, width * scale, height * scale))
    generator.setTitle(sheet.name)
    generator.setDescription(doc.properties.get("Title") or doc.title)

    painter = QtGui.QPainter()
    if not painter.begin(generator):
        raise ValueError("could not write %s" % os.path.basename(path))
    try:
        sheetpaint.paint(painter, doc, sheet, sheetpaint.exact(height, scale),
                         model_properties=_props(properties, sheet),
                         with_paper=False)
    finally:
        painter.end()
    return path


def to_printer(doc: DrawingDocument, printer: QtPrintSupport.QPrinter,
               sheets: Optional[Sequence[Sheet]] = None,
               properties: Properties = None) -> None:
    """The same drawing, on paper, at 1:1."""
    pages = _sheets(doc, sheets)
    painter = QtGui.QPainter()
    if not pages or not painter.begin(printer):
        return
    try:
        for index, sheet in enumerate(pages):
            if index:
                printer.newPage()
            height = sheet.extent()[1]
            scale = printer.resolution() / MM_PER_INCH
            sheetpaint.paint(painter, doc, sheet,
                             sheetpaint.exact(height, scale),
                             model_properties=_props(properties, sheet),
                             with_paper=False)
    finally:
        painter.end()


def to_image(doc: DrawingDocument, sheet: Sheet, path: str,
             dpi: float = 150.0, properties: Properties = None) -> str:
    """A picture of a sheet - for a thumbnail, or for looking at."""
    width, height = sheet.extent()
    scale = dpi / MM_PER_INCH
    image = QtGui.QImage(max(1, int(width * scale)),
                         max(1, int(height * scale)),
                         QtGui.QImage.Format_RGB32)
    image.fill(QtGui.QColor("#ffffff"))
    painter = QtGui.QPainter(image)
    try:
        sheetpaint.paint(painter, doc, sheet, sheetpaint.exact(height, scale),
                         model_properties=_props(properties, sheet),
                         with_paper=False)
    finally:
        painter.end()
    image.save(path)
    return path


DXF_LAYERS = {"visible": "VIEW_VISIBLE", "hidden": "VIEW_HIDDEN",
              "outline": "VIEW_OUTLINE", "smooth": "VIEW_TANGENT",
              "cut": "VIEW_SECTION"}


def to_dxf(doc: DrawingDocument, sheet: Sheet, path: str,
           properties: Properties = None) -> str:
    """The sheet's lines as R12 DXF, in millimetres.

    Layers follow the line kinds, so visible and hidden edges stay apart in
    whatever opens it.  Pen weights and paper do not survive: a DXF is
    geometry, and this is the one export that converts rather than copies.
    """
    from ..core.dxf import DxfDocument

    out = DxfDocument()
    width, height = sheet.extent()

    border = doc.borders.get(sheet.border)
    if border is not None:
        x0, y0, x1, y1 = border.frame(width, height)
        for a, b in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)),
                     ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))):
            out.line(a, b, "BORDER")

    for view in sheet.views:
        if view.projection is None:
            continue
        for line in view.projection.lines:
            layer = DXF_LAYERS.get(line.kind, "VIEW_VISIBLE")
            points = [(view.x + x, view.y + y) for x, y in line.points]
            for a, b in zip(points, points[1:]):
                out.line(a, b, layer)
        # A DXF has no fills here, so hatching goes out as the lines it is
        # actually made of, on their own layer to be turned off easily.
        loops = view.projection.of_kind(hlr.CUT)
        pattern = dwg.hatch_patterns().get(view.projection.hatch or "")
        if loops and pattern is not None:
            for a, b in dwg.hatch_lines([line.points for line in loops],
                                        pattern):
                out.line((view.x + a[0], view.y + a[1]),
                         (view.x + b[0], view.y + b[1]), "VIEW_HATCH")

    values = dwg.properties_for(doc, sheet, _props(properties, sheet))
    block = doc.title_blocks.get(sheet.title_block)
    if block is not None and border is not None:
        frame = border.frame(width, height)
        origin = (frame[2] - block.width, frame[1])
        for line in block.lines:
            out.line((origin[0] + line[0], origin[1] + line[1]),
                     (origin[0] + line[2], origin[1] + line[3]), "BORDER")
        for item in block.fields:
            content = dwg.resolve_field(item, values, sheet)
            if content:
                out.text((origin[0] + item.x, origin[1] + item.y),
                         item.height, content, "ANNOTATION", item.align)

    for view in sheet.views:
        if view.projection is None or not view.label_visible:
            continue
        box = view.projection.box
        out.text((view.x, view.y + box[1] - 6.0), 3.5, view.label,
                 "ANNOTATION", "centre")

    for note in sheet.annotations:
        view = sheet.view(note.view)
        if view is None or not note.points:
            continue
        scale = doc.view_scale(sheet, view)
        anchor = note.points[0]
        offset = (list(note.offset) + [0.0, 0.0])[:2]
        out.text((view.x + anchor[0] + offset[0],
                  view.y + anchor[1] + offset[1]),
                 3.5, note.caption(scale), "ANNOTATION")

    return out.save(path)
