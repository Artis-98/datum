"""Parameters across an assembly: every part's, named for its part.

An assembly's table lists its own parameters and every parameter of every
part it places, each prefixed with the part's number: Box's d2 is Box_d2.
A change in the part's own terms is written into the part.  One that
reaches outside it, to another part or to the assembly, is kept by the
assembly as a driver and the part is handed the number, so it still opens
on its own.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import harness  # noqa: E402,F401

os.environ["DATUM_SETTINGS_ORG"] = "IITEG-tests"

from datum.core import kernel                                     # noqa: E402
from datum.core.assembly import AssemblyDocument                  # noqa: E402
from datum.core.assembly_params import (                          # noqa: E402
    AssemblyParameters, load_unbuilt, prefix_for, sanitise,
)
from datum.core.document import Document                          # noqa: E402
from datum.core.features import PrimitiveFeature                  # noqa: E402
from datum.core.params import ExpressionError                     # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + ("  " + str(extra) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def near(a, b, tol=1e-6):
    return abs(a - b) < tol


WORK = tempfile.mkdtemp(prefix="datum_asm_params_")


def make_part(filename, number, size):
    doc = Document()
    if number:
        doc.properties["PartNumber"] = number
    block = PrimitiveFeature(name="Block")
    block.kind = "box"
    block.a, block.b, block.c = (str(v) for v in size)
    doc.add_feature(block)
    doc.rebuild()
    path = os.path.join(WORK, filename)
    doc.save(path)
    return path


box_path = make_part("box.pdat", "Box", (60, 40, 10))
plate_path = make_part("plate.pdat", "", (100, 80, 5))

asm = AssemblyDocument()
asm.path = os.path.join(WORK, "frame.adat")
asm.place(box_path)
asm.place(box_path)
asm.place(plate_path)

live = {}


def open_live(path):
    """What the window does: open the part once and keep it."""
    key = os.path.normcase(os.path.abspath(path))
    if key not in live:
        live[key] = Document.load(path)
    return live[key]


def read(path):
    key = os.path.normcase(os.path.abspath(path))
    return live.get(key) or load_unbuilt(path)


table = AssemblyParameters(asm, read, open_live)

# ==========================================================================
print("the parts and their names")
check("a part number makes the prefix", prefix_for(
    load_unbuilt(box_path), box_path) == "Box")
check("without one, the file name does", prefix_for(
    load_unbuilt(plate_path), plate_path) == "plate")
check("anything that cannot start a name is made fit",
      sanitise("12-A bracket") == "p12_A_bracket", sanitise("12-A bracket"))
components = table.components()
check("each part file is listed once, however often it is placed",
      [c.prefix for c in components] == ["Box", "plate"],
      [c.prefix for c in components])
rows = {r.qualified: r for r in table.rows()}
check("every parameter of every part, qualified",
      {"Box_d1", "Box_d2", "Box_d3", "plate_d1", "plate_d3"} <= set(rows),
      sorted(rows))
check("with the part's own value", near(rows["Box_d1"].value, 60.0)
      and near(rows["plate_d2"].value, 80.0))
check("listing them built nothing", not live)

# ==========================================================================
print()
print("changing a part from the assembly")
changed = table.set("Box_d3", "25")
box = open_live(box_path)
check("a plain number is written into the part",
      box.params.names() == [] and any(
          m.name == "d3" and m.expression == "25"
          for m in box.model_parameters()),
      [(m.name, m.expression) for m in box.model_parameters()])
check("no driver is kept for it", "Box_d3" not in asm.drivers)
check("the part is reported as changed, and marked so",
      changed == [box] and box.modified)
check("and so is the assembly, which is where it was done", asm.modified)
box.rebuild()
check("the part builds to the new size",
      near(kernel.bounding_box(box.shape)[5], 25.0, 1e-3))

table.set("Box_d3", "Box_d1 / 4")
check("an expression in the part's own names goes in as its own",
      any(m.name == "d3" and m.expression == "d1 / 4"
          for m in box.model_parameters()) and "Box_d3" not in asm.drivers,
      [(m.name, m.expression) for m in box.model_parameters()])

# ==========================================================================
print()
print("the assembly driving a part")
table.set("Box_d1", "plate_d1 / 2")
check("an expression reaching another part is kept as a driver",
      asm.drivers.get("Box_d1") == "plate_d1 / 2", asm.drivers)
check("and the part is handed the number",
      any(m.name == "d1" and m.expression == "50"
          for m in box.model_parameters()),
      [(m.name, m.expression) for m in box.model_parameters()])

asm.params.add("length", "Box_d1 + 5")
table.refresh_assembly()
check("an assembly parameter can read a part's",
      near(asm.params["length"].value, 55.0) and not asm.params["length"].error,
      (asm.params["length"].value, asm.params["length"].error))
table.set("Box_d2", "length * 2")
check("and drive one", any(m.name == "d2" and m.expression == "110"
                           for m in box.model_parameters()),
      [(m.name, m.expression) for m in box.model_parameters()])

changed = table.set("plate_d1", "120")
plate = open_live(plate_path)
check("changing what a driver reads moves the driven part with it",
      box in changed and any(m.name == "d1" and m.expression == "60"
                             for m in box.model_parameters()),
      [(m.name, m.expression) for m in box.model_parameters()])
table.refresh_assembly()
check("  and the assembly parameter that reads it",
      near(asm.params["length"].value, 65.0), asm.params["length"].value)
changed = table.apply()
check("applying again with nothing new changes nothing", changed == [])

asm.drivers["plate_d2"] = "Box_d2 + plate_d2"
before = [(m.name, m.expression) for m in plate.model_parameters()]
table.apply()
check("a driver that reads itself is refused, and the part left alone",
      [(m.name, m.expression) for m in plate.model_parameters()] == before
      and "plate_d2" in table.errors, table.errors)
table.release("plate_d2")
check("releasing a driver forgets it", "plate_d2" not in asm.drivers)

try:
    table.set("Nothing_d1", "5")
    refused = False
except ExpressionError:
    refused = True
check("a name that is no part's is refused", refused)

# ==========================================================================
print()
print("kept with the assembly")
asm.save(asm.path)
again = AssemblyDocument.load(asm.path)
check("the drivers are saved with it",
      again.drivers == {"Box_d1": "plate_d1 / 2", "Box_d2": "length * 2"},
      again.drivers)

print()
print("FAILED: " + ", ".join(FAILS) if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
