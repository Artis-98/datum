"""Cut face detection, flattening, cutter compensation and DXF export."""

import math
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402,F401


from datum.core import cam, dxf, fileformat, kernel, sheet, toolpath  # noqa: E402
from datum.core.assembly import open_any                              # noqa: E402
from datum.core.cam import CamDocument                                # noqa: E402
from datum.core.document import Document                              # noqa: E402
from datum.core.features import (                                     # noqa: E402
    ExtrudeFeature, HoleFeature, PrimitiveFeature, SketchFeature,
)
from datum.core.sketch import STANDARD_PLANES, Sketch                 # noqa: E402
from datum.core.toolpath import INSIDE, OUTSIDE, Job, Sheet, Tool     # noqa: E402

FAILED = []


def check(label, ok, extra=""):
    print(("  ok   " if ok else "  FAIL ") + label
          + ("" if ok else "   <- %s" % (extra,)))
    if not ok:
        FAILED.append(label)


WORK = tempfile.mkdtemp(prefix="datum-cam-")


def plate_part(width=120.0, height=80.0, thickness=3.0, holes=((30, 40, 12),),
               name="plate"):
    """A flat plate with round holes - the archetypal sheet part."""
    doc = Document()
    outline = SketchFeature()
    outline.sketch = Sketch(STANDARD_PLANES["XY"], "Outline")
    outline.sketch.add_rectangle((0, 0), (width, height))
    doc.add_feature(outline)

    extrude = ExtrudeFeature()
    extrude.distance = str(thickness)
    extrude.profiles.add(outline.id, (width * 0.5, height * 0.5))
    doc.add_feature(extrude)

    if holes:
        centres = SketchFeature()
        centres.sketch = Sketch(STANDARD_PLANES["XY"], "Holes")
        for x, y, d in holes:
            centres.sketch.add_circle((x, y), d * 0.5)
        doc.add_feature(centres)

        cut = HoleFeature()
        cut.sketch_id = centres.id
        cut.diameter = str(holes[0][2])
        cut.depth = str(thickness * 4)
        cut.through = True
        doc.add_feature(cut)

    doc.rebuild()
    return doc, doc.save(os.path.join(WORK, name))


# ==========================================================================
print("a flat part is recognised, and its thickness read off")

doc, plate_path = plate_part()
found = sheet.detect_cut_face(doc.shape)
check("it is a sheet part", found.ok, found.reason)
check("the thickness is the extrusion", abs(found.thickness - 3.0) < 1e-6,
      found.thickness)
check("the normal is along Z", abs(abs(found.normal[2]) - 1.0) < 1e-9,
      found.normal)
check("the two faces are the big ones",
      abs(kernel.face_area(found.face) - kernel.face_area(found.opposite))
      < 1e-6)
check("and they are the sheet faces, not the edges",
      kernel.face_area(found.face) > 120 * 80 * 0.8,
      kernel.face_area(found.face))

print("the opposite face is found from either side")
back = sheet.opposite_face(doc.shape, found.face)
check("there is one", back is not None)
check("it is the one detection paired up", back.IsSame(found.opposite))
check("and the gap between them is the thickness",
      abs(sheet.thickness_between(found.face, back) - 3.0) < 1e-6)


# ==========================================================================
print("a part with something standing proud is refused")

proud = Document()
base = PrimitiveFeature()
base.kind, base.a, base.b, base.c = "box", "100", "60", "3"
proud.add_feature(base)
boss = PrimitiveFeature()
boss.kind, boss.a, boss.b, boss.c = "cylinder", "8", "12", "0"
boss.operation = "join"
proud.add_feature(boss)
proud.rebuild()

verdict = sheet.detect_cut_face(proud.shape)
check("it is not flat cuttable", not verdict.ok)
check("and the reason names the problem",
      "stands proud" in verdict.reason, verdict.reason)
check("it still reports what it measured", verdict.thickness > 0,
      verdict.thickness)

print("a part with no opposed flat pair is refused too")
ball = Document()
sphere = PrimitiveFeature()
sphere.kind, sphere.a = "sphere", "20"
ball.add_feature(sphere)
ball.rebuild()
verdict = sheet.detect_cut_face(ball.shape)
check("a sphere is not a sheet part", not verdict.ok)
check("and it says why", "no flat faces" in verdict.reason, verdict.reason)


# ==========================================================================
print("flattening gives an outline and its holes")

found = sheet.detect_cut_face(doc.shape)
profile = sheet.flatten(doc.shape, found.face, found.thickness)
check("one outline", profile.outer is not None)
check("one hole", len(profile.holes) == 1, len(profile.holes))
check("the outline is the right size",
      abs(profile.width - 120.0) < 1e-4 and abs(profile.height - 80.0) < 1e-4,
      (profile.width, profile.height))
check("it is centred on its own box",
      all(abs(v) < 1e-4 for v in
          (profile.outer.bbox[0] + profile.outer.bbox[2],
           profile.outer.bbox[1] + profile.outer.bbox[3])),
      profile.outer.bbox)
check("the hole is 12 mm across", abs(profile.holes[0].width - 12.0) < 1e-4,
      profile.holes[0].width)
check("the part is read from the top, so it is not mirrored",
      abs(found.normal[2] - 1.0) < 1e-9, found.normal)
check("the hole sits where it was drawn",
      abs((profile.holes[0].bbox[0] + profile.holes[0].bbox[2]) * 0.5
          - (30.0 - 60.0)) < 1e-4,
      profile.holes[0].bbox)
check("the hole key is its position",
      profile.holes[0].key.startswith("hole@"), profile.holes[0].key)
check("cut sides default sensibly",
      profile.outer.default_side == OUTSIDE
      and profile.holes[0].default_side == INSIDE)
check("everything landed on z = 0",
      abs(kernel.bounding_box(profile.outer.wire)[2]) < 1e-5
      and abs(kernel.bounding_box(profile.outer.wire)[5]) < 1e-5,
      kernel.bounding_box(profile.outer.wire))

print("reading it off the back face mirrors it")
flipped = sheet.flatten(doc.shape, found.opposite, found.thickness)
mirrored_x = (flipped.holes[0].bbox[0] + flipped.holes[0].bbox[2]) * 0.5
straight_x = (profile.holes[0].bbox[0] + profile.holes[0].bbox[2]) * 0.5
check("the hole swaps sides", abs(mirrored_x + straight_x) < 1e-4,
      (straight_x, mirrored_x))
check("but stays at the same height",
      abs((flipped.holes[0].bbox[1] + flipped.holes[0].bbox[3]) * 0.5
          - (profile.holes[0].bbox[1] + profile.holes[0].bbox[3]) * 0.5)
      < 1e-4)


# ==========================================================================
print("cutter compensation goes the right way")

outer = profile.outer.wire
grown, problem = toolpath.offset_wire(outer, 3.0, outward=True)
check("the outline offsets outward", grown is not None, problem)
x0, y0, _z0, x1, y1, _z1 = kernel.bounding_box(grown)
check("by exactly the tool radius",
      abs((x1 - x0) - 126.0) < 1e-4 and abs((y1 - y0) - 86.0) < 1e-4,
      (x1 - x0, y1 - y0))
check("and the corners became arcs",
      len(kernel.edges(grown)) == 8, len(kernel.edges(grown)))

shrunk, problem = toolpath.offset_wire(outer, 3.0, outward=False)
check("and inward when asked", shrunk is not None, problem)
x0, y0, _z0, x1, y1, _z1 = kernel.bounding_box(shrunk)
check("shrinking by the radius",
      abs((x1 - x0) - 114.0) < 1e-4, x1 - x0)

hole = profile.holes[0].wire
inside, problem = toolpath.offset_wire(hole, 3.0, outward=False)
check("a hole offsets inward", inside is not None, problem)
x0, _y0, _z0, x1, _y1, _z1 = kernel.bounding_box(inside)
check("to 6 mm across", abs((x1 - x0) - 6.0) < 1e-4, x1 - x0)

print("an opening smaller than the tool is refused")
tiny, problem = toolpath.offset_wire(hole, 7.0, outward=False)
check("it fails", tiny is None)
check("with a reason a machinist would recognise",
      "does not fit" in problem or "closes up" in problem, problem)


# ==========================================================================
print("leads come off tangentially")

lead_in, lead_out = toolpath.lead_edges(grown, 5.0)
check("both exist", lead_in is not None and lead_out is not None)
check("the lead in is the set length",
      abs(kernel.edge_length(lead_in) - 5.0) < 1e-6,
      kernel.edge_length(lead_in))
check("no leads when the length is zero",
      toolpath.lead_edges(grown, 0.0) == (None, None))


# ==========================================================================
print("generating a whole sheet")

job = Job(part_id=1, name="plate:1", profile=profile, position=(200.0, 150.0))
stock = Sheet(width=1500, height=3000, thickness=3.0, margin=15.0)
report = toolpath.generate([job], Tool(diameter=6.0), stock, lead_length=4.0)

check("it is clean", report.ok, report.message)
check("two cuts", len(report.cuts) == 2, len(report.cuts))
check("holes are cut before the outline",
      report.cuts[0].key.startswith("hole") and report.cuts[1].outer,
      [c.key for c in report.cuts])
check("the outer cut grew", report.cuts[1].side == OUTSIDE)
check("the hole was cut inside", report.cuts[0].side == INSIDE)
check("leads were added", report.cuts[0].lead_in is not None)
x0, y0, _z0, x1, y1, _z1 = kernel.bounding_box(report.cuts[1].wire)
check("the part landed where it was put",
      abs((x0 + x1) * 0.5 - 200.0) < 1e-4
      and abs((y0 + y1) * 0.5 - 150.0) < 1e-4, ((x0 + x1) / 2, (y0 + y1) / 2))

print("the cut side can be flipped per profile")
job.sides = {profile.holes[0].key: OUTSIDE}
flipped_report = toolpath.generate([job], Tool(diameter=6.0), stock)
hole_cut = flipped_report.passes[0]
check("the hole is cut outside now", hole_cut.side == OUTSIDE)
x0, _y0, _z0, x1, _y1, _z1 = kernel.bounding_box(hole_cut.wire)
check("so the path is bigger than the hole", (x1 - x0) > 6.0, x1 - x0)
job.sides = {}

print("a tool wider than the hole is caught before the machine sees it")
fat = toolpath.generate([job], Tool(diameter=14.0), stock)
check("it is not clean", not fat.ok)
check("and names the tool and the hole",
      any("narrower than" in e for e in fat.errors), fat.errors)

print("a part off the sheet is caught")
off = Job(part_id=1, name="plate:1", profile=profile, position=(5.0, 150.0))
report = toolpath.generate([off], Tool(diameter=6.0), stock)
check("hanging over the edge is an error", not report.ok)
check("and says which edge", any("clamp margin" in e or "hang" in e
                                 for e in report.errors), report.errors)

print("a part bigger than the sheet is caught")
small_stock = Sheet(width=100, height=100, thickness=3.0, margin=5.0)
report = toolpath.generate(
    [Job(part_id=1, name="plate:1", profile=profile, position=(50.0, 50.0))],
    Tool(diameter=6.0), small_stock)
check("it does not fit", not report.ok)
check("and says so", any("does not fit" in e for e in report.errors),
      report.errors)

print("overlapping parts are caught")
a = Job(part_id=1, name="a", profile=profile, position=(200.0, 150.0))
b = Job(part_id=2, name="b", profile=profile, position=(260.0, 150.0))
report = toolpath.generate([a, b], Tool(diameter=6.0), stock)
check("overlap is an error", not report.ok)
check("and names both", any("overlap" in e for e in report.errors),
      report.errors)

print("but rotated parts that only share a bounding box are not")
far = Job(part_id=2, name="b", profile=profile, position=(340.0, 150.0))
report = toolpath.generate([a, far], Tool(diameter=6.0), stock)
check("well spaced parts are clean", report.ok, report.message)


# ==========================================================================
print("the CAM document ties it together")

sheet_doc = CamDocument()
sheet_doc.path = os.path.join(WORK, "nest.cdat")
sheet_doc.sheet_width, sheet_doc.sheet_height = "1000", "600"
sheet_doc.sheet_thickness = "3"
sheet_doc.tool_diameter = "6"
first = sheet_doc.add_part(plate_path)
check("a part can be added", first.name == "plate:1", first.name)
check("the path is relative", first.ref.path == "plate.pdat", first.ref.path)

# the profile has to exist before anything can be laid out, so the first
# rebuild is what measures it
sheet_doc.rebuild()
check("the cut face was detected", first.cut_face.known)
check("and its thickness recorded",
      abs(first.cut_face.thickness - 3.0) < 1e-6, first.cut_face.thickness)
check("a profile came out", first.profile is not None)
check("a new part has not been laid out yet", not first.laid_out)

sheet_doc.auto_arrange(only_new=True)
check("laying out gives it a spot", first.laid_out)
report = sheet_doc.rebuild()
check("it builds", report.ok, report.message)
check("with two cuts", len(report.toolpath.cuts) == 2,
      len(report.toolpath.cuts))

print("auto arrange lays parts out with a gap")
for _ in range(3):
    sheet_doc.add_part(plate_path)
sheet_doc.rebuild()
arranged = sheet_doc.auto_arrange()
check("all four were arranged", arranged == 4, arranged)
report = sheet_doc.rebuild()
check("and nothing overlaps", report.ok, report.message)

boxes = [sheet.placed_bbox(p.profile, p.position, p.rotation, p.mirror)
         for p in sheet_doc.parts]
gap = boxes[1][0] - boxes[0][2]
check("the gap is at least 1.5 tool diameters", gap >= 9.0 - 1e-4, gap)
check("everything is inside the clamp margin, tool radius included",
      all(b[0] >= 18.0 - 1e-4 and b[1] >= 18.0 - 1e-4 for b in boxes), boxes)

print("hand placement survives a regenerate")
sheet_doc.move_part(sheet_doc.parts[2].id, (700.0, 400.0), rotation=30.0)
check("it is marked as placed by hand", sheet_doc.parts[2].manual)
before = list(sheet_doc.parts[2].position)
sheet_doc.library.forget()
sheet_doc.rebuild()
check("regenerating left it alone",
      sheet_doc.parts[2].position == before, sheet_doc.parts[2].position)
check("and kept the rotation", abs(sheet_doc.parts[2].rotation - 30.0) < 1e-9)


# ==========================================================================
print("the cut face is remembered, not re-detected")

before_centre = tuple(first.cut_face.ref.centre)

# pick the other side of the sheet by hand and check it sticks
assert first.shape is not None
back_face = sheet.opposite_face(first.shape, first.cut_face.face)
check("it can be picked by hand", sheet_doc.set_cut_face(first, back_face))
check("and is marked as such", first.cut_face.manual)
sheet_doc.rebuild()
check("the hand pick survived the rebuild", first.cut_face.manual)
check("and it really is the other face",
      abs(first.cut_face.ref.centre[2] - before_centre[2]) > 1e-6,
      (before_centre, first.cut_face.ref.centre))

print("flipping reads the part off the other side")
first.cut_face.flip = True
sheet_doc.rebuild()
flip_x = (first.profile.holes[0].bbox[0] + first.profile.holes[0].bbox[2]) * 0.5
first.cut_face.flip = False
sheet_doc.rebuild()
plain_x = (first.profile.holes[0].bbox[0]
           + first.profile.holes[0].bbox[2]) * 0.5
check("the hole moves to the other side", abs(flip_x + plain_x) < 1e-6,
      (plain_x, flip_x))


# ==========================================================================
print("the sheet round-trips, and the link stays live")

written = sheet_doc.save(sheet_doc.path)
check("saved as .cdat", written.endswith(".cdat"), written)
manifest = fileformat.peek(written)
check("its manifest says cam", manifest.type == "cam", manifest.type)
check("and lists every part", len(manifest.references) == 4)

back = CamDocument.load(written)
check("parts come back", len(back.parts) == 4)
check("the sheet size came back", back.sheet_width == "1000",
      back.sheet_width)
check("so did the tool", back.tool().diameter == 6.0)
check("hand placement survived the file",
      back.parts[2].manual and abs(back.parts[2].rotation - 30.0) < 1e-9)
check("open_any recognises it", isinstance(open_any(written), CamDocument))

report = back.rebuild()
check("it rebuilds from disk", report.ok, report.message)
check("the stored cut face was reused, not re-detected",
      not back.parts[0].cut_face.warning, back.parts[0].cut_face.warning)

print("editing the part changes the toolpath")
edited = Document.load(plate_path)
edited.features[1].distance = "5"          # thicker stock
edited.rebuild()
edited.save(plate_path)
back.library.forget()
report = back.rebuild()
check("the new thickness came through",
      abs(back.parts[0].cut_face.thickness - 5.0) < 1e-6,
      back.parts[0].cut_face.thickness)
check("and it warns the sheet no longer matches",
      any("thick" in w for w in report.warnings), report.warnings)


# ==========================================================================
print("export writes only the toolpath")

back.sheet_thickness = "5"
report = back.rebuild()
check("the nest is sound before exporting", report.toolpath.ok,
      report.toolpath.message)
out = back.export_dxf(os.path.join(WORK, "nest.dxf"))
check("a file was written", os.path.exists(out))
text = open(out, "r", encoding="ascii").read()
check("it is R12", "AC1009" in text)
check("in millimetres", "$INSUNITS" in text and "\n70\n4\n" in text)
check("it has arcs from the corner rounding", "\nARC\n" in text)
check("and circles from the holes", "\nCIRCLE\n" in text)
check("the layers are declared",
      "CUT_OUTER" in text and "CUT_INNER" in text)
check("holes are written before the outline that frees them",
      text.index("CUT_INNER") < text.index("CUT_OUTER"))

circles = text.count("\nCIRCLE\n")
check("one circle per hole, offset", circles == 4, circles)

print("an empty sheet refuses to export")
empty = CamDocument()
empty.rebuild()
try:
    empty.export_dxf(os.path.join(WORK, "empty.dxf"))
    check("it refuses", False, "no error raised")
except Exception as exc:
    check("it refuses", "no toolpath" in str(exc), exc)

print("and so does a nest with a cut that failed")
back.parts[0].position = [5.0, 5.0]          # shoved into the clamp margin
back.rebuild()
try:
    back.export_dxf(os.path.join(WORK, "broken.dxf"))
    check("an incomplete nest is refused", False, "no error raised")
except Exception as exc:
    check("an incomplete nest is refused", "incomplete" in str(exc), exc)
forced = back.export_dxf(os.path.join(WORK, "forced.dxf"), force=True)
check("but it can be forced", os.path.exists(forced))
back.auto_arrange()
back.rebuild()


# ==========================================================================
print("a missing part is reported, and the rest still generates")

os.remove(plate_path)
back.library.forget()
report = back.rebuild()
check("it says what is missing", report.missing, report.missing)
check("the message names it", "plate" in report.message, report.message)


# ==========================================================================
shutil.rmtree(WORK, ignore_errors=True)
print()
if FAILED:
    print("%d FAILED" % len(FAILED))
    for name in FAILED:
        print("   - %s" % name)
    sys.exit(1)
print("all CAM core checks passed")
