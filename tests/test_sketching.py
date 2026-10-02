"""Sketcher behaviour: grounding, colours, dimension flow and heads-up input."""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402,F401


from PySide6 import QtCore, QtWidgets  # noqa: E402

from datum.core import kernel  # noqa: E402
from datum.core.sketch import STANDARD_PLANES, Sketch  # noqa: E402
from datum.ui.main_window import MainWindow  # noqa: E402
from datum.ui.theme import C, stylesheet  # noqa: E402

FAILS = []
app = QtWidgets.QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(stylesheet())
win = MainWindow()
win.resize(1400, 880)
win.show()
app.processEvents()

ed = win.editor


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def pump(n=3):
    for _ in range(n):
        app.processEvents()


def near(a, b, tol=1e-4):
    return abs(a - b) < tol


def move(u, v):
    ed._on_move(u, v, QtCore.Qt.NoModifier)
    pump(1)


def click(u, v):
    ed._on_move(u, v, QtCore.Qt.NoModifier)
    ed._on_click(u, v, QtCore.Qt.NoModifier)
    pump(1)


def start_sketch(plane="XY"):
    win.start_sketch_on_plane(plane)
    pump()
    return ed.sketch


def answer(text):
    """Stand in for typing into the value popup and pressing Enter."""
    ed.value_popup.field.setText(text)
    ed.value_popup._accept()
    pump(1)


# ==========================================================================
print("every sketch is grounded at its plane origin")
win.new_document(prompt=False)
s = start_sketch("XY")
origin = s.origin_point
check("an origin point exists", origin is not None)
check("it sits at 0,0", near(origin.x, 0) and near(origin.y, 0))
check("it is grounded", origin.fixed)
check("it is flagged as the origin", origin.origin)
win.finish_sketch()
pump()

print("a sketch on a face is grounded too")
win.new_primitive("box")
d = win._active_dialog
d.a.set_text("60")
d.b.set_text("40")
d.c.set_text("20")
d.commit()
pump()
top = max(kernel.faces(win.document.shape),
          key=lambda f: kernel.shape_centre(f)[2])
win.viewport.selected_faces = lambda f=top: [f]
win._sketch_on_selected_face()
pump()
win.viewport.__dict__.pop("selected_faces", None)
check("face sketch entered", ed.active)
face_origin = ed.sketch.origin_point
check("face sketch has a grounded origin",
      face_origin is not None and face_origin.fixed)

print("the origin survives pruning and cannot be un-grounded")
ed.set_tool("line")
click(10, 10)
click(30, 10)
ed.escape()
lines = [e for e in ed.sketch.entities.values() if e.kind == "line"]
ed.selected_entities = [lines[0].id]
ed.delete_selected()
pump()
check("origin still there after deleting everything",
      ed.sketch.origin_point is not None)
ed.selected_points = [ed.sketch.origin_point.id]
ed.selected_entities = []
ed.apply_constraint("ground")
check("origin stays grounded", ed.sketch.origin_point.fixed)
win.finish_sketch()
pump()

# ==========================================================================
print("constrained geometry reads as constrained")
win.new_document(prompt=False)
s = start_sketch("XY")
ed.set_tool("rect")
click(0, 0)
click(40, 25)
ed.set_tool("select")
pump()

ids = sorted(s.entities)
check("rectangle drawn", len(ids) == 4, len(ids))
check("nothing is constrained yet",
      not any(s.entity_constrained(e) for e in ids),
      [s.entity_constrained(e) for e in ids])
check("its corners are free", len(s.free_points) > 0, len(s.free_points))

corner = s.entities[ids[0]].points
s.add_constraint("coincident", points=[corner[0], s.origin_point.id])
s.add_constraint("distance_x", points=[corner[0], corner[1]], value=40.0)
left = s.entities[ids[3]].points
s.add_constraint("distance_y", points=[left[1], left[0]], value=25.0)
ed.solve()
check("sketch is fully constrained", s.dof == 0, s.solve_message)
check("every edge now reads constrained",
      all(s.entity_constrained(e) for e in ids),
      [s.entity_constrained(e) for e in ids])
check("no free points left", s.free_points == set(), s.free_points)

print("colours follow that state")
check("constrained colour is white", C.sketch_line == "#e8ecef")
check("free colour is purple-blue", C.sketch_free == "#9a8cf2")
win.finish_sketch()
pump()

# ==========================================================================
print("the grid is off but snapping still works")
s = start_sketch("XY")
check("grid not drawn", not win.viewport.grid_visible)
check("snapping still on", ed.snap_grid)
step = ed.grid_step
snapped = ed._snap(5.06 * step, 1.96 * step, QtCore.Qt.NoModifier)
check("cursor snapped to the grid", near(snapped[0], 5.0 * step)
      and near(snapped[1], 2.0 * step), (snapped, step))
ed.set_show_grid(True)
check("grid can be switched on", win.viewport.grid_visible)
ed.set_show_grid(False)
check("and off again", not win.viewport.grid_visible)

# ==========================================================================
print("dimension flow: pick, place, then type")
ed.set_tool("line")
click(0, 0)
click(50, 0)
ed.escape()
ed.set_tool("select")
line = [e for e in ed.sketch.entities.values() if e.kind == "line"][-1]
a = ed.sketch.points[line.points[0]]
b = ed.sketch.points[line.points[1]]

ed.set_tool("dimension")
click((a.x + b.x) / 2.0, (a.y + b.y) / 2.0)
check("a dimension target was picked", ed._dim_target is not None)
check("it measures the line's length",
      near(ed._dim_target["current"], math.dist((a.x, a.y), (b.x, b.y))),
      ed._dim_target)

move(25.0, 18.0)
check("offset is perpendicular to the line",
      near(ed._dim_offset[0], 0.0, 1e-6) and ed._dim_offset[1] > 0,
      ed._dim_offset)
move(25.0, -12.0)
check("it follows to the other side", ed._dim_offset[1] < 0, ed._dim_offset)

move(60.0, -12.0)
check("sliding along the line does not move the label",
      near(ed._dim_offset[0], 0.0, 1e-6), ed._dim_offset)

before = len(ed.sketch.constraints)
click(25.0, -12.0)
check("clicking asks for a value", ed.value_popup.isVisible())
check("target cleared once placed", ed._dim_target is None)
answer("64")
check("dimension created", len(ed.sketch.constraints) == before + 1)
check("it drove the geometry",
      near(math.dist((ed.sketch.points[line.points[0]].x,
                      ed.sketch.points[line.points[0]].y),
                     (ed.sketch.points[line.points[1]].x,
                      ed.sketch.points[line.points[1]].y)), 64.0, 1e-3),
      math.dist((ed.sketch.points[line.points[0]].x,
                 ed.sketch.points[line.points[0]].y),
                (ed.sketch.points[line.points[1]].x,
                 ed.sketch.points[line.points[1]].y)))

dim = [c for c in ed.sketch.constraints.values() if c.is_dimension][-1]
check("label offset kept its placement", dim.label_offset[1] < 0,
      dim.label_offset)

print("cancelling the popup adds nothing")
count = len(ed.sketch.constraints)
ed.set_tool("dimension")
click((a.x + b.x) / 2.0, (a.y + b.y) / 2.0)
click(25.0, 20.0)
ed.value_popup.field.setText("")
ed.value_popup._accept()
pump()
check("no dimension added on cancel", len(ed.sketch.constraints) == count,
      len(ed.sketch.constraints))

print("double-clicking a dimension edits it")
ed.set_tool("select")
anchor = ed._dimension_anchor(dim)
label = (anchor[0] + dim.label_offset[0], anchor[1] + dim.label_offset[1])
found = ed.pick_dimension(*label)
check("the dimension can be picked", found == dim.id, found)
ed._on_double_click(*label)
pump()
check("edit popup opened", ed.value_popup.isVisible())
answer("30")
check("value changed", near(dim.value, 30.0), dim.value)
check("geometry followed",
      near(math.dist((ed.sketch.points[line.points[0]].x,
                      ed.sketch.points[line.points[0]].y),
                     (ed.sketch.points[line.points[1]].x,
                      ed.sketch.points[line.points[1]].y)), 30.0, 1e-3))

print("an expression works as a value too")
win.document.params.add("bar_len", "45")
# the line shrank, so the label moved with its midpoint - find it again
anchor = ed._dimension_anchor(dim)
label = (anchor[0] + dim.label_offset[0], anchor[1] + dim.label_offset[1])
ed._on_double_click(*label)
check("edit popup opened again", ed.value_popup.isVisible())
answer("bar_len")
check("expression stored", dim.expression == "bar_len", dim.expression)
check("expression drove the length", near(dim.value, 45.0, 1e-6), dim.value)
win.finish_sketch()
pump()

# ==========================================================================
print("heads-up input while drawing a rectangle")
win.new_document(prompt=False)
s = start_sketch("XY")
ed.set_tool("rect")
click(0, 0)
move(20, 15)
check("live bar appeared", ed.live.isVisible())
check("two fields", len(ed.live.fields) == 2, len(ed.live.fields))
check("width tracks the cursor", ed.live.fields[0].text().startswith("20"),
      ed.live.fields[0].text())
check("height tracks too", ed.live.fields[1].text().startswith("15"),
      ed.live.fields[1].text())

for ch in "60":
    ed._on_key(ord(ch), ch)
check("typing locks the width", ed.live.locked[0])
check("width shows what was typed", ed.live.fields[0].text() == "60",
      ed.live.fields[0].text())
ed._on_key(QtCore.Qt.Key_Tab, "\t")
check("tab moves to the height", ed.live.active == 1, ed.live.active)
for ch in "35":
    ed._on_key(ord(ch), ch)
ed._on_key(QtCore.Qt.Key_Return, "\r")
pump()

check("rectangle created", len([e for e in s.entities.values()
                                if e.kind == "line"]) == 4,
      len(s.entities))
check("live bar dismissed", not ed.live.isVisible())
xs = [p.x for p in s.points.values() if not p.origin]
ys = [p.y for p in s.points.values() if not p.origin]
check("width is exactly 60", near(max(xs) - min(xs), 60.0, 1e-3),
      max(xs) - min(xs))
check("height is exactly 35", near(max(ys) - min(ys), 35.0, 1e-3),
      max(ys) - min(ys))
dims = [c for c in s.constraints.values() if c.is_dimension]
check("driving dimensions were added", len(dims) == 2, len(dims))
check("they are the typed values",
      sorted(round(c.value) for c in dims) == [35, 60],
      [c.value for c in dims])
win.finish_sketch()
pump()

print("heads-up input for a circle")
s = start_sketch("XY")
ed.set_tool("circle")
click(0, 0)
move(10, 0)   # grid snapping is on, so use a point that lands on it
check("one field for diameter", len(ed.live.fields) == 1, len(ed.live.fields))
check("diameter tracks", ed.live.fields[0].text().startswith("20"),
      ed.live.fields[0].text())
for ch in "24":
    ed._on_key(ord(ch), ch)
ed._on_key(QtCore.Qt.Key_Return, "\r")
pump()
circles = [e for e in s.entities.values() if e.kind == "circle"]
check("circle created", len(circles) == 1, len(circles))
check("radius is 12", near(circles[0].radius, 12.0, 1e-3), circles[0].radius)
check("a diameter dimension was added",
      any(c.kind == "diameter" for c in s.constraints.values()))

print("escape drops the heads-up input")
ed.set_tool("rect")
click(0, 0)
move(10, 10)
check("bar showing again", ed.live.isVisible())
ed.escape()
check("escape hides it", not ed.live.isVisible())
win.finish_sketch()
pump()

print()
if FAILS:
    print("%d FAILURES: %s" % (len(FAILS), ", ".join(FAILS)))
    sys.exit(1)
print("all sketching tests passed")
sys.exit(0)
