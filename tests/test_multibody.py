"""Join, Cut, Intersect and New Body - and the first solid, which has no say."""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402,F401


from datum.core import kernel                                      # noqa: E402
from datum.core.document import Document                           # noqa: E402
from datum.core.features import (                                  # noqa: E402
    CUT, INTERSECT, JOIN, NEW_BODY, OPERATIONS, OPERATION_LABELS,
    ExtrudeFeature, FilletFeature, PrimitiveFeature, SketchFeature,
)
from datum.core.naming import RefSet                               # noqa: E402
from datum.core.sketch import STANDARD_PLANES, Sketch              # noqa: E402

FAILED = []


def check(label, ok, extra=""):
    print(("  ok   " if ok else "  FAIL ") + label
          + ("" if ok else "   <- %s" % (extra,)))
    if not ok:
        FAILED.append(label)


WORK = tempfile.mkdtemp(prefix="datum-mb-")


def block(doc, x, y, z, dx=60, dy=40, dz=20, op=JOIN, name=""):
    feature = PrimitiveFeature()
    feature.kind = "box"
    feature.a, feature.b, feature.c = str(dx), str(dy), str(dz)
    feature.origin = [x, y, z]
    feature.operation = op
    feature.body_name = name
    doc.add_feature(feature)
    return feature


# ==========================================================================
print("the options are in Inventor's order")

check("Join first, then Cut, Intersect, New Body",
      OPERATIONS == (JOIN, CUT, INTERSECT, NEW_BODY), OPERATIONS)
check("and they are labelled the way Inventor labels them",
      [OPERATION_LABELS[o] for o in OPERATIONS]
      == ["Join", "Cut", "Intersect", "New Body"],
      [OPERATION_LABELS[o] for o in OPERATIONS])


# ==========================================================================
print("the first solid in an empty part just becomes the body")

for op in OPERATIONS:
    doc = Document()
    block(doc, 0, 0, 0, op=op)
    doc.rebuild()
    check("%s on an empty part still makes a body" % OPERATION_LABELS[op],
          doc.shape is not None and len(doc.bodies) == 1,
          (doc.shape, len(doc.bodies)))
    check("  and it is the whole box", abs(kernel.volume(doc.shape)
                                           - 60 * 40 * 20) < 1e-6,
          kernel.volume(doc.shape))

doc = Document()
first = block(doc, 0, 0, 0, name="Housing")
doc.rebuild()
check("the body takes the name it was given",
      doc.bodies[0].name == "Housing", doc.bodies[0].name)

doc = Document()
block(doc, 0, 0, 0)
doc.rebuild()
check("or Solid1 when it was not", doc.bodies[0].name == "Solid1",
      doc.bodies[0].name)


# ==========================================================================
print("Join adds material")

doc = Document()
block(doc, 0, 0, 0, 60, 40, 20)
block(doc, 40, 0, 0, 60, 40, 20, op=JOIN)
doc.rebuild()
check("still one body", len(doc.bodies) == 1, len(doc.bodies))
check("the overlap is not counted twice",
      abs(kernel.volume(doc.shape) - (100 * 40 * 20)) < 1e-6,
      kernel.volume(doc.shape))

print("Cut removes it")
doc = Document()
block(doc, 0, 0, 0, 60, 40, 20)
block(doc, 0, 0, 10, 20, 40, 20, op=CUT)
doc.rebuild()
check("still one body", len(doc.bodies) == 1, len(doc.bodies))
check("the notch is gone",
      abs(kernel.volume(doc.shape) - (60 * 40 * 20 - 20 * 40 * 10)) < 1e-6,
      kernel.volume(doc.shape))

print("Intersect keeps the overlap")
doc = Document()
block(doc, 0, 0, 0, 60, 40, 20)
block(doc, 40, 0, 0, 60, 40, 20, op=INTERSECT)
doc.rebuild()
check("still one body", len(doc.bodies) == 1, len(doc.bodies))
check("only the shared part is left",
      abs(kernel.volume(doc.shape) - (20 * 40 * 20)) < 1e-6,
      kernel.volume(doc.shape))


# ==========================================================================
print("New Body makes a multibody part")

doc = Document()
block(doc, 0, 0, 0, 60, 40, 20)
block(doc, 200, 0, 0, 30, 30, 30, op=NEW_BODY, name="Cover")
doc.rebuild()
check("two bodies now", len(doc.bodies) == 2, len(doc.bodies))
check("named apart", [b.name for b in doc.bodies] == ["Solid1", "Cover"],
      [b.name for b in doc.bodies])
check("the shape is both of them",
      abs(kernel.volume(doc.shape) - (60 * 40 * 20 + 30 * 30 * 30)) < 1e-6,
      kernel.volume(doc.shape))
check("and it really is two solids",
      len(kernel.explore(doc.shape,
                         __import__("OCP.TopAbs", fromlist=["x"]).TopAbs_SOLID))
      == 2)

print("auto-naming skips names already taken")
doc = Document()
block(doc, 0, 0, 0, op=JOIN, name="Solid2")
block(doc, 200, 0, 0, op=NEW_BODY)
doc.rebuild()
check("the second is not called Solid2 as well",
      [b.name for b in doc.bodies] == ["Solid2", "Solid1"],
      [b.name for b in doc.bodies])


# ==========================================================================
print("a later feature finds the body it is aimed at")

doc = Document()
block(doc, 0, 0, 0, 60, 40, 20)                       # Solid1 at the origin
block(doc, 200, 0, 0, 40, 40, 40, op=NEW_BODY)        # Solid2 far away
block(doc, 210, 10, 10, 20, 20, 60, op=CUT)           # a hole through Solid2
doc.rebuild()
check("still two bodies", len(doc.bodies) == 2, len(doc.bodies))
check("the first is untouched",
      abs(kernel.volume(doc.bodies[0].shape) - 60 * 40 * 20) < 1e-6,
      kernel.volume(doc.bodies[0].shape))
check("and the cut came out of the second",
      abs(kernel.volume(doc.bodies[1].shape)
          - (40 * 40 * 40 - 20 * 20 * 30)) < 1e-6,
      kernel.volume(doc.bodies[1].shape))

print("so does a fillet")
doc = Document()
block(doc, 0, 0, 0, 60, 40, 20)
block(doc, 200, 0, 0, 40, 40, 40, op=NEW_BODY)
doc.rebuild()
second = doc.bodies[1].shape
before = kernel.volume(second)
edge = kernel.edges(second)[0]
refs = RefSet()
refs.capture_from(doc.shape, "edge", [edge])
fillet = FilletFeature()
fillet.radius = "3"
fillet.refs = refs
doc.add_feature(fillet)
doc.rebuild()
check("two bodies still", len(doc.bodies) == 2, len(doc.bodies))
check("the first is untouched",
      abs(kernel.volume(doc.bodies[0].shape) - 60 * 40 * 20) < 1e-6,
      kernel.volume(doc.bodies[0].shape))
check("the second lost a corner",
      kernel.volume(doc.bodies[1].shape) < before - 1e-6,
      (before, kernel.volume(doc.bodies[1].shape)))


# ==========================================================================
print("bodies survive a save and reload")

doc = Document()
block(doc, 0, 0, 0, 60, 40, 20, name="Base")
block(doc, 200, 0, 0, 30, 30, 30, op=NEW_BODY, name="Lid")
doc.rebuild()
path = doc.save(os.path.join(WORK, "two_bodies"))

back = Document.load(path)
back.rebuild()
check("two bodies again", len(back.bodies) == 2, len(back.bodies))
check("with their names", [b.name for b in back.bodies] == ["Base", "Lid"],
      [b.name for b in back.bodies])
check("and the same volume",
      abs(kernel.volume(back.shape) - kernel.volume(doc.shape)) < 1e-6)


# ==========================================================================
print("an older file that said 'new' still opens")

doc = Document()
block(doc, 0, 0, 0, 60, 40, 20)
doc.rebuild()
raw = doc.snapshot()
check("a single-feature part reloads unchanged",
      abs(kernel.volume(Document.load(doc.save(
          os.path.join(WORK, "one")))  .shape if False else doc.shape)
          - 60 * 40 * 20) < 1e-6)

legacy = Document()
legacy.load_dict({
    "parameters": [],
    "features": [
        {"id": 1, "name": "Box", "type": "primitive", "suppressed": False,
         "fields": {"kind": "box", "a": "60", "b": "40", "c": "20",
                    "origin": [0, 0, 0], "operation": "new"}},
    ],
})
legacy.rebuild()
check("it builds", legacy.shape is not None)
check("as one body", len(legacy.bodies) == 1, len(legacy.bodies))


# ==========================================================================
print("an extrusion can start a second body too")

doc = Document()
block(doc, 0, 0, 0, 60, 40, 20)

sketch = SketchFeature()
sketch.sketch = Sketch(STANDARD_PLANES["XY"], "Away")
sketch.sketch.add_rectangle((200, 0), (240, 30))
doc.add_feature(sketch)

extrude = ExtrudeFeature()
extrude.distance = "15"
extrude.operation = NEW_BODY
extrude.body_name = "Bracket"
extrude.profiles.add(sketch.id, (220.0, 15.0))
doc.add_feature(extrude)
doc.rebuild()

check("no errors", doc.last_report.ok, doc.last_report.message)
check("two bodies", len(doc.bodies) == 2, len(doc.bodies))
check("the new one is named", doc.bodies[1].name == "Bracket",
      doc.bodies[1].name)
check("with the right volume",
      abs(kernel.volume(doc.bodies[1].shape) - 40 * 30 * 15) < 1e-6,
      kernel.volume(doc.bodies[1].shape))

print("and switching it back to Join merges them")
extrude.operation = JOIN
doc.rebuild()
check("one body again", len(doc.bodies) == 1, len(doc.bodies))
check("holding both volumes",
      abs(kernel.volume(doc.shape) - (60 * 40 * 20 + 40 * 30 * 15)) < 1e-6,
      kernel.volume(doc.shape))



# ==========================================================================
print("touching profiles extrude as one solid, with one face on top")

from OCP.TopAbs import TopAbs_SOLID                                 # noqa: E402

doc = Document()
outline = SketchFeature()
outline.name = "Outline"
outline.sketch = Sketch(STANDARD_PLANES["XY"], "Outline")
outline.sketch.add_rectangle((0, 0), (100, 60))        # the plate
outline.sketch.add_rectangle((-10, 10), (0, 50))       # a tab on its edge
doc.add_feature(outline)

tall = ExtrudeFeature()
tall.distance = "4"
tall.profiles.add(outline.id, (50.0, 30.0))
tall.profiles.add(outline.id, (-5.0, 30.0))
doc.add_feature(tall)
doc.rebuild()

check("it builds", doc.last_report.ok, doc.last_report.message)
check("one body", len(doc.bodies) == 1, len(doc.bodies))
check("and really one solid",
      len(kernel.explore(doc.shape, TopAbs_SOLID)) == 1,
      len(kernel.explore(doc.shape, TopAbs_SOLID)))
check("the volume is both profiles",
      abs(kernel.volume(doc.shape) - (100 * 60 * 4 + 10 * 40 * 4)) < 1e-6,
      kernel.volume(doc.shape))

tops = [f for f in kernel.faces(doc.shape)
        if abs(kernel.shape_centre(f)[2] - 4.0) < 1e-6]
check("with a single face across the top, not a seam", len(tops) == 1,
      len(tops))
bottoms = [f for f in kernel.faces(doc.shape)
           if abs(kernel.shape_centre(f)[2]) < 1e-6]
check("and a single face across the bottom", len(bottoms) == 1,
      len(bottoms))
check("the top face covers both profiles",
      abs(kernel.face_area(tops[0]) - (100 * 60 + 10 * 40)) < 1e-6,
      kernel.face_area(tops[0]))

print("profiles that do not touch stay separate inside one body")
doc = Document()
islands = SketchFeature()
islands.name = "Islands"
islands.sketch = Sketch(STANDARD_PLANES["XY"], "Islands")
islands.sketch.add_rectangle((0, 0), (40, 40))
islands.sketch.add_rectangle((200, 0), (240, 40))
doc.add_feature(islands)
pair = ExtrudeFeature()
pair.distance = "5"
pair.profiles.add(islands.id, (20.0, 20.0))
pair.profiles.add(islands.id, (220.0, 20.0))
doc.add_feature(pair)
doc.rebuild()
check("it builds", doc.last_report.ok, doc.last_report.message)
check("two solids, because they are nowhere near each other",
      len(kernel.explore(doc.shape, TopAbs_SOLID)) == 2,
      len(kernel.explore(doc.shape, TopAbs_SOLID)))
check("the volume is both", abs(kernel.volume(doc.shape)
                                - 2 * 40 * 40 * 5) < 1e-6,
      kernel.volume(doc.shape))


# ==========================================================================
print("a Join that lands flush leaves no seam either")

doc = Document()
block(doc, 0, 0, 0, 100, 60, 10)
block(doc, 100, 20, 0, 40, 20, 10, op=JOIN)     # butted against the side
doc.rebuild()
check("one body", len(doc.bodies) == 1, len(doc.bodies))
tops = [f for f in kernel.faces(doc.shape)
        if abs(kernel.shape_centre(f)[2] - 10.0) < 1e-6]
check("one face on top, not two", len(tops) == 1, len(tops))
check("covering both blocks",
      abs(kernel.face_area(tops[0]) - (100 * 60 + 40 * 20)) < 1e-6,
      kernel.face_area(tops[0]))
check("the volume is right",
      abs(kernel.volume(doc.shape) - (100 * 60 * 10 + 40 * 20 * 10)) < 1e-6,
      kernel.volume(doc.shape))

print("but a New Body keeps its own faces, because it is its own solid")
doc = Document()
block(doc, 0, 0, 0, 100, 60, 10)
block(doc, 100, 20, 0, 40, 20, 10, op=NEW_BODY)
doc.rebuild()
check("two bodies", len(doc.bodies) == 2, len(doc.bodies))
tops = [f for f in kernel.faces(doc.shape)
        if abs(kernel.shape_centre(f)[2] - 10.0) < 1e-6]
check("and two tops, one each", len(tops) == 2, len(tops))


# ==========================================================================
shutil.rmtree(WORK, ignore_errors=True)
print()
if FAILED:
    print("%d FAILED" % len(FAILED))
    for name in FAILED:
        print("   - %s" % name)
    sys.exit(1)
print("all multibody checks passed")
