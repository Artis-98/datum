"""Screenshot the open-document tabs and the Local Update workflow."""
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


def shot(name):
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
            p.end()
        try:
            os.remove(vp_path)
        except OSError:
            pass
    chrome.save(os.path.join(OUT, "%02d_%s.png" % (_n[0], name)))
    print("shot:", name)


def flat_part(name, width, height, thickness, holes):
    doc = Document()
    outline = SketchFeature()
    outline.sketch = Sketch(STANDARD_PLANES["XY"], "Outline")
    outline.sketch.add_rectangle((0, 0), (width, height))
    doc.add_feature(outline)

    extrude = ExtrudeFeature()
    extrude.distance = str(thickness)
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
    cut.depth = "60"
    cut.through = True
    doc.add_feature(cut)

    doc.rebuild()
    return doc.save(os.path.join(OUT, name))


def go():
    plate = flat_part("Base Plate", 180, 120, 8,
                      ((30, 30, 14), (150, 30, 14), (30, 90, 14),
                       (150, 90, 14)))
    rib = flat_part("Rib", 120, 70, 8, ((60, 35, 30),))

    # three documents open at once
    win.open_path(plate)
    win.open_path(rib)
    win.new_assembly()
    win.assembly.path = os.path.join(OUT, "Frame.adat")
    for path in (plate, rib):
        win.assembly.place(path)
    win.assembly.rebuild()
    win.assembly_ui._spread(win.assembly.occurrences[1:])
    win.assembly_ui.rebuild(keep_camera=False)
    win.viewport.set_view("iso")
    settle(500)
    shot("tabs_assembly")

    # edit the plate in its own tab
    plate_entry = win.session.by_path(plate)
    win.activate(plate_entry)
    pump()
    win.document.features[1].distance = "24"
    win.document.modified = True
    win.rebuild()
    win.viewport.set_view("iso")
    win.viewport.fit_all()
    settle(400)
    shot("tabs_part_edited")

    # back to the assembly: out of date, waiting for Local Update
    assembly_entry = next(e for e in win.session
                          if e.doc_type == "assembly")
    win.activate(assembly_entry)
    settle(400)
    shot("tabs_out_of_date")

    win.local_update()
    settle(400)
    shot("tabs_after_update")

    win.show_start_page()
    settle(300)
    shot("tabs_home")

    print("DONE - %d documents open" % len(win.session))
    app.quit()


QtCore.QTimer.singleShot(1500, go)
sys.exit(app.exec())
