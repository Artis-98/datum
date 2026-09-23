"""Recognising a part as cut from flat stock, and flattening it.

A part that came off a sheet has a signature: two big planar faces looking
opposite ways, exactly the material thickness apart, joined by narrow
perimeter faces.  That is what this module looks for.  It is deliberately a
*detector* rather than an assumption - a part with a boss standing proud of
the sheet is not flat cuttable, and saying so is worth more than exporting a
toolpath that quietly ignores the boss.

Once the face is known, the profile comes from its wires: the outer wire is
the part's outline, every inner wire is a hole or a slot.  They are brought
into the world XY plane so everything downstream - layout, offsetting, DXF -
works in honest 2D millimetres.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_Transform
from OCP.BRepTools import BRepTools
from OCP.GeomAbs import GeomAbs_SurfaceType
from OCP.TopAbs import TopAbs_REVERSED, TopAbs_WIRE
from OCP.TopoDS import TopoDS, TopoDS_Face, TopoDS_Shape, TopoDS_Wire
from OCP.gp import gp_Ax3, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec

from . import kernel
from .kernel import KernelError

# how nearly antiparallel two normals must be to count as a facing pair
ANGLE_TOLERANCE = 1e-3
# how far a part may stand proud of its own sheet faces before it stops
# being flat cuttable, in mm
PROUD_TOLERANCE = 0.05
MIN_THICKNESS = 1e-4

OUTER_KEY = "outer"


# --------------------------------------------------------------------------
# detection
# --------------------------------------------------------------------------


@dataclass
class Detection:
    """What the sheet-part test made of a body."""

    ok: bool = False
    face: Optional[TopoDS_Face] = None
    opposite: Optional[TopoDS_Face] = None
    thickness: float = 0.0
    normal: Tuple[float, float, float] = (0.0, 0.0, 1.0)
    reason: str = ""

    @property
    def faces(self) -> Tuple[Optional[TopoDS_Face], Optional[TopoDS_Face]]:
        return (self.face, self.opposite)


def _plane_of(face: TopoDS_Face) -> Optional[Tuple[Tuple[float, float, float],
                                                   Tuple[float, float, float]]]:
    """(a point on the face, its outward normal), or None if it is not flat."""
    try:
        surface = BRepAdaptor_Surface(face)
        if surface.GetType() != GeomAbs_SurfaceType.GeomAbs_Plane:
            return None
        axis = surface.Plane().Axis()
    except Exception:
        return None
    d = axis.Direction()
    normal = (d.X(), d.Y(), d.Z())
    if face.Orientation() == TopAbs_REVERSED:
        normal = (-normal[0], -normal[1], -normal[2])
    return (kernel.shape_centre(face), normal)


def _extent_along(shape: TopoDS_Shape,
                  direction: Sequence[float]) -> float:
    """How thick the whole body is when measured along ``direction``."""
    turned = _to_xy(shape, (0.0, 0.0, 0.0), direction)
    _x0, _y0, zmin, _x1, _y1, zmax = kernel.bounding_box(turned)
    return zmax - zmin


def detect_cut_face(shape: TopoDS_Shape) -> Detection:
    """Find the face a flat part would be cut from, or say why there is none.

    The largest pair of opposed planar faces wins.  Scoring on the *smaller*
    of the two areas is deliberate: a plate with a big chamfer has a huge
    face opposed by a sliver, and that pair is not the sheet.
    """
    if not kernel.is_valid(shape):
        return Detection(reason="there is no body to read")

    planar: List[Tuple[TopoDS_Face, Tuple[float, float, float],
                       Tuple[float, float, float], float]] = []
    for raw in kernel.faces(shape):
        face = TopoDS.Face_s(raw)
        plane = _plane_of(face)
        if plane is None:
            continue
        origin, normal = plane
        planar.append((face, origin, normal, kernel.face_area(face)))

    if not planar:
        return Detection(
            reason="this part has no flat faces at all, so it was not cut "
                   "from sheet")

    best = None
    for i in range(len(planar)):
        face_a, origin_a, normal_a, area_a = planar[i]
        for j in range(i + 1, len(planar)):
            face_b, origin_b, normal_b, area_b = planar[j]
            facing = sum(normal_a[k] * normal_b[k] for k in range(3))
            if facing > -(1.0 - ANGLE_TOLERANCE):
                continue                       # not looking opposite ways
            # both normals point out of the material, so they point away
            # from each other: the far face lies *behind* this one's normal
            separation = sum((origin_a[k] - origin_b[k]) * normal_a[k]
                             for k in range(3))
            if separation <= MIN_THICKNESS:
                continue        # coincident, or the two face inward instead
            score = min(area_a, area_b)
            # The two faces of a sheet have the same area, so the score
            # alone would leave the choice to face order and the part would
            # come out mirrored on a whim.  Preferring the normal that
            # points most nearly up means a part reads the way it was
            # modelled, and the Flip control takes it from there.
            for face, other, normal in ((face_a, face_b, normal_a),
                                        (face_b, face_a, normal_b)):
                upness = (normal[2], normal[1], normal[0])
                if best is None or (score, upness) > (best[0], best[4]):
                    best = (score, face, other, separation, upness, normal)

    if best is None:
        return Detection(
            reason="no two flat faces on this part look opposite ways, so "
                   "there is no sheet to cut it from")

    _score, face, opposite, thickness, _upness, normal = best
    extent = _extent_along(shape, normal)
    if extent - thickness > max(PROUD_TOLERANCE, thickness * 0.01):
        return Detection(
            face=face, opposite=opposite, thickness=thickness, normal=normal,
            reason="this part is %.2f mm thick overall but its sheet faces "
                   "are only %.2f mm apart, so something stands proud of the "
                   "sheet - it cannot be cut flat"
                   % (extent, thickness))

    return Detection(ok=True, face=face, opposite=opposite,
                     thickness=thickness, normal=normal)


def opposite_face(shape: TopoDS_Shape,
                  face: TopoDS_Face) -> Optional[TopoDS_Face]:
    """The other side of the sheet from ``face``, when there is one.

    Used by the Flip control: reading the part off its back face is what
    mirrors it, and doing it with real geometry rather than negating X keeps
    hole positions honest.
    """
    plane = _plane_of(face)
    if plane is None:
        return None
    origin, normal = plane
    best = None
    for raw in kernel.faces(shape):
        candidate = TopoDS.Face_s(raw)
        other = _plane_of(candidate)
        if other is None:
            continue
        other_origin, other_normal = other
        facing = sum(normal[k] * other_normal[k] for k in range(3))
        if facing > -(1.0 - ANGLE_TOLERANCE):
            continue
        separation = sum((origin[k] - other_origin[k]) * normal[k]
                         for k in range(3))
        if separation <= MIN_THICKNESS:
            continue
        area = kernel.face_area(candidate)
        if best is None or area > best[0]:
            best = (area, candidate)
    return best[1] if best else None


def thickness_between(face: TopoDS_Face,
                      other: Optional[TopoDS_Face]) -> float:
    if other is None:
        return 0.0
    a, b = _plane_of(face), _plane_of(other)
    if a is None or b is None:
        return 0.0
    return abs(sum((b[0][k] - a[0][k]) * a[1][k] for k in range(3)))


# --------------------------------------------------------------------------
# flattening
# --------------------------------------------------------------------------


def _to_xy(shape: TopoDS_Shape, origin: Sequence[float],
           normal: Sequence[float],
           xdir: Optional[Sequence[float]] = None) -> TopoDS_Shape:
    """Move a shape so the given plane becomes the world XY plane.

    Real geometry is transformed, not projected: lines stay lines and
    circles stay circles, which is what keeps a 300 mm arc an arc all the
    way through to the DXF.
    """
    direction = gp_Dir(float(normal[0]), float(normal[1]), float(normal[2]))
    if xdir is not None:
        system = gp_Ax3(gp_Pnt(*[float(c) for c in origin]), direction,
                        gp_Dir(float(xdir[0]), float(xdir[1]), float(xdir[2])))
    else:
        system = gp_Ax3(gp_Pnt(*[float(c) for c in origin]), direction)
    trsf = gp_Trsf()
    trsf.SetTransformation(system)
    return BRepBuilderAPI_Transform(shape, trsf, True).Shape()


def _translate_2d(shape: TopoDS_Shape, dx: float, dy: float) -> TopoDS_Shape:
    trsf = gp_Trsf()
    trsf.SetTranslation(gp_Vec(dx, dy, 0.0))
    return BRepBuilderAPI_Transform(shape, trsf, True).Shape()


@dataclass
class Loop:
    """One closed curve of a flat profile: the outline, or a hole in it."""

    key: str = OUTER_KEY
    wire: Optional[TopoDS_Wire] = None
    outer: bool = False
    area: float = 0.0
    width: float = 0.0          # the narrower side of its bounding box
    bbox: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)

    @property
    def label(self) -> str:
        if self.outer:
            return "Outline"
        if self.width > 0 and abs(self.bbox[2] - self.bbox[0]
                                  - (self.bbox[3] - self.bbox[1])) < 1e-6:
            return "Hole %.1f mm" % self.width
        return "Opening %.1f x %.1f mm" % (self.bbox[2] - self.bbox[0],
                                           self.bbox[3] - self.bbox[1])

    @property
    def default_side(self) -> str:
        return "outside" if self.outer else "inside"


@dataclass
class Profile:
    """A part flattened onto the XY plane, centred on its bounding box."""

    loops: List[Loop] = field(default_factory=list)
    thickness: float = 0.0
    width: float = 0.0
    height: float = 0.0

    @property
    def outer(self) -> Optional[Loop]:
        return next((loop for loop in self.loops if loop.outer), None)

    @property
    def holes(self) -> List[Loop]:
        return [loop for loop in self.loops if not loop.outer]

    def loop(self, key: str) -> Optional[Loop]:
        return next((loop for loop in self.loops if loop.key == key), None)


def _wire_bbox(wire: TopoDS_Wire) -> Tuple[float, float, float, float]:
    xmin, ymin, _zmin, xmax, ymax, _zmax = kernel.bounding_box(wire)
    return (xmin, ymin, xmax, ymax)


def _wire_area(wire: TopoDS_Wire) -> float:
    """Area enclosed by a planar wire, or its bounding box if it will not face."""
    try:
        maker = BRepBuilderAPI_MakeFace(TopoDS.Wire_s(wire), True)
        if maker.IsDone():
            return abs(kernel.face_area(maker.Face()))
    except Exception:
        pass
    x0, y0, x1, y1 = _wire_bbox(wire)
    return abs(x1 - x0) * abs(y1 - y0)


def _hole_key(bbox: Tuple[float, float, float, float]) -> str:
    """A key that survives a rebuild, because it is where the hole *is*.

    Keying on position rather than index is what lets a cut side the user
    chose stay attached to the right hole after the part is edited.
    """
    cx = (bbox[0] + bbox[2]) * 0.5
    cy = (bbox[1] + bbox[3]) * 0.5
    return "hole@%.2f,%.2f" % (cx, cy)


def flatten(shape: TopoDS_Shape, face: TopoDS_Face,
            thickness: float = 0.0) -> Profile:
    """Bring one planar face into the XY plane and read off its loops.

    The result is centred on its own bounding box, so a placement is simply
    "where does the middle of this part go", and rotating it does not make
    it wander.
    """
    plane = _plane_of(face)
    if plane is None:
        raise KernelError("that face is not flat, so it cannot be a cut face")
    origin, normal = plane

    flat = _to_xy(face, origin, normal)
    flat_face = TopoDS.Face_s(flat)

    try:
        outer_wire = BRepTools.OuterWire_s(flat_face)
    except Exception:
        outer_wire = None
    wires = [TopoDS.Wire_s(w)
             for w in kernel.explore(flat_face, TopAbs_WIRE)]
    if outer_wire is None:
        if not wires:
            raise KernelError("that face has no closed curves to cut")
        outer_wire = max(wires, key=_wire_area)

    x0, y0, x1, y1 = _wire_bbox(outer_wire)
    dx, dy = -(x0 + x1) * 0.5, -(y0 + y1) * 0.5

    loops: List[Loop] = []
    for wire in wires:
        moved = TopoDS.Wire_s(_translate_2d(wire, dx, dy))
        bbox = _wire_bbox(moved)
        is_outer = wire.IsSame(outer_wire)
        loops.append(Loop(
            key=OUTER_KEY if is_outer else _hole_key(bbox),
            wire=moved, outer=is_outer, area=_wire_area(moved),
            width=min(bbox[2] - bbox[0], bbox[3] - bbox[1]), bbox=bbox))

    # holes in a stable order, biggest first, so the browser reads the same
    # way every time and duplicate keys cannot shuffle
    loops.sort(key=lambda loop: (not loop.outer, -loop.area, loop.key))
    _deduplicate(loops)

    return Profile(loops=loops, thickness=thickness,
                   width=x1 - x0, height=y1 - y0)


def _deduplicate(loops: List[Loop]) -> None:
    """Two holes at the same place would share a key; number them apart."""
    seen: Dict[str, int] = {}
    for loop in loops:
        if loop.key in seen:
            seen[loop.key] += 1
            loop.key = "%s#%d" % (loop.key, seen[loop.key])
        else:
            seen[loop.key] = 1


def place(wire: TopoDS_Wire, position: Sequence[float], rotation: float,
          mirror: bool = False) -> TopoDS_Wire:
    """Put a profile loop where it sits on the sheet.

    Mirror first, then rotate, then translate - so a mirrored part still
    rotates the way the handle is dragged rather than backwards.
    """
    shape: TopoDS_Shape = wire
    if mirror:
        flip = gp_Trsf()
        flip.SetMirror(gp_Ax3(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1),
                              gp_Dir(0, 1, 0)).Ax2())
        shape = BRepBuilderAPI_Transform(shape, flip, True).Shape()
    if abs(rotation) > 1e-12:
        turn = gp_Trsf()
        turn.SetRotation(gp_Ax3(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)).Axis(),
                         math.radians(rotation))
        shape = BRepBuilderAPI_Transform(shape, turn, True).Shape()
    shape = _translate_2d(shape, float(position[0]), float(position[1]))
    return TopoDS.Wire_s(shape)


def placed_bbox(profile: Profile, position: Sequence[float], rotation: float,
                mirror: bool = False) -> Tuple[float, float, float, float]:
    """Where the part's outline lands on the sheet."""
    outer = profile.outer
    if outer is None or outer.wire is None:
        return (0.0, 0.0, 0.0, 0.0)
    return _wire_bbox(place(outer.wire, position, rotation, mirror))
