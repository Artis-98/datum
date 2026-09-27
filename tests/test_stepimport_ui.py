"""STEP files in the window: open one, import one, see each part's colour."""
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WORK = tempfile.mkdtemp(prefix="datum_stepimport_ui_")
os.environ["DATUM_CONFIG_DIR"] = os.path.join(WORK, "cfg")
os.environ["DATUM_DOCUMENTS"] = os.path.join(WORK, "Documents")

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402

from PySide6 import QtWidgets  # noqa: E402
from OCP.AIS import AIS_ColoredShape  # noqa: E402
from OCP.gp import gp_Trsf, gp_Vec  # noqa: E402
from OCP.Quantity import Quantity_Color, Quantity_TypeOfColor  # noqa: E402
from OCP.STEPCAFControl import STEPCAFControl_Writer  # noqa: E402
from OCP.STEPControl import STEPControl_StepModelType  # noqa: E402
from OCP.TCollection import TCollection_ExtendedString  # noqa: E402
from OCP.TDataStd import TDataStd_Name  # noqa: E402
from OCP.TDocStd import TDocStd_Document  # noqa: E402
from OCP.TopLoc import TopLoc_Location  # noqa: E402
from OCP.XCAFDoc import XCAFDoc_ColorType, XCAFDoc_DocumentTool  # noqa: E402

from datum.core import fileio, geometry  # noqa: E402
from datum.ui.main_window import MainWindow  # noqa: E402
from datum.ui.theme import stylesheet  # noqa: E402

FAILS = []
app = QtWidgets.QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(stylesheet())
win = MainWindow()
win.resize(1300, 850)
win.show()


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def pump(n=8):
    for _ in range(n):
        app.processEvents()


def at(x):
    t = gp_Trsf()
    t.SetTranslation(gp_Vec(x, 0, 0))
    return TopLoc_Location(t)


def name(label, text):
    TDataStd_Name.Set_s(label, TCollection_ExtendedString(text))


# a two level assembly: Rig holds a Plate and two Clamps, a sub-assembly of
# a red jaw and a green screw
doc = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))
shapes = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
colours = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())
plate = shapes.AddShape(geometry.box(200, 60, 8).shape, False)
name(plate, "Plate")
jaw = shapes.AddShape(geometry.box(20, 20, 20).shape, False)
name(jaw, "Jaw")
colours.SetColor(jaw, Quantity_Color(1, 0, 0, Quantity_TypeOfColor.Quantity_TOC_sRGB),
                 XCAFDoc_ColorType.XCAFDoc_ColorSurf)
screw = shapes.AddShape(geometry.cylinder(3, 30).move(10, 10, 20).shape,
                        False)
name(screw, "Screw")
colours.SetColor(screw, Quantity_Color(0, 1, 0, Quantity_TypeOfColor.Quantity_TOC_sRGB),
                 XCAFDoc_ColorType.XCAFDoc_ColorSurf)
clamp = shapes.NewShape()
name(clamp, "Clamp")
shapes.AddComponent(clamp, jaw, at(0))
shapes.AddComponent(clamp, screw, at(0))
rig = shapes.NewShape()
name(rig, "Rig")
shapes.AddComponent(rig, plate, at(0))
shapes.AddComponent(rig, clamp, at(20))
shapes.AddComponent(rig, clamp, at(150))
shapes.UpdateAssemblies()
step = os.path.join(WORK, "Rig.stp")
writer = STEPCAFControl_Writer()
writer.Transfer(doc, STEPControl_StepModelType.STEPControl_AsIs)
writer.Write(step)


print("opening a STEP assembly")
harness.reset()
check("it opens", win.open_path(step))
pump()
check("it asked first, and said where the parts would go",
      harness.asked("question") and "folder" in harness.text("question"))
check("as an assembly", win.in_assembly
      and len(win.assembly.occurrences) == 3,
      win.in_assembly and len(win.assembly.occurrences))
check("written into the active project, not beside the download",
      os.path.normcase(win.assembly.path).startswith(
          os.path.normcase(win.project_folder())),
      (win.assembly.path, win.project_folder()))
clamps = [o for o in win.assembly.occurrences
          if o.ref.path.lower().endswith(".adat")]
drawn = win.viewport._component_ais.get(clamps[0].id) if clamps else None
check("a sub-assembly is drawn with each of its parts in its own colour",
      isinstance(drawn, AIS_ColoredShape), type(drawn).__name__)
check("the status bar says what happened", "assembly of 3 parts"
      in win.status_message.text(), win.status_message.text())


print()
print("a single solid opens as a part, no questions")
single = os.path.join(WORK, "Block.step")
fileio.write_shape(geometry.box(30, 20, 10).shape, single)
harness.reset()
check("it opens", win.open_path(single))
pump()
check("as a part holding the body", win.in_part
      and win.document.shape is not None and not harness.asked("question"))


print()
print("importing an assembly into a part offers the assembly instead")
win.new_document()
pump()
harness.reset()
harness.opening(step)
win.import_geometry()
pump()
check("it asked", harness.asked("question"))
check("and yes opened the assembly", win.in_assembly)


print()
print("FAILED: " + ", ".join(FAILS) if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
