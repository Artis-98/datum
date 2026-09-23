"""Screenshot the plywood tote: the parts, the assembly and the nest.

Everything it shows was written by examples/build_tote.py, so this only
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


def shot(name):
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
        p.end()
    try:
        os.remove(vp_path)
    except OSError:
        pass
    chrome.save(os.path.join(OUT, "%02d_%s.png" % (_n[0], name)))
    print("shot:", name)


def look(view="iso"):
    """Point the camera and fit, letting the turn finish first.

    set_view animates, and a fit taken while the model is still swinging
    round frames the pose it was passing through rather than the one asked
    for, which crops the result.
    """
    win.viewport.set_view(view)
    settle(450)
    win.viewport.fit_all()
    settle(450)


def frame(centre, span, view=None):
    """Point the camera at one spot and show ``span`` mm across.

    Zooming about the fitted centre lands on whatever happens to be in the
    middle of the sheet, which for a nest is usually bare stock.
    """
    if view:
        win.viewport.set_view(view)
        settle(250)
    # Pan by pixel and then zoom about the middle.  Moving the camera's
    # look-at point instead - SetAt, or the camera's own SetCenter - leaves
    # the eye where it was, which swings the whole view round to the side.
    px, py = win.viewport.project(centre)
    win.viewport.view.SetCenter(int(px), int(py))
    win.viewport.view.SetSize(float(span))
    settle(450)


def go():
    # ---- the three parts, each on its own tab ---------------------------
    for stem in ("End Panel", "Side Panel", "Base"):
        win.open_path(os.path.join(SRC, stem + ".pdat"))
        settle(400)
        win.browser.expandAll()
        look("iso")
        shot("part_" + stem.lower().replace(" ", "_"))

    # ---- the assembly ---------------------------------------------------
    win.open_assembly(os.path.join(SRC, "Tote.adat"))
    settle(700)
    win.assembly_ui.browser.expandAll()
    look("iso")
    shot("assembly_iso")

    look("front")
    shot("assembly_front")

    # in close on the corner where a side panel's tabs come through an end
    look("iso")
    frame((296.0, 26.0, 72.0), 250.0)
    shot("assembly_joint_detail")

    # ---- the nest -------------------------------------------------------
    win.open_cam(os.path.join(SRC, "Tote Nest.cdat"))
    settle(700)
    win.cam_ui.browser.expandAll()
    look("top")
    shot("cam_nest")

    # the end panel that wrapped onto the second row, where the relief
    # circles at every slot corner are big enough to see
    frame((103.0, 332.0, 0.0), 260.0)
    shot("cam_end_panel_paths")
    frame((49.0, 285.0, 0.0), 90.0)
    shot("cam_relief_detail")

    report = win.cam.last_report
    print("DONE  -  %s" % report.message)
    print("cuts: %d, errors: %s, warnings: %s"
          % (len(report.toolpath.cuts), report.errors, report.warnings))
    app.quit()


QtCore.QTimer.singleShot(1500, go)
sys.exit(app.exec())
