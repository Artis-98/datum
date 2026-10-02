"""The Bill of Materials of an assembly.

Model Data lists what is placed in the assembly directly, parts and
sub-assemblies; Parts Only lists every part, counted right through the
sub-assemblies.  Every dProperties field can be a column, picked from a
list and kept with the assembly, and what is typed in the table goes back
into the parts: an open one in memory, a closed one into its file, its
thumbnail and all else left as it was.
"""

import os
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import harness  # noqa: E402

os.environ["DATUM_SETTINGS_ORG"] = "IITEG-tests"

from PySide6 import QtWidgets                                      # noqa: E402

from datum.core import bom, fileformat                             # noqa: E402
from datum.core.assembly import AssemblyDocument                   # noqa: E402
from datum.core.document import Document                           # noqa: E402
from datum.core.features import PrimitiveFeature                   # noqa: E402
from datum.ui import bom_ui                                        # noqa: E402
from datum.ui.main_window import MainWindow                        # noqa: E402
from datum.ui.theme import stylesheet                              # noqa: E402

FAILS = []
WORK = tempfile.mkdtemp(prefix="datum_bom_")


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def near(a, b, tol=1e-3):
    return abs(a - b) <= tol


def part(name, kind, a, b, c, properties, material="Generic"):
    doc = Document()
    doc.add_feature(PrimitiveFeature(kind=kind, a=a, b=b, c=c))
    doc.properties = dict(properties)
    doc.material = material
    doc.rebuild()
    path = os.path.join(WORK, name + ".pdat")
    doc.save(path, thumbnail=b"\x89PNG not really, but kept as it is")
    return path


bracket = part("Bracket", "box", "40", "20", "10",
               {"PartNumber": "BR-100", "Description": "Bracket"})
bolt = part("Bolt", "cylinder", "4", "30", "0",
            {"PartNumber": "M8x30", "Description": "Bolt",
             "Vendor": "Würth"})

sub = AssemblyDocument()
sub.place(bolt)
sub.place(bolt)
SUB = os.path.join(WORK, "Bolt pair.adat")
sub.save(SUB)

top = AssemblyDocument()
top.place(bracket)
top.place(SUB)
top.place(SUB)
top.place(bolt)
TOP = os.path.join(WORK, "Frame.adat")
top.save(TOP)

app = QtWidgets.QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(stylesheet())
win = MainWindow()
win.resize(1300, 850)
win.show()
app.processEvents()

win.open_path(TOP)
app.processEvents()
check("the assembly is open", win.assembly is not None)
assembly = win.assembly

print("the two tabs")
dialog = bom_ui.BomDialog(win, assembly, win.session, assembly.library)
model = dialog.rows[bom_ui.MODEL_DATA]
parts = dialog.rows[bom_ui.PARTS_ONLY]
names = {r.part_number: r.quantity for r in model}
check("Model Data: what is placed directly, counted",
      names == {"BR-100": 1, "Bolt pair": 2, "M8x30": 1}, names)
names = {r.part_number: r.quantity for r in parts}
check("Parts Only: every part, through the sub-assemblies",
      names == {"BR-100": 1, "M8x30": 5}, names)
check("both tabs are there", [dialog.tabs.tabText(i) for i in range(2)]
      == ["Model Data", "Parts Only"])

print()
print("the columns")
headings = [dialog.tables[bom_ui.PARTS_ONLY].horizontalHeaderItem(i).text()
            for i in range(dialog.tables[bom_ui.PARTS_ONLY].columnCount())]
check("the usual ones to start with", headings == [
    "Item", "Thumbnail", "Part Number", "QTY", "Description", "Material",
    "Mass"], headings)
offered = [bom_ui.heading(c) for c in dialog.available()]
check("every dProperties field can be one", all(
    label in offered for _key, label in bom_ui.DocumentProperties.FIELDS))
check("and a part's own property too", "Vendor" in offered, offered)
dialog.show_column(bom.PROP + "Vendor", True)
dialog.show_column(bom_ui.MASS, False)
table = dialog.tables[bom_ui.PARTS_ONLY]
headings = [table.horizontalHeaderItem(i).text()
            for i in range(table.columnCount())]
check("a column picked shows, one dropped goes",
      "Vendor" in headings and "Mass" not in headings, headings)
check("the choice is kept with the assembly",
      bom.PROP + "Vendor" in assembly.bom_columns
      and bom_ui.MASS not in assembly.bom_columns)
dialog.show_column(bom_ui.MASS, True)

bolt_row = next(r for r in parts if r.part_number == "M8x30")
grams = bom.mass_grams(bolt, assembly.library)
volume = 3.141592653589793 * 4 * 4 * 30
check("a part's mass is its volume times its density",
      grams is not None and near(grams, volume / 1000.0, 0.05), grams)
pair_row = next(r for r in model if r.part_number == "Bolt pair")
check("a sub-assembly weighs what its parts do",
      near(bom.mass_grams(pair_row.path, assembly.library), 2 * grams, 0.1))
check("shown in the Mass column", dialog.value(bolt_row, bom_ui.MASS)
      .endswith(" g"), dialog.value(bolt_row, bom_ui.MASS))

print()
print("typed in the table, kept in the part")
column = dialog.columns.index(bom.PROP + "Description")
row_index = parts.index(bolt_row)
table.item(row_index, column).setText("Hex bolt M8 x 30")
check("the change shows in both tabs", dialog.value(
    next(r for r in model if r.part_number == "M8x30"),
    bom.PROP + "Description") == "Hex bolt M8 x 30")
dialog.set_value(bracket, "StockNumber", "ST-7")
before = zipfile.ZipFile(bolt).read(fileformat.THUMBNAIL_NAME)
written = dialog.apply()
check("closed parts are written to their files",
      sorted(written) == sorted([bolt, bracket]), written)
saved = fileformat.read(bolt)
check("the bolt's file now says so",
      saved.geometry["properties"].get("Description") == "Hex bolt M8 x 30"
      and saved.geometry["properties"].get("Vendor") == "Würth",
      saved.geometry["properties"])
check("and the rest of the file is as it was",
      zipfile.ZipFile(bolt).read(fileformat.THUMBNAIL_NAME) == before)
check("the bracket got its stock number",
      fileformat.read(bracket).geometry["properties"].get("StockNumber")
      == "ST-7")

print()
print("an open part is changed where it is open")
win.open_path(bracket)
app.processEvents()
open_doc = win.document
win.open_path(TOP)
app.processEvents()
dialog = bom_ui.BomDialog(win, win.assembly, win.session,
                          win.assembly.library)
dialog.set_value(bracket, "Description", "Bracket, folded")
written = dialog.apply()
check("not written behind its back", bracket not in written, written)
check("but changed in the open document, to be saved with it",
      open_doc.properties.get("Description") == "Bracket, folded"
      and open_doc.modified)

print()
print("exported")
out = os.path.join(WORK, "bom.csv")
dialog.write_csv(out, bom_ui.PARTS_ONLY)
text = open(out, encoding="utf-8-sig").read()
check("as a CSV of the columns shown", text.startswith("Item,Part Number,QTY")
      and "M8x30,5," in text, text[:120])

print()
print("the button")
button = next((b for b in win.findChildren(QtWidgets.QAbstractButton)
               if b.text().replace("\n", " ") == "Bill of Materials"), None)
check("Bill of Materials is on the assembly ribbon", button is not None)

print()
print("FAILED: " + ", ".join(FAILS) if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
