"""Document units: what a length is shown in, and what a bare number means.

Inside, every length is millimetres and stays so: the kernel, the sketch
solver and the file never see anything else, and changing a part's units
never moves a single point.  Units live at the edge, where a person reads
or types a number.

Showing is simple: millimetres divided by the unit's size.  Typing takes
one rule, Inventor's: a number written without a unit is in the
document's unit.  So that a stored expression can never be read in the
wrong unit later, the unit is written into it as it is stored: 2 typed
into an inch part is kept as "2 in", and d1 + 2 as "d1 + 2 in".  Only the
numbers that are lengths get one.  In d1 * 2 the 2 is a factor, and in
d1 / 4 so is the 4, and those are left alone.

Shown back, an expression that is a number in the document's own unit
loses the unit again, so an inch part reads 2, not 2 in.  A stored plain
number, which is millimetres, is shown converted.  Anything with names in
it is shown as written: it is explicit, and it is the user's.
"""

from __future__ import annotations

import ast
import re
from typing import Dict, List, Optional, Tuple

# millimetres in one of each
LENGTHS: Dict[str, float] = {
    "mm": 1.0,
    "cm": 10.0,
    "m": 1000.0,
    "in": 25.4,
    "ft": 304.8,
}

LABELS: Dict[str, str] = {
    "mm": "Millimetres",
    "cm": "Centimetres",
    "m": "Metres",
    "in": "Inches",
    "ft": "Feet",
}

DEFAULT = "mm"

# what a field holds, which decides whether a bare number gets a unit
LENGTH = "mm"
ANGLE = "deg"


def known(unit: str) -> str:
    """A unit this understands, or millimetres."""
    return unit if unit in LENGTHS else DEFAULT


def factor(unit: str) -> float:
    """Millimetres in one of this unit."""
    return LENGTHS.get(unit, 1.0)


def to_unit(value_mm: float, unit: str) -> float:
    return float(value_mm) / factor(unit)


def to_mm(value: float, unit: str) -> float:
    return float(value) * factor(unit)


def imperial(unit: str) -> bool:
    return unit in ("in", "ft")


def fmt(value: float, digits: int = 4) -> str:
    text = "%.*g" % (digits, value)
    return "0" if text == "-0" else text


def length_text(value_mm: float, unit: str, digits: int = 4) -> str:
    """A length as it should read: 2 in, 50.8 mm."""
    return "%s %s" % (fmt(to_unit(value_mm, unit), digits), known(unit))


def area_text(value_mm2: float, unit: str, digits: int = 4) -> str:
    unit = known(unit)
    return "%s %s2" % (fmt(value_mm2 / factor(unit) ** 2, digits), unit)


def volume_text(value_mm3: float, unit: str, digits: int = 4) -> str:
    unit = known(unit)
    return "%s %s3" % (fmt(value_mm3 / factor(unit) ** 3, digits), unit)


def mass_text(grams: float, unit: str, digits: int = 4) -> str:
    """Pounds in an inch or foot part, grams or kilograms otherwise."""
    if imperial(unit):
        return "%s lb" % fmt(grams / 453.59237, digits)
    if abs(grams) >= 1000.0:
        return "%s kg" % fmt(grams / 1000.0, digits)
    return "%s g" % fmt(grams, digits)


# --------------------------------------------------------------------------
# typing


def _plain(text: str) -> Optional[float]:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


_NUMBER_WITH_UNIT = re.compile(
    r"^\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*([A-Za-z]+)\s*$")


def _has_units(text: str) -> bool:
    """Whether the text already says a unit anywhere: then it is the user's."""
    from .params import ALL_UNITS
    try:
        names = {n.id for n in ast.walk(ast.parse(text, mode="eval"))
                 if isinstance(n, ast.Name)}
    except SyntaxError:
        # "2 in" is not Python: something with a unit in it, most likely
        return True
    return bool(names & set(ALL_UNITS))


def _lengths(node: ast.AST, length: bool, out: List[ast.Constant]) -> None:
    """Collect the number literals that are lengths, by where they stand."""
    if isinstance(node, ast.Constant):
        if length and isinstance(node.value, (int, float)) \
                and not isinstance(node.value, bool):
            out.append(node)
        return
    if isinstance(node, ast.UnaryOp):
        _lengths(node.operand, length, out)
        return
    if isinstance(node, ast.BinOp):
        if isinstance(node.op, (ast.Add, ast.Sub)):
            _lengths(node.left, length, out)
            _lengths(node.right, length, out)
        elif isinstance(node.op, ast.Mult):
            # the side without names is the factor; with names on neither,
            # the first is the length and the rest multiply it
            left_named = _named(node.left)
            right_named = _named(node.right)
            if left_named and not right_named:
                _lengths(node.left, length, out)
                _lengths(node.right, False, out)
            elif right_named and not left_named:
                _lengths(node.left, False, out)
                _lengths(node.right, length, out)
            else:
                _lengths(node.left, length, out)
                _lengths(node.right, False, out)
        elif isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)):
            _lengths(node.left, length, out)
            _lengths(node.right, False, out)
        else:
            # a power is not a length, whatever it is
            _lengths(node.left, False, out)
            _lengths(node.right, False, out)
        return
    if isinstance(node, ast.IfExp):
        _lengths(node.body, length, out)
        _lengths(node.orelse, length, out)
        return
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
            and node.func.id in ("min", "max", "abs", "round", "floor",
                                 "ceil", "hypot"):
        for arg in node.args:
            _lengths(arg, length, out)
        return
    # anything else, a comparison, a trig function, keeps its numbers


def _named(node: ast.AST) -> bool:
    return any(isinstance(n, ast.Name) for n in ast.walk(node))


def for_storage(text: str, unit: str, kind: str = LENGTH) -> str:
    """What to keep for what was typed, so it means the same forever.

    Lengths only, and only in a part that is not in millimetres; numbers
    already given a unit, and expressions that say one anywhere, are kept
    exactly as typed.
    """
    text = str(text).strip()
    unit = known(unit)
    if kind != LENGTH or unit == "mm" or not text:
        return text
    if _plain(text) is not None:
        return "%s %s" % (text, unit)
    if _has_units(text):
        return text
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError:
        return text
    found: List[ast.Constant] = []
    _lengths(tree.body, True, found)
    if not found:
        return text
    # written into the text from the right, so earlier offsets stay true
    out = text
    for node in sorted(found, key=lambda n: n.end_col_offset, reverse=True):
        end = node.end_col_offset
        out = out[:end] + " " + unit + out[end:]
    return out


def for_display(text: str, unit: str, kind: str = LENGTH) -> str:
    """What to show for a stored expression, in the document's unit."""
    text = str(text).strip()
    unit = known(unit)
    if kind != LENGTH:
        return text
    plain = _plain(text)
    if plain is not None:
        # a stored plain number is millimetres
        return fmt(to_unit(plain, unit), 10) if unit != "mm" else text
    match = _NUMBER_WITH_UNIT.match(text)
    if match and match.group(2) == unit:
        return match.group(1)
    return text


def plain_value(text: str, unit: str, kind: str = LENGTH
                ) -> Optional[float]:
    """The millimetres a typed plain number means, or None if it is not one.

    A sketch keeps a typed number as the dimension's value, not as an
    expression, so this is how the value box tells the two apart.
    """
    plain = _plain(str(text).strip())
    if plain is None:
        match = _NUMBER_WITH_UNIT.match(str(text))
        if not match or (kind == LENGTH and match.group(2) not in LENGTHS):
            return None
        if kind != LENGTH:
            return None
        return to_mm(float(match.group(1)), match.group(2))
    if kind == LENGTH:
        return to_mm(plain, known(unit))
    return plain


# --------------------------------------------------------------------------
# keeping the numbers


def _number(value: float) -> str:
    text = "%.12g" % value
    return "0" if text == "-0" else text


def rescaled(text: str, old: str, new: str, kind: str = LENGTH) -> str:
    """A stored expression, rewritten for a part whose numbers were kept.

    Keeping the numbers when a part goes from ``old`` units to ``new``
    multiplies every length in it by one factor, 25.4 from millimetres to
    inches, so an expression has to come out that much larger too.  A
    number written in the old unit is the same number in the new one; a
    bare number, which is millimetres, and a number in any other unit are
    multiplied.  Names are left as they are: what they stand for is
    rescaled where it is kept.  Factors, like the 2 in d1 * 2, stay put.
    """
    from .params import LENGTH_UNITS

    text = str(text).strip()
    old, new = known(old), known(new)
    if kind != LENGTH or old == new or not text:
        return text
    scale = factor(new) / factor(old)

    def bare(digits: str) -> str:
        # millimetres in, millimetres out; from a millimetre part that is
        # simply the same digits in the new unit, which is what was typed
        if old == "mm":
            return "%s %s" % (digits, new)
        return _number(float(digits) * scale)

    if _plain(text) is not None:
        return bare(text)

    held: Dict[str, str] = {}

    def hold(match: "re.Match[str]") -> str:
        digits, unit = match.group(1), match.group(2)
        if abs(LENGTH_UNITS[unit] - factor(old)) < 1e-12:
            out = "%s %s" % (digits, new)
        else:
            out = "%s %s" % (_number(float(digits) * scale), unit)
        key = "__kept%d__" % len(held)
        held[key] = out
        return key

    pattern = re.compile(
        r"(?<![\w.])((?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*("
        + "|".join(sorted(LENGTH_UNITS, key=len, reverse=True)) + r")\b")
    out = pattern.sub(hold, text)
    try:
        tree = ast.parse(out, mode="eval")
    except SyntaxError:
        tree = None
    if tree is not None:
        found: List[ast.Constant] = []
        _lengths(tree.body, True, found)
        # rewritten from the right, so earlier offsets stay true
        for node in sorted(found, key=lambda n: n.col_offset, reverse=True):
            start, end = node.col_offset, node.end_col_offset
            out = out[:start] + bare(out[start:end]) + out[end:]
    for key, value in held.items():
        out = out.replace(key, value)
    return out
