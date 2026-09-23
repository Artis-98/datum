"""Screenshot the start page and the profile picker."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402,F401


from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from datum.core.features import ExtrudeFeature, SketchFeature  # noqa: E402
from datum.core.sketch import STANDARD_PLANES, Sketch  # noqa: E402
from datum.ui import icons  # noqa: E402
from datum.ui.main_window import MainWindow  # noqa: E402
from datum.ui.theme import stylesheet  # noqa: E402

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


def settle(ms=800):
    timer = QtCore.QElapsedTimer()
    timer.start()
    while timer.elapsed() < ms:
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 20)


def shot(name, extra=None):
    pump()
    if not win.on_start_page:
        win.viewport.redraw()
    pump()
    _n[0] += 1
    chrome = win.grab()
    if not win.on_start_page:
        vp_path = os.path.join(OUT, "_vp.png")
        win.viewport.grab_image(vp_path)
        image = QtGui.QImage(vp_path)
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
            os.remove(vp_path)
        except OSError:
            pass
    chrome.save(os.path.join(OUT, "%02d_%s.png" % (_n[0], name)))
    print("shot:", name)


def click(u, v):
    ed._on_move(u, v, QtCore.Qt.NoModifier)
    ed._on_click(u, v, QtCore.Qt.NoModifier)
    pump(1)


def go():
    # give the recent list something to show
    demo = os.path.join(OUT, "Bracket.pdat")
    win.new_document(prompt=False)
    win.start_sketch_on_plane("XY")
    settle(500)
    ed.set_tool("rect")
    click(-35, -25)
    click(35, 25)
    win.finish_sketch()
    settle(500)
    win.new_feature(ExtrudeFeature)
    win.select_all_profiles()
    win._active_dialog.distance.set_text("15")
    win._active_dialog.commit()
    pump()
    win.viewport.set_view("iso")
    win.document.path = demo
    win.save_document()
    pump()

    win.show_start_page()
    shot("start_page")

    # a sketch with two separate regions, to show profile picking
    win.new_document(prompt=False)
    feature = SketchFeature()
    feature.name = "Two Regions"
    feature.sketch = Sketch(STANDARD_PLANES["XY"], "Two Regions")
    feature.sketch.add_rectangle((0, 0), (40, 30))
    feature.sketch.add_circle((70, 15), 12)
    win.document.add_feature(feature)
    win.rebuild()
    win.viewport.set_view("iso")

    win.new_feature(ExtrudeFeature)
    dlg = win._active_dialog
    dlg.profiles.set_picking(True)
    pump()
    region = win._profile_regions[0]
    dlg.on_profile_clicked(region["sketch_id"], region["centre"])
    dlg.distance.set_text("18")
    dlg.preview()
    pump()
    dlg.move(win.mapToGlobal(QtCore.QPoint(win.width() - 420, 150)))
    shot("profile_picker", dlg)
    dlg.cancel()
    pump()

    print("DONE")
    app.quit()


QtCore.QTimer.singleShot(1500, go)
sys.exit(app.exec())
