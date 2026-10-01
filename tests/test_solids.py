"""Choosing the solids a feature works on, and patterning whole solids.

With more than one solid in a part, a cut or a join can be told which
solids it is for: a cut through two at once, a join that fuses several.
Nothing chosen keeps the old behaviour, the solid the material meets.
A pattern can copy whole solids, everything done to them included, either
joined into each or as new solids of their own.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import harness  # noqa: E402,F401

os.environ["DATUM_SETTINGS_ORG"] = "IITEG-tests"

from datum.core import kernel                                      # noqa: E402
from datum.core.document import Document                           # noqa: E402
from datum.core.features import (                                  # noqa: E402
    CUT, INTERSECT, JOIN, NEW_BODY, PatternFeature, PrimitiveFeature,
)

FAILS = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def near(a, b, tol=1e-3):
    return abs(a - b) < tol


def block(doc, x, y, z, dx, dy, dz, op=JOIN, name="", solids=()):
    feature = PrimitiveFeature()
    feature.kind = "box"
    feature.a, feature.b, feature.c = str(dx), str(dy), str(dz)
    feature.origin = (str(x), str(y), str(z))
    feature.operation = op
    feature.body_name = name
    feature.solids = list(solids)
    doc.add_feature(feature)
    return feature


def volumes(doc):
    return {b.name: kernel.volume(b.shape) for b in doc.bodies if b.valid}


def two_blocks():
    doc = Document()
    block(doc, 0, 0, 0, 40, 40, 20, name="Left")
    block(doc, 100, 0, 0, 40, 40, 20, op=NEW_BODY, name="Right")
    return doc


# ==========================================================================
print("a cut through two solids")
doc = two_blocks()
slot = block(doc, -10, 15, 10, 160, 10, 20, op=CUT)
report = doc.rebuild()
v = volumes(doc)
check("left alone, a cut takes from the solid it meets first",
      report.ok and near(v["Left"], 40 * 40 * 20 - 40 * 10 * 10)
      and near(v["Right"], 40 * 40 * 20), v)
slot.solids = ["Left", "Right"]
report = doc.rebuild()
v = volumes(doc)
check("told both, it cuts both", report.ok
      and near(v["Left"], 32000 - 4000) and near(v["Right"], 32000 - 4000), v)
slot.solids = ["Right"]
doc.rebuild()
v = volumes(doc)
check("told one, only that one", near(v["Left"], 32000)
      and near(v["Right"], 28000), v)

print()
print("a join that fuses several")
doc = two_blocks()
bridge = block(doc, 30, 10, 0, 80, 20, 10, op=JOIN,
               solids=["Left", "Right"])
report = doc.rebuild()
v = volumes(doc)
check("the solids and the new material become one", report.ok
      and list(v) == ["Left"]
      and near(v["Left"], 2 * 32000 + 80 * 20 * 10 - 2 * 10 * 20 * 10), v)

print()
print("intersecting each")
doc = two_blocks()
keep = block(doc, -10, 0, 0, 160, 20, 20, op=INTERSECT,
             solids=["Left", "Right"])
doc.rebuild()
v = volumes(doc)
check("each solid keeps only what lies inside the new one",
      near(v["Left"], 40 * 20 * 20) and near(v["Right"], 40 * 20 * 20), v)

print()
print("a solid that is gone")
doc = two_blocks()
gone = block(doc, 0, 0, 0, 10, 10, 10, op=CUT, solids=["Middle"])
report = doc.rebuild()
check("is an error that names it", not report.ok
      and "Middle" in (gone.error or ""), gone.error)

print()
print("which solids a feature could choose from")
doc = two_blocks()
later = block(doc, 0, 0, 30, 10, 10, 10, op=NEW_BODY, name="Top")
doc.rebuild()
check("only the ones there before it",
      doc.bodies_before(later.id) == ["Left", "Right"],
      doc.bodies_before(later.id))
check("and none before the first", doc.bodies_before(doc.features[0].id)
      == [], doc.bodies_before(doc.features[0].id))

# ==========================================================================
print()
print("patterning whole solids")
doc = Document()
block(doc, 0, 0, 0, 20, 20, 10, name="Lug")
block(doc, 0, 50, 0, 20, 20, 10, op=NEW_BODY, name="Pad")
row = PatternFeature(name="Row")
row.of = "bodies"
row.bodies = ["Lug"]
row.count1 = "3"
row.spacing1 = "40"
row.dir1 = "X"
doc.add_feature(row)
report = doc.rebuild()
v = volumes(doc)
check("joined, the copies become part of the solid", report.ok
      and near(v["Lug"], 3 * 4000) and near(v["Pad"], 4000), v)
check("the pattern does not consume a feature",
      row.depends_on() == [], row.depends_on())
row.copies = NEW_BODY
report = doc.rebuild()
v = volumes(doc)
check("as new solids, each copy is one of its own, named for the original",
      report.ok and sorted(v) == ["Lug", "Lug (2)", "Lug (3)", "Pad"]
      and near(v["Lug"], 4000), sorted(v))
box = kernel.bounding_box(doc.bodies[-1].shape)
check("  in its place", near(box[0], 80.0), box)
row.bodies = ["Lug", "Gone"]
report = doc.rebuild()
check("a solid that is gone is an error",
      not report.ok and "Gone" in (row.error or ""), row.error)
row.bodies = ["Lug"]

path = os.path.join(tempfile.mkdtemp(prefix="datum_solids_"), "s.pdat")
doc.save(path)
again = Document.load(path)
pattern = next(f for f in again.features if f.type_name == "pattern")
check("all of it is saved", pattern.of == "bodies"
      and pattern.bodies == ["Lug"] and pattern.copies == NEW_BODY)

# ==========================================================================
print()
print("in the dialogs")

from PySide6 import QtCore, QtWidgets                              # noqa: E402

from datum.ui.main_window import MainWindow                        # noqa: E402
from datum.ui.theme import stylesheet                              # noqa: E402

app = QtWidgets.QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(stylesheet())
win = MainWindow()
win.resize(1400, 900)
win.show()
app.processEvents()


def pump(n=3):
    for _ in range(n):
        app.processEvents()


win.new_document(prompt=False)
block(win.document, 0, 0, 0, 40, 40, 20, name="Left")
block(win.document, 100, 0, 0, 40, 40, 20, op=NEW_BODY, name="Right")
win.rebuild()
win.new_primitive("box")
pump()
dialog = win._active_dialog
check("a part with two solids is asked which", dialog.solids is not None
      and dialog.solids.count() == 2)
dialog.operation.set_value(NEW_BODY)
dialog._sync_solids()
check("but not for a new body, which works on none",
      not dialog.solids.isVisibleTo(dialog))
dialog.operation.set_value(CUT)
dialog._sync_solids()
check("and again for a cut", dialog.solids.isVisibleTo(dialog))
for i in range(dialog.solids.count()):
    dialog.solids.item(i).setCheckState(QtCore.Qt.Checked)
dialog.store()
check("what is ticked is what it works on",
      dialog.feature.solids == ["Left", "Right"], dialog.feature.solids)
dialog.cancel()
pump()

win.new_feature(PatternFeature, mode="rectangular")
pump()
dialog = win._active_dialog
dialog.of.setCurrentIndex(dialog.of.findData("bodies"))
pump()
check("a pattern of solids lists the solids, not the features",
      dialog.bodies.isVisibleTo(dialog)
      and not dialog.parents.isVisibleTo(dialog)
      and dialog.bodies.count() == 2)
dialog.cancel()
pump()

print()
print("FAILED: " + ", ".join(FAILS) if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
