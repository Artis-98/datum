"""Screenshot the assembly workspace: the tree, the ribbon, the constraint
dialog, and a bracket-and-pins assembly actually solved."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402,F401


from PySide6 import QtCore, QtGui, QtWidgets                       # noqa: E402

from datum.core import constraints3d, kernel                       # noqa: E402
from datum.core.constraints3d import INSERT, MATE                  # noqa: E402
from datum.core.document import Document                           # noqa: E402
from datum.core.features import (                                  # noqa: E402
    ExtrudeFeature, HoleFeature, PrimitiveFeature, SketchFeature,
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


def settle(ms=700):
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


def make_plate(path):
    """A plate with two through holes."""
    doc = Document()
    outline = SketchFeature()
    outline.sketch = Sketch(STANDARD_PLANES["XY"], "Outline")
    outline.sketch.add_rectangle((0, 0), (90, 50))
    doc.add_feature(outline)

    extrude = ExtrudeFeature()
    extrude.distance = "10"
    extrude.profiles.add(outline.id, (45.0, 25.0))
    doc.add_feature(extrude)

    centres = SketchFeature()
    centres.sketch = Sketch(STANDARD_PLANES["XY"], "Hole Centres")
    centres.sketch.add_circle((22, 25), 6)
    centres.sketch.add_circle((68, 25), 6)
    doc.add_feature(centres)

    hole = HoleFeature()
    hole.sketch_id = centres.id
    hole.diameter = "12"
    hole.depth = "40"
    hole.through = True
    doc.add_feature(hole)

    doc.rebuild()
    return doc.save(path)


def make_pin(path):
    doc = Document()
    pin = PrimitiveFeature()
    pin.kind = "cylinder"
    pin.a, pin.b, pin.c = "6", "34", "0"
    doc.add_feature(pin)
    doc.rebuild()
    return doc.save(path)


def circular_edge(shape, want_top):
    """The rim of a round face, picked from the top or the bottom."""
    best, best_z = None, None
    for edge in kernel.edges(shape):
        frame = constraints3d.frame_from_shape(edge)
        if frame is None or frame.kind != "circle":
            continue
        z = frame.origin[2]
        if best is None or (z > best_z if want_top else z < best_z):
            best, best_z = edge, z
    return best


def go():
    plate_path = make_plate(os.path.join(OUT, "Plate"))
    pin_path = make_pin(os.path.join(OUT, "Pin"))

    win.new_assembly(prompt=False)
    pump()
    shot("assembly_empty")

    doc = win.assembly
    doc.path = os.path.join(OUT, "Rig.adat")
    for path in (plate_path, pin_path, pin_path):
        doc.place(path)
    doc.rebuild()
    win.assembly_ui._spread(doc.occurrences[1:])
    win.assembly_ui.rebuild(keep_camera=False)
    win.viewport.set_view("iso")
    settle(400)
    shot("assembly_placed")

    plate, pin_a, pin_b = doc.occurrences

    # the constraint dialog, mid-pick
    win.assembly_ui.constrain(INSERT)
    pump()
    dialog = win.assembly_ui.dialog
    dialog.move(win.mapToGlobal(QtCore.QPoint(win.width() - 430, 150)))

    hole_rims = sorted(
        (e for e in kernel.edges(plate.shape)
         if constraints3d.frame_from_shape(e)
         and constraints3d.frame_from_shape(e).kind == "circle"
         and abs(constraints3d.frame_from_shape(e).radius - 6.0) < 0.01),
        key=lambda e: (round(kernel.shape_centre(e)[2], 3),
                       kernel.shape_centre(e)[0]))
    top_rims = [e for e in hole_rims if kernel.shape_centre(e)[2] > 9.0]

    dialog.constraint.a = doc.attach(plate, "edge", top_rims[0])
    dialog.constraint.b = doc.attach(pin_a, "edge",
                                     circular_edge(pin_a.shape, False))
    dialog._sync_fields()
    dialog._changed()
    pump()
    shot("assembly_constraint", dialog)
    dialog.commit()
    pump()

    # the second pin, so the tree has something to show
    win.assembly_ui.constrain(INSERT)
    pump()
    dialog = win.assembly_ui.dialog
    dialog.constraint.a = doc.attach(plate, "edge", top_rims[1])
    dialog.constraint.b = doc.attach(pin_b, "edge",
                                     circular_edge(pin_b.shape, False))
    dialog._sync_fields()
    dialog.offset.set_text("8")
    dialog._changed()
    pump()
    dialog.commit()
    pump()

    win.assembly_ui.browser.expandAll()
    win.viewport.set_view("iso")
    win.viewport.fit_all()
    settle(500)
    shot("assembly_solved")

    print("DONE  -  %s" % win.assembly.last_report.message)
    app.quit()


QtCore.QTimer.singleShot(1500, go)
sys.exit(app.exec())
