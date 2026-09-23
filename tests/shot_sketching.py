"""Screenshot the sketcher: heads-up input, colours and dimensions."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402,F401


from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

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
ed = win.editor

_n = [0]


def pump(n=4):
    for _ in range(n):
        app.processEvents()


def shot(name, popups=()):
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
        for w in popups:
            if w is not None and w.isVisible():
                p.drawPixmap(win.mapFromGlobal(w.mapToGlobal(
                    QtCore.QPoint(0, 0))), w.grab())
        p.end()
    chrome.save(os.path.join(OUT, "%02d_%s.png" % (_n[0], name)))
    try:
        os.remove(vp_path)
    except OSError:
        pass
    print("shot:", name)


def move(u, v):
    ed._on_move(u, v, QtCore.Qt.NoModifier)
    pump(1)


def click(u, v):
    move(u, v)
    ed._on_click(u, v, QtCore.Qt.NoModifier)
    pump(1)


def place_popups():
    """Put the floating inputs somewhere predictable for the screenshot."""
    centre = win.viewport.mapToGlobal(
        QtCore.QPoint(win.viewport.width() // 2, win.viewport.height() // 2))
    if ed.live.isVisible():
        ed.live.move(centre + QtCore.QPoint(40, -30))
    if ed.value_popup.isVisible():
        ed.value_popup.move(centre + QtCore.QPoint(40, 10))
    pump(1)


def go():
    win.new_document(prompt=False)
    win.start_sketch_on_plane("XY")
    win.viewport.set_span(160)
    pump()

    ed.set_tool("rect")
    click(-30, -20)
    move(30, 20)
    for ch in "70":
        ed._on_key(ord(ch), ch)
    ed._on_key(QtCore.Qt.Key_Tab, "\t")
    for ch in "45":
        ed._on_key(ord(ch), ch)
    place_popups()
    shot("live_input_rectangle", (ed.live,))

    ed._on_key(QtCore.Qt.Key_Return, "\r")
    pump()
    shot("rectangle_dimensioned")

    ed.set_tool("circle")
    click(0, 0)
    move(15, 0)
    place_popups()
    shot("live_input_circle", (ed.live,))
    for ch in "26":
        ed._on_key(ord(ch), ch)
    ed._on_key(QtCore.Qt.Key_Return, "\r")
    pump()

    # place a dimension by hand to show the perpendicular drag + popup
    ed.set_tool("dimension")
    lines = [e for e in ed.sketch.entities.values() if e.kind == "line"]
    line = lines[0]
    a = ed.sketch.points[line.points[0]]
    b = ed.sketch.points[line.points[1]]
    click((a.x + b.x) / 2.0, (a.y + b.y) / 2.0)
    move((a.x + b.x) / 2.0, a.y - 18.0)
    shot("dimension_placing")

    ed._on_click((a.x + b.x) / 2.0, a.y - 18.0, QtCore.Qt.NoModifier)
    pump()
    place_popups()
    shot("dimension_value_popup", (ed.value_popup,))
    ed.value_popup.field.setText("70")
    ed.value_popup._accept()
    pump()
    shot("sketch_constrained")

    print("DONE")
    app.quit()


QtCore.QTimer.singleShot(1400, go)
sys.exit(app.exec())
