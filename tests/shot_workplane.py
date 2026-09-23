"""Screenshot the work-plane pull-off gesture."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402,F401


from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from datum.core import kernel  # noqa: E402
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


def go():
    win.new_document(prompt=False)
    win.new_primitive("box")
    d = win._active_dialog
    d.a.set_text("70")
    d.b.set_text("50")
    d.c.set_text("22")
    d.commit()
    pump()
    win.viewport.set_view("iso")
    pump()

    win.start_work_plane()
    shot("pick_a_face")

    face = max(kernel.faces(win.document.shape),
               key=lambda f: kernel.shape_centre(f)[2])
    win.viewport.selected_faces = lambda f=face: [f]
    win._plane_tool_pressed()
    del win.viewport.selected_faces
    win._plane_drag_moved(26.0)
    shot("dragging_off")

    win.viewport.axis_drag_finished.emit(26.0)
    pump()
    dlg = win._active_dialog
    dlg.move(win.mapToGlobal(QtCore.QPoint(win.width() - 400, 150)))
    shot("plane_dialog", dlg)
    dlg.commit()
    pump()
    shot("plane_placed")

    print("DONE")
    app.quit()


QtCore.QTimer.singleShot(1400, go)
sys.exit(app.exec())
