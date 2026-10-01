"""Model states: one part, several variations of it.

Inventor's model states are what a family of parts is made of: the same
bracket with and without its holes, a bolt at five lengths, a casting and
the machined part made from it.  A part has the Primary state, which is
the part as it is, and any number of others, each remembering how it
differs: parameter values, every dimension and feature value included;
which features are suppressed; the part's properties, so each can carry
its own part number; and its material.

While another state is active, changing any of those belongs to that
state.  Adding a feature belongs to every state, because the tree is
shared: a feature added in one state is there in all of them, with
whatever values that state gives it.

A state is kept as differences rather than as a copy, so a dimension
nobody varies follows the Primary state wherever it goes.  The
differences are worked out when the state is left, or the part saved, by
comparing what the part holds then with what Primary holds, which means
nothing anywhere has to remember to tell this when it changes something.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

PRIMARY = "Primary"


def capture(doc: Any) -> Dict[str, Any]:
    """Everything a model state can vary, as it stands, by key.

    p:name     a parameter's expression, user or model
    s:id       whether a feature is suppressed
    prop:key   a property
    material   the material
    """
    from . import modelparams

    out: Dict[str, Any] = {}
    for param in doc.params:
        out["p:" + param.name] = param.expression
    for m in modelparams.collect(doc):
        if not m.reference:
            out.setdefault("p:" + m.name, m.expression)
    for feature in doc.features:
        out["s:%d" % feature.id] = bool(feature.suppressed)
    for key, value in (getattr(doc, "properties", {}) or {}).items():
        out["prop:" + str(key)] = str(value)
    out["material"] = getattr(doc, "material", "Generic")
    return out


def apply(doc: Any, values: Dict[str, Any]) -> None:
    """Put a set of captured values back.  Missing things are skipped.

    A property that the values do not mention is left alone, unless the
    values hold the whole set, which a full capture does: then a property
    the state does not have is taken away.
    """
    from . import modelparams
    from .params import ExpressionError

    by_id = {f.id: f for f in doc.features}
    properties = None
    for key, value in values.items():
        if value is None and not key.startswith("prop:"):
            continue
        if key.startswith("p:"):
            name = key[2:]
            try:
                current = (doc.params[name].expression if name in doc.params
                           else None)
                if current is None:
                    found = modelparams.find(doc, name)
                    current = found.expression if found is not None else None
                    if found is None:
                        continue
                if current != value:
                    modelparams.set_expression(doc, name, str(value))
            except (ExpressionError, KeyError):
                continue
        elif key.startswith("s:"):
            try:
                feature = by_id.get(int(key[2:]))
            except ValueError:
                feature = None
            if feature is not None:
                feature.suppressed = bool(value)
        elif key.startswith("prop:"):
            if properties is None:
                properties = {}
            if value is not None:
                properties[key[5:]] = str(value)
        elif key == "material":
            doc.material = str(value)
    if properties is not None:
        doc.properties = properties
    doc.params.evaluate_all()


class ModelStates:
    """The states a part has, which is active, and what each changes."""

    def __init__(self) -> None:
        self.active = PRIMARY
        # what Primary holds, kept while another state is active
        self.baseline: Dict[str, Any] = {}
        # every other state, in order, as its differences from Primary
        self.states: Dict[str, Dict[str, Any]] = {}

    def names(self) -> List[str]:
        return [PRIMARY] + list(self.states)

    def __contains__(self, name: object) -> bool:
        return name == PRIMARY or name in self.states

    def __len__(self) -> int:
        return 1 + len(self.states)

    # -- keeping up ---------------------------------------------------------

    def sync(self, doc: Any) -> None:
        """Note what the active state now changes.  Changes nothing in doc.

        Anything new since Primary was left, a feature added, a parameter
        made, is shared by every state, so it joins Primary as it is.
        """
        if self.active == PRIMARY:
            return
        current = capture(doc)
        for key, value in current.items():
            self.baseline.setdefault(key, value)
        changes = {key: value for key, value in current.items()
                   if self.baseline.get(key) != value}
        # a property this state does without is a difference too, kept as
        # None; a feature or parameter that has gone has gone from all
        for key in self.baseline:
            if key.startswith("prop:") and key not in current:
                changes[key] = None
        self.states[self.active] = changes

    def values_of(self, name: str) -> Dict[str, Any]:
        """What a state holds, Primary's values with its own on top."""
        out = dict(self.baseline)
        if name != PRIMARY:
            out.update(self.states.get(name, {}))
        return out

    # -- changing -----------------------------------------------------------

    def activate(self, doc: Any, name: str) -> None:
        """Make another state the one the part is, and is edited in."""
        if name not in self:
            raise KeyError("there is no model state called %r" % name)
        if name == self.active:
            return
        if self.active == PRIMARY:
            self.baseline = capture(doc)
        else:
            self.sync(doc)
        apply(doc, self.values_of(name))
        self.active = name
        if name == PRIMARY:
            # Primary is the part itself again; nothing to remember
            self.baseline = {}
        doc.modified = True

    def create(self, doc: Any, name: str = "") -> str:
        """A new state, starting as a copy of the active one, and made active.

        That is how Inventor does it, and what is wanted nearly every
        time: the new variation begins where you are.
        """
        name = (name or "").strip() or self.next_name()
        if name in self:
            raise ValueError("there is already a model state called %r"
                             % name)
        if self.active == PRIMARY:
            self.baseline = capture(doc)
            copied: Dict[str, Any] = {}
        else:
            self.sync(doc)
            copied = dict(self.states.get(self.active, {}))
        self.states[name] = copied
        self.active = name
        doc.modified = True
        return name

    def next_name(self) -> str:
        i = 1
        while "State%d" % i in self:
            i += 1
        return "State%d" % i

    def rename(self, doc: Any, old: str, new: str) -> None:
        new = (new or "").strip()
        if old == PRIMARY:
            raise ValueError("Primary keeps its name")
        if not new or new in self:
            raise ValueError("there is already a model state called %r"
                             % new)
        if old not in self.states:
            raise KeyError(old)
        self.states = {(new if k == old else k): v
                       for k, v in self.states.items()}
        if self.active == old:
            self.active = new
        doc.modified = True

    def delete(self, doc: Any, name: str) -> None:
        if name == PRIMARY:
            raise ValueError("Primary cannot be deleted")
        if name not in self.states:
            raise KeyError(name)
        if self.active == name:
            self.activate(doc, PRIMARY)
        del self.states[name]
        doc.modified = True

    # -- saving -------------------------------------------------------------

    def to_dict(self, doc: Any) -> Optional[Dict[str, Any]]:
        if not self.states:
            return None
        self.sync(doc)
        return {"active": self.active, "baseline": dict(self.baseline),
                "states": {k: dict(v) for k, v in self.states.items()}}

    def load(self, data: Optional[Dict[str, Any]]) -> None:
        self.active = PRIMARY
        self.baseline = {}
        self.states = {}
        if not data:
            return
        self.states = {str(k): dict(v or {})
                       for k, v in (data.get("states") or {}).items()}
        active = str(data.get("active") or PRIMARY)
        self.active = active if active in self else PRIMARY
        if self.active != PRIMARY:
            self.baseline = dict(data.get("baseline") or {})
