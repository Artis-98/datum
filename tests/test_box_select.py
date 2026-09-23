"""Dragging a selection box in the sketcher.

Left to right is a window: only what is completely inside counts.  Right to
left is a crossing box: anything it touches counts, including a line that
runs straight through with both ends outside.
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


def fresh():
    win.new_document(prompt=False)
    win.start_sketch_on_plane("XY")
    pump()
    ed.snap_grid = False
    ed.set_tool("select")
    return ed.sketch


def box(start, end):
    """Drag a selection box from one corner to the other.

    The corners stay clear of (0, 0): a drag that starts on the sketch's
    origin point grabs that point instead of starting a box, which is the
    right behaviour and not what is being tested here.
    """
    ed.clear_selection()
    ed._on_drag_start(start[0], start[1])
    ed._on_drag_move(end[0], end[1])
    ed._on_drag_end()
    pump(1)
    return sorted(ed.selected_entities), sorted(ed.selected_points)


# ==========================================================================
print("a window takes only what is wholly inside")

sketch = fresh()
inside = sketch.add_line((10.0, 10.0), (30.0, 30.0))
straddle = sketch.add_line((20.0, 20.0), (90.0, 20.0))
outside = sketch.add_line((100.0, 100.0), (120.0, 120.0))

entities, points = box((-8.0, -8.0), (50.0, 50.0))
check("the enclosed line is taken", inside in entities, entities)
check("the one hanging out is not", straddle not in entities, entities)
check("nor the one nowhere near", outside not in entities, entities)
check("exactly one entity", len(entities) == 1, entities)
check("the enclosed line's own ends are not offered separately",
      all(pid not in points for pid in sketch.entities[inside].points),
      (points, sketch.entities[inside].points))

print("but loose ends inside it are")
lone = sketch.add_point(15.0, 40.0)
entities, points = box((-8.0, -8.0), (50.0, 50.0))
check("the free point is picked up", lone in points, points)
check("and the straddling line's near end too",
      sketch.entities[straddle].points[0] in points, points)
check("the line itself still is not", straddle not in entities, entities)


# ==========================================================================
print("a crossing box takes anything it touches")

entities, points = box((50.0, 50.0), (-8.0, -8.0))
check("the enclosed line is taken", inside in entities, entities)
check("and so is the one that straddles the edge",
      straddle in entities, entities)
check("the far one still is not", outside not in entities, entities)


print("even a line that passes clean through with both ends outside")
sketch = fresh()
through = sketch.add_line((-50.0, 25.0), (150.0, 25.0))
entities, points = box((60.0, 50.0), (10.0, 0.0))
check("crossing catches it", through in entities, entities)
entities, points = box((10.0, 0.0), (60.0, 50.0))
check("a window does not", through not in entities, entities)


# ==========================================================================
print("it works on curves, not just lines")

sketch = fresh()
circle = sketch.add_circle((0.0, 0.0), 20.0)
entities, points = box((-30.0, -30.0), (30.0, 30.0))
check("a window round the whole circle takes it",
      circle in entities, entities)
entities, points = box((-6.0, -6.0), (6.0, 6.0))
check("a window inside it does not", circle not in entities, entities)
entities, points = box((6.0, 6.0), (-6.0, -6.0))
check("and a crossing box inside it does not either, it touches nothing",
      circle not in entities, entities)
entities, points = box((30.0, 0.0), (10.0, -30.0))
check("but a crossing box clipping the rim does",
      circle in entities, entities)


# ==========================================================================
print("the box knows which way it was dragged")

ed.clear_selection()
ed._on_drag_start(-8.0, -8.0)
ed._on_drag_move(50.0, 50.0)
check("left to right is a window", not ed._box_crossing())
check("and it reports the rect", ed._box_rect() == (-8.0, -8.0, 50.0, 50.0),
      ed._box_rect())
ed._on_drag_end()

ed._on_drag_start(50.0, 50.0)
ed._on_drag_move(-8.0, -8.0)
check("right to left is a crossing box", ed._box_crossing())
check("with the same rect either way",
      ed._box_rect() == (-8.0, -8.0, 50.0, 50.0), ed._box_rect())
ed._on_drag_end()


# ==========================================================================
print("the band is drawn while the drag is live")

sketch = fresh()
sketch.add_line((10.0, 10.0), (30.0, 30.0))
ed.clear_selection()
ed._on_drag_start(-8.0, -8.0)
before = len(win.viewport._preview)
ed._on_drag_move(50.0, 50.0)
pump()
check("four edges of a rubber band appear",
      len(win.viewport._preview) >= 4, len(win.viewport._preview))
ed._on_drag_end()
pump()
check("and it goes away on release",
      ed._box_start is None and ed._box_end is None)


# ==========================================================================
print("a drag that starts on geometry is not a box")

sketch = fresh()
line = sketch.add_line((0.0, 0.0), (40.0, 0.0))
start = sketch.points[sketch.entities[line].points[0]]
ed.clear_selection()
ed._on_drag_start(start.x, start.y)
check("it grabs the point instead", ed._drag_point is not None,
      ed._drag_point)
check("and starts no box", ed._box_start is None)
ed._on_drag_end()
pump()


# ==========================================================================
print("two ends picked by a window can be made coincident")

sketch = fresh()
first = sketch.add_line((0.0, 0.0), (20.0, 0.0))
second = sketch.add_line((24.0, 3.0), (60.0, 3.0))
# the two inner ends sit close together, everything else is far away
entities, points = box((18.0, -4.0), (30.0, 8.0))
check("neither whole line is inside", entities == [], entities)
check("but both inner ends are", len(points) == 2, points)

ed.start_constraint("coincident")
pump()
gap = math.dist(
    (sketch.points[sketch.entities[first].points[1]].x,
     sketch.points[sketch.entities[first].points[1]].y),
    (sketch.points[sketch.entities[second].points[0]].x,
     sketch.points[sketch.entities[second].points[0]].y))
check("and the constraint closed the gap", gap < 1e-6, gap)


# ==========================================================================
print("a window selection can be deleted")

sketch = fresh()
keep = sketch.add_line((100.0, 100.0), (140.0, 140.0))
drop_a = sketch.add_line((0.0, 0.0), (20.0, 0.0))
drop_b = sketch.add_line((0.0, 10.0), (20.0, 10.0))
entities, points = box((-10.0, -10.0), (40.0, 30.0))
check("both near lines are in the window",
      drop_a in entities and drop_b in entities, entities)
ed.delete_selected()
pump()
check("they are gone", drop_a not in ed.sketch.entities
      and drop_b not in ed.sketch.entities, list(ed.sketch.entities))
check("the far one survived", keep in ed.sketch.entities,
      list(ed.sketch.entities))


print("and so can a crossing selection")
sketch = fresh()
keep = sketch.add_line((200.0, 200.0), (240.0, 240.0))
clipped = sketch.add_line((-50.0, 5.0), (50.0, 5.0))
entities, points = box((20.0, 20.0), (0.0, 0.0))
check("the clipped line is selected", clipped in entities, entities)
ed.delete_selected()
pump()
check("it is gone", clipped not in ed.sketch.entities, list(ed.sketch.entities))
check("the far one survived", keep in ed.sketch.entities,
      list(ed.sketch.entities))


# ==========================================================================
print("an empty box selects nothing and says so")

sketch = fresh()
sketch.add_line((100.0, 100.0), (140.0, 140.0))
said = []
ed.status_changed.connect(said.append)
entities, points = box((-8.0, -8.0), (20.0, 20.0))
check("nothing selected", entities == [] and points == [],
      (entities, points))
check("and it says as much", any("Nothing in the box" in m for m in said),
      said)


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
print("all box selection checks passed")
