"""Constraints and dimensions that take two picks rather than one.

Every one of these existed in the solver already and could not be asked
for from the sketch. A line on its own is a measurable thing, so the
dimension tool armed on the first pick and read the second as placing the
label, which made a line against a point, and a line against a line,
unreachable. Coincident against a line attached anywhere along it, with no
way to say "the middle".
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import harness  # noqa: E402,F401

os.environ["DATUM_SETTINGS_ORG"] = "IITEG-pairs-tests"

from PySide6 import QtCore, QtWidgets                          # noqa: E402

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


def accept():
    """Answer the value box the way pressing Enter does."""
    if not ed.value_popup.isVisible():
        return None
    text = ed.value_popup.field.text()
    ed.value_popup.hide()
    ed._value_entered(text)
    pump(3)
    return text


def sketch():
    win.new_document()
    pump(3)
    win.start_sketch_on_plane("XY")
    pump(8)
    return ed.sketch


def kinds(s):
    return sorted(c.kind for c in s.constraints.values())


def value_of(s, kind):
    for c in s.constraints.values():
        if c.kind == kind:
            return c.value
    return None


# ==========================================================================
print("Coincident onto the middle of a line")

s = sketch()
base = s.add_line((0.0, 0.0), (100.0, 0.0), weld=False)
arm = s.add_line((20.0, 60.0), (40.0, 40.0), weld=False)
tip = s.entities[arm].points[1]
s.add_constraint("ground", entities=[base])
ed.render()
pump(3)

ed.start_constraint("coincident")
pump(3)
click(40.0, 40.0)                       # the loose point
click(50.0, 0.0)                        # the middle of the grounded line
pump(5)

p = s.points[tip]
check("it made a midpoint constraint, not a point-on",
      "midpoint" in kinds(s), kinds(s))
check("and the point sits exactly on the middle",
      abs(p.x - 50.0) < 1e-6 and abs(p.y) < 1e-6, (p.x, p.y))

print("clicking the same line away from its middle still means anywhere on it")
s = sketch()
base = s.add_line((0.0, 0.0), (100.0, 0.0), weld=False)
arm = s.add_line((20.0, 60.0), (40.0, 40.0), weld=False)
tip = s.entities[arm].points[1]
s.add_constraint("ground", entities=[base])
ed.render()
pump(3)
ed.start_constraint("coincident")
pump(3)
click(40.0, 40.0)
click(85.0, 0.0)                        # nowhere near the middle
pump(5)
check("that one is a plain point-on", "point_on" in kinds(s), kinds(s))
check("and it did not get dragged to the middle",
      abs(s.points[tip].x - 50.0) > 1.0, s.points[tip].x)


# ==========================================================================
print("Dimension from a line to a point, square to the line")

s = sketch()
base = s.add_line((0.0, 0.0), (100.0, 0.0), weld=False)
arm = s.add_line((20.0, 60.0), (40.0, 40.0), weld=False)
tip = s.entities[arm].points[1]         # 40 mm above the base line
ed.render()
pump(3)

ed.set_tool("dimension")
pump(2)
click(70.0, 0.0)                        # the line
check("one pick does not commit the tool to the line's own length",
      ed._dim_target is not None and ed._dim_picks == [("entity", base)],
      (ed._dim_target, ed._dim_picks))
click(40.0, 40.0)                       # the point
check("the second pick switches it to the perpendicular distance",
      ed._dim_target is not None
      and ed._dim_target["kind"] == "distance_pl",
      ed._dim_target and ed._dim_target["kind"])
click(-40.0, 25.0)                      # place the label clear of everything
shown = accept()
pump(4)
check("a perpendicular dimension was made", "distance_pl" in kinds(s),
      kinds(s))
check("and it reads the real 40 mm", shown is not None
      and abs(float(shown) - 40.0) < 1e-3, shown)


# ==========================================================================
print("Dimension between two parallel lines is a distance")

s = sketch()
one = s.add_line((0.0, 0.0), (100.0, 0.0), weld=False)
two = s.add_line((0.0, 30.0), (100.0, 30.0), weld=False)
ed.render()
pump(3)
ed.set_tool("dimension")
pump(2)
click(50.0, 0.0)
click(50.0, 30.0)
check("it resolved to a distance, not to one line's length",
      ed._dim_target is not None
      and ed._dim_target["kind"] == "distance_pl",
      ed._dim_target and ed._dim_target["kind"])
click(-50.0, 15.0)
shown = accept()
pump(4)
check("the dimension was made", "distance_pl" in kinds(s), kinds(s))
check("and it reads the 30 mm gap",
      shown is not None and abs(float(shown) - 30.0) < 1e-3, shown)


# ==========================================================================
print("Dimension between two lines that are not parallel is an angle")

s = sketch()
one = s.add_line((0.0, 0.0), (100.0, 0.0), weld=False)
two = s.add_line((0.0, 0.0), (80.0, 60.0), weld=False)
ed.render()
pump(3)
ed.set_tool("dimension")
pump(2)
click(50.0, 0.0)
click(40.0, 30.0)
check("it resolved to an angle",
      ed._dim_target is not None and ed._dim_target["kind"] == "angle",
      ed._dim_target and ed._dim_target["kind"])
click(30.0, 8.0)
shown = accept()
pump(4)
check("an angle dimension was made", "angle" in kinds(s), kinds(s))
# 80 across, 60 up, so atan(60/80)
check("and it reads the real angle",
      shown is not None
      and abs(float(shown) - math.degrees(math.atan2(60.0, 80.0))) < 0.05,
      shown)


# ==========================================================================
print("a single line on its own still dimensions its own length")
# the whole change hinges on not breaking the common case

s = sketch()
only = s.add_line((0.0, 0.0), (60.0, 0.0), weld=False)
ed.render()
pump(3)
ed.set_tool("dimension")
pump(2)
click(30.0, 0.0)
click(30.0, -25.0)                      # place it below, clear of the line
shown = accept()
pump(4)
check("a length dimension was made",
      any(c.kind.startswith("distance") for c in s.constraints.values()),
      kinds(s))
check("and it reads 60", shown is not None
      and abs(float(shown) - 60.0) < 1e-3, shown)


# ==========================================================================
print("two picks that mean nothing together are refused, not guessed at")

s = sketch()
line = s.add_line((0.0, 0.0), (100.0, 0.0), weld=False)
circle = s.add_circle((50.0, 60.0), 15.0)
ed.render()
pump(3)
ed.set_tool("dimension")
pump(2)
before = len(s.constraints)
click(30.0, 0.0)                        # the line
click(50.0, 75.0)                       # the circle's rim
pump(3)
# a line and a circle is not a pair this measures; it should say so rather
# than inventing something
check("nothing was silently created",
      len(s.constraints) == before, kinds(s))


# ==========================================================================
print("Project Geometry takes the edges you point at, not the whole body")

from datum.core import kernel                                  # noqa: E402
from datum.core.features import PrimitiveFeature               # noqa: E402
from OCP.TopAbs import TopAbs_EDGE                             # noqa: E402

win.new_document()
pump(4)
f = PrimitiveFeature()
f.kind, f.a, f.b, f.c, f.operation = "box", "60", "40", "20", "new"
win.document.add_feature(f)
win.document.rebuild()
pump(4)
win.start_sketch_on_plane("XY")
pump(8)
s = ed.sketch
check("the sketch starts empty", len(s.entities) == 0, len(s.entities))

win.project_geometry()
pump(4)
check("it arms as a tool rather than projecting everything",
      ed.tool == "project", ed.tool)
check("and model edges become pickable while the sketch is open",
      win.viewport.edge_picking and win.viewport.selection_mode == "edge",
      (win.viewport.edge_picking, win.viewport.selection_mode))
check("nothing has been projected just by arming it",
      len(s.entities) == 0, len(s.entities))

edges = kernel.explore(win.document.shape, TopAbs_EDGE)
# a box standing on the sketch plane has vertical edges too, and those
# project to nothing at all, so find one that has a shadow to cast
made, square = 0, 0
for e in edges:
    got = ed.project_one(e)
    if got and not made:
        made = got
        break
    if not got:
        square += 1
pump(3)
check("one pick brings its edge over", made >= 1 and len(s.entities) == made,
      (len(s.entities), made))
check("and not the other eleven", len(s.entities) < len(edges),
      (len(s.entities), len(edges)))

print("an edge square to the sketch has no shadow, and is refused quietly")
check("those exist on a box and made nothing", square >= 1, square)

print("leaving the tool gives the sketch its clicks back")
ed.set_tool("select")
pump(3)
check("edge picking is off", not win.viewport.edge_picking)
check("and the selection mode with it",
      win.viewport.selection_mode == "none", win.viewport.selection_mode)


# ==========================================================================
print()
if FAILS:
    print("%d FAILED" % len(FAILS))
    for name in FAILS:
        print("   - %s" % name)
    sys.exit(1)
print("all sketch pair checks passed")
