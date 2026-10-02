"""Changing a part's unit while keeping its numbers: the part rescales.

Changing units the ordinary way converts.  Nothing moves, and 25.4 mm
reads 1 in.  Keeping the numbers is the other answer, for a part drawn in
the wrong unit, or a file that came in without one: 25.4 stays 25.4, now in
inches, so every length in the part is multiplied by the same factor.

Every length means every place one is kept, since inside, a part is all
millimetres: sketch geometry and its dimensions, the values features were
typed, user parameters, what each model state holds, the fingerprints
faces and edges are found again by, the spots a feature picked its regions
at, a primitive's corner and an imported body's scale.  Angles and counts
stay as they are.  Rule code and code features are the user's own text and
keep their numbers; what this did not touch is said, not hidden.
"""

from __future__ import annotations

from typing import Any, Dict, List

from . import modelparams, units
from .modelstates import PRIMARY

# feature fields that are not lengths; every other expression field is one
ANGLE_ATTRS = {"angle", "taper", "cs_angle", "rx", "ry", "rz"}
COUNT_ATTRS = {"count1", "count2"}


def length_attrs(type_name: str) -> List[str]:
    """A feature type's expression fields that hold lengths, used or not."""
    return [attr for attr in modelparams.EXPRESSION_ATTRS.get(type_name, ())
            if attr not in ANGLE_ATTRS and attr not in COUNT_ATTRS]


def _scale_ref(ref: Any, f: float) -> None:
    """A face or edge fingerprint, for the model grown f times."""
    ref.centre = tuple(c * f for c in ref.centre)
    if ref.kind == "edge":
        ref.size *= f
    elif ref.kind == "face":
        ref.size *= f * f


def _scale_ref_dict(data: Dict[str, Any], f: float) -> None:
    """The same, for one kept as a dictionary, as a projection keeps it."""
    if "centre" in data:
        data["centre"] = [c * f for c in data["centre"]]
    kind = data.get("kind")
    if "size" in data and kind in ("edge", "face"):
        data["size"] = float(data["size"]) * (f if kind == "edge" else f * f)


def _scale_sketch(sketch: Any, old: str, new: str, f: float) -> None:
    for point in sketch.points.values():
        point.x *= f
        point.y *= f
    for entity in sketch.entities.values():
        entity.radius *= f
    for c in sketch.constraints.values():
        c.label_offset = (c.label_offset[0] * f, c.label_offset[1] * f)
        if not c.is_dimension or c.kind == "angle":
            continue
        c.value *= f
        if c.expression:
            c.expression = units.rescaled(c.expression, old, new)
    plane = sketch.plane
    plane.origin = tuple(o * f for o in plane.origin)
    for record in sketch.projections:
        _scale_ref_dict(record.source, f)


def _scale_feature(feature: Any, old: str, new: str, f: float) -> None:
    from .features import ProfileSelection
    from .naming import RefSet, ShapeRef

    kind = getattr(feature, "type_name", "")
    if kind == "sketch":
        _scale_sketch(feature.sketch, old, new, f)
    for attr in length_attrs(kind):
        value = getattr(feature, attr, None)
        if isinstance(value, str):
            setattr(feature, attr, units.rescaled(value, old, new))
    if kind == "primitive":
        feature.origin = tuple(o * f for o in feature.origin)
    if kind == "import":
        feature.scale *= f
    # whatever references, picks and regions the feature keeps
    for value in list(vars(feature).values()):
        if isinstance(value, ShapeRef):
            _scale_ref(value, f)
        elif isinstance(value, RefSet):
            for ref in value:
                _scale_ref(ref, f)
        elif isinstance(value, ProfileSelection):
            value.items = [(sid, u * f, v * f) for sid, u, v in value.items]


def _kinds(doc: Any) -> Dict[str, Any]:
    """What each parameter name is: a user parameter or a model one."""
    out: Dict[str, Any] = {}
    for param in doc.params:
        out[param.name] = param
    for m in modelparams.collect(doc):
        out.setdefault(m.name, m)
    return out


def _state_value(holder: Any, text: Any, old: str, new: str, f: float):
    """A value a model state keeps, for the rescaled part."""
    if not isinstance(text, str):
        return text
    if isinstance(holder, modelparams.ModelParam):
        if holder.unit != modelparams.LENGTH:
            return text
        if holder.is_dimension:
            # a dimension keeps a plain value as its millimetres, which is
            # what capture writes down for it, so the same goes back
            plain = units.plain_value(text, "mm", units.LENGTH)
            if plain is not None:
                return modelparams.number(plain * f)
        return units.rescaled(text, old, new)
    if holder is not None and _user_param_is_length(holder):
        return units.rescaled(text, old, new)
    return text


def _user_param_is_length(param: Any) -> bool:
    return getattr(param, "unit", "mm") in units.LENGTHS


def keep_numbers(doc: Any, new: str) -> List[str]:
    """Change a part's unit to ``new``, keeping every number it shows.

    The part grows, or shrinks, by the ratio of the two units.  Returns
    what could not be rescaled, for saying so.
    """
    old = units.known(getattr(doc, "units", "mm") or "mm")
    new = units.known(new)
    if old == new:
        return []
    f = units.factor(new) / units.factor(old)

    # Primary first: then the part holds Primary's values and every other
    # state holds only what it changes, and both can be rescaled alike
    states = doc.model_states
    was = states.active
    if was != PRIMARY:
        states.activate(doc, PRIMARY)

    kinds = _kinds(doc)
    for name, changes in states.states.items():
        states.states[name] = {
            key: (_state_value(kinds.get(key[2:]), value, old, new, f)
                  if key.startswith("p:") else value)
            for key, value in changes.items()}

    for param in doc.params:
        if _user_param_is_length(param):
            param.expression = units.rescaled(param.expression, old, new)
            if param.unit == old:
                # it was in the part's unit, and the number still is
                param.unit = new
    for feature in doc.features:
        _scale_feature(feature, old, new, f)

    doc.units = new
    doc.params.evaluate_all()
    if was != PRIMARY:
        states.activate(doc, was)
    doc.modified = True

    notes = []
    if len(getattr(doc, "rules", ()) or ()):
        notes.append("dLogic rules keep the numbers written in them")
    if any(getattr(f, "type_name", "") == "code" for f in doc.features):
        notes.append("code features keep the numbers written in them")
    return notes
