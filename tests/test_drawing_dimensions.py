"""Dimensioning a drawing the Inventor way.

Pick on a view what to measure and click clear of it to place: a line's
length, two points, a point and a line, two parallel lines, the angle
between two that are not, a circle's diameter.  Placed above or beside it
reads across or up the page.  Each is tied to the model where it can be,
so it follows the model when that changes; a selected one's legs can be
dragged onto other points; a double-click opens its text.
"""

import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import harness  # noqa: E402,F401

os.environ["DATUM_SETTINGS_ORG"] = "IITEG-tests"

from PySide6 import QtCore, QtGui, QtWidgets                       # noqa: E402

from datum.core import drawing as dwg, hlr                         # noqa: E402
from datum.core.document import Document                           # noqa: E402
from datum.core.features import CUT, PrimitiveFeature              # noqa: E402
from datum.ui import drawpick                                      # noqa: E402
from datum.ui.drawing_ui import DimensionTool                      # noqa: E402
from datum.ui.main_window import MainWindow                        # noqa: E402
from datum.ui.theme import stylesheet                              # noqa: E402

FAILS = []
WORK = tempfile.mkdtemp(prefix="datum_drawing_dims_")


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def near(a, b, tol=1e-4):
    return abs(a - b) <= tol


# a 60 x 40 x 20 block with a 10 mm hole through it, 30 in from the left
PART = os.path.join(WORK, "Block.pdat")
part = Document()
block = PrimitiveFeature(kind="box", a="60", b="40", c="20")
part.add_feature(block)
hole = PrimitiveFeature(kind="cylinder", a="5", b="20", operation=CUT,
                        origin=("30", "20", "0"))
part.add_feature(hole)
part.rebuild()
part.save(PART)

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


win.new_drawing(prompt=False)
pump()
win.drawing.path = os.path.join(WORK, "Block.ddat")
win.save_document()
pump()
ui = win.drawing_ui
canvas = win.sheet_canvas
doc = win.drawing
sheet = doc.active()

ui.place_base_view()
pump()
session = ui._placer
session.dialog.choose_file(PART)
session.dialog.scale.setEditText("1:1")
session.set_orientation(*hlr.ORIENTATIONS["top"])
session.projections.wait()
session.dialog.place_requested.emit()
pump()
view = sheet.views[0]
check("a top view of the block", view.projection is not None
      and not view.error, view.error)

# ------------------------------------------------------------ what is there
print()
print("the view is known for what it shows")
things = drawpick.items(view)
lines = [i for i in things if i.kind == drawpick.LINE]
circles = [i for i in things if i.kind == drawpick.CIRCLE]
check("four edges and the hole", len(lines) == 4 and len(circles) == 1,
      (len(lines), len(circles)))
if len(lines) != 4 or len(circles) != 1:
    print("FAILED: " + ", ".join(FAILS))
    sys.exit(1)
top = max((i for i in lines if drawpick.axis_of(i) == "x"),
          key=lambda i: i.a[1])
bottom = min((i for i in lines if drawpick.axis_of(i) == "x"),
             key=lambda i: i.a[1])
left = min((i for i in lines if drawpick.axis_of(i) == "y"),
           key=lambda i: i.a[0])
right = max((i for i in lines if drawpick.axis_of(i) == "y"),
            key=lambda i: i.a[0])
circle = circles[0]
check("the hole is a whole circle, 5 round", circle.closed
      and near(circle.radius, 5.0, 1e-3), circle)


def sheet_at(local):
    return (view.x + local[0], view.y + local[1])


def middle(item):
    return ((item.a[0] + item.b[0]) / 2.0, (item.a[1] + item.b[1]) / 2.0)


ui.start_dimension()
tool = ui._placer
check("Dimension starts the tool", isinstance(tool, DimensionTool))
tool._scale = canvas.layout().scale


def measure(picks, place):
    before = len(sheet.annotations)
    for local in picks:
        tool.press(sheet_at(local), QtCore.QPointF())
    tool.move(sheet_at(place), QtCore.QPointF(), False)
    tool.press(sheet_at(place), QtCore.QPointF())
    pump(1)
    if len(sheet.annotations) == before:
        return None
    return sheet.annotations[-1]


def reads(note):
    return note.caption(doc.view_scale(sheet, view)) if note else None


# ---------------------------------------------------------------- distances
print()
print("pick, then click clear of it to place")
length = measure([middle(top)], (middle(top)[0], top.a[1] + 12.0))
check("a line's length, placed above it, reads across: 60",
      length is not None and length.kind == dwg.LINEAR
      and length.axis == "x" and reads(length) == "60", reads(length))
check("tied to the model at both ends",
      length is not None and len(length.anchors) == 2
      and all(a is not None for a in length.anchors))

corner_a, corner_b = left.a, right.b
corners = measure([corner_a, corner_b], (right.a[0] + 15.0, 0.0))
check("two corners, placed beside, read up the page: 40",
      corners is not None and corners.axis == "y" and reads(corners) == "40",
      reads(corners))

across = measure([middle(left), middle(right)], (0.0, bottom.a[1] - 14.0))
check("two parallel lines: how far apart, 60", reads(across) == "60",
      reads(across))

to_hole = measure([circle.a, middle(left)], (-10.0, bottom.a[1] - 25.0))
check("the hole's centre from the left edge: 30", reads(to_hole) == "30",
      reads(to_hole))

angle = measure([middle(left), middle(bottom)],
                (left.a[0] + 6.0, bottom.a[1] + 6.0))
check("the angle between two edges: 90",
      angle is not None and angle.kind == dwg.ANGULAR
      and near(angle.measure(), 90.0, 1e-6), angle and angle.measure())

diameter = measure([(circle.a[0] + circle.radius, circle.a[1])],
                   (circle.a[0] + 20.0, circle.a[1] + 15.0))
check("the hole: a diameter of 10", diameter is not None
      and diameter.kind == dwg.DIAMETER and reads(diameter) == "⌀10",
      reads(diameter))
check("tied to the hole itself", diameter is not None and diameter.anchors
      and diameter.anchors[0] is not None
      and diameter.anchors[0].kind == "edge")

print()
print("Esc lets go of half a dimension, then of the tool")
tool.press(sheet_at(corner_a), QtCore.QPointF())
check("a point picked", len(tool.picks) == 1)
ui.on_escape()
check("Esc drops it, the tool still on", not tool.picks and ui._placer is tool)
ui.on_escape()
check("Esc again ends the tool", ui._placer is None)

# --------------------------------------------------------------- the legs
print()
print("a selected dimension's leg is dragged to another point")
canvas.select([], [length.id])
canvas.repaint()
pump()
grips = canvas._grips()
check("its two legs show grips", len(grips) == 2)
end = grips[1][2]
target = sheet_at(right.b)
to = QtCore.QPointF(*canvas.layout().to_device(*target))


def send(kind, at, button=QtCore.Qt.NoButton, buttons=QtCore.Qt.NoButton):
    QtWidgets.QApplication.sendEvent(canvas, QtGui.QMouseEvent(
        kind, at, QtCore.QPointF(canvas.mapToGlobal(at.toPoint())),
        button, buttons, QtCore.Qt.NoModifier))
    pump(1)


send(QtCore.QEvent.MouseButtonPress, end, QtCore.Qt.LeftButton,
     QtCore.Qt.LeftButton)
send(QtCore.QEvent.MouseMove, to, QtCore.Qt.NoButton, QtCore.Qt.LeftButton)
send(QtCore.QEvent.MouseButtonRelease, to, QtCore.Qt.LeftButton)
check("dropped on the far corner, it snaps there",
      near(length.points[1][0], right.b[0], 1e-6)
      and near(length.points[1][1], right.b[1], 1e-6), length.points[1])
check("and is tied to the model there", length.anchors[1] is not None)

opened = []
canvas.annotation_activated.connect(opened.append)
label = dwg.label_at(corners)
at = QtCore.QPointF(*canvas.layout().to_device(view.x + label[0],
                                               view.y + label[1]))
send(QtCore.QEvent.MouseButtonDblClick, at, QtCore.Qt.LeftButton,
     QtCore.Qt.LeftButton)
check("a double-click on one opens its text", opened == [corners.id],
      opened)

# ------------------------------------------------------------ the model
print()
print("the dimensions follow the model when it changes")
block.a = "80"
hole.a = "6"
part.rebuild()
part.save(PART)
ui.rebuild(force=True)
pump()
check("the length now reads 80", reads(across) == "80", reads(across))
check("the hole's diameter now reads 12", reads(diameter) == "⌀12",
      reads(diameter))
check("still 30 from the edge", reads(to_hole) == "30", reads(to_hole))
check("nothing lost", not any(n.sick for n in sheet.annotations),
      [(n.kind, n.sick) for n in sheet.annotations])

print()
print("FAILED: " + ", ".join(FAILS) if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
