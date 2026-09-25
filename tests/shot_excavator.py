"""Screenshot the excavator: the assembly, and the parts worth a close look.

Everything it shows was written by examples/build_excavator.py, so this only
opens documents and points the camera at them.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402,F401


from PySide6 import QtCore, QtGui, QtWidgets                       # noqa: E402

from datum.ui import icons                                         # noqa: E402
from datum.ui.main_window import MainWindow                        # noqa: E402
from datum.ui.theme import stylesheet                              # noqa: E402

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "examples", "excavator")
OUT = sys.argv[1] if len(sys.argv) > 1 else "."
os.makedirs(OUT, exist_ok=True)

app = QtWidgets.QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(stylesheet())
app.setWindowIcon(icons.app_icon())
win = MainWindow()
win.resize(1600, 980)
win.show()

_n = [0]


def pump(n=4):
    for _ in range(n):
        app.processEvents()


def settle(ms=600):
    timer = QtCore.QElapsedTimer()
    timer.start()
    while timer.elapsed() < ms:
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 20)


def shot(name, plain=None):
    """Save the window, and optionally the bare viewport beside the model.

    The framed shots are for the site; the bare ones go in the sample's own
    folder, where a README wants the machine and not the application.
    """
    pump()
    win.viewport.redraw()
    pump()
    _n[0] += 1
    chrome = win.grab()
    vp_path = os.path.join(OUT, "_vp.png")
    win.viewport.grab_image(vp_path)
    if plain:
        win.viewport.grab_image(os.path.join(SRC, plain + ".png"))
    image = QtGui.QImage(vp_path)
    if not image.isNull():
        origin = win.viewport.mapTo(win, QtCore.QPoint(0, 0))
        p = QtGui.QPainter(chrome)
        p.drawImage(QtCore.QRect(origin.x(), origin.y(),
                                 win.viewport.width(),
                                 win.viewport.height()), image)
        p.end()
    try:
        os.remove(vp_path)
    except OSError:
        pass
    chrome.save(os.path.join(OUT, "%02d_%s.png" % (_n[0], name)))
    print("shot:", name)


def look(view="iso"):
    win.viewport.set_view(view)
    settle(450)
    win.viewport.fit_all()
    settle(450)


def frame(centre, span, view=None):
    if view:
        win.viewport.set_view(view)
        settle(250)
    px, py = win.viewport.project(centre)
    win.viewport.view.SetCenter(int(px), int(py))
    win.viewport.view.SetSize(float(span))
    settle(450)


def go():
    win.open_assembly(os.path.join(SRC, "Excavator.adat"))
    settle(1200)
    win.assembly_ui.browser.expandAll()
    for view, plain in (("iso", "assembly"), ("front", None),
                        ("right", "side"), ("top", None)):
        look(view)
        shot("assembly_" + view, plain)

    look("iso")
    frame((2700.0, 700.0, 1400.0), 2600.0)
    shot("linkage_detail", "linkage")
    frame((0.0, 0.0, 400.0), 2600.0)
    shot("undercarriage_detail", "undercarriage")

    for stem in ("Bucket", "Boom", "Rubber Track", "Ram Barrel"):
        win.open_path(os.path.join(SRC, stem + ".pdat"))
        settle(500)
        win.browser.expandAll()
        look("iso")
        shot("part_" + stem.lower().replace(" ", "_"),
             "part-" + stem.lower().replace(" ", "-"))

    print("DONE")
    app.quit()


QtCore.QTimer.singleShot(1500, go)
sys.exit(app.exec())
