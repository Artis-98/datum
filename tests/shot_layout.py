"""Screenshot the rearranged ribbon, the plane picker and the File menu."""
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402,F401


from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from datum.core import kernel  # noqa: E402
from datum.core.features import FilletFeature  # noqa: E402
from datum.ui import icons  # noqa: E402
from datum.ui.main_window import MainWindow  # noqa: E402
from datum.ui.theme import stylesheet  # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else "."
os.makedirs(OUT, exist_ok=True)

app = QtWidgets.QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(stylesheet())
app.setWindowIcon(icons.app_icon())

win = MainWindow()
win.resize(1600, 1000)
win.show()

_n = [0]


def shot(name, extra_widget=None):
    app.processEvents()
    win.viewport.redraw()
    app.processEvents()
    _n[0] += 1
    path = os.path.join(OUT, "%02d_%s.png" % (_n[0], name))
    vp_path = os.path.join(OUT, "_vp.png")
    win.viewport.grab_image(vp_path)

    chrome = win.grab()
    vp = QtGui.QImage(vp_path)
    if not vp.isNull():
        origin = win.viewport.mapTo(win, QtCore.QPoint(0, 0))
        p = QtGui.QPainter(chrome)
        p.drawImage(QtCore.QRect(origin.x(), origin.y(), win.viewport.width(),
                                 win.viewport.height()), vp)
        if extra_widget is not None:
            pos = extra_widget.mapTo(win, QtCore.QPoint(0, 0))
            p.drawPixmap(pos, extra_widget.grab())
        p.end()
    chrome.save(path)
    try:
        os.remove(vp_path)
    except OSError:
        pass
    print("shot:", os.path.basename(path))


STEPS = []


def step(fn):
    STEPS.append(fn)
    return fn


@step
def s_model_tab():
    shot("model_tab")


@step
def s_file_menu():
    menu = win.file_menu
    btn = win.ribbon.file_button
    menu.popup(btn.mapToGlobal(QtCore.QPoint(0, btn.height())))
    app.processEvents()
    chrome = win.grab()
    p = QtGui.QPainter(chrome)
    pos = win.mapFromGlobal(menu.mapToGlobal(QtCore.QPoint(0, 0)))
    p.drawPixmap(pos, menu.grab())
    p.end()
    _n[0] += 1
    chrome.save(os.path.join(OUT, "%02d_file_menu.png" % _n[0]))
    print("shot: file_menu")
    menu.close()


@step
def s_plane_picker():
    win.start_sketch()
    shot("plane_picker")


@step
def s_pick_xy():
    win._pending_plane_pick = False
    win.viewport.hide_plane_picker()
    win.start_sketch_on_plane("XY")
    win.set_sketch_tool("rect")
    win.editor._on_move(-40, -25, QtCore.Qt.NoModifier)
    win.editor._on_click(-40, -25, QtCore.Qt.NoModifier)
    win.editor._on_move(40, 25, QtCore.Qt.NoModifier)
    win.editor._on_click(40, 25, QtCore.Qt.NoModifier)
    win.finish_sketch()
    from datum.core.features import ExtrudeFeature
    win.new_feature(ExtrudeFeature)
    win.select_all_profiles()
    win._active_dialog.distance.set_text("18")
    win._active_dialog.commit()
    app.processEvents()
    win.viewport.set_view("iso")
    shot("extruded")


@step
def s_fillet_dialog():
    win.new_feature(FilletFeature)
    dlg = win._active_dialog
    app.processEvents()
    verticals = [e for e in kernel.edges(win.document.shape)
                 if abs(kernel.edge_length(e) - 18) < 0.01]
    for edge in verticals:
        remaining = [edge]
        win.viewport.selected_edges = lambda r=remaining: (
            [r.pop()] if r else [])
        win._on_viewport_selection()
        app.processEvents()
        del win.viewport.selected_edges
    dlg.size.set_text("8")
    dlg.preview()
    app.processEvents()
    dlg.move(win.mapToGlobal(QtCore.QPoint(win.width() - 400, 130)))
    shot("fillet_4_edges", dlg)
    dlg.commit()


@step
def s_rollback():
    win.set_rollback(1)
    shot("rolled_back")
    win.set_rollback(None)


@step
def s_manage():
    win.ribbon.show_tab("Manage")
    shot("manage_tab")
    win.ribbon.show_tab("3D Model")


@step
def s_viewcube():
    win.viewport.set_view("front")
    shot("viewcube_front")


@step
def s_done():
    print("LAYOUT SHOTS COMPLETE")
    app.quit()


_i = [0]


def run_next():
    if _i[0] >= len(STEPS):
        app.quit()
        return
    fn = STEPS[_i[0]]
    _i[0] += 1
    try:
        fn()
    except Exception:
        traceback.print_exc()
        print("FAILED AT:", fn.__name__)
        app.quit()
        return
    QtCore.QTimer.singleShot(250, run_next)


QtCore.QTimer.singleShot(1400, run_next)
sys.exit(app.exec())
