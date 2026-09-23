"""Screenshot picking individual regions out of overlapping circles."""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402,F401


from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from datum.core.features import ExtrudeFeature, SketchFeature  # noqa: E402
from datum.core.sketch import STANDARD_PLANES, Sketch  # noqa: E402
from datum.ui.main_window import MainWindow  # noqa: E402
from datum.ui.theme import stylesheet  # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else "."
os.makedirs(OUT, exist_ok=True)

app = QtWidgets.QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(stylesheet())
win = MainWindow()
win.resize(1500, 950)
win.show()

_n = [0]


def pump(n=4):
    for _ in range(n):
        app.processEvents()


def settle(ms=400):
    timer = QtCore.QElapsedTimer()
    timer.start()
    while timer.elapsed() < ms:
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 20)


def shot(name, extra=None):
    pump()
    win.viewport.redraw()
    pump()
    _n[0] += 1
    vp_path = os.path.join(OUT, "_vp.png")
    win.viewport.grab_image(vp_path)
    chrome = win.grab()
    image = QtGui.QImage(vp_path)
    if not image.isNull():
        origin = win.viewport.mapTo(win, QtCore.QPoint(0, 0))
        p = QtGui.QPainter(chrome)
        p.drawImage(QtCore.QRect(origin.x(), origin.y(), win.viewport.width(),
                                 win.viewport.height()), image)
        if extra is not None and extra.isVisible():
            p.drawPixmap(win.mapFromGlobal(
                extra.mapToGlobal(QtCore.QPoint(0, 0))), extra.grab())
        p.end()
    chrome.save(os.path.join(OUT, "%02d_%s.png" % (_n[0], name)))
    try:
        os.remove(vp_path)
    except OSError:
        pass
    print("shot:", name)


def go():
    win.new_document(prompt=False)
    feature = SketchFeature()
    feature.name = "Three Circles"
    feature.sketch = Sketch(STANDARD_PLANES["XY"], "Three Circles")
    for i in range(3):
        a = math.radians(90 + 120 * i)
        feature.sketch.add_circle((20 * math.cos(a), 20 * math.sin(a)), 30.0)
    win.document.add_feature(feature)
    win.rebuild()
    win.viewport.set_view("top")
    settle()

    win.new_feature(ExtrudeFeature)
    dlg = win._active_dialog
    pump()
    dlg.profiles.set_picking(True)
    pump()
    dlg.move(win.mapToGlobal(QtCore.QPoint(win.width() - 420, 150)))
    shot("seven_regions_offered", dlg)

    ordered = sorted(range(len(win._profile_regions)),
                     key=lambda i: win._profile_regions[i]["area"])
    for index in ordered[:4]:
        entry = win._profile_regions[index]
        dlg.on_profile_clicked(entry["sketch_id"], entry["centre"])
    dlg.distance.set_text("12")
    dlg.preview()
    pump()
    shot("four_picked", dlg)

    dlg.commit()
    pump()
    win.viewport.set_view("iso")
    settle()
    shot("extruded")

    print("DONE")
    app.quit()


QtCore.QTimer.singleShot(1500, go)
sys.exit(app.exec())
