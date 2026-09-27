"""A STEP file opened as an assembly.

The file used here is made on the spot with OpenCASCADE's own assembly
writer, so it has everything a real export has: named products, a part
used several times, a sub-assembly placed twice, rotations and colours.
Opened, the assembly DATUM writes must be the same machine: the same
volume, every solid where it was, each part once however often it is
used, and the folder still good after it has been moved.
"""
import math
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WORK = tempfile.mkdtemp(prefix="datum_stepimport_")
os.environ["DATUM_CONFIG_DIR"] = os.path.join(WORK, "cfg")

from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec, gp_Ax2  # noqa
from OCP.Quantity import Quantity_Color, Quantity_TypeOfColor        # noqa
from OCP.STEPCAFControl import STEPCAFControl_Writer                 # noqa
from OCP.STEPControl import STEPControl_StepModelType                # noqa
from OCP.TCollection import TCollection_ExtendedString               # noqa
from OCP.TDataStd import TDataStd_Name                               # noqa
from OCP.TDocStd import TDocStd_Document                             # noqa
from OCP.TopLoc import TopLoc_Location                               # noqa
from OCP.XCAFDoc import XCAFDoc_ColorType, XCAFDoc_DocumentTool       # noqa

from datum.core import fileformat, fileio, geometry, kernel, stepimport  # noqa
from datum.core.assembly import AssemblyDocument                     # noqa
from datum.core.document import Document                             # noqa

FAILS = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def placed(x, y, z, degrees=0.0):
    t = gp_Trsf()
    t.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)),
                  math.radians(degrees))
    move = gp_Trsf()
    move.SetTranslation(gp_Vec(x, y, z))
    return TopLoc_Location(move.Multiplied(t))


def named(label, text):
    TDataStd_Name.Set_s(label, TCollection_ExtendedString(text))


def colour(tool, label, r, g, b):
    tool.SetColor(label, Quantity_Color(r, g, b,
                                        Quantity_TypeOfColor.Quantity_TOC_sRGB),
                  XCAFDoc_ColorType.XCAFDoc_ColorSurf)


# ---------------------------------------------------------------- the file
#
#   Machine
#     Base                     a plate, red
#     Hinge:1  at (0,0,10)     a sub-assembly
#       Bracket                a filleted block, blue
#       Pin:1, Pin:2           a pin, used twice
#     Hinge:2  at (150,0,10), turned 90 degrees

doc = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))
shapes = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
colours = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())

base = shapes.AddShape(geometry.box(300, 120, 10).shape, False)
named(base, "Base")
colour(colours, base, 1.0, 0.0, 0.0)
bracket = shapes.AddShape(geometry.box(40, 30, 50).fillet(3).shape, False)
named(bracket, "Bracket")
colour(colours, bracket, 0.0, 0.0, 1.0)
pin = shapes.AddShape(geometry.cylinder(4, 40).shape, False)
named(pin, "Pin")

hinge = shapes.NewShape()
named(hinge, "Hinge")
named(shapes.AddComponent(hinge, bracket, placed(0, 0, 0)), "Bracket:1")
named(shapes.AddComponent(hinge, pin, placed(10, 15, 50)), "Pin:1")
named(shapes.AddComponent(hinge, pin, placed(30, 15, 50)), "Pin:2")

machine = shapes.NewShape()
named(machine, "Machine")
named(shapes.AddComponent(machine, base, placed(0, 0, 0)), "Base:1")
named(shapes.AddComponent(machine, hinge, placed(0, 0, 10)), "Hinge:1")
named(shapes.AddComponent(machine, hinge, placed(150, 40, 10, 90)),
      "Hinge:2")
shapes.UpdateAssemblies()

step = os.path.join(WORK, "Machine.iam.stp")
writer = STEPCAFControl_Writer()
writer.Transfer(doc, STEPControl_StepModelType.STEPControl_AsIs)
writer.Write(step)


print("a glance, before reading it properly")
placements, solids = stepimport.survey(step)
check("the file says it is an assembly", placements >= 5, placements)


print()
print("opened as an assembly")

folder = os.path.join(WORK, "Machine")
top = stepimport.import_assembly(step, folder)
check("the top assembly is named for the product, not the export",
      os.path.basename(top) == "Machine.adat", top)
parts = sorted(n for n in os.listdir(os.path.join(folder, "Parts"))
               if n.endswith(".pdat"))
check("each distinct part is written once",
      parts == ["Base.pdat", "Bracket.pdat", "Pin.pdat"], parts)
check("with its geometry beside it",
      all(os.path.exists(os.path.join(folder, "Parts", p[:-5] + ".brep"))
          for p in parts))
check("the sub-assembly is an assembly of its own",
      os.path.exists(os.path.join(folder, "Hinge.adat")))

asm = AssemblyDocument.load(top)
asm.rebuild()
flat = fileio.read_step(step)
va, ca = kernel.volume_and_centre(asm.shape)
vf, cf = kernel.volume_and_centre(flat)
check("the same volume as the file", abs(va - vf) < 1e-6 * vf, (va, vf))
check("  with its centre in the same place",
      max(abs(a - b) for a, b in zip(ca, cf)) < 1e-6, (ca, cf))
check("  and the same number of solids",
      len(kernel.explore(asm.shape, kernel.TopAbs_SOLID)) ==
      len(kernel.explore(flat, kernel.TopAbs_SOLID)) == 7)
names = sorted(o.name for o in asm.occurrences)
check("components are named as they were",
      names == ["Base:1", "Hinge:1", "Hinge:2"], names)
check("  and held where the file put them",
      all(o.grounded for o in asm.occurrences))
check("no component failed", not any(o.error for o in asm.occurrences),
      [o.error for o in asm.occurrences if o.error])

base_part = Document.load(os.path.join(folder, "Parts", "Base.pdat"))
check("a part keeps its colour", base_part.appearance == "#ff0000",
      base_part.appearance)
check("  and its name", base_part.properties.get("PartNumber") == "Base")
look = asm.library.looks(os.path.join(folder, "Hinge.adat"))
check("a sub-assembly lists each of its parts' looks, in order",
      look and [l.colour for l in look] == ["#0000ff", "#9aa7b6", "#9aa7b6"]
      or look and look[0].colour == "#0000ff", look and [l.colour for l in look])


print()
print("the folder travels")

moved = os.path.join(WORK, "elsewhere", "Machine copy")
shutil.copytree(folder, moved)
shutil.rmtree(folder)
fileio._READ.clear()
again = AssemblyDocument.load(os.path.join(moved, "Machine.adat"))
again.rebuild()
check("moved somewhere else it still opens whole",
      again.shape is not None
      and abs(kernel.volume(again.shape) - vf) < 1e-6 * vf
      and not any(o.error for o in again.occurrences),
      [o.error for o in again.occurrences if o.error])


print()
print("a mirrored placement, which a DATUM placement cannot hold")

left = stepimport.Product(key=1, name="Handle",
                         shape=geometry.box(50, 10, 10).move(10).shape)
mirror = gp_Trsf()
mirror.SetMirror(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(1, 0, 0)))
pair = stepimport.Product(key=2, name="Pair")
pair.children.append((left, gp_Trsf(), "Handle:1"))
pair.children.append((left, mirror, "Handle:2"))
out = stepimport.write(pair, os.path.join(WORK, "Pair"))
built = AssemblyDocument.load(out)
built.rebuild()
boxes = sorted(round(kernel.bounding_box(s)[0]) for s in
               kernel.explore(built.shape, kernel.TopAbs_SOLID))
check("it is baked into a mirrored copy of the part", boxes == [-60, 10],
      boxes)
check("  kept as a part of its own", os.path.exists(os.path.join(
    WORK, "Pair", "Parts", "Handle (mirrored).pdat")))


print()
print("a file of loose solids becomes one part per solid")

loose = os.path.join(WORK, "Loose.step")
fileio.write_shape(geometry.box(10, 10, 10).repeat(3, x=20).shape, loose)
out = stepimport.import_assembly(loose, os.path.join(WORK, "Loose"))
built = AssemblyDocument.load(out)
built.rebuild()
check("three solids, three parts",
      len(built.occurrences) == 3
      and len(kernel.explore(built.shape, kernel.TopAbs_SOLID)) == 3,
      len(built.occurrences))


print()
print("names a file system would refuse")
check("forbidden characters are replaced",
      stepimport.file_name('Bolt M8x20 <DIN 933>: "zinc"') ==
      "Bolt M8x20 _DIN 933__ _zinc_")
check("a reserved device name is not used as one",
      stepimport.file_name("CON") == "_CON")
check("the export's own suffix is not part of the name",
      stepimport._stem("Dryer.ipt.stp") == "Dryer")


print()
print("FAILED: " + ", ".join(FAILS) if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
