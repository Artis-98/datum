"""The 3D view at a display scale other than 100 percent (GitHub issue #2).

Qt hands out mouse positions in logical pixels and OpenCASCADE draws in
the window's real ones.  At 150 percent the two differ by half, and every
click on a sketch landed a third of the way back towards the top left
corner of the view.  This runs the window at 150 percent and points at
things.
"""

import os
import sys

# before Qt starts, or it is too late to change
os.environ["QT_SCALE_FACTOR"] = "1.5"

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
win.resize(1300, 850)
win.show()
for _ in range(10):
    app.processEvents()
vp = win.viewport
ed = win.editor


def pump(n=6):
    for _ in range(n):
        app.processEvents()


def settle(ms=600):
    loop = QtCore.QEventLoop()
    QtCore.QTimer.singleShot(ms, loop.quit)
    loop.exec()


def move_to(x, y):
    point = QtCore.QPointF(x, y)
    QtWidgets.QApplication.sendEvent(vp, QtGui.QMouseEvent(
        QtCore.QEvent.MouseMove, point, vp.mapToGlobal(point),
        QtCore.Qt.NoButton, QtCore.Qt.NoButton, QtCore.Qt.NoModifier))
    pump(2)


ratio = vp.devicePixelRatioF()
print("device pixel ratio", ratio)
check("the window really is scaled", abs(ratio - 1.5) < 1e-6, ratio)

# ==========================================================================
print("the sketch cursor lands where the mouse is")
win.new_document(prompt=False)
win.start_sketch_on_plane("XY")
settle(1200)
plane = ed.sketch.plane
step = vp.pixel_scale()
for target in ((10.0, 5.0), (40.0, -20.0), (-25.0, 15.0)):
    x, y = vp.project(plane.to_3d(*target))
    move_to(x, y)
    got = ed._cursor
    off = ((got[0] - target[0]) ** 2 + (got[1] - target[1]) ** 2) ** 0.5
    # snapping may pull it onto the grid, but never further than a few
    # pixels; before the fix it was off by tens of millimetres
    check("pointing at %s lands there (%.2f off)" % (target, off),
          off <= max(3 * step, 0.6), got)

print("a pixel is the same size to the mouse and to the snaps")
a = vp.project(plane.to_3d(0.0, 0.0))
b = vp.project(plane.to_3d(50.0, 0.0))
pixels = abs(b[0] - a[0])
check("50 mm measured in mouse pixels comes back as 50 mm",
      abs(pixels * vp.pixel_scale() - 50.0) < 1.0,
      pixels * vp.pixel_scale())

print("the ray under a point goes through what is drawn there")
x, y = vp.project(plane.to_3d(20.0, 10.0))
uv = vp.plane_point(x, y)
check("plane_point undoes project",
      uv is not None and abs(uv[0] - 20.0) < 1.0 and abs(uv[1] - 10.0) < 1.0,
      uv)
win.finish_sketch()
pump()

# ==========================================================================
print("hovering a face finds that face")
win.new_document(prompt=False)
win.new_primitive("box")
pump()
dialog = win._active_dialog
if dialog is not None:
    dialog.commit()
pump()
vp.set_view("iso")
vp.finish_animation()
vp.fit_all()
pump()
vp.set_selection_mode("face")
pump()
shape = win.document.shape
check("a box to point at", shape is not None)
if shape is not None:
    picked = 0
    for face in kernel.faces(shape):
        centre = kernel.shape_centre(face)
        x, y = vp.project(centre)
        if not (0 < x < vp.width() and 0 < y < vp.height()):
            continue
        vp._detect(x, y)
        ctx = vp.context
        if (ctx.HasDetected() and ctx.HasDetectedShape()
                and ctx.DetectedShape().IsSame(face)):
            picked += 1
    # an iso view sees three faces of a box
    check("the three faces in sight are each found at their centre",
          picked >= 3, picked)
vp.set_selection_mode("none")

print()
if FAILS:
    print("%d FAILURES: %s" % (len(FAILS), ", ".join(FAILS)))
    sys.exit(1)
print("all display scale tests passed")
sys.exit(0)
