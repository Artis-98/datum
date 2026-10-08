"""Start page, profiles, projection, DXF, normal-to and two-way constraints."""
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402,F401


# keep the test run out of the real recent-files list
os.environ["DATUM_SETTINGS_ORG"] = "IITEG-tests"

from PySide6 import QtCore, QtWidgets  # noqa: E402

from datum.core import dxf, fileformat, kernel  # noqa: E402
from datum.core.features import (  # noqa: E402
    ExtrudeFeature, ProfileSelection, SketchFeature,
)
from datum.core.sketch import STANDARD_PLANES, Sketch  # noqa: E402
from datum.ui.main_window import MainWindow  # noqa: E402
from datum.ui.theme import stylesheet  # noqa: E402

FAILS = []
WORK = tempfile.mkdtemp(prefix="datum_b9_")

app = QtWidgets.QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(stylesheet())
win = MainWindow()
win.resize(1400, 900)
win.show()
app.processEvents()
ed = win.editor


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def pump(n=4):
    for _ in range(n):
        app.processEvents()


def near(a, b, tol=1e-4):
    return abs(a - b) < tol


def click(u, v):
    ed._on_move(u, v, QtCore.Qt.NoModifier)
    ed._on_click(u, v, QtCore.Qt.NoModifier)
    pump(1)


def make_box(a="60", b="40", c="20"):
    win.new_document(prompt=False)
    win.new_primitive("box")
    d = win._active_dialog
    d.a.set_text(a)
    d.b.set_text(b)
    d.c.set_text(c)
    d.commit()
    pump()


def top_face():
    return max(kernel.faces(win.document.shape),
               key=lambda f: kernel.shape_centre(f)[2])


# ==========================================================================
print("the start page")
win.show_start_page()
pump()
check("shown on demand", win.on_start_page)
# The pages go quiet, not the whole ribbon: there is no document for
# Extrude to act on, but every reason to still want Open or Check for
# Updates, and disabling the lot took those away too.
check("the ribbon pages are out of the way",
      not win.ribbon.stack.isEnabled()
      and not win.ribbon._buttons["3D Model"].isEnabled())
check("but File still works", win.ribbon.file_button.isEnabled())
check("browser hidden", not win.browser_dock.isVisible())
check("three New tiles", all(t is not None for t in (
    win.start_page.part_tile, win.start_page.assembly_tile,
    win.start_page.drawing_tile)))
check("part is the one that works", win.start_page.part_tile.isEnabled())
check("assembly works too", win.start_page.assembly_tile.isEnabled())
check("and so does drawing", win.start_page.drawing_tile.isEnabled())

win.start_page.new_requested.emit(fileformat.PART)
pump()
check("New Part opens the modeller", not win.on_start_page)
check("ribbon is back", win.ribbon.isEnabled())

win._start_new(fileformat.ASSEMBLY)
pump()
check("New Assembly opens the assembly workspace", win.in_assembly)
win.new_document(prompt=False)
pump()
check("and a new part leaves it again", not win.in_assembly)

# a drawing opens straight onto its sheet, with no model behind it.
# prompt=False skips the template picker, which is a modal the harness
# declines - the same way new_document is driven above.
win.new_drawing(prompt=False)
pump()
check("New Drawing opens the drawing workspace", win.in_drawing)
check("on the sheet canvas, not the 3D view",
      win.stack.currentWidget() is win.sheet_canvas,
      win.stack.currentWidget().__class__.__name__)
win.new_document(prompt=False)
pump()
check("and a new part leaves that too", not win.in_drawing)

print("recent files remember what was saved")
make_box()
path = os.path.join(WORK, "recent_demo.pdat")
win.document.path = path
win.save_document()
pump()
entries = [os.path.normcase(e["path"]) for e in win.start_page.projects.active().recent]
check("saved file is remembered", os.path.normcase(path) in entries, entries)
win.start_page.refresh()
check("it shows in the list", win.start_page.recent_list.count() >= 1)

# ==========================================================================
print("the viewport sizes itself to the widget")
win.show_model()
pump()
check("viewport is ready", win.viewport._ready)
check("sync_size is available and safe", win.viewport.sync_size() is None)
win.resize(1100, 700)
pump()
size = win.viewport.view.Window().Size()
check("OCCT window follows the widget",
      abs(size[0] - win.viewport.width()) <= 2
      and abs(size[1] - win.viewport.height()) <= 2,
      "occt %s vs widget %dx%d" % (size, win.viewport.width(),
                                   win.viewport.height()))

# ==========================================================================
print("normal to a face, with animation")
make_box()
win.viewport.set_view("iso")
pump()
before = win.viewport.view.Camera().Eye()
before = (before.X(), before.Y(), before.Z())
face = top_face()
win.viewport.selected_faces = lambda f=face: [f]
win.normal_to_selected_face()
check("an animation is running", win.viewport._animation is not None)
# let the timer actually tick - processEvents alone does not advance time
deadline = QtCore.QElapsedTimer()
deadline.start()
while win.viewport._animation is not None and deadline.elapsed() < 3000:
    QtWidgets.QApplication.processEvents(
        QtCore.QEventLoop.AllEvents, 20)
win.viewport.finish_animation()
pump()
win.viewport.__dict__.pop("selected_faces", None)
after = win.viewport.view.Camera().Eye()
after = (after.X(), after.Y(), after.Z())
check("the camera moved", math.dist(before, after) > 1.0,
      "%s -> %s" % (before, after))
direction = win.viewport.view.Camera().Direction()
check("it now looks down -Z at the top face",
      abs(abs(direction.Z()) - 1.0) < 1e-3,
      (direction.X(), direction.Y(), direction.Z()))

# ==========================================================================
print("constraints work in both orders")
win.new_document(prompt=False)
win.start_sketch_on_plane("XY")
pump()
ed.set_tool("line")
click(0, 0)
click(50, 5)
ed.escape()
ed.set_tool("select")
line = [e for e in ed.sketch.entities.values() if e.kind == "line"][-1]

before_count = len(ed.sketch.constraints)
ed.selected_entities = [line.id]
ed.start_constraint("horizontal")
pump()
check("selection first, then constraint",
      len(ed.sketch.constraints) == before_count + 1,
      len(ed.sketch.constraints))

ed.set_tool("line")
click(0, 30)
click(40, 36)
ed.escape()
ed.set_tool("select")
second = [e for e in ed.sketch.entities.values() if e.kind == "line"][-1]

before_count = len(ed.sketch.constraints)
armed = []
ed.constraint_armed.connect(armed.append)
ed.clear_selection()
ed.start_constraint("horizontal")
check("constraint arms and waits", ed._pending_constraint == "horizontal")
check("nothing applied yet", len(ed.sketch.constraints) == before_count)
check("the UI was told", armed and armed[0] == "horizontal", armed)

ed._tool_select((20.0, 33.0), QtCore.Qt.NoModifier)
pump()
check("picking after arming applies it",
      len(ed.sketch.constraints) == before_count + 1,
      len(ed.sketch.constraints))
check("it stays armed for the next pick until Esc",
      ed._pending_constraint == "horizontal")
ed.escape()
check("Esc puts it away", ed._pending_constraint is None)

print("a two-point constraint waits for both picks")
ed.clear_selection()
before_count = len(ed.sketch.constraints)
ed.start_constraint("coincident")
pts = list(second.points)
ed.selected_points = [pts[0]]
ed._check_pending_constraint()
check("one point is not enough", len(ed.sketch.constraints) == before_count)
ed.selected_points = [pts[0], line.points[1]]
ed._check_pending_constraint()
pump()
check("two points fire it", len(ed.sketch.constraints) == before_count + 1,
      len(ed.sketch.constraints))

win.finish_sketch()
pump()

# ==========================================================================
print("drawing on existing geometry constrains itself")
win.new_document(prompt=False)
s = win.start_sketch_on_plane("XY") or ed.sketch
pump()
origin = ed.sketch.origin_point
ed.set_tool("circle")
click(0, 0)          # snaps to the grounded origin
click(20, 0)
pump()
circle = [e for e in ed.sketch.entities.values() if e.kind == "circle"][0]
centre_id = circle.points[0]
coincident = [c for c in ed.sketch.constraints.values()
              if c.kind == "coincident"
              and origin.id in c.points and centre_id in c.points]
check("a circle drawn on the origin is tied to it",
      bool(coincident) or centre_id == origin.id,
      [c.kind for c in ed.sketch.constraints.values()])

ed.sketch.add_constraint("diameter", entities=[circle.id], value=40.0)
ed.solve()
check("one dimension then fully defines it", ed.sketch.dof == 0,
      ed.sketch.solve_message)

print("starting a line on an existing edge attaches to it")
ed.set_tool("rect")
click(-60, -40)
click(-20, -10)
pump()
ed.set_tool("line")
lines = [e for e in ed.sketch.entities.values() if e.kind == "line"]
edge = lines[0]
a = ed.sketch.points[edge.points[0]]
b = ed.sketch.points[edge.points[1]]
midpoint = ((a.x + b.x) / 2.0, (a.y + b.y) / 2.0)
before_count = len(ed.sketch.constraints)
click(*midpoint)
click(midpoint[0] + 15, midpoint[1] - 25)
ed.escape()
pump()
attached = [c for c in ed.sketch.constraints.values()
            if c.kind in ("point_on", "coincident")]
check("the new line is attached to what it started on",
      len(ed.sketch.constraints) > before_count and attached,
      len(ed.sketch.constraints) - before_count)

print("dragging a point onto another welds them")
ed.set_tool("select")
# drag a fresh, unattached line's endpoint onto a rectangle corner: they must
# not already share an entity, or welding them would be meaningless
loose = ed.sketch.add_line((100.0, 100.0), (130.0, 100.0), weld=False)
free_id = ed.sketch.entities[loose].points[1]
free = ed.sketch.points[free_id]
own = set(ed.sketch.entities[loose].points)
target = next(p for p in ed.sketch.points.values()
              if p.id not in own and not p.origin and p.fixed is False)
ed.solve()
before_count = len(ed.sketch.constraints)
ed._on_drag_start(free.x, free.y)
ed._on_drag_move(target.x, target.y)
ed._on_drag_end()
pump()
check("a coincident relationship was made",
      len(ed.sketch.constraints) > before_count,
      "%d new, free at %.2f,%.2f target %.2f,%.2f"
      % (len(ed.sketch.constraints) - before_count,
         ed.sketch.points[free_id].x, ed.sketch.points[free_id].y,
         target.x, target.y))
win.finish_sketch()
pump()

# ==========================================================================
print("features select profiles, not whole sketches")
win.new_document(prompt=False)
feature = SketchFeature()
feature.name = "Two Regions"
feature.sketch = Sketch(STANDARD_PLANES["XY"], "Two Regions")
feature.sketch.add_rectangle((0, 0), (40, 30))
feature.sketch.add_rectangle((60, 0), (80, 30))
win.document.add_feature(feature)
win.rebuild()

regions = kernel.sketch_regions(feature.sketch)
check("two closed regions found", len(regions) == 2, len(regions))
areas = sorted(round(r["area"]) for r in regions)
check("their areas are right", areas == [600, 1200], areas)

extrude = ExtrudeFeature()
extrude.sketch_id = feature.id
extrude.distance = "10"
win.document.add_feature(extrude)
win.rebuild()
check("no selection uses every region",
      near(kernel.volume(win.document.shape), (1200 + 600) * 10, 1.0),
      kernel.volume(win.document.shape))

small = min(regions, key=lambda r: r["area"])
extrude.profiles = ProfileSelection()
extrude.profiles.add(feature.id, small["centre"])
win.rebuild()
check("selecting one region extrudes only that one",
      near(kernel.volume(win.document.shape), 600 * 10, 1.0),
      kernel.volume(win.document.shape))
check("the summary says so", "1 profile" in extrude.summary(),
      extrude.summary())

print("the selection survives a save and reload")
path = os.path.join(WORK, "profiles.pdat")
path = win.document.save(path)
from datum.core.document import Document

reloaded = Document.load(path)
check("still one region after reload",
      near(kernel.volume(reloaded.shape), 600 * 10, 1.0),
      kernel.volume(reloaded.shape))

# the dialog's profile picking moved into the viewport; see test_batch10.py

# ==========================================================================
print("project geometry")
make_box("50", "30", "20")
top = top_face()
win.viewport.selected_faces = lambda f=top: [f]
win._sketch_on_selected_face()
pump()
win.viewport.__dict__.pop("selected_faces", None)
check("sketching on the top face", ed.active)

before_entities = len(ed.sketch.entities)
count = ed.project_geometry(win.document.shape)
pump()
check("edges were projected", count > 0, count)
check("they became sketch entities",
      len(ed.sketch.entities) > before_entities,
      len(ed.sketch.entities) - before_entities)

xs = [p.x for p in ed.sketch.points.values() if not p.origin]
ys = [p.y for p in ed.sketch.points.values() if not p.origin]
check("the box outline projects to 50 x 30",
      near(max(xs) - min(xs), 50.0, 0.05) and near(max(ys) - min(ys), 30.0, 0.05),
      "%.2f x %.2f" % (max(xs) - min(xs), max(ys) - min(ys)))
check("projected geometry is pinned",
      all(p.fixed for p in ed.sketch.points.values() if not p.origin))
win.finish_sketch()
pump()

print("an angled edge projects to its shadow")
angled = Sketch(STANDARD_PLANES["XZ"], "probe")
projected = kernel.project_to_plane(win.document.shape, STANDARD_PLANES["XZ"])
check("projecting onto another plane works", len(projected) > 0, len(projected))
kinds = {k for k, _d in projected}
check("it produces lines", "line" in kinds, kinds)

# ==========================================================================
print("DXF export")
make_box("40", "25", "10")
face = top_face()
out = os.path.join(WORK, "face.dxf")
written = dxf.face_to_dxf(face, out)
check("a file was written", os.path.exists(written), written)
text = open(written, encoding="ascii").read()
check("it declares R12", "AC1009" in text)
check("it has an entities section", "ENTITIES" in text)
check("it contains lines", "\nLINE\n" in text)
check("it closes properly", text.rstrip().endswith("EOF"))
check("units are millimetres", "$INSUNITS" in text)

lines = text.count("\nLINE\n")
check("four edges for a rectangular face", lines == 4, lines)

print("a face with a hole exports its circle")
win.new_document(prompt=False)
holed = SketchFeature()
holed.sketch = Sketch(STANDARD_PLANES["XY"], "Plate")
holed.sketch.add_rectangle((0, 0), (60, 40))
holed.sketch.add_circle((30, 20), 8)
win.document.add_feature(holed)
ex = ExtrudeFeature()
ex.sketch_id = holed.id
ex.distance = "6"
win.document.add_feature(ex)
win.rebuild()
face = top_face()
out2 = os.path.join(WORK, "holed.dxf")
dxf.face_to_dxf(face, out2)
text2 = open(out2, encoding="ascii").read()
check("the hole came out as a CIRCLE", "\nCIRCLE\n" in text2)
check("and the outline as lines", text2.count("\nLINE\n") == 4,
      text2.count("\nLINE\n"))

print("sketches export too")
out3 = os.path.join(WORK, "sketch.dxf")
dxf.sketch_to_dxf(holed.sketch, out3)
text3 = open(out3, encoding="ascii").read()
check("sketch DXF written", "CIRCLE" in text3 and "LINE" in text3)

print()
if FAILS:
    print("%d FAILURES: %s" % (len(FAILS), ", ".join(FAILS)))
    sys.exit(1)
print("all batch tests passed")
sys.exit(0)
