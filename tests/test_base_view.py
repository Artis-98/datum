"""Base View the Inventor way.

Base opens the Drawing View box: the parts and assemblies open in tabs,
the last one worked on first, or a file from a folder, with style, scale
and name.  The view is on the sheet at once, drawn, with a view cube
beside it to point it.  It can be dragged; clicks round it add
projections, which can be dragged along their line and dropped with a
right-click.  OK makes them all; Cancel or Esc makes nothing.
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

from datum.core import drawing as dwg, hlr                         # noqa: E402
from datum.ui import sheetcube                                     # noqa: E402
from datum.ui.drawing_ui import BaseViewSession                    # noqa: E402
from datum.ui.main_window import MainWindow                        # noqa: E402
from datum.ui.theme import stylesheet                              # noqa: E402

FAILS = []
WORK = tempfile.mkdtemp(prefix="datum_base_view_")


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def near(a, b, tol=1e-6):
    return abs(a - b) <= tol


# a position that came through the mouse is only as good as a pixel
PIXEL = 0.7


def same(u, v):
    return all(near(a, b, 1e-9) for a, b in zip(u, v))


# ------------------------------------------------------------- the cube
print("the cube points the view")
check("a face looks straight at it",
      sheetcube.face_view("top") == ((0.0, 0.0, -1.0), (0.0, 1.0, 0.0))
      and same(sheetcube.face_view("front")[0], hlr.ORIENTATIONS["front"][0]))
d, u = sheetcube.looking_at((1.0, -1.0, 1.0))
check("the front right top corner is the drawing's isometric",
      same(d, [c / math.sqrt(3) for c in hlr.ORIENTATIONS["iso"][0]]), d)
front, up = hlr.ORIENTATIONS["front"]
check("the right arrow brings the right face round",
      same(sheetcube.turned(front, up, "right")[0],
           hlr.ORIENTATIONS["right"][0]))
check("the up arrow brings the top round, front to the top of the page",
      sheetcube.turned(front, up, "up") == ((0.0, 0.0, -1.0),
                                            (0.0, 1.0, 0.0)))
check("a roll keeps the direction and turns the page",
      sheetcube.turned(front, up, "cw") == ((0.0, 1.0, 0.0),
                                            (-1.0, 0.0, 0.0)))

app = QtWidgets.QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(stylesheet())
win = MainWindow()
win.resize(1400, 900)
win.show()
app.processEvents()


def pump(n=3):
    for _ in range(n):
        app.processEvents()


def make_box(name, a, b, c):
    win.new_document(prompt=False)
    win.new_primitive("box")
    dialog = win._active_dialog
    dialog.a.set_text(a)
    dialog.b.set_text(b)
    dialog.c.set_text(c)
    dialog.commit()
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


def device(point):
    return QtCore.QPointF(*canvas.layout().to_device(*point))


def send(kind, point, button=QtCore.Qt.NoButton,
         buttons=QtCore.Qt.NoButton):
    at = device(point)
    QtWidgets.QApplication.sendEvent(canvas, QtGui.QMouseEvent(
        kind, at, QtCore.QPointF(canvas.mapToGlobal(at.toPoint())),
        button, buttons, QtCore.Qt.NoModifier))
    pump(1)


def drag(start, end):
    send(QtCore.QEvent.MouseButtonPress, start, QtCore.Qt.LeftButton,
         QtCore.Qt.LeftButton)
    middle = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
    send(QtCore.QEvent.MouseMove, middle, QtCore.Qt.NoButton,
         QtCore.Qt.LeftButton)
    send(QtCore.QEvent.MouseMove, end, QtCore.Qt.NoButton,
         QtCore.Qt.LeftButton)
    send(QtCore.QEvent.MouseButtonRelease, end, QtCore.Qt.LeftButton,
         QtCore.Qt.NoButton)


def click(point):
    send(QtCore.QEvent.MouseMove, point)
    send(QtCore.QEvent.MouseButtonPress, point, QtCore.Qt.LeftButton,
         QtCore.Qt.LeftButton)
    send(QtCore.QEvent.MouseButtonRelease, point, QtCore.Qt.LeftButton,
         QtCore.Qt.NoButton)


def settle():
    session.projections.wait()
    canvas.repaint()
    pump()


# ------------------------------------------------------------- the box
print()
print("Base opens the box, and the view is on the sheet at once")
ui.place_base_view()
pump()
session = ui._placer
dialog = session.dialog if isinstance(session, BaseViewSession) else None
check("a session is running, with the Drawing View box open",
      dialog is not None and dialog.isVisible() and canvas.placer is session)
check("the box has no orientation: the cube does that",
      not hasattr(dialog, "orientation"))
offered = [dialog.file.itemData(i) for i in range(dialog.file.count())]
check("it offers both open parts",
      os.path.abspath(block) in offered and os.path.abspath(plate) in offered,
      offered)
check("the part last worked on comes first",
      dialog.path() == os.path.abspath(plate), dialog.path())
harness.opening(block)
dialog._browse()
check("the folder button picks a file", dialog.path() == os.path.abspath(block))
dialog.scale.setEditText("1:2")
check("a typed scale is read as typed", near(dialog.scale_value(), 0.5))
settle()

width, height = sheet.extent()
middle = session._middle()
check("the view starts in the middle of the sheet",
      near(session.x, middle[0]) and near(session.y, middle[1]))
base = session.views()[0]
check("drawn, from the front", base["projection"] is not None
      and base["projection"].ok and same(base["direction"],
                                         hlr.ORIENTATIONS["front"][0]))
box = session._box(base)
check("the front of the block at 1:2 is 30 by 10",
      near(box[2] - box[0], 30.0, 1e-3) and near(box[3] - box[1], 10.0,
                                                  1e-3), box)
check("nothing is made yet", len(sheet.views) == 0)

# -------------------------------------------------------------- the cube
print()
print("clicking the cube turns the view")
cube = session.cube
check("the cube is drawn beside the view", cube._faces and cube._buttons)
check("from the front it is one square, the front",
      [name for name, _poly, _n in cube._faces] == ["front"],
      [name for name, _poly, _n in cube._faces])


def press_on_cube(where):
    click(canvas.layout().to_sheet(where.x(), where.y()))
    settle()


up_arrow = next(poly for name, poly in cube._buttons if name == "up")
press_on_cube(up_arrow.boundingRect().center())
check("the up arrow looks down from the top",
      same(session.direction, hlr.ORIENTATIONS["top"][0]),
      session.direction)
box = session._box(session.views()[0])
check("and the view is redrawn from there, 30 by 20",
      near(box[2] - box[0], 30.0, 1e-3)
      and near(box[3] - box[1], 20.0, 1e-3), box)
session.set_orientation(*hlr.ORIENTATIONS["front"])
settle()
square = cube._faces[0][1].boundingRect()
press_on_cube(square.topRight())
check("the square's top right corner is the isometric from there",
      same(session.direction,
           [c / math.sqrt(3) for c in hlr.ORIENTATIONS["iso"][0]]),
      session.direction)
session.set_orientation(*hlr.ORIENTATIONS["front"])
settle()
home = next(poly for name, poly in cube._buttons if name == "home")
centre = home.boundingRect().center()
click(canvas.layout().to_sheet(centre.x(), centre.y()))
settle()
check("the house goes to the isometric",
      same(session.direction,
           [c / math.sqrt(3) for c in hlr.ORIENTATIONS["iso"][0]]),
      session.direction)
session.set_orientation(*hlr.ORIENTATIONS["front"])
settle()

# -------------------------------------------------------------- dragging
print()
print("the view is dragged, not stuck to the cursor")
start = (session.x, session.y)
send(QtCore.QEvent.MouseMove, (start[0] + 80.0, start[1] + 40.0))
check("moving the mouse over the sheet leaves it where it is",
      near(session.x, start[0]) and near(session.y, start[1]))
drag(start, (start[0] - 40.0, start[1] + 30.0))
check("dragging it moves it", near(session.x, start[0] - 40.0, PIXEL)
      and near(session.y, start[1] + 30.0, PIXEL), (session.x, session.y))

# ------------------------------------------------------------ projections
print()
print("clicks round it add projections, which drag along their line")
bx, by = session.x, session.y
click((bx + 60.0, by + 2.0))
click((bx - 1.0, by - 45.0))
click((bx + 60.0, by - 45.0))
settle()
kids = session.children
check("three projections", len(kids) == 3, kids)
if len(kids) == 3:
    check("the side one level with the view", near(kids[0][1], by))
    check("the lower one in line with it", near(kids[1][0], bx))
    check("the corner one where it was put",
          near(kids[2][0], bx + 60.0, PIXEL) and near(kids[2][1], by - 45.0,
                                                       PIXEL))
    labels = [v["label"] for v in session.views()[1:]]
    check("named for what they show", labels[0] in ("Left", "Right")
          and labels[1] in ("Top", "Bottom") and labels[2] == "Isometric",
          labels)
    check("each drawn", all(v["projection"] is not None
                            and v["projection"].ok
                            for v in session.views()), labels)
    drag((kids[0][0], kids[0][1]), (kids[0][0] + 20.0, kids[0][1] + 25.0))
    check("dragging the side one keeps it level",
          near(session.children[0][1], by)
          and near(session.children[0][0], bx + 80.0, PIXEL),
          session.children[0])
    drag((bx, by), (bx + 10.0, by + 10.0))
    check("dragging the view takes its projections with it",
          near(session.children[1][0], bx + 10.0, PIXEL)
          and near(session.children[0][1], by + 10.0, PIXEL))
    bx, by = session.x, session.y
    at = session.children[2]
    send(QtCore.QEvent.MouseButtonPress, tuple(at), QtCore.Qt.RightButton,
         QtCore.Qt.RightButton)
    check("a right-click drops one", len(session.children) == 2
          and ui._placer is session)

# -------------------------------------------------------------------- OK
print()
print("OK makes them all")
dialog.place_requested.emit()
pump()
views = sheet.views
check("a base view and two projections", len(views) == 3
      and views[0].kind == dwg.BASE
      and all(v.kind == dwg.PROJECTED and v.parent == views[0].id
              for v in views[1:]), [v.kind for v in views])
if views:
    made = views[0]
    check("where it was put, at the scale and name given",
          near(made.x, bx) and near(made.y, by) and near(made.scale, 0.5)
          and made.name == "View1", (made.x, made.y, made.scale, made.name))
    check("pointed as the cube left it",
          same(made.direction, hlr.ORIENTATIONS["front"][0]))
    check("all drawn", all(v.projection is not None and not v.error
                           for v in views), [(v.name, v.error) for v in views])
check("the box and the session are gone",
      ui._placer is None and canvas.placer is None and not dialog.isVisible())

# ---------------------------------------------------------- Cancel and Esc
print()
print("Cancel and Esc make nothing")
count = len(sheet.views)
ui.place_base_view()
pump()
ui._placer.dialog.reject()
pump()
check("Cancel", ui._placer is None and len(sheet.views) == count)
ui.place_base_view()
pump()
click((60.0, 60.0))
QTest.keyClick(canvas, QtCore.Qt.Key_Escape)
pump()
check("Esc", ui._placer is None and len(sheet.views) == count)

# ------------------------------------------------------ Projected button
print()
print("Projected, with a view picked, places projections of it")
base_view = sheet.views[0]
canvas.select([base_view.id])
ui.add_projected()
pump()
check("placing from the picked view",
      ui._placer is not None and ui._placer.parent == base_view.id)
click((base_view.x, base_view.y + 70.0))
above = sheet.views[-1]
check("above it, in line", above.parent == base_view.id
      and near(above.x, base_view.x) and above.y > base_view.y)
send(QtCore.QEvent.MouseButtonPress, (20.0, 20.0), QtCore.Qt.RightButton,
     QtCore.Qt.RightButton)
pump()
check("a right-click ends it", ui._placer is None)

print()
print("FAILED: " + ", ".join(FAILS) if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
