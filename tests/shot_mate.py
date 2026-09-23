"""Screenshot the Mate constraint: Type, Solution, and the normal arrows."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402,F401

os.environ["DATUM_SETTINGS_ORG"] = "IITEG-tests"

from PySide6 import QtCore, QtGui, QtWidgets                       # noqa: E402

from datum.core.constraints3d import FLUSH, MATE                   # noqa: E402
from datum.ui import icons                                         # noqa: E402
from datum.ui.main_window import MainWindow                        # noqa: E402
from datum.ui.theme import stylesheet                              # noqa: E402

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "examples", "plywood-tote")
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


def stub_pick(pairs):
    doc = win.assembly
    located = []
    for occurrence_id, shape in pairs:
        occurrence = doc.occurrence(occurrence_id)
        located.append((occurrence_id,
                        shape.Moved(occurrence.placement.location())))
    win.viewport.selected_component_shapes = lambda: located


def go():
    from datum.core import kernel

    # a fresh assembly with two of the tote's panels, unconstrained
    win.new_assembly(prompt=False)
    settle(400)
    doc = win.assembly
    for stem in ("Base", "Side Panel"):
        doc.place(os.path.join(SRC, stem + ".pdat"), stem)
    doc.occurrences[1].placement.position = [40.0, 240.0, 90.0]
    doc.occurrences[1].placement.rotation = [0.9, 0.35, 0.2]
    doc.occurrences[1].grounded = False
    win.assembly_ui.rebuild(keep_camera=False)
    win.viewport.set_view("iso")
    settle(400)
    win.viewport.fit_all()
    settle(400)
    shot("before")

    base, side = doc.occurrences
    win.assembly_ui.constrain(MATE)
    settle(400)
    dialog = win.assembly_ui.dialog
    dialog.move(win.mapToGlobal(QtCore.QPoint(win.width() - 430, 150)))

    top = max(kernel.faces(base.shape), key=lambda f: kernel.shape_centre(f)[2])
    stub_pick([(base.id, top)])
    dialog.on_selection()
    settle(500)
    win.viewport.fit_all()
    settle(400)
    shot("first_pick_arrow", dialog)

    under = min(kernel.faces(side.shape),
                key=lambda f: kernel.shape_centre(f)[2])
    stub_pick([(side.id, under)])
    dialog.on_selection()
    settle(600)
    win.viewport.fit_all()
    settle(400)
    shot("mate_solution", dialog)

    dialog.solution.set_value(FLUSH)
    dialog._solution_changed(FLUSH)
    settle(600)
    win.viewport.fit_all()
    settle(400)
    shot("flush_solution", dialog)

    dialog.solution.set_value(MATE)
    dialog._solution_changed(MATE)
    dialog.offset.set_text("25")
    settle(600)
    win.viewport.fit_all()
    settle(400)
    shot("mate_with_offset", dialog)

    print("DONE  -  %s" % win.assembly.last_report.message)
    app.quit()


QtCore.QTimer.singleShot(1500, go)
sys.exit(app.exec())
