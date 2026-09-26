"""Geometry a script can build: the toolkit behind code features.

A parameter can make a box wider. It cannot decide that a staircase has
fourteen steps rather than twelve, put a baluster on each one and run a
handrail along the top, because that is not a size, it is a structure,
and a structure is a loop. So a code feature is a feature whose solid is
whatever a short script builds, and this module is what the script builds
with.

The shape of it is deliberately small and forgiving:

    post = cylinder(20, 900)                   # radius, height, standing on Z
    rail = circle(20).sweep(path([(0, 0, 900), (2000, 0, 1600)]))
    for i in range(12):
        body += post.move(i * 180, 0, i * 60)

Everything returns a new solid rather than changing the one it was called
on, so a solid can be reused as a template without copying it first.
Booleans are operators, because `rail + posts - holes` reads the way you
would say it. Profiles are drawn on XY about the origin and put into
place by whatever uses them: a sweep moves its profile to the start of
the path and turns it to face along it, because a sweep whose profile
has to be placed by hand first is a sweep nobody gets right the first
time.

Units are the document's, millimetres unless it says otherwise. Angles
are degrees.
"""

from __future__ import annotations

import math
from typing import Iterable, List, Optional, Sequence, Tuple, Union

from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace,
    BRepBuilderAPI_MakePolygon, BRepBuilderAPI_MakeWire,
    BRepBuilderAPI_Transform,
)
from OCP.BRepLib import BRepLib
from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipeShell
from OCP.GC import GC_MakeArcOfCircle, GC_MakeSegment
from OCP.GCE2d import GCE2d_MakeSegment
from OCP.Geom import Geom_CylindricalSurface
from OCP.gp import gp_Ax2, gp_Ax3, gp_Circ, gp_Pnt, gp_Pnt2d, gp_Trsf, gp_Vec
from OCP.TopoDS import TopoDS, TopoDS_Shape

from . import kernel

Number = Union[int, float]
Point3 = Tuple[float, float, float]

AXES = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}


class GeometryError(RuntimeError):
    """Something a script asked for that cannot be built, said plainly."""


def _axis(axis) -> Point3:
    if isinstance(axis, str):
        key = axis.lower()
        if key not in AXES:
            raise GeometryError("an axis is 'x', 'y' or 'z', not %r" % axis)
        return AXES[key]
    x, y, z = (float(c) for c in axis)
    if abs(x) + abs(y) + abs(z) < 1e-12:
        raise GeometryError("an axis cannot be zero length")
    return (x, y, z)


def _unit(v: Sequence[float]) -> Point3:
    n = math.sqrt(sum(c * c for c in v))
    if n < 1e-12:
        raise GeometryError("a direction cannot be zero length")
    return (v[0] / n, v[1] / n, v[2] / n)


def _sub(a, b) -> Point3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a, b) -> Point3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _mul(a, k) -> Point3:
    return (a[0] * k, a[1] * k, a[2] * k)


def _dot(a, b) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b) -> Point3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _transformed(shape: TopoDS_Shape, trsf: gp_Trsf) -> TopoDS_Shape:
    return BRepBuilderAPI_Transform(shape, trsf, True).Shape()


# ------------------------------------------------------------------ solids


class Solid:
    """A body, and everything that can be done to one.

    Nothing here changes the solid it is called on. ``post.move(100)``
    gives a moved copy and leaves ``post`` where it was, which is what lets
    one solid stand in for every baluster on a staircase.
    """

    def __init__(self, shape: Optional[TopoDS_Shape] = None) -> None:
        self.shape = shape

    # -- what it is --------------------------------------------------------

    @property
    def empty(self) -> bool:
        return self.shape is None or self.shape.IsNull()

    @property
    def volume(self) -> float:
        return 0.0 if self.empty else kernel.volume(self.shape)

    @property
    def bbox(self) -> Tuple[float, float, float, float, float, float]:
        if self.empty:
            return (0.0,) * 6
        return kernel.bounding_box(self.shape)

    @property
    def size(self) -> Point3:
        b = self.bbox
        return (b[3] - b[0], b[4] - b[1], b[5] - b[2])

    @property
    def centre(self) -> Point3:
        b = self.bbox
        return ((b[0] + b[3]) / 2, (b[1] + b[4]) / 2, (b[2] + b[5]) / 2)

    def __repr__(self) -> str:
        if self.empty:
            return "<Solid, empty>"
        return "<Solid %.1f x %.1f x %.1f>" % self.size

    # -- moving it ---------------------------------------------------------

    def move(self, x: Number = 0, y: Number = 0, z: Number = 0) -> "Solid":
        if self.empty:
            return Solid()
        return Solid(kernel.translate(self.shape, (x, y, z)))

    def move_to(self, x: Number = 0, y: Number = 0, z: Number = 0) -> "Solid":
        """Move so the centre of the bounding box lands on this point."""
        c = self.centre
        return self.move(x - c[0], y - c[1], z - c[2])

    def rotate(self, axis="z", degrees: Number = 0,
               about: Sequence[Number] = (0, 0, 0)) -> "Solid":
        if self.empty or abs(degrees) < 1e-12:
            return Solid(self.shape)
        return Solid(kernel.rotate(self.shape, tuple(about), _axis(axis),
                                   float(degrees)))

    def mirror(self, plane: str = "yz",
               about: Sequence[Number] = (0, 0, 0)) -> "Solid":
        """Reflected in a plane through ``about``: 'xy', 'yz' or 'xz'."""
        normal = {"xy": "z", "yz": "x", "xz": "y", "zx": "y",
                  "yx": "z", "zy": "x"}.get(plane.lower())
        if normal is None:
            raise GeometryError("a mirror plane is 'xy', 'yz' or 'xz'")
        trsf = gp_Trsf()
        trsf.SetMirror(gp_Ax2(kernel.pnt(tuple(about)),
                              kernel.direction(AXES[normal])))
        return Solid(_transformed(self.shape, trsf))

    def scale(self, factor: Number,
              about: Sequence[Number] = (0, 0, 0)) -> "Solid":
        return Solid(kernel.scale(self.shape, float(factor), tuple(about)))

    # -- combining it ------------------------------------------------------

    def _pair(self, other, op: str) -> "Solid":
        other_shape = other.shape if isinstance(other, Solid) else other
        if self.empty:
            return Solid(other_shape) if op == "join" else Solid()
        if other_shape is None or other_shape.IsNull():
            return Solid(self.shape)
        return Solid(kernel.boolean(self.shape, other_shape, op))

    def __add__(self, other) -> "Solid":
        return self._pair(other, "join")

    __or__ = __add__

    def __radd__(self, other) -> "Solid":
        # so sum(list_of_solids) and `body = 0; body += post` both work
        if other == 0 or other is None:
            return Solid(self.shape)
        return self.__add__(other)

    def __sub__(self, other) -> "Solid":
        return self._pair(other, "cut")

    def __and__(self, other) -> "Solid":
        return self._pair(other, "intersect")

    def fuse(self, *others) -> "Solid":
        return union(self, *others)

    def cut(self, *others) -> "Solid":
        out = self
        for other in others:
            out = out - other
        return out

    def common(self, other) -> "Solid":
        return self & other

    # -- softening it ------------------------------------------------------

    def fillet(self, radius: Number) -> "Solid":
        """Round every edge.  Fails, loudly, if the radius does not fit."""
        if self.empty:
            return Solid()
        try:
            return Solid(kernel.fillet(self.shape, kernel.edges(self.shape),
                                       float(radius)))
        except Exception as exc:
            raise GeometryError("a fillet of %g would not fit: %s"
                                % (radius, exc)) from exc

    def chamfer(self, distance: Number) -> "Solid":
        if self.empty:
            return Solid()
        try:
            return Solid(kernel.chamfer(self.shape,
                                        kernel.edges(self.shape),
                                        float(distance)))
        except Exception as exc:
            raise GeometryError("a chamfer of %g would not fit: %s"
                                % (distance, exc)) from exc

    # -- repeating it ------------------------------------------------------

    def repeat(self, count: int, x: Number = 0, y: Number = 0,
               z: Number = 0) -> "Solid":
        """This, then ``count - 1`` more copies each (x, y, z) further on."""
        return union(*[self.move(i * x, i * y, i * z)
                       for i in range(max(1, int(count)))])

    def repeat_around(self, count: int, axis="z", degrees: Number = 360,
                      about: Sequence[Number] = (0, 0, 0)) -> "Solid":
        """Copies spaced round an axis.  A full turn does not double up."""
        n = max(1, int(count))
        full = abs(abs(float(degrees)) - 360.0) < 1e-9
        step = float(degrees) / (n if full else max(1, n - 1))
        return union(*[self.rotate(axis, i * step, about) for i in range(n)])


def union(*solids) -> Solid:
    """Everything given, as one solid.  Lists are fine too."""
    shapes: List[TopoDS_Shape] = []

    def gather(item):
        if item is None:
            return
        if isinstance(item, Solid):
            if not item.empty:
                shapes.append(item.shape)
        elif isinstance(item, TopoDS_Shape):
            shapes.append(item)
        else:
            for sub in item:
                gather(sub)

    for s in solids:
        gather(s)
    if not shapes:
        return Solid()
    return Solid(kernel.fuse_all(shapes))


# --------------------------------------------------------------- primitives


def box(x: Number, y: Number, z: Number, centered: bool = False) -> Solid:
    """From the origin along +X +Y +Z, or about it if centred."""
    return Solid(kernel.box(float(x), float(y), float(z), (0, 0, 0),
                            centered))


def cylinder(radius: Number, height: Number, axis="z") -> Solid:
    """Standing on the origin, pointing along the axis."""
    return Solid(kernel.cylinder(float(radius), float(height), (0, 0, 0),
                                 _axis(axis)))


def cone(bottom: Number, top: Number, height: Number, axis="z") -> Solid:
    return Solid(kernel.cone(float(bottom), float(top), float(height),
                             (0, 0, 0), _axis(axis)))


def sphere(radius: Number) -> Solid:
    return Solid(kernel.sphere(float(radius)))


def torus(ring: Number, tube: Number, axis="z") -> Solid:
    return Solid(kernel.torus(float(ring), float(tube), (0, 0, 0),
                              _axis(axis)))


def tube(outer: Number, inner: Number, height: Number, axis="z") -> Solid:
    """A pipe: a cylinder with a cylinder taken out of it."""
    if inner >= outer:
        raise GeometryError("a tube's inner radius must be smaller than "
                            "its outer one")
    return cylinder(outer, height, axis) - cylinder(inner, height, axis)


def _span(start, end) -> Tuple[Point3, Point3, float]:
    a = tuple(float(c) for c in start)
    b = tuple(float(c) for c in end)
    length = math.sqrt(_dot(_sub(b, a), _sub(b, a)))
    if length < 1e-9:
        raise GeometryError("the two ends are the same point")
    return a, _sub(b, a), length


def rod(start: Sequence[Number], end: Sequence[Number],
        radius: Number) -> Solid:
    """A round bar from one point to another: balusters, pins, braces."""
    a, along, length = _span(start, end)
    shape = kernel.cylinder(float(radius), length, (0, 0, 0), (0, 0, 1))
    return Solid(_transformed(shape, _frame_at(a, along)))


def beam(start: Sequence[Number], end: Sequence[Number], width: Number,
         height: Number) -> Solid:
    """A square bar from one point to another, centred on the line.

    Its height stands up wherever the line lets it, so a stringer running
    up a stair stays upright rather than rolling onto its side.
    """
    a, along, length = _span(start, end)
    shape = kernel.box(float(width), float(height), length,
                       (-float(width) / 2.0, -float(height) / 2.0, 0.0),
                       False)
    return Solid(_transformed(shape, _frame_at(a, along)))


# ----------------------------------------------------------------- profiles


class Profile:
    """A flat outline on XY, about the origin, waiting to become a solid."""

    def __init__(self, wire, label: str = "profile") -> None:
        self.wire = wire
        self.label = label

    def _face(self):
        mk = BRepBuilderAPI_MakeFace(self.wire, True)
        if not mk.IsDone():
            raise GeometryError("%s is not a closed outline" % self.label)
        return mk.Face()

    def extrude(self, height: Number, taper: Number = 0) -> Solid:
        """Up the Z axis, from XY."""
        return Solid(kernel.extrude(self._face(), (0, 0, 1), float(height),
                                    float(taper)))

    def revolve(self, degrees: Number = 360, axis="y") -> Solid:
        """Round an axis through the origin.  Draw the profile beside it."""
        return Solid(kernel.revolve(self._face(), (0, 0, 0), _axis(axis),
                                    float(degrees)))

    def sweep(self, along: "Path") -> Solid:
        """Along a path, the profile carried to its start and turned to it.

        The profile is drawn about the origin and put into place here,
        because working out the rotation that stands a circle square to
        the first leg of a handrail is exactly the sort of thing nobody
        should have to do by hand.
        """
        if not isinstance(along, Path):
            raise GeometryError("sweep wants a path")
        start, tangent = along.start, along.first_tangent
        placed = _transformed(self._face(), _frame_at(start, tangent))
        mk = BRepOffsetAPI_MakePipeShell(along.wire)
        mk.SetMode(False)                  # corrected Frenet, stays upright
        wires = kernel.explore(placed, kernel.TopAbs_WIRE)
        if not wires:
            raise GeometryError("the profile has no outline to sweep")
        mk.Add(TopoDS.Wire_s(wires[0]), False, False)
        mk.Build()
        if not mk.IsDone():
            raise GeometryError("the sweep failed; a path with a corner "
                                "sharper than the profile is wide usually "
                                "does that, so try a bigger corner radius")
        mk.MakeSolid()
        return Solid(mk.Shape())

    def move(self, x: Number = 0, y: Number = 0) -> "Profile":
        trsf = gp_Trsf()
        trsf.SetTranslation(gp_Vec(float(x), float(y), 0.0))
        return Profile(TopoDS.Wire_s(_transformed(self.wire, trsf)),
                       self.label)


def _frame_at(origin: Point3, tangent: Point3) -> gp_Trsf:
    """The transform taking XY-about-the-origin to square across a path."""
    z = _unit(tangent)
    helper = (0.0, 0.0, 1.0) if abs(z[2]) < 0.9 else (1.0, 0.0, 0.0)
    x = _unit(_cross(helper, z))
    target = gp_Ax3(kernel.pnt(origin), kernel.direction(z),
                    kernel.direction(x))
    trsf = gp_Trsf()
    trsf.SetTransformation(target, gp_Ax3())
    return trsf


def circle(radius: Number) -> Profile:
    if radius <= 0:
        raise GeometryError("a circle needs a positive radius")
    circ = gp_Circ(gp_Ax2(gp_Pnt(0, 0, 0), kernel.direction((0, 0, 1))),
                   float(radius))
    edge = BRepBuilderAPI_MakeEdge(circ).Edge()
    return Profile(BRepBuilderAPI_MakeWire(edge).Wire(), "circle")


def polygon(points: Iterable[Sequence[Number]]) -> Profile:
    """A closed outline through (x, y) points, in order."""
    pts = [(float(p[0]), float(p[1])) for p in points]
    if len(pts) < 3:
        raise GeometryError("a polygon needs three points at least")
    mk = BRepBuilderAPI_MakePolygon()
    for x, y in pts:
        mk.Add(gp_Pnt(x, y, 0.0))
    mk.Close()
    if not mk.IsDone():
        raise GeometryError("those points do not make a polygon")
    return Profile(mk.Wire(), "polygon")


def rect(width: Number, height: Number, centered: bool = True) -> Profile:
    w, h = float(width), float(height)
    if w <= 0 or h <= 0:
        raise GeometryError("a rectangle needs a positive width and height")
    x0, y0 = (-w / 2, -h / 2) if centered else (0.0, 0.0)
    return polygon([(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h)])


def regular(sides: int, radius: Number) -> Profile:
    """A regular polygon, corners on a circle of that radius."""
    n = int(sides)
    if n < 3:
        raise GeometryError("a regular polygon has three sides at least")
    return polygon([(radius * math.cos(2 * math.pi * i / n),
                     radius * math.sin(2 * math.pi * i / n))
                    for i in range(n)])


# -------------------------------------------------------------------- paths


class Path:
    """A line through space, for a sweep to follow."""

    def __init__(self, wire, start: Point3, first_tangent: Point3) -> None:
        self.wire = wire
        self.start = start
        self.first_tangent = first_tangent


def path(points: Iterable[Sequence[Number]], corner: Number = 0) -> Path:
    """Straight legs through (x, y, z) points, rounded at the corners.

    ``corner`` is the radius each bend is rounded to. A handrail wants it:
    a tube swept round a sharp corner folds into itself, and the fold is
    what makes a sweep fail.
    """
    pts = [tuple(float(c) for c in p) for p in points]
    pts = [p if len(p) == 3 else (p[0], p[1], 0.0) for p in pts]
    if len(pts) < 2:
        raise GeometryError("a path needs two points at least")

    radius = float(corner)
    edges = []
    cursor = pts[0]
    for i in range(1, len(pts)):
        here = pts[i]
        last = i == len(pts) - 1
        if last or radius <= 0:
            edges.append(_segment(cursor, here))
            cursor = here
            continue
        nxt = pts[i + 1]
        incoming = _unit(_sub(here, cursor))
        outgoing = _unit(_sub(nxt, here))
        turn = math.acos(max(-1.0, min(1.0, _dot(incoming, outgoing))))
        if turn < 1e-6:
            edges.append(_segment(cursor, here))
            cursor = here
            continue
        # how far back from the corner the arc starts
        back = radius * math.tan(turn / 2.0)
        room = min(math.dist(cursor, here), math.dist(here, nxt))
        back = min(back, room * 0.49)
        a = _sub(here, _mul(incoming, back))
        b = _add(here, _mul(outgoing, back))
        edges.append(_segment(cursor, a))
        edges.append(_arc(a, _mid_of_bend(a, here, b), b))
        cursor = b
    wire = BRepBuilderAPI_MakeWire()
    for edge in edges:
        wire.Add(edge)
    if not wire.IsDone():
        raise GeometryError("those points do not make a continuous path")
    return Path(wire.Wire(), pts[0], _unit(_sub(pts[1], pts[0])))


def _mid_of_bend(a: Point3, corner: Point3, b: Point3) -> Point3:
    """The middle of a circular bend from a to b round the corner."""
    chord_mid = _mul(_add(a, b), 0.5)
    towards = _sub(corner, chord_mid)
    reach = math.sqrt(_dot(towards, towards))
    if reach < 1e-9:
        return chord_mid
    half_chord = math.dist(a, b) / 2.0
    # the arc is tangent to both legs at a and b; its middle sits between
    # the chord and the corner, at the circle's own sagitta
    leg = math.dist(a, corner)
    radius = leg * half_chord / max(math.sqrt(max(leg * leg - half_chord ** 2,
                                                  1e-18)), 1e-9)
    sagitta = radius - math.sqrt(max(radius * radius - half_chord ** 2, 0.0))
    return _add(chord_mid, _mul(_unit(towards), sagitta))


def _segment(a: Point3, b: Point3):
    maker = GC_MakeSegment(kernel.pnt(a), kernel.pnt(b))
    return BRepBuilderAPI_MakeEdge(maker.Value()).Edge()


def _arc(a: Point3, m: Point3, b: Point3):
    maker = GC_MakeArcOfCircle(kernel.pnt(a), kernel.pnt(m), kernel.pnt(b))
    return BRepBuilderAPI_MakeEdge(maker.Value()).Edge()


def arc(start: Sequence[Number], middle: Sequence[Number],
        end: Sequence[Number]) -> Path:
    """A circular arc through three points."""
    a, m, b = (tuple(float(c) for c in p) for p in (start, middle, end))
    edge = _arc(a, m, b)
    wire = BRepBuilderAPI_MakeWire(edge).Wire()
    return Path(wire, a, _unit(_sub(m, a)))


def helix(radius: Number, pitch: Number, height: Number) -> Path:
    """Round the Z axis and up it: a spiral stair's handrail, for one.

    Drawn as a straight line on the unrolled surface of a cylinder, which
    is exactly what a helix is, and then wrapped back round.
    """
    r, p, h = float(radius), float(pitch), float(height)
    if r <= 0 or p <= 0 or h <= 0:
        raise GeometryError("a helix needs a positive radius, pitch and "
                            "height")
    surface = Geom_CylindricalSurface(gp_Ax3(), r)
    turns = h / p
    line = GCE2d_MakeSegment(gp_Pnt2d(0.0, 0.0),
                             gp_Pnt2d(2 * math.pi * turns, h)).Value()
    edge = BRepBuilderAPI_MakeEdge(line, surface).Edge()
    BRepLib.BuildCurves3d_s(edge)
    wire = BRepBuilderAPI_MakeWire(edge).Wire()
    # the tangent at the start of a helix of this radius and pitch
    tangent = _unit((0.0, 2 * math.pi * r, p))
    return Path(wire, (r, 0.0, 0.0), tangent)


# ------------------------------------------------------- what a script sees


def namespace() -> dict:
    """Everything a code feature is handed, by name."""
    return {
        "Solid": Solid,
        "box": box, "cylinder": cylinder, "cone": cone, "sphere": sphere,
        "torus": torus, "tube": tube, "union": union,
        "rod": rod, "beam": beam,
        "circle": circle, "rect": rect, "polygon": polygon,
        "regular": regular,
        "path": path, "arc": arc, "helix": helix,
        "GeometryError": GeometryError,
        "math": math,
    }
