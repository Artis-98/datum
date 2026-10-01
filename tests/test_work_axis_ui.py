"""Work axes, Revolve about one, and Extrude To Plane, from the window.

The core is covered by test_work_axis.py; this drives the ribbon button and
the dialogs, which is where a choice that exists in the model but is never
offered would hide.
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import harness  # noqa: E402,F401

os.environ["DATUM_SETTINGS_ORG"] = "IITEG-tests"

from PySide6 import QtCore, QtWidgets                              # noqa: E402

from datum.core import kernel                                      # noqa: E402
from datum.core.features import (                                  # noqa: E402
    AXIS_PREFIX, ExtrudeFeature, RevolveFeature, WorkAxisFeature,
    WorkPlaneFeature,
)
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


def click(u, v):
    ed._on_move(u, v, QtCore.Qt.NoModifier)
    ed._on_click(u, v, QtCore.Qt.NoModifier)
    pump(1)


def near(a, b, tol=1e-2):
    return abs(a - b) < tol


print("the Axis button")
win.new_document(prompt=False)
win.viewport.clear_selection()
win.start_work_axis()
pump()
dialog = win._active_dialog
check("opens the Work Axis dialog",
      dialog is not None and isinstance(dialog.feature, WorkAxisFeature),
      dialog)
check("with a name of its own", dialog.feature.name == "Work Axis1",
      dialog.feature.name)
check("starting on two planes, since nothing was picked",
      dialog.mode.currentData() == "planes")
dialog.plane_a.setCurrentIndex(dialog.plane_a.findData("XZ"))
dialog.plane_b.setCurrentIndex(dialog.plane_b.findData("YZ"))
dialog.commit()
pump()
axis = win.document.axes.get("Work Axis1")
check("it makes an axis where XZ meets YZ",
      axis is not None and near(abs(axis.direction[2]), 1.0, 1e-6),
      axis)

gold = []
_real_edge = win.viewport.draw_edge


def spy(a, b, colour, width=1.8, preview=False, dashed=False):
    if colour == "#c8a24f" and dashed:
        gold.append((a, b))
    return _real_edge(a, b, colour, width, preview=preview, dashed=dashed)


win.viewport.draw_edge = spy
win._draw_visible_sketches()
win.viewport.draw_edge = _real_edge
check("and it is drawn in the view", len(gold) == 1, gold)

print()
print("Revolve offers it")
win.start_sketch_on_plane("XZ")
pump()
ed.snap_grid = False
ed.set_tool("rect")
click(20, 0)
click(30, 10)
win.finish_sketch()
pump()
win.new_feature(RevolveFeature)
pump()
dialog = win._active_dialog
choices = [dialog.axis.itemData(i) for i in range(dialog.axis.count())]
check("the work axis is in the Axis list",
      AXIS_PREFIX + "Work Axis1" in choices, choices)
check("and so are the part's own axes",
      AXIS_PREFIX + "Z" in choices, choices)
dialog.axis.setCurrentIndex(dialog.axis.findData(AXIS_PREFIX + "Work Axis1"))
win.select_all_profiles()
pump()
dialog.commit()
pump()
check("revolving about it builds a ring", win.document.last_report.ok
      and near(kernel.volume(win.document.shape),
               math.pi * (30 ** 2 - 20 ** 2) * 10, 1.0),
      win.document.last_report.message)

print()
print("Extrude To Plane")
win.new_document(prompt=False)
top = WorkPlaneFeature(name="Lid")
top.base = "XY"
top.offset = "25"
win.document.add_feature(top)
win.rebuild()
win.start_sketch_on_plane("XY")
pump()
ed.snap_grid = False
ed.set_tool("rect")
click(0, 0)
click(10, 10)
win.finish_sketch()
pump()
win.new_feature(ExtrudeFeature)
pump()
dialog = win._active_dialog
pos = dialog.extent.findData("to_plane")
check("To Plane is one of the extents", pos >= 0)
dialog.extent.setCurrentIndex(pos)
pump()
check("choosing it swaps Distance for a plane",
      dialog.to_plane.isVisibleTo(dialog)
      and not dialog.distance.isVisibleTo(dialog))
planes = [dialog.to_plane.itemData(i) for i in range(dialog.to_plane.count())]
check("the work plane made earlier is on offer", "Lid" in planes, planes)
dialog.to_plane.setCurrentIndex(dialog.to_plane.findData("Lid"))
win.select_all_profiles()
pump()
dialog.commit()
pump()
check("it runs up to the plane", win.document.last_report.ok
      and near(kernel.bounding_box(win.document.shape)[5], 25.0),
      win.document.last_report.message)
feature = win.document.features[-1]
check("and remembers which plane",
      isinstance(feature, ExtrudeFeature) and feature.to_plane == "Lid"
      and feature.extent == "to_plane")

print()
print("FAILED: " + ", ".join(FAILS) if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
