"""The Parameters table opened on an assembly, in the window.

The core is covered by test_assembly_params.py.  This opens a real
assembly, changes a part's parameter from the assembly's table, sees the
assembly move, and saves: the part goes to disk with the assembly.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import harness  # noqa: E402,F401

os.environ["DATUM_SETTINGS_ORG"] = "IITEG-tests"

from PySide6 import QtWidgets                                      # noqa: E402

from datum.core import kernel                                      # noqa: E402
from datum.core.assembly import AssemblyDocument                   # noqa: E402
from datum.core.assembly_params import (                           # noqa: E402
    AssemblyParameters, load_unbuilt,
)
from datum.core.document import Document                           # noqa: E402
from datum.core.features import PrimitiveFeature                   # noqa: E402
from datum.ui.main_window import MainWindow                        # noqa: E402
from datum.ui.panels import EXPRESSION, ParametersDialog           # noqa: E402
from datum.ui.theme import stylesheet                              # noqa: E402

FAILS = []
WORK = tempfile.mkdtemp(prefix="datum_asm_params_ui_")

app = QtWidgets.QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(stylesheet())
win = MainWindow()
win.resize(1400, 900)
win.show()
app.processEvents()


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def pump(n=4):
    for _ in range(n):
        app.processEvents()


def make_part(filename, number, size):
    doc = Document()
    if number:
        doc.properties["PartNumber"] = number
    block = PrimitiveFeature(name="Block")
    block.kind = "box"
    block.a, block.b, block.c = (str(v) for v in size)
    doc.add_feature(block)
    doc.rebuild()
    return doc.save(os.path.join(WORK, filename))


box_path = make_part("box.pdat", "Box", (60, 40, 10))
plate_path = make_part("plate.pdat", "", (100, 80, 5))
asm = AssemblyDocument()
asm.place(box_path)
asm.place(plate_path)
asm_path = asm.save(os.path.join(WORK, "frame.adat"))

print("opening the table on an assembly")
win.open_path(asm_path)
pump(8)
check("the assembly is open", win.in_assembly)
components = AssemblyParameters(win.assembly, win._component_for_reading,
                                win._in_place_document)
dialog = ParametersDialog(win.assembly, win, components=components)
dialog.parts_changed.connect(win._parts_changed_from_assembly)
dialog.changed.connect(lambda: win.rebuild(keep_camera=True))
check("every part's parameters are listed by part number",
      dialog.row_of("Box_d1") >= 0 and dialog.row_of("plate_d3") >= 0)
headers = [dialog.table.item(r, 0).text()
           for r in range(dialog.table.rowCount())
           if dialog.table.item(r, 0) is not None
           and dialog.table.columnSpan(r, 0) > 1]
check("under the assembly's own, a section a part",
      headers[0].startswith("Assembly Parameters")
      and any(h.startswith("Box") for h in headers)
      and any(h.startswith("plate") for h in headers), headers)
check("and listing them built nothing", not win._in_place_docs)

print()
print("changing a part from the assembly")
top_before = kernel.bounding_box(win.assembly.shape)[5]
dialog.table.item(dialog.row_of("Box_d3"), EXPRESSION).setText("25")
pump(4)
box = win._in_place_docs.get(os.path.normcase(os.path.abspath(box_path)))
check("the part is opened to take it", box is not None and box.modified)
check("and rebuilt", box is not None
      and abs(kernel.bounding_box(box.shape)[5] - 25.0) < 1e-3)
top_after = kernel.bounding_box(win.assembly.shape)[5]
check("the assembly shows it straight away",
      abs(top_after - 25.0) < 1e-3 and top_after != top_before,
      (top_before, top_after))
check("the assembly knows it has unsaved changes", win.assembly.modified)

dialog.table.item(dialog.row_of("Box_d1"), EXPRESSION).setText(
    "plate_d1 / 2")
pump(4)
check("written in terms of another part, the assembly drives it",
      win.assembly.drivers.get("Box_d1") == "plate_d1 / 2",
      win.assembly.drivers)
dialog.table.setCurrentCell(dialog.row_of("Box_d1"), 0)
check("a driven parameter can be released", dialog.release_btn.isEnabled())
dialog.table.setCurrentCell(dialog.row_of("Box_d2"), 0)
check("one that is not driven cannot", not dialog.release_btn.isEnabled())
dialog.close()

print()
print("saving the assembly saves the part")
check("saved", win.save_document())
on_disk = load_unbuilt(box_path)
values = {m.name: m.expression for m in on_disk.model_parameters()}
check("the part file has the new values",
      values.get("d3") == "25" and values.get("d1") == "50", values)
check("and is no longer marked changed", not box.modified)
again = AssemblyDocument.load(asm_path)
check("the assembly kept its driver", again.drivers.get("Box_d1")
      == "plate_d1 / 2", again.drivers)

print()
print("FAILED: " + ", ".join(FAILS) if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
