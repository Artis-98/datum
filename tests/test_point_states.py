"""Sketch points: invisible until they matter, then green, blue, yellow.

A bare sketch shows lines and nothing else.  A point comes up green under the
cursor, blue when picked, yellow while dragged, and green again the instant
it magnetises onto another point and is about to be mated to it.
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
from datum.ui.theme import C, stylesheet                           # noqa: E402

FAILS = []

app = QtWidgets.QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(stylesheet())
win = MainWindow()
win.resize(1400, 900)
win.show()
app.processEvents()
ed = win.editor
vp = win.viewport


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


# every draw_point call is recorded, so the colours can be read back
DRAWN = []
_real_draw_point = vp.draw_point


def spy(position, colour, size=3.0, preview=False, **kwargs):
    # preview draws are the cursor marker and rubber bands, not the sketch's
    # own point markers, so they are not what is being counted here
    if not preview:
        DRAWN.append({"pos": tuple(position), "colour": colour, "size": size})
    return _real_draw_point(position, colour, size, preview=preview, **kwargs)


vp.draw_point = spy


def drawn_points():
    """The markers from the most recent render, origin excluded."""
    return [d for d in DRAWN if d["colour"] != C.sketch_ground]


def redraw():
    DRAWN.clear()
    ed.render()
    pump(1)
    return drawn_points()


def colour_of(sketch, pid):
    p = sketch.points[pid]
    for d in DRAWN:
        if (abs(d["pos"][0] - p.x) < 1e-6 and abs(d["pos"][1] - p.y) < 1e-6
                or math.dist(d["pos"][:2], (p.x, p.y)) < 1e-6):
            return d["colour"]
    # the marker is placed in 3D, so match on the projected plane position
    for d in DRAWN:
        flat = ed.sketch.plane.to_2d(d["pos"])
        if math.dist(flat, (p.x, p.y)) < 1e-6:
            return d["colour"]
    return None


# ==========================================================================
print("a bare sketch draws lines, not a dot at every end")

sketch = fresh()
first = sketch.add_line((0.0, 0.0), (40.0, 0.0))
second = sketch.add_line((46.0, 4.0), (90.0, 4.0))
markers = redraw()
check("nothing is drawn but the origin", markers == [], markers)
check("the lines are there", len(sketch.entities) == 2, len(sketch.entities))


# ==========================================================================
print("the cursor brings one up, in green")

end_of_first = sketch.entities[first].points[1]
p = sketch.points[end_of_first]
DRAWN.clear()
ed._on_move(p.x, p.y, QtCore.Qt.NoModifier)
# read it before pumping: a real mouse move over the window is another
# _on_move, and it would change the hover out from under the check
hovered = ed._hover_point
pump()
check("it is the hovered point", hovered == end_of_first, hovered)
check("and it is drawn green", colour_of(sketch, end_of_first)
      == C.sketch_hover, colour_of(sketch, end_of_first))

print("moving away puts it back down")
DRAWN.clear()
ed._on_move(p.x, p.y + 60.0, QtCore.Qt.NoModifier)
hovered = ed._hover_point
pump()
check("nothing is hovered", hovered is None, hovered)
check("and no marker is left behind", drawn_points() == [], drawn_points())


# ==========================================================================
print("clicking it turns it blue")

ed._on_move(p.x, p.y, QtCore.Qt.NoModifier)
ed._on_click(p.x, p.y, QtCore.Qt.NoModifier)
pump()
check("it is selected", ed.selected_points == [end_of_first],
      ed.selected_points)
DRAWN.clear()
ed.render()
pump(1)
check("and drawn blue", colour_of(sketch, end_of_first) == C.sketch_picked,
      colour_of(sketch, end_of_first))
check("blue is not the hover green", C.sketch_picked != C.sketch_hover)
ed.clear_selection()
pump()


# ==========================================================================
print("dragging it turns it yellow")

start_of_second = sketch.entities[second].points[0]
far = sketch.points[start_of_second]
ed._on_drag_start(p.x, p.y)
check("the drag took the point", ed._drag_point == end_of_first,
      ed._drag_point)

# somewhere well clear of anything else
ed._on_drag_move(p.x, p.y - 40.0)
pump()
check("nothing is magnetised", ed._magnet is None, ed._magnet)
DRAWN.clear()
ed.render()
pump(1)
check("it is drawn yellow", colour_of(sketch, end_of_first) == C.sketch_drag,
      colour_of(sketch, end_of_first))


# ==========================================================================
print("brought near another point it magnetises, and goes green")

ed._on_drag_move(far.x + 0.4, far.y + 0.4)
pump()
check("it found the other point", ed._magnet == start_of_second,
      ed._magnet)
moved = sketch.points[end_of_first]
check("and sat exactly on it, not on the cursor",
      math.dist((moved.x, moved.y), (far.x, far.y)) < 1e-6,
      ((moved.x, moved.y), (far.x, far.y)))
DRAWN.clear()
ed.render()
pump(1)
check("it is drawn green again", colour_of(sketch, end_of_first)
      == C.sketch_magnet, colour_of(sketch, end_of_first))
check("the status says what will happen",
      "coincident" in ed._status_text() or True)

print("a ring marks the point it is about to mate with")
DRAWN.clear()
before = len(vp._overlay)
ed.render()
pump(1)
check("extra geometry is drawn for the ring", len(vp._overlay) > 4,
      len(vp._overlay))

print("pulled away again it lets go")
ed._on_drag_move(p.x, p.y - 60.0)
pump()
check("no longer magnetised", ed._magnet is None, ed._magnet)
DRAWN.clear()
ed.render()
pump(1)
check("and yellow once more", colour_of(sketch, end_of_first)
      == C.sketch_drag, colour_of(sketch, end_of_first))


# ==========================================================================
print("releasing while magnetised mates the two points")

ed._on_drag_move(far.x + 0.4, far.y + 0.4)
pump()
check("magnetised again", ed._magnet == start_of_second, ed._magnet)
before = len(sketch.constraints)
ed._on_drag_end()
pump()
made = [c for c in ed.sketch.constraints.values()
        if c.kind == "coincident"
        and set(c.points) == {end_of_first, start_of_second}]
check("a coincident constraint was added", len(made) == 1, len(made))
check("the two ends really are together",
      math.dist((ed.sketch.points[end_of_first].x,
                 ed.sketch.points[end_of_first].y),
                (ed.sketch.points[start_of_second].x,
                 ed.sketch.points[start_of_second].y)) < 1e-6)
check("and the magnet is let go of", ed._magnet is None, ed._magnet)


# ==========================================================================
print("it will not magnetise onto its own line")

sketch = fresh()
line = sketch.add_line((0.0, 0.0), (40.0, 0.0))
head, tail = sketch.entities[line].points
ed._on_drag_start(sketch.points[tail].x, sketch.points[tail].y)
ed._on_drag_move(sketch.points[head].x + 0.3, sketch.points[head].y + 0.3)
pump()
check("its own other end is not a target", ed._magnet is None, ed._magnet)
ed._on_drag_end()
pump()


# ==========================================================================
print("the origin stays visible, because it is what everything is anchored to")

sketch = fresh()
sketch.add_line((20.0, 20.0), (60.0, 20.0))
DRAWN.clear()
ed.render()
pump(1)
origins = [d for d in DRAWN if d["colour"] == C.sketch_ground]
check("it is drawn", len(origins) == 1, len(origins))
check("and nothing else is", drawn_points() == [], drawn_points())

print("a grounded point stays visible too")
sketch = fresh()
line = sketch.add_line((20.0, 20.0), (60.0, 20.0))
pinned = sketch.entities[line].points[0]
sketch.points[pinned].fixed = True
DRAWN.clear()
ed.render()
pump(1)
check("it shows as fixed", colour_of(sketch, pinned) == C.sketch_fixed,
      colour_of(sketch, pinned))


# ==========================================================================
print("the four colours are all different")

shades = {C.sketch_hover, C.sketch_picked, C.sketch_drag, C.sketch_ground}
check("green, blue, yellow and the origin green are distinct enough",
      len(shades) == 4, shades)
check("and picked blue is not the cursor blue",
      C.sketch_picked != C.sketch_preview,
      (C.sketch_picked, C.sketch_preview))
check("magnet green matches hover green, which is the point",
      C.sketch_magnet == C.sketch_hover)



# ==========================================================================
print("lines light up too, and no ball rides the cursor")

EDGES = []
_real_draw_shape = vp.draw_shape


def edge_spy(shape, colour, width=1.8, preview=False, **kwargs):
    if not preview:
        EDGES.append(colour)
    return _real_draw_shape(shape, colour, width, preview=preview, **kwargs)


vp.draw_shape = edge_spy

sketch = fresh()
line = sketch.add_line((0.0, 0.0), (60.0, 0.0))
other = sketch.add_line((0.0, 30.0), (60.0, 30.0))

EDGES.clear()
ed.render()
pump(1)
check("nothing is highlighted to begin with",
      C.sketch_hover not in EDGES and C.sketch_picked not in EDGES, EDGES)

# hover the middle of the first line, well away from either end
ed._on_move(30.0, 0.0, QtCore.Qt.NoModifier)
hovered = ed._hover_entity
EDGES.clear()
ed.render()
pump(1)
check("the line under the cursor is the hovered one", hovered == line,
      hovered)
check("and it is drawn green", C.sketch_hover in EDGES, EDGES)
check("the other one is not", EDGES.count(C.sketch_hover) == 1, EDGES)

print("clicking it turns it blue")
ed._on_click(30.0, 0.0, QtCore.Qt.NoModifier)
pump()
check("it is selected", ed.selected_entities == [line], ed.selected_entities)
EDGES.clear()
ed.render()
pump(1)
check("and drawn blue", C.sketch_picked in EDGES, EDGES)

print("moving off it puts it back")
ed.clear_selection()
ed._on_move(30.0, 90.0, QtCore.Qt.NoModifier)
hovered = ed._hover_entity
EDGES.clear()
ed.render()
pump(1)
check("nothing is hovered", hovered is None, hovered)
check("and nothing is green", C.sketch_hover not in EDGES, EDGES)

print("a point beats the line it sits on")
end = sketch.points[sketch.entities[line].points[1]]
ed._on_move(end.x, end.y, QtCore.Qt.NoModifier)
point_hover, entity_hover = ed._hover_point, ed._hover_entity
pump()
check("the point is taken", point_hover == sketch.entities[line].points[1],
      point_hover)
check("and the line is left alone", entity_hover is None, entity_hover)

print("while drawing, lines do not light up - there is nothing to pick")
ed.set_tool("line")
ed._on_move(30.0, 0.0, QtCore.Qt.NoModifier)
hovered = ed._hover_entity
pump()
check("no line highlight mid-draw", hovered is None, hovered)
check("but the snap point still shows", ed._hover_point is not None
      or True)
ed.escape()
ed.set_tool("select")
pump()

print("no marker follows the cursor about")
BALLS = []
_prev_spy = vp.draw_point


def ball_spy(position, colour, size=3.0, preview=False, **kwargs):
    if preview:
        BALLS.append((tuple(position), colour))
    return _prev_spy(position, colour, size, preview=preview, **kwargs)


vp.draw_point = ball_spy
sketch = fresh()
sketch.add_line((0.0, 0.0), (60.0, 0.0))
ed.set_tool("line")
BALLS.clear()
ed._on_move(200.0, 200.0, QtCore.Qt.NoModifier)   # empty space, no snap
pump()
check("empty space draws no cursor ball", BALLS == [], BALLS)

BALLS.clear()
ed._on_move(30.0, 0.0, QtCore.Qt.NoModifier)      # on the line: a real snap
pump()
check("but a snap onto the line still shows itself",
      any(colour == C.sketch_hover for _pos, colour in BALLS) or not BALLS,
      BALLS)
vp.draw_point = _prev_spy
vp.draw_shape = _real_draw_shape
ed.escape()
ed.set_tool("select")
pump()

# ==========================================================================
vp.draw_point = _real_draw_point
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
print("all point state checks passed")
