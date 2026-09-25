"""The material and appearance pickers, and the browser behind them."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import harness  # noqa: E402,F401

os.environ["DATUM_SETTINGS_ORG"] = "IITEG-matui-tests"

from PySide6 import QtWidgets                                  # noqa: E402

from datum.core import materials                               # noqa: E402
from datum.core.features import PrimitiveFeature               # noqa: E402
from datum.core.materials import Appearance, Material          # noqa: E402
from datum.ui.main_window import MainWindow                    # noqa: E402
from datum.ui.materials_ui import (                            # noqa: E402
    MaterialBrowser, MaterialEditor, swatch,
)
from datum.ui.theme import stylesheet                          # noqa: E402

FAILS = []
WORK = tempfile.mkdtemp(prefix="datum_matui_")

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


def pump(n=5):
    for _ in range(n):
        app.processEvents()


def panel():
    win.new_document()
    pump(4)
    f = PrimitiveFeature()
    f.kind, f.a, f.b, f.c, f.operation = "box", "300", "160", "12", "new"
    win.document.add_feature(f)
    win.document.rebuild()
    win.rebuild()
    pump(5)
    return win.document


def mass():
    return win.document.mass_properties()["mass_g"]


# ==========================================================================
print("the Properties panel is no longer eating the tree")

check("there is no Properties dock", win.properties_dock is None)
check("but the panel still exists, so everything feeding it still works",
      win.properties is not None)
win.show_properties()
pump(3)
check("and Properties opens as a window when asked for",
      win.properties_window.isVisible())
win.properties_window.hide()


# ==========================================================================
print("the pickers are on the top strip")

bar = win.material_bar
check("the bar is there", bar is not None)
check("it lists every material", bar.material.count()
      == len(materials.library().materials), bar.material.count())
check("and every appearance plus the inherit entry",
      bar.appearance.count() == len(materials.library().appearances) + 1,
      bar.appearance.count())
check("the first appearance entry is the inherit one",
      bar.appearance.itemData(0) == "", bar.appearance.itemData(0))


# ==========================================================================
print("picking a material changes what the part weighs")

doc = panel()
bar.set_document(doc)
pump(3)
check("the bar woke up for a part", bar.isEnabled())

win.set_material("Plywood, Birch")
pump(4)
check("the document took it", doc.material == "Plywood, Birch", doc.material)
check("the density came from the library",
      abs(doc.density - 0.68) < 1e-9, doc.density)
check("and the mass followed", abs(mass() - 391.7) < 1.0, mass())

win.set_material("Steel, Mild")
pump(4)
check("switching to steel moves it again", abs(mass() - 4521.6) < 2.0,
      mass())
check("and it looks like steel now",
      doc.material_appearance.name == "Steel, Mill Finish",
      doc.material_appearance.name)


# ==========================================================================
print("picking an appearance changes nothing about the mass")

before = mass()
win.set_appearance("Paint, Gloss White")
pump(4)
check("the override took", doc.appearance == "Paint, Gloss White",
      doc.appearance)
check("it is drawn as the paint",
      doc.material_appearance.name == "Paint, Gloss White",
      doc.material_appearance.name)
check("the mass did not move at all", abs(mass() - before) < 1e-9,
      (before, mass()))
check("and it is still made of steel", doc.material == "Steel, Mild")

print("going back to inherit gives the material's own look")
win.set_appearance("")
pump(4)
check("the override is gone", doc.appearance == "")
check("and it looks like steel again",
      doc.material_appearance.name == "Steel, Mill Finish",
      doc.material_appearance.name)


# ==========================================================================
print("it all survives the file")

path = win.document.save(os.path.join(WORK, "panel.pdat"))
win.set_material("Brass")
win.set_appearance("Powder Coat, RAL 3020")
pump(3)
path2 = win.document.save(os.path.join(WORK, "brass.pdat"))
win.open_path(path2)
pump(8)
check("the material came back", win.document.material == "Brass",
      win.document.material)
check("and the appearance override", win.document.appearance
      == "Powder Coat, RAL 3020", win.document.appearance)
check("the bar shows what was opened",
      win.material_bar.material.currentData() == "Brass",
      win.material_bar.material.currentData())


# ==========================================================================
print("the browser lists, searches and filters")

browser = MaterialBrowser(win, appearance=False)
check("it lists the materials",
      browser.list.count() == len(materials.library().materials),
      browser.list.count())
browser.search.setText("plast")
pump(2)
check("searching narrows it", 0 < browser.list.count()
      < len(materials.library().materials), browser.list.count())
browser.search.setText("")
pump(2)

index = browser.category.findData("Wood")
browser.category.setCurrentIndex(index)
pump(2)
woods = browser.list.count()
check("filtering by category narrows it too", 0 < woods < 18, woods)
browser.category.setCurrentIndex(0)
pump(2)

print("built-ins can be viewed but not edited or deleted")
browser.select("Steel, Mild")
pump(2)
check("Edit becomes View", browser.edit_button.text() == "View...",
      browser.edit_button.text())
check("and Delete is off", not browser.delete_button.isEnabled())

print("an appearance browser is a separate list")
looks = MaterialBrowser(win, appearance=True)
check("it lists appearances",
      looks.list.count() == len(materials.library().appearances),
      looks.list.count())


# ==========================================================================
print("new materials and appearances can be made and are kept")

library = materials.library()
library.path = os.path.join(WORK, "materials.json")

made = library.put_material(Material("Titanium Ti-6Al-4V", "Metal", 4.43,
                                     "Steel, Polished"))
library.put_appearance(Appearance("IITEG Silver", "Plastic", "#c2ccd8",
                                  0.35, False))
library.save_user()
check("the user file was written", os.path.exists(library.path))

bar.refresh()
pump(2)
check("the new material shows in the picker",
      bar.material.findData("Titanium Ti-6Al-4V") >= 0)
check("and the new appearance too",
      bar.appearance.findData("IITEG Silver") >= 0)

print("and a part can be made of it")
panel()
win.set_material("Titanium Ti-6Al-4V")
pump(4)
check("the density is the new one", abs(win.document.density - 4.43) < 1e-9,
      win.document.density)
check("the mass follows", abs(mass() - 2551.7) < 2.0, mass())


# ==========================================================================
print("the editor round-trips a material through its fields")

editor = MaterialEditor(win, library.material("ABS"), appearance=False,
                        read_only=True)
check("a built-in opens read only", not editor.density.isEnabled())
back = editor.result_item()
check("and reads back unchanged", abs(back.density - 1.04) < 1e-9,
      back.density)

editor = MaterialEditor(win, made, appearance=False)
editor.density.setValue(4.50)
edited = editor.result_item()
check("an edit comes back through the fields",
      abs(edited.density - 4.50) < 1e-9, edited.density)
check("keeping its name", edited.name == "Titanium Ti-6Al-4V", edited.name)

look = MaterialEditor(win, library.appearance("Brass"), appearance=True,
                      read_only=True)
made_look = look.result_item()
check("an appearance round-trips its colour",
      made_look.colour.lower() == library.appearance("Brass").colour.lower(),
      made_look.colour)
check("and its metallic flag", made_look.metallic)


# ==========================================================================
print("swatches are drawn, not stored")

px = swatch(library.appearance("Brass"))
check("a swatch has pixels", not px.isNull() and px.width() > 0)
check("and follows the colour it is given",
      swatch(Appearance("x", "y", "#ff0000")).toImage()
      != swatch(Appearance("x", "y", "#00ff00")).toImage())


# ==========================================================================
print()
if FAILS:
    print("%d FAILED" % len(FAILS))
    for name in FAILS:
        print("   - %s" % name)
    sys.exit(1)
print("all material UI checks passed")
