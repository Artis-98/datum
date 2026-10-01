"""Assembly constraints: rigid placements solved against one another.

A sketch solves points on a plane; an assembly solves whole bodies in space.
Each component that is not grounded carries six unknowns - three of position
and three of rotation - and every constraint contributes residuals that must
go to zero.  The same damped least-squares core drives both.

Rotation is held as a *rotation vector*: an axis scaled by the angle in
radians.  Euler angles would gimbal-lock exactly where assemblies live (flat
on a face), and a quaternion needs a normalisation constraint the solver
would have to carry.  A rotation vector has neither problem; its only
awkwardness is the wrap at a half turn, which is tidied up after the solve.

What a constraint attaches to is a :class:`Frame` - a point, a direction and
a radius, which between them describe every piece of geometry worth mating:

    plane    a flat face          origin on the face, direction = outward normal
    axis     a cylinder or line   origin on the axis, direction along it
    circle   a circular edge      origin at the centre, direction along its axis
    point    a vertex             origin only

Frames are stored in the component's *own* coordinates, so a part can be
moved around the assembly all day without them going stale.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.GeomAbs import GeomAbs_CurveType, GeomAbs_SurfaceType
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_REVERSED, TopAbs_VERTEX
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS, TopoDS_Shape
from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec

from . import kernel, lstsq

# -------------------------------------------------------------- frame kinds

PLANE = "plane"
AXIS = "axis"
CIRCLE = "circle"
POINT = "point"

AXIAL = (AXIS, CIRCLE)

# ---------------------------------------------------------- constraint kinds

MATE = "mate"
FLUSH = "flush"
ANGLE = "angle"
TANGENT = "tangent"
INSERT = "insert"
PARALLEL = "parallel"

KINDS = (MATE, FLUSH, ANGLE, TANGENT, INSERT, PARALLEL)

# Inventor does not offer Flush as a *type*: it is one of two Solutions to a
# Mate, and which one you want is a property of the pair of faces rather than
# a different relationship.  They stay two kinds here because the residual
# differs by a sign, but the dialog presents them the way Inventor does.
TYPES = (MATE, ANGLE, TANGENT, INSERT, PARALLEL)
SOLUTIONS = (MATE, FLUSH)


def type_of(kind: str) -> str:
    """The Type row entry a kind belongs under."""
    return MATE if kind == FLUSH else kind

KIND_LABELS = {
    MATE: "Mate",
    FLUSH: "Flush",
    ANGLE: "Angle",
    TANGENT: "Tangent",
    INSERT: "Insert",
    PARALLEL: "Parallel",
}

SOLUTION_HINTS = {
    MATE: "Faces turned towards each other - the arrows point in.",
    FLUSH: "Faces turned the same way - the arrows point the same way.",
}

KIND_HINTS = {
    MATE: "Faces turned towards each other, axes made collinear.",
    FLUSH: "Faces turned the same way, lying in one plane.",
    ANGLE: "A fixed angle between two faces or axes.",
    TANGENT: "A round face touching a flat or round one.",
    INSERT: "Axes collinear and the two circles a set distance apart.",
    PARALLEL: "Two faces or axes kept parallel, free to slide apart.",
}

KIND_UNITS = {ANGLE: "deg"}

# The offset expression means different things per kind, and the dialog says
# so rather than leaving the user to guess.
OFFSET_LABELS = {
    MATE: "Offset",
    FLUSH: "Offset",
    ANGLE: "Angle",
    TANGENT: "Clearance",
    INSERT: "Offset",
    PARALLEL: "Offset",
}

FLIP_LABELS = {
    TANGENT: ("Outside", "Inside"),
    INSERT: ("Opposed", "Aligned"),
    PARALLEL: ("Aligned", "Opposed"),
}

# How hard a component resists being moved when the constraints do not care
# where it goes.  Small enough that a real constraint always wins, large
# enough that a free part does not wander off across the assembly.
#
# This is priced per *span of the assembly*, not per millimetre, and that
# distinction is the whole point.  A flat weight makes travel cost grow with
# the size of the parts while rotation stays fixed, so past about a hundred
# millimetres the cheapest way for the solver to close a gap stops being
# "slide the part down" and becomes "tilt it until the gap measures zero" -
# which satisfies the distance and wrecks the direction, and it gets stuck
# there.  Dividing by how far the geometry actually starts apart keeps the
# price of crossing the assembly the same whether it is a bracket or a bench.
ANCHOR_TRAVEL = 0.3
# rotation is in radians, where a whole turn is only ~6 units, so the same
# nominal weight would let parts spin freely; they should slide, not roll
ANCHOR_ROTATION = 0.45
# no assembly counts as smaller than this, so parts that start on top of one
# another do not get an enormous anchor from a near-zero span
ANCHOR_MIN_SPAN = 15.0

SATISFIED = 1e-4

# how far off a symmetry a retry starts, in radians.  Big enough that the
# finite-difference Jacobian sees which way is downhill, small enough that
# the answer it finds is still the one nearest to where the part was.
NUDGE = 0.25


# --------------------------------------------------------------------------
# rotation vectors
# --------------------------------------------------------------------------


def rotation_matrix(w: Sequence[float]) -> np.ndarray:
    """Rodrigues' formula: a rotation vector to a 3x3 matrix."""
    wx, wy, wz = float(w[0]), float(w[1]), float(w[2])
    theta = math.sqrt(wx * wx + wy * wy + wz * wz)
    K = np.array([[0.0, -wz, wy], [wz, 0.0, -wx], [-wy, wx, 0.0]])
    if theta < 1e-9:
        # the series expansion, which stays well behaved at the identity where
        # the closed form divides by zero
        return np.eye(3) + K + 0.5 * (K @ K)
    return (np.eye(3)
            + (math.sin(theta) / theta) * K
            + ((1.0 - math.cos(theta)) / (theta * theta)) * (K @ K))


def rotation_vector(m: np.ndarray) -> Tuple[float, float, float]:
    """The inverse of :func:`rotation_matrix`, for a well-formed rotation."""
    trace = float(np.clip((m[0, 0] + m[1, 1] + m[2, 2] - 1.0) * 0.5, -1.0, 1.0))
    theta = math.acos(trace)
    if theta < 1e-9:
        return (0.0, 0.0, 0.0)
    if abs(math.pi - theta) < 1e-6:
        # a half turn: the antisymmetric part vanishes, so take the axis from
        # the largest diagonal of (R + I) instead
        d = np.diag(m) + 1.0
        i = int(np.argmax(d))
        axis = (m[:, i] + np.eye(3)[:, i]) / math.sqrt(max(d[i], 1e-12) * 2.0)
        axis = axis / (np.linalg.norm(axis) or 1.0)
        return tuple(float(c) * theta for c in axis)
    scale = theta / (2.0 * math.sin(theta))
    return (float((m[2, 1] - m[1, 2]) * scale),
            float((m[0, 2] - m[2, 0]) * scale),
            float((m[1, 0] - m[0, 1]) * scale))


def shorten(w: Sequence[float]) -> Tuple[float, float, float]:
    """Bring a rotation vector back to the shortest equivalent turn."""
    theta = math.sqrt(sum(float(c) * float(c) for c in w))
    if theta <= math.pi or theta < 1e-9:
        return (float(w[0]), float(w[1]), float(w[2]))
    turns = math.floor((theta + math.pi) / (2.0 * math.pi))
    scale = (theta - turns * 2.0 * math.pi) / theta
    return (float(w[0]) * scale, float(w[1]) * scale, float(w[2]) * scale)


def compose(outer: Sequence[float], inner: Sequence[float]
            ) -> Tuple[float, float, float]:
    """Rotation vector of ``R(outer) @ R(inner)``."""
    return shorten(rotation_vector(rotation_matrix(outer)
                                   @ rotation_matrix(inner)))


@dataclass
class Placement:
    """Where a component sits: a translation and a rotation about its origin."""

    position: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    rotation: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])

    def copy(self) -> "Placement":
        return Placement(list(self.position), list(self.rotation))

    def matrix(self) -> np.ndarray:
        return rotation_matrix(self.rotation)

    def apply_point(self, p: Sequence[float]) -> Tuple[float, float, float]:
        out = self.matrix() @ np.asarray(p, dtype=float) + np.asarray(
            self.position, dtype=float)
        return (float(out[0]), float(out[1]), float(out[2]))

    def apply_direction(self, d: Sequence[float]) -> Tuple[float, float, float]:
        out = self.matrix() @ np.asarray(d, dtype=float)
        return (float(out[0]), float(out[1]), float(out[2]))

    def trsf(self) -> gp_Trsf:
        """The same placement as an OCCT transform, rotation applied first."""
        rotate = gp_Trsf()
        w = self.rotation
        theta = math.sqrt(sum(float(c) * float(c) for c in w))
        if theta > 1e-12:
            rotate.SetRotation(
                gp_Ax1(gp_Pnt(0.0, 0.0, 0.0),
                       gp_Dir(w[0] / theta, w[1] / theta, w[2] / theta)),
                theta)
        move = gp_Trsf()
        move.SetTranslation(gp_Vec(float(self.position[0]),
                                   float(self.position[1]),
                                   float(self.position[2])))
        return move.Multiplied(rotate)

    def location(self) -> TopLoc_Location:
        return TopLoc_Location(self.trsf())

    def translated(self, delta: Sequence[float]) -> "Placement":
        return Placement([self.position[i] + float(delta[i]) for i in range(3)],
                         list(self.rotation))

    def rotated(self, w: Sequence[float],
                centre: Optional[Sequence[float]] = None) -> "Placement":
        """Turn by ``w`` about a world point, keeping the body rigid."""
        R = rotation_matrix(w)
        pivot = np.asarray(centre if centre is not None else self.position,
                           dtype=float)
        position = R @ (np.asarray(self.position, dtype=float) - pivot) + pivot
        return Placement([float(c) for c in position],
                         list(compose(w, self.rotation)))

    def to_dict(self) -> Dict[str, Any]:
        return {"position": [float(c) for c in self.position],
                "rotation": [float(c) for c in self.rotation]}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Placement":
        return cls([float(c) for c in data.get("position", (0, 0, 0))],
                   [float(c) for c in data.get("rotation", (0, 0, 0))])


# --------------------------------------------------------------------------
# frames
# --------------------------------------------------------------------------


@dataclass
class Frame:
    """The geometry a constraint attaches to, in component coordinates."""

    kind: str = POINT
    origin: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    direction: Tuple[float, float, float] = (0.0, 0.0, 1.0)
    radius: float = 0.0
    label: str = ""

    @property
    def axial(self) -> bool:
        return self.kind in AXIAL

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "origin": list(self.origin),
                "direction": list(self.direction), "radius": self.radius,
                "label": self.label}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Frame":
        return cls(kind=str(data.get("kind", POINT)),
                   origin=tuple(float(c) for c in
                                data.get("origin", (0, 0, 0))),
                   direction=tuple(float(c) for c in
                                   data.get("direction", (0, 0, 1))),
                   radius=float(data.get("radius", 0.0)),
                   label=str(data.get("label", "")))


def _unit(v: Sequence[float]) -> Tuple[float, float, float]:
    n = math.sqrt(sum(float(c) * float(c) for c in v)) or 1.0
    return (float(v[0]) / n, float(v[1]) / n, float(v[2]) / n)


def _project_onto_axis(point: Sequence[float], origin: Sequence[float],
                       direction: Sequence[float]
                       ) -> Tuple[float, float, float]:
    """Slide an axis anchor along to the nearest point to ``point``.

    A cylinder's stored axis can be anchored anywhere, often far outside the
    face you actually clicked.  Anchoring it beside the face keeps offsets
    readable and previews drawn where the user is looking.
    """
    d = np.asarray(direction, dtype=float)
    o = np.asarray(origin, dtype=float)
    t = float(np.dot(np.asarray(point, dtype=float) - o, d))
    out = o + t * d
    return (float(out[0]), float(out[1]), float(out[2]))


def frame_from_shape(shape: TopoDS_Shape) -> Optional[Frame]:
    """Describe a picked face, edge or vertex as a constraint frame.

    Returns ``None`` for geometry nothing can be mated to - a spline face, a
    free-form edge - rather than inventing a frame that would solve wrongly.
    """
    if shape is None or shape.IsNull():
        return None

    kind = shape.ShapeType()

    if kind == TopAbs_FACE:
        face = TopoDS.Face_s(shape)
        try:
            surface = BRepAdaptor_Surface(face)
            surface_type = surface.GetType()
        except Exception:
            return None

        if surface_type == GeomAbs_SurfaceType.GeomAbs_Plane:
            axis = surface.Plane().Axis()
            d = axis.Direction()
            normal = (d.X(), d.Y(), d.Z())
            # the adaptor reports the surface's own normal; a reversed face
            # points the other way, and "which way does this face look" is
            # the whole basis of a mate
            if face.Orientation() == TopAbs_REVERSED:
                normal = (-normal[0], -normal[1], -normal[2])
            return Frame(PLANE, kernel.shape_centre(face), _unit(normal),
                         0.0, "planar face")

        if surface_type == GeomAbs_SurfaceType.GeomAbs_Cylinder:
            cylinder = surface.Cylinder()
            axis = cylinder.Axis()
            location, d = axis.Location(), axis.Direction()
            direction = _unit((d.X(), d.Y(), d.Z()))
            origin = _project_onto_axis(kernel.shape_centre(face),
                                        (location.X(), location.Y(),
                                         location.Z()), direction)
            return Frame(AXIS, origin, direction, cylinder.Radius(),
                         "cylindrical face")

        if surface_type == GeomAbs_SurfaceType.GeomAbs_Cone:
            cone = surface.Cone()
            axis = cone.Axis()
            location, d = axis.Location(), axis.Direction()
            direction = _unit((d.X(), d.Y(), d.Z()))
            origin = _project_onto_axis(kernel.shape_centre(face),
                                        (location.X(), location.Y(),
                                         location.Z()), direction)
            return Frame(AXIS, origin, direction, cone.RefRadius(),
                         "conical face")
        return None

    if kind == TopAbs_EDGE:
        edge = TopoDS.Edge_s(shape)
        try:
            curve = BRepAdaptor_Curve(edge)
            curve_type = curve.GetType()
        except Exception:
            return None

        if curve_type == GeomAbs_CurveType.GeomAbs_Circle:
            circle = curve.Circle()
            location = circle.Location()
            d = circle.Axis().Direction()
            return Frame(CIRCLE, (location.X(), location.Y(), location.Z()),
                         _unit((d.X(), d.Y(), d.Z())), circle.Radius(),
                         "circular edge")

        if curve_type == GeomAbs_CurveType.GeomAbs_Line:
            line = curve.Line()
            location, d = line.Location(), line.Direction()
            direction = _unit((d.X(), d.Y(), d.Z()))
            origin = _project_onto_axis(kernel.shape_centre(edge),
                                        (location.X(), location.Y(),
                                         location.Z()), direction)
            return Frame(AXIS, origin, direction, 0.0, "straight edge")
        return None

    if kind == TopAbs_VERTEX:
        return Frame(POINT, kernel.shape_centre(shape), (0.0, 0.0, 1.0), 0.0,
                     "vertex")

    return None


def compatible(a: Frame, b: Frame, kind: str) -> bool:
    """Whether this pairing is one the solver knows how to drive."""
    if a is None or b is None:
        return False
    if kind == INSERT:
        return a.axial and b.axial
    if kind in (ANGLE, PARALLEL):
        return a.kind != POINT and b.kind != POINT
    if kind == TANGENT:
        return ((a.axial and b.kind == PLANE) or (a.kind == PLANE and b.axial)
                or (a.axial and b.axial))
    # mate and flush cope with every combination except vertex-to-vertex
    # under flush, which has nothing to align
    if kind == FLUSH and (a.kind == POINT or b.kind == POINT):
        return False
    return True


def describe(kind: str, a: Frame, b: Frame) -> str:
    """One line saying what this constraint will actually do."""
    if not compatible(a, b, kind):
        return "%s does not apply to a %s and a %s." % (
            KIND_LABELS.get(kind, kind), a.label if a else "?",
            b.label if b else "?")
    if kind == INSERT:
        return "Axes collinear, circles held apart by the offset."
    if kind == ANGLE:
        return "A fixed angle between the two directions."
    if kind == PARALLEL:
        return "The two directions held parallel; nothing else is fixed."
    if kind == TANGENT:
        return "The round face rolls against the other, touching."
    if a.axial and b.axial:
        return "Axes made collinear; the part can still slide along them."
    if a.kind == PLANE and b.kind == PLANE:
        return ("Faces turned towards each other." if kind == MATE
                else "Faces turned the same way.")
    return "%s between a %s and a %s." % (KIND_LABELS.get(kind, kind),
                                          a.label, b.label)


# --------------------------------------------------------------------------
# residuals
# --------------------------------------------------------------------------


@dataclass
class ResolvedConstraint:
    """A constraint with both its frames found, ready to be solved."""

    id: int = 0
    kind: str = MATE
    occ_a: int = 0
    occ_b: int = 0
    frame_a: Frame = field(default_factory=Frame)
    frame_b: Frame = field(default_factory=Frame)
    offset: float = 0.0
    flip: bool = False


def _rows(c: ResolvedConstraint, oa: np.ndarray, da: np.ndarray,
          ob: np.ndarray, db: np.ndarray) -> List[float]:
    """Residuals for one constraint, given both frames in world space."""
    ka, kb = c.frame_a.kind, c.frame_b.kind
    axis_a, axis_b = ka in AXIAL, kb in AXIAL
    ra, rb = c.frame_a.radius, c.frame_b.radius
    delta = ob - oa
    out: List[float] = []

    if c.kind == ANGLE:
        # one residual, one degree of freedom removed
        out.append(float(np.dot(da, db)) - math.cos(math.radians(c.offset)))
        return out

    if c.kind == PARALLEL:
        # An Angle of zero says the same thing, but its residual has no
        # slope at zero, which is exactly where it is wanted, so the solver
        # crawls in.  Matching the directions themselves keeps a slope all
        # the way.  Two removed: a direction has two, the spin about it is
        # left free, and so is every slide.
        if ka == PLANE and kb == PLANE:
            out.extend(float(v) for v in (da + db if c.flip else da - db))
        else:
            # an axis has no front and back, so either way along it will do
            out.extend(float(v) for v in np.cross(da, db))
        return out

    if c.kind == TANGENT:
        sign = -1.0 if c.flip else 1.0
        if axis_a and kb == PLANE:
            out.append(float(np.dot(da, db)))          # axis parallel to face
            out.append(float(np.dot(-delta, db)) - sign * (ra + c.offset))
            return out
        if ka == PLANE and axis_b:
            out.append(float(np.dot(db, da)))
            out.append(float(np.dot(delta, da)) - sign * (rb + c.offset))
            return out
        if axis_a and axis_b:
            out.extend(float(v) for v in np.cross(da, db))
            perpendicular = delta - float(np.dot(delta, da)) * da
            out.append(float(np.linalg.norm(perpendicular))
                       - sign * (ra + rb + c.offset))
            return out
        # two flat faces cannot be tangent; treat it as a mate rather than
        # silently contributing nothing
        c = ResolvedConstraint(c.id, MATE, c.occ_a, c.occ_b, c.frame_a,
                               c.frame_b, c.offset, c.flip)

    if c.kind == INSERT and axis_a and axis_b:
        # Insert pins the direction as well as the line, because which way a
        # bolt goes into a hole is exactly what it is for
        out.extend(float(v) for v in (da - db if c.flip else da + db))
        out.extend(float(v) for v in
                   (delta - float(np.dot(delta, da)) * da))
        out.append(float(np.dot(delta, da)) - c.offset)
        return out

    aligned = (c.kind == FLUSH)

    if ka == PLANE and kb == PLANE:
        out.extend(float(v) for v in (da - db if aligned else da + db))
        out.append(float(np.dot(delta, da)) - c.offset)
        return out

    if axis_a and axis_b:
        # collinear axes.  The slide along them is deliberately left free:
        # that is what a concentric mate means, and pinning it would jam the
        # part to the middle of whatever cylinder was clicked.
        out.extend(float(v) for v in np.cross(da, db))
        out.extend(float(v) for v in
                   (delta - float(np.dot(delta, da)) * da))
        return out

    if ka == PLANE and axis_b:
        out.append(float(np.dot(db, da)))              # axis parallel to face
        out.append(float(np.dot(delta, da)) - c.offset)
        return out

    if axis_a and kb == PLANE:
        out.append(float(np.dot(da, db)))
        out.append(float(np.dot(-delta, db)) - c.offset)
        return out

    if ka == POINT and kb == POINT:
        out.extend(float(v) for v in delta)
        return out

    if ka == POINT and kb == PLANE:
        out.append(float(np.dot(-delta, db)) - c.offset)
        return out

    if ka == PLANE and kb == POINT:
        out.append(float(np.dot(delta, da)) - c.offset)
        return out

    if ka == POINT and axis_b:
        out.extend(float(v) for v in
                   (-delta - float(np.dot(-delta, db)) * db))
        return out

    if axis_a and kb == POINT:
        out.extend(float(v) for v in
                   (delta - float(np.dot(delta, da)) * da))
        return out

    return out


# --------------------------------------------------------------------------
# the solve
# --------------------------------------------------------------------------


@dataclass
class SolveReport:
    ok: bool = True
    dof: int = 0
    free: int = 0
    unsatisfied: List[int] = field(default_factory=list)
    message: str = ""


def _span(prepared, current) -> float:
    """How far the constrained geometry starts apart, roughly.

    This is the distance scale of the problem: the furthest a free component
    might reasonably have to travel to satisfy what has been asked of it,
    together with any radius or offset that features in the answer.
    """
    reach = 0.0
    for c, ao, ad, bo, bd in prepared:
        pa, Ra = current[c.occ_a]
        pb, Rb = current[c.occ_b]
        reach = max(reach,
                    float(np.linalg.norm((Rb @ bo + pb) - (Ra @ ao + pa))),
                    abs(c.offset), c.frame_a.radius, c.frame_b.radius)
    return max(ANCHOR_MIN_SPAN, reach)


def solve(placements: Dict[int, Placement],
          constraints: Sequence[ResolvedConstraint],
          free: Sequence[int], max_iter: int = 60) -> SolveReport:
    """Move the free components until the constraints are satisfied.

    ``placements`` is updated in place.  Components not listed in ``free``
    are held exactly where they are - that is what grounding means.
    """
    report = SolveReport()
    order = [oid for oid in free if oid in placements]
    report.free = len(order)

    live = [c for c in constraints
            if c.occ_a in placements and c.occ_b in placements]
    if not live or not order:
        report.dof = 6 * len(order)
        report.message = _describe_state(report, len(live))
        return report

    index = {oid: i for i, oid in enumerate(order)}
    fixed = {oid: (np.asarray(p.position, dtype=float), p.matrix())
             for oid, p in placements.items() if oid not in index}

    x0 = np.zeros(6 * len(order))
    for oid, i in index.items():
        p = placements[oid]
        x0[6 * i:6 * i + 3] = p.position
        x0[6 * i + 3:6 * i + 6] = p.rotation

    # frames are fixed in component space, so unpack them once
    prepared = [(c,
                 np.asarray(c.frame_a.origin, dtype=float),
                 np.asarray(c.frame_a.direction, dtype=float),
                 np.asarray(c.frame_b.origin, dtype=float),
                 np.asarray(c.frame_b.direction, dtype=float))
                for c in live]

    def state(x: np.ndarray) -> Dict[int, Tuple[np.ndarray, np.ndarray]]:
        out = dict(fixed)
        for oid, i in index.items():
            out[oid] = (x[6 * i:6 * i + 3],
                        rotation_matrix(x[6 * i + 3:6 * i + 6]))
        return out

    def residuals(x: np.ndarray) -> np.ndarray:
        current = state(x)
        rows: List[float] = []
        for c, ao, ad, bo, bd in prepared:
            pa, Ra = current[c.occ_a]
            pb, Rb = current[c.occ_b]
            rows.extend(_rows(c, Ra @ ao + pa, Ra @ ad,
                              Rb @ bo + pb, Rb @ bd))
        return np.asarray(rows, dtype=float) if rows else np.zeros(0)

    span = _span(prepared, state(x0))
    weights = np.tile(
        np.array([ANCHOR_TRAVEL / span] * 3 + [ANCHOR_ROTATION] * 3),
        len(order))

    def anchored(x: np.ndarray) -> np.ndarray:
        base = residuals(x)
        return np.concatenate([base, (x - x0) * weights])

    if residuals(x0).size == 0:
        report.dof = 6 * len(order)
        report.message = _describe_state(report, len(live))
        return report

    # Position is in millimetres and rotation in radians, which are not
    # comparable amounts: on a 250 mm assembly a milliradian of tilt swings
    # a face a quarter of a millimetre, so the residuals are hundreds of
    # times more sensitive to the rotation unknowns than the position ones.
    # Damped least squares damps every unknown equally, so mixed units make
    # it hold back the sensitive directions hardest and stop with the part
    # hanging at an angle that happens to measure the right distance.
    # Solving in units where one unit of any unknown moves the geometry
    # about equally far puts them on the same terms.  The scale comes off
    # again before anything is written back.
    unit = np.tile(np.array([1.0, 1.0, 1.0, span, span, span]), len(order))

    def scaled(u: np.ndarray) -> np.ndarray:
        return residuals(u / unit)

    def scaled_anchored(u: np.ndarray) -> np.ndarray:
        return anchored(u / unit)

    def attempt(start: np.ndarray) -> np.ndarray:
        # Two passes, exactly as the sketch solver drags: the anchored pass
        # picks *which* solution to head for out of the many a loose
        # assembly allows, then the unanchored pass makes the real
        # constraints exact.
        u = lstsq.minimise(scaled_anchored, start * unit, max_iter)
        u = lstsq.minimise(scaled, u, max_iter)
        return u / unit

    x = attempt(x0)
    worst = float(np.abs(residuals(x)).max())

    # Two faces pointing exactly away from each other are a stationary point
    # of the flush residual: turning the part either way improves it by the
    # same amount, so the gradient cancels and the solver sits on the spike
    # rather than falling off it.  Switching a mate to a flush lands there
    # every time, because a mate is precisely that arrangement.  Nudging the
    # free components off the symmetry and starting again gets past it, and
    # the nudge is applied to the original pose rather than the stuck one so
    # a failed attempt cannot drag the next one along with it.
    for nudge in ((NUDGE, 0.0, 0.0), (0.0, NUDGE, 0.0), (0.0, 0.0, NUDGE)):
        if worst < SATISFIED:
            break
        start = x0.copy()
        for i in range(len(order)):
            start[6 * i + 3:6 * i + 6] = compose(
                nudge, x0[6 * i + 3:6 * i + 6])
        candidate = attempt(start)
        value = float(np.abs(residuals(candidate)).max())
        if value < worst:
            x, worst = candidate, value

    final = residuals(x)
    report.ok = final.size == 0 or float(np.abs(final).max()) < SATISFIED

    for oid, i in index.items():
        placements[oid].position = [float(v) for v in x[6 * i:6 * i + 3]]
        placements[oid].rotation = list(shorten(x[6 * i + 3:6 * i + 6]))

    J = lstsq.jacobian(residuals, x, final) if final.size else np.zeros((0, 0))
    report.dof = lstsq.degrees_of_freedom(J, 6 * len(order))

    if not report.ok:
        report.unsatisfied = _unsatisfied(prepared, state(x))
    report.message = _describe_state(report, len(live))
    return report


def _unsatisfied(prepared, current) -> List[int]:
    """Which constraints are still fighting after a failed solve."""
    bad: List[int] = []
    for c, ao, ad, bo, bd in prepared:
        pa, Ra = current[c.occ_a]
        pb, Rb = current[c.occ_b]
        rows = _rows(c, Ra @ ao + pa, Ra @ ad, Rb @ bo + pb, Rb @ bd)
        if rows and max(abs(v) for v in rows) > SATISFIED:
            bad.append(c.id)
    return bad


def _describe_state(report: SolveReport, count: int) -> str:
    if not report.ok:
        return "%d constraint(s) cannot be satisfied" % len(report.unsatisfied)
    if report.free == 0:
        return "All components grounded"
    if report.dof == 0:
        return "Fully constrained"
    return "%d constraint(s), %d DOF" % (count, report.dof)
