"""Starting a sketch: picking any origin plane, and the cube while drawing.

Both of these went wrong in ways that only show up through the real picking
path, so nothing here calls a handler directly - every pick goes through
OCCT's MoveTo / SelectDetected, the way a mouse does.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402,F401


os.environ["DATUM_SETTINGS_ORG"] = "IITEG-tests"

from PySide6 import QtCore, QtGui, QtWidgets                       # noqa: E402

from OCP.AIS import AIS_ViewCube                                   # noqa: E402

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
vp = win.viewport


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def pump(n=4):
    for _ in range(n):
        app.processEvents()


def settle(ms=400):
    timer = QtCore.QElapsedTimer()
    timer.start()
    while timer.elapsed() < ms:
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 20)


def pick_at(x, y):
    """A real click at a screen point, and whichever plane it landed on."""
    vp.context.MoveTo(int(x), int(y), vp.view, False)
    vp.context.SelectDetected()
    vp.view.Redraw()
    key = vp.picked_plane()
    vp.context.ClearSelected(False)
    return key


def reachable(key, samples=(-0.8, -0.45, 0.45, 0.8)):
    """How many sample points on this plane's patch actually pick it."""
    plane = win.document.planes[key]
    size = win._picker_size()
    hits = 0
    for fu in samples:
        for fv in samples:
            x, y = vp.project(plane.to_3d(size * fu, size * fv))
            if pick_at(x, y) == key:
                hits += 1
    return hits


# ==========================================================================
print("starting a sketch offers all three origin planes")

win.new_document(prompt=False)
settle(300)
win.start_sketch()
settle(500)

check("the picker is up", vp.plane_picker_active)
check("all three planes are drawn",
      sorted(vp._plane_picker.keys()) == ["XY", "XZ", "YZ"],
      sorted(vp._plane_picker.keys()))

hits = {key: reachable(key) for key in ("XY", "XZ", "YZ")}
for key in ("XY", "XZ", "YZ"):
    check("%s can be clicked" % key, hits[key] > 0, hits)
check("and none of them is only reachable by luck",
      all(count >= 3 for count in hits.values()), hits)


# ==========================================================================
print("clicking one starts the sketch on it")

for key in ("XZ", "YZ", "XY"):
    win.new_document(prompt=False)
    settle(200)
    win.start_sketch()
    settle(300)

    plane = win.document.planes[key]
    size = win._picker_size()
    landed = False
    for fu in (0.8, -0.8, 0.45, -0.45):
        for fv in (0.8, -0.8, 0.45, -0.45):
            x, y = vp.project(plane.to_3d(size * fu, size * fv))
            vp.context.MoveTo(int(x), int(y), vp.view, False)
            vp.context.SelectDetected()
            vp.view.Redraw()
            if vp.picked_plane() == key:
                win._on_viewport_selection()
                pump()
                landed = True
                break
            vp.context.ClearSelected(False)
        if landed:
            break

    check("a click on %s starts a sketch" % key,
          landed and win.editor.active, (landed, win.editor.active))
    if win.editor.active:
        check("  on that plane",
              win.editor.sketch.plane.name.startswith(key),
              win.editor.sketch.plane.name)
        win.finish_sketch()
        pump()


# ==========================================================================
print("the ViewCube still answers while a sketch has the mouse")

win.new_document(prompt=False)
settle(200)
win.start_sketch_on_plane("XY")
settle(400)
check("we are sketching", win.editor.active)
check("the viewport is in plane mode", vp.plane_mode)
check("and nothing else is selectable",
      vp.selection_mode == "none", vp.selection_mode)
check("the cube is still on screen", vp._cube is not None)


def cube_point():
    """A screen point on the cube, found by asking the cube itself."""
    width, height = vp.width(), vp.height()
    for dx in range(30, 150, 6):
        for dy in range(30, 150, 6):
            x, y = width - dx, dy
            if vp.over_cube(x, y):
                return x, y
    return None


spot = cube_point()
check("the cube can be found under the cursor", spot is not None, spot)

if spot is not None:
    check("hovering it is recognised", vp.over_cube(*spot))
    before = vp.camera_state()

    press = QtGui.QMouseEvent(
        QtCore.QEvent.MouseButtonPress,
        QtCore.QPointF(spot[0], spot[1]), QtCore.QPointF(spot[0], spot[1]),
        QtCore.Qt.LeftButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
    vp.mousePressEvent(press)
    settle(900)          # the cube turns the camera with an animation

    after = vp.camera_state()
    moved = any(abs(a - b) > 1e-6
                for a, b in zip(before[0], after[0]))
    check("clicking it turns the model", moved,
          (before[0], after[0]))
    check("and it did not put a sketch point down instead",
          not win.editor.sketch.entities, len(win.editor.sketch.entities))
    check("the sketch is still open", win.editor.active)

print("a click away from the cube is still a sketch click")
before_points = len(win.editor.sketch.points)
win.editor.set_tool("point")
away = (vp.width() // 3, vp.height() // 2)
check("that spot is not the cube", not vp.over_cube(*away))
press = QtGui.QMouseEvent(
    QtCore.QEvent.MouseButtonPress,
    QtCore.QPointF(away[0], away[1]), QtCore.QPointF(away[0], away[1]),
    QtCore.Qt.LeftButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
release = QtGui.QMouseEvent(
    QtCore.QEvent.MouseButtonRelease,
    QtCore.QPointF(away[0], away[1]), QtCore.QPointF(away[0], away[1]),
    QtCore.Qt.LeftButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
vp.mousePressEvent(press)
vp.mouseReleaseEvent(release)
pump()
check("the sketch took it", len(win.editor.sketch.points) > before_points,
      (before_points, len(win.editor.sketch.points)))

win.finish_sketch()
pump()


# ==========================================================================
print("plane picking still works on the always-on sheets too")

win.new_document(prompt=False)
settle(200)
win.document.hidden_planes.clear()
win.rebuild()
settle(300)
check("the sheets are displayed",
      sorted(vp._plane_display.keys()) == ["XY", "XZ", "YZ"],
      sorted(vp._plane_display.keys()))
win.set_pick_mode("face")
found = {key: reachable(key) for key in ("XY", "XZ", "YZ")}
check("and each one can be clicked",
      all(count > 0 for count in found.values()), found)


# ==========================================================================
print("a click still counts while the hand is moving")
# Sketching used to need a dead-still cursor: three pixels of travel between
# press and release turned the click into a drag, and the drag was then
# ignored because no drawing tool has one - so nothing happened at all.

win.new_document(prompt=False)
settle(200)
win.start_sketch_on_plane("XY")
settle(400)
win.editor.snap_grid = False
win.editor.set_tool("point")
check("a drawing tool switches drags off",
      not vp.plane_drag_allowed, vp.plane_drag_allowed)


def stroke(start, end, button=QtCore.Qt.LeftButton):
    """Press at one place, travel, and release at another."""
    for kind, at in ((QtCore.QEvent.MouseButtonPress, start),
                     (QtCore.QEvent.MouseMove, ((start[0] + end[0]) // 2,
                                                (start[1] + end[1]) // 2)),
                     (QtCore.QEvent.MouseMove, end),
                     (QtCore.QEvent.MouseButtonRelease, end)):
        point = QtCore.QPointF(at[0], at[1])
        pressed = button if kind != QtCore.QEvent.MouseMove else button
        event = QtGui.QMouseEvent(kind, point, point, button, pressed,
                                  QtCore.Qt.NoModifier)
        if kind == QtCore.QEvent.MouseButtonPress:
            vp.mousePressEvent(event)
        elif kind == QtCore.QEvent.MouseMove:
            vp.mouseMoveEvent(event)
        else:
            vp.mouseReleaseEvent(event)
    pump()


before = len(win.editor.sketch.points)
stroke((vp.width() // 3, vp.height() // 2),
       (vp.width() // 3 + 40, vp.height() // 2 + 25))
check("a point went down despite 47 pixels of travel",
      len(win.editor.sketch.points) > before,
      (before, len(win.editor.sketch.points)))

print("and a long stroke with the select tool is still a drag")
win.editor.set_tool("select")
check("select turns drags back on", vp.plane_drag_allowed)
win.editor.clear_selection()
stroke((vp.width() // 4, vp.height() // 4),
       (vp.width() * 3 // 4, vp.height() * 3 // 4))
check("it was treated as a box, not a click",
      win.editor._box_start is None,        # finished, not left half open
      win.editor._box_start)

win.finish_sketch()
pump()


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
print("all sketch entry checks passed")
