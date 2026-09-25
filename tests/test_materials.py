"""Materials and appearances.

The two are deliberately separate: a material is what a part is, an
appearance is what it looks like, and they do not line up one to one. ABS
comes in white, black and natural; "Plastic, White" sits equally well on
ABS, PLA and nylon. Tying colour to material would mean a new material per
shade, and a part's mass would start depending on what colour it was
painted.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datum.core import materials                               # noqa: E402
from datum.core.document import Document                       # noqa: E402
from datum.core.features import PrimitiveFeature               # noqa: E402
from datum.core.materials import (                             # noqa: E402
    Appearance, Material, MaterialLibrary,
)

FAILS = []
WORK = tempfile.mkdtemp(prefix="datum_materials_")


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def panel(material=None, appearance=None):
    """A 300 x 160 x 12 panel, which is 576 cm3."""
    doc = Document()
    f = PrimitiveFeature()
    f.kind, f.a, f.b, f.c, f.operation = "box", "300", "160", "12", "new"
    doc.add_feature(f)
    doc.rebuild()
    if material:
        doc.material = material
    if appearance:
        doc.appearance = appearance
    return doc


# ==========================================================================
print("the library ships with enough to describe a workshop")

lib = MaterialLibrary()
check("there are materials", len(lib.materials) >= 15, len(lib.materials))
check("and more appearances than materials, which is the point",
      len(lib.appearances) > len(lib.materials),
      (len(lib.appearances), len(lib.materials)))
check("sorted into categories",
      {"Metal", "Plastic", "Wood"} <= set(lib.categories()),
      lib.categories())
check("appearances have a Paint category materials do not",
      "Paint" in lib.categories(appearance=True)
      and "Paint" not in lib.categories(), lib.categories(appearance=True))

print("several materials can share one appearance, which is why they split")
users = [m.name for m in lib.materials.values()
         if m.appearance == "Plastic, Natural"]
check("more than one material is 'Plastic, Natural'", len(users) >= 2, users)


# ==========================================================================
print("density decides mass, and comes from the material")

check("a 576 cm3 panel of generic weighs 576 g",
      abs(panel().mass_properties()["mass_g"] - 576.0) < 1.0,
      panel().mass_properties()["mass_g"])
check("the same panel in birch ply weighs 392 g",
      abs(panel("Plywood, Birch").mass_properties()["mass_g"] - 391.7) < 1.0,
      panel("Plywood, Birch").mass_properties()["mass_g"])
check("and in mild steel, 4522 g",
      abs(panel("Steel, Mild").mass_properties()["mass_g"] - 4521.6) < 2.0,
      panel("Steel, Mild").mass_properties()["mass_g"])

print("a material nobody has heard of does not silently weigh 1 g/cm3")
odd = panel()
odd.material = "Unobtanium"
odd.density = 19.3
check("the density set on it stands", abs(odd.density - 19.3) < 1e-9,
      odd.density)


# ==========================================================================
print("appearance follows the material until it is overridden")

steel = panel("Steel, Mild")
check("steel is drawn as its own finish",
      steel.material_appearance.name == "Steel, Mill Finish",
      steel.material_appearance.name)
check("which is metallic", steel.material_appearance.metallic)

painted = panel("Steel, Mild", "Paint, Gloss White")
check("painting it changes how it looks",
      painted.material_appearance.name == "Paint, Gloss White",
      painted.material_appearance.name)
check("and not one gram of what it weighs",
      abs(painted.mass_properties()["mass_g"]
          - steel.mass_properties()["mass_g"]) < 1e-6)
check("it is still made of steel", painted.material == "Steel, Mild")


# ==========================================================================
print("both survive the file")

doc = panel("Aluminium 6061", "Powder Coat, RAL 3020")
path = doc.save(os.path.join(WORK, "panel.pdat"))
back = Document.load(path)
back.rebuild()
check("the material came back", back.material == "Aluminium 6061",
      back.material)
check("the appearance override came back",
      back.appearance == "Powder Coat, RAL 3020", back.appearance)
check("and the mass is the same on the other side",
      abs(back.mass_properties()["mass_g"]
          - doc.mass_properties()["mass_g"]) < 1e-6)

print("a file naming a material this machine lacks is believed, not reset")
stranger = panel()
stranger.material = "Unobtanium"
stranger.density = 19.3
path = stranger.save(os.path.join(WORK, "stranger.pdat"))
back = Document.load(path)
back.rebuild()
check("the name is kept", back.material == "Unobtanium", back.material)
check("and so is the density it was saved with",
      abs(back.density - 19.3) < 1e-9, back.density)
check("while it still draws as something",
      back.material_appearance.name == "Default",
      back.material_appearance.name)


# ==========================================================================
print("the library can be edited, and only the edits are written")

lib = MaterialLibrary()
user_file = os.path.join(WORK, "materials.json")

made = lib.put_material(Material("Titanium Ti-6Al-4V", "Metal", 4.43,
                                 "Steel, Polished",
                                 youngs_modulus=114.0,
                                 yield_strength=880.0))
shade = lib.put_appearance(Appearance("Plastic, IITEG Silver", "Plastic",
                                      "#c2ccd8", 0.35, False))
lib.save_user(user_file)

with open(user_file, encoding="utf-8") as handle:
    written = json.load(handle)
names = [m["name"] for m in written["materials"]]
check("the new material was written", "Titanium Ti-6Al-4V" in names, names)
check("and not the eighteen that ship with it", len(names) == 1, names)
shades = [a["name"] for a in written["appearances"]]
check("same for the appearance", shades == ["Plastic, IITEG Silver"], shades)

print("and it comes back on the next start")
fresh = MaterialLibrary()
fresh.load_user(user_file)
check("the material is there", "Titanium Ti-6Al-4V" in fresh.materials)
check("with its density", abs(fresh.material("Titanium Ti-6Al-4V").density
                              - 4.43) < 1e-9)
check("and the built-ins are still there too",
      "Steel, Mild" in fresh.materials and len(fresh.materials) > 18,
      len(fresh.materials))

print("changing a built-in is written; leaving it alone is not")
fresh2 = MaterialLibrary()
fresh2.material("Steel, Mild").density = 7.80
second = os.path.join(WORK, "changed.json")
fresh2.save_user(second)
with open(second, encoding="utf-8") as handle:
    changed = json.load(handle)
check("the edited built-in was written",
      [m["name"] for m in changed["materials"]] == ["Steel, Mild"],
      [m["name"] for m in changed["materials"]])

print("built-ins cannot be deleted, user entries can")
check("a built-in refuses", not fresh.remove("Steel, Mild"))
check("it is still there", "Steel, Mild" in fresh.materials)
check("a user one goes", fresh.remove("Titanium Ti-6Al-4V"))
check("and is gone", "Titanium Ti-6Al-4V" not in fresh.materials)


# ==========================================================================
print("duplicating gives something safe to edit")

lib = MaterialLibrary()
copy_of = lib.duplicate("ABS")
check("a copy was made", copy_of is not None)
check("with a name of its own", copy_of.name != "ABS", copy_of.name)
check("and the original untouched",
      abs(lib.material("ABS").density - 1.04) < 1e-9)
copy_of.density = 1.10
check("editing the copy leaves the original alone",
      abs(lib.material("ABS").density - 1.04) < 1e-9)


# ==========================================================================
print("searching finds things by name and by category")

lib = MaterialLibrary()
check("by name", any(m.name == "PETG" for m in lib.search("pet")))
check("by category", len(lib.search("metal")) >= 4,
      len(lib.search("metal")))
check("an empty search is everything", len(lib.search("")) ==
      len(lib.materials))
check("appearances search separately",
      any(a.name.startswith("Paint") for a in lib.search("paint", True)))


# ==========================================================================
print()
if FAILS:
    print("%d FAILED" % len(FAILS))
    for name in FAILS:
        print("   - %s" % name)
    sys.exit(1)
print("all material checks passed")
