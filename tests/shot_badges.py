"""Screenshot the constraint glyphs on a selected line and a selected point."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402,F401


from PySide6 import QtCore, QtGui, QtWidgets                       # noqa: E402

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
ed = win.editor
_n = [0]


def pump(n=4):
    for _ in range(n):
        app.processEvents()


def settle(ms=400):
    t = QtCore.QElapsedTimer()
    t.start()
    while t.elapsed() < ms:
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


def go():
    win.new_document(prompt=False)
    win.start_sketch_on_plane("XY")
    pump()
    ed.snap_grid = False
    s = ed.sketch

    upright = s.add_line((-45.0, -35.0), (-45.0, 35.0))
    across = s.add_line((-45.0, 35.0), (45.0, 35.0))
    leg = s.add_line((45.0, 35.0), (45.0, -25.0))

    s.add_constraint("vertical", entities=[upright])
    s.add_constraint("horizontal", entities=[across])
    s.add_constraint("perpendicular", entities=[upright, across])
    s.add_constraint("coincident",
                     points=[s.entities[upright].points[1],
                             s.entities[across].points[0]])
    s.add_constraint("coincident",
                     points=[s.entities[across].points[1],
                             s.entities[leg].points[0]])
    s.add_constraint("parallel", entities=[upright, leg])
    ed.solve()

    # sketch geometry is overlay, which Fit All deliberately ignores, so
    # frame it by span instead
    win.viewport.look_at_plane(s.plane, fit=False, animate=False)
    win.viewport.set_span(190.0)
    win.viewport.refresh_grid()
    ed.selected_entities = [upright]
    ed.render()
    settle(400)
    print("points:", {i: (round(q.x, 1), round(q.y, 1))
                      for i, q in s.points.items()})
    print("selected:", ed.selected_entities, "badges:", len(ed._badges))
    print("badge spots:", [(b[2], (round(b[1][0], 1), round(b[1][1], 1)))
                           for b in ed._badges])
    print("pixel scale:", round(win.viewport.pixel_scale(), 4))
    shot("line_selected")

    ed.clear_selection()
    ed.selected_points = [s.entities[across].points[0]]
    ed.render()
    settle(300)
    shot("point_selected")

    # hover one of the glyphs so it lights up
    ed.clear_selection()
    ed.selected_entities = [across]
    ed.render()
    pump()
    if ed._badges:
        spot = ed._badges[0][1]
        ed._on_move(spot[0], spot[1], QtCore.Qt.NoModifier)
        pump()
        ed._tool_select(spot, QtCore.Qt.NoModifier)
        pump()
    settle(300)
    shot("glyph_picked")

    print("DONE - %d glyphs, picked=%s"
          % (len(ed._badges), ed.selected_constraint))
    app.quit()


QtCore.QTimer.singleShot(1500, go)
sys.exit(app.exec())
