"""The window's half of the speed work.

Each check here is a cost that used to be paid and is not any more, stated
so it would fail if the cost came back: a mesh made and then thrown away,
a hidden panel doing sums, an assembly drawn twice on its way in.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WORK = tempfile.mkdtemp(prefix="datum_mesh_ui_")
os.environ["DATUM_CONFIG_DIR"] = os.path.join(WORK, "cfg")

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402,F401

from PySide6 import QtWidgets  # noqa: E402
from OCP.BRep import BRep_Tool  # noqa: E402
from OCP.TopAbs import TopAbs_FACE  # noqa: E402
from OCP.TopExp import TopExp_Explorer  # noqa: E402
from OCP.TopLoc import TopLoc_Location  # noqa: E402
from OCP.TopoDS import TopoDS  # noqa: E402
from OCP.gp import gp_Trsf, gp_Vec  # noqa: E402

from datum.core import geometry, mesh  # noqa: E402
from datum.ui.main_window import MainWindow  # noqa: E402
from datum.ui.theme import stylesheet  # noqa: E402

FAILS = []
app = QtWidgets.QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(stylesheet())


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def pump(n=6):
    for _ in range(n):
        app.processEvents()


def triangles(shape):
    """Triangle count per face: a remesh at another tolerance changes it."""
    out = []
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        tri = BRep_Tool.Triangulation_s(TopoDS.Face_s(explorer.Current()),
                                        TopLoc_Location())
        out.append(None if tri is None else tri.NbTriangles())
        explorer.Next()
    return out


def moved(shape, x):
    t = gp_Trsf()
    t.SetTranslation(gp_Vec(x, 0, 0))
    return shape.Moved(TopLoc_Location(t))


# counted from the moment the window exists, so the first open is included
win = MainWindow()
drawn = []
real_set_components = win.viewport.set_components


def counting(items, *a, **k):
    drawn.append(len(list(items)) if not isinstance(items, list)
                 else len(items))
    return real_set_components(items, *a, **k)


win.viewport.set_components = counting
win.resize(1300, 850)
win.show()
pump(10)


print("the first assembly of a session is drawn once")

asm = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(
    __file__))), "examples", "excavator", "Excavator.adat")
check("the excavator opens", win.open_path(asm))
pump(10)
check("  and is drawn once, not twice", len(drawn) == 1, drawn)
component = next(iter(win.viewport._component_ais.values()))
check("components draw the mesh they were given",
      not component.Attributes().IsAutoTriangulation())


print()
print("a mesh made here is the mesh drawn")

win.new_document()
pump(10)
body = (geometry.box(400, 300, 200) + geometry.sphere(250)).shape
# finer than the viewport would pick, so a remesh would show as a change
mesh.mesh(body, mesh.deflection(body) / 4.0)
before = triangles(body)
win.viewport.set_shape(body, keep_camera=False)
pump()
check("a part's body is drawn from its mesh, not remeshed",
      triangles(body) == before, (before[:4], triangles(body)[:4]))
check("  and the viewport is told so",
      not win.viewport.model_ais.Attributes().IsAutoTriangulation())

# big and round, so the count follows the tolerance rather than the angle
part = geometry.sphere(300).shape
mesh.mesh(part, mesh.deflection(part) / 4.0)
shared = triangles(part)
win.viewport.set_components([
    {"id": i + 1, "shape": moved(part, i * 700)} for i in range(5)])
pump()
check("five copies of a part share one mesh, untouched by drawing them",
      triangles(part) == shared)
win.viewport.clear_components()
win.viewport.set_shape(None)


print()
print("what did not change is not drawn again")

win.viewport.set_shape(body, keep_camera=True)
pump()
drawn_once = win.viewport.model_ais
win.viewport.set_shape(body, keep_camera=True)
check("the same body handed over twice keeps the picture it has",
      win.viewport.model_ais is drawn_once)
from datum.core import materials  # noqa: E402
steel = materials.library().appearance_for("Steel, Mild")
win.viewport.set_shape(body, keep_camera=True, appearance=steel)
check("a new material recolours it rather than drawing it again",
      win.viewport.model_ais is drawn_once)
win.viewport.set_shape(None)

win.new_document()
pump()
from datum.core.features import PrimitiveFeature  # noqa: E402
block = PrimitiveFeature()
block.kind = "box"
block.a, block.b, block.c = "40", "30", "20"
block.operation = "new"
win.document.add_feature(block)
win.rebuild(keep_camera=False)
pump()
shown = win.viewport.model_ais
win.start_sketch_on_plane("XY")
pump()
win.finish_sketch()
pump()
check("drawing a sketch leaves the body on screen as it was",
      win.viewport.model_ais is shown)


print()
print("a body of many pieces is drawn a piece at a time")

from datum.core import fileio  # noqa: E402
from datum.core.features import ImportFeature  # noqa: E402
many = os.path.join(tempfile.mkdtemp(prefix="pieces_"), "twelve.brep")
fileio.write_shape(geometry.box(10, 10, 10).repeat(12, x=30).shape, many)
win.new_document()
pump()
brought = ImportFeature()
brought.path = many
brought.operation = "new"
win.document.add_feature(brought)
win.rebuild(keep_camera=False)
pump()
first = list(win.viewport._model_chunks)
check("twelve solids, twelve pieces on screen", len(first) == 12,
      len(first))
hole = PrimitiveFeature()
hole.kind = "cylinder"
hole.a, hole.b, hole.c = "3", "20", "0"
hole.origin = ("35", "5", "-5")
hole.operation = "cut"
win.document.add_feature(hole)
win.rebuild(keep_camera=True)
pump()
after = list(win.viewport._model_chunks)
kept = sum(1 for a in after if any(a is b for b in first))
check("a hole through one of them redraws that one and keeps the rest",
      len(after) == 12 and kept == 11, (len(after), kept))


print()
print("a big assembly is drawn first and made pickable after")
win.new_document()
pump()
win.viewport.set_shape(None)
blocks = [{"id": i + 1,
           "shape": geometry.box(10, 10, 10).move(i * 15, 0, 0).shape}
          for i in range(40)]
win.viewport.set_components(blocks, keep_camera=False)
waiting = len(win.viewport._arming[0])
check("forty parts on screen, most still to be made pickable",
      waiting > win.viewport.ARM_NOW, waiting)
x, y = win.viewport.project((20 * 15 + 5, 5, 5))
check("pointing at one finds it all the same",
      win.viewport.component_under(x, y) == 21,
      win.viewport.component_under(x, y))
check("  having made everything pickable first",
      not win.viewport._arming[0])
win.viewport.set_components(blocks, keep_camera=True)
for _ in range(200):
    if not win.viewport._arming[0]:
        break
    app.processEvents()
check("left alone, the rest are made pickable between events",
      not win.viewport._arming[0])
x, y = win.viewport.project((35 * 15 + 5, 5, 5))
check("  and picked like any other", win.viewport.component_under(x, y) == 36)
win.viewport.clear_components()
check("taken off screen, nothing is left waiting",
      not win.viewport._arming[0])


print()
print("closing a document lets go of what was worked out about its shapes")
from datum.core import kernel  # noqa: E402
win.new_document()
pump()
brought = ImportFeature()
brought.path = many
brought.operation = "new"
win.document.add_feature(brought)
win.rebuild(keep_camera=False)
pump()
win.document.mass_properties()
check("measuring a body of many pieces remembers them",
      len(kernel._MEASURES) >= 12 and len(mesh._BOXES) >= 12)
win.document.modified = False
win.close_entry(win.session.active)
pump()
check("  and closing its tab forgets them, and the meshes they hold",
      not kernel._MEASURES and not kernel._BOXES and not mesh._BOXES)


print()
print("the SpaceMouse looks for new devices without stopping the window")

import time  # noqa: E402
started = time.perf_counter()
win.spacemouse._rescan_in_background()
check("a rescan hands the looking to another thread and returns",
      time.perf_counter() - started < 0.05,
      "%.3f s" % (time.perf_counter() - started))
deadline = time.perf_counter() + 5.0
while win.spacemouse._scanning and time.perf_counter() < deadline:
    pump(2)
    time.sleep(0.01)
check("  and what it found comes back", not win.spacemouse._scanning)


print()
print("the hidden properties panel does no sums")

document = win.document
calls = []
real = document.mass_properties


def counted():
    calls.append(1)
    return real()


document.mass_properties = counted
win.rebuild(keep_camera=True)
pump()
check("a rebuild with the panel hidden works nothing out", not calls,
      len(calls))
win.show_properties()
pump()
check("opening the panel works it out", len(calls) >= 1)
check("  and shows it", win.properties.table.rowCount() > 0)
win.properties_window.close()
pump()


print()
print("FAILED: " + ", ".join(FAILS) if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
