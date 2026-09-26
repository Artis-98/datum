"""dLogic: rules that drive a document, and code that builds one.

Inventor has iLogic, and the reason people ask for it is not the language,
it is that a model can be told how to behave rather than only what shape
to be. A rule reads the parameters, decides something, and writes them
back, and the model rebuilds around the answer.

The language here is Python, for one reason that matters: it is the
language DATUM itself is written in, so a rule is not a guest in the
application, it is the same thing the application is made of.

There are two kinds of script, and they do different jobs:

    a rule          belongs to the document, runs when asked or on a
                    trigger, and changes things: parameters, sketch
                    dimensions, components, properties, materials. It can
                    open a window with sliders on it.

    a code feature  sits in the feature tree like an extrude, runs on every
                    rebuild, and builds a solid: posts, treads, handrails,
                    anything a loop can describe. See geometry.py.

What a rule sees:

    params       parameters: params.width, params.width = 120
    dims         every named sketch dimension: dims.d1, dims.d1 = 40
    sketches     sketches by name, when two share a dimension name
    features     the feature tree by name, for suppressing
    components   an assembly's components: move, suppress, hide, ground
    props        the document's properties: props.PartNumber = "A-1"
    material()   read it, or set it: material("Steel, Mild")
    appearance() the same for how it looks
    form(...)    a window: slider, number, choice, note
    log(...)     output, shown next to the rule
    doc          the document itself, for anything the shorthands miss

What neither may be is a quiet way to run code. A document carries both
inside it, so opening somebody else's file would otherwise be enough to
execute whatever they put there. iLogic has that problem. This does not:
nothing runs until the document is trusted, and trust comes from the
person at the keyboard, never from the file. It can be given for one
document for this session, or for a whole folder for good.

Being plain about the rest: this is not a sandbox. Python cannot be made
into one from the inside, and the short list of builtins below will not
stop anybody who means it. That list is there to keep scripts readable
and the useful names obvious. The trust gate is the protection.
"""

from __future__ import annotations

import io
import math
import os
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple


# --------------------------------------------------------------------- trust
#
# Trust answers one question, "may this file's code run on my machine",
# and the file is never the one asked. It is held in two places: a set of
# paths the user allowed during this session, and a list of folders they
# allowed for good, kept in their own preferences. Neither is ever written
# into a document.

_TRUSTED_PATHS: set = set()


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path)) if path else ""


def trust_path(path: str) -> None:
    """Allow this file's code for the rest of the session."""
    if path:
        _TRUSTED_PATHS.add(_norm(path))


def trusted_folders() -> List[str]:
    try:
        from . import prefs
        return [_norm(f) for f in prefs.prefs().trusted_folders if f]
    except Exception:
        return []


def trust_folder(folder: str) -> None:
    """Allow code from everything in this folder, from now on."""
    from . import prefs

    held = prefs.prefs()
    folder = os.path.abspath(folder)
    if _norm(folder) not in trusted_folders():
        held.trusted_folders.append(folder)
        held.save()


def is_trusted_path(path: str) -> bool:
    if not path:
        return False
    key = _norm(path)
    if key in _TRUSTED_PATHS:
        return True
    return any(key == f or key.startswith(f + os.sep)
               for f in trusted_folders())


def trust_document(document) -> List[str]:
    """Allow this document's code, and that of everything it places.

    Trusting an assembly is trusting what you opened, and what you opened
    is the assembly with its parts in it: a part whose railing is a code
    feature should not come up broken because nobody trusted it on its
    own. Hands back the paths it trusted, so whatever built them before
    can forget the answer it got while they were not allowed to run.
    """
    rules = getattr(document, "rules", None)
    if rules is not None:
        rules.trusted = True
    trusted: List[str] = []
    path = getattr(document, "path", "") or ""
    if path:
        trust_path(path)
        trusted.append(path)
    for component in _component_paths(document, set()):
        trust_path(component)
        trusted.append(component)
    return trusted


def _component_paths(document, seen: set, depth: int = 0) -> List[str]:
    out: List[str] = []
    resolved = getattr(document, "resolved_components", None)
    if resolved is None:
        return out
    try:
        pairs = resolved()
    except Exception:
        return out
    for ref, path in pairs:
        if not path and os.path.isabs(getattr(ref, "path", "") or ""):
            path = ref.path
        key = _norm(path or "")
        if not path or key in seen:
            continue
        seen.add(key)
        out.append(path)
        if depth < 16 and path.lower().endswith(".adat") and os.path.exists(path):
            try:
                from .assembly import AssemblyDocument
                out += _component_paths(AssemblyDocument.load(path), seen,
                                        depth + 1)
            except Exception:
                pass
    return out


def document_trusted(document) -> bool:
    """Whether this document's code may run: rules and code features both."""
    rules = getattr(document, "rules", None)
    if rules is not None and rules.trusted:
        return True
    return is_trusted_path(getattr(document, "path", "") or "")


# --------------------------------------------------------------------- forms
#
# A rule that only runs is half of what people want from one. The other
# half is a rule that asks: a window with a slider on it, and the model
# moving while you drag. A form is a few lines in the rule itself, because
# the rule already knows which parameters it cares about and a designer
# would only be somewhere else to keep that same list.
#
# The controls are described here, where they are only data. The window
# that shows them lives in the interface and installs itself through
# show_form. With nothing installed, which is how a headless build and
# every test runs, a form is a no-op that reports nobody answered it.


@dataclass
class Control:
    """One row on a form, naming the parameter it drives."""

    kind: str = "number"            # slider / number / choice / label
    param: str = ""
    label: str = ""
    low: float = 0.0
    high: float = 100.0
    step: float = 1.0
    options: List[str] = field(default_factory=list)


def slider(param: str, low: float, high: float, step: float = 1.0,
           label: str = "") -> Control:
    """A parameter you drag rather than type."""
    return Control(kind="slider", param=param, label=label or param,
                   low=float(low), high=float(high), step=float(step))


def number(param: str, label: str = "") -> Control:
    return Control(kind="number", param=param, label=label or param)


def choice(param: str, options: Sequence[str], label: str = "") -> Control:
    return Control(kind="choice", param=param, label=label or param,
                   options=[str(o) for o in options])


def note(text: str) -> Control:
    """A line of explanation, driving nothing."""
    return Control(kind="label", label=text)


# Set by the interface. Takes (document, title, controls) and returns
# True when the person accepted the form.
show_form: Optional[Callable[[Any, str, List[Control]], bool]] = None

# True while rules are running because something happened, rather than
# because somebody asked. An automatic run never stops to open a window.
_automatic = False


def forms_allowed() -> bool:
    return show_form is not None and not _automatic


class RuleError(RuntimeError):
    """A script that would not run, with the line it failed on."""


# ---------------------------------------------------------------- the rule


# What can set a rule off, besides somebody pressing Run.
TRIGGERS = ("on_rebuild", "on_open", "before_save")


@dataclass
class Rule:
    """One named piece of logic belonging to a document."""

    id: int = 0
    name: str = "Rule"
    source: str = ""
    enabled: bool = True
    # after the document rebuilds
    on_rebuild: bool = True
    # once the document is open and allowed to run
    on_open: bool = False
    # just before the document is written to disk
    before_save: bool = False
    # parameters whose change sets this rule off, whatever else it waits for
    watch: List[str] = field(default_factory=list)
    # shown as a button on the panel's Forms page, for rules that ask
    button: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "name": self.name, "source": self.source,
                "enabled": self.enabled, "on_rebuild": self.on_rebuild,
                "on_open": self.on_open, "before_save": self.before_save,
                "watch": list(self.watch), "button": self.button}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Rule":
        return cls(id=int(data.get("id", 0)),
                   name=str(data.get("name", "Rule")),
                   source=str(data.get("source", "")),
                   enabled=bool(data.get("enabled", True)),
                   on_rebuild=bool(data.get("on_rebuild", True)),
                   on_open=bool(data.get("on_open", False)),
                   before_save=bool(data.get("before_save", False)),
                   watch=[str(w) for w in data.get("watch", []) or []],
                   button=bool(data.get("button", False)))

    @property
    def triggers(self) -> List[str]:
        out = [t for t in TRIGGERS if getattr(self, t)]
        if self.watch:
            out.append("watching " + ", ".join(self.watch))
        return out


@dataclass
class RuleResult:
    """What happened when a rule ran."""

    rule: str = ""
    ok: bool = True
    output: str = ""
    error: str = ""
    line: int = 0
    changed: List[str] = field(default_factory=list)

    def summary(self) -> str:
        if not self.ok:
            where = " (line %d)" % self.line if self.line else ""
            return "%s failed%s: %s" % (self.rule, where, self.error)
        if self.changed:
            return "%s set %s" % (self.rule, ", ".join(self.changed))
        return "%s ran" % self.rule


# ------------------------------------------------------------ what it sees


class Parameters:
    """The parameter table as a rule wants to see it.

    ``params.width`` reads the value, ``params.width = 120`` writes the
    expression, and a name that is not there yet is created rather than
    refused, because a rule that has to check first is a rule full of
    checks. Writing back the value that is already there is not a change,
    so a rule that keeps something in proportion settles instead of
    asking for a rebuild forever.
    """

    def __init__(self, table, changes: Optional[List[str]] = None) -> None:
        object.__setattr__(self, "_table", table)
        object.__setattr__(self, "changed",
                           changes if changes is not None else [])

    def _set(self, name: str, value: Any) -> None:
        table = object.__getattribute__(self, "_table")
        text = value if isinstance(value, str) else repr(float(value))
        if name in table:
            if table[name].expression == text:
                return
            table.set_expression(name, text)
        else:
            table.add(name, text)
        object.__getattribute__(self, "changed").append(name)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        table = object.__getattribute__(self, "_table")
        if name not in table:
            raise AttributeError("there is no parameter called %r" % name)
        return table[name].value

    def __setattr__(self, name: str, value: Any) -> None:
        self._set(name, value)

    def __getitem__(self, name: str) -> Any:
        return getattr(self, name)

    def __setitem__(self, name: str, value: Any) -> None:
        self._set(name, value)

    def __contains__(self, name: object) -> bool:
        return name in object.__getattribute__(self, "_table")

    def __iter__(self):
        return iter(object.__getattribute__(self, "_table").names())

    def names(self) -> List[str]:
        return list(object.__getattribute__(self, "_table").names())

    def expression(self, name: str) -> str:
        return object.__getattribute__(self, "_table")[name].expression


class Features:
    """The feature tree by name, for the handful of things rules do to it."""

    def __init__(self, document, changes: Optional[List[str]] = None) -> None:
        self._document = document
        self._changes = changes if changes is not None else []

    def _all(self):
        return list(getattr(self._document, "features", ()) or ())

    def __getitem__(self, name: str):
        for feature in self._all():
            if feature.name == name:
                return feature
        raise KeyError("there is no feature called %r.  There are: %s"
                       % (name, ", ".join(self.names()) or "none"))

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(str(exc)) from exc

    def __contains__(self, name: object) -> bool:
        return any(f.name == name for f in self._all())

    def __iter__(self):
        return iter(self._all())

    def names(self) -> List[str]:
        return [f.name for f in self._all()]

    def suppress(self, name: str, suppressed: bool = True) -> None:
        feature = self[name]
        if feature.suppressed != bool(suppressed):
            feature.suppressed = bool(suppressed)
            self._changes.append("feature " + name)


class _Dims:
    """Named dimensions, from one sketch or from all of them.

    A sketch dimension is d1, d2 and so on, per sketch, which means two
    sketches can both have a d1. Asked for from ``dims`` such a name is
    refused, with the sketches that share it named, rather than quietly
    changing whichever was found first.
    """

    def __init__(self, sketches: List[Tuple[str, Any]],
                 changes: List[str]) -> None:
        object.__setattr__(self, "_sketches", sketches)
        object.__setattr__(self, "_changes", changes)

    def _find(self, name: str):
        hits = []
        for label, sketch in object.__getattribute__(self, "_sketches"):
            found = sketch.dimension(name)
            if found is not None:
                hits.append((label, found))
        if not hits:
            raise AttributeError("there is no dimension called %r" % name)
        if len(hits) > 1:
            raise AttributeError(
                "%r is in more than one sketch (%s); say which with "
                "sketches[\"%s\"].dims.%s" % (name, ", ".join(h[0] for h in hits),
                                              hits[0][0], name))
        return hits[0]

    def __getattr__(self, name: str) -> float:
        if name.startswith("_"):
            raise AttributeError(name)
        return float(self._find(name)[1].value)

    def __setattr__(self, name: str, value: Any) -> None:
        label, constraint = self._find(name)
        text = value if isinstance(value, str) else repr(float(value))
        if constraint.expression == text:
            return
        constraint.expression = text
        try:
            constraint.value = float(value)
        except (TypeError, ValueError):
            pass
        object.__getattribute__(self, "_changes").append(
            "%s.%s" % (label, name))

    def __getitem__(self, name: str) -> float:
        return getattr(self, name)

    def __setitem__(self, name: str, value: Any) -> None:
        setattr(self, name, value)

    def __contains__(self, name: object) -> bool:
        try:
            self._find(str(name))
            return True
        except AttributeError:
            return False

    def names(self) -> List[str]:
        out = []
        for _label, sketch in object.__getattribute__(self, "_sketches"):
            for c in sketch.constraints.values():
                if c.name and c.is_dimension and c.name not in out:
                    out.append(c.name)
        return out


class _Sketch:
    def __init__(self, label: str, sketch, changes: List[str]) -> None:
        self.name = label
        self.sketch = sketch
        self.dims = _Dims([(label, sketch)], changes)


class Sketches:
    """Sketches by name, for reaching a dimension two sketches both have."""

    def __init__(self, document, changes: List[str]) -> None:
        self._items = []
        for feature in getattr(document, "features", ()) or ():
            sketch = getattr(feature, "sketch", None)
            if sketch is not None and hasattr(sketch, "dimension"):
                self._items.append((feature.name, sketch))
        self._changes = changes

    def all(self) -> List[Tuple[str, Any]]:
        return list(self._items)

    def __getitem__(self, name: str) -> _Sketch:
        for label, sketch in self._items:
            if label == name:
                return _Sketch(label, sketch, self._changes)
        raise KeyError("there is no sketch called %r.  There are: %s"
                       % (name, ", ".join(self.names()) or "none"))

    def __contains__(self, name: object) -> bool:
        return any(label == name for label, _s in self._items)

    def names(self) -> List[str]:
        return [label for label, _s in self._items]


class Properties:
    """The document's properties: props.PartNumber, props["Designer"]."""

    def __init__(self, document, changes: List[str]) -> None:
        object.__setattr__(self, "_doc", document)
        object.__setattr__(self, "_changes", changes)

    def _held(self) -> Dict[str, str]:
        doc = object.__getattribute__(self, "_doc")
        if getattr(doc, "properties", None) is None:
            doc.properties = {}
        return doc.properties

    def __getattr__(self, name: str) -> str:
        if name.startswith("_"):
            raise AttributeError(name)
        return self._held().get(name, "")

    def __setattr__(self, name: str, value: Any) -> None:
        held = self._held()
        text = str(value)
        if held.get(name) == text:
            return
        held[name] = text
        object.__getattribute__(self, "_changes").append("property " + name)

    def __getitem__(self, name: str) -> str:
        return getattr(self, name)

    def __setitem__(self, name: str, value: Any) -> None:
        setattr(self, name, value)

    def names(self) -> List[str]:
        return sorted(self._held())


class Component:
    """One component of an assembly, the way a rule handles it."""

    def __init__(self, occurrence, changes: List[str]) -> None:
        self._o = occurrence
        self._changes = changes

    def _mark(self, what: str) -> None:
        self._changes.append("%s %s" % (self._o.name, what))

    @property
    def name(self) -> str:
        return self._o.name

    @property
    def label(self) -> str:
        return self._o.label

    @property
    def path(self) -> str:
        return self._o.ref.path

    @property
    def position(self) -> Tuple[float, float, float]:
        return tuple(float(c) for c in self._o.placement.position)

    def _flag(self, attr: str, value: bool) -> None:
        if getattr(self._o, attr) != bool(value):
            setattr(self._o, attr, bool(value))
            self._mark(attr)

    suppressed = property(lambda self: self._o.suppressed,
                          lambda self, v: self._flag("suppressed", v))
    visible = property(lambda self: self._o.visible,
                       lambda self, v: self._flag("visible", v))
    grounded = property(lambda self: self._o.grounded,
                        lambda self, v: self._flag("grounded", v))

    def move(self, x: float = 0, y: float = 0, z: float = 0) -> None:
        """Shift it by that much from where it is."""
        p = self._o.placement.position
        self.place(p[0] + x, p[1] + y, p[2] + z)

    def place(self, x: float, y: float, z: float,
              rx: float = None, ry: float = None, rz: float = None) -> None:
        """Put it at a position, and optionally a rotation in degrees.

        Rotations are applied about X, then Y, then Z. Left out, the
        rotation it already has is kept.
        """
        placement = self._o.placement
        placement.position = [float(x), float(y), float(z)]
        if rx is not None or ry is not None or rz is not None:
            from . import constraints3d
            placement.rotation = list(constraints3d.rotation_vector(
                _euler(rx or 0.0, ry or 0.0, rz or 0.0)))
        self._mark("placement")

    def __repr__(self) -> str:
        return "<Component %s>" % self.name


def _euler(rx: float, ry: float, rz: float):
    import numpy as np

    a, b, c = (math.radians(v) for v in (rx, ry, rz))
    x = np.array([[1, 0, 0], [0, math.cos(a), -math.sin(a)],
                  [0, math.sin(a), math.cos(a)]])
    y = np.array([[math.cos(b), 0, math.sin(b)], [0, 1, 0],
                  [-math.sin(b), 0, math.cos(b)]])
    z = np.array([[math.cos(c), -math.sin(c), 0],
                  [math.sin(c), math.cos(c), 0], [0, 0, 1]])
    return z @ y @ x


class Components:
    """An assembly's components.  Empty for anything that is not one."""

    def __init__(self, document, changes: List[str]) -> None:
        self._occ = list(getattr(document, "occurrences", ()) or ())
        self._changes = changes

    def __getitem__(self, name: str) -> Component:
        for o in self._occ:
            if o.name == name:
                return Component(o, self._changes)
        for o in self._occ:
            if o.label == name:
                return Component(o, self._changes)
        raise KeyError("there is no component called %r.  There are: %s"
                       % (name, ", ".join(self.names()[:12]) or "none"))

    def __contains__(self, name: object) -> bool:
        return any(o.name == name or o.label == name for o in self._occ)

    def __iter__(self):
        return iter(Component(o, self._changes) for o in self._occ)

    def __len__(self) -> int:
        return len(self._occ)

    def names(self) -> List[str]:
        return [o.name for o in self._occ]

    def like(self, text: str) -> List[Component]:
        """Every component whose name contains that text."""
        low = text.lower()
        return [Component(o, self._changes) for o in self._occ
                if low in o.name.lower()]


SAFE_BUILTINS = {
    name: getattr(__builtins__, name, None) if not isinstance(__builtins__, dict)
    else __builtins__.get(name)
    for name in (
        "abs", "all", "any", "bool", "dict", "divmod", "enumerate", "filter",
        "float", "format", "int", "len", "list", "map", "max", "min", "pow",
        "range", "repr", "reversed", "round", "set", "sorted", "str", "sum",
        "tuple", "zip", "isinstance", "getattr", "setattr", "hasattr",
        "print", "True", "False", "None", "Exception", "ValueError",
        "TypeError", "KeyError", "AttributeError", "ZeroDivisionError",
    )
}
SAFE_BUILTINS = {k: v for k, v in SAFE_BUILTINS.items() if v is not None}


def environment(document) -> Dict[str, Any]:
    """The names a rule is given, and nothing else by default."""
    changes: List[str] = []
    params = Parameters(document.params, changes)
    sketches = Sketches(document, changes)
    lines: List[str] = []

    def log(*parts: Any) -> None:
        lines.append(" ".join(str(p) for p in parts))

    def form(title: str, *controls: Control) -> bool:
        """Ask, with a window.  False when nobody answered."""
        if not forms_allowed():
            lines.append("(form %r not shown: %s)"
                         % (title, "it ran by itself" if _automatic
                            else "no interface"))
            return False
        return bool(show_form(document, title, list(controls)))

    def material(name: Optional[str] = None) -> str:
        """The material, or set it: material("Steel, Mild")."""
        if name is not None and hasattr(document, "material"):
            from . import materials
            if name not in materials.library().materials:
                raise KeyError("there is no material called %r" % name)
            if document.material != name:
                document.material = name
                changes.append("material")
        return getattr(document, "material", "")

    def appearance(name: Optional[str] = None) -> str:
        """How it looks, or set it: appearance("Paint, Machine Yellow")."""
        if name is not None and hasattr(document, "appearance"):
            from . import materials
            if name and name not in materials.library().appearances:
                raise KeyError("there is no appearance called %r" % name)
            if document.appearance != name:
                document.appearance = name
                changes.append("appearance")
        return getattr(document, "appearance", "")

    return {
        "params": params,
        "dims": _Dims(sketches.all(), changes),
        "sketches": sketches,
        "features": Features(document, changes),
        "components": Components(document, changes),
        "props": Properties(document, changes),
        "material": material,
        "appearance": appearance,
        "doc": document,
        "log": log,
        "math": math,
        "units": getattr(document, "units", "mm"),
        "form": form,
        "slider": slider,
        "number": number,
        "choice": choice,
        "note": note,
        "_log_lines": lines,
        "_changes": changes,
        "__builtins__": dict(SAFE_BUILTINS),
    }


def _failure_line(exc: BaseException, filename: str) -> int:
    for frame in reversed(traceback.extract_tb(exc.__traceback__)):
        if frame.filename == filename:
            return frame.lineno or 0
    return 0


def run(rule: Rule, document) -> RuleResult:
    """Run one rule against one document, catching whatever it does.

    A rule that throws is a failed rule and not a failed application: the
    message and the line come back in the result, to be shown beside the
    rule that caused them.
    """
    result = RuleResult(rule=rule.name)
    scope = environment(document)
    printed = io.StringIO()
    scope["print"] = lambda *a, **k: print(*a, file=printed, **k)
    filename = "<%s>" % rule.name
    try:
        code = compile(rule.source, filename, "exec")
    except SyntaxError as exc:
        result.ok = False
        result.error = "%s" % exc.msg
        result.line = exc.lineno or 0
        return result
    try:
        exec(code, scope)                                   # noqa: S102
    except Exception as exc:                                # noqa: BLE001
        result.ok = False
        result.error = "%s: %s" % (type(exc).__name__, exc)
        result.line = _failure_line(exc, filename)
    out = "\n".join(scope["_log_lines"])
    extra = printed.getvalue().strip()
    result.output = "\n".join(p for p in (out, extra) if p)
    result.changed = list(scope["_changes"])
    return result


# ------------------------------------------------------------ code features


class Values:
    """Parameter values, read only.  A code feature builds; it does not set.

    A feature that changed a parameter while the model was rebuilding
    would be changing the thing the rebuild was reading, and the next
    rebuild would build something else again. So here they can only be
    read. A rule is the place to change them.
    """

    def __init__(self, scope: Dict[str, float]) -> None:
        object.__setattr__(self, "_scope", dict(scope))

    def __getattr__(self, name: str) -> float:
        scope = object.__getattribute__(self, "_scope")
        if name.startswith("_"):
            raise AttributeError(name)
        if name not in scope:
            raise AttributeError("there is no parameter called %r" % name)
        return float(scope[name])

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("a code feature reads parameters; set %r from "
                             "a rule instead" % name)

    def __getitem__(self, name: str) -> float:
        return getattr(self, name)

    def __contains__(self, name: object) -> bool:
        return name in object.__getattribute__(self, "_scope")

    def get(self, name: str, default: float = 0.0) -> float:
        return float(object.__getattribute__(self, "_scope").get(name,
                                                                 default))

    def names(self) -> List[str]:
        return sorted(object.__getattribute__(self, "_scope"))


def run_code(source: str, scope: Dict[str, float], name: str = "Code"):
    """Run a code feature's script and hand back its solid and its output.

    Raises RuleError, with the line, if the script fails, so the feature
    can show the failure in the tree the way any other failed feature does.
    """
    from . import geometry

    lines: List[str] = []
    answer: List[Any] = []

    def log(*parts: Any) -> None:
        lines.append(" ".join(str(p) for p in parts))

    def result(solid) -> None:
        answer.append(solid)

    names = dict(geometry.namespace())
    names.update({
        "params": Values(scope),
        "log": log,
        "print": log,
        "result": result,
        "__builtins__": dict(SAFE_BUILTINS),
    })
    filename = "<%s>" % name
    try:
        code = compile(source, filename, "exec")
    except SyntaxError as exc:
        raise RuleError("line %d: %s" % (exc.lineno or 0, exc.msg)) from exc
    try:
        exec(code, names)                                   # noqa: S102
    except Exception as exc:                                # noqa: BLE001
        line = _failure_line(exc, filename)
        where = "line %d: " % line if line else ""
        raise RuleError("%s%s: %s" % (where, type(exc).__name__, exc)) \
            from exc

    solid = answer[-1] if answer else names.get("body")
    shape = getattr(solid, "shape", solid)
    if shape is None or not hasattr(shape, "IsNull") or shape.IsNull():
        return None, "\n".join(lines)
    return shape, "\n".join(lines)


# ---------------------------------------------------------------- rule sets


@dataclass
class RuleSet:
    """The rules belonging to one document, and whether they may run.

    ``trusted`` is not saved. A document arrives from disk untrusted every
    time, however many times it has been opened before, unless it sits in
    a folder the user has trusted for good, because the question trust
    answers is "do I want this file's code to run on my machine", and the
    file is not the one to answer it.
    """

    rules: List[Rule] = field(default_factory=list)
    trusted: bool = False
    _next_id: int = 1
    # parameter values as the watching rules last saw them
    _seen: Dict[str, float] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.rules)

    def __iter__(self):
        return iter(self.rules)

    def add(self, name: str = "", source: str = "") -> Rule:
        rule = Rule(id=self._next_id, name=name or self.unique_name(),
                    source=source)
        self._next_id += 1
        self.rules.append(rule)
        return rule

    def unique_name(self, base: str = "Rule") -> str:
        taken = {r.name for r in self.rules}
        index = 0
        while "%s%d" % (base, index) in taken:
            index += 1
        return "%s%d" % (base, index)

    def get(self, rule_id: int) -> Optional[Rule]:
        return next((r for r in self.rules if r.id == rule_id), None)

    def remove(self, rule_id: int) -> bool:
        rule = self.get(rule_id)
        if rule is None:
            return False
        self.rules.remove(rule)
        return True

    @property
    def buttons(self) -> List[Rule]:
        return [r for r in self.rules if r.button and r.enabled]

    def to_list(self) -> List[Dict[str, Any]]:
        return [r.to_dict() for r in self.rules]

    def load(self, data: Any) -> None:
        self.rules = [Rule.from_dict(d) for d in (data or [])
                      if isinstance(d, dict)]
        self._next_id = max([r.id for r in self.rules], default=0) + 1
        self._seen = {}
        # whatever the file said, trust is this machine's to give
        self.trusted = False

    def _moved(self, document) -> List[str]:
        """Parameters whose values changed since the watchers last looked."""
        now = {}
        for name in document.params.names():
            try:
                now[name] = float(document.params[name].value)
            except (TypeError, ValueError):
                continue
        moved = [n for n, v in now.items()
                 if n in self._seen and abs(self._seen[n] - v) > 1e-12]
        moved += [n for n in now if n not in self._seen and self._seen]
        self._seen = now
        return moved

    def run_event(self, document, event: str) -> List[RuleResult]:
        """Run whatever this event sets off: rebuild, open or save.

        These runs are automatic, so forms stay shut: a model that opens a
        window every time it rebuilds is one you cannot work in.
        """
        global _automatic
        if not (self.trusted or document_trusted(document)):
            return []
        moved = self._moved(document) if event == "on_rebuild" else []
        was = _automatic
        _automatic = True
        try:
            out = []
            for rule in self.rules:
                if not rule.enabled:
                    continue
                fires = getattr(rule, event, False)
                if event == "on_rebuild" and rule.watch:
                    fires = fires or any(w in moved for w in rule.watch)
                if fires:
                    out.append(run(rule, document))
        finally:
            _automatic = was
        if event == "on_rebuild" and any(r.changed for r in out):
            # the rules just moved things, so what they moved is the new
            # baseline rather than a change for them to react to again
            self._moved(document)
        return out

    def run_all(self, document, only_on_rebuild: bool = False
                ) -> List[RuleResult]:
        """Run every enabled rule, or, for a rebuild, what a rebuild sets off.

        Asked for directly, every enabled rule runs and forms may open.
        """
        global _automatic
        if only_on_rebuild:
            return self.run_event(document, "on_rebuild")
        if not (self.trusted or document_trusted(document)):
            return []
        was = _automatic
        _automatic = False
        try:
            return [run(rule, document) for rule in self.rules
                    if rule.enabled]
        finally:
            _automatic = was
