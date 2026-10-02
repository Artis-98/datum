"""Screenshot Base View: the Drawing View box, the view following the
cursor, and a projection about to be placed."""
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

app = QtWidgets.QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(stylesheet())
win = MainWindow()
win.resize(1500, 950)
win.show()


def pump(n=5):
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
    win.document.path = os.path.join(WORK, name + ".pdat")
    win.save_document()
    pump()


def hover(here):
    canvas = win.sheet_canvas
    device = QtCore.QPointF(*canvas.layout().to_device(*here))
    QtWidgets.QApplication.sendEvent(canvas, QtGui.QMouseEvent(
        QtCore.QEvent.MouseMove, device,
        QtCore.QPointF(canvas.mapToGlobal(device.toPoint())),
        QtCore.Qt.NoButton, QtCore.Qt.NoButton, QtCore.Qt.NoModifier))
    pump()


def shot(name, extra=None):
    pump()
    image = win.grab()
    if extra is not None and extra.isVisible():
        painter = QtGui.QPainter(image)
        painter.drawPixmap(win.mapFromGlobal(extra.mapToGlobal(
            QtCore.QPoint(0, 0))), extra.grab())
        painter.end()
    image.save(os.path.join(OUT, name + ".png"))
    print("shot:", name)


make_box("Bracket", "80", "50", "30")
win.new_drawing(prompt=False)
pump()
win.drawing.path = os.path.join(WORK, "Bracket.ddat")
win.save_document()
pump()

ui = win.drawing_ui
ui.place_base_view()
pump()
dialog = ui._placer.dialog
dialog.move(win.mapToGlobal(QtCore.QPoint(60, 180)))
hover((150.0, 200.0))
shot("01_base_following", dialog)

ui._placer.click((150.0, 200.0))
pump(10)
hover((290.0, 203.0))
shot("02_projected_beside")
ui._placer.click((290.0, 200.0))
pump(10)
hover((290.0, 90.0))
shot("03_iso_corner")
