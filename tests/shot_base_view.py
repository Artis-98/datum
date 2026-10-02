"""Screenshot Base View: the Drawing View box, the part drawn on the sheet
with the cube beside it, and projections set up round it."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import harness  # noqa: E402,F401

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from datum.ui.main_window import MainWindow  # noqa: E402
from datum.ui.theme import stylesheet  # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else "."
os.makedirs(OUT, exist_ok=True)
WORK = tempfile.mkdtemp(prefix="datum_shot_base_")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PART = os.path.join(ROOT, "examples", "excavator", "Boom.pdat")

app = QtWidgets.QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(stylesheet())
win = MainWindow()
win.resize(1500, 950)
win.show()


def pump(n=5):
    for _ in range(n):
        app.processEvents()


def shot(name, extra=None):
    session = win.drawing_ui._placer
    if session is not None and hasattr(session, "projections"):
        session.projections.wait()
    win.sheet_canvas.repaint()
    pump()
    image = win.grab()
    if extra is not None and extra.isVisible():
        painter = QtGui.QPainter(image)
        painter.drawPixmap(win.mapFromGlobal(extra.mapToGlobal(
            QtCore.QPoint(0, 0))), extra.grab())
        painter.end()
    image.save(os.path.join(OUT, name + ".png"))
    print("shot:", name)


win.open_path(PART)
pump(10)
win.new_drawing(prompt=False)
pump()
win.drawing.path = os.path.join(WORK, "Boom.ddat")
win.save_document()
pump()

ui = win.drawing_ui
ui.place_base_view()
pump()
session = ui._placer
dialog = session.dialog
shot("01_base_on_sheet", dialog)

bx, by = session.x, session.y
size = session._size(session.views()[0])
for point in ((bx + size[0] * 0.5 + 45.0, by),
              (bx, by - size[1] * 0.5 - 40.0),
              (bx + size[0] * 0.5 + 45.0, by - size[1] * 0.5 - 40.0)):
    session.press(point, QtCore.QPointF(-500, -500))
    session.release(point, QtCore.QPointF(-500, -500))
shot("02_projections_set_up", dialog)

session.set_orientation(*session.cube.view_for("corner:1,-1,1"))
shot("03_turned_by_the_cube", dialog)
