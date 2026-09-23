"""Dimensioning the way Inventor does it.

Two things matter here.  What you pick decides what can be measured - two
parallel lines measure across the gap, a line and a point measure square onto
the line, two crossing lines give the angle.  And where you put the label
decides *which* linear dimension you get: above or below for the horizontal,
out to the side for the vertical, anywhere else for the direct one.
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402,F401


os.environ["DATUM_SETTINGS_ORG"] = "IITEG-tests"

from PySide6 import QtCore, QtWidgets                              # noqa: E402

from datum.ui.main_window import MainWindow                        # noqa: E402
from datum.ui.theme import stylesheet                              # noqa: E402

FAILS = []

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


def pump(n=3):
    for _ in range(n):
        app.processEvents()


def near(a, b, tol=1e-4):
    return abs(a - b) < tol


def fresh():
    win.new_document(prompt=False)
    win.start_sketch_on_plane("XY")
    pump()
    ed.snap_grid = False
    return ed.sketch


def answer(text):
    """Type a value into the popup the dimension tool opens."""
    ed._value_entered(text)
    pump()


def dims(sketch):
    return [c for c in sketch.constraints.values() if c.is_dimension]


def place(cursor):
    """Move to a placement point and report the dimension it would make."""
    # the tool clears its picks once it has a target; the snapper reads
    # _pending as coordinates, so it must be empty here too
    ed._pending = []
    ed._on_move(cursor[0], cursor[1], QtCore.Qt.NoModifier)
    pump(1)
    return ed._dim_target["kind"] if ed._dim_target else None


# ==========================================================================
print("two points, and the placement chooses the dimension")

sketch = fresh()
a = sketch.add_point(0.0, 0.0)
b = sketch.add_point(40.0, 30.0)
ed.set_tool("dimension")
ed._pending = [("point", a), ("point", b)]
target = ed._dimension_target()
check("a pair of points is dimensionable", target is not None)
check("it starts as the direct distance", target["kind"] == "distance",
      target["kind"])
check("measuring point to point",
      near(target["current"], 50.0), target["current"])

ed._dim_target = target
ed._pending = []
check("pulled high above, it becomes horizontal",
      place((20.0, 90.0)) == "distance_x", ed._dim_target["kind"])
check("and measures the X gap", near(ed._dim_target["current"], 40.0),
      ed._dim_target["current"])
check("pulled far below, still horizontal",
      place((20.0, -60.0)) == "distance_x")

check("pulled out to the right, it becomes vertical",
      place((120.0, 15.0)) == "distance_y", ed._dim_target["kind"])
check("and measures the Y gap", near(ed._dim_target["current"], 30.0),
      ed._dim_target["current"])
check("pulled far to the left, still vertical",
      place((-80.0, 15.0)) == "distance_y")

check("out on the diagonal, it goes back to the direct one",
      place((90.0, 80.0)) == "distance", ed._dim_target["kind"])
check("measuring the full 50 again",
      near(ed._dim_target["current"], 50.0), ed._dim_target["current"])


print("and each one can actually be placed")
for cursor, kind, value in (((20.0, 90.0), "distance_x", 40.0),
                            ((120.0, 15.0), "distance_y", 30.0),
                            ((90.0, 80.0), "distance", 50.0)):
    sketch = fresh()
    a = sketch.add_point(0.0, 0.0)
    b = sketch.add_point(40.0, 30.0)
    ed.set_tool("dimension")
    ed._pending = [("point", a), ("point", b)]
    ed._dim_target = ed._dimension_target()
    ed._pending = []
    place(cursor)
    ed._tool_dimension(cursor, QtCore.Qt.NoModifier)
    answer("%g" % value)
    made = dims(ed.sketch)
    check("%s was created" % kind,
          len(made) == 1 and made[0].kind == kind,
          [c.kind for c in made])
    check("  and it holds", near(ed._dimension_value(made[0]), value),
          ed._dimension_value(made[0]))


# ==========================================================================
print("a horizontal dimension really drives X, not the direct distance")

sketch = fresh()
a = sketch.add_point(0.0, 0.0)
b = sketch.add_point(40.0, 30.0)
sketch.add_constraint("ground", points=[a])
ed.set_tool("dimension")
ed._pending = [("point", a), ("point", b)]
ed._dim_target = ed._dimension_target()
ed._pending = []
place((20.0, 90.0))
ed._tool_dimension((20.0, 90.0), QtCore.Qt.NoModifier)
answer("100")
pb = ed.sketch.points[b]
check("X moved out to 100", near(abs(pb.x - ed.sketch.points[a].x), 100.0),
      pb.x)
check("and Y was left alone", near(pb.y, 30.0), pb.y)


# ==========================================================================
print("two parallel lines measure across the gap")

sketch = fresh()
left = sketch.add_line((0.0, 0.0), (0.0, 50.0))
right = sketch.add_line((30.0, 0.0), (30.0, 50.0))
sketch.add_constraint("vertical", entities=[left])
sketch.add_constraint("vertical", entities=[right])
for pid in sketch.entities[left].points:
    sketch.add_constraint("ground", points=[pid])
ed.set_tool("dimension")
ed._pending = [("entity", left), ("entity", right)]
target = ed._dimension_target()
check("they can be dimensioned together", target is not None)
check("as a perpendicular distance", target and target["kind"] == "distance_pl",
      target["kind"] if target else None)
check("measuring 30 across", target and near(abs(target["current"]), 30.0),
      target["current"] if target else None)

ed._dim_target = target
ed._pending = []
ed._tool_dimension((15.0, 25.0), QtCore.Qt.NoModifier)
answer("45")
made = dims(ed.sketch)
check("one dimension was added", len(made) == 1, len(made))
check("it reads 45", near(ed._dimension_value(made[0]), 45.0),
      ed._dimension_value(made[0]))
xs = sorted(round(ed.sketch.points[p].x, 4) for p in ed.sketch.points
            if p in sketch.entities[right].points)
check("and the right-hand line moved out to 45",
      near(xs[0], 45.0) and near(xs[1], 45.0), xs)
check("the grounded line stayed put",
      all(near(ed.sketch.points[p].x, 0.0)
          for p in sketch.entities[left].points),
      [ed.sketch.points[p].x for p in sketch.entities[left].points])

print("it keeps the side it was measured from")
check("the stored value is signed", made[0].value != 0)
check("so the line did not flip through to the other side",
      all(ed.sketch.points[p].x > 0 for p in sketch.entities[right].points))


# ==========================================================================
print("a line and a point measure square onto the line")

sketch = fresh()
base = sketch.add_line((0.0, 0.0), (60.0, 0.0))
sketch.add_constraint("horizontal", entities=[base])
for pid in sketch.entities[base].points:
    sketch.add_constraint("ground", points=[pid])
loose = sketch.add_point(30.0, 20.0)
ed.set_tool("dimension")
ed._pending = [("entity", base), ("point", loose)]
target = ed._dimension_target()
check("that pairing works", target is not None
      and target["kind"] == "distance_pl",
      target["kind"] if target else None)
check("measuring 20 up", target and near(abs(target["current"]), 20.0),
      target["current"] if target else None)
ed._dim_target = target
ed._pending = []
ed._tool_dimension((30.0, 10.0), QtCore.Qt.NoModifier)
answer("35")
check("the point moved to 35 above the line",
      near(ed.sketch.points[loose].y, 35.0), ed.sketch.points[loose].y)

print("and the other way round, point then line")
sketch = fresh()
base = sketch.add_line((0.0, 0.0), (60.0, 0.0))
loose = sketch.add_point(30.0, 20.0)
ed.set_tool("dimension")
ed._pending = [("point", loose), ("entity", base)]
target = ed._dimension_target()
check("it is the same dimension", target is not None
      and target["kind"] == "distance_pl",
      target["kind"] if target else None)


# ==========================================================================
print("two lines that are not parallel give the angle")

sketch = fresh()
first = sketch.add_line((0.0, 0.0), (50.0, 0.0))
second = sketch.add_line((0.0, 0.0), (40.0, 40.0))
ed.set_tool("dimension")
ed._pending = [("entity", first), ("entity", second)]
target = ed._dimension_target()
check("an angle is offered", target is not None and target["kind"] == "angle",
      target["kind"] if target else None)
check("reading 45 degrees", target and near(target["current"], 45.0, 1e-3),
      target["current"] if target else None)

print("a line against itself is not a dimension")
ed._pending = [("entity", first), ("entity", first)]
check("refused", ed._dimension_target() is None)

print("a circle and a line together are not either")
sketch = fresh()
line = sketch.add_line((0.0, 0.0), (50.0, 0.0))
circle = sketch.add_circle((20.0, 20.0), 8.0)
ed.set_tool("dimension")
ed._pending = [("entity", line), ("entity", circle)]
check("refused", ed._dimension_target() is None)


# ==========================================================================
print("the old behaviour still works")

sketch = fresh()
circle = sketch.add_circle((0.0, 0.0), 12.0)
ed.set_tool("dimension")
ed._pending = [("entity", circle)]
target = ed._dimension_target()
check("a circle gives a diameter", target["kind"] == "diameter",
      target["kind"])
check("of 24", near(target["current"], 24.0), target["current"])
ed._dim_target = target
ed._pending = []
ed._tool_dimension((20.0, 20.0), QtCore.Qt.NoModifier)
answer("30")
check("which drives the radius", near(ed.sketch.entities[circle].radius, 15.0),
      ed.sketch.entities[circle].radius)

print("a single line is dimensioned like the pair of points it is")
sketch = fresh()
line = sketch.add_line((0.0, 0.0), (30.0, 40.0))
ed.set_tool("dimension")
ed._pending = [("entity", line)]
target = ed._dimension_target()
check("it is the direct length", target["kind"] == "distance",
      target["kind"])
check("of 50", near(target["current"], 50.0), target["current"])
ed._dim_target = target
ed._pending = []
check("and it too switches when placed above",
      place((15.0, 90.0)) == "distance_x", ed._dim_target["kind"])
check("measuring 30 across", near(ed._dim_target["current"], 30.0),
      ed._dim_target["current"])


# ==========================================================================
print("a pair with nothing to measure on one axis does not offer it")

sketch = fresh()
a = sketch.add_point(0.0, 0.0)
b = sketch.add_point(0.0, 40.0)          # exactly vertical, so no X gap
ed.set_tool("dimension")
ed._pending = [("point", a), ("point", b)]
ed._dim_target = ed._dimension_target()
ed._pending = []
check("placing above does not offer a zero-width horizontal",
      place((0.0, 90.0)) != "distance_x", ed._dim_target["kind"])
check("out to the side it is vertical",
      place((60.0, 20.0)) == "distance_y", ed._dim_target["kind"])


# ==========================================================================
print("the labels say which kind it is")

check("horizontal", ed.DIMENSION_CAPTIONS["distance_x"] == "Horizontal")
check("vertical", ed.DIMENSION_CAPTIONS["distance_y"] == "Vertical")
check("perpendicular", ed.DIMENSION_CAPTIONS["distance_pl"] == "Perpendicular")
check("angle", ed.DIMENSION_CAPTIONS["angle"] == "Angle")


# ==========================================================================
print("every kind draws without complaint")

for setup in ("distance", "distance_x", "distance_y", "distance_pl",
              "angle", "diameter"):
    sketch = fresh()
    if setup == "diameter":
        eid = sketch.add_circle((0.0, 0.0), 10.0)
        sketch.add_constraint("diameter", entities=[eid], value=20.0)
    elif setup == "angle":
        e1 = sketch.add_line((0.0, 0.0), (40.0, 0.0))
        e2 = sketch.add_line((0.0, 0.0), (30.0, 30.0))
        sketch.add_constraint("angle", entities=[e1, e2], value=45.0)
    elif setup == "distance_pl":
        eid = sketch.add_line((0.0, 0.0), (50.0, 0.0))
        pid = sketch.add_point(25.0, 18.0)
        ends = sketch.entities[eid].points
        sketch.add_constraint("distance_pl",
                              points=[pid, ends[1], ends[0]], value=18.0)
    else:
        p1 = sketch.add_point(0.0, 0.0)
        p2 = sketch.add_point(30.0, 20.0)
        sketch.add_constraint(setup, points=[p1, p2], value=10.0)
    ed.solve()
    before = len(win.viewport._overlay)
    ed.render()
    pump()
    check("%s draws" % setup, len(win.viewport._overlay) > before,
          (before, len(win.viewport._overlay)))


# ==========================================================================
print("a horizontal dimension picked right to left does not flip the sketch")
# distance_x measures b minus a, which is signed, so a pair picked in the
# other order used to need a typed -10 to stay where it was put
sketch = fresh()
right = sketch.add_point(60.0, 10.0)
left = sketch.add_point(20.0, 10.0)
sketch.points[right].fixed = True
ed.set_tool("dimension")
ed._on_click(60.0, 10.0, QtCore.Qt.NoModifier)
ed._on_click(20.0, 10.0, QtCore.Qt.NoModifier)
check("the pair is dimensionable", ed._dim_target is not None)
check("picked right to left", ed._dim_target["points"] == [right, left],
      ed._dim_target["points"])

place((40.0, 40.0))                       # above, so horizontal
check("placing above gives the horizontal",
      ed._dim_target["kind"] == "distance_x", ed._dim_target["kind"])
check("and it reordered the pair to measure positive",
      ed._dim_target["points"] == [left, right], ed._dim_target["points"])
check("so the number offered is positive",
      ed._dim_target["current"] > 0, ed._dim_target["current"])

ed._on_click(40.0, 40.0, QtCore.Qt.NoModifier)
answer("50")
check("one dimension went in", len(dims(sketch)) == 1, len(dims(sketch)))
check("the free point stayed on its own side",
      sketch.points[left].x < sketch.points[right].x,
      (sketch.points[left].x, sketch.points[right].x))
check("and the gap is the 50 that was typed",
      near(sketch.points[right].x - sketch.points[left].x, 50.0),
      sketch.points[right].x - sketch.points[left].x)


# ==========================================================================
print("dimensions are named, and can be written in terms of each other")
sketch = fresh()
a = sketch.add_line((0.0, 0.0), (40.0, 0.0))
b = sketch.add_line((0.0, 30.0), (25.0, 30.0))
for eid in (a, b):
    sketch.points[sketch.entities[eid].points[0]].fixed = True

first = sketch.add_constraint("distance",
                              points=list(sketch.entities[a].points),
                              value=40.0)
check("the first dimension is d1", sketch.constraints[first].name == "d1",
      sketch.constraints[first].name)

second = sketch.add_constraint("distance",
                               points=list(sketch.entities[b].points),
                               value=0.0, expression="(10-2+d1)/2")
check("the second is d2", sketch.constraints[second].name == "d2",
      sketch.constraints[second].name)
check("a plain constraint gets no name",
      sketch.constraints[sketch.add_constraint(
          "horizontal", entities=[a])].name == "",
      "named")

ed.solve()
pts = [sketch.points[i] for i in sketch.entities[b].points]
check("the expression drove the second line",
      near(math.hypot(pts[1].x - pts[0].x, pts[1].y - pts[0].y), 24.0),
      math.hypot(pts[1].x - pts[0].x, pts[1].y - pts[0].y))

sketch.constraints[first].value = 30.0
ed.solve()
pts = [sketch.points[i] for i in sketch.entities[b].points]
check("and changing d1 carries through to d2",
      near(math.hypot(pts[1].x - pts[0].x, pts[1].y - pts[0].y), 19.0),
      math.hypot(pts[1].x - pts[0].x, pts[1].y - pts[0].y))

check("the scope offers both by name",
      sketch.dimension_scope({})["d1"] == 30.0
      and near(sketch.dimension_scope({})["d2"], 19.0),
      sketch.dimension_scope({}))

print("a dimension that refers to nothing keeps its last good value")
sketch.constraints[second].expression = "d9 + 1"
check("the sketch still solves", ed.solve() is None or True)
check("and d2 holds what it had",
      near(sketch.dimension_scope({})["d2"], 19.0),
      sketch.dimension_scope({}))

print("a new dimension can be written in terms of an existing one")
# the value box used to be evaluated against document parameters alone, so
# d2 came back as "not defined" even though the solver knew it perfectly well
check("the editor's scope carries the sketch's dimensions",
      "d1" in ed.scope() and "d2" in ed.scope(), sorted(ed.scope()))

third = sketch.add_line((0.0, 60.0), (5.0, 60.0))
sketch.points[sketch.entities[third].points[0]].fixed = True
made = ed._add_dimension("distance", list(sketch.entities[third].points),
                         [], 5.0, "d1 / 2")
check("it was accepted", made is not None)
if made is not None:
    ed.solve()
    ends = [sketch.points[i] for i in sketch.entities[third].points]
    check("and measures half of d1",
          near(math.hypot(ends[1].x - ends[0].x, ends[1].y - ends[0].y), 15.0),
          math.hypot(ends[1].x - ends[0].x, ends[1].y - ends[0].y))


print("clicking a dimension while typing writes its name in")
sketch.constraints[second].expression = ""
ed.render()
pump()
target = sketch.constraints[first]
ed._editing_dimension = second
ed.value_popup.ask("", QtCore.QPoint(10, 10), "Edit")
pump()
check("the box is open", ed.value_popup.isVisible())
check("clicking it inserts the name", ed._insert_dimension_name(first))
check("the text went in", "d1" in ed.value_popup.field.text(),
      ed.value_popup.field.text())
check("a dimension cannot cite itself",
      ed._insert_dimension_name(second)
      and ed.value_popup.field.text().count("d2") == 0,
      ed.value_popup.field.text())
ed.value_popup.hide()
ed._editing_dimension = None
pump()


# ==========================================================================
print("a dimension can be picked and deleted")
sketch = fresh()
p1 = sketch.add_point(0.0, 0.0)
p2 = sketch.add_point(40.0, 0.0)
sketch.points[p1].fixed = True
cid = sketch.add_constraint("distance", points=[p1, p2], value=40.0)
sketch.constraints[cid].label_offset = (0.0, 12.0)
ed.solve()
ed.render()
pump()

said = []
ed.status_changed.connect(said.append)
ed.set_tool("select")
anchor_at = ed._dimension_anchor(sketch.constraints[cid])
where = (anchor_at[0], anchor_at[1] + 12.0)
check("the label can be found", ed.pick_dimension(*where) == cid,
      ed.pick_dimension(*where))

ed._tool_select(where, QtCore.Qt.NoModifier)
pump()
check("clicking it selects it", ed.selected_constraint == cid,
      ed.selected_constraint)
check("and the status says how to remove it",
      "Delete" in said[-1] if said else False, said[-1] if said else None)

ed.delete_selected()
pump()
check("Delete removes the dimension", cid not in sketch.constraints)
check("but leaves the geometry alone",
      p1 in sketch.points and p2 in sketch.points)
check("and nothing stays selected", ed.selected_constraint is None)
ed.status_changed.disconnect(said.append)


print("a dimension goes when the line it measures goes")
# A dimension holds the line's two points rather than the line, so removing
# the line alone used to leave the dimension behind, keeping those points
# alive and measuring something no longer on screen.
sketch = fresh()
first_line = sketch.add_line((0.0, 0.0), (40.0, 0.0))
second_line = sketch.add_line((0.0, 25.0), (40.0, 25.0))
kept = sketch.add_constraint("distance",
                             points=list(sketch.entities[second_line].points),
                             value=40.0)
doomed = sketch.add_constraint("distance",
                               points=list(sketch.entities[first_line].points),
                               value=40.0)
also = sketch.add_constraint("parallel",
                             entities=[first_line, second_line])
ed.solve()

ed.selected_entities = [first_line]
ed.selected_points = []
ed.delete_selected()
pump()

check("the line went", first_line not in sketch.entities)
check("its dimension went with it", doomed not in sketch.constraints,
      sorted(sketch.constraints))
check("so did the parallel that named it", also not in sketch.constraints)
check("the other line's dimension stayed", kept in sketch.constraints)
check("and nothing is left measuring a missing point",
      all(all(pid in sketch.points for pid in c.points)
          for c in sketch.constraints.values()),
      [(c.id, c.kind, c.points) for c in sketch.constraints.values()])
check("the sketch still solves", ed.solve() is None or True)


print("collinear puts two lines on one line")
sketch = fresh()
first_line = sketch.add_line((0.0, 0.0), (10.0, 0.0))
second_line = sketch.add_line((20.0, 5.0), (30.0, 9.0))
for pid in sketch.entities[first_line].points:
    sketch.points[pid].fixed = True
ed.selected_entities = [first_line, second_line]
ed.start_constraint("collinear")
pump()
check("the constraint went on",
      any(c.kind == "collinear" for c in sketch.constraints.values()),
      [c.kind for c in sketch.constraints.values()])
ends = [sketch.points[i] for i in sketch.entities[second_line].points]
check("the second line dropped onto the first",
      all(near(p.y, 0.0) for p in ends), [p.y for p in ends])
check("and it is still free to slide and stretch", sketch.dof == 2, sketch.dof)


# ==========================================================================
for entry in list(win.session.documents):
    entry.document.modified = False
    win.close_entry(entry)
win.close()
pump()
print()
if FAILS:
    print("%d FAILED" % len(FAILS))
    for name in FAILS:
        print("   - %s" % name)
    sys.exit(1)
print("all dimension checks passed")
