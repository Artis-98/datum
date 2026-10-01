"""Model parameters: every dimension, and every value a feature was given.

Inventor's parameter table has two halves.  User parameters are the ones
somebody adds by name.  Model parameters are made by modelling itself:
each driving sketch dimension, and each number a feature was typed, an
extrude's distance, a fillet's radius, a work plane's offset.  Every one
has a name of its own, d1, d2 and so on across the whole part, so any of
them can be written into another's expression and all of them can be
changed from the table.

The values stay where they always lived, on the sketch constraint or the
feature field, as expression strings.  A model parameter is a handle on one
of them: it knows where it is, reads and writes it there, and lends it a
name.  Nothing is copied, so nothing can drift apart, which is how a part
used to end up with a d1 in the table saying 50 and a d1 in its sketch
saying 30.

Names used to be per sketch, so every sketch had its own d1.  A part saved
like that is renamed on the way in, one sketch at a time, and each
sketch's own expressions follow its own dimensions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from .params import (
    CONSTANTS, FUNCTIONS, IDENT_RE, ExpressionError, evaluate,
    referenced_names,
)

LENGTH = "mm"
ANGLE = "deg"
COUNT = "ul"            # Inventor's word for a plain number: unitless

PREFIX = "d"

DIMENSION_LABELS = {
    "distance": "Distance",
    "distance_x": "Horizontal distance",
    "distance_y": "Vertical distance",
    "distance_pl": "Distance",
    "radius": "Radius",
    "diameter": "Diameter",
    "angle": "Angle",
    "radial_gap": "Distance",
}

PRIMITIVE_LABELS = {
    "box": ("Length X", "Length Y", "Length Z"),
    "cylinder": ("Radius", "Height", None),
    "sphere": ("Radius", None, None),
    "cone": ("Bottom radius", "Top radius", "Height"),
    "torus": ("Ring radius", "Tube radius", None),
}

# Every attribute of every feature type that can hold an expression, used
# or not.  A rename has to reach all of them, including a counterbore depth
# on a hole that is simple today and counterbored tomorrow.
EXPRESSION_ATTRS: Dict[str, Tuple[str, ...]] = {
    "workplane": ("offset", "angle"),
    "extrude": ("distance", "taper"),
    "revolve": ("angle",),
    "hole": ("diameter", "depth", "cb_diameter", "cb_depth", "cs_angle"),
    "primitive": ("a", "b", "c"),
    "fillet": ("radius",),
    "chamfer": ("distance",),
    "shell": ("thickness",),
    "pattern": ("count1", "spacing1", "count2", "spacing2", "angle"),
    "move": ("dx", "dy", "dz", "rx", "ry", "rz"),
}


def feature_fields(feature: Any) -> List[Tuple[str, str, str]]:
    """The fields of a feature that are parameters as it stands.

    ``(attribute, label, unit)``, for the ones its settings actually use:
    an extrude run through all has no distance worth a row in the table,
    and a simple hole has no counterbore.  A field that comes back into use
    gets back the name it had.
    """
    kind = getattr(feature, "type_name", "")
    if kind == "workplane":
        return [("offset", "Offset", LENGTH), ("angle", "Tilt", ANGLE)]
    if kind == "extrude":
        out = []
        if feature.extent in ("distance", "symmetric"):
            out.append(("distance", "Distance", LENGTH))
        if feature.extent == "distance":
            out.append(("taper", "Taper", ANGLE))
        return out
    if kind == "revolve":
        return [("angle", "Angle", ANGLE)]
    if kind == "hole":
        out = [("diameter", "Diameter", LENGTH)]
        if not feature.through:
            out.append(("depth", "Depth", LENGTH))
        if feature.hole_type == "counterbore":
            out += [("cb_diameter", "Counterbore diameter", LENGTH),
                    ("cb_depth", "Counterbore depth", LENGTH)]
        elif feature.hole_type == "countersink":
            out += [("cb_diameter", "Countersink diameter", LENGTH),
                    ("cs_angle", "Countersink angle", ANGLE)]
        return out
    if kind == "primitive":
        labels = PRIMITIVE_LABELS.get(feature.kind, ("A", "B", "C"))
        return [(attr, label, LENGTH)
                for attr, label in zip(("a", "b", "c"), labels) if label]
    if kind == "fillet":
        return [("radius", "Radius", LENGTH)]
    if kind == "chamfer":
        return [("distance", "Distance", LENGTH)]
    if kind == "shell":
        return [("thickness", "Thickness", LENGTH)]
    if kind == "pattern":
        if feature.mode == "rectangular":
            return [("count1", "Count", COUNT), ("spacing1", "Spacing", LENGTH),
                    ("count2", "Count 2", COUNT),
                    ("spacing2", "Spacing 2", LENGTH)]
        out = [("count1", "Count", COUNT)]
        if not feature.full_circle:
            out.append(("angle", "Angle", ANGLE))
        return out
    if kind == "move":
        return [("dx", "Move X", LENGTH), ("dy", "Move Y", LENGTH),
                ("dz", "Move Z", LENGTH), ("rx", "Turn X", ANGLE),
                ("ry", "Turn Y", ANGLE), ("rz", "Turn Z", ANGLE)]
    return []


def number(value: float) -> str:
    """A value as an expression would write it: 30, not 30.000000000001."""
    text = "%.10g" % round(float(value), 10)
    return "0" if text == "-0" else text


def _plain_number(text: str) -> Optional[float]:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


@lru_cache(maxsize=8192)
def _refs(text: str) -> Tuple[str, ...]:
    return tuple(referenced_names(text))


# --------------------------------------------------------------------------


@dataclass
class ModelParam:
    """One model parameter, and where it lives."""

    name: str
    owner: str              # the feature that uses it, for the table
    label: str              # what it is to that feature
    unit: str
    feature: Any
    attr: str = ""          # the feature field, when it is one
    constraint: Any = None  # the sketch dimension, when it is one
    value: float = 0.0
    error: str = ""

    @property
    def is_dimension(self) -> bool:
        return self.constraint is not None

    @property
    def reference(self) -> bool:
        """A driven dimension: it measures the sketch, nothing sets it."""
        return self.constraint is not None and not self.constraint.driving

    @property
    def expression(self) -> str:
        if self.constraint is not None:
            c = self.constraint
            return c.expression or number(c.value)
        return str(getattr(self.feature, self.attr, ""))

    def set_expression(self, text: str) -> None:
        text = str(text).strip()
        if not text:
            raise ExpressionError("empty expression")
        if self.constraint is None:
            setattr(self.feature, self.attr, text)
            return
        if self.reference:
            raise ExpressionError(
                "%s is a driven dimension: it measures the sketch, so "
                "nothing can set it" % self.name)
        from . import units
        # 2 in, or a plain number, which is millimetres: either way a value
        plain = units.plain_value(
            text, "mm", units.ANGLE if self.unit == ANGLE else units.LENGTH)
        if plain is not None:
            # a plain number is the dimension's value, which is how the
            # sketch holds one it was typed rather than written as a sum
            self.constraint.expression = ""
            self.constraint.value = plain
        else:
            self.constraint.expression = text

    @property
    def comment(self) -> str:
        if self.constraint is not None:
            return getattr(self.constraint, "comment", "")
        return dict(getattr(self.feature, "param_comments", {}) or {}).get(
            self.attr, "")

    def set_comment(self, text: str) -> None:
        if self.constraint is not None:
            self.constraint.comment = str(text)
            return
        comments = getattr(self.feature, "param_comments", None)
        if comments is None:
            comments = {}
            self.feature.param_comments = comments
        if text:
            comments[self.attr] = str(text)
        else:
            comments.pop(self.attr, None)


def _sketch_of(feature: Any):
    return getattr(feature, "sketch", None) \
        if getattr(feature, "type_name", "") == "sketch" else None


def collect(doc: Any) -> List[ModelParam]:
    """Every model parameter of a part, in tree order."""
    out: List[ModelParam] = []
    for feature in getattr(doc, "features", ()):
        sketch = _sketch_of(feature)
        if sketch is not None:
            for cid in sorted(sketch.constraints):
                c = sketch.constraints[cid]
                if not c.is_dimension or not c.name:
                    continue
                out.append(ModelParam(
                    c.name, feature.name, DIMENSION_LABELS.get(c.kind, c.kind),
                    ANGLE if c.kind == "angle" else LENGTH, feature,
                    constraint=c))
            continue
        names = getattr(feature, "param_names", None) or {}
        for attr, label, unit in feature_fields(feature):
            name = names.get(attr)
            if name:
                out.append(ModelParam(name, feature.name, label, unit,
                                      feature, attr=attr))
    return out


def taken_names(doc: Any) -> Set[str]:
    """Every name a parameter of this part already has, used or not."""
    taken: Set[str] = set(doc.params.names())
    for feature in getattr(doc, "features", ()):
        sketch = _sketch_of(feature)
        if sketch is not None:
            taken.update(c.name for c in sketch.constraints.values()
                         if c.name)
            continue
        taken.update(n for n in (getattr(feature, "param_names", None)
                                 or {}).values() if n)
    return taken


def fresh_name(taken: Iterable[str]) -> str:
    """The lowest d-number nobody has."""
    held = set(taken)
    i = 1
    while "%s%d" % (PREFIX, i) in held:
        i += 1
    return "%s%d" % (PREFIX, i)


def _usable(name: str) -> bool:
    return bool(name) and bool(IDENT_RE.match(name)) \
        and name not in FUNCTIONS and name not in CONSTANTS


def assign_names(doc: Any) -> bool:
    """Give every model parameter a name of its own.  True if any changed.

    Run on every rebuild, so a feature gets its names however it arrived:
    the dialog, a rule, a file.  A name already taken further up the tree,
    or by a user parameter, is a dimension from the days when every sketch
    counted from d1; it is renamed, and that sketch's own expressions are
    rewritten to follow it, since inside a sketch the name always meant the
    sketch's own dimension.
    """
    everything = taken_names(doc)
    seen: Set[str] = set(doc.params.names())
    changed = False

    def fresh() -> str:
        name = fresh_name(everything)
        everything.add(name)
        return name

    for feature in getattr(doc, "features", ()):
        sketch = _sketch_of(feature)
        if sketch is not None:
            renamed: Dict[str, str] = {}
            for cid in sorted(sketch.constraints):
                c = sketch.constraints[cid]
                if not c.is_dimension:
                    continue
                if not _usable(c.name) or c.name in seen:
                    new = fresh()
                    if c.name:
                        renamed[c.name] = new
                    c.name = new
                    changed = True
                seen.add(c.name)
            if renamed:
                for c in sketch.constraints.values():
                    if c.expression:
                        c.expression = _substitute(c.expression, renamed)
            continue

        names = getattr(feature, "param_names", None)
        if names is None:
            names = {}
            try:
                feature.param_names = names
            except AttributeError:
                continue
        wanted = {attr for attr, _l, _u in feature_fields(feature)}
        for attr in EXPRESSION_ATTRS.get(feature.type_name, ()):
            name = names.get(attr, "")
            if name and (not _usable(name) or name in seen):
                name = ""
                names.pop(attr, None)
                changed = True
            if not name and attr in wanted:
                name = fresh()
                names[attr] = name
                changed = True
            if name:
                seen.add(name)
    return changed


# --------------------------------------------------------------------------
# evaluation


def evaluate_document(doc: Any, cache: Optional[Dict[str, Any]] = None
                      ) -> Tuple[Dict[str, float], Dict[str, str],
                                 List[ModelParam]]:
    """Every parameter's value, user and model alike, worked out together.

    They are one namespace: a user parameter can be written in terms of a
    sketch dimension and a fillet radius in terms of a user parameter, so
    they are resolved in one dependency order rather than table first.  A
    driven dimension is a measurement and goes in as it stands.  Whatever
    cannot be worked out keeps a safe value, the dimension its last good
    one, so a typo costs the expression and not the sketch.

    Returns the scope, the errors by name, and the model parameters with
    their values filled in.  ``cache``, a dict the caller keeps, holds the
    last answer and what it was worked out from: the sketcher asks on
    every mouse move of a drag, and nothing has usually changed.
    """
    model = collect(doc)
    expressions: Dict[str, str] = {}
    fixed: Dict[str, float] = {}
    fallback: Dict[str, float] = {}
    for param in doc.params:
        expressions[param.name] = param.expression
        fallback[param.name] = 0.0
    for m in model:
        if m.name in expressions or m.name in fixed:
            continue
        if m.reference:
            fixed[m.name] = float(m.constraint.value)
            continue
        expressions[m.name] = m.expression
        fallback[m.name] = (float(m.constraint.value)
                            if m.constraint is not None else 0.0)

    key = (tuple(expressions.items()), tuple(sorted(fixed.items())),
           tuple(sorted(fallback.items())))
    if cache is not None and cache.get("key") == key:
        scope = dict(cache["scope"])
        errors = dict(cache["errors"])
    else:
        order, cyclic = _order(expressions)
        scope = dict(fixed)
        errors = {}
        for name in order:
            if name in cyclic:
                errors[name] = "circular reference"
                scope[name] = fallback[name]
                continue
            try:
                scope[name] = evaluate(expressions[name], scope)
            except ExpressionError as exc:
                errors[name] = str(exc)
                scope[name] = fallback[name]
        if cache is not None:
            cache.clear()
            cache.update(key=key, scope=dict(scope), errors=dict(errors))

    for m in model:
        m.value = scope.get(m.name, 0.0)
        m.error = errors.get(m.name, "")
    return scope, errors, model


def _order(expressions: Dict[str, str]) -> Tuple[List[str], Set[str]]:
    """Names in an order where each comes after what it reads."""
    state: Dict[str, int] = {}
    order: List[str] = []
    cyclic: Set[str] = set()

    for root in expressions:
        if state.get(root):
            continue
        # iterative, because a long chain of dimensions is not a reason to
        # run out of stack
        stack: List[Tuple[str, Iterable[str]]] = [
            (root, iter(_refs(expressions[root])))]
        path = [root]
        state[root] = 1
        while stack:
            name, deps = stack[-1]
            advanced = False
            for dep in deps:
                if dep not in expressions:
                    continue
                if state.get(dep) == 1:
                    cyclic.update(path[path.index(dep):])
                    continue
                if not state.get(dep):
                    state[dep] = 1
                    stack.append((dep, iter(_refs(expressions[dep]))))
                    path.append(dep)
                    advanced = True
                    break
            if not advanced:
                stack.pop()
                path.pop()
                state[name] = 2
                order.append(name)
    return order, cyclic


def dependents(doc: Any, names: Iterable[str]) -> Set[str]:
    """The names given, and every parameter worked out from any of them."""
    expressions: Dict[str, str] = {p.name: p.expression for p in doc.params}
    for m in collect(doc):
        expressions.setdefault(m.name, m.expression)
    moved = set(names)
    grew = True
    while grew:
        grew = False
        for name, text in expressions.items():
            if name not in moved and moved.intersection(_refs(text)):
                moved.add(name)
                grew = True
    return moved


def users_of(doc: Any, name: str) -> List[str]:
    """The parameters whose expressions name this one."""
    out = [p.name for p in doc.params if name in _refs(p.expression)]
    out += [m.name for m in collect(doc) if name in _refs(m.expression)]
    return out


# --------------------------------------------------------------------------
# changing them


def find(doc: Any, name: str) -> Optional[ModelParam]:
    return next((m for m in collect(doc) if m.name == name), None)


def set_expression(doc: Any, name: str, text: str) -> None:
    """Write an expression into a parameter, wherever it lives."""
    if name in doc.params:
        doc.params.set_expression(name, text)
        return
    found = find(doc, name)
    if found is None:
        raise ExpressionError("there is no parameter called %r" % name)
    found.set_expression(text)


def _substitute(text: str, mapping: Dict[str, str]) -> str:
    if not text or not mapping:
        return text
    pattern = re.compile(r"\b(%s)\b" % "|".join(
        re.escape(k) for k in sorted(mapping, key=len, reverse=True)))
    return pattern.sub(lambda m: mapping[m.group(1)], text)


def rewrite(doc: Any, mapping: Dict[str, str]) -> None:
    """Rename references in every expression of a part, all at once."""
    if not mapping:
        return
    for param in doc.params:
        param.expression = _substitute(param.expression, mapping)
    for feature in getattr(doc, "features", ()):
        sketch = _sketch_of(feature)
        if sketch is not None:
            for c in sketch.constraints.values():
                if c.expression:
                    c.expression = _substitute(c.expression, mapping)
            continue
        for attr in EXPRESSION_ATTRS.get(feature.type_name, ()):
            value = getattr(feature, attr, None)
            if isinstance(value, str):
                setattr(feature, attr, _substitute(value, mapping))
        origin = getattr(feature, "origin", None)
        if feature.type_name == "primitive" and isinstance(origin,
                                                           (tuple, list)):
            feature.origin = tuple(_substitute(str(v), mapping)
                                   for v in origin)


def rename(doc: Any, old: str, new: str) -> None:
    """Rename a parameter, user or model, and every reference to it."""
    new = str(new).strip()
    if new == old:
        return
    if not IDENT_RE.match(new):
        raise ExpressionError("%r is not a valid parameter name" % new)
    if new in FUNCTIONS or new in CONSTANTS:
        raise ExpressionError("%r is a reserved name" % new)
    if new in taken_names(doc):
        raise ExpressionError("there is already a parameter called %r" % new)

    if old in doc.params:
        doc.params.rename(old, new)
    else:
        owner = None
        for feature in getattr(doc, "features", ()):
            sketch = _sketch_of(feature)
            if sketch is not None:
                owner = next((c for c in sketch.constraints.values()
                              if c.name == old), None)
                if owner is not None:
                    owner.name = new
                    break
                continue
            names = getattr(feature, "param_names", None) or {}
            attr = next((a for a, n in names.items() if n == old), None)
            if attr is not None:
                names[attr] = new
                owner = feature
                break
        if owner is None:
            raise ExpressionError("there is no parameter called %r" % old)
    rewrite(doc, {old: new})


class ParameterView:
    """What an expression box, the sketcher or a rule asks of a part.

    The scope is every parameter, user and model, worked out afresh, so a
    box typed into a moment after a dimension changed sees the new value.
    """

    def __init__(self, doc: Any) -> None:
        self._doc = doc

    def scope(self) -> Dict[str, float]:
        return self._doc.parameter_scope()

    @property
    def units(self) -> str:
        """The part's length unit, which a bare typed number is in."""
        return getattr(self._doc, "units", "mm") or "mm"

    def taken(self) -> Set[str]:
        return taken_names(self._doc)

    def names(self) -> List[str]:
        return list(self._doc.params.names()) + [
            m.name for m in collect(self._doc)]

    def __contains__(self, name: object) -> bool:
        return name in self.taken()


class UnifiedTable:
    """User and model parameters as one table, the way a rule sees them.

    ``params.d7 = 30`` in a rule sets an extrude's distance as readily as
    ``params.width = 120`` sets a user parameter; a name that is neither is
    made a new user parameter, as it always was.
    """

    def __init__(self, doc: Any) -> None:
        self._doc = doc

    def __contains__(self, name: object) -> bool:
        return name in self._doc.params or find(self._doc, str(name)) \
            is not None

    def __getitem__(self, name: str) -> Any:
        if name in self._doc.params:
            self._doc.params.evaluate_all()
            return self._doc.params[name]
        for m in evaluate_document(self._doc)[2]:
            if m.name == name:
                return m
        raise KeyError(name)

    def set_expression(self, name: str, text: str) -> None:
        set_expression(self._doc, name, text)
        self._doc.params.evaluate_all()

    def add(self, name: str, text: str) -> None:
        self._doc.params.add(name, text)

    def names(self) -> List[str]:
        return list(self._doc.params.names()) + [
            m.name for m in collect(self._doc)]


def consumers(doc: Any, name: str) -> List[str]:
    """The features whose own values are written in terms of this name."""
    out: List[str] = []
    for feature in getattr(doc, "features", ()):
        sketch = _sketch_of(feature)
        if sketch is not None:
            texts = [c.expression for c in sketch.constraints.values()
                     if c.expression]
        else:
            texts = [getattr(feature, attr, "") for attr in
                     EXPRESSION_ATTRS.get(feature.type_name, ())]
            origin = getattr(feature, "origin", None)
            if feature.type_name == "primitive" and isinstance(origin, tuple):
                texts += [str(v) for v in origin]
        if any(isinstance(t, str) and name in _refs(t) for t in texts):
            out.append(feature.name)
    return out
