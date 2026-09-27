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
