"""The rest of the pictures on datum.iiteg.com, in the current look.

Usage:  python tests/shot_site_more.py [out_dir [name ...]]

Run one picture per process for clean tabs: sketch, inplace, dlogic, cam.

shot_site.py takes the assembly and the part; this takes the sketch, a
part edited in place, dLogic and the CAM sheet, from the samples in this
repository, opened in the real window.  Dialogs and the heads-up boxes are
separate windows, so they are painted onto the picture where they sit.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import harness  # noqa: E402,F401

from PySide6 import QtCore, QtGui, QtWidgets                   # noqa: E402

from datum.ui import icons                                     # noqa: E402
from datum.ui.main_window import MainWindow                    # noqa: E402
from datum.ui.theme import stylesheet                          # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLES = os.path.join(ROOT, "examples")
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "site", "img")
os.makedirs(OUT, exist_ok=True)

app = QtWidgets.QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(stylesheet())
app.setWindowIcon(icons.app_icon())
win = MainWindow()
win.resize(1600, 958)
win.show()


def pump(n=4):
    for _ in range(n):
        app.processEvents()


def settle(ms=600):
    timer = QtCore.QElapsedTimer()
    timer.start()
    while timer.elapsed() < ms:
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 20)


def shot(name, *popups, crop=None):
    pump()
    win.viewport.redraw()
    pump()
    chrome = win.grab()
    scratch = os.path.join(OUT, "_vp.png")
    win.viewport.grab_image(scratch)
    image = QtGui.QImage(scratch)
    painter = QtGui.QPainter(chrome)
    if not image.isNull():
        origin = win.viewport.mapTo(win, QtCore.QPoint(0, 0))
        painter.drawImage(QtCore.QRect(origin.x(), origin.y(),
                                       win.viewport.width(),
                                       win.viewport.height()), image)
    for popup in popups:
        if popup is None or not popup.isVisible():
            continue
        where = win.mapFromGlobal(popup.mapToGlobal(QtCore.QPoint(0, 0)))
        painter.drawPixmap(where, popup.grab())
    painter.end()
    try:
        os.remove(scratch)
    except OSError:
        pass
    if crop is not None:
        chrome = chrome.copy(crop)
    path = os.path.join(OUT, name + ".png")
    chrome.save(path)
    print("%-14s %d x %d" % (name, chrome.width(), chrome.height()))


def look(view="iso"):
    win.viewport.set_view(view)
    settle(450)
    win.viewport.fit_all()
    settle(450)


def sketch_picture():
    """A plate with a hole, fully constrained, with the offset tool out."""
    win.new_document(prompt=False)
    win.start_sketch_on_plane("XY")
    settle(1200)
    ed = win.editor
    s = ed.sketch
    origin = s.ensure_origin_point()
    bottom, right, top, left = s.add_rectangle((0.0, 0.0), (70.0, 45.0))
    a, b = s.entities[bottom].points
    d = s.entities[left].points[0]
    s.add_constraint("coincident", points=[origin, a])
    hole = s.add_circle((35.0, 22.5), 13.0)
    centre = s.entities[hole].points[0]
    width = s.add_constraint("distance_x", points=[a, b], value=70.0)
    s.constraints[width].label_offset = (0.0, -9.0)
    height = s.add_constraint("distance_y", points=[a, d], value=45.0)
    s.constraints[height].label_offset = (-9.0, 0.0)
    across = s.add_constraint("diameter", entities=[hole], value=26.0)
    s.constraints[across].label_offset = (13.0, 13.0)
    w_name = s.constraints[width].name
    h_name = s.constraints[height].name
    s.add_constraint("distance_x", points=[a, centre], value=35.0,
                     expression="%s/2" % w_name)
    s.add_constraint("distance_y", points=[a, centre], value=22.5,
                     expression="%s/2" % h_name)
    # the hole's place, stacked outside the plate's own two
    for cid, c in s.constraints.items():
        if c.kind in ("distance_x", "distance_y") and c.expression:
            c.label_offset = ((0.0, -27.0) if c.kind == "distance_x"
                              else (-33.0, 0.0))
    ed.solve()
    ed.render()
    win.viewport.fit_all()
    win.viewport.view.SetZoom(0.72)
    win.viewport.refresh_grid()
    settle(500)
    # the offset tool, pulled in from the plate's edge for a lip
    ed.set_tool("offset")
    ed._on_click(35.0, 45.0, QtCore.Qt.NoModifier)
    cursor = (58.0, 41.0)
    ed._on_move(cursor[0], cursor[1], QtCore.Qt.NoModifier)
    settle(300)
    # the heads-up box sits by the cursor, wherever the real mouse is
    vp = win.viewport
    x, y = vp.project(s.plane.to_3d(*cursor))
    ed.live.track([abs(s.offset_distance(ed._offset_chain[0], cursor))],
                  vp.mapToGlobal(QtCore.QPoint(x, y)))
    settle(200)
    shot("sketch", ed.live)
    ed.escape()
    ed.escape()
    win.finish_sketch()
    settle(300)


def in_place_picture():
    win.open_assembly(os.path.join(EXAMPLES, "excavator", "Excavator.adat"))
    settle(1600)
    boom = next(o for o in win.assembly.occurrences
                if o.label.startswith("Boom"))
    look("iso")
    win.edit_in_place(boom.id)
    settle(1500)
    win.assembly_ui.browser.expandAll()
    settle(300)
    shot("inplace")
    win.finish_in_place()
    settle(800)


def dlogic_picture():
    win.open_path(os.path.join(EXAMPLES, "railing", "Stair Railing.pdat"))
    settle(1500)
    # a sample from this repository is code we wrote: run it
    win.document.rules.trusted = True
    win.rebuild()
    settle(800)
    look("iso")
    win.show_rules()
    win.rules_panel.set_document(win.document)
    settle(400)
    # the railing to the left, clear of the dialog on the right
    win.viewport.view.Pan(-380, 0)
    win.viewport.redraw()
    railing = next(f for f in win.document.features
                   if f.name == "Railing")
    win.edit_feature(railing.id)
    settle(1200)
    dialog = win._active_dialog
    if dialog is not None:
        dialog.resize(760, 600)
        dialog.move(win.mapToGlobal(QtCore.QPoint(
            win.width() - dialog.width() - 24, 300)))
        settle(300)
    win.status_message.setText("")
    shot("dlogic", dialog)
    if dialog is not None:
        dialog.cancel()
    settle(300)


def cam_picture():
    win.open_cam(os.path.join(EXAMPLES, "plywood-tote", "Tote Nest.cdat"))
    settle(1500)
    look("top")
    win.status_message.setText("")
    # the sheet, without the tree beside it, as the page has always shown
    left = win.viewport.mapTo(win, QtCore.QPoint(0, 0)).x()
    bottom = win.statusBar().mapTo(win, QtCore.QPoint(0, 0)).y()
    shot("cam", crop=QtCore.QRect(left, 0, win.width() - left, bottom))


PICTURES = {"sketch": sketch_picture, "inplace": in_place_picture,
            "dlogic": dlogic_picture, "cam": cam_picture}


def go():
    for name in (sys.argv[2:] or list(PICTURES)):
        PICTURES[name]()
    print("DONE")
    app.quit()


QtCore.QTimer.singleShot(1500, go)
sys.exit(app.exec())
