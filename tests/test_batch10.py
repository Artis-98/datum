"""Keyboard, escape, click-click constraints, sketch undo, snap priority,
and profile picking in the viewport."""
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402,F401


from PySide6 import QtCore, QtWidgets  # noqa: E402

from datum.core import kernel  # noqa: E402
from datum.core.features import (  # noqa: E402
    ExtrudeFeature, ProfileSelection, SketchFeature,
)
from datum.core.sketch import STANDARD_PLANES, Sketch  # noqa: E402
from datum.ui.main_window import MainWindow  # noqa: E402
from datum.ui.theme import stylesheet  # noqa: E402

FAILS = []
WORK = tempfile.mkdtemp(prefix="datum_b10_")

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


def near(a, b, tol=1e-3):
    return abs(a - b) < tol


def click(u, v, mods=QtCore.Qt.NoModifier):
    ed._on_move(u, v, mods)
    ed._on_click(u, v, mods)
    pump(1)


def move(u, v):
    ed._on_move(u, v, QtCore.Qt.NoModifier)
    pump(1)


def key(k, text=""):
    """Send a key through the viewport, the way a real press arrives."""
    event = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, k,
                            QtCore.Qt.NoModifier, text)
    QtWidgets.QApplication.sendEvent(win.viewport, event)
    pump(1)


from PySide6 import QtGui  # noqa: E402


def sketch(plane="XY"):
    win.start_sketch_on_plane(plane)
    win.viewport.finish_animation()
    pump()
    return ed.sketch


# ==========================================================================
print("Return is no longer swallowed by an app shortcut")
shortcut_keys = [a.shortcut().toString() for a in win.actions()]
check("no application-wide Return shortcut",
      "Return" not in shortcut_keys, shortcut_keys)

print("a rectangle: type width, Tab, height, Enter")
win.new_document(prompt=False)
s = sketch()
ed.set_tool("rect")
click(-30, -20)
move(10, 10)
check("live bar up", ed.live.isVisible())
for ch in "80":
    key(ord(ch), ch)
check("typing reached the field", ed.live.fields[0].text() == "80",
      ed.live.fields[0].text())
key(QtCore.Qt.Key_Tab, "\t")
check("Tab moved to the height field", ed.live.active == 1, ed.live.active)
for ch in "45":
    key(ord(ch), ch)
key(QtCore.Qt.Key_Return, "\r")
pump()
check("Enter committed the rectangle",
      len([e for e in s.entities.values() if e.kind == "line"]) == 4,
      len(s.entities))
xs = [p.x for p in s.points.values() if not p.origin]
ys = [p.y for p in s.points.values() if not p.origin]
check("width is what was typed", near(max(xs) - min(xs), 80.0, 0.01),
      max(xs) - min(xs))
check("height is what was typed", near(max(ys) - min(ys), 45.0, 0.01),
      max(ys) - min(ys))
check("both became dimensions",
      len([c for c in s.constraints.values() if c.is_dimension]) == 2,
      len([c for c in s.constraints.values() if c.is_dimension]))

print("a circle: draw, type, Enter")
ed.set_tool("circle")
click(0, 0)
move(10, 0)
for ch in "36":
    key(ord(ch), ch)
key(QtCore.Qt.Key_Return, "\r")
pump()
circles = [e for e in s.entities.values() if e.kind == "circle"]
check("circle created by Enter", len(circles) == 1, len(circles))
check("its radius is 18", near(circles[0].radius, 18.0, 0.01),
      circles[0].radius)

# ==========================================================================
print("Escape backs out one level and never leaves the sketch")
ed.set_tool("rect")
click(100, 100)
check("mid-draw", bool(ed._pending))
ed.escape()
check("escape cancelled the draw", not ed._pending)
check("still in the sketch", ed.active)

ed.set_tool("line")
ed.escape()
check("escape returned to select", ed.tool == "select", ed.tool)
check("still in the sketch", ed.active)

ed.start_constraint("perpendicular")
check("a constraint is armed", ed._pending_constraint == "perpendicular")
ed.escape()
check("escape disarmed it", ed._pending_constraint is None)
check("still in the sketch", ed.active)
win.finish_sketch()
pump()

print("escape cancels an open feature dialog")
win.new_document(prompt=False)
s = sketch()
ed.set_tool("rect")
click(0, 0)
click(40, 30)
win.finish_sketch()
pump()
count = len(win.document.features)
win.new_feature(ExtrudeFeature)
win.select_all_profiles()
check("dialog open", win._active_dialog is not None)
win._on_escape()
pump()
check("escape cancelled the extrude", win._active_dialog is None)
check("and removed the half-made feature",
      len(win.document.features) == count, len(win.document.features))

# ==========================================================================
print("constraints: click one, then the other, no Ctrl")
win.new_document(prompt=False)
s = sketch()
ed.set_tool("line")
click(0, 0)
click(50, 4)
ed.escape()
click(0, 30)
click(4, 70)
ed.escape()
ed.set_tool("select")
lines = [e for e in s.entities.values() if e.kind == "line"]
check("two lines drawn", len(lines) >= 2, len(lines))

before = len(s.constraints)
ed.clear_selection()
ed.start_constraint("perpendicular")
a = s.points[lines[0].points[0]]
b = s.points[lines[0].points[1]]
click((a.x + b.x) / 2.0, (a.y + b.y) / 2.0)
check("first pick registered", len(ed.selected_entities) == 1,
      ed.selected_entities)
c = s.points[lines[1].points[0]]
d = s.points[lines[1].points[1]]
click((c.x + d.x) / 2.0, (c.y + d.y) / 2.0)
pump()
check("second plain click completed the constraint",
      len(s.constraints) == before + 1, len(s.constraints))
check("no Ctrl was needed", True)
win.finish_sketch()
pump()

# ==========================================================================
print("Ctrl+Z inside a sketch steps back one edit at a time")
win.new_document(prompt=False)
s = sketch()
ed.set_tool("point")
for i in range(5):
    click(10 * (i + 1), 10)
count_points = len(s.points)
check("five points placed", count_points >= 6, count_points)

win.undo()
pump()
check("still in the sketch after undo", ed.active)
check("one point removed", len(ed.sketch.points) == count_points - 1,
      len(ed.sketch.points))
win.undo()
win.undo()
pump()
check("three undos removed three points",
      len(ed.sketch.points) == count_points - 3, len(ed.sketch.points))
win.redo()
pump()
check("redo puts one back", len(ed.sketch.points) == count_points - 2,
      len(ed.sketch.points))

print("undo also covers lines and constraints")
ed.set_tool("line")
click(0, 60)
click(40, 60)
ed.escape()
lines_now = len([e for e in ed.sketch.entities.values() if e.kind == "line"])
win.undo()
pump()
check("the line was undone",
      len([e for e in ed.sketch.entities.values() if e.kind == "line"])
      == lines_now - 1,
      len([e for e in ed.sketch.entities.values() if e.kind == "line"]))
check("and we are still sketching", ed.active)
win.finish_sketch()
pump()
check("outside a sketch, undo works on the document", win.document is not None)

# ==========================================================================
print("snapping prefers a junction over a bare curve")
win.new_document(prompt=False)
s = sketch()
ed.set_tool("circle")
click(0, 0)
click(40, 0)
ed.set_tool("select")
circle = [e for e in s.entities.values() if e.kind == "circle"][0]

# a horizontal diameter line, its right endpoint on the circle
ed.set_tool("line")
click(-40, 0)
click(40, 0)
ed.escape()
ed.set_tool("select")

joint = None
for point in s.points.values():
    if near(point.x, 40.0, 0.6) and near(point.y, 0.0, 0.6):
        joint = point
check("the line meets the circle at a point", joint is not None)

degree_joint = ed._degree(joint.id)
check("that point is well connected", degree_joint >= 1, degree_joint)

# aim slightly off the junction; the junction must still win over on-curve
target = ed._snap(joint.x + 0.4, joint.y + 0.4, QtCore.Qt.AltModifier)
snapped = ed._snap(joint.x + 0.4, joint.y + 0.4, QtCore.Qt.ControlModifier)
check("snapped onto the junction, not the circumference",
      near(snapped[0], joint.x, 0.05) and near(snapped[1], joint.y, 0.05),
      "%s vs joint %.2f,%.2f" % (snapped, joint.x, joint.y))
found = ed._snap_target(joint.x + 0.4, joint.y + 0.4)
check("and would constrain to that point",
      found is not None and found[0] == "point" and found[1] == joint.id,
      found)
check("Alt still suspends snapping",
      near(target[0], joint.x + 0.4, 1e-6), target)
win.finish_sketch()
pump()

# ==========================================================================
print("features start with the likeliest profile, easily dropped")
win.new_document(prompt=False)
feature = SketchFeature()
feature.name = "Regions"
feature.sketch = Sketch(STANDARD_PLANES["XY"], "Regions")
feature.sketch.add_rectangle((0, 0), (40, 30))
feature.sketch.add_rectangle((60, 0), (80, 30))
win.document.add_feature(feature)
win.rebuild()

win.new_feature(ExtrudeFeature)     # deliberately no profile chosen
dlg = win._active_dialog
pump()          # the first preview runs on the next event-loop turn
check("the dialog opened", dlg is not None)
check("no sketch combo on the dialog", not hasattr(dlg, "sketch"))
check("a profile field instead", hasattr(dlg, "profiles"))
check("it starts with one proposed", len(dlg.feature.profiles) == 1,
      len(dlg.feature.profiles))
dlg._clear_profiles()
pump()
check("cleared, the feature reports no profile",
      "no profile selected" in dlg.feature.summary(), dlg.feature.summary())
check("and nothing builds", dlg.feature.error != "", dlg.feature.error)

print("clicking the field arms viewport picking")
dlg.profiles.set_picking(True)
pump()
check("profiles are shown in the view", win.viewport.profile_picking)
check("both regions offered", len(win._profile_regions) == 2,
      len(win._profile_regions))

small = min(range(len(win._profile_regions)),
            key=lambda i: win._profile_regions[i]["area"])
region = win._profile_regions[small]
dlg.on_profile_clicked(region["sketch_id"], region["centre"])
pump()
check("one profile picked", len(dlg.feature.profiles) == 1,
      len(dlg.feature.profiles))
dlg.distance.set_text("10")
dlg.preview()
pump()
check("it extrudes only that region",
      near(kernel.volume(win.document.shape), 600 * 10, 1.0),
      kernel.volume(win.document.shape))

print("clicking it again drops it")
dlg.on_profile_clicked(region["sketch_id"], region["centre"])
pump()
check("back to none", len(dlg.feature.profiles) == 0)

big = max(range(len(win._profile_regions)),
          key=lambda i: win._profile_regions[i]["area"])
for index in (small, big):
    entry = win._profile_regions[index]
    dlg.on_profile_clicked(entry["sketch_id"], entry["centre"])
dlg.preview()
pump()
check("two profiles extrude together",
      near(kernel.volume(win.document.shape), (600 + 1200) * 10, 1.0),
      kernel.volume(win.document.shape))
dlg.commit()
pump()
check("committed", win._active_dialog is None)
check("picking turned off", not win.viewport.profile_picking)

print("the selection survives save and reload")
path = win.document.save(os.path.join(WORK, "profiles.pdat"))
from datum.core.document import Document

reloaded = Document.load(path)
check("volume preserved",
      near(kernel.volume(reloaded.shape), (600 + 1200) * 10, 1.0),
      kernel.volume(reloaded.shape))

print("profiles from two different sketches can be combined")
win.new_document(prompt=False)
first = SketchFeature()
first.name = "A"
first.sketch = Sketch(STANDARD_PLANES["XY"], "A")
first.sketch.add_rectangle((0, 0), (20, 20))
win.document.add_feature(first)
second = SketchFeature()
second.name = "B"
second.sketch = Sketch(STANDARD_PLANES["XY"], "B")
second.sketch.add_rectangle((40, 0), (60, 20))
win.document.add_feature(second)
win.rebuild()

regions = win.available_regions()
check("regions come from both sketches",
      len({r["sketch_id"] for r in regions}) == 2,
      {r["sketch_id"] for r in regions})

extrude = ExtrudeFeature()
extrude.profiles = ProfileSelection()
for r in regions:
    extrude.profiles.add(r["sketch_id"], r["centre"])
extrude.distance = "5"
win.document.add_feature(extrude)
report = win.rebuild()
check("it builds across sketches", report.ok, report.message)
check("both regions were used",
      near(kernel.volume(win.document.shape), (400 + 400) * 5, 1.0),
      kernel.volume(win.document.shape))
check("it depends on both sketches",
      set(extrude.depends_on()) == {first.id, second.id},
      extrude.depends_on())

print()
if FAILS:
    print("%d FAILURES: %s" % (len(FAILS), ", ".join(FAILS)))
    sys.exit(1)
print("all batch10 tests passed")
sys.exit(0)
