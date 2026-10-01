"""Work axes, revolving about one, and extruding up to a plane.

A work axis is the line a revolve turns about or a pattern goes round,
without having to draw it into the sketch.  Extrude To Plane runs a
profile until it meets a datum or work plane, square or tilted.
"""
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import harness  # noqa: E402,F401

from datum.core import kernel                                     # noqa: E402
from datum.core.document import Document                          # noqa: E402
from datum.core.features import (                                 # noqa: E402
    AXIS_PREFIX, ExtrudeFeature, PrimitiveFeature, RevolveFeature,
    SketchFeature, WorkAxisFeature, WorkPlaneFeature,
)
from datum.core.naming import RefSet                              # noqa: E402
from datum.core.sketch import STANDARD_PLANES, Sketch             # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + ("  " + str(extra) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def near(a, b, tol=1e-3):
    return abs(a - b) < tol


def parallel(d, want):
    return near(abs(sum(d[i] * want[i] for i in range(3))), 1.0, 1e-6)


def on_line(point, axis):
    """Distance from a point to an axis line."""
    v = [point[i] - axis.origin[i] for i in range(3)]
    t = sum(v[i] * axis.direction[i] for i in range(3))
    return math.dist(point, [axis.origin[i] + t * axis.direction[i]
                             for i in range(3)])


# ==========================================================================
print("the three kinds of work axis")

doc = Document()
along_z = WorkAxisFeature(name="Along Z")
along_z.mode = "origin"
along_z.origin_axis = "Z"
doc.add_feature(along_z)

offset = WorkPlaneFeature(name="Raised")
offset.base = "XZ"
offset.offset = "15"
doc.add_feature(offset)
meet = WorkAxisFeature(name="Meet")
meet.mode = "planes"
meet.plane_a = "Raised"
meet.plane_b = "YZ"
doc.add_feature(meet)

report = doc.rebuild()
check("it builds", report.ok, report.message)
check("the origin axes are always there",
      set(("X", "Y", "Z")) <= set(doc.axes))
check("an origin axis copies it",
      parallel(doc.axes["Along Z"].direction, (0, 0, 1)))
m = doc.axes["Meet"]
check("two planes meet along the line they share",
      parallel(m.direction, (0, 0, 1)), m.direction)
xz = STANDARD_PLANES["XZ"]
raised_at = [xz.normal[i] * 15.0 for i in range(3)]
check("and it lies in both of them",
      on_line(raised_at, m) < 1e-6, (m.origin, raised_at))

clash = WorkAxisFeature(name="Never")
clash.mode = "planes"
clash.plane_a = "XZ"
clash.plane_b = "Raised"
doc.add_feature(clash)
report = doc.rebuild()
check("two parallel planes are refused, and say why",
      not report.ok and "parallel" in report.message, report.message)
doc.remove_feature(clash.id)

block = PrimitiveFeature(name="Rod")
block.kind = "cylinder"
block.a, block.b, block.c = "10", "40", "0"
doc.add_feature(block)
doc.rebuild()
round_face = next(f for f in kernel.faces(doc.shape)
                  if kernel.face_area(f) > 2000.0)
refs = RefSet()
refs.capture_from(doc.shape, "face", [round_face])
on_rod = WorkAxisFeature(name="Rod Axis")
on_rod.mode = "edge"
on_rod.ref = refs.refs[0]
doc.add_feature(on_rod)
report = doc.rebuild()
check("a round face gives its centre line", report.ok
      and parallel(doc.axes["Rod Axis"].direction, (0, 0, 1))
      and on_line((0, 0, 0), doc.axes["Rod Axis"]) < 1e-6,
      report.message)

path = os.path.join(tempfile.mkdtemp(prefix="datum_axis_"), "axes.pdat")
doc.save(path)
again = Document.load(path)
again.rebuild()
check("work axes survive a save",
      "Rod Axis" in again.axes and "Meet" in again.axes, list(again.axes))
check("and remember how they were made",
      again.feature(meet.id).plane_a == "Raised"
      and again.feature(on_rod.id).ref is not None)


# ==========================================================================
print()
print("revolving about a work axis")

doc = Document()
sk = SketchFeature(name="Ring")
sk.sketch = Sketch(STANDARD_PLANES["XZ"], "Ring")
sk.sketch.add_rectangle((20, 0), (30, 10))
doc.add_feature(sk)
axis = WorkAxisFeature(name="Spindle")
axis.mode = "origin"
axis.origin_axis = "Z"
doc.add_feature(axis)
rev = RevolveFeature(name="Turned")
rev.sketch_id = sk.id
rev.axis = AXIS_PREFIX + "Spindle"
doc.add_feature(rev)
report = doc.rebuild()
check("it builds", report.ok, report.message)
want = math.pi * (30 ** 2 - 20 ** 2) * 10
check("a ring turned about Z", doc.shape is not None
      and near(kernel.volume(doc.shape), want, 1.0),
      doc.shape is not None and (kernel.volume(doc.shape), want))
check("the summary names the axis", "Spindle" in rev.summary(),
      rev.summary())
rev.axis = AXIS_PREFIX + "Gone"
report = doc.rebuild()
check("an axis that is gone is an error, not a guess",
      not report.ok and "Gone" in report.message, report.message)


# ==========================================================================
print()
print("extruding up to a plane")


def plate_to(plane_feature, flip=False):
    doc = Document()
    sk = SketchFeature(name="Square")
    sk.sketch = Sketch(STANDARD_PLANES["XY"], "Square")
    sk.sketch.add_rectangle((0, 0), (20, 20))
    doc.add_feature(sk)
    if plane_feature is not None:
        doc.add_feature(plane_feature)
    ex = ExtrudeFeature(name="Up")
    ex.sketch_id = sk.id
    ex.extent = "to_plane"
    ex.to_plane = plane_feature.name if plane_feature else "Nowhere"
    doc.add_feature(ex)
    return doc, doc.rebuild()


top = WorkPlaneFeature(name="Top")
top.base = "XY"
top.offset = "35"
doc, report = plate_to(top)
check("square to the sketch it is a plain distance", report.ok
      and near(kernel.volume(doc.shape), 20 * 20 * 35, 0.01),
      report.message)
box = kernel.bounding_box(doc.shape)
check("and stops at the plane", near(box[5], 35.0), box)

below = WorkPlaneFeature(name="Below")
below.base = "XY"
below.offset = "12"
below.flip = True
doc, report = plate_to(below)
check("a plane behind the sketch is reached by going the other way",
      report.ok and near(kernel.bounding_box(doc.shape)[2], -12.0),
      report.message)

tilted = WorkPlaneFeature(name="Tilted")
tilted.base = "XY"
tilted.offset = "30"
tilted.angle = "20"
doc, report = plate_to(tilted)
check("a tilted plane builds", report.ok, report.message)
if report.ok:
    # the top follows the slope: the wedge's volume is its mean height
    plane = doc.planes["Tilted"]
    n = plane.normal

    def height(x, y):
        return (sum(n[i] * plane.origin[i] for i in range(3))
                - n[0] * x - n[1] * y) / n[2]

    mean = sum(height(x, y) for x in (0, 20) for y in (0, 20)) / 4.0
    check("and its top is cut on the slope",
          near(kernel.volume(doc.shape), 400.0 * mean, 0.5),
          (kernel.volume(doc.shape), 400.0 * mean))

doc, report = plate_to(None)
check("a plane that does not exist says so",
      not report.ok and "Nowhere" in report.message, report.message)

side = WorkPlaneFeature(name="Side")
side.base = "YZ"
side.offset = "50"
doc, report = plate_to(side)
check("a plane alongside the extrude is refused",
      not report.ok and "never meets" in report.message, report.message)

print()
print("FAILED: " + ", ".join(FAILS) if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
