"""What an assembly is made of, counted up.

A parts list and a balloon are two ways of showing the same thing, so both
read from here rather than each working it out.  The list is grouped by the
file a component came from, because two occurrences of the same file are two
of one part, not two parts - which is the whole point of counting.

Nothing in here knows about drawings or about paper.  It is handed a
document and gives back rows, and it is the drawing that decides where they
go and how wide the columns are.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

# Which columns a parts list can show, and what each one is called on the
# sheet.  The keys are what a Row answers to.
COLUMNS = {
    "item": "ITEM",
    "qty": "QTY",
    "part_number": "PART NUMBER",
    "description": "DESCRIPTION",
    "material": "MATERIAL",
    "mass": "MASS",
}

DEFAULT_COLUMNS = ("item", "qty", "part_number", "description", "material")

# Millimetres, at a text height of 3.5.  A parts list that has to be read
# across a workshop is mostly part number and description.
DEFAULT_WIDTHS = {
    "item": 12.0, "qty": 12.0, "part_number": 45.0,
    "description": 60.0, "material": 32.0, "mass": 20.0,
}


@dataclass
class Row:
    """One line of a parts list: one part, however many times it appears."""

    item: int = 0
    quantity: int = 1
    part_number: str = ""
    description: str = ""
    material: str = ""
    mass: str = ""
    # the file this row counts, normalised, so a balloon can find its row
    key: str = ""
    path: str = ""
    occurrences: List[int] = field(default_factory=list)

    def value(self, column: str) -> str:
        if column == "item":
            return str(self.item)
        if column == "qty":
            return str(self.quantity)
        return str(getattr(self, {"part_number": "part_number",
                                  "description": "description",
                                  "material": "material",
                                  "mass": "mass"}.get(column, column), ""))

    def to_dict(self) -> Dict[str, Any]:
        return {"item": self.item, "quantity": self.quantity,
                "part_number": self.part_number,
                "description": self.description, "material": self.material,
                "mass": self.mass, "key": self.key, "path": self.path,
                "occurrences": list(self.occurrences)}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Row":
        return cls(item=int(d.get("item", 0)),
                   quantity=int(d.get("quantity", 1)),
                   part_number=str(d.get("part_number", "")),
                   description=str(d.get("description", "")),
                   material=str(d.get("material", "")),
                   mass=str(d.get("mass", "")),
                   key=str(d.get("key", "")), path=str(d.get("path", "")),
                   occurrences=[int(i) for i in d.get("occurrences", [])])


def key_for(path: str) -> str:
    """The one spelling of a path that two references can be compared by."""
    if not path:
        return ""
    return os.path.normcase(os.path.abspath(path))


def rows_for(document, base_dir: str = "", library=None,
             recurse: bool = False) -> List[Row]:
    """The parts list for a document, as rows in item order.

    An assembly is counted occurrence by occurrence; a part is one row of
    itself, because a drawing of a single part with a list on it is a
    perfectly ordinary thing and refusing to make one would be silly.

    ``recurse`` walks into sub-assemblies and counts the parts inside them
    instead of the sub-assembly itself - Inventor calls that a parts-only
    list.  Left off, a sub-assembly is one line, which is the structured
    view and the one a shop floor usually wants.
    """
    occurrences = getattr(document, "occurrences", None)
    if occurrences is None:
        return [_row_for_part(document, base_dir)]

    base = base_dir or getattr(document, "base_dir", "") or ""
    grouped: Dict[str, Row] = {}
    order: List[str] = []
    for occurrence in occurrences:
        if occurrence.suppressed:
            continue
        _collect(occurrence, base, library, recurse, grouped, order, document)

    out = []
    for index, key in enumerate(order):
        row = grouped[key]
        row.item = index + 1
        out.append(row)
    return out


def _collect(occurrence, base: str, library, recurse: bool,
             grouped: Dict[str, Row], order: List[str], parent) -> None:
    path = occurrence.ref.resolve(base) if base else ""
    if recurse and path and path.lower().endswith(".adat"):
        inner = _open(path, library)
        if inner is not None:
            for child in getattr(inner, "occurrences", []):
                if not child.suppressed:
                    _collect(child, os.path.dirname(os.path.abspath(path)),
                             library, recurse, grouped, order, inner)
            return

    key = key_for(path) or (occurrence.ref.path or occurrence.label).lower()
    if key in grouped:
        grouped[key].quantity += 1
        grouped[key].occurrences.append(occurrence.id)
        return

    name = os.path.splitext(occurrence.ref.name or "")[0] or occurrence.label
    # An occurrence is named "Side:2" - the colon and the number are the
    # instance, not the part, and a parts list counts parts.
    if ":" in name:
        name = name.rsplit(":", 1)[0]
    document = _open(path, library) if path else None
    grouped[key] = Row(
        quantity=1,
        part_number=_property(document, "part_number", name),
        description=_property(document, "description", ""),
        material=_property(document, "material", ""),
        key=key, path=path, occurrences=[occurrence.id])
    order.append(key)


def _row_for_part(document, base_dir: str) -> Row:
    path = getattr(document, "path", "") or ""
    name = os.path.splitext(os.path.basename(path))[0] if path else "Part1"
    return Row(item=1, quantity=1,
               part_number=_property(document, "part_number", name),
               description=_property(document, "description", ""),
               material=_property(document, "material", ""),
               key=key_for(path), path=path)


def _property(document, name: str, fallback: str) -> str:
    """A document's own answer, or the fallback when it has nothing to say.

    Looked up by several spellings because a part, an assembly and a drawing
    each grew their properties separately and none of them is wrong.
    """
    if document is None:
        return fallback
    properties = getattr(document, "properties", None)
    if isinstance(properties, dict):
        for spelling in (name, name.replace("_", " ").title(),
                         name.title().replace("_", "")):
            value = properties.get(spelling)
            if value:
                return str(value)
    direct = getattr(document, name, None)
    if direct and str(direct) != "Generic":
        return str(direct)
    return fallback


def _open(path: str, library) -> Optional[Any]:
    """The document at a path, without insisting on it being there."""
    if not path or not os.path.exists(path):
        return None
    try:
        from . import assembly
        return assembly.open_any(path)
    except Exception:
        return None


def row_for_key(rows: Sequence[Row], key: str) -> Optional[Row]:
    """Which row a balloon pointing at this file belongs to."""
    if not key:
        return None
    wanted = key_for(key) if os.path.isabs(key) else key
    for row in rows:
        if row.key == wanted or row.path == key:
            return row
    # a drawing that has moved folders still knows the file name
    name = os.path.basename(key).lower()
    for row in rows:
        if os.path.basename(row.path).lower() == name:
            return row
    return None
