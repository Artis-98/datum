"""Every dimension and every feature value is a parameter of its own.

Inventor's table has model parameters, made by modelling, and user
parameters, made by name.  Here both are one namespace: d-numbers run
across the whole part, any of them can be written in terms of any other,
and the table edits the value where it lives, so the table and the sketch
cannot disagree.  A part from when every sketch counted from d1 is renamed
as it opens, each sketch's expressions following its own dimensions.
"""
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import harness  # noqa: E402,F401

os.environ["DATUM_SETTINGS_ORG"] = "IITEG-tests"

from datum.core import kernel, modelparams, rules                 # noqa: E402
from datum.core.document import Document                          # noqa: E402
from datum.core.features import (                                 # noqa: E402
    ExtrudeFeature, FilletFeature, SketchFeature,
)
from datum.core.params import ExpressionError                     # noqa: E402
from datum.core.sketch import STANDARD_PLANES, Sketch             # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + ("  " + str(extra) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def near(a, b, tol=1e-3):
    return abs(a - b) < tol


def plate_sketch(name, width, depth):
    """A rectangle with its corner pinned and two dimensions on it."""
    feature = SketchFeature(name=name)
    feature.sketch = Sketch(STANDARD_PLANES["XY"], name)
    s = feature.sketch
    ids = s.add_rectangle((0, 0), (width, depth))
    corner = s.entities[ids[0]].points
    s.points[corner[0]].fixed = True
    s.add_constraint("distance_x", points=[corner[0], corner[1]],
                     value=float(width))
    left = s.entities[ids[3]].points
    s.add_constraint("distance_y", points=[left[1], left[0]],
                     value=float(depth))
    return feature


def names_of(doc):
    return [m.name for m in doc.model_parameters()]


# ==========================================================================
print("every dimension and feature value gets a name")

doc = Document()
check("a new document has no parameters", len(doc.params) == 0)
sk = plate_sketch("Plate", 60, 40)
doc.add_feature(sk)
ex = ExtrudeFeature(name="Extrude")
ex.sketch_id = sk.id
ex.distance = "8"
doc.add_feature(ex)
report = doc.rebuild()
check("it builds", report.ok, report.message)
model = {m.name: m for m in doc.model_parameters()}
check("the sketch's two dimensions are d1 and d2",
      model.get("d1") and model["d1"].owner == "Plate"
      and model.get("d2") and model["d2"].owner == "Plate", sorted(model))
check("the extrude's distance and taper follow on",
      model.get("d3") and model["d3"].attr == "distance"
      and model.get("d4") and model["d4"].attr == "taper",
      [(m.name, m.owner, m.attr) for m in model.values()])
check("each knows its value", near(model["d1"].value, 60)
      and near(model["d3"].value, 8), (model["d1"].value, model["d3"].value))

other = plate_sketch("Lid", 20, 10)
doc.add_feature(other)
lid_names = sorted(c.name for c in other.sketch.constraints.values()
                   if c.name)
check("a second sketch does not start again at d1",
      "d1" not in lid_names and "d2" not in lid_names, lid_names)
check("no two parameters share a name",
      len(names_of(doc)) == len(set(names_of(doc))), names_of(doc))

through = ExtrudeFeature(name="Through")
through.sketch_id = other.id
through.extent = "through_all"
doc.add_feature(through)
check("a value its feature does not use is not a row",
      not any(m.owner == "Through" for m in doc.model_parameters()))
through.extent = "distance"
modelparams.assign_names(doc)
check("and becomes one when it is used again",
      any(m.owner == "Through" and m.attr == "distance"
          for m in doc.model_parameters()))
doc.remove_feature(through.id)
doc.remove_feature(other.id)

# ==========================================================================
print()
print("one namespace")

doc.set_parameter("d3", "d1 / 4")
report = doc.rebuild()
check("a feature value can be written in terms of a dimension",
      report.ok and near(kernel.bounding_box(doc.shape)[5], 15.0),
      report.message)
doc.set_parameter("d1", "80")
report = doc.rebuild()
box = kernel.bounding_box(doc.shape)
check("and follows it when the dimension changes",
      near(box[3], 80.0) and near(box[5], 20.0), box)
check("a plain number goes in as the dimension's value",
      sk.sketch.constraints[next(c for c in sk.sketch.constraints
                                 if sk.sketch.constraints[c].name == "d1")]
      .expression == "")

doc.params.add("wall", "d2 / 8")
check("a user parameter can read a model one",
      near(doc.params["wall"].value, 5.0) and not doc.params["wall"].error,
      (doc.params["wall"].value, doc.params["wall"].error))
doc.set_parameter("d2", "wall * 10")
check("and a model one a user one, round in a circle, is caught",
      doc.params["wall"].error == "circular reference"
      and any(m.name == "d2" and m.error == "circular reference"
              for m in doc.model_parameters()),
      doc.params["wall"].error)
doc.set_parameter("d2", "40")
doc.params.set_expression("wall", "5")
report = doc.rebuild()
check("and put right again builds", report.ok, report.message)

fillet = FilletFeature(name="Round")
fillet.radius = "d3 / 4"
doc.add_feature(fillet)
radius_name = fillet.param_names.get("radius")
check("a fillet's radius is a parameter too", bool(radius_name),
      fillet.param_names)
doc.rename_parameter("d3", "height")
check("renaming carries to every expression that used it",
      ex.param_names["distance"] == "height"
      and fillet.radius == "height / 4", (ex.param_names, fillet.radius))
try:
    doc.rename_parameter("height", "d1")
    refused = False
except ExpressionError:
    refused = True
check("a name already in use is refused", refused)
doc.remove_feature(fillet.id)

# ==========================================================================
print()
print("driven dimensions are measurements")

sketch = sk.sketch
corner = sketch.points[min(sketch.points)]
measured = sketch.add_constraint(
    "distance", points=list(sketch.entities[min(sketch.entities)].points),
    value=0.0)
sketch.constraints[measured].driving = False
modelparams.assign_names(doc)
doc.rebuild()
driven = next(m for m in doc.model_parameters()
              if m.constraint is sketch.constraints[measured])
check("a driven dimension is listed as a reference", driven.reference)
try:
    driven.set_expression("10")
    refused = False
except ExpressionError:
    refused = True
check("and cannot be set", refused)
del sketch.constraints[measured]

# ==========================================================================
print()
print("a part from when every sketch counted from d1")

old = Document()
old.params.add("d1", "50", comment="example parameter")
first = plate_sketch("A", 30, 20)
second = plate_sketch("B", 10, 6)
data = old.to_dict()
data["features"] = [dict(first.to_dict(), id=1), dict(second.to_dict(), id=2)]
# the second sketch's height was written in terms of its own width
for c in data["features"][1]["sketch"]["constraints"]:
    if c["name"] == "d2":
        c["expression"] = "d1 * 2"
migrated = Document()
migrated.load_dict(data)
names = names_of(migrated)
check("every name is unique after opening it",
      len(names) == len(set(names)) and "d1" not in names, names)
check("the user parameter keeps its own name and value",
      "d1" in migrated.params and migrated.params["d1"].expression == "50")
b = migrated.features[1].sketch
b_names = {c.name: c for c in b.constraints.values() if c.name}
twice = next(c for c in b_names.values() if c.expression)
check("a sketch's expressions follow its own renamed dimensions",
      twice.expression in ("%s * 2" % n for n in b_names) and "d1" not in
      twice.expression, twice.expression)
report = migrated.rebuild()
values = {m.name: m.value for m in migrated.model_parameters()}
width_b = next(n for n, c in b_names.items() if c.kind == "distance_x")
check("and it still means what it meant: twice that sketch's width",
      near(values[twice.name], 2 * values[width_b]), values)

# ==========================================================================
print()
print("they survive a save, and rules can drive them")

ex.param_comments["distance"] = "plate thickness"
path = os.path.join(tempfile.mkdtemp(prefix="datum_mparams_"), "p.pdat")
doc.save(path)
again = Document.load(path)
again_ex = next(f for f in again.features if f.type_name == "extrude")
check("names come back", again_ex.param_names.get("distance") == "height",
      again_ex.param_names)
check("comments come back",
      again_ex.param_comments.get("distance") == "plate thickness")
again.rules.trusted = True
got = rules.run(rules.Rule(name="Thick", source="params.height = 12"), again)
check("a rule sets a model parameter by name",
      got.ok and again_ex.distance == "12.0", got.error or again_ex.distance)
got = rules.run(rules.Rule(name="Read", source="log(params.d1)"), again)
check("and reads a sketch dimension the same way",
      got.ok and "80" in got.output, got.error or got.output)
same = rules.run(rules.Rule(name="Same", source="params.height = 12"), again)
check("writing the same value again is not a change",
      same.ok and not same.changed, same.changed)

# ==========================================================================
print()
print("the table")

from PySide6 import QtWidgets                                     # noqa: E402

from datum.ui.panels import (                                     # noqa: E402
    EXPRESSION, NAME, VALUE, ParametersDialog,
)
from datum.ui.theme import stylesheet                             # noqa: E402

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
app.setStyleSheet(stylesheet())
dialog = ParametersDialog(again)
rows = {dialog.table.item(r, NAME).text(): r
        for r in range(dialog.table.rowCount())
        if dialog.table.item(r, NAME) is not None}
check("model and user parameters are both listed",
      dialog.row_of("d1") >= 0 and dialog.row_of("height") >= 0
      and dialog.row_of("wall") >= 0)
check("under their own headings",
      any(t.startswith("Model Parameters") for t in rows)
      and any(t.startswith("User Parameters") for t in rows), list(rows))
row = dialog.row_of("d1")
check("a dimension's value is the sketch's own",
      dialog.table.item(row, VALUE).text() == "80",
      dialog.table.item(row, VALUE).text())
dialog.table.item(row, EXPRESSION).setText("75")
first_sketch = next(f for f in again.features if f.type_name == "sketch")
dim = next(c for c in first_sketch.sketch.constraints.values()
           if c.name == "d1")
check("editing it in the table edits the sketch", near(dim.value, 75.0),
      dim.value)
row = dialog.row_of("height")
dialog.table.item(row, NAME).setText("thickness")
check("renaming it in the table renames it in the part",
      again_ex.param_names.get("distance") == "thickness"
      and dialog.row_of("thickness") >= 0)
dialog.table.setCurrentCell(dialog.row_of("thickness"), NAME)
check("a model parameter cannot be deleted",
      not dialog.del_btn.isEnabled())
dialog.table.setCurrentCell(dialog.row_of("wall"), NAME)
check("a user parameter can", dialog.del_btn.isEnabled())
dialog.close()

print()
print("FAILED: " + ", ".join(FAILS) if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
