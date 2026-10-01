"""Turntable orbit: spin about Z, tilt, and Z never leaves the top.

What makes a turntable a turntable is checked as geometry, not as pixels:
the camera's right-hand axis stays level (no roll), its up vector never
points below the horizon, and the tilt stops at the poles.
"""

import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WORK = tempfile.mkdtemp(prefix="datum_orbit_")
os.environ["DATUM_CONFIG_DIR"] = WORK
os.environ["DATUM_SETTINGS_ORG"] = "IITEG-tests"

import harness  # noqa: E402,F401

from PySide6 import QtCore, QtWidgets                              # noqa: E402
from PySide6.QtTest import QTest                                   # noqa: E402

from datum.core import prefs                                       # noqa: E402
from datum.ui.main_window import MainWindow                        # noqa: E402
from datum.ui.prefs_ui import PreferencesDialog                    # noqa: E402
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


def pump(n=3):
    for _ in range(n):
        app.processEvents()


def frame():
    eye, centre, up, _scale, _ortho = vp.camera_state()
    d = [centre[i] - eye[i] for i in range(3)]
    n = math.sqrt(sum(c * c for c in d))
    d = [c / n for c in d]
    right = (d[1] * up[2] - d[2] * up[1],
             d[2] * up[0] - d[0] * up[2],
             d[0] * up[1] - d[1] * up[0])
    return d, up, right


def level(tol=1e-6):
    d, up, right = frame()
    return abs(right[2]) < tol and up[2] >= -tol


print("the preference")
check("free is the default", prefs.prefs().orbit == prefs.ORBIT_FREE)
check("an unknown value reads as free",
      prefs.Preferences.from_dict({"orbit": "sideways"}).orbit
      == prefs.ORBIT_FREE)
dialog = PreferencesDialog(win)
dialog.orbit_combo.setCurrentIndex(
    dialog.orbit_combo.findData(prefs.ORBIT_TURNTABLE))
dialog.accept()
check("Preferences sets it", prefs.prefs().orbit == prefs.ORBIT_TURNTABLE)
check("and it is saved", prefs.load().orbit == prefs.ORBIT_TURNTABLE)
check("the view sees it", vp.turntable_wanted())

print()
print("the camera")
win.new_document(prompt=False)
vp.set_view("iso")
vp.finish_animation()
pump()
# roll the camera on purpose, the way a free orbit leaves it
eye, centre, up, scale, _o = vp.camera_state()
vp._set_camera(eye, centre, (0.3, 0.4, 0.2), scale)
check("a rolled view is not level", not level(1e-3))
vp.level_camera()
check("levelling stands Z up the screen", level(), frame())
before = frame()[0]
vp.turntable(120, 0)
after = frame()[0]
check("a sideways drag spins about Z",
      abs(before[2] - after[2]) < 1e-9
      and abs(before[0] - after[0]) > 0.1, (before, after))
check("and stays level", level())
vp.turntable(0, 40)
check("a vertical drag tilts, still level", level())
vp.turntable(0, 5000)
d = frame()[0]
check("tilting stops at looking straight down", abs(d[2] + 1.0) < 1e-6, d)
vp.turntable(0, -10000)
d = frame()[0]
check("and at looking straight up", abs(d[2] - 1.0) < 1e-6, d)
vp.turntable(0, 3000)
vp.turntable(77, -31)
check("level after all of it", level())

print()
print("with the mouse")
vp.set_view("iso")
vp.finish_animation()
eye, centre, up, scale, _o = vp.camera_state()
vp._set_camera(eye, centre, (0.3, 0.4, 0.2), scale)
pump()
middle = QtCore.QPoint(vp.width() // 2, vp.height() // 2)
QTest.mousePress(vp, QtCore.Qt.RightButton, QtCore.Qt.NoModifier, middle)
for step in range(1, 8):
    QTest.mouseMove(vp, middle + QtCore.QPoint(step * 15, step * 6))
    pump(1)
QTest.mouseRelease(vp, QtCore.Qt.RightButton, QtCore.Qt.NoModifier,
                   middle + QtCore.QPoint(105, 42))
pump()
check("a right-drag orbit is a turntable", level(), frame())

print()
print("free is unchanged")
prefs.prefs().orbit = prefs.ORBIT_FREE
check("the view sees free again", not vp.turntable_wanted())
vp.set_view("iso")
vp.finish_animation()
pump()
QTest.mousePress(vp, QtCore.Qt.RightButton, QtCore.Qt.NoModifier, middle)
for step in range(1, 8):
    QTest.mouseMove(vp, middle + QtCore.QPoint(step * 15, step * 25))
    pump(1)
QTest.mouseRelease(vp, QtCore.Qt.RightButton, QtCore.Qt.NoModifier,
                   middle + QtCore.QPoint(105, 175))
pump()
check("a free orbit is not held level", not level(1e-3), frame())

print()
print("FAILED: " + ", ".join(FAILS) if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
