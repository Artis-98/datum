"""Base View the Inventor way.

Base opens the Drawing View box: the parts and assemblies open in tabs,
the last one worked on first, or a file from a folder, with orientation,
style, scale and name.  The view follows the cursor and a click puts it
down; then each click places a projection, beside, above or below for
the orthographic views and off a corner for an isometric, until a
right-click or Esc.
"""

import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import harness  # noqa: E402

os.environ["DATUM_SETTINGS_ORG"] = "IITEG-tests"

from PySide6 import QtCore, QtGui, QtWidgets                       # noqa: E402
from PySide6.QtTest import QTest                                   # noqa: E402

from datum.core import drawing as dwg                              # noqa: E402
from datum.ui.main_window import MainWindow                        # noqa: E402
from datum.ui.theme import stylesheet                              # noqa: E402

FAILS = []
WORK = tempfile.mkdtemp(prefix="datum_base_view_")

app = QtWidgets.QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(stylesheet())
win = MainWindow()
win.resize(1400, 900)
win.show()
app.processEvents()


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def near(a, b, tol=1e-6):
    return abs(a - b) <= tol


def pump(n=3):
    for _ in range(n):
        app.processEvents()


def make_box(name, a, b, c):
    win.new_document(prompt=False)
    win.new_primitive("box")
    d = win._active_dialog
    d.a.set_text(a)
    d.b.set_text(b)
    d.c.set_text(c)
    d.commit()
    pump()
    path = os.path.join(WORK, name + ".pdat")
    win.document.path = path
    win.save_document()
    pump()
    return path


block = make_box("Block", "60", "40", "20")
plate = make_box("Plate", "100", "10", "5")

win.new_drawing(prompt=False)
pump()
win.drawing.path = os.path.join(WORK, "Sheet.ddat")
win.save_document()
pump()
ui = win.drawing_ui
canvas = win.sheet_canvas
sheet = win.drawing.active()

# ------------------------------------------------------------------ the box
print("Base opens the Drawing View box")
ui.place_base_view()
pump()
placer = ui._placer
dialog = placer.dialog if placer else None
check("a placer is running, with its box open",
      dialog is not None and dialog.isVisible() and canvas.placer is placer)
offered = [dialog.file.itemData(i) for i in range(dialog.file.count())]
check("it offers both open parts",
      os.path.abspath(block) in offered and os.path.abspath(plate) in offered,
      offered)
check("the part last worked on comes first",
      dialog.path() == os.path.abspath(plate), dialog.path())
dialog.choose_file(block)
pump()
check("picking another changes the file", dialog.path() == os.path.abspath(block))
scale = dialog.scale_value()
check("the scale is a standard one that fits", scale in dwg.SCALES, scale)
check("hidden lines shown, as a base view usually is",
      dialog.settings()["display"] == dwg.WITH_HIDDEN)

harness.opening(plate)
dialog._browse()
check("the folder button picks a file too",
      dialog.path() == os.path.abspath(plate))
dialog.choose_file(block)
dialog.scale.setEditText("1:2")
check("a typed scale is read as typed", near(dialog.scale_value(), 0.5))

# --------------------------------------------------------------- the base
print()
print("the view follows the cursor, and a click puts it down")
layout = canvas.layout()
here = (140.0, 190.0)
device = QtCore.QPoint(*[int(round(c)) for c in layout.to_device(*here)])
# sent straight to the canvas: a synthetic move of the real cursor is only
# delivered once the system gets round to it
QtWidgets.QApplication.sendEvent(canvas, QtGui.QMouseEvent(
    QtCore.QEvent.MouseMove, QtCore.QPointF(device),
    QtCore.QPointF(canvas.mapToGlobal(device)), QtCore.Qt.NoButton,
    QtCore.Qt.NoButton, QtCore.Qt.NoModifier))
pump()
shown = placer.preview()
check("one box follows the cursor", len(shown) == 1, shown)
if shown:
    box = shown[0][0]
    check("the size of the front of the block at 1:2",
          near(box[2] - box[0], 30.0, 1e-6) and near(box[3] - box[1], 10.0,
                                                      1e-6), box)
QTest.mouseClick(canvas, QtCore.Qt.LeftButton, pos=device)
pump()
views = sheet.views
base = views[0] if views else None
check("a click puts the base view down", len(views) == 1
      and base.kind == dwg.BASE, [v.kind for v in views])
if base is not None:
    at = layout.to_sheet(device.x(), device.y())
    check("where the cursor was", near(base.x, at[0], 1e-6)
          and near(base.y, at[1], 1e-6), (base.x, base.y))
    check("at the scale, orientation and name the box said",
          near(base.scale, 0.5) and base.orientation == "front"
          and base.name == "View1", (base.scale, base.orientation, base.name))
    check("drawn", base.projection is not None and not base.error, base.error)
check("the box has gone", placer.dialog is None and not dialog.isVisible())
check("and projections are being placed from it",
      ui._placer is placer and placer.parent == base.id)

# ---------------------------------------------------------- projections
print()
print("each click after that places a projection")


def place(point):
    placer.hover(point)
    label = placer.preview()[0][1] if placer.preview() else ""
    before = len(sheet.views)
    placer.click(point)
    pump()
    made = sheet.views[before:] if len(sheet.views) > before else []
    return (made[0] if made else None), label


side, side_label = place((base.x + 70.0, base.y + 3.0))
check("beside it, a side view, put exactly level",
      side is not None and near(side.y, base.y) and side.parent == base.id
      and side.kind == dwg.PROJECTED, side and (side.x, side.y))
check("named in the preview for what it will show",
      side_label in ("Left", "Right"), side_label)
below, below_label = place((base.x - 2.0, base.y - 60.0))
check("below it, a view put exactly in line",
      below is not None and near(below.x, base.x), below and (below.x,))
check("which is the top or bottom", below_label in ("Top", "Bottom"),
      below_label)
iso, iso_label = place((base.x + 70.0, base.y - 60.0))
check("off a corner, an isometric where the cursor is",
      iso is not None and near(iso.x, base.x + 70.0)
      and iso_label == "Isometric", (iso_label, iso and (iso.x, iso.y)))
check("without its hidden lines", iso is not None and iso.display == "visible")
check("nothing to place over the base view itself",
      place((base.x + 1.0, base.y + 1.0))[0] is None)
check("every projection drawn",
      all(v.projection is not None and not v.error for v in sheet.views),
      [(v.name, v.error) for v in sheet.views])

QTest.keyClick(canvas, QtCore.Qt.Key_Escape)
pump()
check("Esc ends it, keeping what was placed",
      ui._placer is None and canvas.placer is None and len(sheet.views) == 4)

# ------------------------------------------------------- Projected button
print()
print("Projected, with a view picked, places projections of that view")
canvas.select([base.id])
ui.add_projected()
pump()
check("placing from the picked view",
      ui._placer is not None and ui._placer.parent == base.id
      and ui._placer.dialog is None)
ui._placer.click((base.x, base.y + 70.0))
pump()
above = sheet.views[-1]
check("above it, in line", above.parent == base.id and near(above.x, base.x)
      and above.y > base.y)
canvas.placing_finished.emit()
pump()
check("a right-click ends it", ui._placer is None)

# ------------------------------------------------------ cancel, and OK
print()
print("Cancel places nothing, OK places it mid-sheet")
count = len(sheet.views)
ui.place_base_view()
pump()
ui._placer.dialog.reject()
pump()
check("Cancel ends it with nothing placed",
      ui._placer is None and len(sheet.views) == count)

ui.place_base_view()
pump()
dialog = ui._placer.dialog
dialog.projected.setChecked(False)
dialog.place_requested.emit()
pump()
latest = sheet.views[-1]
width, height = sheet.extent()
check("OK with no cursor yet puts it in the middle of the sheet",
      latest.kind == dwg.BASE and near(latest.x, width / 2.0)
      and near(latest.y, height / 2.0), (latest.x, latest.y))
check("and without projections asked for, that is the end of it",
      ui._placer is None)

print()
print("FAILED: " + ", ".join(FAILS) if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
