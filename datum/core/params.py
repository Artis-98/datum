"""Named parameters with safe expression evaluation.

Every numeric input on a feature is stored as an *expression string*, not a
float.  That is what makes the model parametric: ``"width / 2 - wall"`` keeps
tracking its inputs after a rebuild.  Expressions are evaluated against the
document's parameter table with a whitelisted AST walker - no ``eval`` of
arbitrary code.
"""

from __future__ import annotations

import ast
import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

# --------------------------------------------------------------------------
# functions and constants a user may call from an expression
# --------------------------------------------------------------------------

FUNCTIONS: Dict[str, Callable[..., float]] = {
    "abs": abs,
    "acos": lambda x: math.degrees(math.acos(x)),
    "asin": lambda x: math.degrees(math.asin(x)),
    "atan": lambda x: math.degrees(math.atan(x)),
    "atan2": lambda y, x: math.degrees(math.atan2(y, x)),
    "ceil": math.ceil,
    "cos": lambda x: math.cos(math.radians(x)),
    "exp": math.exp,
    "floor": math.floor,
    "hypot": math.hypot,
    "ln": math.log,
    "log": math.log10,
    "max": max,
    "min": min,
    "pow": pow,
    "round": round,
    "sign": lambda x: (x > 0) - (x < 0),
    "sin": lambda x: math.sin(math.radians(x)),
    "sqrt": math.sqrt,
    "tan": lambda x: math.tan(math.radians(x)),
}

CONSTANTS: Dict[str, float] = {
    "pi": math.pi,
    "e": math.e,
}

# Unit suffixes are accepted and normalised to mm / degrees, which are the
# document's internal units.
LENGTH_UNITS = {
    "mm": 1.0,
    "cm": 10.0,
    "m": 1000.0,
    "in": 25.4,
    "inch": 25.4,
    "ft": 304.8,
    "thou": 0.0254,
    "mil": 0.0254,
}
ANGLE_UNITS = {
    "deg": 1.0,
    "rad": 180.0 / math.pi,
}
ALL_UNITS = {**LENGTH_UNITS, **ANGLE_UNITS}

_UNIT_RE = re.compile(
    r"(?<=[\d\)\s])\s*(" + "|".join(sorted(ALL_UNITS, key=len, reverse=True)) + r")\b"
)

IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*$")


class ExpressionError(ValueError):
    """Raised when an expression cannot be parsed or evaluated."""


# --------------------------------------------------------------------------


def _strip_units(text: str) -> str:
    """Rewrite ``12mm`` / ``30 deg`` into plain numbers in document units."""

    def repl(m: "re.Match[str]") -> str:
        return " * %.12g" % ALL_UNITS[m.group(1)]

    return _UNIT_RE.sub(repl, text)


_ALLOWED_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.USub,
    ast.UAdd,
    ast.Constant,
    ast.Name,
    ast.Load,
    ast.Call,
    ast.Compare,
    ast.Lt,
    ast.Gt,
    ast.LtE,
    ast.GtE,
    ast.Eq,
    ast.NotEq,
    ast.IfExp,
    ast.Tuple,
)


def _check(node: ast.AST) -> None:
    for child in ast.walk(node):
        if not isinstance(child, _ALLOWED_NODES):
            raise ExpressionError(
                "%s is not allowed in an expression" % type(child).__name__
            )
        if isinstance(child, ast.Call):
            if not isinstance(child.func, ast.Name) or child.func.id not in FUNCTIONS:
                raise ExpressionError("unknown function in expression")


def referenced_names(expression: str) -> List[str]:
    """Parameter names an expression depends on (functions/constants excluded)."""
    try:
        tree = ast.parse(_strip_units(str(expression)), mode="eval")
    except SyntaxError:
        return []
    out: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if node.id in FUNCTIONS or node.id in CONSTANTS:
                continue
            if node.id not in out:
                out.append(node.id)
    return out


@lru_cache(maxsize=8192)
def _compiled(text: str) -> Tuple[Any, str]:
    """An expression parsed, checked and compiled once, however often used.

    The sketcher works the whole part's parameters out on every mouse move
    of a drag, and parsing is most of what that costs.  The answer depends
    on the text alone, so it is kept: (code, "") or (None, the problem).
    """
    try:
        tree = ast.parse(_strip_units(text), mode="eval")
    except SyntaxError:
        return None, "cannot parse %r" % text
    try:
        _check(tree)
    except ExpressionError as exc:
        return None, str(exc)
    return compile(tree, "<expr>", "eval"), ""


def evaluate(expression: Any, scope: Optional[Dict[str, float]] = None) -> float:
    """Evaluate an expression string to a float in document units."""
    if isinstance(expression, (int, float)):
        return float(expression)

    text = str(expression).strip()
    if not text:
        raise ExpressionError("empty expression")

    code, problem = _compiled(text)
    if code is None:
        raise ExpressionError(problem)

    names: Dict[str, Any] = dict(CONSTANTS)
    if scope:
        names.update(scope)
    names.update(FUNCTIONS)

    try:
        value = eval(  # noqa: S307 - AST is whitelisted above
            code, {"__builtins__": {}}, names
        )
    except NameError as exc:
        raise ExpressionError(str(exc).replace("name", "parameter")) from exc
    except ZeroDivisionError as exc:
        raise ExpressionError("division by zero") from exc
    except Exception as exc:  # kernel-agnostic guard
        raise ExpressionError(str(exc)) from exc

    if isinstance(value, bool):
        return float(value)
    if not isinstance(value, (int, float)):
        raise ExpressionError("expression did not produce a number")
    if value != value or value in (float("inf"), float("-inf")):
        raise ExpressionError("expression produced a non-finite value")
    return float(value)


# --------------------------------------------------------------------------


@dataclass
class Parameter:
    """A single named model parameter."""

    name: str
    expression: str = "0"
    unit: str = "mm"
    comment: str = ""
    value: float = 0.0
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "expression": self.expression,
            "unit": self.unit,
            "comment": self.comment,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Parameter":
        return cls(
            name=data["name"],
            expression=str(data.get("expression", "0")),
            unit=data.get("unit", "mm"),
            comment=data.get("comment", ""),
        )


class ParameterTable:
    """Ordered parameter set with dependency-aware evaluation."""

    def __init__(self) -> None:
        self._params: Dict[str, Parameter] = {}
        # A part works its user parameters out together with its model
        # parameters, since either may be written in terms of the other.
        # It hands its own way of doing that in here; held weakly, so the
        # table does not keep a closed document alive.
        self._resolver: Optional[Callable[[], Any]] = None

    def bind(self, resolver: Callable[[], None]) -> None:
        """Have a document evaluate this table along with its own names."""
        import weakref

        self._resolver = weakref.WeakMethod(resolver)

    # -- container behaviour ------------------------------------------------

    def __contains__(self, name: object) -> bool:
        return name in self._params

    def __iter__(self):
        return iter(self._params.values())

    def __len__(self) -> int:
        return len(self._params)

    def __getitem__(self, name: str) -> Parameter:
        return self._params[name]

    def names(self) -> List[str]:
        return list(self._params)

    def get(self, name: str) -> Optional[Parameter]:
        return self._params.get(name)

    # -- mutation -----------------------------------------------------------

    def add(
        self,
        name: str,
        expression: str = "0",
        unit: str = "mm",
        comment: str = "",
    ) -> Parameter:
        if not IDENT_RE.match(name):
            raise ExpressionError("%r is not a valid parameter name" % name)
        if name in FUNCTIONS or name in CONSTANTS:
            raise ExpressionError("%r is a reserved name" % name)
        if name in self._params:
            raise ExpressionError("parameter %r already exists" % name)
        param = Parameter(name=name, expression=str(expression), unit=unit,
                          comment=comment)
        self._params[name] = param
        self.evaluate_all()
        return param

    def set_expression(self, name: str, expression: str) -> None:
        self._params[name].expression = str(expression)
        self.evaluate_all()

    def rename(self, old: str, new: str) -> None:
        if old == new:
            return
        if not IDENT_RE.match(new):
            raise ExpressionError("%r is not a valid parameter name" % new)
        if new in self._params:
            raise ExpressionError("parameter %r already exists" % new)
        # preserve insertion order while swapping the key
        items = [(new, p) if k == old else (k, p) for k, p in self._params.items()]
        param = self._params[old]
        param.name = new
        self._params = dict(items)
        pattern = re.compile(r"\b%s\b" % re.escape(old))
        for other in self._params.values():
            if other is not param:
                other.expression = pattern.sub(new, other.expression)
        self.evaluate_all()

    def remove(self, name: str) -> None:
        self._params.pop(name, None)
        self.evaluate_all()

    def dependents(self, name: str) -> List[str]:
        """Parameters whose expressions reference ``name``."""
        return [p.name for p in self._params.values()
                if name in referenced_names(p.expression)]

    # -- evaluation ---------------------------------------------------------

    def evaluate_all(self) -> None:
        """Resolve every parameter, detecting cycles and unknown references."""
        resolver = self._resolver() if self._resolver is not None else None
        if resolver is not None:
            resolver()
            return
        order, cyclic = self._topological_order()
        scope: Dict[str, float] = {}

        for name in order:
            param = self._params[name]
            if name in cyclic:
                param.error = "circular reference"
                param.value = 0.0
                continue
            try:
                param.value = evaluate(param.expression, scope)
                param.error = ""
            except ExpressionError as exc:
                param.error = str(exc)
                param.value = 0.0
            scope[name] = param.value

        for name in cyclic:
            self._params[name].error = "circular reference"

    def _topological_order(self) -> Tuple[List[str], set]:
        visited: Dict[str, int] = {}
        order: List[str] = []
        cyclic: set = set()

        def visit(name: str, stack: Tuple[str, ...]) -> None:
            state = visited.get(name, 0)
            if state == 2:
                return
            if state == 1:
                cyclic.update(stack[stack.index(name):])
                return
            visited[name] = 1
            for dep in referenced_names(self._params[name].expression):
                if dep in self._params:
                    visit(dep, stack + (name,))
            visited[name] = 2
            order.append(name)

        for name in self._params:
            visit(name, ())
        return order, cyclic

    def scope(self) -> Dict[str, float]:
        """Name -> value mapping used to evaluate feature expressions."""
        return {p.name: p.value for p in self._params.values()}

    def eval(self, expression: Any, default: Optional[float] = None) -> float:
        """Evaluate ``expression`` in this table's scope."""
        try:
            return evaluate(expression, self.scope())
        except ExpressionError:
            if default is None:
                raise
            return default

    @property
    def errors(self) -> List[str]:
        return ["%s: %s" % (p.name, p.error) for p in self._params.values() if p.error]

    # -- serialisation ------------------------------------------------------

    def to_list(self) -> List[Dict[str, Any]]:
        return [p.to_dict() for p in self._params.values()]

    def load(self, data: Iterable[Dict[str, Any]]) -> None:
        self._params.clear()
        for entry in data:
            param = Parameter.from_dict(entry)
            self._params[param.name] = param
        self.evaluate_all()
