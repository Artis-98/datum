"""Document units: millimetres, centimetres, metres, inches, feet.

Geometry is millimetres inside and never moves when the units change.
What changes is how a length reads and what a bare typed number means:
2 typed into an inch part is two inches, and it is kept as "2 in" so it
means the same forever after.
"""

import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WORK = tempfile.mkdtemp(prefix="datum_units_")
os.environ["DATUM_CONFIG_DIR"] = WORK
os.environ["DATUM_SETTINGS_ORG"] = "IITEG-tests"

import harness  # noqa: E402,F401

from datum.core import kernel, prefs                               # noqa: E402
from datum.core import units as u                                  # noqa: E402
from datum.core.params import evaluate                             # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def near(a, b, tol=1e-6):
    return abs(a - b) < tol


print("the rules")
check("an inch is 25.4 mm and a foot 304.8", near(u.to_mm(1, "in"), 25.4)
      and near(u.to_mm(1, "ft"), 304.8) and near(u.to_unit(50.8, "in"), 2))
for typed, kept in (("2", "2 in"), ("d1 + 2", "d1 + 2 in"),
                    ("d1 * 2", "d1 * 2"), ("d1 / 4", "d1 / 4"),
                    ("(d1 + 3) / 2", "(d1 + 3 in) / 2"),
                    ("max(d1, 5)", "max(d1, 5 in)"), ("5 mm", "5 mm"),
                    ("sin(30) * d1", "sin(30) * d1")):
    check("%r typed in an inch part is kept as %r" % (typed, kept),
          u.for_storage(typed, "in") == kept, u.for_storage(typed, "in"))
check("a millimetre part keeps what was typed",
      u.for_storage("d1 + 2", "mm") == "d1 + 2")
check("an angle never gets a length unit",
      u.for_storage("30", "in", u.ANGLE) == "30")
check("what is kept means what was typed",
      near(evaluate(u.for_storage("d1 + 2", "in"), {"d1": 10.0}), 60.8))
check("a plain stored number, millimetres, shows converted",
      u.for_display("50.8", "in") == "2")
check("a number in the part's own unit shows bare",
      u.for_display("2 in", "in") == "2")
check("one in another unit shows as written",
      u.for_display("5 mm", "in") == "5 mm")
check("a typed plain number is a value in mm",
      near(u.plain_value("2", "in"), 50.8)
      and near(u.plain_value("2 mm", "in"), 2.0)
      and u.plain_value("d1 + 2", "in") is None)
check("mass reads in pounds in an inch part",
      u.mass_text(453.59237, "in") == "1 lb"
      and u.mass_text(2500, "mm") == "2.5 kg")

# ==========================================================================
print()
print("in the window")

from PySide6 import QtCore, QtWidgets                              # noqa: E402

from datum.core.features import ExtrudeFeature                     # noqa: E402
from datum.ui.main_window import MainWindow                        # noqa: E402
from datum.ui.panels import EXPRESSION, UNIT, VALUE, ParametersDialog  # noqa
from datum.ui.prefs_ui import PreferencesDialog                    # noqa: E402
from datum.ui.theme import stylesheet                              # noqa: E402

app = QtWidgets.QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(stylesheet())
win = MainWindow()
win.resize(1400, 900)
win.show()
app.processEvents()
ed = win.editor


def pump(n=3):
    for _ in range(n):
        app.processEvents()


def click(x, y):
    ed._on_move(x, y, QtCore.Qt.NoModifier)
    ed._on_click(x, y, QtCore.Qt.NoModifier)
    pump(1)


dialog = PreferencesDialog(win)
dialog.units_combo.setCurrentIndex(dialog.units_combo.findData("in"))
dialog.accept()
check("Preferences sets the units for new documents",
      prefs.load().units == "in")
win.new_document(prompt=False)
pump()
check("a new part is drawn in them", win.document.units == "in")
check("and the status bar says so", win.status_units.text() == "in")

win.start_sketch_on_plane("XY")
pump()
ed.snap_grid = False
sketch = ed.sketch
line = sketch.add_line((0.0, 0.0), (40.0, 0.0))
sketch.points[sketch.entities[line].points[0]].fixed = True
cid = ed._add_dimension("distance", list(sketch.entities[line].points), [],
                        40.0, "2")
c = sketch.constraints[cid]
check("a dimension typed as 2 is two inches", near(c.value, 50.8)
      and c.expression == "", (c.value, c.expression))
ends = [sketch.points[p] for p in sketch.entities[line].points]
check("and the line is 50.8 mm long inside",
      near(math.hypot(ends[1].x - ends[0].x, ends[1].y - ends[0].y), 50.8,
           1e-5))
check("it reads as 2 in", ed._dimension_text(c) == "2 in",
      ed._dimension_text(c))
other = sketch.add_line((0.0, 20.0), (10.0, 20.0))
sketch.points[sketch.entities[other].points[0]].fixed = True
half = ed._add_dimension("distance", list(sketch.entities[other].points),
                         [], 10.0, "%s / 2 + 1" % c.name)
h = sketch.constraints[half]
check("an expression keeps its factors, and its lengths get the unit",
      h.expression == "%s / 2 + 1 in" % c.name and near(h.value, 50.8),
      (h.expression, h.value))
ed._edit_dimension(cid, "3")
check("editing it to 3 makes it three inches", near(c.value, 76.2))

ed.set_tool("rect")
click(100.0, 100.0)
ed._on_move(130.0, 120.0, QtCore.Qt.NoModifier)
pump()
check("the heads-up fields follow in inches",
      ed.live.fields and abs(float(ed.live.fields[0].text())
                             - 30.0 / 25.4) < 1e-3,
      ed.live.fields and ed.live.fields[0].text())
ed._on_key(QtCore.Qt.Key_1, "1")
before = set(sketch.entities)
ed._on_key(QtCore.Qt.Key_Return, "")
pump()
made = [e for e in sketch.entities if e not in before]
xs = sorted({round(sketch.points[p].x, 6) for e in made
             for p in sketch.entities[e].points})
check("typing 1 into one makes the rectangle an inch wide",
      len(xs) == 2 and near(xs[1] - xs[0], 25.4, 1e-6), xs)
win.finish_sketch()
pump()

win.new_feature(ExtrudeFeature)
pump()
extrude_dialog = win._active_dialog
extrude_dialog.distance.edit.setText("1")
check("an extrude box reads in inches",
      extrude_dialog.distance.readout.text() == "= 1 in",
      extrude_dialog.distance.readout.text())
check("and keeps what it was given with its unit",
      extrude_dialog.distance.text() == "1 in")
extrude_dialog.cancel()
pump()

print()
print("the table and the panel")
# cancelling the extrude put the document back from its snapshot, so the
# dimension is a new object now
c = next(k for f in win.document.sketch_features()
         for k in f.sketch.constraints.values() if k.name == c.name)
table = ParametersDialog(win.document, win)
row = table.row_of(c.name)
check("a dimension's row reads in inches",
      table.table.item(row, UNIT).text() == "in"
      and table.table.item(row, VALUE).text() == "3",
      (table.table.item(row, UNIT).text(), table.table.item(row, VALUE).text()))
table.table.item(row, EXPRESSION).setText("4")
check("typing 4 in the table makes it four inches", near(c.value, 101.6),
      c.value)
table.close()

print()
print("changing the units moves nothing")
win.set_document_units("mm")
check("the document is in millimetres now", win.document.units == "mm"
      and win.status_units.text() == "mm")
from datum.ui.panels import ParametersDialog as _Table  # noqa: E402
again = _Table(win.document, win)
row = again.row_of(c.name)
check("the dimension is the same length, read in mm",
      near(c.value, 101.6) and again.table.item(row, UNIT).text() == "mm"
      and again.table.item(row, VALUE).text() == "101.6",
      (c.value, again.table.item(row, VALUE).text()))
again.close()
win.undo()
pump()
check("and changing units is undone like anything else",
      win.document.units == "in", win.document.units)

prefs.prefs().units = "mm"
print()
print("FAILED: " + ", ".join(FAILS) if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
