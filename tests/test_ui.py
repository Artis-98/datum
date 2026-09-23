"""Drive every UI path that can crash, and assert the model stays sane."""
import math
import os
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402,F401


from PySide6 import QtCore, QtWidgets  # noqa: E402

from datum.core import kernel  # noqa: E402
from datum.core.features import (  # noqa: E402
    ChamferFeature, ExtrudeFeature, FilletFeature, HoleFeature, MirrorFeature,
    MoveFeature, PatternFeature, PrimitiveFeature, RevolveFeature,
    ShellFeature, SketchFeature, WorkPlaneFeature,
)
from datum.core.naming import RefSet  # noqa: E402
from datum.ui.main_window import MainWindow  # noqa: E402
from datum.ui.panels import MeasureDialog, ParametersDialog  # noqa: E402
from datum.ui.theme import stylesheet  # noqa: E402

FAILS = []
app = QtWidgets.QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(stylesheet())

win = MainWindow()
win.resize(1400, 880)
win.show()
app.processEvents()


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def pump(n=3):
    for _ in range(n):
        app.processEvents()


def click(u, v, mods=QtCore.Qt.NoModifier):
    win.editor._on_move(u, v, mods)
    win.editor._on_click(u, v, mods)
    pump(1)


def volume():
    return kernel.volume(win.document.shape) if win.document.shape else 0.0


# ==========================================================================
print("sketch tools")
win.new_document(prompt=False)
win.start_sketch_on_plane("XY")
check("sketch mode active", win.editor.active)
check("sketch ribbon tab shown", win.ribbon.current_tab() == "Sketch")

win.set_sketch_tool("line")
click(0, 0)
click(40, 0)
click(40, 30)
click(0, 30)
click(0, 0)
sk = win.editor.sketch
check("line chain made 4 lines",
      len([e for e in sk.entities.values() if e.kind == "line"]) == 4,
      len(sk.entities))
check("chain welded closed", len(sk.points) == 4, len(sk.points))
check("auto H/V constraints added", len(sk.constraints) >= 4,
      len(sk.constraints))

win.editor.escape()
win.set_sketch_tool("circle")
click(20, 15)
click(26, 15)
check("circle created",
      any(e.kind == "circle" for e in sk.entities.values()))

win.set_sketch_tool("arc")
click(80, 0)
click(90, 0)
click(80, 10)
check("arc created", any(e.kind == "arc" for e in sk.entities.values()))

win.set_sketch_tool("polygon")
click(120, 0)
click(130, 0)
check("polygon made 6 lines",
      len([e for e in sk.entities.values() if e.kind == "line"]) == 10)

win.set_sketch_tool("slot")
click(160, 0)
click(190, 0)
click(175, 6)
check("slot made 2 lines + 2 arcs",
      len([e for e in sk.entities.values() if e.kind == "arc"]) == 3)

win.set_sketch_tool("spline")
click(0, 60)
click(20, 80)
click(40, 55)
win.editor.finish_spline()
check("spline created", any(e.kind == "spline" for e in sk.entities.values()))

win.set_sketch_tool("point")
click(-20, -20)
check("point created", len(sk.points) > 4)

print("sketch selection, constraints and dragging")
win.set_sketch_tool("select")
line_ids = [e.id for e in sk.entities.values() if e.kind == "line"][:2]
win.editor.selected_entities = list(line_ids)
before = len(sk.constraints)
win.editor.apply_constraint("equal")
check("equal constraint applied", len(sk.constraints) == before + 1)

win.editor.selected_entities = [line_ids[0]]
win.editor.toggle_construction()
check("construction toggled", sk.entities[line_ids[0]].construction)
win.editor.toggle_construction()

circle = [e for e in sk.entities.values() if e.kind == "circle"][0]
win.editor.selected_entities = [circle.id]
win.editor.selected_points = []
before_r = circle.radius
sk.add_constraint("radius", entities=[circle.id], value=9.0)
win.editor.solve()
check("radius constraint drives the circle", abs(circle.radius - 9.0) < 1e-4,
      circle.radius)

# drag a free point and confirm the solver keeps the sketch consistent
free = [p for p in sk.points.values() if not p.fixed][0]
win.editor._on_drag_start(free.x, free.y)
win.editor._on_drag_move(free.x + 5, free.y + 5)
win.editor._on_drag_end()
check("drag did not break the sketch",
      not sk.solve_message.startswith("over"), sk.solve_message)

print("sketch trim and 2D fillet")
n_before = len(sk.entities)
trim_target = [e.id for e in sk.entities.values()
               if e.kind == "line" and not e.construction][-1]
win.editor._trim_entity(trim_target, (0, 0))
check("trim removed or split the entity", len(sk.entities) != n_before
      or trim_target not in sk.entities)

win.editor.escape()
win.set_sketch_tool("select")
win.finish_sketch()
check("left sketch mode", not win.editor.active)
check("back on the model tab", win.ribbon.current_tab() == "3D Model")

# ==========================================================================
print("features through their dialogs")
win.new_document(prompt=False)
win.start_sketch_on_plane("XY")
win.set_sketch_tool("rect")
click(0, 0)
click(60, 40)
win.finish_sketch()

win.new_feature(ExtrudeFeature)
win.select_all_profiles()
dlg = win._active_dialog
check("extrude dialog opened", dlg is not None)
dlg.distance.set_text("10")
pump()
check("extrude preview built a body", win.document.shape is not None)
dlg.commit()
pump()
check("extrude volume", abs(volume() - 60 * 40 * 10) < 1.0, volume())

win.new_feature(FilletFeature)
dlg = win._active_dialog
verticals = [e for e in kernel.edges(win.document.shape)
             if abs(kernel.edge_length(e) - 10) < 0.01]
refs = RefSet()
refs.capture_from(win.document.shape, "edge", verticals)
dlg.feature.refs = refs
dlg.size.set_text("5")
dlg.preview()
pump()
check("fillet previewed", volume() < 60 * 40 * 10 - 10, volume())
dlg.commit()
pump()

after_fillet = volume()
win.new_feature(ChamferFeature)
dlg = win._active_dialog
dlg.all_edges.setChecked(True)
dlg.size.set_text("1")
dlg.preview()
pump()
ok_chamfer = dlg.ok_button.isEnabled()
dlg.cancel()
pump()
check("chamfer-all previewed then cancelled cleanly",
      abs(volume() - after_fillet) < 1e-6, volume())
check("chamfer preview was accepted by the kernel", ok_chamfer)

print("cancel really removes a new feature")
count_before = len(win.document.features)
win.new_feature(MoveFeature)
win._active_dialog.cancel()
pump()
check("cancel removed the new feature",
      len(win.document.features) == count_before,
      "%d vs %d" % (len(win.document.features), count_before))

print("primitives, pattern and mirror")
win.new_primitive("cylinder")
dlg = win._active_dialog
dlg.a.set_text("6")
dlg.b.set_text("30")
dlg.ox.set_text("15")
dlg.oy.set_text("20")
dlg.oz.set_text("10")
dlg.operation.set_value("join")
dlg.commit()
pump()
boss_volume = volume()
check("boss joined on", boss_volume > after_fillet, boss_volume)

cylinder_id = win.document.features[-1].id
win.new_feature(PatternFeature)
dlg = win._active_dialog
dlg.mode.setCurrentIndex(0)
for i in range(dlg.parents.count()):
    item = dlg.parents.item(i)
    if item.data(QtCore.Qt.UserRole) == cylinder_id:
        item.setCheckState(QtCore.Qt.Checked)
dlg.count1.set_text("3")
dlg.spacing1.set_text("15")
dlg.preview()
pump()
patterned = volume()
check("pattern added two more bosses", patterned > boss_volume, patterned)
dlg.commit()
pump()

win.new_feature(MirrorFeature)
dlg = win._active_dialog
dlg.scope.setCurrentIndex(0)
dlg.plane.setCurrentIndex(dlg.plane.findData("YZ"))
dlg.preview()
pump()
check("mirror produced a bigger body", volume() > patterned, volume())
dlg.commit()
pump()

print("revolve on its own document")
win.new_document(prompt=False)
win.start_sketch_on_plane("XZ")
win.set_sketch_tool("rect")
click(10, 0)
click(20, 30)
win.finish_sketch()
win.new_feature(RevolveFeature)
win.select_all_profiles()
dlg = win._active_dialog
dlg.axis.setCurrentIndex(dlg.axis.findData("Y"))
dlg.angle.set_text("270")
dlg.preview()
pump()
expect = math.pi * (400 - 100) * 30 * 0.75
check("revolve 270 deg", abs(volume() - expect) < 8.0,
      "%.1f vs %.1f" % (volume(), expect))
dlg.commit()
pump()

print("work plane and a sketch on it")
win.new_feature(WorkPlaneFeature)
dlg = win._active_dialog
dlg.base.setCurrentIndex(dlg.base.findData("XY"))
dlg.offset.set_text("40")
dlg.commit()
pump()
check("work plane registered",
      any(name not in ("XY", "XZ", "YZ") for name in win.document.planes),
      list(win.document.planes))

print("hole with counterbore")
win.new_document(prompt=False)
win.new_primitive("box")
dlg = win._active_dialog
dlg.a.set_text("60")
dlg.b.set_text("60")
dlg.c.set_text("20")
dlg.commit()
pump()
solid_volume = volume()

feature = SketchFeature()
from datum.core.sketch import STANDARD_PLANES, Sketch
feature.sketch = Sketch(STANDARD_PLANES["XY"], "Holes")
feature.sketch.add_circle((30, 30), 4)
win.document.add_feature(feature)
win.rebuild()

win.new_feature(HoleFeature)
dlg = win._active_dialog
dlg.sketch.setCurrentIndex(dlg.sketch.count() - 1)
dlg.kind.setCurrentIndex(dlg.kind.findData("counterbore"))
dlg.diameter.set_text("6")
dlg.cb_diameter.set_text("12")
dlg.cb_depth.set_text("5")
dlg.through.setChecked(True)
dlg.flip.setChecked(False)
dlg.preview()
pump()
removed = solid_volume - volume()
expect = math.pi * 9 * 20 + math.pi * 36 * 5 - math.pi * 9 * 5
check("counterbored hole removed the right volume",
      abs(removed - expect) < 3.0, "%.1f vs %.1f" % (removed, expect))
dlg.commit()
pump()

print("shell, suppress, rollback, undo")
win.new_feature(ShellFeature)
dlg = win._active_dialog
top = max(kernel.faces(win.document.shape),
          key=lambda f: kernel.shape_centre(f)[2])
refs = RefSet()
refs.capture_from(win.document.shape, "face", [top])
dlg.feature.refs = refs
dlg.thickness.set_text("2")
dlg.preview()
pump()
shell_ok = dlg.ok_button.isEnabled()
check("shell previewed", shell_ok, dlg.status.text())
if shell_ok:
    dlg.commit()
else:
    dlg.cancel()
pump()
shelled = volume()

hole_feature = [f for f in win.document.features
                if isinstance(f, HoleFeature)][0]
win.toggle_suppress(hole_feature.id)
pump()
suppressed = volume()
# The shell wraps a wall around the bore, so dropping the hole makes the part
# *lighter*, not heavier - all that matters is that the tree rebuilt cleanly.
check("suppressing the hole rebuilt the tree",
      win.document.last_report.ok and abs(suppressed - shelled) > 1.0,
      "%.2f vs %.2f  (%s)" % (suppressed, shelled,
                              win.document.last_report.message))
check("suppressed hole means a plain shelled box",
      abs(suppressed - (60 * 60 * 20 - 56 * 56 * 18)) < 2.0, suppressed)
win.toggle_suppress(hole_feature.id)
pump()
check("unsuppress restores", abs(volume() - shelled) < 1.0)

win.set_rollback(1)
pump()
check("rollback shows only the box", abs(volume() - solid_volume) < 1.0,
      volume())
win.set_rollback(None)
pump()
check("roll to end restores", abs(volume() - shelled) < 1.0)

win.undo()
pump()
check("undo works and leaves a body", win.document.shape is not None)
win.redo()
pump()
check("redo works", win.document.shape is not None)

print("browser operations")
first_sketch = win.document.sketch_features()[0]
win.rename_feature(first_sketch.id, "Renamed Sketch")
check("rename applied",
      win.document.feature(first_sketch.id).name == "Renamed Sketch")
win.browser.refresh()
# consumed sketches nest under the feature that used them, so count every
# row in the tree rather than only the top-level ones
from datum.ui.browser import ROLE_ID as _ROLE_ID, ROLE_KIND as _ROLE_KIND

listed = set()
_it = QtWidgets.QTreeWidgetItemIterator(win.browser)
while _it.value():
    _item = _it.value()
    if _item.data(0, _ROLE_KIND) == "feature":
        listed.add(int(_item.data(0, _ROLE_ID)))
    _it += 1
check("browser lists every feature",
      listed == {f.id for f in win.document.features},
      "%d listed of %d" % (len(listed), len(win.document.features)))

print("panels")
pdlg = ParametersDialog(win.document, win)
win.document.params.add("test_p", "12.5")
pdlg.reload()
check("parameter dialog lists parameters", pdlg.table.rowCount() >= 2)
pdlg.close()

mdlg = MeasureDialog(win, win)
win.set_pick_mode("face")
mdlg.refresh()
check("measure dialog reports without a selection",
      "Select geometry" in mdlg.readout.toPlainText())
mdlg.close()
pump()

props = win.document.mass_properties()
check("mass properties computed", props.get("faces", 0) > 0)

print("save, load, export")
tmp = tempfile.mkdtemp(prefix="forge_ui_")
path = os.path.join(tmp, "part.pdat")
path = win.document.save(path)
check("saved", os.path.getsize(path) > 500)

from datum.core.document import Document
reloaded = Document.load(path)
check("reload keeps volume", abs(kernel.volume(reloaded.shape) - shelled) < 1.0,
      kernel.volume(reloaded.shape))
check("reload keeps feature count",
      len(reloaded.features) == len(win.document.features))

from datum.core import fileio
for ext in (".step", ".stl", ".iges", ".brep"):
    out = os.path.join(tmp, "part" + ext)
    try:
        fileio.write_shape(win.document.shape, out)
        check("export %s" % ext, os.path.getsize(out) > 200)
    except Exception as exc:
        check("export %s" % ext, False, exc)

print("viewport behaviour")
win.viewport.set_display_mode("wireframe")
win.viewport.set_display_mode("shaded")
win.viewport.set_display_mode("shaded_edges")
for view in ("front", "top", "right", "iso"):
    win.viewport.set_view(view)
win.viewport.set_projection(False)
win.viewport.set_projection(True)
win.viewport.set_section(True)
win.viewport.set_section(False)
win.viewport.set_show_origin(False)
win.viewport.set_show_origin(True)
pump()
check("viewport survived every display change", win.viewport._ready)

uv = win.viewport.plane_point(700, 440, STANDARD_PLANES["XY"])
check("screen ray hits the sketch plane", uv is not None, uv)

print("error handling")
bad = FilletFeature()
bad.radius = "500"
bad.all_edges = True
win.document.add_feature(bad)
report = win.rebuild()
check("impossible fillet reported, app alive", not report.ok)
check("body survived the failure", win.document.shape is not None)
win.document.remove_feature(bad.id)
win.rebuild()

win.document.params.add("broken", "nope * 2")
report = win.rebuild()
check("bad expression reported", not report.ok)
win.document.params.remove("broken")
win.rebuild()

print()
if FAILS:
    print("%d FAILURES: %s" % (len(FAILS), ", ".join(FAILS)))
    sys.exit(1)
print("all UI tests passed")
sys.exit(0)
