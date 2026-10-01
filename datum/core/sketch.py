"""2D sketch geometry, constraints and the numeric constraint solver.

A sketch owns a flat pool of points plus entities that index into it.  All
solving happens on the raw variable vector (every point's x/y, plus a radius
variable per circle/arc), which keeps the solver independent of entity types.

The solver is damped Gauss-Newton (Levenberg-Marquardt).  Sketches are small -
a few hundred variables at most - so a dense numeric Jacobian is both fast
enough and far more robust than hand-derived analytic ones.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import (
    Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple,
)

import numpy as np

from . import lstsq

TOL = 1e-7
POINT_MERGE_TOL = 1e-6
DRAG_WEIGHT = 0.05


# ==========================================================================
# plane
# ==========================================================================


@dataclass
class SketchPlane:
    """A placed 2D coordinate system in 3D space."""

    origin: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    normal: Tuple[float, float, float] = (0.0, 0.0, 1.0)
    xdir: Tuple[float, float, float] = (1.0, 0.0, 0.0)
    name: str = "XY Plane"

    @staticmethod
    def _norm(v: Sequence[float]) -> Tuple[float, float, float]:
        n = math.sqrt(sum(c * c for c in v))
        if n < TOL:
            raise ValueError("zero-length direction vector")
        return (v[0] / n, v[1] / n, v[2] / n)

    def __post_init__(self) -> None:
        self.origin = tuple(float(c) for c in self.origin)  # type: ignore[assignment]
        self.normal = self._norm(self.normal)
        x = self.xdir
        n = self.normal
        # re-orthogonalise xdir against the normal (Gram-Schmidt)
        dot = sum(a * b for a, b in zip(x, n))
        x = tuple(a - dot * b for a, b in zip(x, n))
        if math.sqrt(sum(c * c for c in x)) < TOL:
            fallback = (0.0, 0.0, 1.0) if abs(n[0]) > 0.9 else (1.0, 0.0, 0.0)
            dot = sum(a * b for a, b in zip(fallback, n))
            x = tuple(a - dot * b for a, b in zip(fallback, n))
        self.xdir = self._norm(x)

    @property
    def ydir(self) -> Tuple[float, float, float]:
        n, x = self.normal, self.xdir
        return (
            n[1] * x[2] - n[2] * x[1],
            n[2] * x[0] - n[0] * x[2],
            n[0] * x[1] - n[1] * x[0],
        )

    def to_3d(self, u: float, v: float) -> Tuple[float, float, float]:
        o, x, y = self.origin, self.xdir, self.ydir
        return (
            o[0] + x[0] * u + y[0] * v,
            o[1] + x[1] * u + y[1] * v,
            o[2] + x[2] * u + y[2] * v,
        )

    def to_2d(self, p: Sequence[float]) -> Tuple[float, float]:
        o, x, y = self.origin, self.xdir, self.ydir
        d = (p[0] - o[0], p[1] - o[1], p[2] - o[2])
        return (sum(a * b for a, b in zip(d, x)), sum(a * b for a, b in zip(d, y)))

    def distance_to(self, p: Sequence[float]) -> float:
        o, n = self.origin, self.normal
        d = (p[0] - o[0], p[1] - o[1], p[2] - o[2])
        return sum(a * b for a, b in zip(d, n))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "origin": list(self.origin),
            "normal": list(self.normal),
            "xdir": list(self.xdir),
            "name": self.name,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SketchPlane":
        return cls(
            origin=tuple(data.get("origin", (0, 0, 0))),
            normal=tuple(data.get("normal", (0, 0, 1))),
            xdir=tuple(data.get("xdir", (1, 0, 0))),
            name=data.get("name", "Plane"),
        )


PLANE_XY = SketchPlane((0, 0, 0), (0, 0, 1), (1, 0, 0), "XY Plane")
PLANE_XZ = SketchPlane((0, 0, 0), (0, -1, 0), (1, 0, 0), "XZ Plane")
PLANE_YZ = SketchPlane((0, 0, 0), (1, 0, 0), (0, 1, 0), "YZ Plane")

STANDARD_PLANES = {
    "XY": PLANE_XY,
    "XZ": PLANE_XZ,
    "YZ": PLANE_YZ,
}


# ==========================================================================
# primitives
# ==========================================================================


@dataclass
class SketchPoint:
    id: int
    x: float = 0.0
    y: float = 0.0
    fixed: bool = False
    # the sketch's grounded origin: created with every sketch, never pruned
    origin: bool = False

    def as_tuple(self) -> Tuple[float, float]:
        return (self.x, self.y)


@dataclass
class Entity:
    """Base sketch entity.  ``points`` are ids into the sketch point pool."""

    id: int
    kind: str = "line"
    points: List[int] = field(default_factory=list)
    radius: float = 0.0
    construction: bool = False
    # arcs store their sweep direction; True = counter-clockwise from p1 to p2
    ccw: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "points": list(self.points),
            "radius": self.radius,
            "construction": self.construction,
            "ccw": self.ccw,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Entity":
        return cls(
            id=d["id"],
            kind=d.get("kind", "line"),
            points=list(d.get("points", [])),
            radius=float(d.get("radius", 0.0)),
            construction=bool(d.get("construction", False)),
            ccw=bool(d.get("ccw", True)),
        )


@dataclass
class Projection:
    """Sketch geometry that is a shadow of the model rather than drawn.

    It has to be re-cast every time the model under it changes, or a
    sketch halfway down a feature tree goes on describing a shape that is
    no longer there.  The source is held as a naming.ShapeRef in
    dictionary form rather than as the object: kernel imports this module
    for SketchPlane, and naming imports kernel, so importing naming here
    would close the circle.  features.py has all three and does the
    rebinding.

    ``entities`` are kept and moved rather than deleted and remade, so a
    dimension drawn to a projected edge survives the model changing under
    it.
    """

    id: int = 0
    source: Dict[str, Any] = field(default_factory=dict)   # ShapeRef.to_dict
    entities: List[int] = field(default_factory=list)
    construction: bool = False
    # set when the source can no longer be found, so it can be shown as
    # out of date rather than silently freezing at its last position
    stale: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "source": dict(self.source),
                "entities": list(self.entities),
                "construction": self.construction, "stale": self.stale}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Projection":
        return cls(id=int(d.get("id", 0)),
                   source=dict(d.get("source") or {}),
                   entities=[int(e) for e in d.get("entities", [])],
                   construction=bool(d.get("construction", False)),
                   stale=bool(d.get("stale", False)))


# Constraint kinds and how many entities/points/values they take.
CONSTRAINT_KINDS = (
    "coincident", "horizontal", "vertical", "parallel", "perpendicular",
    "equal", "distance", "distance_x", "distance_y", "distance_pl",
    "radius", "diameter", "point_on", "tangent", "angle", "symmetric",
    "fix", "ground", "concentric", "midpoint", "collinear",
)

# Kinds that carry a number the user typed, and so get a name of their own
# that other dimensions can refer to.
DIMENSION_KINDS = ("distance", "distance_x", "distance_y", "distance_pl",
                   "radius", "diameter", "angle")


@dataclass
class Constraint:
    id: int
    kind: str
    points: List[int] = field(default_factory=list)
    entities: List[int] = field(default_factory=list)
    value: float = 0.0
    expression: str = ""
    # d1, d2, ... for dimensions, so one can be written into another's
    # expression the way Inventor lets you
    name: str = ""
    driving: bool = True
    label_offset: Tuple[float, float] = (0.0, 0.0)
    # what the Parameters table says about it
    comment: str = ""

    def to_dict(self) -> Dict[str, Any]:
        out = {
            "id": self.id,
            "kind": self.kind,
            "points": list(self.points),
            "entities": list(self.entities),
            "value": self.value,
            "expression": self.expression,
            "name": self.name,
            "driving": self.driving,
            "label_offset": list(self.label_offset),
        }
        if self.comment:
            out["comment"] = self.comment
        return out

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Constraint":
        return cls(
            id=d["id"],
            kind=d["kind"],
            points=list(d.get("points", [])),
            entities=list(d.get("entities", [])),
            value=float(d.get("value", 0.0)),
            expression=d.get("expression", ""),
            name=str(d.get("name", "")),
            driving=bool(d.get("driving", True)),
            label_offset=tuple(d.get("label_offset", (0.0, 0.0))),
            comment=str(d.get("comment", "")),
        )

    @property
    def is_dimension(self) -> bool:
        return self.kind in DIMENSION_KINDS


# ==========================================================================
# sketch
# ==========================================================================


class Sketch:
    """A constrained 2D profile living on a :class:`SketchPlane`."""

    def __init__(self, plane: Optional[SketchPlane] = None, name: str = "Sketch") -> None:
        self.name = name
        self.plane = plane or SketchPlane()
        self.points: Dict[int, SketchPoint] = {}
        self.entities: Dict[int, Entity] = {}
        self.constraints: Dict[int, Constraint] = {}
        # geometry cast from the model, re-cast whenever it changes
        self.projections: List[Projection] = []
        self._next_id = 1
        self.dof = 0
        self.solve_message = ""
        self.conflicting: List[int] = []
        # variables the solver says can still move, filled in by solve()
        self.free_points: set = set()
        self.free_radii: set = set()
        # Names already in use elsewhere in the part, asked for when a new
        # dimension is named: d-numbers run across the whole part, so the
        # second sketch's first dimension is not a second d1.  Set by
        # whatever is editing the sketch; never saved.
        self.name_pool: Optional[Callable[[], Iterable[str]]] = None

    # -- ids ----------------------------------------------------------------

    def _new_id(self) -> int:
        i = self._next_id
        self._next_id += 1
        return i

    # -- creation -----------------------------------------------------------

    def add_point(self, x: float, y: float, fixed: bool = False) -> int:
        pid = self._new_id()
        self.points[pid] = SketchPoint(pid, float(x), float(y), fixed)
        return pid

    def ensure_origin_point(self) -> int:
        """Every sketch gets a grounded point at the plane's origin.

        It gives the solver something to hold on to, so a fresh sketch is
        never free-floating, and it gives the user an anchor to dimension
        from - which is why it is created for face sketches too, not just
        sketches on the datum planes.
        """
        for point in self.points.values():
            if point.origin:
                point.fixed = True
                return point.id
        existing = self.point_at(0.0, 0.0)
        if existing is not None:
            self.points[existing].origin = True
            self.points[existing].fixed = True
            return existing
        pid = self.add_point(0.0, 0.0, fixed=True)
        self.points[pid].origin = True
        return pid

    @property
    def origin_point(self) -> Optional[SketchPoint]:
        for point in self.points.values():
            if point.origin:
                return point
        return None

    def point_at(self, x: float, y: float, tol: float = POINT_MERGE_TOL) -> Optional[int]:
        """Existing point within ``tol``, if any - used to auto-weld chains."""
        best, best_d = None, tol
        for p in self.points.values():
            d = math.hypot(p.x - x, p.y - y)
            if d <= best_d:
                best, best_d = p.id, d
        return best

    def _pt(self, x: float, y: float, weld: bool = True) -> int:
        if weld:
            existing = self.point_at(x, y)
            if existing is not None:
                return existing
        return self.add_point(x, y)

    def add_line(self, p1: Tuple[float, float], p2: Tuple[float, float],
                 construction: bool = False, weld: bool = True) -> int:
        a = self._pt(p1[0], p1[1], weld)
        b = self._pt(p2[0], p2[1], weld)
        if a == b:
            b = self.add_point(p2[0], p2[1])
        eid = self._new_id()
        self.entities[eid] = Entity(eid, "line", [a, b], construction=construction)
        return eid

    def add_line_ids(self, a: int, b: int, construction: bool = False) -> int:
        eid = self._new_id()
        self.entities[eid] = Entity(eid, "line", [a, b], construction=construction)
        return eid

    def add_circle(self, center: Tuple[float, float], radius: float,
                   construction: bool = False) -> int:
        c = self.add_point(center[0], center[1])
        eid = self._new_id()
        self.entities[eid] = Entity(eid, "circle", [c], radius=abs(float(radius)),
                                    construction=construction)
        return eid

    def add_arc(self, center: Tuple[float, float], radius: float,
                start_angle: float, end_angle: float,
                construction: bool = False) -> int:
        """Arc from ``start_angle`` to ``end_angle`` (radians, CCW)."""
        cx, cy = center
        r = abs(float(radius))
        c = self.add_point(cx, cy)
        p1 = self._pt(cx + r * math.cos(start_angle), cy + r * math.sin(start_angle))
        p2 = self._pt(cx + r * math.cos(end_angle), cy + r * math.sin(end_angle))
        eid = self._new_id()
        self.entities[eid] = Entity(eid, "arc", [c, p1, p2], radius=r,
                                    construction=construction)
        return eid

    def add_rectangle(self, p1: Tuple[float, float], p2: Tuple[float, float],
                      construction: bool = False) -> List[int]:
        x1, y1 = p1
        x2, y2 = p2
        a = self.add_point(x1, y1)
        b = self.add_point(x2, y1)
        c = self.add_point(x2, y2)
        d = self.add_point(x1, y2)
        ids = [
            self.add_line_ids(a, b, construction),
            self.add_line_ids(b, c, construction),
            self.add_line_ids(c, d, construction),
            self.add_line_ids(d, a, construction),
        ]
        self.add_constraint("horizontal", entities=[ids[0]])
        self.add_constraint("horizontal", entities=[ids[2]])
        self.add_constraint("vertical", entities=[ids[1]])
        self.add_constraint("vertical", entities=[ids[3]])
        return ids

    def add_slot(self, p1: Tuple[float, float], p2: Tuple[float, float],
                 width: float) -> List[int]:
        """Straight slot: two parallel lines capped by two half-circle arcs."""
        r = abs(width) / 2.0
        dx, dy = p2[0] - p1[0], p2[1] - p1[1]
        length = math.hypot(dx, dy)
        if length < TOL:
            return []
        nx, ny = -dy / length * r, dx / length * r
        a1 = (p1[0] + nx, p1[1] + ny)
        a2 = (p2[0] + nx, p2[1] + ny)
        b1 = (p1[0] - nx, p1[1] - ny)
        b2 = (p2[0] - nx, p2[1] - ny)
        ang = math.atan2(dy, dx)
        ids = [
            self.add_line(a1, a2),
            self.add_line(b2, b1),
        ]
        # Each cap has to bulge away from the slot.  Arcs sweep anti-clockwise
        # from the first angle to the second, so the far end runs through the
        # forward direction and the near end through the backward one - the
        # other way round closes the profile across the middle and gives a
        # bowtie with two thirds of the area.
        ids.append(self.add_arc(p2, r, ang - math.pi / 2, ang + math.pi / 2))
        ids.append(self.add_arc(p1, r, ang + math.pi / 2,
                                ang + 3.0 * math.pi / 2))

        # What Inventor puts in a slot besides its outline: the sides held
        # tangent to the ends and the ends held the same size, so dragging
        # one part of it moves a slot and not four loose pieces; and a
        # construction centre line with a point held at its middle, which
        # is the thing to constrain when a slot has to sit centred on
        # something.  The centre line runs between the two arc centres, so
        # it is the slot's own axis and needs nothing to keep it there.
        for side in ids[:2]:
            for end in ids[2:]:
                self.add_constraint("tangent", entities=[side, end])
        self.add_constraint("equal", entities=[ids[2], ids[3]])
        centre_line = self.add_line_ids(self.entities[ids[3]].points[0],
                                        self.entities[ids[2]].points[0],
                                        construction=True)
        middle = self.add_point((p1[0] + p2[0]) / 2.0,
                                (p1[1] + p2[1]) / 2.0)
        self.add_constraint("midpoint", points=[middle],
                            entities=[centre_line])
        ids.append(centre_line)
        return ids

    def add_quad(self, corners: Sequence[Tuple[float, float]],
                 construction: bool = False) -> List[int]:
        """A rectangle at any angle, from its four corners.

        Unlike :meth:`add_rectangle` this cannot lean on horizontal and
        vertical constraints, so it is held square by parallels on the
        opposite sides and one perpendicular at a corner.  That is the same
        number of constraints and it survives being dragged.
        """
        if len(corners) != 4:
            return []
        pts = [self.add_point(x, y) for x, y in corners]
        ids = [self.add_line_ids(pts[i], pts[(i + 1) % 4], construction)
               for i in range(4)]
        self.add_constraint("parallel", entities=[ids[0], ids[2]])
        self.add_constraint("parallel", entities=[ids[1], ids[3]])
        self.add_constraint("perpendicular", entities=[ids[0], ids[1]])
        return ids

    def add_arc_slot(self, centre: Tuple[float, float], radius: float,
                     start_angle: float, end_angle: float,
                     width: float) -> List[int]:
        """A slot bent round an arc: two concentric arcs, two round ends."""
        r = abs(width) / 2.0
        if radius <= r + TOL or r < TOL:
            return []
        inner, outer = radius - r, radius + r
        if end_angle < start_angle:
            start_angle, end_angle = end_angle, start_angle

        ids = [
            self.add_arc(centre, outer, start_angle, end_angle),
            self.add_arc(centre, inner, start_angle, end_angle),
        ]
        # Each cap joins the inner rail to the outer one, which sit on the
        # same radial line through the cap's centre.  It has to bulge *away*
        # from the slot, so the two ends sweep opposite halves: anything
        # else closes the profile across the slot instead of round the end.
        for angle, outward in ((start_angle, False), (end_angle, True)):
            cap = (centre[0] + radius * math.cos(angle),
                   centre[1] + radius * math.sin(angle))
            if outward:
                a0, a1 = angle, angle + math.pi
            else:
                a0, a1 = angle + math.pi, angle + 2.0 * math.pi
            ids.append(self.add_arc(cap, r, a0, a1))
        return ids

    def add_polygon(self, center: Tuple[float, float], radius: float, sides: int,
                    rotation: float = 0.0, construction: bool = False) -> List[int]:
        sides = max(3, int(sides))
        pts = []
        for i in range(sides):
            a = rotation + 2 * math.pi * i / sides
            pts.append(self.add_point(center[0] + radius * math.cos(a),
                                      center[1] + radius * math.sin(a)))
        ids = []
        for i in range(sides):
            ids.append(self.add_line_ids(pts[i], pts[(i + 1) % sides], construction))
        return ids

    def add_spline(self, pts: Sequence[Tuple[float, float]],
                   construction: bool = False) -> int:
        ids = [self.add_point(p[0], p[1]) for p in pts]
        eid = self._new_id()
        self.entities[eid] = Entity(eid, "spline", ids, construction=construction)
        return eid

    # -- deletion -----------------------------------------------------------

    def remove_entity(self, eid: int) -> None:
        ent = self.entities.pop(eid, None)
        if ent is None:
            return
        for cid in [c.id for c in self.constraints.values() if eid in c.entities]:
            self.constraints.pop(cid, None)

        # A dimension on a line holds the line's two *points*, not the line,
        # so dropping the constraints that name the entity is not enough:
        # the dimension would survive, keep those points alive through the
        # prune below, and be left floating in the sketch measuring a line
        # that is no longer there.  Anything that holds a point which nothing
        # else uses any more goes with it.
        held = set()
        for other in self.entities.values():
            held.update(other.points)
        going = {pid for pid in ent.points
                 if pid not in held
                 and not (pid in self.points and self.points[pid].origin)}
        if going:
            for cid in [c.id for c in self.constraints.values()
                        if going.intersection(c.points)]:
                self.constraints.pop(cid, None)

        self._prune_points()

    def remove_constraint(self, cid: int) -> None:
        self.constraints.pop(cid, None)

    def _prune_points(self) -> None:
        used = set()
        for ent in self.entities.values():
            used.update(ent.points)
        for c in self.constraints.values():
            used.update(c.points)
        # the grounded origin survives even when nothing references it
        used.update(p.id for p in self.points.values() if p.origin)
        for pid in [p for p in self.points if p not in used]:
            del self.points[pid]
        for cid in [c.id for c in self.constraints.values()
                    if any(p not in self.points for p in c.points)]:
            self.constraints.pop(cid, None)

    # -- constraints --------------------------------------------------------

    def add_constraint(self, kind: str, points: Optional[List[int]] = None,
                       entities: Optional[List[int]] = None, value: float = 0.0,
                       expression: str = "") -> int:
        if kind not in CONSTRAINT_KINDS:
            raise ValueError("unknown constraint %r" % kind)

        # Grounding is held on the point, not as an equation: the solver
        # drops fixed points out of the system rather than adding a pull
        # towards where they were, so a grounded edge really does not move.
        # Recording the constraint alone would therefore look like it had
        # worked and do nothing at all, so the flag is set here too.
        if kind in ("fix", "ground"):
            targets = list(points or [])
            for eid in (entities or []):
                entity = self.entities.get(eid)
                if entity is not None:
                    targets.extend(entity.points)
            for pid in targets:
                point = self.points.get(pid)
                if point is not None:
                    point.fixed = True

        cid = self._new_id()
        self.constraints[cid] = Constraint(
            cid, kind, list(points or []), list(entities or []),
            float(value), expression,
            name=self.next_dimension_name() if kind in DIMENSION_KINDS else "",
        )
        return cid

    def next_dimension_name(self) -> str:
        """The next free d1, d2, ... in this sketch, and in its part."""
        taken = {c.name for c in self.constraints.values() if c.name}
        if self.name_pool is not None:
            try:
                taken.update(self.name_pool())
            except Exception:
                pass
        i = 1
        while "d%d" % i in taken:
            i += 1
        return "d%d" % i

    def dimension(self, name: str) -> Optional[Constraint]:
        return next((c for c in self.constraints.values()
                     if c.name == name), None)

    def dimension_scope(self, scope: Optional[Dict[str, float]] = None
                        ) -> Dict[str, float]:
        """Named parameters plus every dimension in this sketch.

        A dimension may be written in terms of another - ``(10 - 2 + d2)/2``
        - so they are resolved in passes: each round adds the ones whose
        expressions can now be worked out, and stops when a round adds
        nothing.  Whatever is left over refers to something that does not
        exist, or to itself round a loop, and keeps its last good value
        rather than taking the whole sketch down.
        """
        from .params import ExpressionError, evaluate

        out = dict(scope or {})
        pending = [c for c in self.constraints.values()
                   if c.name and c.is_dimension]
        while pending:
            progressed = []
            for c in pending:
                if not c.expression:
                    out[c.name] = c.value
                    progressed.append(c)
                    continue
                try:
                    # cache it on the constraint as well, so if the thing it
                    # refers to later disappears the dimension holds its last
                    # good number instead of collapsing to zero
                    out[c.name] = c.value = evaluate(c.expression, out)
                    progressed.append(c)
                except ExpressionError:
                    continue
            if not progressed:
                for c in pending:           # unresolvable; hold what we had
                    out.setdefault(c.name, c.value)
                break
            pending = [c for c in pending if c not in progressed]
        return out

    def auto_constrain_new(self, entity_ids: Sequence[int],
                           angle_tol_deg: float = 2.0) -> None:
        """Infer horizontal/vertical constraints, the way Inventor does on draw."""
        tol = math.radians(angle_tol_deg)
        for eid in entity_ids:
            ent = self.entities.get(eid)
            if ent is None or ent.kind != "line":
                continue
            a, b = self.points[ent.points[0]], self.points[ent.points[1]]
            ang = math.atan2(b.y - a.y, b.x - a.x)
            if min(abs(ang), abs(abs(ang) - math.pi)) < tol:
                self.add_constraint("horizontal", entities=[eid])
            elif abs(abs(ang) - math.pi / 2) < tol:
                self.add_constraint("vertical", entities=[eid])

    # ----------------------------------------------------------------------
    # solver
    # ----------------------------------------------------------------------

    def _variable_map(self) -> Tuple[List[Tuple[str, int, int]], np.ndarray]:
        """Build the variable vector: point x/y plus one radius per circle/arc."""
        layout: List[Tuple[str, int, int]] = []
        values: List[float] = []
        for pid in sorted(self.points):
            p = self.points[pid]
            layout.append(("px", pid, 0))
            values.append(p.x)
            layout.append(("py", pid, 0))
            values.append(p.y)
        for eid in sorted(self.entities):
            ent = self.entities[eid]
            if ent.kind in ("circle", "arc"):
                layout.append(("r", eid, 0))
                values.append(ent.radius)
        return layout, np.array(values, dtype=float)

    def _apply(self, layout: Sequence[Tuple[str, int, int]], x: np.ndarray) -> None:
        for i, (tag, oid, _) in enumerate(layout):
            if tag == "px":
                self.points[oid].x = float(x[i])
            elif tag == "py":
                self.points[oid].y = float(x[i])
            else:
                self.entities[oid].radius = float(x[i])

    def _index(self, layout: Sequence[Tuple[str, int, int]]) -> Dict[Tuple[str, int], int]:
        return {(tag, oid): i for i, (tag, oid, _) in enumerate(layout)}

    def _residual_builder(self, idx: Dict[Tuple[str, int], int],
                          scope: Optional[Dict[str, float]]):
        """Return a function mapping the variable vector to a residual vector."""
        from .params import ExpressionError, evaluate

        def value_of(c: Constraint) -> float:
            if c.expression:
                try:
                    return evaluate(c.expression, scope or {})
                except ExpressionError:
                    return c.value
            return c.value

        # Pre-resolve every constraint into a small closure list so the hot
        # loop does no dict lookups by name.
        specs: List[Tuple[str, Constraint, float]] = []
        for c in self.constraints.values():
            if not c.driving:
                continue
            specs.append((c.kind, c, value_of(c)))

        # implicit constraints: arc endpoints must sit on their own radius
        arcs = [e for e in self.entities.values() if e.kind == "arc"]

        fixed_points = [(p.id, p.x, p.y) for p in self.points.values() if p.fixed]

        def P(x: np.ndarray, pid: int) -> Tuple[float, float]:
            return x[idx[("px", pid)]], x[idx[("py", pid)]]

        def R(x: np.ndarray, eid: int) -> float:
            return x[idx[("r", eid)]]

        def line_pts(x: np.ndarray, eid: int):
            ent = self.entities[eid]
            return P(x, ent.points[0]), P(x, ent.points[1])

        def residuals(x: np.ndarray) -> np.ndarray:
            out: List[float] = []

            for eid_ent in arcs:
                cx, cy = P(x, eid_ent.points[0])
                r = R(x, eid_ent.id)
                for pid in eid_ent.points[1:]:
                    px, py = P(x, pid)
                    out.append(math.hypot(px - cx, py - cy) - r)

            for pid, fx, fy in fixed_points:
                px, py = P(x, pid)
                out.append(px - fx)
                out.append(py - fy)

            for kind, c, val in specs:
                try:
                    if kind == "coincident":
                        (ax, ay), (bx, by) = P(x, c.points[0]), P(x, c.points[1])
                        out += [ax - bx, ay - by]
                    elif kind == "concentric":
                        e1 = self.entities[c.entities[0]]
                        e2 = self.entities[c.entities[1]]
                        (ax, ay) = P(x, e1.points[0])
                        (bx, by) = P(x, e2.points[0])
                        out += [ax - bx, ay - by]
                    elif kind in ("horizontal", "vertical"):
                        # on a line, or between two points: the two points
                        # level with each other, or one above the other
                        if c.entities:
                            (ax, ay), (bx, by) = line_pts(x, c.entities[0])
                        else:
                            (ax, ay) = P(x, c.points[0])
                            (bx, by) = P(x, c.points[1])
                        out.append(ay - by if kind == "horizontal"
                                   else ax - bx)
                    elif kind == "parallel":
                        (a, b) = line_pts(x, c.entities[0])
                        (p, q) = line_pts(x, c.entities[1])
                        d1 = (b[0] - a[0], b[1] - a[1])
                        d2 = (q[0] - p[0], q[1] - p[1])
                        n1 = math.hypot(*d1) or 1.0
                        n2 = math.hypot(*d2) or 1.0
                        out.append((d1[0] * d2[1] - d1[1] * d2[0]) / (n1 * n2))
                    elif kind == "collinear":
                        # Two lines on one infinite line: parallel, and one
                        # line's start lying on the other.  Parallel alone
                        # would let them sit on separate tracks; the second
                        # residual is what pulls them onto the same one.
                        (a, b) = line_pts(x, c.entities[0])
                        (p, q) = line_pts(x, c.entities[1])
                        d1 = (b[0] - a[0], b[1] - a[1])
                        d2 = (q[0] - p[0], q[1] - p[1])
                        n1 = math.hypot(*d1) or 1.0
                        n2 = math.hypot(*d2) or 1.0
                        out.append((d1[0] * d2[1] - d1[1] * d2[0]) / (n1 * n2))
                        out.append(((p[0] - a[0]) * d1[1]
                                    - (p[1] - a[1]) * d1[0]) / n1)
                    elif kind == "perpendicular":
                        (a, b) = line_pts(x, c.entities[0])
                        (p, q) = line_pts(x, c.entities[1])
                        d1 = (b[0] - a[0], b[1] - a[1])
                        d2 = (q[0] - p[0], q[1] - p[1])
                        n1 = math.hypot(*d1) or 1.0
                        n2 = math.hypot(*d2) or 1.0
                        out.append((d1[0] * d2[0] + d1[1] * d2[1]) / (n1 * n2))
                    elif kind == "equal":
                        e1 = self.entities[c.entities[0]]
                        e2 = self.entities[c.entities[1]]
                        if e1.kind == "line" and e2.kind == "line":
                            (a, b) = line_pts(x, e1.id)
                            (p, q) = line_pts(x, e2.id)
                            out.append(math.hypot(b[0] - a[0], b[1] - a[1])
                                       - math.hypot(q[0] - p[0], q[1] - p[1]))
                        else:
                            out.append(R(x, e1.id) - R(x, e2.id))
                    elif kind == "distance":
                        (ax, ay), (bx, by) = P(x, c.points[0]), P(x, c.points[1])
                        out.append(math.hypot(bx - ax, by - ay) - val)
                    elif kind == "distance_x":
                        (ax, _), (bx, _2) = P(x, c.points[0]), P(x, c.points[1])
                        out.append((bx - ax) - val)
                    elif kind == "distance_y":
                        (_, ay), (_2, by) = P(x, c.points[0]), P(x, c.points[1])
                        out.append((by - ay) - val)
                    elif kind == "distance_pl":
                        # A point held a set distance from a line, measured
                        # perpendicular to it.  The line is given as its two
                        # ends rather than as an entity, so that swapping
                        # them flips which side the point is held on: the
                        # residual can then stay signed - and keep the point
                        # where it was put - while the value stays positive.
                        (px, py) = P(x, c.points[0])
                        (ax, ay) = P(x, c.points[1])
                        (bx, by) = P(x, c.points[2])
                        dx, dy = bx - ax, by - ay
                        n = math.hypot(dx, dy)
                        if n < TOL:
                            continue
                        out.append(((px - ax) * dy - (py - ay) * dx) / n - val)
                    elif kind == "radius":
                        out.append(R(x, c.entities[0]) - val)
                    elif kind == "diameter":
                        out.append(2.0 * R(x, c.entities[0]) - val)
                    elif kind == "point_on":
                        ent = self.entities[c.entities[0]]
                        px, py = P(x, c.points[0])
                        if ent.kind == "line":
                            (ax, ay), (bx, by) = line_pts(x, ent.id)
                            dx, dy = bx - ax, by - ay
                            n = math.hypot(dx, dy) or 1.0
                            out.append(((px - ax) * dy - (py - ay) * dx) / n)
                        else:
                            cx, cy = P(x, ent.points[0])
                            out.append(math.hypot(px - cx, py - cy) - R(x, ent.id))
                    elif kind == "midpoint":
                        ent = self.entities[c.entities[0]]
                        (ax, ay), (bx, by) = line_pts(x, ent.id)
                        px, py = P(x, c.points[0])
                        out += [px - 0.5 * (ax + bx), py - 0.5 * (ay + by)]
                    elif kind == "tangent":
                        e1 = self.entities[c.entities[0]]
                        e2 = self.entities[c.entities[1]]
                        line, circ = (e1, e2) if e1.kind == "line" else (e2, e1)
                        if line.kind == "line" and circ.kind in ("circle", "arc"):
                            (ax, ay), (bx, by) = line_pts(x, line.id)
                            cx, cy = P(x, circ.points[0])
                            dx, dy = bx - ax, by - ay
                            n = math.hypot(dx, dy) or 1.0
                            dist = abs((cx - ax) * dy - (cy - ay) * dx) / n
                            out.append(dist - R(x, circ.id))
                        else:
                            (ax, ay) = P(x, e1.points[0])
                            (bx, by) = P(x, e2.points[0])
                            d = math.hypot(bx - ax, by - ay)
                            out.append(d - abs(R(x, e1.id) + R(x, e2.id)))
                    elif kind == "angle":
                        (a, b) = line_pts(x, c.entities[0])
                        (p, q) = line_pts(x, c.entities[1])
                        a1 = math.atan2(b[1] - a[1], b[0] - a[0])
                        a2 = math.atan2(q[1] - p[1], q[0] - p[0])
                        diff = math.degrees(a2 - a1) % 360.0
                        target = val % 360.0
                        delta = (diff - target + 180.0) % 360.0 - 180.0
                        out.append(delta / 57.29577951308232)
                    elif kind == "symmetric":
                        (ax, ay), (bx, by) = P(x, c.points[0]), P(x, c.points[1])
                        (la, lb) = line_pts(x, c.entities[0])
                        dx, dy = lb[0] - la[0], lb[1] - la[1]
                        n = math.hypot(dx, dy) or 1.0
                        mx, my = 0.5 * (ax + bx), 0.5 * (ay + by)
                        out.append(((mx - la[0]) * dy - (my - la[1]) * dx) / n)
                        out.append(((bx - ax) * dx + (by - ay) * dy) / n)
                    # "fix" is expressed through SketchPoint.fixed, handled above
                except (KeyError, IndexError):
                    continue

            return np.array(out, dtype=float) if out else np.zeros(0)

        return residuals

    def solve(self, scope: Optional[Dict[str, float]] = None,
              anchors: Optional[Dict[int, Tuple[float, float]]] = None,
              max_iter: int = 60) -> bool:
        """Solve the constraint system in place.

        ``anchors`` pins specific points (used while dragging).  Returns True
        when the system converged.
        """
        if not self.points:
            self.dof = 0
            self.solve_message = "empty sketch"
            self.free_points = set()
            self.free_radii = set()
            return True

        # dimensions can be written in terms of each other, so the scope the
        # residuals are evaluated against has to carry them too
        scope = self.dimension_scope(scope)

        layout, x0 = self._variable_map()
        idx = self._index(layout)
        residuals = self._residual_builder(idx, scope)

        anchor_rows: List[Tuple[int, int, float, float]] = []
        if anchors:
            for pid, (ax, ay) in anchors.items():
                if pid in self.points:
                    anchor_rows.append((idx[("px", pid)], idx[("py", pid)], ax, ay))

        def full(x: np.ndarray) -> np.ndarray:
            base = residuals(x)
            if not anchor_rows:
                return base
            # The drag anchor is deliberately weak: real constraints must win,
            # the cursor only picks which solution inside the DOF we land on.
            extra = np.empty(len(anchor_rows) * 2)
            for i, (ix, iy, ax, ay) in enumerate(anchor_rows):
                extra[2 * i] = (x[ix] - ax) * DRAG_WEIGHT
                extra[2 * i + 1] = (x[iy] - ay) * DRAG_WEIGHT
            return np.concatenate([base, extra])

        n = len(x0)
        if full(x0).size == 0:
            self.dof = n
            self.solve_message = "unconstrained"
            self.conflicting = []
            self._analyse_freedom(layout, np.zeros((0, n)), 0)
            return True

        # Dragging runs in two passes.  The first uses the soft cursor anchor
        # to choose *which* solution inside the remaining DOF we head towards;
        # the second drops the anchor and re-solves the real constraints from
        # there, so dimensions stay exact no matter how hard the user pulls.
        x = self._minimise(full, x0.copy(), max_iter)
        if anchor_rows:
            x = self._minimise(residuals, x, max_iter)

        hard = residuals(x)
        converged = hard.size == 0 or float(np.abs(hard).max()) < 1e-6

        # keep radii positive after the solve
        for i, (tag, oid, _) in enumerate(layout):
            if tag == "r" and x[i] < 0:
                x[i] = abs(x[i])

        self._apply(layout, x)

        J = self._jacobian(residuals, x, hard) if hard.size else np.zeros((0, n))
        rank = int(np.linalg.matrix_rank(J, tol=1e-8)) if J.size else 0
        self.dof = max(0, n - rank)
        self._analyse_freedom(layout, J, rank)
        if not converged:
            self.solve_message = "over-constrained or inconsistent"
            self.conflicting = self._find_conflicts(idx, scope, x)
        elif self.dof == 0:
            self.solve_message = "fully constrained"
            self.conflicting = []
        else:
            self.solve_message = "%d DOF" % self.dof
            self.conflicting = []
        return converged

    def _analyse_freedom(self, layout, J: np.ndarray, rank: int) -> None:
        """Work out which points and radii the constraints still leave loose.

        The null space of the constraint Jacobian is exactly the set of ways
        the sketch can still move; a variable that appears in none of those
        directions is pinned down.  That is what lets the editor colour
        constrained geometry differently from the rest.
        """
        n = len(layout)
        self.free_points = set()
        self.free_radii = set()

        if n == 0:
            return
        if J.size == 0:
            self.free_points = set(self.points)
            self.free_radii = {e.id for e in self.entities.values()
                               if e.kind in ("circle", "arc")}
            return

        try:
            _u, _s, vt = np.linalg.svd(J)
        except np.linalg.LinAlgError:
            return

        if rank >= n:
            return                      # nothing loose at all
        null_basis = vt[rank:]
        if null_basis.size == 0:
            return
        loose = np.abs(null_basis).max(axis=0) > 1e-7

        for i, (tag, oid, _) in enumerate(layout):
            if i < len(loose) and loose[i]:
                if tag in ("px", "py"):
                    self.free_points.add(oid)
                else:
                    self.free_radii.add(oid)

    def entity_constrained(self, eid: int) -> bool:
        """True when nothing about this entity can move any more."""
        ent = self.entities.get(eid)
        if ent is None:
            return False
        if any(pid in self.free_points for pid in ent.points):
            return False
        if ent.kind in ("circle", "arc") and ent.id in self.free_radii:
            return False
        return True

    def point_constrained(self, pid: int) -> bool:
        return pid not in self.free_points

    # The damped least-squares core is shared with the assembly solver; see
    # lstsq.py.  Both reduce to the same question in different variables.

    @classmethod
    def _minimise(cls, fn, x: np.ndarray, max_iter: int) -> np.ndarray:
        return lstsq.minimise(fn, x, max_iter)

    @staticmethod
    def _jacobian(fn, x: np.ndarray, r0: np.ndarray) -> np.ndarray:
        return lstsq.jacobian(fn, x, r0)

    def _find_conflicts(self, idx, scope, x: np.ndarray) -> List[int]:
        """Constraints whose residual is still large after a failed solve."""
        bad: List[int] = []
        for c in self.constraints.values():
            if not c.driving:
                continue
            single = Sketch.__new__(Sketch)
            single.__dict__ = dict(self.__dict__)
            single.constraints = {c.id: c}
            fn = single._residual_builder(idx, scope)
            try:
                res = fn(x)
            except Exception:
                continue
            if res.size and float(np.abs(res).max()) > 1e-4:
                bad.append(c.id)
        return bad

    # ----------------------------------------------------------------------
    # tessellation helpers (used for picking, previews and profile detection)
    # ----------------------------------------------------------------------

    def entity_polyline(self, eid: int, segments: int = 48) -> List[Tuple[float, float]]:
        ent = self.entities[eid]
        if ent.kind == "line":
            a, b = self.points[ent.points[0]], self.points[ent.points[1]]
            return [a.as_tuple(), b.as_tuple()]
        if ent.kind == "circle":
            c = self.points[ent.points[0]]
            return [(c.x + ent.radius * math.cos(2 * math.pi * i / segments),
                     c.y + ent.radius * math.sin(2 * math.pi * i / segments))
                    for i in range(segments + 1)]
        if ent.kind == "arc":
            c = self.points[ent.points[0]]
            a0, a1 = self.arc_angles(eid)
            return [(c.x + ent.radius * math.cos(a0 + (a1 - a0) * i / segments),
                     c.y + ent.radius * math.sin(a0 + (a1 - a0) * i / segments))
                    for i in range(segments + 1)]
        if ent.kind == "spline":
            return _bspline_samples([self.points[p].as_tuple() for p in ent.points],
                                    segments)
        return []

    def arc_angles(self, eid: int) -> Tuple[float, float]:
        ent = self.entities[eid]
        c = self.points[ent.points[0]]
        p1 = self.points[ent.points[1]]
        p2 = self.points[ent.points[2]]
        a0 = math.atan2(p1.y - c.y, p1.x - c.x)
        a1 = math.atan2(p2.y - c.y, p2.x - c.x)
        if ent.ccw:
            while a1 <= a0:
                a1 += 2 * math.pi
        else:
            while a1 >= a0:
                a1 -= 2 * math.pi
        return a0, a1

    def bounds(self) -> Tuple[float, float, float, float]:
        if not self.points:
            return (-10.0, -10.0, 10.0, 10.0)
        xs, ys = [], []
        for eid in self.entities:
            for x, y in self.entity_polyline(eid, 16):
                xs.append(x)
                ys.append(y)
        if not xs:
            xs = [p.x for p in self.points.values()]
            ys = [p.y for p in self.points.values()]
        return (min(xs), min(ys), max(xs), max(ys))

    # -- serialisation ------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "plane": self.plane.to_dict(),
            "next_id": self._next_id,
            "points": [{"id": p.id, "x": p.x, "y": p.y, "fixed": p.fixed,
                        "origin": p.origin}
                       for p in self.points.values()],
            "entities": [e.to_dict() for e in self.entities.values()],
            "constraints": [c.to_dict() for c in self.constraints.values()],
            "projections": [p.to_dict() for p in self.projections],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Sketch":
        s = cls(SketchPlane.from_dict(data.get("plane", {})),
                data.get("name", "Sketch"))
        for p in data.get("points", []):
            s.points[p["id"]] = SketchPoint(p["id"], p["x"], p["y"],
                                            bool(p.get("fixed", False)),
                                            bool(p.get("origin", False)))
        for e in data.get("entities", []):
            ent = Entity.from_dict(e)
            s.entities[ent.id] = ent
        for c in data.get("constraints", []):
            con = Constraint.from_dict(c)
            s.constraints[con.id] = con
        for record in data.get("projections", []):
            s.projections.append(Projection.from_dict(record))
        s._next_id = int(data.get("next_id", max(
            [1] + list(s.points) + list(s.entities) + list(s.constraints)) + 1))
        return s

    def add_projection(self, source: Dict[str, Any], entities: List[int],
                       construction: bool = False) -> "Projection":
        """Record that these entities are a shadow of that bit of model."""
        record = Projection(id=self._new_id(), source=dict(source or {}),
                            entities=list(entities),
                            construction=construction)
        self.projections.append(record)
        return record

    def projected_entities(self) -> set:
        """Every entity that came from the model rather than from a hand."""
        out = set()
        for record in self.projections:
            out.update(record.entities)
        return out

    def drop_projection(self, entity_id: int) -> None:
        """Forget a projection once its geometry has been deleted."""
        for record in list(self.projections):
            if entity_id in record.entities:
                record.entities.remove(entity_id)
                if not record.entities:
                    self.projections.remove(record)

    def copy(self) -> "Sketch":
        return Sketch.from_dict(self.to_dict())


# ==========================================================================
# helpers
# ==========================================================================


def _bspline_samples(ctrl: Sequence[Tuple[float, float]],
                     segments: int) -> List[Tuple[float, float]]:
    """Catmull-Rom interpolation through the control points."""
    pts = list(ctrl)
    if len(pts) < 2:
        return list(pts)
    if len(pts) == 2:
        return list(pts)
    ext = [pts[0]] + pts + [pts[-1]]
    out: List[Tuple[float, float]] = []
    per = max(2, segments // max(1, len(pts) - 1))
    for i in range(len(ext) - 3):
        p0, p1, p2, p3 = ext[i], ext[i + 1], ext[i + 2], ext[i + 3]
        for j in range(per):
            t = j / per
            t2, t3 = t * t, t * t * t
            out.append((
                0.5 * ((2 * p1[0]) + (-p0[0] + p2[0]) * t
                       + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
                       + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3),
                0.5 * ((2 * p1[1]) + (-p0[1] + p2[1]) * t
                       + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
                       + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3),
            ))
    out.append(pts[-1])
    return out


def polygon_area(poly: Sequence[Tuple[float, float]]) -> float:
    """Signed area (positive when counter-clockwise)."""
    a = 0.0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return a / 2.0


def point_in_polygon(pt: Tuple[float, float],
                     poly: Sequence[Tuple[float, float]]) -> bool:
    x, y = pt
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xi = x1 + (y - y1) * (x2 - x1) / (y2 - y1 or TOL)
            if xi > x:
                inside = not inside
    return inside
