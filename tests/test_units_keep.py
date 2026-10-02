"""Units in dProperties, and keeping the numbers through a change (#122).

A part's unit is picked in dProperties as well as the status bar, and
either way it asks: convert, so the part keeps its size, or keep the
numbers, so 25.4 mm becomes 25.4 in and the part is rescaled.  Keeping
them has to reach every length the part holds, or a fillet loses its edge,
a model state reads the old size, or an imported body stays small.
"""

import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import harness  # noqa: E402,F401

os.environ["DATUM_SETTINGS_ORG"] = "IITEG-tests"

from PySide6 import QtWidgets                                      # noqa: E402

from datum.core import fileio, kernel, modelparams, rescale        # noqa: E402
from datum.core import units as unitlib                            # noqa: E402
from datum.core.document import Document                           # noqa: E402
from datum.core.features import (                                  # noqa: E402
    ExtrudeFeature, Feature, FilletFeature, ImportFeature, SketchFeature)
from datum.core.modelstates import PRIMARY                         # noqa: E402
from datum.core.naming import RefSet                               # noqa: E402
from datum.core.sketch import STANDARD_PLANES, Sketch              # noqa: E402

FAILS = []
WORK = tempfile.mkdtemp(prefix="datum_units_keep_")
INCH = 25.4


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def near(a, b, tol=1e-6):
    return abs(a - b) <= tol * max(1.0, abs(b))


def size(shape):
    b = kernel.bounding_box(shape)
    return (b[3] - b[0], b[4] - b[1], b[5] - b[2])


def plate():
    """A 25.4 by 10 plate, t thick, its four upright edges filleted."""
    doc = Document()
    doc.params.add("t", "5", unit="mm")
    sk = SketchFeature()
    sk.name = "Base"
    sk.sketch = Sketch(STANDARD_PLANES["XY"], "Base")
    ids = sk.sketch.add_rectangle((0, 0), (25.4, 10))
    corner = sk.sketch.entities[ids[0]].points
    sk.sketch.points[corner[0]].fixed = True
    sk.sketch.add_constraint("distance_x", points=[corner[0], corner[1]],
                             value=25.4)
    left = sk.sketch.entities[ids[3]].points
    sk.sketch.add_constraint("distance_y", points=[left[1], left[0]],
                             value=10.0)
    doc.add_feature(sk)
    ex = ExtrudeFeature()
    ex.sketch_id = sk.id
    ex.distance = "t"
    ex.profiles.add(sk.id, (12.7, 5.0))
    doc.add_feature(ex)
    doc.rebuild()
    upright = [e for e in kernel.edges(doc.shape)
               if near(kernel.edge_length(e), 5.0, 1e-4)]
    fl = FilletFeature()
    fl.radius = "1"
    fl.refs = RefSet()
    fl.refs.capture_from(doc.shape, "edge", upright)
    doc.add_feature(fl)
    modelparams.assign_names(doc)
    return doc, sk, ex, fl


# --------------------------------------------------------------- the text
print("an expression keeps its numbers")
for text, old, new, want in (("10", "mm", "in", "10 in"),
                             ("d1 + 5", "mm", "in", "d1 + 5 in"),
                             ("d1 * 2", "mm", "in", "d1 * 2"),
                             ("d1 / 2 + 1 in", "in", "mm", "d1 / 2 + 1 mm"),
                             ("2 in", "in", "mm", "2 mm"),
                             ("", "mm", "in", "")):
    got = unitlib.rescaled(text, old, new)
    check("%r from %s to %s is %r" % (text, old, new, want), got == want, got)

# --------------------------------------------------------------- the part
print()
print("keeping the numbers rescales the whole part")
doc, sk, ex, fl = plate()
report = doc.rebuild()
check("the plate builds, filleted", report.ok and doc.shape is not None,
      report.message)
before = size(doc.shape)
volume = kernel.volume(doc.shape)
width = next(c for c in sk.sketch.constraints.values()
             if c.kind == "distance_x")

states = doc.model_states
states.create(doc, "Long")
modelparams.set_expression(doc, width.name, "50.8")
doc.rebuild()
states.activate(doc, PRIMARY)
doc.rebuild()

notes = rescale.keep_numbers(doc, "in")
report = doc.rebuild()
check("the part is in inches now", doc.units == "in")
check("and rebuilds, the fillet still finding its edges",
      report.ok and not fl.error, (report.message, fl.error))
after = size(doc.shape)
check("every side is 25.4 times as long",
      all(near(a, b * INCH, 1e-5) for a, b in zip(after, before)),
      (before, after))
check("the volume is 25.4 cubed times as much",
      near(kernel.volume(doc.shape), volume * INCH ** 3, 1e-3))
check("the width still reads 25.4",
      near(unitlib.to_unit(width.value, "in"), 25.4), width.value)
check("the thickness parameter still reads 5",
      near(unitlib.to_unit(doc.params["t"].value, "in"), 5.0)
      and doc.params["t"].unit == "in",
      (doc.params["t"].expression, doc.params["t"].unit))
check("the fillet radius still reads 1", fl.radius == "1 in", fl.radius)
check("the extrude kept its region", len(ex.profiles) == 1
      and near(ex.profiles.items[0][1], 12.7 * INCH), ex.profiles.items)
check("nothing it could not rescale", notes == [], notes)

states.activate(doc, "Long")
doc.rebuild()
check("the Long state still reads 50.8",
      near(unitlib.to_unit(width.value, "in"), 50.8), width.value)
check("and holds nothing else as a change",
      set(states.states["Long"]) == {"p:" + width.name},
      states.states["Long"])
states.activate(doc, PRIMARY)
doc.rebuild()
check("Primary is back to 25.4",
      near(unitlib.to_unit(width.value, "in"), 25.4), width.value)

notes = rescale.keep_numbers(doc, "mm")
doc.rebuild()
check("and back to millimetres, the same numbers, the old size",
      all(near(a, b, 1e-5) for a, b in zip(size(doc.shape), before))
      and fl.radius == "1 mm", (size(doc.shape), fl.radius))

# ------------------------------------------------------------- an import
print()
print("an imported body, which has no numbers, is scaled instead")
source = os.path.join(WORK, "block.brep")
fileio.write_shape(kernel.box(10, 10, 10), source)
imported = Document()
body = ImportFeature()
body.path = source
imported.add_feature(body)
imported.rebuild()
check("it comes in 10 across", near(size(imported.shape)[0], 10.0, 1e-6))
check("an ordinary import writes no scale",
      "scale" not in body.field_dict())
rescale.keep_numbers(imported, "in")
imported.rebuild()
check("kept as 10, in inches, it is 254 mm across",
      near(size(imported.shape)[0], 254.0, 1e-6), size(imported.shape))
check("its scale is saved", body.field_dict().get("scale") == INCH)
again = Feature.from_dict(body.to_dict())
check("and read back", near(again.scale, INCH))

# ------------------------------------------------------------- the window
print()
print("dProperties has the units, and asks how")
from datum.ui.main_window import MainWindow                        # noqa: E402
from datum.ui.theme import stylesheet                              # noqa: E402

app = QtWidgets.QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(stylesheet())
win = MainWindow()
win.resize(1400, 900)
win.show()
app.processEvents()

win.new_document(prompt=False)
app.processEvents()
part = win.document
sketch = SketchFeature()
sketch.sketch = Sketch(STANDARD_PLANES["XY"], "Sketch1")
sketch.sketch.add_rectangle((0, 0), (20, 10))
part.add_feature(sketch)
extrude = ExtrudeFeature()
extrude.sketch_id = sketch.id
extrude.distance = "4"
extrude.profiles.add(sketch.id, (10.0, 5.0))
part.add_feature(extrude)
win.rebuild()
start = size(part.shape)

answers = []
win._ask_unit_change = lambda document, old, new: answers.pop(0)
win.show_properties()
app.processEvents()
combo = win.doc_properties.units_combo
check("dProperties is what the window is called",
      win.properties_window.windowTitle() == "dProperties")
check("it shows the part's units", combo.currentData() == "mm",
      combo.currentData())


def pick(unit):
    combo.setCurrentIndex(combo.findData(unit))
    combo.activated.emit(combo.findData(unit))
    app.processEvents()


answers.append("keep")
pick("in")
check("keeping the numbers puts the part in inches",
      part.units == "in" and combo.currentData() == "in"
      and win.status_units.text() == "in")
check("rescaled 25.4 times",
      near(size(part.shape)[0], start[0] * INCH, 1e-6), size(part.shape))

answers.append("convert")
pick("mm")
check("converting puts it back in millimetres",
      part.units == "mm" and combo.currentData() == "mm")
check("without moving anything",
      near(size(part.shape)[0], start[0] * INCH, 1e-6), size(part.shape))

answers.append(None)
pick("cm")
check("cancelling changes nothing, and the box says so",
      part.units == "mm" and combo.currentData() == "mm",
      (part.units, combo.currentData()))

answers.append("convert")
win._unit_actions["in"].trigger()
app.processEvents()
check("the status bar asks the same question",
      part.units == "in" and not answers and combo.currentData() == "in")

labels = {b.text() for b in win.findChildren(QtWidgets.QAbstractButton)}
check("the ribbon says dProperties",
      any(t.replace("\n", " ") == "dProperties" for t in labels)
      and "Properties" not in labels, sorted(t for t in labels
                                             if "Prop" in t))

print()
print("FAILED: " + ", ".join(FAILS) if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
