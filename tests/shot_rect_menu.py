"""Screenshot the Rectangle drop-down and what its variants draw."""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402,F401


from PySide6 import QtCore, QtGui, QtWidgets                       # noqa: E402

from datum.ui import icons                                         # noqa: E402
from datum.ui.main_window import MainWindow                        # noqa: E402
from datum.ui.theme import stylesheet                              # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else "."
os.makedirs(OUT, exist_ok=True)

app = QtWidgets.QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(stylesheet())
app.setWindowIcon(icons.app_icon())
win = MainWindow()
win.resize(1500, 950)
win.show()
ed = win.editor
_n = [0]


def pump(n=4):
    for _ in range(n):
        app.processEvents()


def settle(ms=500):
    t = QtCore.QElapsedTimer()
    t.start()
    while t.elapsed() < ms:
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 20)


def shot(name, extra=None):
    pump()
    if not win.on_start_page:
        win.viewport.redraw()
    pump()
    _n[0] += 1
    chrome = win.grab()
    vp = os.path.join(OUT, "_vp.png")
    win.viewport.grab_image(vp)
    image = QtGui.QImage(vp)
    if not image.isNull():
        origin = win.viewport.mapTo(win, QtCore.QPoint(0, 0))
        p = QtGui.QPainter(chrome)
        p.drawImage(QtCore.QRect(origin.x(), origin.y(),
                                 win.viewport.width(),
                                 win.viewport.height()), image)
        if extra is not None and extra.isVisible():
            p.drawPixmap(win.mapFromGlobal(
                extra.mapToGlobal(QtCore.QPoint(0, 0))), extra.grab())
        p.end()
    try:
        os.remove(vp)
    except OSError:
        pass
    chrome.save(os.path.join(OUT, "%02d_%s.png" % (_n[0], name)))
    print("shot:", name)


def click(u, v):
    ed._on_move(u, v, QtCore.Qt.NoModifier)
    ed._on_click(u, v, QtCore.Qt.NoModifier)
    pump(1)


def draw(tool, points):
    ed.snap_grid = False
    ed.set_tool(tool)
    for u, v in points:
        click(u, v)


def go():
    win.new_document(prompt=False)
    win.start_sketch_on_plane("XY")
    pump()

    # the menu itself, popped open over the sketch tab
    menu = win.rect_button.menu()
    corner = win.rect_button.mapToGlobal(
        QtCore.QPoint(0, win.rect_button.height()))
    menu.popup(corner)
    settle(400)
    shot("rect_menu", menu)
    menu.close()
    pump()

    # one of each variant, laid out across the sketch
    draw("rect", [(-140, 60), (-90, 90)])
    draw("rect3", [(-70, 60), (-25, 75), (-32, 98)])
    draw("rect_centre", [(20, 75), (45, 92)])
    draw("rect3_centre", [(95, 78), (125, 88), (90, 92)])

    draw("slot", [(-135, 10), (-90, 10), (-90, 22)])
    draw("slot_overall", [(-65, 10), (-15, 10), (-15, 22)])
    draw("slot_centre", [(25, 10), (50, 10), (50, 22)])

    draw("slot_arc3", [(-120, -60), (-70, -60),
                       (-95, -35), (-95, -27)])
    draw("slot_arc_centre", [(20, -75), (60, -75),
                             (20, -35), (20, -25)])
    draw("polygon", [(110, -50), (135, -50)])

    settle(300)
    win.viewport.fit_all()
    settle(400)
    shot("rect_variants")

    counts = {}
    for ent in ed.sketch.entities.values():
        counts[ent.kind] = counts.get(ent.kind, 0) + 1
    print("DONE - entities:", counts)
    app.quit()


QtCore.QTimer.singleShot(1500, go)
sys.exit(app.exec())
