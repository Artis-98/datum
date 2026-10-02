"""Merry_Janet's sketching gripes, from the Reddit thread (#118 to #121).

The grid is spaced in the part's own unit, so a part in inches snaps to
0.1 in and not 0.197.  Starting a line on the middle of another holds it
there with a midpoint constraint.  The line tool shows its angle beside its
length, and an angle typed there holds the line at it.  A sketch left
visible shows its dimensions, and one can be clicked from a new sketch to
reuse it.
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import harness  # noqa: E402,F401

os.environ["DATUM_SETTINGS_ORG"] = "IITEG-tests"

from PySide6 import QtCore, QtWidgets                              # noqa: E402

from datum.core import units as unitlib                            # noqa: E402
from datum.core.features import ExtrudeFeature                     # noqa: E402
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
vp = win.viewport


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def near(a, b, tol=1e-4):
    return abs(a - b) <= tol


def pump(n=3):
    for _ in range(n):
        app.processEvents()


def click(u, v):
    ed._on_move(u, v, QtCore.Qt.NoModifier)
    ed._on_click(u, v, QtCore.Qt.NoModifier)
    pump(1)


def keys(text):
    for ch in text:
        if ch == "\t":
            ed._on_key(QtCore.Qt.Key_Tab, "")
        elif ch == "\n":
            ed._on_key(QtCore.Qt.Key_Return, "")
        else:
            ed._on_key(ord(ch.upper()), ch)
    pump(1)


def round_step(step):
    """Whether a grid step is 1, 2 or 5 times a power of ten."""
    mantissa = step / 10.0 ** math.floor(math.log10(step) + 1e-9)
    return any(near(mantissa, nice, 1e-6) for nice in (1.0, 2.0, 5.0))


def heading(sketch, eid):
    a, b = (sketch.points[p] for p in sketch.entities[eid].points)
    return math.degrees(math.atan2(b.y - a.y, b.x - a.x)) % 360.0


def length(sketch, eid):
    a, b = (sketch.points[p] for p in sketch.entities[eid].points)
    return math.hypot(b.x - a.x, b.y - a.y)


# ------------------------------------------------------------------ grid
print("the grid is spaced in the part's own unit")
win.new_document(prompt=False)
pump()
win.start_sketch_on_plane("XY")
pump()
sketch = ed.sketch
step = ed.grid_step
check("in millimetres it is a round number of millimetres",
      round_step(step), step)
win.set_document_units("in")
pump()
inches = unitlib.to_unit(ed.grid_step, "in")
check("switched to inches, a round number of inches",
      round_step(inches), inches)
check("not 5 mm in disguise", not near(inches, 5.0 / 25.4, 1e-6), inches)

ed.snap_grid = True
step = ed.grid_step
far = (37 * step + 0.05 * step, -23 * step - 0.05 * step)
snapped = ed._snap(*far)
check("a cursor by a grid point snaps onto it",
      near(snapped[0], 37 * step, 1e-9) and near(snapped[1], -23 * step,
                                                 1e-9), snapped)
check("which is a whole number of grid steps in inches",
      near(unitlib.to_unit(snapped[0], "in") / inches, 37.0, 1e-6))

ed.set_show_grid(True)
pump()
check("the grid drawn and the grid snapped to are the same one",
      near(vp.grid_step, ed.grid_step, 1e-12))
ed.set_show_grid(False)
win.set_document_units("mm")
pump()
check("back in millimetres, so is the grid",
      round_step(ed.grid_step), ed.grid_step)

# -------------------------------------------------------------- midpoint
print()
print("starting a line on the middle of another holds it there")
ed.snap_grid = False
ed.set_tool("line")
click(10.0, 10.0)
click(50.0, 10.0)
ed.escape()
pump()
first = max(sketch.entities)
tol = 0.3 * 10.0 * vp.pixel_scale()
ed._on_move(30.0 + tol, 10.0 + tol, QtCore.Qt.NoModifier)
check("the cursor latches onto the midpoint", ed._snap_info == "midpoint",
      ed._snap_info)
click(30.0 + tol, 10.0 + tol)
click(30.0, 40.0)
ed.escape()
pump()
second = max(sketch.entities)
start = sketch.entities[second].points[0]
mids = [c for c in sketch.constraints.values() if c.kind == "midpoint"
        and c.entities == [first] and c.points == [start]]
check("with a midpoint constraint, not just on the line", len(mids) == 1,
      [(c.kind, c.points, c.entities) for c in sketch.constraints.values()])
end = sketch.entities[first].points[1]
sketch.solve({}, anchors={end: (70.0, 10.0)})
p = sketch.points[start]
a, b = (sketch.points[q] for q in sketch.entities[first].points)
check("so stretching the line keeps it in the middle",
      b.x > 55.0 and near(p.x, (a.x + b.x) / 2.0, 1e-3)
      and near(p.y, (a.y + b.y) / 2.0, 1e-3), (p.x, p.y, a.x, b.x))

# ----------------------------------------------------------------- angle
print()
print("the line tool shows its angle, and holds one typed")
ed.set_tool("line")
click(100.0, 100.0)
ed._on_move(130.0, 120.0, QtCore.Qt.NoModifier)
pump()
check("two heads-up fields, length and angle", len(ed.live.fields) == 2,
      len(ed.live.fields))
shown = float(ed.live.fields[1].text()) if len(ed.live.fields) > 1 else 0
check("the angle reads from the horizontal",
      near(shown, math.degrees(math.atan2(20.0, 30.0)), 1e-3), shown)
before = set(sketch.entities)
keys("25\t30\n")
made = [e for e in sketch.entities if e not in before]
ed.escape()
pump()
check("typing 25 and 30 drew one line", len(made) == 1, made)
if made:
    line = made[0]
    check("25 long", near(length(sketch, line), 25.0, 1e-4),
          length(sketch, line))
    check("at 30 degrees", near(heading(sketch, line), 30.0, 1e-4),
          heading(sketch, line))
    angles = [c for c in sketch.constraints.values()
              if c.kind == "angle" and c.entities == [line]]
    check("held by an angle dimension on that line alone", len(angles) == 1)
    if angles:
        check("which reads 30", near(ed._dimension_value(angles[0]), 30.0,
                                     1e-4), ed._dimension_value(angles[0]))
        angles[0].value = 45.0
        ed.solve()
        check("and turns the line when it is changed to 45",
              near(heading(sketch, line), 45.0, 1e-3)
              and near(length(sketch, line), 25.0, 1e-3),
              (heading(sketch, line), length(sketch, line)))
        ed.render()

for typed, kind in (("90", "vertical"), ("1", None)):
    ed.set_tool("line")
    click(-100.0, -100.0 - float(typed))
    ed._on_move(-60.0, -80.0, QtCore.Qt.NoModifier)
    before = set(sketch.entities)
    keys("\t%s\n" % typed)
    made = [e for e in sketch.entities if e not in before]
    ed.escape()
    pump()
    if not made:
        check("a line typed at %s degrees was drawn" % typed, False)
        continue
    line = made[0]
    on_it = {c.kind for c in sketch.constraints.values()
             if line in c.entities}
    if kind == "vertical":
        check("typed 90 is simply vertical, with no angle on top",
              "vertical" in on_it and "angle" not in on_it, on_it)
    else:
        check("typed 1 is an angle, not horizontal guessed from it",
              "angle" in on_it and "horizontal" not in on_it, on_it)
        check("and sits at 1 degree", near(heading(sketch, line), 1.0, 1e-3),
              heading(sketch, line))
check("the sketch is not over-constrained",
      not sketch.solve_message.startswith("over"), sketch.solve_message)
win.finish_sketch()
pump()

# ------------------------------------------------------------ references
print()
print("a visible sketch shows its dimensions, and lends them out")
win.new_document(prompt=False)
pump()
win.start_sketch_on_plane("XY")
pump()
ed.snap_grid = False
ed.set_tool("rect")
click(0.0, 0.0)
ed._on_move(30.0, 20.0, QtCore.Qt.NoModifier)
keys("30\t20\n")
old = ed.sketch
names = {c.name: c for c in old.constraints.values() if c.is_dimension}
width = next((c.name for c in names.values() if near(c.value, 30.0)), None)
check("the first sketch has a 30 wide dimension", width is not None, names)
win.finish_sketch()
pump()
first_sketch = win.document.sketch_features()[0]

win.new_feature(ExtrudeFeature)
pump()
dialog = win._active_dialog
dialog.distance.set_text("5")
win.select_all_profiles()
pump()
dialog.commit()
pump()
check("the extrude built", win.document.last_report.ok)

texts = []
original_text = vp.draw_text


def recording(text, *args, **kwargs):
    texts.append(text)
    return original_text(text, *args, **kwargs)


vp.draw_text = recording
texts.clear()
win._draw_visible_sketches()
check("a used sketch is hidden, dimensions and all",
      not any(t.startswith("30") for t in texts), texts)
win.browser.toggle_sketch_visibility(first_sketch.id)
pump()
texts.clear()
win._draw_visible_sketches()
check("shown again, its dimensions come with it",
      any(t.startswith("30") for t in texts)
      and any(t.startswith("20") for t in texts), texts)
vp.draw_text = original_text

win.start_sketch_on_plane("XY")
pump()
ed.snap_grid = False
check("the visible sketch stays on screen behind the new one",
      first_sketch.sketch in ed.references)
labels = {name: where for name, where, _text in ed._reference_labels}
check("with its dimensions", width in labels, list(labels))

new = ed.sketch
line = new.add_line((0.0, 60.0), (12.0, 60.0))
new.points[new.entities[line].points[0]].fixed = True
new.add_constraint("horizontal", entities=[line])
ed.solve()
ed.render()
ed.set_tool("dimension")
click(6.0, 60.0)
click(6.0, 70.0)
pump()
check("dimensioning the new line asks for a value",
      ed.value_popup.isVisible())
if width in labels and ed.value_popup.isVisible():
    u, v = new.plane.to_2d(labels[width])
    click(u, v)
    check("clicking the old sketch's 30 writes its name in",
          ed.value_popup.field.text() == width, ed.value_popup.field.text())
    ed.value_popup._accept()
    pump()
    ed.solve()
    check("so the new line is 30 long, tied to it",
          near(length(new, line), 30.0, 1e-4), length(new, line))
    tied = [c for c in new.constraints.values() if c.expression == width]
    check("by name", len(tied) == 1,
          [(c.kind, c.expression) for c in new.constraints.values()])
win.finish_sketch()
pump()

print()
print("FAILED: " + ", ".join(FAILS) if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
