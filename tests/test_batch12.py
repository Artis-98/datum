"""Stray sketch lines, cylinder seams and the End of Part.

A line left lying about in a sketch, with an end that meets nothing, used
to be sewn into the region round it: the extrusion carried it into the
solid as an edge across the top, fillets tripped over it, and the regions
next to it could refuse to build.  Only curves that enclose something
divide a sketch now.

The seam down the side of an extruded circle is neither drawn nor picked.

A new feature always lands above the End of Part, so it is built and seen
rather than parked below the marker where nothing happens to it.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import harness  # noqa: E402,F401

os.environ["DATUM_SETTINGS_ORG"] = "IITEG-tests"

from PySide6 import QtWidgets                                      # noqa: E402

from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet             # noqa: E402
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox                    # noqa: E402
from OCP.GeomAbs import GeomAbs_Shape                              # noqa: E402

from datum.core import kernel                                      # noqa: E402
from datum.core.document import Document                           # noqa: E402
from datum.core.features import (                                  # noqa: E402
    ExtrudeFeature, FilletFeature, ProfileSelection, SketchFeature,
    WorkPlaneFeature,
)
from datum.core.sketch import STANDARD_PLANES, Sketch              # noqa: E402
from datum.ui.main_window import MainWindow                        # noqa: E402
from datum.ui.theme import stylesheet                              # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def near(a, b, tol=0.5):
    return abs(a - b) < tol


def block_and_round(strays=()):
    """A rectangle with a circle beside it, and whatever lines are added."""
    s = Sketch(STANDARD_PLANES["XY"], "Sketch")
    s.add_rectangle((-40.0, -20.0), (0.0, 20.0))
    s.add_circle((25.0, 0.0), 15.0)
    for a, b in strays:
        s.add_line(a, b)
    return s


# the lines from the screenshots, more or less: loose ends inside the
# circle, one poking into the block, one crossing into the block from
# outside, one crossing the circle's rim and stopping inside
STRAYS = [
    ((20.0, 0.0), (30.0, 5.0)),
    ((-40.0, 0.0), (-20.0, 0.0)),
    ((-10.0, 30.0), (-5.0, -5.0)),
    ((25.0, 20.0), (25.0, 5.0)),
    ((60.0, 40.0), (70.0, 45.0)),
]


def part(sketch, picks, height="10"):
    doc = Document()
    feature = SketchFeature()
    feature.name = "Sketch"
    feature.sketch = sketch
    doc.add_feature(feature)
    extrude = ExtrudeFeature()
    extrude.distance = height
    extrude.sketch_id = feature.id
    extrude.profiles = ProfileSelection()
    for point in picks:
        extrude.profiles.add(feature.id, point)
    doc.add_feature(extrude)
    report = doc.rebuild()
    return doc, report


def edge_count(shape):
    return len(kernel.edges(shape)) if shape is not None else 0


# ==========================================================================
print("loose lines do not make regions of their own")
clean = kernel.sketch_regions(block_and_round())
messy = kernel.sketch_regions(block_and_round(STRAYS))
check("the clean sketch has two regions", len(clean) == 2, len(clean))
check("the messy one has the same two", len(messy) == 2, len(messy))
check("and they are the same size",
      sorted(round(r["area"]) for r in messy)
      == sorted(round(r["area"]) for r in clean),
      ([round(r["area"]) for r in messy], [round(r["area"]) for r in clean]))

for face in kernel.sketch_regions_faces(block_and_round(STRAYS)):
    edges = len(kernel.edges(face))
    check("a region is bounded by its outline only (%d edges)" % edges,
          edges in (1, 4), edges)

print("a line right across still divides, as it should")
across = kernel.sketch_regions(
    block_and_round([((25.0, -20.0), (25.0, 20.0))]))
check("the circle cut through is two halves", len(across) == 3, len(across))

print("extruding past the stray lines")
doc, report = part(block_and_round(STRAYS), [(25.0, -8.0)])
check("the round builds", report.ok, report.message)
check("as a plain cylinder: two circles and its seam",
      edge_count(doc.shape) == 3, edge_count(doc.shape))
check("of the right volume",
      near(kernel.volume(doc.shape), 3.14159265 * 15 * 15 * 10, 5.0),
      kernel.volume(doc.shape))

doc, report = part(block_and_round(STRAYS), [(-30.0, 10.0)])
check("the block beside it builds", report.ok, report.message)
check("as a plain block: twelve edges",
      edge_count(doc.shape) == 12, edge_count(doc.shape))

doc, report = part(block_and_round(STRAYS), [(-30.0, 10.0), (25.0, -8.0)])
check("both together build", report.ok, report.message)
check("and come to both volumes",
      near(kernel.volume(doc.shape),
           40 * 40 * 10 + 3.14159265 * 15 * 15 * 10, 6.0),
      kernel.volume(doc.shape))

print("every edge of the stray-line cylinder can be filleted")
doc, report = part(block_and_round(STRAYS), [(25.0, -8.0)])
fillet = FilletFeature()
fillet.radius = "2"
fillet.all_edges = True
doc.add_feature(fillet)
report = doc.rebuild()
check("the fillet builds", report.ok, report.message)
check("and leaves a valid solid", kernel.is_valid(doc.shape))

# ==========================================================================
print("seams are known for what they are")
doc, report = part(block_and_round(), [(25.0, 0.0)])
seams = [e for e in kernel.edges(doc.shape) if kernel.is_seam(e)]
check("a cylinder has one seam", len(seams) == 1, len(seams))
doc_box, _ = part(block_and_round(), [(-30.0, 0.0)])
check("a block has none",
      not any(kernel.is_seam(e) for e in kernel.edges(doc_box.shape)))
box = BRepPrimAPI_MakeBox(20.0, 20.0, 20.0).Shape()
rounding = BRepFilletAPI_MakeFillet(box)
rounding.Add(3.0, kernel.edges(box)[0])
rounded = rounding.Shape()
kernel.mark_seams(rounded)
from OCP.BRepLib import BRepLib                                    # noqa: E402
BRepLib.EncodeRegularity_s(rounded, 1e-10)
check("a fillet's tangent edges are still edges",
      not any(kernel.is_seam(e) for e in kernel.edges(rounded)))

# ==========================================================================
print("a new feature goes in above the End of Part")
doc, report = part(block_and_round(), [(-30.0, 0.0)])
second = SketchFeature()
second.name = "Later"
second.sketch = Sketch(STANDARD_PLANES["XY"], "Later")
doc.add_feature(second)
doc.rollback_index = 2              # the block built, the later sketch not
plane = WorkPlaneFeature()
doc.add_feature(plane, 2)
check("it is put where the marker was", doc.index_of(plane.id) == 2)
check("and the marker moves down past it", doc.rollback_index == 3,
      doc.rollback_index)
check("the feature below the marker stays below it",
      doc.index_of(second.id) >= doc.rollback_index)

another = WorkPlaneFeature()
doc.add_feature(another)
check("added with no place given, it still goes above the marker",
      doc.index_of(another.id) < doc.rollback_index,
      (doc.index_of(another.id), doc.rollback_index))

doc.remove_feature(plane.id)
check("deleting one above the marker keeps it under the same feature",
      doc.features[doc.rollback_index - 1] is another,
      [f.name for f in doc.features])
check("and the later sketch still below it",
      doc.index_of(second.id) == doc.rollback_index)

doc.rollback_index = None
appended = WorkPlaneFeature()
doc.add_feature(appended)
check("with no marker, a new feature goes at the end",
      doc.features[-1] is appended and doc.rollback_index is None)

# ==========================================================================
app = QtWidgets.QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(stylesheet())
win = MainWindow()
win.resize(1400, 900)
win.show()
app.processEvents()
vp = win.viewport


def pump(n=6):
    for _ in range(n):
        app.processEvents()


print("in the window: a fillet started while rolled back")
win.new_document(prompt=False)
doc, _ = part(block_and_round(), [(-30.0, 0.0)])
for feature in list(doc.features):
    win.document.add_feature(feature)
later = SketchFeature()
later.name = "Later"
later.sketch = Sketch(STANDARD_PLANES["XY"], "Later")
win.document.add_feature(later)
win.rebuild()
win.set_rollback(2)
pump()
win.new_feature(FilletFeature)
pump()
dialog = win._active_dialog
check("the fillet dialog opened", dialog is not None)
if dialog is not None:
    fid = dialog.feature.id
    check("the new fillet is above the End of Part",
          win.document.index_of(fid) < win.document.rollback_index,
          (win.document.index_of(fid), win.document.rollback_index))
    dialog.cancel()
    pump()
check("cancelling puts the marker back", win.document.rollback_index == 2,
      win.document.rollback_index)
check("and takes the fillet away",
      not any(isinstance(f, FilletFeature) for f in win.document.features))

print("a sketch started while rolled back")
win.start_sketch_on_plane("XZ")
pump()
sketch_id = win._sketch_feature_id
win.finish_sketch()
pump()
check("it lands above the End of Part too",
      sketch_id is not None
      and win.document.index_of(sketch_id) < win.document.rollback_index,
      (win.document.index_of(sketch_id) if sketch_id else None,
       win.document.rollback_index))
win.set_rollback(None)

print("the seam is neither drawn nor picked")
win.new_document(prompt=False)
doc, _ = part(block_and_round(), [(25.0, 0.0)], height="30")
for feature in list(doc.features):
    win.document.add_feature(feature)
win.rebuild()
pump()
ais = vp.model_ais
check("the cylinder is on screen", ais is not None)
if ais is not None:
    check("face boundaries stop short of seams",
          ais.Attributes().FaceBoundaryUpperContinuity()
          == GeomAbs_Shape.GeomAbs_C2)

vp.set_view("right")
vp.finish_animation()
vp.fit_all()
pump()
vp.set_selection_mode("edge")
pump()
seam = [e for e in kernel.edges(win.document.shape) if kernel.is_seam(e)][0]
lo, hi = kernel.bounding_box(seam)[2], kernel.bounding_box(seam)[5]
middle = kernel.shape_centre(seam)
x, y = vp.project(middle)

ctx = vp.context
ctx.MoveTo(*vp._px(x, y), vp.view, False)
raw = (ctx.HasDetected() and ctx.HasDetectedShape()
       and kernel.is_seam(ctx.DetectedShape()))
check("left to itself OCCT finds the seam there", raw)
vp._detect(x, y)
found_seam = (ctx.HasDetected() and ctx.HasDetectedShape()
              and kernel.is_seam(ctx.DetectedShape()))
check("hovering there finds no seam", not found_seam)

top = (25.0, 0.0, 30.0)
# a point on the top rim, away from the seam, in the right view: the rim
# is seen edge on, so its near point is (25 + 15, 0, 30)
rim = (40.0, 0.0, 30.0)
tx, ty = vp.project(rim)
vp._detect(tx, ty)
check("the rim right above it is still found",
      ctx.HasDetected() and ctx.HasDetectedShape()
      and not kernel.is_seam(ctx.DetectedShape()))
vp.set_selection_mode("none")

print()
if FAILS:
    print("%d FAILURES: %s" % (len(FAILS), ", ".join(FAILS)))
    sys.exit(1)
print("all batch 12 tests passed")
sys.exit(0)
