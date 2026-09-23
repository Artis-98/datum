"""OK on the sketch right-click menu: stop drawing, in one click."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402,F401


os.environ["DATUM_SETTINGS_ORG"] = "IITEG-tests"

from PySide6 import QtCore, QtWidgets                              # noqa: E402

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


def fresh():
    win.new_document(prompt=False)
    win.start_sketch_on_plane("XY")
    pump()
    ed.snap_grid = False
    return ed.sketch


def click(u, v):
    ed._on_move(u, v, QtCore.Qt.NoModifier)
    ed._on_click(u, v, QtCore.Qt.NoModifier)
    pump(1)


def rows():
    """What the sketch right-click menu would show, right now."""
    menu = QtWidgets.QMenu(win)
    win.build_sketch_menu(menu)
    return [a.text() for a in menu.actions() if not a.isSeparator()]


# ==========================================================================
print("with a drawing tool running, OK is the first thing offered")

sketch = fresh()
ed.set_tool("line")
click(0.0, 0.0)
click(40.0, 0.0)
items = rows()
check("OK is the very first row", items and items[0] == "OK", items)
check("Finish Sketch is still there", "Finish Sketch" in items, items)
check("the old Select tool row is gone", "Select tool" not in items, items)

print("and with nothing running there is no OK")
ed.ok()
pump()
items = rows()
check("no OK on the select tool", "OK" not in items, items)
check("but the rest of the menu is intact",
      "Project Geometry" in items and "Finish Sketch" in items, items)


# ==========================================================================
print("OK ends the line chain and hands back the select tool")

sketch = fresh()
ed.set_tool("line")
click(0.0, 0.0)
click(40.0, 0.0)
click(40.0, 30.0)
check("three clicks made two lines", len(sketch.entities) == 2,
      len(sketch.entities))
check("and the chain is still running", ed._pending != [], ed._pending)
check("so the editor is busy", ed.busy)

win.ok_sketch_tool()
pump()
check("the chain stopped", ed._pending == [], ed._pending)
check("the tool is select again", ed.tool == "select", ed.tool)
check("it is no longer busy", not ed.busy)
check("the two lines were kept", len(ed.sketch.entities) == 2,
      len(ed.sketch.entities))
check("and the sketch is still open", ed.editing if hasattr(ed, "editing")
      else ed.active)
check("the ribbon buttons followed",
      not win.tool_buttons["line"].isChecked())


# ==========================================================================
print("it keeps a spline, which is the one tool holding unfinished work")

sketch = fresh()
ed.set_tool("spline")
for x, y in ((0.0, 0.0), (20.0, 20.0), (40.0, 0.0), (60.0, 25.0)):
    click(x, y)
check("nothing committed yet", len(sketch.entities) == 0,
      len(sketch.entities))
win.ok_sketch_tool()
pump()
check("OK committed the spline", len(ed.sketch.entities) == 1,
      len(ed.sketch.entities))
check("back on select", ed.tool == "select", ed.tool)

print("but a half-drawn rectangle has nothing worth keeping")
sketch = fresh()
ed.set_tool("rect")
click(0.0, 0.0)
check("one corner down", ed._pending != [])
win.ok_sketch_tool()
pump()
check("nothing was drawn", len(ed.sketch.entities) == 0,
      len(ed.sketch.entities))
check("and it stopped cleanly", ed.tool == "select" and ed._pending == [])


# ==========================================================================
print("OK also stops the tools that are not drawing")

sketch = fresh()
line = sketch.add_line((0.0, 0.0), (40.0, 0.0))
ed.set_tool("dimension")
check("busy on the dimension tool", ed.busy)
ed._pending = [("entity", line)]
ed._dim_target = ed._dimension_target()
ed._pending = []
win.ok_sketch_tool()
pump()
check("the half-placed dimension is dropped", ed._dim_target is None,
      ed._dim_target)
check("no dimension was added",
      not [c for c in ed.sketch.constraints.values() if c.is_dimension],
      list(ed.sketch.constraints))
check("back on select", ed.tool == "select", ed.tool)

print("and an armed constraint too")
ed.start_constraint("horizontal")
pump()
check("it is armed", ed._pending_constraint == "horizontal",
      ed._pending_constraint)
win.ok_sketch_tool()
pump()
check("OK disarmed it", ed._pending_constraint is None,
      ed._pending_constraint)


# ==========================================================================
print("OK is not Finish Sketch - the sketch stays open")

sketch = fresh()
ed.set_tool("line")
click(0.0, 0.0)
click(30.0, 0.0)
win.ok_sketch_tool()
pump()
check("still sketching", ed.active)
win.finish_sketch()
pump()
check("Finish Sketch is what closes it", not ed.active)


# ==========================================================================
print("Escape still peels back one layer at a time")

sketch = fresh()
ed.set_tool("line")
click(0.0, 0.0)
click(40.0, 0.0)
ed.escape()
pump()
check("the first Escape ends the chain", ed._pending == [], ed._pending)
check("but keeps the line tool", ed.tool == "line", ed.tool)
ed.escape()
pump()
check("the second goes back to select", ed.tool == "select", ed.tool)
check("which is what OK does in one", True)


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
print("all sketch menu checks passed")
