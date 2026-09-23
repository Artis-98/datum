"""Screenshot the Output row in both states: Body Name, then Boolean."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402,F401


from PySide6 import QtCore, QtGui, QtWidgets                       # noqa: E402

from datum.core.features import (                                  # noqa: E402
    NEW_BODY, ExtrudeFeature, SketchFeature,
)
from datum.core.sketch import STANDARD_PLANES, Sketch              # noqa: E402
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


def sketch(name, x0, y0, x1, y1):
    f = SketchFeature()
    f.name = name
    f.sketch = Sketch(STANDARD_PLANES["XY"], name)
    f.sketch.add_rectangle((x0, y0), (x1, y1))
    win.document.add_feature(f)
    win.rebuild()
    return f


def go():
    win.new_document(prompt=False)
    pump()
    sketch("Outline", 0, 0, 80, 60)

    win.new_feature(ExtrudeFeature)
    pump()
    d = win._active_dialog
    d.distance.set_text("25")
    win.select_all_profiles()
    d.body_name.setText("Housing")
    d.preview()
    win.viewport.set_view("iso")
    win.viewport.fit_all()
    settle(400)
    d.move(win.mapToGlobal(QtCore.QPoint(win.width() - 420, 150)))
    shot("first_solid_body_name", d)
    d.commit()
    pump()

    sketch("Boss", 90, 10, 140, 50)
    win.new_feature(ExtrudeFeature)
    pump()
    d = win._active_dialog
    d.distance.set_text("30")
    for r in win.available_regions():
        if r["centre"][0] > 85:
            d.on_profile_clicked(r["sketch_id"], r["centre"])
    d.operation.set_value(NEW_BODY)
    d.preview()
    win.viewport.set_view("iso")
    win.viewport.fit_all()
    settle(400)
    d.move(win.mapToGlobal(QtCore.QPoint(win.width() - 420, 150)))
    shot("second_solid_boolean", d)
    d.commit()
    pump()
    win.browser.expandAll()
    settle(300)
    shot("multibody_tree")

    print("DONE - %d bodies" % len(win.document.bodies))
    app.quit()


QtCore.QTimer.singleShot(1500, go)
sys.exit(app.exec())
