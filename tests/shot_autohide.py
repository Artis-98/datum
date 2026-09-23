"""Screenshot the origin planes standing down, and a pickable work plane."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402,F401


from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from datum.core import kernel  # noqa: E402
from datum.core.features import ExtrudeFeature, WorkPlaneFeature  # noqa: E402
from datum.core.naming import RefSet  # noqa: E402
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


def shot(name, extra=None):
    pump()
    win.viewport.redraw()
    pump()
    _n[0] += 1
    vp_path = os.path.join(OUT, "_vp.png")
    win.viewport.grab_image(vp_path)
    chrome = win.grab()
    vp = QtGui.QImage(vp_path)
    if not vp.isNull():
        origin = win.viewport.mapTo(win, QtCore.QPoint(0, 0))
        p = QtGui.QPainter(chrome)
        p.drawImage(QtCore.QRect(origin.x(), origin.y(), win.viewport.width(),
                                 win.viewport.height()), vp)
        if extra is not None:
            p.drawPixmap(win.mapFromGlobal(extra.mapToGlobal(
                QtCore.QPoint(0, 0))), extra.grab())
        p.end()
    chrome.save(os.path.join(OUT, "%02d_%s.png" % (_n[0], name)))
    try:
        os.remove(vp_path)
    except OSError:
        pass
    print("shot:", name)


def click(u, v):
    win.editor._on_move(u, v, QtCore.Qt.NoModifier)
    win.editor._on_click(u, v, QtCore.Qt.NoModifier)
    pump(1)


def go():
    win.new_document(prompt=False)
    win.viewport.set_view("iso")
    shot("01_new_part")

    win.start_sketch_on_plane("XY")
    win.set_sketch_tool("rect")
    click(-35, -25)
    click(35, 25)
    win.finish_sketch()
    pump()
    shot("02_sketch_done_planes_still_on")

    win.new_feature(ExtrudeFeature)
    win.select_all_profiles()
    win._active_dialog.distance.set_text("18")
    win._active_dialog.commit()
    pump()
    win.viewport.set_view("iso")
    shot("03_extruded_planes_stood_down")

    top = max(kernel.faces(win.document.shape),
              key=lambda f: kernel.shape_centre(f)[2])
    refs = RefSet()
    refs.capture_from(win.document.shape, "face", [top])
    wp = WorkPlaneFeature()
    wp.face_ref = refs.refs[0]
    wp.offset = "24"
    win.document.add_feature(wp)
    win.rebuild()
    win.viewport.set_view("iso")
    pump()

    plane = win.document.planes[wp.name]
    px, py = win.viewport.project(plane.to_3d(-20.0, -14.0))
    win.viewport.context.MoveTo(int(px), int(py), win.viewport.view, True)
    shot("04_work_plane_highlighted")

    win.viewport.context.SelectDetected()
    win.viewport.view.Redraw()
    menu = QtWidgets.QMenu(win)
    win._add_plane_menu(menu, wp.name)
    menu.popup(win.viewport.mapToGlobal(QtCore.QPoint(int(px), int(py))))
    pump()
    chrome = win.grab()
    p = QtGui.QPainter(chrome)
    vp_path = os.path.join(OUT, "_vp.png")
    win.viewport.grab_image(vp_path)
    vp = QtGui.QImage(vp_path)
    origin = win.viewport.mapTo(win, QtCore.QPoint(0, 0))
    p.drawImage(QtCore.QRect(origin.x(), origin.y(), win.viewport.width(),
                             win.viewport.height()), vp)
    p.drawPixmap(win.mapFromGlobal(menu.mapToGlobal(QtCore.QPoint(0, 0))),
                 menu.grab())
    p.end()
    chrome.save(os.path.join(OUT, "05_work_plane_menu.png"))
    print("shot: 05_work_plane_menu")
    menu.close()
    try:
        os.remove(vp_path)
    except OSError:
        pass

    print("DONE")
    app.quit()


QtCore.QTimer.singleShot(1400, go)
sys.exit(app.exec())
