"""Drawing views after they are placed: moved quickly, edited, followed.

Moving a view draws nothing again unless it turned, so views can be slid
round as fast as the mouse goes.  An isometric is taken from the view it
comes from and turns with it.  A double-click opens a view's box again:
the base view's with the cube, any other's with its scale locked to the
base view's unless it is given its own.  Two quick clicks on the cube's
arrows turn the view twice.  The wheel clicked twice fits the sheet, and
New asks what to make.
"""

import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import harness  # noqa: E402,F401

os.environ["DATUM_SETTINGS_ORG"] = "IITEG-tests"

from PySide6 import QtCore, QtGui, QtWidgets                       # noqa: E402

from datum.core import drawing as dwg, fileformat, hlr             # noqa: E402
from datum.core import views as viewgen                            # noqa: E402
from datum.ui import dialogs                                       # noqa: E402
from datum.ui.drawing_ui import BaseViewSession, ViewDialog        # noqa: E402
from datum.ui.main_window import MainWindow                        # noqa: E402
from datum.ui.theme import stylesheet                              # noqa: E402

FAILS = []
WORK = tempfile.mkdtemp(prefix="datum_drawing_edit_")


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def near(a, b, tol=1e-6):
    return abs(a - b) <= tol


def same(u, v, tol=1e-9):
    return all(near(a, b, tol) for a, b in zip(u, v))


# --------------------------------------------------------------- isometric
print("an isometric is taken from the view it comes from")
front_d, front_u = hlr.ORIENTATIONS["front"]
d, u = viewgen.iso_orientation(front_d, front_u, 50.0, 40.0)
check("off the top right of a front view, the usual isometric",
      same(d, [c / math.sqrt(3) for c in hlr.ORIENTATIONS["iso"][0]]), d)
d2, _u2 = viewgen.iso_orientation(front_d, front_u, -50.0, 40.0)
check("off the top left, seen from the left instead",
      d2[0] > 0 and d2[1] > 0 and d2[2] < 0, d2)
top_d, top_u = hlr.ORIENTATIONS["top"]
d3, u3 = viewgen.iso_orientation(top_d, top_u, 50.0, 40.0)
check("off a top view, the top view tipped, not the front's isometric",
      not same(d3, d) and d3[2] < -0.5, d3)
check("its up is square to where it looks",
      near(sum(a * b for a, b in zip(d3, u3)), 0.0, 1e-9))

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
win.new_drawing(prompt=False)
pump()
win.drawing.path = os.path.join(WORK, "Sheet.ddat")
win.save_document()
pump()
ui = win.drawing_ui
canvas = win.sheet_canvas
doc = win.drawing
sheet = doc.active()


def device(point):
    return QtCore.QPointF(*canvas.layout().to_device(*point))


def send(kind, point, button=QtCore.Qt.NoButton, buttons=QtCore.Qt.NoButton):
    at = device(point)
    QtWidgets.QApplication.sendEvent(canvas, QtGui.QMouseEvent(
        kind, at, QtCore.QPointF(canvas.mapToGlobal(at.toPoint())),
        button, buttons, QtCore.Qt.NoModifier))
    pump(1)


def drag(start, end):
    send(QtCore.QEvent.MouseButtonPress, start, QtCore.Qt.LeftButton,
         QtCore.Qt.LeftButton)
    send(QtCore.QEvent.MouseMove, end, QtCore.Qt.NoButton,
         QtCore.Qt.LeftButton)
    send(QtCore.QEvent.MouseButtonRelease, end, QtCore.Qt.LeftButton,
         QtCore.Qt.NoButton)


# a base view with a side view and an isometric, made the usual way
ui.place_base_view()
pump()
session = ui._placer
session.dialog.scale.setEditText("1:1")
session.projections.wait()
bx, by = session.x, session.y
for point in ((bx + 90.0, by), (bx, by - 70.0), (bx + 90.0, by + 70.0)):
    session.press(point, QtCore.QPointF(-900, -900))
    session.release(point, QtCore.QPointF(-900, -900))

# ---------------------------------------------------------------- the cube
print()
print("two quick clicks on a cube arrow turn the view twice")
session.projections.wait()
canvas.repaint()
pump()
right = next(poly for name, poly in session.cube._buttons if name == "turn:right")
at = right.boundingRect().center()
point = canvas.layout().to_sheet(at.x(), at.y())
send(QtCore.QEvent.MouseButtonPress, point, QtCore.Qt.LeftButton,
     QtCore.Qt.LeftButton)
send(QtCore.QEvent.MouseButtonRelease, point, QtCore.Qt.LeftButton)
send(QtCore.QEvent.MouseButtonDblClick, point, QtCore.Qt.LeftButton,
     QtCore.Qt.LeftButton)
send(QtCore.QEvent.MouseButtonRelease, point, QtCore.Qt.LeftButton)
check("front, right, back", same(session.direction,
                                 hlr.ORIENTATIONS["back"][0]),
      session.direction)
for _ in range(2):
    session.press(point, at)
check("and on round to the front again", same(session.direction,
                                             front_d), session.direction)
session.dialog.place_requested.emit()
pump()
base = sheet.views[0]
side, below, iso = sheet.views[1:4]
check("the views are made", base.kind == dwg.BASE and len(sheet.views) == 4
      and all(v.projection is not None for v in sheet.views))
iso_now = ui.generator.orientation(doc, sheet, iso)[0]
check("the isometric off the top right is the usual one",
      same(iso_now, [c / math.sqrt(3) for c in hlr.ORIENTATIONS["iso"][0]]),
      iso_now)

# ---------------------------------------------------------------- moving
print()
print("moving views draws nothing again unless one turned")
counted = []
original_one = ui.generator._one


def counting(doc_, sheet_, view, *args, **kwargs):
    counted.append(view.id)
    return original_one(doc_, sheet_, view, *args, **kwargs)


ui.generator._one = counting
changes = []
canvas.changed.connect(lambda: changes.append(1))
before = {v.id: v.projection for v in sheet.views}

send(QtCore.QEvent.MouseButtonPress, (side.x, side.y), QtCore.Qt.LeftButton,
     QtCore.Qt.LeftButton)
send(QtCore.QEvent.MouseButtonRelease, (side.x, side.y), QtCore.Qt.LeftButton)
check("a click on a view is not a change", not changes and not counted)

drag((side.x, side.y), (side.x + 25.0, side.y + 10.0))
check("sliding a side view along its line draws nothing again",
      changes and not counted and side.projection is before[side.id],
      counted)
check("and it stays level", near(side.y, base.y))
drag((base.x, base.y), (base.x - 15.0, base.y - 12.0))
check("dragging the base view takes the others along and draws nothing",
      not counted and all(v.projection is before[v.id] for v in sheet.views)
      and near(side.y, base.y) and near(below.x, base.x), counted)
check("and still nothing is selected away: the base view stays picked",
      canvas.selected_views == [base.id], canvas.selected_views)

side_was = ui.generator.orientation(doc, sheet, side)[0]
drag((side.x, side.y), (base.x - (side.x - base.x), side.y))
side_now = ui.generator.orientation(doc, sheet, side)[0]
check("dragged across to the other side, it turns",
      not same(side_now, side_was), (side_was, side_now))
check("and only it is drawn again", counted == [side.id], counted)
ui.generator._one = original_one

# ------------------------------------------------------------- editing
print()
print("a double-click opens the view's box again")
send(QtCore.QEvent.MouseButtonDblClick, (base.x, base.y + 9.0),
     QtCore.Qt.LeftButton, QtCore.Qt.LeftButton)
pump()
session = ui._placer
check("the base view gets the Drawing View box and the cube",
      isinstance(session, BaseViewSession) and session.editing is base
      and session.dialog is not None)
if isinstance(session, BaseViewSession):
    dialog = session.dialog
    check("filled in from the view", dialog.path() == os.path.abspath(block)
          and dialog.name.text() == base.name
          and near(dialog.scale_value(), base.scale))
    check("with its projections there to drag",
          sorted(session.child_ids) == sorted(v.id for v in (side, below,
                                                              iso)))
    check("and the views themselves left out while it is open",
          base.id in session.hidden and side.id in session.hidden)
    session.set_orientation(*hlr.ORIENTATIONS["top"])
    dialog.scale.setEditText("1:2")
    session.projections.wait()
    dialog.place_requested.emit()
    pump()
    check("OK changes the view rather than making another",
          len(sheet.views) == 4 and sheet.views[0] is base
          and near(base.scale, 0.5)
          and same(ui.generator.orientation(doc, sheet, base)[0],
                   hlr.ORIENTATIONS["top"][0]))
    check("and its projections turn with it, drawn again",
          all(v.projection is not None and not v.error for v in sheet.views)
          and not same(ui.generator.orientation(doc, sheet, iso)[0],
                       iso_now))
    check("the box is gone", ui._placer is None)

print()
print("any other view has its scale locked to the base view's")
box = ViewDialog(win, doc, sheet, side)
check("locked, showing the base view's",
      not box.own_scale.isChecked() and not box.scale.isEnabled()
      and dwg.parse_scale(box.scale.currentText()) == 0.5,
      box.scale.currentText())
box.own_scale.setChecked(True)
check("unlocked with the box ticked", box.scale.isEnabled())
box.scale.setEditText("1:5")
box.apply()
check("then it has its own", near(side.scale, 0.2))
box = ViewDialog(win, doc, sheet, side)
box.own_scale.setChecked(False)
box.apply()
check("and unticked, the base view's again", side.scale == 0.0)

# ----------------------------------------------------------- the sheet
print()
print("the wheel clicked twice fits the sheet")
canvas.zoom(3.0)
send(QtCore.QEvent.MouseButtonDblClick, (100.0, 100.0),
     QtCore.Qt.MiddleButton, QtCore.Qt.MiddleButton)
check("fitted", canvas._scale == 1.0 and canvas._pan == [0.0, 0.0])

print()
print("New asks what to make")
asked = []
original_ask = dialogs.NewDocumentDialog.ask
dialogs.NewDocumentDialog.ask = staticmethod(
    lambda parent=None: asked.append(1) or fileformat.ASSEMBLY)
button = next(b for b in win.findChildren(QtWidgets.QAbstractButton)
              if b.toolTip().startswith("New (Ctrl+N)"))
button.click()
pump()
dialogs.NewDocumentDialog.ask = original_ask
check("the corner button asks, and makes what was picked",
      asked and win.in_assembly)

print()
print("FAILED: " + ", ".join(FAILS) if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
