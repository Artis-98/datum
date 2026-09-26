"""The pictures on datum.iiteg.com, taken from the real application.

Usage:  python tests/shot_site.py [out_dir]

Everything it shows is a sample in this repository, opened in the real
window, so a picture on the site cannot drift away from what the software
actually does.  The window is sized to the width the page lays out for,
and the page carries the height each file comes out at, so nothing is
squeezed by a stale attribute.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import harness  # noqa: E402,F401


from PySide6 import QtCore, QtGui, QtWidgets                   # noqa: E402

from datum.ui import icons                                     # noqa: E402
from datum.ui.main_window import MainWindow                    # noqa: E402
from datum.ui.theme import stylesheet                          # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES = os.path.join(ROOT, "examples", "excavator")
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "site", "img")
os.makedirs(OUT, exist_ok=True)

app = QtWidgets.QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(stylesheet())
app.setWindowIcon(icons.app_icon())
win = MainWindow()
win.resize(1600, 958)
win.show()


def pump(n=4):
    for _ in range(n):
        app.processEvents()


def settle(ms=600):
    timer = QtCore.QElapsedTimer()
    timer.start()
    while timer.elapsed() < ms:
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 20)


def shot(name):
    pump()
    win.viewport.redraw()
    pump()
    chrome = win.grab()
    scratch = os.path.join(OUT, "_vp.png")
    win.viewport.grab_image(scratch)
    image = QtGui.QImage(scratch)
    if not image.isNull():
        origin = win.viewport.mapTo(win, QtCore.QPoint(0, 0))
        painter = QtGui.QPainter(chrome)
        painter.drawImage(QtCore.QRect(origin.x(), origin.y(),
                                       win.viewport.width(),
                                       win.viewport.height()), image)
        painter.end()
    try:
        os.remove(scratch)
    except OSError:
        pass
    path = os.path.join(OUT, name + ".png")
    chrome.save(path)
    print("%-14s %d x %d" % (name, chrome.width(), chrome.height()))


def look(view="iso"):
    win.viewport.set_view(view)
    settle(450)
    win.viewport.fit_all()
    settle(450)


def go():
    win.open_assembly(os.path.join(SAMPLES, "Excavator.adat"))
    settle(1400)
    win.assembly_ui.browser.expandAll()
    look("iso")
    shot("assembly")

    win.open_path(os.path.join(SAMPLES, "Bucket.pdat"))
    settle(700)
    win.browser.expandAll()
    look("iso")
    shot("part")

    print("DONE")
    app.quit()


QtCore.QTimer.singleShot(1500, go)
sys.exit(app.exec())
