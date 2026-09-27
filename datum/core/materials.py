"""Materials and appearances, kept apart on purpose.

A material is what a part *is*: its density, and what it would do under
load. An appearance is what it *looks like*. Keeping them separate is the
whole point, because the two do not line up one to one. ABS is one
material and comes in white, black and natural; a single "Plastic, White"
appearance sits just as well on ABS, on PLA and on nylon. Tying colour to
material would mean a new material every time somebody wanted a different
shade, and a part's mass would start depending on what colour it was
painted.

So a material *names* a default appearance and nothing more. A part can
take that default or override it, and overriding it changes not one gram.

Both live in a library. The one that ships is read-only as far as the
application is concerned, and anything the user makes or edits is written
to their own file beside it, so an update never overwrites their work.
"""

from __future__ import annotations

import copy
import json
import os

from . import prefs
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Where a user's own materials go.  Beside the settings rather than in the
# installation, which an update replaces wholesale.
USER_FILE = "materials.json"

# g/cm3 is what a workshop actually quotes, and what every density table
# is written in, so that is what is stored and shown.  Mass in grams is
# then volume in cm3 times this, with nothing to remember.
DEFAULT_DENSITY = 1.0


def plain_colour(name: str) -> str:
    """"#rrggbb" when a name is a colour and nothing else, "" otherwise."""
    text = (name or "").strip()
    if len(text) == 7 and text.startswith("#") and all(
            c in "0123456789abcdefABCDEF" for c in text[1:]):
        return text.lower()
    return ""


@dataclass
class Appearance:
    """How something looks.  Nothing here affects mass or strength."""

    name: str = "Default"
    category: str = "Misc"
    colour: str = "#9aa7b6"
    # 0 is a dead matte surface, 1 a mirror; metals want a low roughness
    # and metallic on, everything else wants metallic off
    roughness: float = 0.45
    metallic: bool = False
    opacity: float = 1.0
    # a tiling image laid over the colour, by absolute path or by a path
    # relative to the document
    texture: str = ""
    texture_scale: float = 50.0     # millimetres across one tile

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "category": self.category,
                "colour": self.colour, "roughness": self.roughness,
                "metallic": self.metallic, "opacity": self.opacity,
                "texture": self.texture,
                "texture_scale": self.texture_scale}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Appearance":
        base = cls()
        return cls(
            name=str(d.get("name", base.name)),
            category=str(d.get("category", base.category)),
            colour=str(d.get("colour", base.colour)),
            roughness=_clamp(d.get("roughness", base.roughness), 0.0, 1.0),
            metallic=bool(d.get("metallic", base.metallic)),
            opacity=_clamp(d.get("opacity", base.opacity), 0.0, 1.0),
            texture=str(d.get("texture", "")),
            texture_scale=max(0.1, float(d.get("texture_scale",
                                               base.texture_scale))))


@dataclass
class Material:
    """What a part is made of.

    ``density`` is the only field the rest of the application insists on,
    because it is the one that turns a volume into a mass.  The mechanical
    numbers are carried so a part can say what it is made of properly, and
    so there is somewhere for an FEA module to read them from later.
    """

    name: str = "Generic"
    category: str = "Misc"
    density: float = DEFAULT_DENSITY        # g/cm3
    appearance: str = "Default"             # the appearance it comes in
    # mechanical, all optional and all in the units an engineer quotes
    youngs_modulus: float = 0.0             # GPa
    poisson_ratio: float = 0.0
    yield_strength: float = 0.0             # MPa
    tensile_strength: float = 0.0           # MPa
    thermal_expansion: float = 0.0          # 1e-6 / K
    notes: str = ""

    def mass_of(self, volume_mm3: float) -> float:
        """Grams, from a volume in cubic millimetres."""
        return volume_mm3 / 1000.0 * self.density

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "category": self.category,
                "density": self.density, "appearance": self.appearance,
                "youngs_modulus": self.youngs_modulus,
                "poisson_ratio": self.poisson_ratio,
                "yield_strength": self.yield_strength,
                "tensile_strength": self.tensile_strength,
                "thermal_expansion": self.thermal_expansion,
                "notes": self.notes}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Material":
        base = cls()
        return cls(
            name=str(d.get("name", base.name)),
            category=str(d.get("category", base.category)),
            density=max(0.0, float(d.get("density", base.density))),
            appearance=str(d.get("appearance", base.appearance)),
            youngs_modulus=float(d.get("youngs_modulus", 0.0)),
            poisson_ratio=float(d.get("poisson_ratio", 0.0)),
            yield_strength=float(d.get("yield_strength", 0.0)),
            tensile_strength=float(d.get("tensile_strength", 0.0)),
            thermal_expansion=float(d.get("thermal_expansion", 0.0)),
            notes=str(d.get("notes", "")))


def _clamp(value: Any, low: float, high: float) -> float:
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return low


# --------------------------------------------------------------- the library


def builtin_appearances() -> List[Appearance]:
    """Enough to describe most things that come out of a workshop.

    Named for what they look like rather than for what they are made of,
    which is the point of them being separate: "Plastic, White" is as
    right on PLA as it is on ABS.
    """
    return [
        Appearance("Default", "Misc", "#9aa7b6", 0.45, False),

        Appearance("Steel, Polished", "Metal", "#b9c0c7", 0.16, True),
        Appearance("Steel, Brushed", "Metal", "#9ea5ad", 0.38, True),
        Appearance("Steel, Mill Finish", "Metal", "#868d95", 0.55, True),
        Appearance("Stainless, Semi-Polished", "Metal", "#c2c8cf", 0.24,
                   True),
        Appearance("Aluminium, Anodised", "Metal", "#adb4bb", 0.34, True),
        Appearance("Aluminium, Cast", "Metal", "#9a9fa4", 0.62, True),
        Appearance("Brass", "Metal", "#c3a15a", 0.26, True),
        Appearance("Copper", "Metal", "#b5744a", 0.24, True),
        Appearance("Galvanised", "Metal", "#a8afb4", 0.52, True),

        Appearance("Plastic, White", "Plastic", "#eceeef", 0.42, False),
        Appearance("Plastic, Black", "Plastic", "#2a2d31", 0.40, False),
        Appearance("Plastic, Natural", "Plastic", "#ded8c8", 0.48, False),
        Appearance("Plastic, Grey", "Plastic", "#8d9398", 0.44, False),
        Appearance("Plastic, Safety Orange", "Plastic", "#d4601f", 0.40,
                   False),
        Appearance("Plastic, Matte Black", "Plastic", "#232528", 0.72,
                   False),

        Appearance("Plywood, Birch", "Wood", "#d8b483", 0.58, False),
        Appearance("Plywood, Sanded", "Wood", "#cfa876", 0.50, False),
        Appearance("Oak", "Wood", "#b98b52", 0.55, False),
        Appearance("MDF", "Wood", "#b99a74", 0.68, False),

        Appearance("Paint, Matte Black", "Paint", "#1d1f22", 0.82, False),
        Appearance("Paint, Gloss White", "Paint", "#f2f4f5", 0.12, False),
        Appearance("Powder Coat, Grey", "Paint", "#6f757b", 0.60, False),
        Appearance("Powder Coat, RAL 3020", "Paint", "#a3161a", 0.55, False),
        # machine paint: the yellow every excavator, loader and telehandler
        # on a site is wearing, and the charcoal its undercarriage is
        Appearance("Paint, Machine Yellow", "Paint", "#e3a615", 0.44, False),
        Appearance("Paint, Machine Charcoal", "Paint", "#3a4046", 0.58, False),

        Appearance("Glass, Clear", "Glass", "#cfe0e6", 0.05, False, 0.28),
        Appearance("Acrylic, Frosted", "Glass", "#dde4e7", 0.55, False, 0.62),
        Appearance("Rubber, Black", "Rubber", "#232528", 0.88, False),
        Appearance("Concrete", "Stone", "#a8a49c", 0.78, False),
    ]


def builtin_materials() -> List[Material]:
    """Densities are the usual engineering values, in g/cm3."""
    return [
        Material("Generic", "Misc", 1.0, "Default"),

        Material("Steel, Mild", "Metal", 7.85, "Steel, Mill Finish",
                 youngs_modulus=210.0, poisson_ratio=0.30,
                 yield_strength=250.0, tensile_strength=400.0,
                 thermal_expansion=12.0,
                 notes="S235. The default for anything cut on the plasma "
                       "table."),
        Material("Steel, Stainless 304", "Metal", 8.00,
                 "Stainless, Semi-Polished",
                 youngs_modulus=193.0, poisson_ratio=0.29,
                 yield_strength=215.0, tensile_strength=505.0,
                 thermal_expansion=17.3),
        Material("Aluminium 6061", "Metal", 2.70, "Aluminium, Anodised",
                 youngs_modulus=68.9, poisson_ratio=0.33,
                 yield_strength=276.0, tensile_strength=310.0,
                 thermal_expansion=23.6),
        Material("Brass", "Metal", 8.50, "Brass",
                 youngs_modulus=100.0, poisson_ratio=0.33,
                 yield_strength=200.0, tensile_strength=400.0),
        Material("Copper", "Metal", 8.96, "Copper",
                 youngs_modulus=117.0, poisson_ratio=0.34,
                 yield_strength=70.0, tensile_strength=220.0),

        Material("ABS", "Plastic", 1.04, "Plastic, White",
                 youngs_modulus=2.3, poisson_ratio=0.35,
                 yield_strength=40.0, tensile_strength=44.0,
                 thermal_expansion=90.0),
        Material("PLA", "Plastic", 1.24, "Plastic, Natural",
                 youngs_modulus=3.5, poisson_ratio=0.36,
                 yield_strength=50.0, tensile_strength=60.0,
                 thermal_expansion=68.0,
                 notes="What most of the print farm runs."),
        Material("PETG", "Plastic", 1.27, "Plastic, Natural",
                 youngs_modulus=2.1, poisson_ratio=0.38,
                 yield_strength=50.0, tensile_strength=53.0),
        Material("Nylon 6/6", "Plastic", 1.14, "Plastic, Natural",
                 youngs_modulus=2.8, poisson_ratio=0.39,
                 yield_strength=82.0, tensile_strength=82.0),
        Material("POM (Acetal)", "Plastic", 1.41, "Plastic, Black",
                 youngs_modulus=3.1, poisson_ratio=0.35,
                 yield_strength=70.0, tensile_strength=70.0),
        Material("Acrylic (PMMA)", "Plastic", 1.18, "Acrylic, Frosted",
                 youngs_modulus=3.2, poisson_ratio=0.37,
                 tensile_strength=72.0),

        Material("Plywood, Birch", "Wood", 0.68, "Plywood, Birch",
                 youngs_modulus=10.0, tensile_strength=40.0,
                 notes="12 mm birch ply, the workshop default."),
        Material("MDF", "Wood", 0.75, "MDF", youngs_modulus=3.6),
        Material("Oak", "Wood", 0.74, "Oak", youngs_modulus=11.0),

        Material("Glass", "Glass", 2.50, "Glass, Clear",
                 youngs_modulus=70.0, poisson_ratio=0.22),
        Material("Rubber, EPDM", "Rubber", 1.15, "Rubber, Black",
                 youngs_modulus=0.01, poisson_ratio=0.49),
        Material("Concrete", "Stone", 2.40, "Concrete",
                 youngs_modulus=30.0, poisson_ratio=0.20),
    ]


class MaterialLibrary:
    """Every material and appearance the application knows about.

    Two tiers: what ships, and what the user has added or changed. Only
    the second is ever written, so an update brings new built-ins without
    trampling anything somebody made.
    """

    def __init__(self) -> None:
        self.materials: Dict[str, Material] = {}
        self.appearances: Dict[str, Appearance] = {}
        self._builtin_materials: set = set()
        self._builtin_appearances: set = set()
        self.path = ""
        self.load_builtins()

    # ------------------------------------------------------------ loading

    def load_builtins(self) -> None:
        for appearance in builtin_appearances():
            self.appearances[appearance.name] = appearance
            self._builtin_appearances.add(appearance.name)
        for material in builtin_materials():
            self.materials[material.name] = material
            self._builtin_materials.add(material.name)

    def load_user(self, path: str) -> None:
        """Merge the user's own file over the built-ins."""
        self.path = path
        if not os.path.exists(path):
            return
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return
        for raw in data.get("appearances", []):
            appearance = Appearance.from_dict(raw)
            if appearance.name:
                self.appearances[appearance.name] = appearance
        for raw in data.get("materials", []):
            material = Material.from_dict(raw)
            if material.name:
                self.materials[material.name] = material

    def save_user(self, path: str = "") -> str:
        """Write only what is not a built-in, or differs from one."""
        target = path or self.path
        if not target:
            return ""
        builtin_m = {m.name: m for m in builtin_materials()}
        builtin_a = {a.name: a for a in builtin_appearances()}

        materials = [m.to_dict() for name, m in sorted(self.materials.items())
                     if name not in builtin_m
                     or m.to_dict() != builtin_m[name].to_dict()]
        appearances = [a.to_dict() for name, a
                       in sorted(self.appearances.items())
                       if name not in builtin_a
                       or a.to_dict() != builtin_a[name].to_dict()]

        os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            json.dump({"materials": materials, "appearances": appearances},
                      handle, indent=1)
        return target

    # ------------------------------------------------------------ lookups

    def material(self, name: str) -> Material:
        """The named material, or a Generic stand-in rather than nothing."""
        found = self.materials.get(name)
        if found is not None:
            return found
        return self.materials.get("Generic") or Material()

    def appearance(self, name: str) -> Appearance:
        found = self.appearances.get(name)
        if found is not None:
            return found
        colour = plain_colour(name)
        if colour:
            # A colour on its own, "#83807d", as a part from another CAD
            # system arrives with: what it looked like there, without
            # adding a library entry for every shade of grey in the file.
            return Appearance(name=colour, category="Imported",
                              colour=colour, roughness=0.5)
        return self.appearances.get("Default") or Appearance()

    def appearance_for(self, material_name: str) -> Appearance:
        """What the named material looks like, unless told otherwise."""
        return self.appearance(self.material(material_name).appearance)

    def is_builtin(self, name: str, appearance: bool = False) -> bool:
        return name in (self._builtin_appearances if appearance
                        else self._builtin_materials)

    def categories(self, appearance: bool = False) -> List[str]:
        pool = self.appearances if appearance else self.materials
        return sorted({item.category for item in pool.values()})

    def in_category(self, category: str,
                    appearance: bool = False) -> List[Any]:
        pool = self.appearances if appearance else self.materials
        return sorted((item for item in pool.values()
                       if not category or item.category == category),
                      key=lambda i: i.name)

    def search(self, text: str, appearance: bool = False) -> List[Any]:
        pool = self.appearances if appearance else self.materials
        needle = (text or "").strip().lower()
        out = [item for item in pool.values()
               if not needle or needle in item.name.lower()
               or needle in item.category.lower()]
        return sorted(out, key=lambda i: (i.category, i.name))

    # ------------------------------------------------------------ editing

    def put_material(self, material: Material) -> Material:
        self.materials[material.name] = material
        return material

    def put_appearance(self, appearance: Appearance) -> Appearance:
        self.appearances[appearance.name] = appearance
        return appearance

    def remove(self, name: str, appearance: bool = False) -> bool:
        """Drop a user-made entry.  Built-ins are never removed."""
        if self.is_builtin(name, appearance):
            return False
        pool = self.appearances if appearance else self.materials
        return pool.pop(name, None) is not None

    def unique_name(self, base: str, appearance: bool = False) -> str:
        pool = self.appearances if appearance else self.materials
        if base not in pool:
            return base
        i = 2
        while "%s %d" % (base, i) in pool:
            i += 1
        return "%s %d" % (base, i)

    def duplicate(self, name: str, appearance: bool = False):
        """A copy of something, ready to be edited without touching it."""
        pool = self.appearances if appearance else self.materials
        source = pool.get(name)
        if source is None:
            return None
        made = copy.deepcopy(source)
        made.name = self.unique_name(name, appearance)
        pool[made.name] = made
        return made


# One library for the whole application: a material named on a part has to
# mean the same thing in the assembly it sits in and the drawing of it.
_LIBRARY: Optional[MaterialLibrary] = None


def library() -> MaterialLibrary:
    global _LIBRARY
    if _LIBRARY is None:
        _LIBRARY = MaterialLibrary()
        # the user's own materials sit beside their preferences, outside
        # the installation, so an update cannot take them away
        _LIBRARY.load_user(prefs.config_path(USER_FILE))
    return _LIBRARY


def reset_library() -> None:
    """Forget the shared library.  For tests, so they do not leak into each
    other through it."""
    global _LIBRARY
    _LIBRARY = None
