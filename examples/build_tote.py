"""Build a slot-together plywood tote: parts, assembly and CAM nest.

Everything here goes through DATUM's own API - the same code the buttons
call - so the result is a real set of documents you can open, edit and cut.

The tote is five pieces of 12 mm ply from three unique parts: two ends with
a handle, two sides whose tabs pass right through them, and a base whose
tabs reach into both.  Nothing is glued in the model and nothing needs to
be: the tabs and slots locate every piece.

All the numbers come from the two that matter, so changing either rebuilds
the whole thing consistently:

    T   the plywood thickness, which every slot is cut to
    TAB how far a tab stands proud, which is one thickness
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from datum.core import constraints3d, kernel
from datum.core.assembly import AssemblyDocument
from datum.core.cam import CamDocument
from datum.core.constraints3d import Placement
from datum.core.document import Document
from datum.core.features import (
    NEW_BODY, CUT, ExtrudeFeature, SketchFeature, collect_regions, BuildContext,
)
from datum.core.sketch import STANDARD_PLANES, Sketch

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "plywood-tote")
os.makedirs(OUT, exist_ok=True)

T = 12.0          # plywood thickness; every slot is cut to this
TAB = T           # a tab stands one thickness proud
TOOL_R = 3.0      # radius of the 6 mm cutter the sheet is set up for
BITE = 0.4        # how far the corner relief may reach into a gripping wall


def relieved(x0, y0, x1, y1):
    """A slot, plus the corner relief that lets a square tab seat in it.

    A 6 mm cutter cannot cut an inside corner sharper than R3, so a square
    tab fouls all four corners of a plain rectangular slot and stands about
    a millimetre proud of the panel.  The fix is a T-bone: a cutter-sized
    circle at each corner, sitting on the slot's *end* walls so that the two
    walls actually gripping the plywood keep their full length.  It reaches
    ``BITE`` past the gripping wall rather than exactly touching it, because
    a circle tangent to a corner is the sort of geometry that makes a
    boolean fail for reasons nobody enjoys tracking down.

    Returns the rectangle and the four circles that relieve it.
    """
    r = TOOL_R
    if (x1 - x0) >= (y1 - y0):        # long axis is X, so relieve the ends
        ends, sides, swap = (x0, x1), (y0 + r - BITE, y1 - r + BITE), False
    else:                             # long axis is Y
        ends, sides, swap = (y0, y1), (x0 + r - BITE, x1 - r + BITE), True
    return (x0, y0, x1, y1), [((s, e) if swap else (e, s), r)
                              for e in ends for s in sides]


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def sketch_of(doc, name, rectangles=(), slots=(), circles=()):
    """A sketch of axis-aligned rectangles, slots and circles."""
    feature = SketchFeature()
    feature.name = name
    feature.sketch = Sketch(STANDARD_PLANES["XY"], name)
    for x0, y0, x1, y1 in rectangles:
        feature.sketch.add_rectangle((x0, y0), (x1, y1))
    for p1, p2, width in slots:
        feature.sketch.add_slot(p1, p2, width)
    for centre, radius in circles:
        feature.sketch.add_circle(centre, radius)
    doc.add_feature(feature)
    return feature


def regions_of(doc, sketch_id):
    """Every closed region of one sketch, as the extrude dialog sees them."""
    ctx = BuildContext(doc.params.scope())
    ctx.sketches = dict(doc.all_sketches())
    return [r for r in collect_regions(ctx) if r["sketch_id"] == sketch_id]


def extrude(doc, sketch, distance, operation=NEW_BODY, name="",
            biggest_only=False):
    """Extrude a sketch, taking every region of it or only the largest.

    The outline sketches are drawn as a body rectangle with the tabs butted
    against it, so taking every region and letting them fuse is what turns
    them into one panel with one clean edge.
    """
    doc.rebuild()
    regions = regions_of(doc, sketch.id)
    if biggest_only:
        regions = [max(regions, key=lambda r: r["area"])]

    feature = ExtrudeFeature()
    feature.distance = str(distance)
    feature.operation = operation
    feature.body_name = name
    feature.extent = "through_all" if operation == CUT else "distance"
    for region in regions:
        feature.profiles.add(sketch.id, region["centre"])
    doc.add_feature(feature)
    return feature


def save(doc, name):
    doc.rebuild()
    if not doc.last_report.ok:
        raise SystemExit("%s failed: %s" % (name, doc.last_report.message))
    path = doc.save(os.path.join(OUT, name))
    box = kernel.bounding_box(doc.shape)
    print("  %-18s %6.1f x %6.1f x %5.1f mm   %d face(s)"
          % (os.path.basename(path), box[3] - box[0], box[4] - box[1],
             box[5] - box[2], len(kernel.faces(doc.shape))))
    return path


def rotation(*columns):
    """A rotation vector from where the part's own axes have to end up."""
    return list(constraints3d.rotation_vector(np.array(columns).T))


# --------------------------------------------------------------------------
# the parts
# --------------------------------------------------------------------------

print("parts")

# ---- end panel: 160 x 200, a handle, and every slot the others need -------
END_W, END_H = 160.0, 200.0

end = Document()
end.params.add("thickness", str(T), comment="plywood thickness")
sketch_of(end, "End Outline", [(0, 0, END_W, END_H)])
extrude(end, end.features[-1], T, NEW_BODY, "End Panel")

END_SLOTS = [
    # the side panels pass through here, two tabs each side
    (20, 40, 20 + T, 65), (20, 80, 20 + T, 105),
    (END_W - 20 - T, 40, END_W - 20, 65),
    (END_W - 20 - T, 80, END_W - 20, 105),
    # and the base reaches in here
    (50, 24, 110, 24 + T),
]
rects, circles = [], []
for slot in END_SLOTS:
    rect, relief = relieved(*slot)
    rects.append(rect)
    circles.extend(relief)

sketch_of(end, "End Cuts", rectangles=rects, circles=circles,
          slots=[((55, 172), (105, 172), 26)])   # the handle
extrude(end, end.features[-1], T * 3, CUT)
END = save(end, "End Panel")

# ---- side panel: 276 long, tabs through both ends -------------------------
SIDE_L, SIDE_H = 276.0, 100.0

side = Document()
side.params.add("thickness", str(T), comment="plywood thickness")
sketch_of(side, "Side Outline", [
    (0, 0, SIDE_L, SIDE_H),
    # tabs, butted against the body so they fuse into one outline
    (-TAB, 20, 0, 45), (-TAB, 60, 0, 85),
    (SIDE_L, 20, SIDE_L + TAB, 45), (SIDE_L, 60, SIDE_L + TAB, 85),
])
extrude(side, side.features[-1], T, NEW_BODY, "Side Panel")

SIDE_SLOTS = [(58, 4, 108, 4 + T), (168, 4, 218, 4 + T)]  # base tabs
rects, circles = [], []
for slot in SIDE_SLOTS:
    rect, relief = relieved(*slot)
    rects.append(rect)
    circles.extend(relief)

sketch_of(side, "Side Cuts", rectangles=rects, circles=circles)
extrude(side, side.features[-1], T * 3, CUT)
SIDE = save(side, "Side Panel")

# ---- base: tabs on all four edges ----------------------------------------
BASE_L, BASE_W = 276.0, 96.0

base = Document()
base.params.add("thickness", str(T), comment="plywood thickness")
sketch_of(base, "Base Outline", [
    (0, 0, BASE_L, BASE_W),
    (58, -TAB, 108, 0), (168, -TAB, 218, 0),                 # into side A
    (58, BASE_W, 108, BASE_W + TAB), (168, BASE_W, 218, BASE_W + TAB),
    (-TAB, 18, 0, 78), (BASE_L, 18, BASE_L + TAB, 78),       # into the ends
])
extrude(base, base.features[-1], T, NEW_BODY, "Base")
BASE = save(base, "Base")


# --------------------------------------------------------------------------
# the assembly
# --------------------------------------------------------------------------

print()
print("assembly")

asm = AssemblyDocument()
asm.path = os.path.join(OUT, "Tote.adat")

# where each part's own X, Y and Z end up in the tote
UPRIGHT = rotation((0, 1, 0), (0, 0, 1), (1, 0, 0))   # ends stand across
ON_EDGE = rotation((1, 0, 0), (0, 0, 1), (0, -1, 0))  # sides stand on edge

layout = [
    (BASE, "Base", [0.0, 0.0, 0.0], [12.0, 32.0, 24.0]),
    (SIDE, "Side Panel", ON_EDGE, [12.0, 32.0, 20.0]),
    (SIDE, "Side Panel", ON_EDGE, [12.0, 140.0, 20.0]),
    (END, "End Panel", UPRIGHT, [0.0, 0.0, 0.0]),
    (END, "End Panel", UPRIGHT, [288.0, 0.0, 0.0]),
]

for path, label, turn, where in layout:
    placed = asm.place(path, label)
    placed.placement = Placement(list(where), list(turn))
    placed.grounded = True          # interlocking parts locate themselves

report = asm.rebuild()
print("  %s" % report.message)
box = kernel.bounding_box(asm.shape)
print("  tote is %.0f x %.0f x %.0f mm"
      % (box[3] - box[0], box[4] - box[1], box[5] - box[2]))
print("  %.2f litres of ply" % (kernel.volume(asm.shape) / 1e6))
asm.save(asm.path)


# --------------------------------------------------------------------------
# the CAM sheet
# --------------------------------------------------------------------------

print()
print("CAM")

nest = CamDocument()
nest.path = os.path.join(OUT, "Tote Nest.cdat")
nest.sheet_width, nest.sheet_height = "1220", "610"
nest.sheet_thickness = str(T)
nest.tool_diameter = "6"
nest.tool_name = "6 mm straight, 2 flute"
nest.lead_length = "0"
nest.margin = "20"

for path in (BASE, SIDE, SIDE, END, END):
    nest.add_part(path)

nest.rebuild()
nest.auto_arrange()
report = nest.rebuild()
print("  %s" % report.message)
for placed in nest.parts:
    print("    %-14s %.2f mm thick, %d profile(s)"
          % (placed.label, placed.cut_face.thickness,
             len(placed.profile.loops) if placed.profile else 0))
if report.warnings:
    print("  warnings:", report.warnings)
if report.errors:
    print("  errors:", report.errors)

nest.save(nest.path)
dxf_path = nest.export_dxf(os.path.join(OUT, "Tote Nest.dxf"))
print("  wrote %s" % os.path.basename(dxf_path))
print()
print("all written to %s" % OUT)
