"""Fillets on a washer, Extrude starting with a profile, and assemblies.

A washer's inner and outer rims share a centre, and that alone made the
second one picked undo the first.  A radius of exactly half a wall rounds
it right over, which OCCT refuses and is now built a hair under.

A new Extrude starts with the likeliest profile already chosen.

In an assembly: a part already open in a tab of its own is edited there,
not a second time in place; the parts around one being edited in place
stay see-through whatever the part does; and Place Constraint shows its
first pick in blue and its second in purple, on the model and in the box.
"""

import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import harness  # noqa: E402,F401

os.environ["DATUM_SETTINGS_ORG"] = "IITEG-tests"

from PySide6 import QtCore, QtWidgets                              # noqa: E402

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox                    # noqa: E402

from datum.core import fileformat, kernel                          # noqa: E402
from datum.core.assembly import Occurrence                         # noqa: E402
from datum.core.document import Document                           # noqa: E402
from datum.core.features import (                                  # noqa: E402
    ExtrudeFeature, FilletFeature, PrimitiveFeature, ProfileSelection,
    SketchFeature,
)
from datum.core.sketch import STANDARD_PLANES, Sketch              # noqa: E402
from datum.ui.main_window import MainWindow                        # noqa: E402
from datum.ui.theme import stylesheet                              # noqa: E402

FAILS = []
WORK = tempfile.mkdtemp(prefix="datum_b14_")


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


app = QtWidgets.QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(stylesheet())
win = MainWindow()
win.resize(1400, 900)
win.show()
app.processEvents()
vp = win.viewport


def pump(n=6):
    for _ in range(n):
        app.processEvents()


def settle(ms=300):
    loop = QtCore.QEventLoop()
    QtCore.QTimer.singleShot(ms, loop.quit)
    loop.exec()


def ring_in_window(thickness="10"):
    win.new_document(prompt=False)
    doc = win.document
    sketch = SketchFeature()
    sketch.name = "Sketch1"
    sketch.sketch = Sketch(STANDARD_PLANES["XY"], "Sketch1")
    sketch.sketch.add_circle((0.0, 0.0), 40.0)
    sketch.sketch.add_circle((0.0, 0.0), 20.0)
    doc.add_feature(sketch)
    extrude = ExtrudeFeature()
    extrude.distance = thickness
    extrude.sketch_id = sketch.id
    extrude.profiles = ProfileSelection()
    extrude.profiles.add(sketch.id,
                         kernel.sketch_regions(sketch.sketch)[0]["centre"])
    doc.add_feature(extrude)
    win.rebuild()
    pump()
    return doc


def rim(shape, radius, z=None):
    # the top, whatever the part's units made of the thickness
    z = kernel.bounding_box(shape)[5] if z is None else z
    for edge in kernel.edges(shape):
        if kernel.is_seam(edge):
            continue
        box = kernel.bounding_box(edge)
        if abs(box[3] - radius) < 0.2 and abs(box[5] - z) < 0.2:
            return edge
    return None


# ==========================================================================
print("a washer's inner and outer rims, filleted together")
doc = ring_in_window()
win.new_feature(FilletFeature)
pump()
dialog = win._active_dialog
check("the fillet dialog is open", dialog is not None)
dialog.picker.set_picking(True)
base = win.pick_shape()
for radius in (40.0, 20.0):
    edge = rim(base, radius)
    vp.selected_edges = lambda e=edge: [e]
    dialog.on_selection()
    pump()
del vp.selected_edges
check("both rims are held, the second did not undo the first",
      len(dialog.feature.refs) == 2, len(dialog.feature.refs))
dialog.size.set_text("3")
dialog.commit()
pump()
check("and the fillet builds on both", not dialog.feature.error,
      dialog.feature.error)
faces = len(kernel.faces(win.document.shape))
check("two rounded rims: four flat or straight faces and two round ones",
      faces == 6, faces)

print("a radius of half the wall rounds it right over")
box = BRepPrimAPI_MakeBox(300.0, 100.0, 60.0).Shape()
long_top = [e for e in kernel.edges(box)
            if abs(kernel.bounding_box(e)[3] - kernel.bounding_box(e)[0]
                   - 300.0) < 1e-3
            and abs(kernel.bounding_box(e)[2] - 60.0) < 1e-3
            and abs(kernel.bounding_box(e)[5] - 60.0) < 1e-3]
check("the two long top edges of a 100 wide wall", len(long_top) == 2)
rounded = kernel.fillet(box, long_top, 50.0)
expect = 300 * 100 * 60 - 300 * (2 * 50 * 50 - math.pi * 50 * 50 / 2)
check("radius 50 builds, a full round",
      kernel.is_valid(rounded) and abs(kernel.volume(rounded) - expect) < 50,
      (kernel.volume(rounded), expect))
try:
    kernel.fillet(box, long_top, 52.0)
    refused = False
except kernel.KernelError:
    refused = True
check("past half the wall is still refused", refused)

doc = ring_in_window("10")
fillet = FilletFeature()
fillet.radius = "5"
fillet.all_edges = True
win.document.add_feature(fillet)
report = win.rebuild()
check("the washer rounded on every rim at half its thickness",
      report.ok and kernel.is_valid(win.document.shape), report.message)

# ==========================================================================
print("a new Extrude starts with the likeliest profile chosen")
win.new_document(prompt=False)
doc = win.document
sketch = SketchFeature()
sketch.name = "Sketch1"
sketch.sketch = Sketch(STANDARD_PLANES["XY"], "Sketch1")
sketch.sketch.add_rectangle((0.0, 0.0), (80.0, 50.0))
sketch.sketch.add_circle((40.0, 25.0), 10.0)
doc.add_feature(sketch)
win.rebuild()
win.new_feature(ExtrudeFeature)
pump()
settle(200)
dialog = win._active_dialog
chosen = list(dialog.feature.profiles) if dialog else []
check("one profile is already chosen", len(chosen) == 1, len(chosen))
regions = kernel.sketch_regions(sketch.sketch)
plate = max(regions, key=lambda r: r["area"])
check("the plate with its hole, not the hole",
      dialog is not None and dialog.feature.profiles.contains(
          sketch.id, plate["centre"]))
check("the field says so", dialog is not None
      and "1 profile" in dialog.profiles.count.text(),
      dialog.profiles.count.text() if dialog else None)
dialog.commit()
pump()
check("OK straight away makes the plate",
      abs(kernel.volume(win.document.shape)
          - (80 * 50 - math.pi * 100) * 10.0) < 5.0,
      kernel.volume(win.document.shape))

# ==========================================================================


def make_part(name, w, d, h):
    part = Document()
    f = PrimitiveFeature()
    f.kind, f.a, f.b, f.c, f.operation = "box", str(w), str(d), str(h), "new"
    part.add_feature(f)
    part.rebuild()
    return part.save(os.path.join(WORK, name))


BLOCK = make_part("block.pdat", 40, 30, 10)
PLATE = make_part("plate.pdat", 60, 60, 5)

win.new_assembly()
pump(8)
asm = win.assembly
asm.path = os.path.join(WORK, "rig.adat")
for source in (BLOCK, PLATE):
    asm.occurrences.append(Occurrence(
        id=asm.new_id(),
        ref=fileformat.ComponentRef(
            path=fileformat.relative_path(source, WORK),
            name=os.path.basename(source)),
        name=asm.unique_name(os.path.splitext(os.path.basename(source))[0])))
asm.rebuild()
win.assembly_ui.rebuild()
pump(8)
assembly_entry = win.session.active
block_occ, plate_occ = asm.occurrences[0], asm.occurrences[1]

print("a part open in its own tab is edited there")
win.open_path(BLOCK)
pump(8)
block_entry = win.session.active
check("the block has a tab of its own", block_entry is not assembly_entry)
win.activate(assembly_entry)
pump(8)
check("back in the assembly", win.in_assembly)
win.edit_in_place(block_occ.id)
pump(8)
check("double-clicking it brings its tab to the front",
      win.session.active is block_entry, win.session.active)
check("and does not edit it in place as well", not win.in_place,
      win.in_place)

print("the parts round one edited in place stay see-through")
win.activate(assembly_entry)
pump(8)
win.edit_in_place(plate_occ.id)
pump(10)
check("editing the plate in place", bool(win.in_place))
ghosts = list(vp._component_ais.values())
check("the block is drawn round it, see-through",
      ghosts and all(a.Transparency() > 0.5 for a in ghosts),
      [round(a.Transparency(), 2) for a in ghosts])
win.new_feature(FilletFeature)
pump(8)
dialog = win._active_dialog
dialog.all_edges.setChecked(True)
dialog.size.set_text("1")
dialog.preview()
pump(8)
ghosts = list(vp._component_ais.values())
check("still see-through with a feature started and previewed",
      ghosts and all(a.Transparency() > 0.5 for a in ghosts),
      [round(a.Transparency(), 2) for a in ghosts])
dialog.cancel()
pump(8)
ghosts = list(vp._component_ais.values())
check("and after it is cancelled",
      ghosts and all(a.Transparency() > 0.5 for a in ghosts),
      [round(a.Transparency(), 2) for a in ghosts])
win.finish_in_place()
pump(10)

print("Place Constraint: first blue, second purple")
from datum.ui.assembly_ui import PICK_COLOURS                      # noqa: E402

win.assembly_ui.constrain()
pump(6)
dialog = win.assembly_ui.dialog
check("the dialog is open", dialog is not None)
check("the first button is underlined blue",
      PICK_COLOURS[0] in dialog.first.button.styleSheet(),
      dialog.first.button.styleSheet())
check("the second purple",
      PICK_COLOURS[1] in dialog.second.button.styleSheet(),
      dialog.second.button.styleSheet())
before = len(vp._preview)
block_occ = win.assembly.occurrences[0]
top = max(kernel.faces(block_occ.shape),
          key=lambda f: kernel.shape_centre(f)[2])
dialog.constraint.a = win.assembly.attach(block_occ, "face", top)
dialog._show_arrows()
pump(4)
tinted = [ais for ais in vp._preview
          if hasattr(ais, "Shape") and not ais.Shape().IsNull()
          and ais.Shape().ShapeType() == top.ShapeType()]
check("the picked face is lit up on the model", len(tinted) == 1,
      len(vp._preview) - before)
dialog.cancel()
pump(4)

print()
if FAILS:
    print("%d FAILURES: %s" % (len(FAILS), ", ".join(FAILS)))
    sys.exit(1)
print("all batch 14 tests passed")
sys.exit(0)
