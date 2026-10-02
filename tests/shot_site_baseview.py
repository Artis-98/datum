"""The Base View picture for the website: a view being set up on the sheet.

The bearing housing from the drawing picture, with the Drawing View box
open, the front view drawn in the middle of the sheet with the view cube
beside it, and its projections round it, before OK makes them.

    python tests/shot_site_baseview.py [output.png]
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import harness  # noqa: E402,F401

os.environ["DATUM_SETTINGS_ORG"] = "IITEG-shots"

from PySide6 import QtCore, QtGui, QtWidgets                       # noqa: E402

from datum.core.document import Document                           # noqa: E402
from datum.core.features import PrimitiveFeature                   # noqa: E402
from datum.ui import icons                                         # noqa: E402
from datum.ui.main_window import MainWindow                        # noqa: E402
from datum.ui.theme import stylesheet                              # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "site", "img", "baseview.png")

app = QtWidgets.QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(stylesheet())
app.setWindowIcon(icons.app_icon())
win = MainWindow()
win.resize(1600, 958)
win.show()

PLATE = (160.0, 90.0, 16.0)
BOSS_R, BOSS_TOP = 40.0, 72.0
BORE_R, COUNTER_R, COUNTER_DEPTH = 23.5, 26.0, 10.0
HOLES = [(18.0, 18.0), (142.0, 18.0), (18.0, 72.0), (142.0, 72.0)]
HOLE_R, SPOT_R = 6.5, 11.0
CX, CY = PLATE[0] / 2.0, PLATE[1] / 2.0


def settle(ms=500):
    timer = QtCore.QElapsedTimer()
    timer.start()
    while timer.elapsed() < ms:
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 20)


def primitive(kind, a, b, c, origin, operation):
    feature = PrimitiveFeature()
    feature.kind = kind
    feature.a, feature.b, feature.c = "%g" % a, "%g" % b, "%g" % c
    feature.origin = tuple("%g" % v for v in origin)
    feature.operation = operation
    return feature


def housing(path):
    part = Document()
    part.material = "Aluminium 6061"
    part.properties.update({"Title": "BEARING HOUSING",
                            "PartNumber": "BH-100"})
    part.add_feature(primitive("box", *PLATE, (0, 0, 0), "new"))
    part.add_feature(primitive("cylinder", BOSS_R, BOSS_TOP - PLATE[2], 0,
                               (CX, CY, PLATE[2]), "join"))
    part.add_feature(primitive("cylinder", BORE_R, BOSS_TOP + 10, 0,
                               (CX, CY, -5), "cut"))
    part.add_feature(primitive("cylinder", COUNTER_R, COUNTER_DEPTH + 5, 0,
                               (CX, CY, BOSS_TOP - COUNTER_DEPTH), "cut"))
    for x, y in HOLES:
        part.add_feature(primitive("cylinder", HOLE_R, PLATE[2] + 10, 0,
                                   (x, y, -5), "cut"))
        part.add_feature(primitive("cylinder", SPOT_R, 5, 0,
                                   (x, y, PLATE[2] - 2), "cut"))
    part.rebuild()
    part.save(path)
    return path


def go():
    work = tempfile.mkdtemp(prefix="datum_site_baseview_")
    part = housing(os.path.join(work, "Bearing Housing.pdat"))
    win.open_path(part)
    settle(600)
    win.new_drawing(prompt=False)
    settle(400)
    win.drawing.path = os.path.join(work, "Bearing Housing.ddat")
    win.save_document()
    settle(300)

    ui = win.drawing_ui
    ui.place_base_view()
    settle(300)
    session = ui._placer
    dialog = session.dialog
    dialog.scale.setEditText("1:2")
    session.projections.wait()
    # The front view a little right of the middle, its plan under it, its
    # side view beside it, and an isometric off its top left corner, which
    # is the housing seen from the front, the left and above; the cube
    # keeps the top right
    sheet = win.drawing.active()
    width, height = sheet.extent()
    session.x, session.y = width * 0.47, height * 0.56
    for point in ((session.x, session.y - 62.0),
                  (session.x + 82.0, session.y),
                  (session.x - 118.0, session.y + 62.0)):
        session.press(point, QtCore.QPointF(-900, -900))
        session.release(point, QtCore.QPointF(-900, -900))
    session.projections.wait()
    win.sheet_canvas.fit()
    settle(400)
    session.cursor = None
    canvas = win.sheet_canvas
    dialog.move(canvas.mapToGlobal(QtCore.QPoint(
        14, canvas.height() - dialog.height() - 56)))
    win.status_message.setText(
        "Point the view with the cube and drag it where it goes. Click "
        "round it for projections, then OK.")
    win.status_build.setText("")
    canvas.repaint()
    settle(500)

    picture = win.grab()
    painter = QtGui.QPainter(picture)
    painter.drawPixmap(win.mapFromGlobal(dialog.mapToGlobal(
        QtCore.QPoint(0, 0))), dialog.grab())
    painter.end()
    picture.save(OUT)
    print("saved", OUT, picture.width(), "x", picture.height())
    app.quit()


QtCore.QTimer.singleShot(1600, go)
sys.exit(app.exec())
