"""Build a working drawing of the plywood tote's side panel.

Goes through the same core the UI does: reference a part, place a base view
and three projections, add a section and a detail, dimension it, fill the
title block, and write out PDF, SVG and DXF.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6 import QtWidgets                                     # noqa: E402

from datum.core import fileformat, views                          # noqa: E402
from datum.core.drawing import (                                  # noqa: E402
    BASE, DETAIL, DIAMETER, DrawingDocument, LINEAR, PROJECTED, SECTION,
    Annotation, View, WITH_HIDDEN,
)

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "drawings")
os.makedirs(OUT, exist_ok=True)

PART = os.path.join(HERE, "plywood-tote", "Side Panel.pdat")

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

from datum.ui import drawingexport                                # noqa: E402


def reference(path: str, base_dir: str) -> fileformat.ComponentRef:
    return fileformat.ComponentRef(
        path=fileformat.relative_path(path, base_dir),
        name=os.path.basename(path),
        label=os.path.splitext(os.path.basename(path))[0])


doc = DrawingDocument()
doc.standard = "ISO"
doc.properties.update({
    "Title": "SIDE PANEL",
    "Author": "aa",
    "Company": "IITEG",
    "Revision": "A",
})
doc.path = os.path.join(OUT, "Side Panel.ddat")
sheet = doc.add_sheet("A3")

# ---- the views -----------------------------------------------------------
# the panel is 300 x 100 x 12, and three views of it plus an isometric have
# to share an A3 with the title block; let the scale follow from that
from datum.core import kernel                                      # noqa: E402
from datum.core.document import Document                           # noqa: E402
model = Document.load(PART)
model.rebuild()
mx0, my0, mz0, mx1, my1, mz1 = kernel.bounding_box(model.shape)
span_x, span_y, span_z = mx1 - mx0, my1 - my0, mz1 - mz0

border = doc.borders[sheet.border].frame(*sheet.extent())
block = doc.title_blocks[sheet.title_block]
from datum.core.drawing import three_view_layout                   # noqa: E402
plan = three_view_layout(border, (span_x, span_y, span_z),
                         reserve=(block.width, block.height))
scale = plan["scale"]
print("part %.0f x %.0f x %.0f mm -> scale %s"
      % (span_x, span_y, span_z, scale))

base = View(kind=BASE, orientation="top", scale=scale,
            x=plan["base"][0], y=plan["base"][1],
            display=WITH_HIDDEN, name="Plan")
base.ref = reference(PART, OUT)
doc.add_view(sheet, base)

# first angle: below the plan is the view from the front, right of it the end
front = doc.add_view(sheet, View(kind=PROJECTED, parent=base.id, name="Front",
                                 x=plan["below"][0], y=plan["below"][1]))
side = doc.add_view(sheet, View(kind=PROJECTED, parent=base.id, name="End",
                                x=plan["side"][0], y=plan["side"][1]))
iso = doc.add_view(sheet, View(kind=PROJECTED, parent=base.id, name="Iso",
                               display="visible", scale=plan["iso_scale"],
                               x=plan["iso"][0], y=plan["iso"][1]))

detail = doc.add_view(sheet, View(
    kind=DETAIL, parent=base.id, letter="B", scale=scale * 4.0,
    centre=[-span_x * scale * 0.30, -span_y * scale * 0.28], radius=14.0,
    x=border[0] + 46.0, y=border[1] + 44.0, name="Detail"))

report = views.Generator().rebuild(doc)
print("views:", report.message)
for v in sheet.views:
    state = ("%3d lines" % len(v.projection.lines)) if v.projection \
        else ("ERROR " + v.error)
    print("   %-8s %-10s %s" % (v.name, v.kind, state))

# ---- dimensions ----------------------------------------------------------
box = base.projection.box if base.projection else (0, 0, 0, 0)
doc.add_annotation(sheet, Annotation(
    kind=LINEAR, view=base.id,
    points=[[box[0], box[1]], [box[2], box[1]]], offset=[0.0, -16.0]))
doc.add_annotation(sheet, Annotation(
    kind=LINEAR, view=base.id,
    points=[[box[2], box[1]], [box[2], box[3]]], offset=[18.0, 0.0]))

if front.projection is not None:
    fbox = front.projection.box
    doc.add_annotation(sheet, Annotation(
        kind=LINEAR, view=front.id,
        points=[[fbox[2], fbox[1]], [fbox[2], fbox[3]]], offset=[14.0, 0.0]))

for view in sheet.views:
    view.scale_visible = True

doc.save(doc.path)
print()
print("saved %s" % os.path.basename(doc.path))

# reopening must not need the model: the projections are cached in the file
reopened = DrawingDocument.load(doc.path)
drawn = sum(1 for v in reopened.sheets[0].views if v.projection is not None)
print("reopened with %d of %d views already drawn, no model loaded"
      % (drawn, len(reopened.sheets[0].views)))

# ---- output --------------------------------------------------------------
props = views.model_properties(PART, base.projection and None, None)
pdf = drawingexport.to_pdf(doc, os.path.join(OUT, "Side Panel.pdf"))
svg = drawingexport.to_svg(doc, sheet, os.path.join(OUT, "Side Panel.svg"))
dxf = drawingexport.to_dxf(doc, sheet, os.path.join(OUT, "Side Panel.dxf"))
png = drawingexport.to_image(doc, sheet, os.path.join(OUT, "Side Panel.png"),
                             dpi=150.0)
for path in (pdf, svg, dxf, png):
    print("  %-18s %8d bytes" % (os.path.basename(path),
                                 os.path.getsize(path)))
print()
print("all written to %s" % OUT)
