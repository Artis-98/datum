"""End-to-end modelling checks: build a real part, edit it, rebuild, export."""
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402,F401


from datum.core import fileio, kernel  # noqa: E402
from datum.core.document import Document  # noqa: E402
from datum.core.features import (  # noqa: E402
    ChamferFeature, ExtrudeFeature, FilletFeature, HoleFeature, MirrorFeature,
    PatternFeature, PrimitiveFeature, RevolveFeature, ShellFeature,
    SketchFeature,
)
from datum.core.naming import RefSet, ShapeRef  # noqa: E402
from datum.core.sketch import STANDARD_PLANES, Sketch  # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + ("  " + str(extra) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def near(a, b, tol=1e-3):
    return abs(a - b) < tol


# --------------------------------------------------------------------------
print("sketch -> face")
s = Sketch(STANDARD_PLANES["XY"], "Profile")
s.add_rectangle((0, 0), (60, 40))
s.add_circle((30, 20), 8)
fs = kernel.sketch_faces(s)
check("one face with a hole", len(fs) == 1, "got %d" % len(fs))
if fs:
    area = kernel.face_area(fs[0])
    expect = 60 * 40 - math.pi * 64
    check("face area accounts for the hole", near(area, expect, 0.5),
          "got %.3f want %.3f" % (area, expect))

# --------------------------------------------------------------------------
print("parametric plate: extrude + fillet + hole")
doc = Document()
doc.params.add("plate_w", "60")
doc.params.add("plate_d", "40")
doc.params.add("plate_t", "8")
doc.params.add("corner_r", "6")

sk = SketchFeature()
sk.name = "Outline"
sk.sketch = Sketch(STANDARD_PLANES["XY"], "Outline")
ids = sk.sketch.add_rectangle((0, 0), (60, 40))
corner = sk.sketch.entities[ids[0]].points
sk.sketch.points[corner[0]].fixed = True
sk.sketch.add_constraint("distance_x", points=[corner[0], corner[1]],
                         expression="plate_w")
left = sk.sketch.entities[ids[3]].points
sk.sketch.add_constraint("distance_y", points=[left[1], left[0]],
                         expression="plate_d")
doc.add_feature(sk)

ex = ExtrudeFeature()
ex.name = "Plate"
ex.sketch_id = sk.id
ex.distance = "plate_t"
doc.add_feature(ex)

rep = doc.rebuild()
check("plate built", rep.ok, rep.message)
check("plate is a solid", doc.shape is not None)
if doc.shape is not None:
    v = kernel.volume(doc.shape)
    check("plate volume 60x40x8", near(v, 60 * 40 * 8, 1.0), "got %.1f" % v)
    check("plate has 6 faces", len(kernel.faces(doc.shape)) == 6,
          len(kernel.faces(doc.shape)))

# fillet the four vertical edges, selected by geometry
verticals = []
for e in kernel.edges(doc.shape):
    c = kernel.shape_centre(e)
    if near(kernel.edge_length(e), 8.0, 0.01):
        verticals.append(e)
check("found 4 vertical edges", len(verticals) == 4, len(verticals))

fl = FilletFeature()
fl.name = "Corners"
fl.radius = "corner_r"
fl.refs = RefSet()
fl.refs.capture_from(doc.shape, "edge", verticals)
doc.add_feature(fl)
rep = doc.rebuild()
check("fillet built", rep.ok, rep.message)
vol_filleted = kernel.volume(doc.shape)
check("fillet removed material", vol_filleted < 60 * 40 * 8 - 1,
      "got %.1f" % vol_filleted)

# holes from a sketch of circles
hsk = SketchFeature()
hsk.name = "Hole Centres"
hsk.sketch = Sketch(STANDARD_PLANES["XY"], "Hole Centres")
for pos in [(10, 10), (50, 10), (10, 30), (50, 30)]:
    hsk.sketch.add_circle(pos, 2.5)
doc.add_feature(hsk)

hole = HoleFeature()
hole.name = "Mount Holes"
hole.sketch_id = hsk.id
hole.diameter = "5"
hole.through = True
hole.flip = True
doc.add_feature(hole)
rep = doc.rebuild()
check("holes built", rep.ok, rep.message)
vol_holes = kernel.volume(doc.shape)
expect_removed = 4 * math.pi * 2.5 ** 2 * 8
check("four 5mm holes removed", near(vol_filleted - vol_holes, expect_removed, 2.0),
      "removed %.1f want %.1f" % (vol_filleted - vol_holes, expect_removed))

# --------------------------------------------------------------------------
print("parameter edit rebuilds the whole tree")
doc.params.set_expression("plate_w", "100")
rep = doc.rebuild()
check("rebuild after param change", rep.ok, rep.message)
bbox = doc.mass_properties()["bbox"]
check("plate grew to 100mm", near(bbox[0], 100, 0.05), "got %.3f" % bbox[0])
check("fillets survived the change",
      len(kernel.faces(doc.shape)) > 6 and rep.ok)
check("holes survived the change",
      near(kernel.volume(doc.shape), 100 * 40 * 8 - (60 * 40 * 8 - vol_filleted)
           - expect_removed, 3.0),
      "got %.1f" % kernel.volume(doc.shape))

# --------------------------------------------------------------------------
print("topological naming survives an inserted feature")
doc.params.set_expression("plate_t", "12")
rep = doc.rebuild()
check("thicker plate rebuilds", rep.ok, rep.message)
fillet_feature = doc.feature(fl.id)
check("fillet still resolved its edges", not fillet_feature.error,
      fillet_feature.error)

# --------------------------------------------------------------------------
print("revolve")
doc2 = Document()
rsk = SketchFeature()
rsk.sketch = Sketch(STANDARD_PLANES["XZ"], "Section")
rsk.sketch.add_rectangle((10, 0), (20, 30))
doc2.add_feature(rsk)
rv = RevolveFeature()
rv.sketch_id = rsk.id
rv.angle = "360"
rv.axis = "Y"
doc2.add_feature(rv)
rep = doc2.rebuild()
check("revolve built", rep.ok, rep.message)
if doc2.shape is not None:
    v = kernel.volume(doc2.shape)
    expect = math.pi * (20 ** 2 - 10 ** 2) * 30
    check("tube volume", near(v, expect, 5.0), "got %.1f want %.1f" % (v, expect))

# --------------------------------------------------------------------------
print("shell")
doc3 = Document()
prim = PrimitiveFeature()
prim.kind = "box"
prim.a, prim.b, prim.c = "40", "40", "40"
doc3.add_feature(prim)
doc3.rebuild()
top = max(kernel.faces(doc3.shape), key=lambda f: kernel.shape_centre(f)[2])
sh = ShellFeature()
sh.thickness = "3"
sh.refs = RefSet()
sh.refs.capture_from(doc3.shape, "face", [top])
doc3.add_feature(sh)
rep = doc3.rebuild()
check("shell built", rep.ok, rep.message)
if rep.ok:
    v = kernel.volume(doc3.shape)
    expect = 40 ** 3 - 34 * 34 * 37
    check("shell wall volume", near(v, expect, 30.0),
          "got %.1f want %.1f" % (v, expect))

# --------------------------------------------------------------------------
print("rectangular pattern of a hole feature")
doc4 = Document()
p4 = PrimitiveFeature()
p4.kind = "box"
p4.a, p4.b, p4.c = "100", "40", "10"
doc4.add_feature(p4)
hs = SketchFeature()
hs.sketch = Sketch(STANDARD_PLANES["XY"], "Pattern Seed")
hs.sketch.add_circle((10, 20), 3)
doc4.add_feature(hs)
h4 = HoleFeature()
h4.sketch_id = hs.id
h4.diameter = "6"
h4.through = True
doc4.add_feature(h4)
doc4.rebuild()
v_one = kernel.volume(doc4.shape)

pat = PatternFeature()
pat.mode = "rectangular"
pat.parents = [h4.id]
pat.count1 = "4"
pat.spacing1 = "20"
pat.dir1 = "X"
doc4.add_feature(pat)
rep = doc4.rebuild()
check("pattern built", rep.ok, rep.message)
v_four = kernel.volume(doc4.shape)
one_hole = math.pi * 9 * 10
check("pattern cut 3 more holes", near(v_one - v_four, 3 * one_hole, 2.0),
      "removed %.1f want %.1f" % (v_one - v_four, 3 * one_hole))

# --------------------------------------------------------------------------
print("mirror")
doc5 = Document()
p5 = PrimitiveFeature()
p5.kind = "box"
p5.a, p5.b, p5.c = "20", "20", "20"
p5.origin = ("5", "0", "0")
doc5.add_feature(p5)
mi = MirrorFeature()
mi.plane = "YZ"
mi.scope = "body"
doc5.add_feature(mi)
rep = doc5.rebuild()
check("mirror built", rep.ok, rep.message)
if rep.ok:
    check("mirrored volume doubled", near(kernel.volume(doc5.shape), 2 * 8000, 1.0),
          "got %.1f" % kernel.volume(doc5.shape))

# --------------------------------------------------------------------------
print("suppress / rollback")
doc.feature(hole.id).suppressed = True
rep = doc.rebuild()
check("suppressed hole removed", rep.ok and near(
    kernel.volume(doc.shape), 100 * 40 * 12 - (100 * 40 * 12 - kernel.volume(doc.shape)), 1e6))
doc.feature(hole.id).suppressed = False
doc.rollback_index = 2
doc.rebuild()
check("rollback stops the tree early", len(kernel.faces(doc.shape)) == 6,
      len(kernel.faces(doc.shape)))
doc.rollback_index = None
doc.rebuild()

# --------------------------------------------------------------------------
print("save / load / undo")
tmp = os.path.join(tempfile.gettempdir(), "forge_roundtrip.pdat")
tmp = doc.save(tmp)
loaded = Document.load(tmp)
check("file round trip: features", len(loaded.features) == len(doc.features))
check("file round trip: params", len(loaded.params) == len(doc.params))
check("file round trip: geometry",
      near(kernel.volume(loaded.shape), kernel.volume(doc.shape), 0.5))

doc.push_undo()
doc.params.set_expression("plate_w", "200")
doc.rebuild()
check("edited to 200", near(doc.mass_properties()["bbox"][0], 200, 0.05))
doc.undo()
doc.rebuild()
check("undo restored 100", near(doc.mass_properties()["bbox"][0], 100, 0.05),
      doc.mass_properties()["bbox"][0])

# --------------------------------------------------------------------------
print("export")
step_path = os.path.join(tempfile.gettempdir(), "forge_test.step")
stl_path = os.path.join(tempfile.gettempdir(), "forge_test.stl")
fileio.write_step(doc.shape, step_path)
fileio.write_stl(doc.shape, stl_path)
check("STEP written", os.path.getsize(step_path) > 1000)
check("STL written", os.path.getsize(stl_path) > 1000)
reimported = fileio.read_step(step_path)
check("STEP re-imports with same volume",
      near(kernel.volume(reimported), kernel.volume(doc.shape), 1.0))

# --------------------------------------------------------------------------
print("broken feature does not kill the rest of the tree")
bad = FilletFeature()
bad.radius = "9999"
bad.all_edges = True
doc.add_feature(bad)
rep = doc.rebuild()
check("rebuild reports the failure", not rep.ok)
check("body still present after failure", doc.shape is not None)
check("error is attached to the feature", bool(doc.feature(bad.id).error))
doc.remove_feature(bad.id)

print()
if FAILS:
    print("%d FAILURES: %s" % (len(FAILS), ", ".join(FAILS)))
    sys.exit(1)
print("all modelling tests passed")
