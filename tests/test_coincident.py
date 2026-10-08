"""Coincident, tried every way somebody actually reaches for it.

Inventor's Coincident does two jobs: it merges two points, and it drops a
point onto a curve. DATUM keeps those as two constraint kinds internally,
which is fine, but the button has to cover both or half of what people ask
of it silently does nothing. That is what this is here to stop happening
again.
"""
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import harness  # noqa: E402,F401

os.environ["DATUM_SETTINGS_ORG"] = "IITEG-coincident-tests"

from PySide6 import QtCore, QtWidgets                          # noqa: E402

from datum.core.sketch import Sketch                           # noqa: E402
from datum.ui.main_window import MainWindow                    # noqa: E402
from datum.ui.theme import stylesheet                          # noqa: E402

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


def pump(n=5):
    for _ in range(n):
        app.processEvents()


def click(u, v, mods=QtCore.Qt.NoModifier):
    ed._on_move(u, v, mods)
    ed._on_click(u, v, mods)
    pump(2)


def two_lines(gap=30.0):
    """A fresh sketch with two lines whose ends are `gap` apart."""
    win.new_document()
    pump(3)
    win.start_sketch_on_plane("XY")
    pump(8)
    s = ed.sketch
    a = s.add_line((0.0, 0.0), (40.0, 0.0), weld=False)
    b = s.add_line((40.0 + gap, 0.0), (90.0, 30.0), weld=False)
    ed.render()
    pump(3)
    return s, s.entities[a].points[1], s.entities[b].points[0]


def at(s, pid):
    p = s.points[pid]
    return (round(p.x, 4), round(p.y, 4))


def kinds(s):
    return sorted(c.kind for c in s.constraints.values())


# ==========================================================================
print("the solver joins two points when told to")

s = Sketch()
a = s.entities[s.add_line((0, 0), (50, 0))]
b = s.entities[s.add_line((80, 20), (120, 40))]
pa, pb = a.points[1], b.points[0]
s.add_constraint("coincident", points=[pa, pb])
check("it solved", s.solve())
check("and the two points are now one place",
      at(s, pa) == at(s, pb), (at(s, pa), at(s, pb)))


# ==========================================================================
print("pick the two points, then press the button")

s, pa, pb = two_lines()
ed.set_tool("select")
click(40.0, 0.0)
click(70.0, 0.0, QtCore.Qt.ControlModifier)
check("both points got selected", len(ed.selected_points) == 2,
      ed.selected_points)
ed.start_constraint("coincident")
pump(4)
check("the constraint was made", "coincident" in kinds(s), kinds(s))
check("and the points moved together", at(s, pa) == at(s, pb),
      (at(s, pa), at(s, pb)))


# ==========================================================================
print("press the button, then pick the two points")

s, pa, pb = two_lines()
ed.start_constraint("coincident")
pump(3)
check("the tool armed and waits", ed._pending_constraint == "coincident")
click(40.0, 0.0)
check("one point is not enough to fire it",
      ed._pending_constraint == "coincident")
click(70.0, 0.0)
pump(4)
check("the second pick fires it, and it stays armed for the next",
      ed._pending_constraint == "coincident" and "coincident" in kinds(s),
      (ed._pending_constraint, kinds(s)))
ed.escape()
check("the constraint was made", "coincident" in kinds(s), kinds(s))
check("and the points moved together", at(s, pa) == at(s, pb),
      (at(s, pa), at(s, pb)))


# ==========================================================================
print("points already close together are still two separate picks")
# coincident is exactly what you use on points that nearly touch, so the
# picking has to tell them apart rather than hitting the same one twice

for gap in (5.0, 2.0, 0.5):
    s, pa, pb = two_lines(gap)
    ed.start_constraint("coincident")
    pump(3)
    click(40.0, 0.0)
    click(40.0 + gap, 0.0)
    pump(4)
    check("%.1f mm apart: joined" % gap, at(s, pa) == at(s, pb),
          (at(s, pa), at(s, pb), kinds(s)))


# ==========================================================================
print("a point and a line, which is what Inventor's Coincident also does")

win.new_document()
pump(3)
win.start_sketch_on_plane("XY")
pump(8)
s = ed.sketch
edge = s.add_line((0.0, 0.0), (100.0, 0.0), weld=False)
floater = s.add_line((30.0, 40.0), (50.0, 20.0), weld=False)
loose = s.entities[floater].points[1]
for pid in s.entities[edge].points:            # hold the edge still
    s.add_constraint("fix", points=[pid])
ed.render()
pump(3)

ed.start_constraint("coincident")
pump(3)
click(50.0, 20.0)
check("the point was picked", ed.selected_points == [loose],
      ed.selected_points)
click(70.0, 0.0)
pump(5)

check("picking a line after a point completes it, rather than waiting "
      "for a second point for ever",
      any(c.kind in ("point_on", "coincident")
          for c in s.constraints.values())
      and not ed.selected_points, kinds(s))
ed.escape()
check("a constraint really was made",
      any(c.kind in ("point_on", "coincident") for c in s.constraints.values()),
      kinds(s))

ax, ay = at(s, s.entities[edge].points[0])
bx, by = at(s, s.entities[edge].points[1])
p = s.points[loose]
den = math.hypot(bx - ax, by - ay) or 1.0
distance = abs((bx - ax) * (ay - p.y) - (ax - p.x) * (by - ay)) / den
# a micron, which is well inside what the solver converges to and
# far tighter than anything a drawing or a machine cares about
check("and the point is on the line, not near it", distance < 1e-3,
      "%.6f mm away" % distance)


# ==========================================================================
print("grounding really holds, from both the button and the core call")

# The solver keeps grounding on the point rather than as an equation, so a
# call that records the constraint but never sets the flag looks like it
# worked and does nothing.  Both routes are checked because only one of
# them used to set it.
for route in ("button", "add_constraint"):
    win.new_document()
    pump(3)
    win.start_sketch_on_plane("XY")
    pump(8)
    s = ed.sketch
    edge = s.add_line((0.0, 0.0), (100.0, 0.0), weld=False)
    drop = s.add_line((30.0, 40.0), (50.0, 20.0), weld=False)
    loose = s.entities[drop].points[1]
    a, b = s.entities[edge].points

    if route == "button":
        ed.clear_selection()
        ed.selected_entities = [edge]
        ed.apply_constraint("ground")
    else:
        s.add_constraint("ground", entities=[edge])
    pump(3)

    s.add_constraint("point_on", points=[loose], entities=[edge])
    ed.solve()
    pump(3)
    held = abs(s.points[a].y) < 1e-6 and abs(s.points[b].y) < 1e-6
    landed = abs(s.points[loose].y) < 1e-3
    check("%s: the grounded edge did not move" % route, held,
          (at(s, a), at(s, b)))
    check("%s: and the loose point came to it instead" % route, landed,
          at(s, loose))


# ==========================================================================
print("the hint says both things it accepts")

description = ed.NEEDS["coincident"][0]
check("point-to-point is mentioned", "point" in description, description)
check("and a line is too", "line" in description, description)


# ==========================================================================
print("asking for it with nothing useful selected says so")

win.new_document()
pump(3)
win.start_sketch_on_plane("XY")
pump(8)
s = ed.sketch
s.add_line((0.0, 0.0), (40.0, 0.0), weld=False)
ed.clear_selection()
ed.selected_entities = [list(s.entities)[0]]      # a line on its own
before = len(s.constraints)
ed.apply_constraint("coincident")
pump(3)
check("no constraint was invented from a single line",
      len(s.constraints) == before, kinds(s))


# ==========================================================================
print()
if FAILS:
    print("%d FAILED" % len(FAILS))
    for name in FAILS:
        print("   - %s" % name)
    sys.exit(1)
print("all coincident checks passed")
