"""dLogic: rules that drive a document.

Inventor has iLogic, and the reason people ask for it is not the language,
it is that a model can be told how to behave rather than only what shape
to be. A rule reads the parameters, decides something, and writes them
back, and the model rebuilds around the answer. That is the whole idea and
it is worth having.

The language here is Python, for one reason that matters: it is the
language DATUM itself is written in, so a rule is not a guest in the
application, it is the same thing the application is made of. Anything the
core can do a rule can do, and there is no bridge to maintain between them.

A rule sees a small, deliberate set of names:

    params      the parameter table, by name or by attribute:
                params.width, params["width"], params.width = 120
    doc         the document itself, for anything the shorthands miss
    features    the feature tree by name, for suppressing and renaming
    log(...)    output, collected and shown next to the rule
    units       the document's units, "mm" or "in"

What a rule must not be is a quiet way to run code. A document carries its
rules inside it, so opening somebody else's file would otherwise be enough
to execute whatever they put there. iLogic has that problem. This does
not: rules never run until the document they are in is trusted, trust is
per document and per session, and it is the user who gives it.

Being plain about the rest of it: this is not a sandbox. Python cannot be
made into one from the inside, and the short list of builtins below will
not stop anybody who means it. That list is there to keep rules readable
and to make the useful names obvious, not to contain them. The thing that
protects you is the trust gate, which is why it is the part with no way
around it.
"""

from __future__ import annotations

import io
import math
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence


# --------------------------------------------------------------------- forms
#
# A rule that only runs is half of what people want from one. The other
# half is a rule that asks: a window with a slider on it, and the model
# moving while you drag. iLogic calls those Forms and builds them in a
# designer; here a form is three lines in the rule itself, because the
# rule already knows which parameters it cares about and a designer would
# only be somewhere else to keep that same list.
#
#     form("Size",
#          slider("width", 20, 200),
#          slider("height", 10, 100),
#          choice("material", ["Steel, Mild", "Aluminium 6061"]))
#
# The controls are described here, in core, where they are only data. The
# window that shows them lives in the interface and installs itself
# through show_form. With nothing installed, which is how a headless
# build and every test runs, a form is a no-op that reports that nobody
# answered it, so a rule full of forms still runs to the end.


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

# True while rules are running because the document rebuilt, rather than
# because somebody asked. A rebuild must never stop to open a window.
_automatic = False


def forms_allowed() -> bool:
    return show_form is not None and not _automatic


class RuleError(RuntimeError):
    """A rule that would not run, with the line it failed on."""


@dataclass
class Rule:
    """One named piece of logic belonging to a document."""

    id: int = 0
    name: str = "Rule"
    source: str = ""
    enabled: bool = True
    # whether it runs by itself after the document rebuilds, or only when
    # somebody asks it to
    on_rebuild: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "name": self.name, "source": self.source,
                "enabled": self.enabled, "on_rebuild": self.on_rebuild}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Rule":
        return cls(id=int(data.get("id", 0)),
                   name=str(data.get("name", "Rule")),
                   source=str(data.get("source", "")),
                   enabled=bool(data.get("enabled", True)),
                   on_rebuild=bool(data.get("on_rebuild", True)))


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


class Parameters:
    """The parameter table as a rule wants to see it.

    ``params.width`` reads the value, ``params.width = 120`` writes the
    expression, and a name that is not there yet is created rather than
    raising, because a rule that has to check first is a rule full of
    checks. Every write is recorded, so the caller knows whether the
    model needs building again.
    """

    def __init__(self, table) -> None:
        object.__setattr__(self, "_table", table)
        object.__setattr__(self, "changed", [])

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

    def __init__(self, document) -> None:
        self._document = document

    def _all(self):
        return list(getattr(self._document, "features", ()) or ())

    def __getitem__(self, name: str):
        for feature in self._all():
            if feature.name == name:
                return feature
        raise KeyError("there is no feature called %r" % name)

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
        self[name].suppressed = bool(suppressed)


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
    params = Parameters(document.params)
    lines: List[str] = []

    def log(*parts: Any) -> None:
        lines.append(" ".join(str(p) for p in parts))

    def form(title: str, *controls: Control) -> bool:
        """Ask, with a window.  False when nobody answered."""
        if not forms_allowed():
            lines.append("(form %r not shown: %s)"
                         % (title, "a rebuild is running" if _automatic
                            else "no interface"))
            return False
        return bool(show_form(document, title, list(controls)))

    return {
        "params": params,
        "doc": document,
        "features": Features(document),
        "log": log,
        "math": math,
        "units": getattr(document, "units", "mm"),
        "form": form,
        "slider": slider,
        "number": number,
        "choice": choice,
        "note": note,
        "_log_lines": lines,
        "__builtins__": dict(SAFE_BUILTINS),
    }


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
    try:
        code = compile(rule.source, "<%s>" % rule.name, "exec")
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
        tb = traceback.extract_tb(exc.__traceback__)
        for frame in reversed(tb):
            if frame.filename == "<%s>" % rule.name:
                result.line = frame.lineno or 0
                break
    out = "\n".join(scope["_log_lines"])
    extra = printed.getvalue().strip()
    result.output = "\n".join(p for p in (out, extra) if p)
    result.changed = list(scope["params"].changed)
    return result


@dataclass
class RuleSet:
    """The rules belonging to one document, and whether they may run.

    ``trusted`` is not saved. A document arrives from disk untrusted every
    time, however many times it has been opened before, because the
    question trust answers is "do I want this file's code to run on my
    machine", and the file is not the one to answer it.
    """

    rules: List[Rule] = field(default_factory=list)
    trusted: bool = False
    _next_id: int = 1

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

    def to_list(self) -> List[Dict[str, Any]]:
        return [r.to_dict() for r in self.rules]

    def load(self, data: Any) -> None:
        self.rules = [Rule.from_dict(d) for d in (data or [])
                      if isinstance(d, dict)]
        self._next_id = max([r.id for r in self.rules], default=0) + 1
        # whatever the file said, trust is this machine's to give
        self.trusted = False

    def run_all(self, document, only_on_rebuild: bool = False
                ) -> List[RuleResult]:
        """Run every enabled rule, in order, and report on each.

        ``only_on_rebuild`` also means "nobody asked for this", so forms
        stay shut: a model that opens a window every time it rebuilds is
        a model you cannot work in.
        """
        global _automatic
        if not self.trusted:
            return []
        was = _automatic
        _automatic = only_on_rebuild
        try:
            out = []
            for rule in self.rules:
                if not rule.enabled:
                    continue
                if only_on_rebuild and not rule.on_rebuild:
                    continue
                out.append(run(rule, document))
        finally:
            _automatic = was
        return out
