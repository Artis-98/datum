"""Drive the real application through a scripted session and screenshot it.

Usage:  python tests/shot_app.py <out_dir> [script]

The Qt chrome and the OCCT viewport are captured separately and composited,
because the viewport is a native child window that Qt's own grab() cannot see.
"""
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402,F401


from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from datum.ui import icons  # noqa: E402
from datum.ui.main_window import MainWindow  # noqa: E402
from datum.ui.theme import stylesheet  # noqa: E402

OUT_DIR = sys.argv[1] if len(sys.argv) > 1 else "."
SCRIPT = sys.argv[2] if len(sys.argv) > 2 else "tour"
os.makedirs(OUT_DIR, exist_ok=True)

app = QtWidgets.QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(stylesheet())
app.setWindowIcon(icons.app_icon())

win = MainWindow()
win.resize(1600, 1000)
win.show()

_shot_index = [0]


def shot(name):
    """Composite the Qt chrome with an OCCT dump of the viewport."""
    app.processEvents()
    win.viewport.redraw()
    app.processEvents()

    _shot_index[0] += 1
    path = os.path.join(OUT_DIR, "%02d_%s.png" % (_shot_index[0], name))
    vp_path = os.path.join(OUT_DIR, "_vp.png")
    win.viewport.grab_image(vp_path)

    chrome = win.grab()
    vp = QtGui.QImage(vp_path)
    if not vp.isNull():
        origin = win.viewport.mapTo(win, QtCore.QPoint(0, 0))
        painter = QtGui.QPainter(chrome)
        painter.drawImage(QtCore.QRect(origin.x(), origin.y(),
                                       win.viewport.width(),
                                       win.viewport.height()), vp)
        painter.end()
    chrome.save(path)
    try:
        os.remove(vp_path)
    except OSError:
        pass
    print("shot:", os.path.basename(path))
    return path


def click_plane(u, v, mods=QtCore.Qt.NoModifier):
    """Feed a sketch-plane click straight into the editor."""
    win.editor._on_move(u, v, mods)
    win.editor._on_click(u, v, mods)
    app.processEvents()


STEPS = []


def step(fn):
    STEPS.append(fn)
    return fn


# --------------------------------------------------------------------------
# the scripted session
# --------------------------------------------------------------------------


@step
def s_empty():
    shot("empty")


@step
def s_start_sketch():
    win.start_sketch_on_plane("XY")
    win.viewport.set_view("top")
    shot("sketch_mode")


@step
def s_draw():
    win.set_sketch_tool("rect")
    click_plane(-45, -30)
    click_plane(45, 30)

    win.set_sketch_tool("circle")
    click_plane(0, 0)
    click_plane(14, 0)

    win.set_sketch_tool("select")
    shot("sketch_drawn")


@step
def s_dimension():
    s = win.editor.sketch
    ids = sorted(s.entities)
    rect_bottom = ids[0]
    pts = s.entities[rect_bottom].points
    s.points[pts[0]].fixed = True
    s.add_constraint("distance_x", points=[pts[0], pts[1]], value=120.0)
    s.constraints[max(s.constraints)].label_offset = (0.0, -14.0)

    left = s.entities[ids[3]].points
    s.add_constraint("distance_y", points=[left[1], left[0]], value=70.0)
    s.constraints[max(s.constraints)].label_offset = (-16.0, 0.0)

    circle = [e for e in s.entities.values() if e.kind == "circle"][0]
    s.add_constraint("diameter", entities=[circle.id], value=30.0)
    s.constraints[max(s.constraints)].label_offset = (10.0, 12.0)

    win.editor.solve()
    win.editor.render()
    shot("sketch_dimensioned")


@step
def s_finish_sketch():
    win.finish_sketch()
    shot("sketch_finished")


@step
def s_extrude():
    from datum.core.features import ExtrudeFeature
    win.new_feature(ExtrudeFeature)
    win.select_all_profiles()
    dlg = win._active_dialog
    dlg.distance.set_text("12")
    app.processEvents()
    shot("extrude_dialog")
    dlg.commit()
    app.processEvents()
    win.viewport.set_view("iso")
    shot("extruded")


@step
def s_fillet():
    from datum.core import kernel
    from datum.core.features import FilletFeature
    from datum.core.naming import RefSet

    win.new_feature(FilletFeature)
    dlg = win._active_dialog
    verticals = [e for e in kernel.edges(win.document.shape)
                 if abs(kernel.edge_length(e) - 12) < 0.01]
    refs = RefSet()
    refs.capture_from(win.document.shape, "edge", verticals)
    dlg.feature.refs = refs
    dlg.picker.set_count(len(refs), "edge")
    dlg.size.set_text("10")
    app.processEvents()
    shot("fillet_dialog")
    dlg.commit()
    app.processEvents()
    shot("filleted")


@step
def s_params():
    win.document.params.add("wall", "3")
    win.document.params.add("boss_d", "20")
    win.rebuild()
    shot("with_params")


@step
def s_shell():
    from datum.core import kernel
    from datum.core.features import ShellFeature
    from datum.core.naming import RefSet

    win.new_feature(ShellFeature)
    dlg = win._active_dialog
    top = max(kernel.faces(win.document.shape),
              key=lambda f: kernel.shape_centre(f)[2])
    refs = RefSet()
    refs.capture_from(win.document.shape, "face", [top])
    dlg.feature.refs = refs
    dlg.picker.set_count(1, "face")
    dlg.thickness.set_text("wall")
    app.processEvents()
    dlg.commit()
    app.processEvents()
    win.viewport.set_view("iso")
    shot("shelled")


@step
def s_holes():
    from datum.core.features import HoleFeature, SketchFeature
    from datum.core.sketch import STANDARD_PLANES, Sketch

    feature = SketchFeature()
    feature.sketch = Sketch(STANDARD_PLANES["XY"], "Mount Holes")
    for p in [(-45, -22), (45, -22), (-45, 22), (45, 22)]:
        feature.sketch.add_circle(p, 2.5)
    win.document.add_feature(feature)
    win.rebuild()

    win.new_feature(HoleFeature)
    dlg = win._active_dialog
    dlg.sketch.setCurrentIndex(dlg.sketch.count() - 1)
    dlg.diameter.set_text("5")
    dlg.through.setChecked(True)
    dlg.flip.setChecked(False)
    app.processEvents()
    dlg.commit()
    app.processEvents()
    shot("holes")


@step
def s_pattern_tab():
    win.ribbon.show_tab("View")
    shot("view_tab")
    win.ribbon.show_tab("Inspect")
    shot("inspect_tab")
    win.ribbon.show_tab("3D Model")


@step
def s_params_dialog():
    from datum.ui.panels import ParametersDialog
    dlg = ParametersDialog(win.document, win)
    dlg.show()
    app.processEvents()
    dlg.grab().save(os.path.join(OUT_DIR, "90_parameters.png"))
    print("shot: 90_parameters.png")
    dlg.close()


@step
def s_save():
    path = os.path.join(OUT_DIR, "demo_part.pdat")
    path = win.document.save(path)
    print("saved:", os.path.basename(path),
          os.path.getsize(path), "bytes")
    step_path = os.path.join(OUT_DIR, "demo_part.step")
    from datum.core import fileio
    fileio.write_step(win.document.shape, step_path)
    print("exported STEP:", os.path.getsize(step_path), "bytes")
    print("REPORT:", win.document.last_report.message)


@step
def s_done():
    print("SCRIPT COMPLETE")
    app.quit()


# --------------------------------------------------------------------------

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
        print("FAILED AT STEP:", fn.__name__)
        app.quit()
        return
    QtCore.QTimer.singleShot(220, run_next)


QtCore.QTimer.singleShot(1400, run_next)
sys.exit(app.exec())
