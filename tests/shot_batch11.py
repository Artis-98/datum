"""Screenshot the batch 11 sketching changes.

The heads-up angle beside the length, the small cross a snap shows, and a
visible sketch's dimensions behind a new sketch.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import harness  # noqa: E402,F401

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from datum.core.features import ExtrudeFeature  # noqa: E402
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


def wait(ms=900):
    """Let the camera finish swinging round before a picture is taken."""
    loop = QtCore.QEventLoop()
    QtCore.QTimer.singleShot(ms, loop.quit)
    loop.exec()


def place_popups():
    """Put the floating inputs somewhere predictable for the screenshot."""
    centre = win.viewport.mapToGlobal(
        QtCore.QPoint(win.viewport.width() // 2, win.viewport.height() // 2))
    if ed.live.isVisible():
        ed.live.move(centre + QtCore.QPoint(160, -60))


def move(u, v):
    ed._on_move(u, v, QtCore.Qt.NoModifier)
    pump(1)


def click(u, v):
    move(u, v)
    ed._on_click(u, v, QtCore.Qt.NoModifier)
    pump(1)


def keys(text):
    for ch in text:
        if ch == "\t":
            ed._on_key(QtCore.Qt.Key_Tab, "")
        elif ch == "\n":
            ed._on_key(QtCore.Qt.Key_Return, "")
        else:
            ed._on_key(ord(ch.upper()), ch)
    pump(1)


win.new_document(prompt=False)
pump()
win.start_sketch_on_plane("XY")
wait()
ed.snap_grid = False

# a rectangle with typed sizes, so it has dimensions to show later
ed.set_tool("rect")
click(0.0, 0.0)
move(60.0, 40.0)
keys("60\t40\n")
ed.set_tool("line")
click(60.0, 0.0)
move(95.0, 22.0)
place_popups()
shot("line_angle_live", popups=(ed.live,))
keys("\t32\n")
ed.escape()
ed.escape()
pump()

# the cross on a midpoint snap, starting a line on the top edge's middle
ed.set_tool("line")
move(30.4, 40.3)
shot("midpoint_cross")
ed.escape()
win.finish_sketch()
pump(10)

win.new_feature(ExtrudeFeature)
pump()
dialog = win._active_dialog
dialog.distance.set_text("10")
win.select_all_profiles()
pump()
dialog.commit()
pump(10)
first = win.document.sketch_features()[0]
win.browser.toggle_sketch_visibility(first.id)
pump(10)
win.viewport.set_view("iso")
wait()
shot("visible_sketch_dimensions")

win.start_sketch_on_plane("XY")
wait()
shot("new_sketch_with_reference")
win.finish_sketch()
pump()
