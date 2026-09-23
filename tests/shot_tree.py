"""Screenshot the design tree with nested and shared sketches."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402,F401


from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from datum.core.features import ExtrudeFeature, HoleFeature, SketchFeature  # noqa: E402
from datum.core.sketch import STANDARD_PLANES, Sketch  # noqa: E402
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


def pump(n=4):
    for _ in range(n):
        app.processEvents()


def add_sketch(name, build):
    f = SketchFeature()
    f.name = name
    f.sketch = Sketch(STANDARD_PLANES["XY"], name)
    build(f.sketch)
    win.document.add_feature(f)
    f.sketch.name = f.name
    win.rebuild()
    return f


def build():
    win.new_document(prompt=False)
    add_sketch("Plate Outline", lambda s: s.add_rectangle((0, 0), (90, 60)))
    win.new_feature(ExtrudeFeature)
    win.select_all_profiles()
    win._active_dialog.distance.set_text("12")
    win._active_dialog.commit()
    pump()

    holes = add_sketch("Bolt Circle", lambda s: [
        s.add_circle(p, 3) for p in ((12, 12), (78, 12), (12, 48), (78, 48))])
    win.new_feature(HoleFeature)
    d = win._active_dialog
    d.sketch.setCurrentIndex(d.sketch.findData(holes.id))
    d.diameter.set_text("6")
    d.commit()
    pump()

    boss = add_sketch("Boss Profile", lambda s: s.add_circle((45, 30), 14))
    win.new_feature(ExtrudeFeature)
    win.select_all_profiles()
    d = win._active_dialog
    d.sketch.setCurrentIndex(d.sketch.findData(boss.id))
    d.distance.set_text("8")
    d.operation.set_value("join")
    d.commit()
    pump()

    # share one sketch so the tree shows both states side by side
    win.toggle_share(boss.id)
    pump()

    root = win.browser.topLevelItem(0)
    for i in range(root.childCount()):
        root.child(i).setExpanded(True)
    win.viewport.set_view("iso")
    pump()


def shot(name):
    pump()
    win.viewport.redraw()
    pump()
    vp_path = os.path.join(OUT, "_vp.png")
    win.viewport.grab_image(vp_path)
    chrome = win.grab()
    vp = QtGui.QImage(vp_path)
    if not vp.isNull():
        origin = win.viewport.mapTo(win, QtCore.QPoint(0, 0))
        p = QtGui.QPainter(chrome)
        p.drawImage(QtCore.QRect(origin.x(), origin.y(), win.viewport.width(),
                                 win.viewport.height()), vp)
        p.end()
    chrome.save(os.path.join(OUT, name))
    try:
        os.remove(vp_path)
    except OSError:
        pass
    # a tight crop of just the browser, so the tree is readable
    dock = win.browser_dock
    pos = dock.mapTo(win, QtCore.QPoint(0, 0))
    chrome.copy(pos.x(), pos.y(), dock.width(), 340).save(
        os.path.join(OUT, "tree_" + name))
    print("shot:", name)


def go():
    build()
    shot("design_tree.png")
    print("DONE")
    app.quit()


QtCore.QTimer.singleShot(1400, go)
sys.exit(app.exec())
