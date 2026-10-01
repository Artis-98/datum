"""dProperties: the custom half of a part's Properties window.

A property is only worth typing if it turns up where the part does.  So
this follows one from the window, into the file, back out of it, into a
parts list column and into a title block field.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WORK = tempfile.mkdtemp(prefix="datum_dprops_")
os.environ["DATUM_CONFIG_DIR"] = WORK
os.environ["DATUM_SETTINGS_ORG"] = "IITEG-dprops-tests"

import harness  # noqa: E402,F401

from PySide6 import QtWidgets                                  # noqa: E402

from datum.core import bom                                     # noqa: E402
from datum.core.assembly import AssemblyDocument               # noqa: E402
from datum.core.document import Document                       # noqa: E402
from datum.core.views import model_properties                  # noqa: E402
from datum.ui.panels import DocumentProperties                 # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

print("the window")
part = Document()
part.properties = {"PartNumber": "BRK-01", "Vendor": "Steel Ltd",
                   "Finish": "Galvanised"}
panel = DocumentProperties()
panel.update_from(part)
check("a fixed field shows in the form",
      panel.edits["PartNumber"].text() == "BRK-01")
check("so does one of the new fixed fields",
      panel.edits["Vendor"].text() == "Steel Ltd")
check("a custom one shows in the table, and only there",
      panel.custom.rowCount() == 1
      and panel.custom.item(0, 0).text() == "Finish",
      panel.custom.rowCount())

panel.add_custom()
check("Add makes a row with a unique name",
      "Property1" in part.properties, part.properties)
panel.custom.item(1, 0).setText("Supplier code")
panel.custom.item(1, 1).setText("SC-77")
check("renaming it renames the property",
      part.properties.get("Supplier code") == "SC-77"
      and "Property1" not in part.properties, part.properties)
check("and marks the document changed", part.modified)

panel.custom.item(1, 0).setText("PartNumber")
check("a custom name cannot shadow a fixed field",
      part.properties.get("PartNumber") == "BRK-01", part.properties)
panel.custom.item(1, 0).setText("Supplier code")

panel.edits["PartNumber"].setText("BRK-02")
panel._write()
check("editing the form keeps the custom ones",
      part.properties.get("PartNumber") == "BRK-02"
      and part.properties.get("Finish") == "Galvanised", part.properties)

panel.custom.setCurrentCell(0, 0)
panel.custom.selectRow(0)
panel.remove_custom()
check("Remove takes it out of the document",
      "Finish" not in part.properties
      and "Supplier code" in part.properties, part.properties)

print()
print("the file")
path = os.path.join(WORK, "bracket.pdat")
part.save(path)
again = Document.load(path)
check("custom properties survive a save and a load",
      again.properties.get("Supplier code") == "SC-77"
      and again.properties.get("Vendor") == "Steel Ltd", again.properties)

print()
print("the parts list")
rows = bom.rows_for(again)
check("a row carries every property of its part",
      rows[0].extra.get("Supplier code") == "SC-77", rows[0].extra)
check("a prop: column reads one",
      rows[0].value("prop:Vendor") == "Steel Ltd")
check("and one the part does not have is blank, not an error",
      rows[0].value("prop:Colour") == "")
check("the heading is the property's name",
      bom.heading("prop:Vendor") == "VENDOR")
check("the fixed headings are unchanged",
      bom.heading("part_number") == "PART NUMBER")
check("the names on offer come from the rows",
      "Supplier code" in bom.property_names(rows), bom.property_names(rows))
check("a row with properties survives its own round trip",
      bom.Row.from_dict(rows[0].to_dict()).extra == rows[0].extra)

assembly = AssemblyDocument()
assembly.path = os.path.join(WORK, "frame.adat")
assembly.place(path)
assembly.place(path)
listed = bom.rows_for(assembly, WORK)
check("an assembly's parts list reads it from the part file",
      len(listed) == 1 and listed[0].quantity == 2
      and listed[0].value("prop:Supplier code") == "SC-77",
      [(r.quantity, r.extra) for r in listed])

print()
print("the title block")
fields = model_properties(path, None, again)
check("{Model.Vendor} resolves", fields.get("Model.Vendor") == "Steel Ltd",
      fields)
check("and so does a custom one",
      fields.get("Model.Supplier code") == "SC-77")
check("the named fields still win",
      fields.get("Model.PartNumber") == "BRK-02", fields)

panel.deleteLater()
print()
print("FAILED: " + ", ".join(FAILS) if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
