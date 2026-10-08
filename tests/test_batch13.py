"""Sketching the way Inventor does it, round two.

Offset takes a curve's whole loop, shows the copy riding on the cursor on
whichever side it is pulled to, and places it with a click, or with a
typed distance and Enter, which leaves that distance on it as a dimension.

The first dimension of a fresh sketch scales the whole sketch to it, and
frames it.  A double click on the wheel frames everything.

A constraint stays armed for the next pick and the one after, until Esc.
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import harness  # noqa: E402,F401

os.environ["DATUM_SETTINGS_ORG"] = "IITEG-tests"

from PySide6 import QtCore, QtGui, QtWidgets                       # noqa: E402

from datum.core import kernel                                      # noqa: E402
from datum.ui.main_window import MainWindow                        # noqa: E402
from datum.ui.theme import stylesheet                              # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


app = QtWidgets.QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(stylesheet())
win = MainWindow()
win.resize(1400, 900)
win.show()
app.processEvents()
ed = win.editor
vp = win.viewport
NONE = QtCore.Qt.NoModifier


def pump(n=6):
    for _ in range(n):
        app.processEvents()


def settle(ms=500):
    loop = QtCore.QEventLoop()
    QtCore.QTimer.singleShot(ms, loop.quit)
    loop.exec()


def fresh_sketch(plane="XY"):
    win.new_document(prompt=False)
    win.start_sketch_on_plane(plane)
    settle(900)
    ed.auto_constrain = False


def type_text(text):
    for ch in text:
        key = (QtCore.Qt.Key_Period if ch == "."
               else getattr(QtCore.Qt, "Key_%s" % ch))
        ed._on_key(key, ch)
    pump(2)


def dims(kind=None):
    return [c for c in ed.sketch.constraints.values()
            if c.is_dimension and (kind is None or c.kind == kind)]


def lines_at_y(y, tol=0.01):
    s = ed.sketch
    out = []
    for e in s.entities.values():
        if e.kind != "line":
            continue
        a, b = (s.points[p] for p in e.points)
        if abs(a.y - y) < tol and abs(b.y - y) < tol:
            out.append(e.id)
    return out


# ==========================================================================
print("offset: the whole rectangle follows the cursor")
fresh_sketch()
ed.sketch.add_rectangle((0.0, 0.0), (40.0, 20.0))
ed.solve()
ed.render()
before = len(ed.sketch.entities)
ed.set_tool("offset")
ed._on_click(20.0, 20.0, NONE)                    # the top side
pump()
check("picking a side takes its loop of four",
      ed._offset_chain is not None and len(ed._offset_chain[0]) == 4,
      ed._offset_chain)
ed._on_move(20.0, 26.0, NONE)
pump()
check("the distance box follows along", ed.live.isVisible())
check("and shows how far the cursor is",
      ed.live.fields and abs(float(ed.live.fields[0].text()) - 6.0) < 0.05,
      ed.live.fields[0].text() if ed.live.fields else None)
check("the copy is drawn while it is pulled", bool(vp._preview_items)
      if hasattr(vp, "_preview_items") else True)
ed._on_click(20.0, 26.0, NONE)
pump()
check("a click makes four new lines",
      len(ed.sketch.entities) == before + 4,
      len(ed.sketch.entities) - before)
check("outside, where it was pulled to: the top is at y 26",
      len(lines_at_y(26.0, 0.05)) == 1, lines_at_y(26.0, 0.05))
check("and the bottom at y -6", len(lines_at_y(-6.0, 0.05)) == 1)
check("with no dimension, since none was typed", not dims())
check("the tool is ready for the next one", ed.tool == "offset"
      and not ed._pending)

print("offset: a typed distance, inwards, with its dimension")
ed._on_click(20.0, 0.0, NONE)                     # the original bottom
ed._on_move(20.0, 3.0, NONE)                      # pulled inwards
pump()
type_text("4")
ed._on_key(QtCore.Qt.Key_Return, "\r")
pump()
check("Enter makes it at the typed distance, inside",
      len(lines_at_y(4.0, 0.01)) == 1 and len(lines_at_y(16.0, 0.01)) == 1,
      sorted(round(ed.sketch.points[ed.sketch.entities[e].points[0]].y, 3)
             for e in ed.sketch.entities))
gaps = dims("distance_pl")
check("and leaves that distance on it as a dimension",
      len(gaps) == 1 and abs(gaps[0].value - 4.0) < 1e-6,
      [(c.kind, c.value) for c in dims()])


def box_of(eids):
    pts = [ed.sketch.points[p] for e in eids
           for p in ed.sketch.entities[e].points]
    return (min(p.x for p in pts), min(p.y for p in pts),
            max(p.x for p in pts), max(p.y for p in pts))


if gaps:
    lines_now = sorted(e for e, ent in ed.sketch.entities.items()
                       if ent.kind == "line")
    original, inner = lines_now[:4], lines_now[-4:]
    gaps[0].value = 6.0
    ed.solve()
    a, b = box_of(original), box_of(inner)
    check("changing it moves the whole copy, every side",
          all(abs(abs(a[i] - b[i]) - 6.0) < 1e-4 for i in range(4)),
          (a, b, ed.sketch.solve_message))

print("offset: a circle, outwards and in")
fresh_sketch()
ed.sketch.add_circle((0.0, 0.0), 10.0)
ed.solve()
ed.set_tool("offset")
ed._on_click(10.0, 0.0, NONE)
ed._on_move(13.0, 0.0, NONE)
pump()
type_text("2.5")
ed._on_key(QtCore.Qt.Key_Return, "\r")
pump()
radii = sorted(round(e.radius, 3) for e in ed.sketch.entities.values()
               if e.kind == "circle")
check("a circle 2.5 out", radii == [10.0, 12.5], radii)
check("dimensioned as the gap between them",
      [c.kind for c in dims()] == ["radial_gap"], [c.kind for c in dims()])
ed._on_click(10.0, 0.0, NONE)
ed._on_move(7.0, 0.0, NONE)
ed._on_click(7.0, 0.0, NONE)
pump()
radii = sorted(round(e.radius, 2) for e in ed.sketch.entities.values()
               if e.kind == "circle")
check("and one 3 in, by a click", radii[0] == 7.0, radii)

print("offset: a slot keeps its round ends")
fresh_sketch()
ed.sketch.add_slot((0.0, 0.0), (40.0, 0.0), 10.0)
ed.solve()
ed.set_tool("offset")
ed._on_click(20.0, 5.0, NONE)
ed._on_move(20.0, 8.0, NONE)
ed._on_click(20.0, 8.0, NONE)
pump()
arcs = sorted(round(e.radius, 2) for e in ed.sketch.entities.values()
              if e.kind == "arc")
check("its ends come out 3 bigger", arcs == [5.0, 5.0, 8.0, 8.0], arcs)
regions = kernel.sketch_regions(ed.sketch)
check("and the two slots make a ring and a core", len(regions) == 2,
      len(regions))

print("offset: too far in for the shape is refused")
fresh_sketch()
ed.sketch.add_rectangle((0.0, 0.0), (40.0, 20.0))
ed.solve()
count = len(ed.sketch.entities)
ed.set_tool("offset")
ed._on_click(20.0, 0.0, NONE)
ed._on_move(20.0, 3.0, NONE)
type_text("15")
ed._on_key(QtCore.Qt.Key_Return, "\r")
pump()
check("15 in from a 20 high rectangle: nothing made",
      len(ed.sketch.entities) == count)
ed.escape()
check("Esc lets go of the loop", not ed._pending and ed._offset_chain is None)

# ==========================================================================
print("the first dimension scales a fresh sketch")
fresh_sketch()
ed.sketch.add_rectangle((0.0, 0.0), (40.0, 20.0))
ed.solve()
ed.render()
ed.set_tool("dimension")
ed._on_click(20.0, 0.0, NONE)                     # the bottom side
ed._on_click(20.0, -8.0, NONE)                    # clear of it: the label
pump()
ed._value_entered("10000")
pump()
settle(400)
s = ed.sketch
xs = [p.x for p in s.points.values()]
ys = [p.y for p in s.points.values()]
check("the side is 10000", abs(max(xs) - min(xs) - 10000.0) < 1e-3,
      max(xs) - min(xs))
check("and the other side kept its proportion: 5000",
      abs(max(ys) - min(ys) - 5000.0) < 1e-3, max(ys) - min(ys))
check("one dimension", len(dims()) == 1)
x, y = vp.project(s.plane.to_3d(10000.0, 5000.0))
check("and the whole of it is framed on screen",
      0 <= x <= vp.width() and 0 <= y <= vp.height(), (x, y))
ed.undo()
pump()
xs = [p.x for p in ed.sketch.points.values()]
check("one undo takes it back to how it was drawn",
      abs(max(xs) - min(xs) - 40.0) < 1e-6 and not dims(),
      max(xs) - min(xs))

print("the second dimension does not")
vp.fit_all()                     # back down to the 40 mm it was drawn at
pump()
ed.set_tool("dimension")
ed._on_click(20.0, 0.0, NONE)
ed._on_click(20.0, -8.0, NONE)
ed._value_entered("50")
ed.set_tool("dimension")
ed._on_click(0.0, 10.0, NONE)                     # the left side
ed._on_click(-8.0, 10.0, NONE)
ed._value_entered("30")
pump()
xs = [p.x for p in ed.sketch.points.values()]
ys = [p.y for p in ed.sketch.points.values()]
check("the first scaled it: 50 wide", abs(max(xs) - min(xs) - 50.0) < 1e-3,
      max(xs) - min(xs))
check("the second only moved its own side: 30 high",
      abs(max(ys) - min(ys) - 30.0) < 1e-3, max(ys) - min(ys))
win.finish_sketch()
pump()

print("not once there is a body")
win.new_document(prompt=False)
win.start_sketch_on_plane("XY")
settle(600)
ed.sketch.add_rectangle((0.0, 0.0), (40.0, 20.0))
win.finish_sketch()
pump()
from datum.core.features import ExtrudeFeature, ProfileSelection  # noqa: E402
sketch_feature = win.document.sketch_features()[0]
extrude = ExtrudeFeature()
extrude.distance = "10"
extrude.sketch_id = sketch_feature.id
extrude.profiles = ProfileSelection()
extrude.profiles.add(sketch_feature.id, (20.0, 10.0))
win.document.add_feature(extrude)
win.rebuild()
win.start_sketch_on_plane("XZ")
settle(600)
check("a sketch made after the body is not scaled",
      ed.autoscale is False)
win.finish_sketch()
pump()

# ==========================================================================
print("a double click on the wheel frames it all")
fresh_sketch()
ed.sketch.add_rectangle((0.0, 0.0), (40.0, 20.0))
ed.solve()
ed.render()
pump()
for _ in range(6):
    vp.view.Pan(900, 0)
vp.redraw()
pump()
x, y = vp.project(ed.sketch.plane.to_3d(20.0, 10.0))
check("panned well away first", not (0 <= x <= vp.width()), x)
centre = QtCore.QPointF(vp.width() / 2.0, vp.height() / 2.0)
QtWidgets.QApplication.sendEvent(vp, QtGui.QMouseEvent(
    QtCore.QEvent.MouseButtonDblClick, centre, vp.mapToGlobal(centre),
    QtCore.Qt.MiddleButton, QtCore.Qt.MiddleButton, QtCore.Qt.NoModifier))
QtWidgets.QApplication.sendEvent(vp, QtGui.QMouseEvent(
    QtCore.QEvent.MouseButtonRelease, centre, vp.mapToGlobal(centre),
    QtCore.Qt.MiddleButton, QtCore.Qt.NoButton, QtCore.Qt.NoModifier))
pump()
x, y = vp.project(ed.sketch.plane.to_3d(20.0, 10.0))
check("the sketch is back in view",
      0 <= x <= vp.width() and 0 <= y <= vp.height(), (x, y))
check("and the view is not left panning", not vp._navigating)
win.finish_sketch()
pump()

# ==========================================================================
print("a constraint stays armed until Esc")
fresh_sketch()
s = ed.sketch
lines = [s.add_line((0.0, 0.0), (20.0, 0.0), weld=False),
         s.add_line((20.5, 0.5), (20.5, 20.0), weld=False),
         s.add_line((20.0, 20.5), (0.0, 20.5), weld=False),
         s.add_line((-0.5, 20.0), (-0.5, 0.5), weld=False)]
ed.solve()
ed.render()
pump()


def end_of(eid, which):
    p = s.points[s.entities[eid].points[which]]
    return (p.x, p.y)


ed.start_constraint("coincident")
before = sum(1 for c in s.constraints.values() if c.kind == "coincident")
pairs = [((lines[0], 1), (lines[1], 0)),
         ((lines[1], 1), (lines[2], 0)),
         ((lines[2], 1), (lines[3], 0))]
for (ea, wa), (eb, wb) in pairs:
    ed._on_click(*end_of(ea, wa), NONE)
    ed._on_click(*end_of(eb, wb), NONE)
    pump()
after = sum(1 for c in s.constraints.values() if c.kind == "coincident")
check("three corners joined with one press of the button",
      after - before == 3, after - before)
check("and it is still armed", ed._pending_constraint == "coincident")
ed.escape()
check("Esc puts it away", ed._pending_constraint is None)
win.finish_sketch()
pump()

print()
if FAILS:
    print("%d FAILURES: %s" % (len(FAILS), ", ".join(FAILS)))
    sys.exit(1)
print("all batch 13 tests passed")
sys.exit(0)
