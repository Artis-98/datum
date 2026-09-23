"""Drawings: projection, sheets, title blocks, dimensions and output."""

import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402,F401

os.environ["DATUM_SETTINGS_ORG"] = "IITEG-tests"

from PySide6 import QtWidgets                                      # noqa: E402

from datum.core import drawing as dwg                              # noqa: E402
from datum.core import fileformat, hlr, kernel, templates          # noqa: E402
from datum.core import views as viewgen                            # noqa: E402
from datum.core.document import Document                           # noqa: E402
from datum.core.drawing import (                                   # noqa: E402
    Annotation, BASE, DETAIL, DIAMETER, DrawingDocument, LINEAR, PROJECTED,
    SECTION, View, WITH_HIDDEN,
)
from datum.core.features import PrimitiveFeature                   # noqa: E402

FAILS = []
app = QtWidgets.QApplication(sys.argv)
WORK = tempfile.mkdtemp(prefix="datum_drawing_")


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def near(a, b, tol=1e-4):
    return abs(a - b) < tol


def make_part(name, steps):
    doc = Document()
    for kind, a, b, c, op in steps:
        f = PrimitiveFeature()
        f.kind, f.a, f.b, f.c, f.operation = kind, str(a), str(b), str(c), op
        doc.add_feature(f)
    doc.rebuild()
    return doc.save(os.path.join(WORK, name))


# a block that is different from every side, so a view that came out turned
# the wrong way cannot pass by accident
BLOCK = make_part("block", [("box", 120, 60, 20, "new"),
                            ("box", 40, 25, 60, "join"),
                            ("cylinder", 8, 30, 0, "cut")])


def reference(path, base_dir):
    return fileformat.ComponentRef(
        path=fileformat.relative_path(path, base_dir),
        name=os.path.basename(path))


def drawing_of(part=BLOCK, orientation="front", scale=1.0, folder=None):
    folder = folder or WORK
    doc = DrawingDocument()
    doc.path = os.path.join(folder, "d.ddat")
    sheet = doc.add_sheet("A3")
    base = View(kind=BASE, orientation=orientation, scale=scale,
                x=140.0, y=190.0, display=WITH_HIDDEN)
    base.ref = reference(part, folder)
    doc.add_view(sheet, base)
    return doc, sheet, base


# ==========================================================================
print("a shape becomes lines, sorted by what kind of edge they came from")

model = Document.load(BLOCK)
model.rebuild()
projection = hlr.project(model.shape, *hlr.ORIENTATIONS["front"])
check("it projected", projection.ok, projection.error)
check("there are visible lines", projection.of_kind(hlr.VISIBLE))
check("and hidden ones, kept apart", projection.of_kind(hlr.HIDDEN))
check("the box matches the model, seen front on",
      near(projection.width, 120.0) and near(projection.height, 60.0),
      (projection.width, projection.height))
check("it is centred on itself, not on the model's origin",
      near(projection.box[0], -projection.box[2]), projection.box)

print("hidden lines can be left out")
plain = hlr.project(model.shape, *hlr.ORIENTATIONS["front"], hidden=False)
check("none come back", not plain.of_kind(hlr.HIDDEN))
check("but the visible ones are unchanged",
      len(plain.of_kind(hlr.VISIBLE))
      == len(projection.of_kind(hlr.VISIBLE)))


# ==========================================================================
print("which way a projected view looks depends on the standard")

front, up = hlr.ORIENTATIONS["front"]
first = viewgen.projected_orientation(front, up, 1.0, 0.0, dwg.FIRST_ANGLE)
third = viewgen.projected_orientation(front, up, 1.0, 0.0, dwg.THIRD_ANGLE)


def names(direction):
    return [k for k, (d, _u) in hlr.ORIENTATIONS.items()
            if max(abs(d[i] - direction[i]) for i in range(3)) < 1e-6]


check("dragged right, first angle gives the left view",
      names(first[0]) == ["left"], names(first[0]))
check("dragged right, third angle gives the right view",
      names(third[0]) == ["right"], names(third[0]))
below = viewgen.projected_orientation(front, up, 0.0, -1.0, dwg.FIRST_ANGLE)
check("dragged below, first angle gives the top view",
      names(below[0]) == ["top"], names(below[0]))


# ==========================================================================
print("a drawing generates its views from a referenced model")

doc, sheet, base = drawing_of()
side = doc.add_view(sheet, View(kind=PROJECTED, parent=base.id,
                                x=base.x + 190.0, y=base.y))
under = doc.add_view(sheet, View(kind=PROJECTED, parent=base.id,
                                 x=base.x, y=base.y - 110.0))
report = viewgen.Generator().rebuild(doc)
check("every view was drawn", report.ok and report.generated == 3,
      report.message)
check("the base view measures the part", near(base.projection.width, 120.0),
      base.projection.width)
check("a child inherits the scale it was not given",
      doc.view_scale(sheet, side) == 1.0)
check("and the display style", doc.view_display(sheet, side) == WITH_HIDDEN)

print("scale is applied to the lines, not just written on them")
base.scale = 0.5
viewgen.Generator().rebuild(doc, force=True)
check("half scale halves the drawing", near(base.projection.width, 60.0),
      base.projection.width)
check("and the children follow", near(side.projection.height, 30.0),
      side.projection.height)
base.scale = 1.0


# ==========================================================================
print("a section cuts the model, a detail enlarges the parent")

doc, sheet, base = drawing_of()
viewgen.Generator().rebuild(doc)
box = base.projection.box

section = doc.add_view(sheet, View(
    kind=SECTION, parent=base.id, letter="A",
    cut=[box[0] - 5.0, 0.0, box[2] + 5.0, 0.0],
    x=base.x, y=base.y - 120.0))
detail = doc.add_view(sheet, View(
    kind=DETAIL, parent=base.id, letter="B", scale=2.0,
    centre=[box[0] + 15.0, box[1] + 15.0], radius=18.0,
    x=base.x + 180.0, y=base.y))
report = viewgen.Generator().rebuild(doc, force=True)

check("the section was cut", section.projection is not None,
      section.error)
check("it is labelled the way a drawing labels one",
      section.label == "SECTION A-A", section.label)
check("the detail was taken", detail.projection is not None, detail.error)
check("it is labelled too", detail.label == "DETAIL B", detail.label)
if detail.projection is not None:
    check("the detail is bigger than the circle it came from",
          detail.projection.width > 18.0, detail.projection.width)
    check("and no wider than the circle allows at its scale",
          detail.projection.width <= 18.0 * 2.0 * 2.0 + 1e-6,
          detail.projection.width)

print("the faces the cut passed through get hatched")
cut_loops = section.projection.of_kind(hlr.CUT) if section.projection else []
check("the cut face came back as a closed loop", len(cut_loops) >= 1,
      len(cut_loops))
if cut_loops:
    loop = cut_loops[0]
    check("with enough points to be an area", len(loop.points) >= 3,
          len(loop.points))
    xs = [p[0] for p in loop.points]
    ys = [p[1] for p in loop.points]
    area = abs(sum(loop.points[i][0] * loop.points[i - 1][1]
                   - loop.points[i - 1][0] * loop.points[i][1]
                   for i in range(len(loop.points)))) / 2.0
    check("and a real area, not a squashed line", area > 1.0, area)
    check("it sits inside the view it belongs to",
          min(xs) >= section.projection.box[0] - 1e-6
          and max(xs) <= section.projection.box[2] + 1e-6, (min(xs), max(xs)))
check("a pattern was chosen for it", section.projection.hatch != "",
      section.projection.hatch)
check("and it is one the painter knows",
      section.projection.hatch in dwg.hatch_patterns(),
      section.projection.hatch)

print("a view that was not cut is not hatched")
check("the base view has no cut faces",
      not base.projection.of_kind(hlr.CUT))

print("turning hatching off leaves the section drawn but unfilled")
section.hatch = False
viewgen.Generator().rebuild(doc, force=True)
check("the section is still there", section.projection is not None,
      section.error)
check("with nothing to fill", not section.projection.of_kind(hlr.CUT))
section.hatch = True

print("the material decides the pattern when nothing else does")
check("plywood reads as plywood",
      dwg.hatch_for_material("12mm Birch Plywood") == "plywood")
check("a steel grade reads as steel",
      dwg.hatch_for_material("S235JR") == "steel")
check("an unknown material says nothing and lets the default stand",
      dwg.hatch_for_material("unobtanium") == "")
section.hatch_pattern = "plywood"
viewgen.Generator().rebuild(doc, force=True)
check("asking for a pattern by name wins", section.projection.hatch
      == "plywood", section.projection.hatch)
section.hatch_pattern = ""

print("hatching survives a save and a reload")
saved = os.path.join(WORK, "hatched.ddat")
doc.save(saved)
again = DrawingDocument.load(saved)
back = next(v for v in again.active().views if v.kind == SECTION)
check("the view remembers it is hatched", back.hatch is True)
check("and which pattern was asked for", back.hatch_pattern == "")
viewgen.Generator().rebuild(again, force=True)
check("and the cut faces come back", len(
    back.projection.of_kind(hlr.CUT)) >= 1)
viewgen.Generator().rebuild(doc, force=True)


print("a detail keeps edges that only cross its circle")
# an edge stored as its two ends, one outside, used to vanish entirely
runs = viewgen._clip_to_circle([(-50.0, 0.0), (50.0, 0.0)], 0.0, 0.0, 10.0)
check("the crossing piece survives", len(runs) == 1 and len(runs[0]) == 2,
      runs)
if runs:
    check("cut exactly at the circle",
          near(abs(runs[0][0][0]), 10.0) and near(abs(runs[0][1][0]), 10.0),
          runs[0])


# ==========================================================================
print("the title block resolves what it is asked for")

doc, sheet, base = drawing_of()
doc.properties.update({"Title": "TEST PART", "Author": "aa",
                       "Company": "IITEG", "Revision": "C"})
viewgen.Generator().rebuild(doc)
model_props = viewgen.model_properties(BLOCK, model.shape, model)
values = dwg.properties_for(doc, sheet, model_props)

check("the drawing's own fields", values["Drawing.Title"] == "TEST PART"
      and values["Drawing.Revision"] == "C", values["Drawing.Title"])
check("the sheet's", values["Sheet.Size"] == "A3"
      and values["Sheet.Number"] == "1" and values["Sheet.Count"] == "1",
      (values["Sheet.Size"], values["Sheet.Number"]))
check("the scale, written the way a drawing writes it",
      values["Sheet.Scale"] == "1:1", values["Sheet.Scale"])
check("and the model's, through the base view",
      values["Model.Name"] == "block" and values["Model.Volume"],
      (values["Model.Name"], values["Model.Volume"]))

block = doc.title_blocks["Standard"]
title = next(f for f in block.fields if f.name == "title")
check("a property field reads its property",
      dwg.resolve_field(title, values, sheet) == "TEST PART")
label = next(f for f in block.fields if f.name == "drawn_label")
check("a static field reads itself",
      dwg.resolve_field(label, values, sheet) == "DRAWN")

print("a prompted field takes the answer given on the sheet")
prompted = dwg.TextField("note", dwg.PROMPTED, "Checked by", value="")
sheet.field_values["note"] = "EA"
check("the answer wins", dwg.resolve_field(prompted, values, sheet) == "EA")
check("and an unanswered one falls back to its prompt",
      dwg.resolve_field(dwg.TextField("other", dwg.PROMPTED, "ask me"),
                        values, sheet) == "ask me")

print("scales are written the way drawings write them")
for value, text in ((1.0, "1:1"), (0.5, "1:2"), (2.0, "2:1"), (0.1, "1:10")):
    check("%g reads %s" % (value, text), dwg.scale_text(value) == text,
          dwg.scale_text(value))


# ==========================================================================
print("dimensions measure what they hold")

doc, sheet, base = drawing_of(scale=0.5)
viewgen.Generator().rebuild(doc)
box = base.projection.box

linear = doc.add_annotation(sheet, Annotation(
    kind=LINEAR, view=base.id,
    points=[[box[0], box[1]], [box[2], box[1]]], offset=[0.0, -14.0]))
check("a linear dimension measures across the view",
      near(linear.measure(), 60.0), linear.measure())
check("and reads back in model millimetres, not paper ones",
      linear.caption(0.5) == "120", linear.caption(0.5))

aligned = Annotation(kind=dwg.ALIGNED, view=base.id,
                     points=[[0.0, 0.0], [30.0, 40.0]])
check("an aligned dimension measures along itself",
      near(aligned.measure(), 50.0), aligned.measure())

angular = Annotation(kind=dwg.ANGULAR, view=base.id,
                     points=[[10.0, 0.0], [0.0, 0.0], [0.0, 10.0]])
check("an angular one measures the angle", near(angular.measure(), 90.0),
      angular.measure())
check("and says so in degrees", "90" in angular.caption(1.0)
      and "°" in angular.caption(1.0), angular.caption(1.0))

radial = Annotation(kind=DIAMETER, view=base.id,
                    points=[[0.0, 0.0], [5.0, 0.0]])
check("a diameter is twice its radius", near(radial.measure(), 10.0))
check("and carries the diameter sign",
      radial.caption(1.0).startswith("⌀"), radial.caption(1.0))

override = Annotation(kind=LINEAR, view=base.id,
                      points=[[0.0, 0.0], [10.0, 0.0]], text="SEE NOTE 4")
check("typed text wins over the measurement",
      override.caption(1.0) == "SEE NOTE 4")


# ==========================================================================
print("sheets: add, duplicate, reorder, delete")

doc, sheet, base = drawing_of()
second = doc.add_sheet("A4", dwg.PORTRAIT, "Details")
check("a second sheet", len(doc.sheets) == 2)
check("portrait swaps the paper round",
      near(second.extent()[0], 210.0) and near(second.extent()[1], 297.0),
      second.extent())

copied = doc.duplicate_sheet(sheet.id)
check("duplicating copies the views", len(copied.views) == len(sheet.views))
check("with ids of their own",
      not ({v.id for v in copied.views} & {v.id for v in sheet.views}),
      [v.id for v in copied.views])
check("and a name of its own", copied.name != sheet.name)

doc.move_sheet(copied.id, 0)
check("a sheet can be reordered", doc.sheets[0].id == copied.id)
check("deleting works", doc.remove_sheet(copied.id) and len(doc.sheets) == 2)
while len(doc.sheets) > 1:
    doc.remove_sheet(doc.sheets[-1].id)
check("but never the last one", not doc.remove_sheet(doc.sheets[0].id)
      and len(doc.sheets) == 1)


# ==========================================================================
print("deleting a view takes what was derived from it")

doc, sheet, base = drawing_of()
child = doc.add_view(sheet, View(kind=PROJECTED, parent=base.id,
                                 x=base.x, y=base.y - 100.0))
grandchild = doc.add_view(sheet, View(kind=DETAIL, parent=child.id,
                                      centre=[0.0, 0.0], radius=10.0,
                                      x=base.x + 150.0, y=base.y))
note = doc.add_annotation(sheet, Annotation(kind=LINEAR, view=child.id,
                                            points=[[0, 0], [10, 0]]))
gone = doc.remove_view(sheet, base.id, children=True)
check("all three went", len(gone) == 3 and not sheet.views, gone)
check("and the dimension on one of them went too", not sheet.annotations)

print("or leaves them standing, if that is what was asked")
doc, sheet, base = drawing_of()
child = doc.add_view(sheet, View(kind=PROJECTED, parent=base.id,
                                 x=base.x, y=base.y - 100.0))
doc.remove_view(sheet, base.id, children=False)
check("the child survived", len(sheet.views) == 1)
check("and is no longer pointing at something gone",
      sheet.views[0].parent == 0, sheet.views[0].parent)


# ==========================================================================
print("the file: saved, reopened, and drawn without the model")

doc, sheet, base = drawing_of()
doc.add_view(sheet, View(kind=PROJECTED, parent=base.id,
                         x=base.x, y=base.y - 110.0))
doc.properties["Title"] = "ROUND TRIP"
viewgen.Generator().rebuild(doc)
written = doc.save(doc.path)
check("it wrote a .ddat", written.endswith(".ddat") and os.path.isfile(written))

reopened = DrawingDocument.load(written)
check("the properties came back",
      reopened.properties["Title"] == "ROUND TRIP")
check("the sheets did", len(reopened.sheets) == 1
      and len(reopened.sheets[0].views) == 2)
check("the reference is relative, so the folder can be moved",
      not os.path.isabs(reopened.sheets[0].views[0].ref.path),
      reopened.sheets[0].views[0].ref.path)
check("and every view came back already drawn, from the cache",
      all(v.projection is not None for v in reopened.sheets[0].views))
check("the cached lines are the same ones",
      len(reopened.sheets[0].views[0].projection.lines)
      == len(base.projection.lines))

print("a model that has changed marks its views out of date")
moved = os.path.join(WORK, "changing.pdat")
part = Document()
f = PrimitiveFeature()
f.kind, f.a, f.b, f.c = "box", "40", "30", "10"
part.add_feature(f)
part.rebuild()
part.save(moved)

doc2, sheet2, base2 = drawing_of(part=moved)
viewgen.Generator().rebuild(doc2)
check("drawn once", base2.projection is not None)
f.a = "80"
part.rebuild()
part.save(moved)
report = viewgen.Generator().rebuild(doc2)
check("the change is noticed", report.stale, report.message)
check("and the old view is still there to look at",
      base2.projection is not None)
report = viewgen.Generator().rebuild(doc2, force=True)
check("Update redraws it", near(base2.projection.width, 80.0),
      base2.projection.width)
check("and it is no longer out of date", not report.stale, report.message)

print("a model that has gone is reported, not crashed over")
gone_folder = tempfile.mkdtemp(prefix="datum_drawing_gone_")
doomed = make_part("doomed", [("box", 20, 20, 20, "new")])
doc3, sheet3, base3 = drawing_of(part=doomed)
viewgen.Generator().rebuild(doc3)
os.remove(doomed)
report = viewgen.Generator().rebuild(doc3, force=True)
check("it says which view cannot find its model", report.missing,
      report.message)
check("and the drawing still loads and draws", not report.ok)
check("the broken link is reportable", doc3.broken_links(),
      doc3.broken_links())


# ==========================================================================
print("templates are ordinary drawings in a folder")

templates.ensure_builtins()
shipped = templates.available()
check("some ship with the application", len(shipped) >= 3,
      [t.name for t in shipped])
check("each is a real .ddat",
      all(t.path.endswith(".ddat") and os.path.isfile(t.path)
          for t in shipped))

a3 = templates.find("ISO A3")
check("one can be found by name", a3 is not None and a3.builtin)
started = templates.new_from(a3)
check("a drawing made from it keeps the sheet",
      started.sheets and started.sheets[0].size == "A3",
      [s.size for s in started.sheets])
check("but not the path, so it is untitled", started.path == "")
check("and not the template's own views",
      not any(s.views for s in started.sheets))

project = tempfile.mkdtemp(prefix="datum_drawing_project_")
started.properties["Company"] = "IITEG"
started.path = os.path.join(project, "some.ddat")
started.save()
saved = templates.save_as_template(started, "IITEG A3", project)
check("a drawing can become a template", os.path.isfile(saved))
check("the project's own comes first",
      templates.available(project)[0].name == "IITEG A3",
      [t.name for t in templates.available(project)])
check("and saving it did not move the drawing",
      started.path.endswith("some.ddat"), started.path)


# ==========================================================================
print("output: PDF, SVG, DXF and a picture")

from datum.ui import drawingexport                                 # noqa: E402

doc, sheet, base = drawing_of()
doc.properties["Title"] = "OUTPUT TEST"
doc.add_view(sheet, View(kind=PROJECTED, parent=base.id,
                         x=base.x, y=base.y - 110.0))
doc.add_annotation(sheet, Annotation(
    kind=LINEAR, view=base.id, points=[[-60.0, -30.0], [60.0, -30.0]],
    offset=[0.0, -14.0]))
viewgen.Generator().rebuild(doc)

pdf = drawingexport.to_pdf(doc, os.path.join(WORK, "out.pdf"))
check("a PDF was written", os.path.getsize(pdf) > 3000, os.path.getsize(pdf))
with open(pdf, "rb") as handle:
    head = handle.read(8)
check("and it really is one", head.startswith(b"%PDF"), head)

svg = drawingexport.to_svg(doc, sheet, os.path.join(WORK, "out.svg"))
with open(svg, "r", encoding="utf-8") as handle:
    body = handle.read()
check("an SVG was written", "<svg" in body and len(body) > 2000, len(body))

dxf = drawingexport.to_dxf(doc, sheet, os.path.join(WORK, "out.dxf"))
with open(dxf, "r", encoding="utf-8") as handle:
    body = handle.read()
check("a DXF was written", "SECTION" in body and "LINE" in body)
check("with the views on layers of their own",
      "VIEW_VISIBLE" in body and "VIEW_HIDDEN" in body)
check("and the title block's text in it", "OUTPUT TEST" in body)

png = drawingexport.to_image(doc, sheet, os.path.join(WORK, "out.png"),
                             dpi=60.0)
check("a picture was written", os.path.getsize(png) > 2000)

print("two sheets make two pages")
doc.add_sheet("A4")
multi = drawingexport.to_pdf(doc, os.path.join(WORK, "multi.pdf"))
check("the second page grew the file",
      os.path.getsize(multi) > os.path.getsize(pdf),
      (os.path.getsize(pdf), os.path.getsize(multi)))


# ==========================================================================
print("a parts list counts what an assembly is made of")

from datum.core import bom                                        # noqa: E402
from datum.core.assembly import AssemblyDocument, Occurrence      # noqa: E402
from datum.core.drawing import BALLOON, PartsList                 # noqa: E402

PLATE = make_part("plate", [("box", 40, 40, 5, "new")])
PIN = make_part("pin", [("cylinder", 4, 30, 0, "new")])

rig = AssemblyDocument()
rig.path = os.path.join(WORK, "rig.adat")
for source, count in ((PLATE, 1), (PIN, 3)):
    for i in range(count):
        o = Occurrence(id=rig.new_id(), ref=reference(source, WORK),
                       name="%s:%d" % (os.path.basename(source), i + 1))
        o.placement.position = [i * 50.0, 0.0, 0.0]
        rig.occurrences.append(o)
rig.rebuild()
rig.save()

rows = bom.rows_for(rig, WORK)
check("two parts, not four lines", len(rows) == 2, len(rows))
check("numbered from one", [r.item for r in rows] == [1, 2],
      [r.item for r in rows])
check("and counted", sorted(r.quantity for r in rows) == [1, 3],
      [r.quantity for r in rows])
check("the instance number is not part of the part's name",
      all(":" not in r.part_number for r in rows),
      [r.part_number for r in rows])

print("a suppressed component is not on the list")
rig.occurrences[-1].suppressed = True
fewer = bom.rows_for(rig, WORK)
check("the pin is down to two", [r.quantity for r in fewer
                                 if "pin" in r.path.lower()] == [2],
      [(r.part_number, r.quantity) for r in fewer])
rig.occurrences[-1].suppressed = False

print("the drawing reads it, and the balloons follow the numbering")
doc, sheet, base = drawing_of(part=rig.path, orientation="iso")
report = viewgen.Generator().rebuild(doc)
check("the assembly drew", base.projection is not None, base.error)

table = doc.add_parts_list(sheet, PartsList(view=base.id))
generator = viewgen.Generator()
generator.rebuild(doc, force=True)
check("the list filled itself in", len(table.rows) == 2,
      table.error or len(table.rows))

targets = generator.balloon_targets(doc, sheet, base)
check("every part has somewhere to point at", len(targets) == 2,
      len(targets))
check("and each one offers corners, not just a middle",
      all(len(t) == 3 and t[2] for t in targets))

print("a balloon points at the corner facing it, not through the middle")
target = targets[0]
away = (target[1][0] + 60.0, target[1][1] + 60.0)
corner = viewgen.nearest_corner(target, away)
other = viewgen.nearest_corner(
    target, (target[1][0] - 60.0, target[1][1] - 60.0))
check("which corner depends on where the bubble is", corner != other,
      (corner, other))

balloons = []
for path, middle, corners in targets:
    note = Annotation(kind=BALLOON, view=base.id,
                      points=[list(middle)], offset=[20.0, 20.0],
                      component=path)
    balloons.append(doc.add_annotation(sheet, note))
numbers = [doc.balloon_number(sheet, b) for b in balloons]
check("each balloon found its row", sorted(numbers) == [1, 2], numbers)
generator.rebuild(doc, force=True)
check("and a rebuild writes the number onto the balloon, so the tree and "
      "the sheet agree",
      [b.caption() for b in balloons] == [str(n) for n in numbers],
      [b.caption() for b in balloons])
check("a balloon that found its part is not flagged sick",
      not any(b.sick for b in balloons))

print("renumbering the list renumbers the balloons")
table.rows[0].item, table.rows[1].item = 7, 9
check("the balloons follow without being touched",
      sorted(doc.balloon_number(sheet, b) for b in balloons) == [7, 9],
      [doc.balloon_number(sheet, b) for b in balloons])
generator.rebuild(doc, force=True)
check("and a rebuild puts the real numbers back",
      sorted(doc.balloon_number(sheet, b) for b in balloons) == [1, 2],
      [doc.balloon_number(sheet, b) for b in balloons])

print("a balloon whose part has gone says so rather than lying")
orphan = doc.add_annotation(sheet, Annotation(
    kind=BALLOON, view=base.id, points=[[0.0, 0.0]],
    component=os.path.join(WORK, "never-existed.pdat")))
check("it has no number to show", doc.balloon_number(sheet, orphan) == 0)
check("and draws a question mark instead", orphan.caption() == "?",
      orphan.caption())
doc.remove_annotation(sheet, orphan.id)

print("the table knows how big it is and where its corners are")
table.corner = "bottom-right"
table.x, table.y = 400.0, 40.0
box = table.box()
check("it hangs left of the point it was placed by",
      near(box[2], 400.0) and near(box[0], 400.0 - table.width()), box)
check("and grows upwards from it", near(box[1], 40.0), box)
check("three lines high: two parts and a heading",
      near(box[3] - box[1], 3 * table.row_height), box[3] - box[1])
table.heading = False
check("without the heading it is one shorter",
      near(table.height(), 2 * table.row_height), table.height())
table.heading = True

print("a parts list survives the file, rows and all")
saved = os.path.join(WORK, "listed.ddat")
doc.save(saved)
reopened = DrawingDocument.load(saved)
back = reopened.active()
check("the table came back", len(back.parts_lists) == 1)
check("with the rows it had, so it draws without the model",
      len(back.parts_lists[0].rows) == 2,
      len(back.parts_lists[0].rows))
check("the balloons came back too",
      sum(1 for a in back.annotations if a.kind == BALLOON) == 2)
check("still pointing at their components",
      all(a.component for a in back.annotations if a.kind == BALLOON))
check("and still numbered",
      sorted(reopened.balloon_number(back, a) for a in back.annotations
             if a.kind == BALLOON) == [1, 2])

print("adding a component upstairs turns up on the next rebuild")
extra = Occurrence(id=rig.new_id(), ref=reference(BLOCK, WORK),
                   name="block:1")
extra.placement.position = [0.0, 80.0, 0.0]
rig.occurrences.append(extra)
rig.rebuild()
rig.save()
viewgen.Generator().rebuild(doc, force=True)
check("the list grew by one", len(table.rows) == 3, len(table.rows))
check("and the old balloons kept their numbers",
      sorted(doc.balloon_number(sheet, b) for b in balloons) == [1, 2],
      [doc.balloon_number(sheet, b) for b in balloons])

print("a parts list on a drawing of a single part lists that part")
solo_doc, solo_sheet, solo_base = drawing_of()
solo = solo_doc.add_parts_list(solo_sheet, PartsList(view=solo_base.id))
viewgen.Generator().rebuild(solo_doc, force=True)
check("one row", len(solo.rows) == 1, solo.error or len(solo.rows))
check("which is the part itself", solo.rows[0].part_number == "block",
      solo.rows[0].part_number)


# ==========================================================================
print()
if FAILS:
    print("%d FAILED" % len(FAILS))
    for name in FAILS:
        print("   - %s" % name)
    sys.exit(1)
print("all drawing checks passed")
