"""Screenshot the CAM workspace: the sheet, the nest and the toolpath."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402,F401


from PySide6 import QtCore, QtGui, QtWidgets                       # noqa: E402

from datum.core.document import Document                           # noqa: E402
from datum.core.features import (                                  # noqa: E402
    ExtrudeFeature, HoleFeature, SketchFeature,
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


def settle(ms=600):
    timer = QtCore.QElapsedTimer()
    timer.start()
    while timer.elapsed() < ms:
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 20)


def shot(name, extra=None):
    pump()
    win.viewport.redraw()
    pump()
    _n[0] += 1
    chrome = win.grab()
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


def flat_part(name, width, height, holes, slot=None):
    doc = Document()
    outline = SketchFeature()
    outline.sketch = Sketch(STANDARD_PLANES["XY"], "Outline")
    outline.sketch.add_rectangle((0, 0), (width, height))
    doc.add_feature(outline)

    extrude = ExtrudeFeature()
    extrude.distance = "3"
    extrude.profiles.add(outline.id, (width * 0.5, height * 0.5))
    doc.add_feature(extrude)

    centres = SketchFeature()
    centres.sketch = Sketch(STANDARD_PLANES["XY"], "Holes")
    for x, y, d in holes:
        centres.sketch.add_circle((x, y), d * 0.5)
    doc.add_feature(centres)

    cut = HoleFeature()
    cut.sketch_id = centres.id
    cut.diameter = str(holes[0][2])
    cut.depth = "20"
    cut.through = True
    doc.add_feature(cut)

    doc.rebuild()
    return doc.save(os.path.join(OUT, name))


def go():
    bracket = flat_part("Bracket", 180, 110,
                        ((30, 30, 12), (150, 30, 12), (30, 80, 12),
                         (150, 80, 12), (90, 55, 40)))
    gusset = flat_part("Gusset", 120, 120, ((60, 60, 50), (20, 20, 10)))

    win.new_cam(prompt=False)
    pump()
    win.cam.sheet_width, win.cam.sheet_height = "800", "500"
    win.cam.sheet_thickness = "3"
    win.cam.tool_diameter = "6"
    win.cam.lead_length = "8"
    win.cam_ui.rebuild(keep_camera=False)
    settle(400)
    shot("cam_empty_sheet")

    for path in (bracket, gusset, bracket, gusset):
        win.cam.add_part(path)
    win.cam.rebuild()
    win.cam.auto_arrange()
    win.cam_ui.rebuild(keep_camera=False)
    win.viewport.set_view("top")
    win.viewport.fit_all()
    settle(500)
    shot("cam_nested")

    # the sheet and tool dialog, with the toolpath live behind it
    win.cam_ui.sheet_dialog()
    pump()
    dialog = win.cam_ui.dialog
    dialog.move(win.mapToGlobal(QtCore.QPoint(win.width() - 420, 150)))
    settle(300)
    shot("cam_sheet_dialog", dialog)
    dialog.commit()
    pump()

    # zoom in so the compensation and the leads are visible
    win.cam_ui.browser.expandAll()
    win.viewport.set_view("top")
    win.viewport.fit_all()
    for _ in range(6):
        win.viewport.view.SetZoom(1.22)
    settle(400)
    shot("cam_toolpath_detail")

    report = win.cam.last_report
    print("DONE  -  %s" % report.message)
    print("cuts: %d, errors: %d, warnings: %d"
          % (len(report.toolpath.cuts), len(report.errors),
             len(report.warnings)))
    app.quit()


QtCore.QTimer.singleShot(1500, go)
sys.exit(app.exec())
