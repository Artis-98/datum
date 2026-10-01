"""Parameters across an assembly: every part's, named for its part.

Opened in an assembly, the Parameters table lists the assembly's own
parameters and then every parameter of every part it places, each named
for the part it belongs to: a part numbered Box has its d2 listed as
box_d2.  The prefix is the part number from the part's properties, or its
file name when it has none.

Changing one changes the part.  A plain number, or an expression in the
part's own names, is written straight into the part, which then simply
has that value.  An expression that reaches outside the part, another
part's parameter or one of the assembly's, is the assembly driving the
part: the expression is kept by the assembly, worked out there, and the
part is handed the number.  So a part still opens and builds on its own,
and the assembly is where the relationship lives, which is where somebody
looking for it would look.

Nothing here opens, builds or saves a file by itself.  It is handed two
ways to get at a part, one cheap for reading and one live for changing,
and whoever owns the window decides what those mean.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from . import modelparams
from .params import (
    CONSTANTS, FUNCTIONS, ExpressionError, evaluate, referenced_names,
)

SEPARATOR = "_"


def sanitise(text: str) -> str:
    """A part number made fit to start a parameter name."""
    out = re.sub(r"[^A-Za-z0-9_]+", "_", str(text or "").strip()).strip("_")
    if not out:
        return "part"
    if out[0].isdigit():
        out = "p" + out
    return out


def prefix_for(document: Any, path: str) -> str:
    """The part number, or the file name, as a parameter prefix."""
    properties = getattr(document, "properties", None) or {}
    number = str(properties.get("PartNumber", "") or "").strip()
    if not number:
        number = os.path.splitext(os.path.basename(path or ""))[0]
    return sanitise(number)


def load_unbuilt(path: str):
    """A part read from its file without building it: enough to list it.

    Listing every parameter of every part in an assembly must not mean
    building every part, so the table reads the files and works the
    parameters out from their expressions alone.
    """
    from . import fileformat
    from .document import Document

    opened = fileformat.read(path, expected_type=fileformat.PART)
    document = Document()
    document.load_dict(opened.geometry)
    document.units = opened.manifest.units or document.units
    document.path = path
    document.thumbnail = opened.thumbnail
    document.modified = False
    return document


@dataclass
class Component:
    """One part file the assembly places, and the prefix it goes by."""

    prefix: str
    path: str
    label: str
    document: Any


@dataclass
class ComponentParam:
    """A part's parameter, as the assembly sees it."""

    qualified: str          # box_d2
    name: str               # d2, its name inside the part
    component: Component
    model: bool             # a dimension or feature value, not a user one
    owner: str              # the feature that uses it, inside the part
    unit: str
    expression: str         # as the part has it
    value: float
    error: str = ""
    driven: str = ""        # the assembly's expression, when it drives it
    reference: bool = False


class AssemblyParameters:
    """Every parameter of an assembly and of the parts it places."""

    def __init__(self, assembly: Any,
                 read: Callable[[str], Any],
                 live: Optional[Callable[[str], Any]] = None) -> None:
        """``read`` gives a part for listing, ``live`` one to change.

        Both take an absolute path.  ``live`` defaults to ``read``.
        """
        self.assembly = assembly
        self.read = read
        self.live = live or read
        self._components: Optional[List[Component]] = None

    # -- the parts ----------------------------------------------------------

    def components(self) -> List[Component]:
        """Each part file once, in the order the assembly first places it."""
        if self._components is not None:
            return self._components
        out: List[Component] = []
        seen: Set[str] = set()
        taken: Set[str] = set(self.assembly.params.names())
        for occurrence in getattr(self.assembly, "occurrences", ()):
            path = self.assembly.component_path(occurrence)
            if not path or not path.lower().endswith(".pdat"):
                continue
            key = os.path.normcase(os.path.abspath(path))
            if key in seen:
                continue
            seen.add(key)
            try:
                document = self.read(path)
            except Exception:
                continue
            if document is None:
                continue
            prefix = prefix_for(document, path)
            # two different parts with the same number still need two names
            base, n = prefix, 2
            while prefix in taken:
                prefix = "%s%d" % (base, n)
                n += 1
            taken.add(prefix)
            label = os.path.splitext(os.path.basename(path))[0]
            out.append(Component(prefix, path, label, document))
        self._components = out
        return out

    def forget(self) -> None:
        """Read the parts again next time; one has been changed."""
        self._components = None

    def _component(self, prefix: str) -> Optional[Component]:
        return next((c for c in self.components() if c.prefix == prefix),
                    None)

    def split(self, qualified: str) -> Tuple[Optional[Component], str]:
        """box_d2 -> (the Box component, "d2")."""
        best = None
        for component in self.components():
            head = component.prefix + SEPARATOR
            if qualified.startswith(head) and len(qualified) > len(head):
                if best is None or len(component.prefix) > len(best.prefix):
                    best = component
        if best is None:
            return None, ""
        return best, qualified[len(best.prefix) + len(SEPARATOR):]

    # -- reading ------------------------------------------------------------

    def rows(self) -> List[ComponentParam]:
        """Every part's parameters, qualified, part by part."""
        drivers = getattr(self.assembly, "drivers", {}) or {}
        out: List[ComponentParam] = []
        for component in self.components():
            document = component.document
            head = component.prefix + SEPARATOR
            if hasattr(document, "model_parameters"):
                for m in document.model_parameters():
                    qualified = head + m.name
                    out.append(ComponentParam(
                        qualified, m.name, component, True, m.owner, m.unit,
                        m.expression, m.value, m.error,
                        drivers.get(qualified, ""), m.reference))
            document.params.evaluate_all()
            for p in document.params:
                qualified = head + p.name
                out.append(ComponentParam(
                    qualified, p.name, component, False, "", p.unit,
                    p.expression, p.value, p.error,
                    drivers.get(qualified, "")))
        return out

    def part_scope(self) -> Dict[str, float]:
        """Every part parameter's value by its qualified name."""
        return {row.qualified: row.value for row in self.rows()}

    def scope(self) -> Tuple[Dict[str, float], Dict[str, str]]:
        """Assembly and part parameters together, drivers worked out.

        A driven part parameter takes its value from its driver, so an
        assembly parameter written in terms of it, or another driver, reads
        what it is about to become rather than what it was.
        """
        fixed = self.part_scope()
        expressions: Dict[str, str] = {
            p.name: p.expression for p in self.assembly.params}
        drivers = getattr(self.assembly, "drivers", {}) or {}
        for qualified, text in drivers.items():
            expressions[qualified] = text
            fixed.pop(qualified, None)
        order, cyclic = modelparams._order(expressions)
        scope = dict(fixed)
        errors: Dict[str, str] = {}
        for name in order:
            if name in cyclic:
                errors[name] = "circular reference"
                scope[name] = 0.0
                continue
            try:
                scope[name] = evaluate(expressions[name], scope)
            except ExpressionError as exc:
                errors[name] = str(exc)
                scope[name] = 0.0
        return scope, errors

    def refresh_assembly(self) -> None:
        """Give the assembly's own parameters the part values to read."""
        self.assembly.component_values = self.part_scope()
        self.assembly.params.evaluate_all()

    # -- changing -----------------------------------------------------------

    def _local(self, component: Component, text: str) -> Optional[str]:
        """The expression in the part's own names, if it can be written so.

        None when it reaches outside the part: another part, or the
        assembly.
        """
        names = set(modelparams.taken_names(component.document)) \
            if hasattr(component.document, "model_parameters") \
            else set(component.document.params.names())
        mapping: Dict[str, str] = {}
        for name in referenced_names(text):
            if name in FUNCTIONS or name in CONSTANTS:
                continue
            owner, inner = self.split(name)
            if owner is component and inner in names:
                mapping[name] = inner
                continue
            if name in names and name not in self.assembly.params \
                    and self.split(name)[0] is None:
                continue
            return None
        return modelparams._substitute(text, mapping)

    def set(self, qualified: str, text: str) -> List[Any]:
        """Change a part's parameter from the assembly.

        Returns the part documents that changed, which want rebuilding.
        """
        text = str(text).strip()
        if not text:
            raise ExpressionError("empty expression")
        component, name = self.split(qualified)
        if component is None:
            raise ExpressionError("there is no part parameter called %r"
                                  % qualified)
        drivers = self.assembly.drivers
        local = self._local(component, text)
        changed: List[Any] = []
        if local is not None:
            drivers.pop(qualified, None)
            document = self._live(component)
            self._write(document, name, local)
            changed.append(document)
        else:
            drivers[qualified] = text
        self.assembly.modified = True
        self.forget()
        # whatever the assembly drives from what just changed moves too
        changed += [d for d in self.apply() if d not in changed]
        return changed

    def release(self, qualified: str) -> None:
        """Stop the assembly driving a part parameter; it keeps its value."""
        if self.assembly.drivers.pop(qualified, None) is not None:
            self.assembly.modified = True

    def apply(self) -> List[Any]:
        """Work every driver out and hand each part its number.

        Returns the part documents that changed.  A driver that cannot be
        worked out leaves its part alone and says why in ``errors``.
        """
        drivers = getattr(self.assembly, "drivers", {}) or {}
        if not drivers:
            return []
        scope, errors = self.scope()
        self.errors = errors
        changed: List[Any] = []
        for qualified in list(drivers):
            if qualified in errors:
                continue
            component, name = self.split(qualified)
            if component is None:
                continue
            value = modelparams.number(scope[qualified])
            current = self._current(component.document, name)
            if current is not None and _same(current, value):
                continue
            document = self._live(component)
            self._write(document, name, value)
            if document not in changed:
                changed.append(document)
        if changed:
            self.forget()
        return changed

    def _live(self, component: Component):
        document = self.live(component.path)
        if document is not component.document:
            component.document = document
        return document

    @staticmethod
    def _current(document, name: str) -> Optional[str]:
        if name in document.params:
            return document.params[name].expression
        if hasattr(document, "model_parameters"):
            found = modelparams.find(document, name)
            if found is not None:
                return found.expression
        return None

    @staticmethod
    def _write(document, name: str, text: str) -> None:
        if hasattr(document, "set_parameter"):
            document.set_parameter(name, text)
        else:
            document.params.set_expression(name, text)
        document.modified = True


def _same(a: str, b: str) -> bool:
    try:
        return abs(float(a) - float(b)) < 1e-12
    except (TypeError, ValueError):
        return a.strip() == b.strip()
