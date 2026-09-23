"""Model features: the ordered operations that build the solid.

Rebuilding walks the list in order, handing each feature a
:class:`BuildContext` that carries the body built so far.  A feature that
creates material also publishes its *tool* solid into the context, which is
what lets pattern and mirror features replay earlier operations at new
positions the way Inventor does.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from OCP.TopAbs import TopAbs_FACE
from OCP.TopoDS import TopoDS, TopoDS_Shape

from . import kernel
from .kernel import KernelError
from .naming import RefSet, ShapeRef
from .params import ExpressionError, evaluate
from .sketch import STANDARD_PLANES, Sketch, SketchPlane

EXTENTS = ("distance", "symmetric", "through_all")


class FeatureError(RuntimeError):
    """A feature could not be built."""


# ==========================================================================


# The four ways a new solid can meet the ones already there, in the order
# Inventor lists them.  Order matters: Join is what you want nearly every
# time, and New Body is the deliberate one you go looking for.
JOIN = "join"
CUT = "cut"
INTERSECT = "intersect"
NEW_BODY = "new"

OPERATIONS = (JOIN, CUT, INTERSECT, NEW_BODY)
OPERATION_LABELS = {
    JOIN: "Join",
    CUT: "Cut",
    INTERSECT: "Intersect",
    NEW_BODY: "New Body",
}
OPERATION_HINTS = {
    JOIN: "Add this material to the body",
    CUT: "Remove this material from the body",
    INTERSECT: "Keep only where this and the body overlap",
    NEW_BODY: "Leave this as a separate solid in the same part",
}


@dataclass
class Body:
    """One solid in the part.  A part with several is a multibody part."""

    name: str = "Solid1"
    shape: Optional[TopoDS_Shape] = None

    @property
    def valid(self) -> bool:
        return kernel.is_valid(self.shape)


class BuildContext:
    """State threaded through a rebuild."""

    def __init__(self, scope: Dict[str, float]) -> None:
        self.scope = scope
        self.bodies: List[Body] = []
        self.sketches: Dict[int, Sketch] = {}
        self.planes: Dict[int, SketchPlane] = dict(STANDARD_PLANES)
        # feature id -> (tool solid, operation) for pattern/mirror replay
        self.tools: Dict[int, Tuple[TopoDS_Shape, str]] = {}
        self.log: List[str] = []

    def evaluate(self, expression: Any, what: str) -> float:
        try:
            return evaluate(expression, self.scope)
        except ExpressionError as exc:
            raise FeatureError("%s: %s" % (what, exc)) from exc

    # -- the bodies --------------------------------------------------------

    @property
    def live(self) -> List[Body]:
        return [b for b in self.bodies if b.valid]

    @property
    def shape(self) -> Optional[TopoDS_Shape]:
        """Everything in the part as one shape.

        A single body is returned as itself rather than wrapped, so the
        common case never pays for a compound and nothing downstream has to
        learn about multibody parts to keep working.
        """
        live = self.live
        if not live:
            return None
        if len(live) == 1:
            return live[0].shape
        return kernel.compound([b.shape for b in live])

    @shape.setter
    def shape(self, value: Optional[TopoDS_Shape]) -> None:
        """Replace the part's geometry wholesale, keeping one body."""
        if value is None:
            self.bodies = []
        elif self.bodies:
            self.bodies = [Body(self.bodies[0].name, value)]
        else:
            self.bodies = [Body(self.next_name(), value)]

    def next_name(self) -> str:
        taken = {b.name for b in self.bodies}
        i = 1
        while "Solid%d" % i in taken:
            i += 1
        return "Solid%d" % i

    def body(self, name: str) -> Optional[Body]:
        return next((b for b in self.bodies if b.name == name), None)

    def require_shape(self, what: str) -> TopoDS_Shape:
        if not self.live:
            raise FeatureError("%s needs an existing body" % what)
        return self.shape

    def require_body(self, what: str,
                     near: Optional[TopoDS_Shape] = None) -> Body:
        """The body a modifying feature should work on.

        With one body there is no question.  With several, the one that
        actually contains the geometry being modified is the right answer,
        which is why ``near`` is offered.
        """
        live = self.live
        if not live:
            raise FeatureError("%s needs an existing body" % what)
        if len(live) == 1 or near is None:
            return live[0]
        return self.owner_of(near) or live[0]

    def owner_of(self, sub: TopoDS_Shape) -> Optional[Body]:
        """Which body a picked face or edge belongs to."""
        for body in self.live:
            for kind in (kernel.faces, kernel.edges):
                try:
                    if any(candidate.IsSame(sub) for candidate in kind(body.shape)):
                        return body
                except Exception:
                    continue
        return None

    def apply(self, tool: TopoDS_Shape, op: str, feature_id: int,
              target: str = "", name: str = "") -> None:
        """Combine a tool solid into the part according to ``op``.

        The very first solid in an empty part becomes a body whatever the
        operation says: there is nothing to join to, cut from or intersect
        with, so asking the question would be theatre.
        """
        self.tools[feature_id] = (tool, op)

        if not self.live:
            self.bodies = [Body(name or self.next_name(), tool)]
            return

        if op == NEW_BODY:
            self.bodies.append(Body(name or self.next_name(), tool))
            return

        body = self.body(target) if target else None
        if body is None:
            body = self._touching(tool) or self.live[0]
        body.shape = kernel.unify(kernel.boolean(body.shape, tool, op))

    def _touching(self, tool: TopoDS_Shape) -> Optional[Body]:
        """The body this tool actually meets, when there is more than one.

        Without it a cut aimed at the second solid would silently be taken
        out of the first and come back looking like it did nothing.
        """
        live = self.live
        if len(live) == 1:
            return live[0]
        for body in live:
            try:
                if _overlaps(body.shape, tool):
                    return body
            except Exception:
                continue
        return None


def _overlaps(a: TopoDS_Shape, b: TopoDS_Shape) -> bool:
    """Whether two solids share any volume, bounding boxes checked first."""
    ax0, ay0, az0, ax1, ay1, az1 = kernel.bounding_box(a)
    bx0, by0, bz0, bx1, by1, bz1 = kernel.bounding_box(b)
    if (ax1 < bx0 or bx1 < ax0 or ay1 < by0 or by1 < ay0
            or az1 < bz0 or bz1 < az0):
        return False
    try:
        return kernel.volume(kernel.boolean(a, b, "intersect")) > 1e-9
    except Exception:
        return True


# ==========================================================================


@dataclass
class Feature:
    """Base class.  Subclasses implement :meth:`build`."""

    id: int = 0
    name: str = "Feature"
    suppressed: bool = False
    error: str = ""
    type_name: str = "feature"
    icon: str = "feature"

    # -- interface ----------------------------------------------------------

    def build(self, ctx: BuildContext) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def output_summary(self) -> str:
        """How this feature's solid meets the part, in the dialog's words."""
        label = OPERATION_LABELS.get(getattr(self, "operation", JOIN),
                                     getattr(self, "operation", JOIN))
        name = getattr(self, "body_name", "")
        if name:
            return "%s, body %s" % (label, name)
        return label

    def summary(self) -> str:
        """One-line description shown in the browser tooltip."""
        return self.type_name

    def depends_on(self) -> List[int]:
        """Feature ids this feature consumes."""
        return []

    # -- serialisation ------------------------------------------------------

    def field_dict(self) -> Dict[str, Any]:
        return {}

    def load_fields(self, data: Dict[str, Any]) -> None:
        pass

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "id": self.id,
            "name": self.name,
            "type": self.type_name,
            "suppressed": self.suppressed,
        }
        d.update(self.field_dict())
        return d

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Feature":
        cls = FEATURE_TYPES.get(data.get("type", ""))
        if cls is None:
            raise FeatureError("unknown feature type %r" % data.get("type"))
        feat = cls()
        feat.id = int(data.get("id", 0))
        feat.name = data.get("name", cls.type_name)
        feat.suppressed = bool(data.get("suppressed", False))
        feat.load_fields(data)
        return feat


# ==========================================================================
# sketch + work geometry
# ==========================================================================


@dataclass
class SketchFeature(Feature):
    type_name: str = "sketch"
    icon: str = "sketch"
    name: str = "Sketch"
    sketch: Sketch = field(default_factory=Sketch)
    # when the sketch was placed on a model face, this lets the plane follow it
    face_ref: Optional[ShapeRef] = None
    # a consumed sketch is nested under the feature that used it; sharing it
    # also keeps it visible at the top of the tree for reuse
    shared: bool = False

    def build(self, ctx: BuildContext) -> None:
        if self.face_ref is not None and ctx.shape is not None:
            face = self.face_ref.rebind(ctx.shape)
            if face is not None:
                derived = plane_from_face(TopoDS.Face_s(face))
                if derived is not None:
                    derived.name = self.sketch.plane.name
                    self.sketch.plane = derived
        self.sketch.solve(ctx.scope)
        ctx.sketches[self.id] = self.sketch

    def summary(self) -> str:
        return "%s on %s - %d entities, %s" % (
            self.name, self.sketch.plane.name, len(self.sketch.entities),
            self.sketch.solve_message or "unsolved")

    def field_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"sketch": self.sketch.to_dict(),
                             "shared": self.shared}
        if self.face_ref is not None:
            d["face_ref"] = self.face_ref.to_dict()
        return d

    def load_fields(self, data: Dict[str, Any]) -> None:
        self.sketch = Sketch.from_dict(data.get("sketch", {}))
        self.shared = bool(data.get("shared", False))
        if data.get("face_ref"):
            self.face_ref = ShapeRef.from_dict(data["face_ref"])


@dataclass
class WorkPlaneFeature(Feature):
    """A construction plane offset from a datum plane or a flat model face."""

    type_name: str = "workplane"
    icon: str = "plane"
    name: str = "Work Plane"
    base: str = "XY"                     # datum / work plane name
    face_ref: Optional[ShapeRef] = None  # set instead, when built off a face
    offset: str = "10"
    angle: str = "0"
    flip: bool = False

    @property
    def base_label(self) -> str:
        return "model face" if self.face_ref is not None else self.base

    def base_plane(self, ctx: BuildContext) -> SketchPlane:
        if self.face_ref is not None:
            if ctx.shape is None:
                raise FeatureError("%s: needs a body to sit on" % self.name)
            face = self.face_ref.rebind(ctx.shape)
            if face is None:
                raise FeatureError("%s: its face no longer exists" % self.name)
            if not self.face_ref.resolved:
                # A fallback match keeps the tree building rather than taking
                # every downstream sketch down with it - but a plane in the
                # wrong place is easy to miss, so say so loudly.
                ctx.log.append("%s: could not find its original face, so it "
                               "fell back to another one - check the plane"
                               % self.name)
            plane = plane_from_face(TopoDS.Face_s(face))
            if plane is None:
                raise FeatureError("%s: that face is not flat" % self.name)
            return plane
        return ctx.planes.get(self.base) or STANDARD_PLANES["XY"]

    def build(self, ctx: BuildContext) -> None:
        base = self.base_plane(ctx)
        d = ctx.evaluate(self.offset, "%s offset" % self.name)
        ang = ctx.evaluate(self.angle, "%s angle" % self.name)
        if self.flip:
            d = -d

        n = base.normal
        origin = tuple(o + n[i] * d for i, o in enumerate(base.origin))
        plane = SketchPlane(origin, base.normal, base.xdir, self.name)
        if abs(ang) > 1e-9:
            plane = _rotate_plane(plane, base.xdir, ang)
        ctx.planes[self.name] = plane

    def summary(self) -> str:
        return "%s: %s offset %s" % (self.name, self.base_label, self.offset)

    def field_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"base": self.base, "offset": self.offset,
                             "angle": self.angle, "flip": self.flip}
        if self.face_ref is not None:
            d["face_ref"] = self.face_ref.to_dict()
        return d

    def load_fields(self, data: Dict[str, Any]) -> None:
        self.base = data.get("base", "XY")
        self.offset = str(data.get("offset", "10"))
        self.angle = str(data.get("angle", "0"))
        self.flip = bool(data.get("flip", False))
        if data.get("face_ref"):
            self.face_ref = ShapeRef.from_dict(data["face_ref"])


def _rotate_plane(plane: SketchPlane, axis: Sequence[float],
                  angle_deg: float) -> SketchPlane:
    a = math.radians(angle_deg)
    ax = SketchPlane._norm(axis)
    ca, sa = math.cos(a), math.sin(a)

    def rot(v: Sequence[float]) -> Tuple[float, float, float]:
        dot = sum(x * y for x, y in zip(v, ax))
        cross = (ax[1] * v[2] - ax[2] * v[1],
                 ax[2] * v[0] - ax[0] * v[2],
                 ax[0] * v[1] - ax[1] * v[0])
        return tuple(v[i] * ca + cross[i] * sa + ax[i] * dot * (1 - ca)
                     for i in range(3))

    return SketchPlane(plane.origin, rot(plane.normal), rot(plane.xdir),
                       plane.name)


def plane_from_face(face) -> Optional[SketchPlane]:
    """Derive a sketch plane from a planar model face."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_SurfaceType

    try:
        surf = BRepAdaptor_Surface(face)
        if surf.GetType() != GeomAbs_SurfaceType.GeomAbs_Plane:
            return None
        pln = surf.Plane()
        ax = pln.Position()
        loc = ax.Location()
        n = ax.Direction()
        x = ax.XDirection()
        return SketchPlane((loc.X(), loc.Y(), loc.Z()),
                           (n.X(), n.Y(), n.Z()),
                           (x.X(), x.Y(), x.Z()),
                           "Face")
    except Exception:
        return None


# ==========================================================================
# material-creating features
# ==========================================================================


@dataclass
class ExtrudeFeature(Feature):
    type_name: str = "extrude"
    icon: str = "extrude"
    name: str = "Extrude"
    sketch_id: int = 0
    profiles: "ProfileSelection" = field(
        default_factory=lambda: ProfileSelection())
    distance: str = "10"
    extent: str = "distance"
    operation: str = JOIN
    body_name: str = ""
    reversed: bool = False
    taper: str = "0"

    def depends_on(self) -> List[int]:
        ids = self.profiles.sketch_ids()
        return ids or ([self.sketch_id] if self.sketch_id else [])

    def build(self, ctx: BuildContext) -> None:
        profile, plane = resolve_profile(ctx, self.profiles, self.name,
                                        self.sketch_id)
        normal = plane.normal
        taper = ctx.evaluate(self.taper, "%s taper" % self.name)

        if self.extent == "through_all":
            # sweep from well behind the body to well in front of it, so the
            # cut clears the solid no matter which side the sketch sits on
            reach = _through_all_reach(ctx.shape, plane)
            profile = kernel.translate(
                profile, [-normal[i] * reach for i in range(3)])
            tool = kernel.extrude(profile, normal, 2 * reach, 0.0)
        else:
            d = ctx.evaluate(self.distance, "%s distance" % self.name)
            if d <= 0:
                raise FeatureError("%s: distance must be positive" % self.name)
            sign = -1.0 if self.reversed else 1.0
            if self.extent == "symmetric":
                profile = kernel.translate(
                    profile, [-normal[i] * d / 2.0 for i in range(3)])
                tool = kernel.extrude(profile, normal, d, taper)
            else:
                tool = kernel.extrude(profile, normal, d * sign, taper)

        ctx.apply(tool, self.operation, self.id, name=self.body_name)

    def summary(self) -> str:
        if self.extent == "through_all":
            what = "through all"
        elif self.extent == "symmetric":
            what = "%s symmetric" % self.distance
        else:
            what = self.distance
        profiles = ("%d profile(s)" % len(self.profiles) if self.profiles
                    else "no profile selected")
        return "%s: %s, %s, %s" % (self.name, what, profiles, self.output_summary())

    def field_dict(self) -> Dict[str, Any]:
        return {
            "sketch_id": self.sketch_id,
            "profiles": self.profiles.to_list(),
            "distance": self.distance,
            "extent": self.extent,
            "operation": self.operation, "body_name": self.body_name,
            "reversed": self.reversed,
            "taper": self.taper,
        }

    def load_fields(self, data: Dict[str, Any]) -> None:
        self.sketch_id = int(data.get("sketch_id", 0))
        self.profiles = ProfileSelection.from_list(data.get("profiles"))
        self.distance = str(data.get("distance", "10"))
        self.extent = data.get("extent", "distance")
        self.operation = data.get("operation", JOIN)
        self.body_name = str(data.get("body_name", ""))
        self.reversed = bool(data.get("reversed", False))
        self.taper = str(data.get("taper", "0"))


def _through_all_reach(shape: Optional[TopoDS_Shape], plane: SketchPlane) -> float:
    """A distance guaranteed to clear the whole body in either direction."""
    if shape is None:
        return 100.0
    xmin, ymin, zmin, xmax, ymax, zmax = kernel.bounding_box(shape)
    corners = [(x, y, z) for x in (xmin, xmax) for y in (ymin, ymax)
               for z in (zmin, zmax)]
    reach = max(abs(plane.distance_to(c)) for c in corners)
    return max(reach * 1.5 + 10.0, 10.0)


@dataclass
class ProfileSelection:
    """The closed regions a feature consumes, wherever they came from.

    A profile is identified by its sketch and by where its centre sits in
    that sketch's coordinates, so editing the sketch keeps the selection
    pointing at the same loop even as entity ids and face ordering change.
    Nothing is selected by default: a feature has to be told which regions
    it uses, rather than quietly swallowing a whole sketch.
    """

    items: List[Tuple[int, float, float]] = field(default_factory=list)
    tolerance: float = 2.0

    def __bool__(self) -> bool:
        return bool(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self):
        return iter(self.items)

    def add(self, sketch_id: int, centre: Tuple[float, float]) -> None:
        if not self.contains(sketch_id, centre):
            self.items.append((int(sketch_id), float(centre[0]),
                               float(centre[1])))

    def remove(self, sketch_id: int, centre: Tuple[float, float]) -> bool:
        for entry in list(self.items):
            if entry[0] == sketch_id and math.dist(centre,
                                                   entry[1:]) < self.tolerance:
                self.items.remove(entry)
                return True
        return False

    def toggle(self, sketch_id: int, centre: Tuple[float, float]) -> None:
        if not self.remove(sketch_id, centre):
            self.add(sketch_id, centre)

    def contains(self, sketch_id: int, centre: Tuple[float, float]) -> bool:
        return any(entry[0] == sketch_id
                   and math.dist(centre, entry[1:]) < self.tolerance
                   for entry in self.items)

    def sketch_ids(self) -> List[int]:
        seen: List[int] = []
        for sketch_id, _u, _v in self.items:
            if sketch_id not in seen:
                seen.append(sketch_id)
        return seen

    def to_list(self) -> List[List[float]]:
        return [[e[0], e[1], e[2]] for e in self.items]

    @classmethod
    def from_list(cls, data) -> "ProfileSelection":
        out = cls()
        for entry in (data or []):
            if len(entry) >= 3:
                out.items.append((int(entry[0]), float(entry[1]),
                                  float(entry[2])))
        return out


def collect_regions(ctx: BuildContext) -> List[Dict[str, Any]]:
    """Every closed region in every sketch built so far."""
    found: List[Dict[str, Any]] = []
    for sketch_id, sketch in ctx.sketches.items():
        try:
            regions = kernel.sketch_regions(sketch)
        except Exception:
            continue
        for region in regions:
            entry = dict(region)
            entry["sketch_id"] = sketch_id
            entry["sketch"] = sketch
            found.append(entry)
    return found


def resolve_profile(ctx: BuildContext, profiles: ProfileSelection,
                    name: str, sketch_id: int = 0
                    ) -> Tuple[TopoDS_Shape, SketchPlane]:
    """The shape a feature works on, plus the plane it was drawn on.

    ``sketch_id`` is the older whole-sketch form: files written before
    profiles existed, and scripts that build a feature without picking
    regions, fall back to using every region of that sketch.  The editor
    never sets it - a feature created in the UI starts with nothing
    selected and says so.
    """
    if not profiles:
        # The whole-sketch form keeps its original meaning - outer loops
        # minus the loops inside them - rather than the region partition,
        # so a plate drawn with a hole still extrudes with the hole.
        sketch = ctx.sketches.get(sketch_id) if sketch_id else None
        if sketch is not None:
            try:
                return kernel.sketch_profile(sketch), sketch.plane
            except kernel.KernelError as exc:
                raise FeatureError("%s: %s" % (name, exc)) from exc
        raise FeatureError("%s: no profile selected - pick one or more "
                           "closed regions" % name)

    available = collect_regions(ctx)

    chosen: List[Dict[str, Any]] = []
    for wanted_sketch, u, v in profiles:
        best, best_distance = None, math.inf
        for region in available:
            if region["sketch_id"] != wanted_sketch:
                continue
            distance = math.dist((u, v), region["centre"])
            # a large region's identity point can travel a long way when a
            # parameter changes; a small one must not be mistaken for its
            # neighbour, so the allowance follows the region's own size
            allowed = max(profiles.tolerance,
                          math.sqrt(max(region["area"], 1.0)) * 0.5)
            if distance < min(best_distance, allowed):
                best, best_distance = region, distance
        if best is not None and best not in chosen:
            chosen.append(best)

    if not chosen:
        raise FeatureError(
            "%s: the profiles it used no longer exist - reselect them" % name)

    plane = chosen[0]["sketch"].plane
    faces = [region["face"] for region in chosen]
    if len(faces) == 1:
        return faces[0], plane
    # Regions that share an edge become a single face, so extruding them
    # gives one solid with one top rather than two prisms glued together
    # with a seam down the middle.  Islands that do not touch stay separate
    # inside the result, which is what lets one extrude cover several.
    merged = kernel.fuse_all(faces)
    if merged is None:
        raise FeatureError("%s: the chosen profiles produced nothing" % name)
    return kernel.unify(merged), plane


def _drill_direction(shape: Optional[TopoDS_Shape], plane: SketchPlane,
                     flip: bool) -> Tuple[float, float, float]:
    """Which way a hole should be drilled: into the material by default.

    Sketch planes can sit on either side of a body - the bottom face of a box
    has its normal pointing away from the solid - so picking the direction
    from the geometry beats picking it from the plane's normal.  ``flip``
    reverses whatever that comes out to be.
    """
    n = plane.normal
    direction = tuple(-c for c in n)

    if shape is not None:
        xmin, ymin, zmin, xmax, ymax, zmax = kernel.bounding_box(shape)
        corners = [(x, y, z) for x in (xmin, xmax) for y in (ymin, ymax)
                   for z in (zmin, zmax)]
        offsets = [plane.distance_to(c) for c in corners]
        above = sum(o for o in offsets if o > 0)
        below = -sum(o for o in offsets if o < 0)
        if above > below:
            direction = n

    return tuple(-c for c in direction) if flip else direction


@dataclass
class RevolveFeature(Feature):
    type_name: str = "revolve"
    icon: str = "revolve"
    name: str = "Revolve"
    sketch_id: int = 0
    profiles: "ProfileSelection" = field(
        default_factory=lambda: ProfileSelection())
    angle: str = "360"
    operation: str = JOIN
    body_name: str = ""
    axis: str = "X"          # X / Y of the sketch plane, or "entity"
    axis_entity: int = 0     # sketch entity id when axis == "entity"
    reversed: bool = False

    def depends_on(self) -> List[int]:
        ids = self.profiles.sketch_ids()
        return ids or ([self.sketch_id] if self.sketch_id else [])

    def build(self, ctx: BuildContext) -> None:
        profile, plane = resolve_profile(ctx, self.profiles, self.name,
                                        self.sketch_id)
        angle = ctx.evaluate(self.angle, "%s angle" % self.name)
        if self.reversed:
            angle = -angle

        # the axis is expressed in the sketch the profile came from
        ids = self.profiles.sketch_ids() or ([self.sketch_id]
                                             if self.sketch_id else [])
        sketch = ctx.sketches.get(ids[0]) if ids else None
        origin, axis_dir = (self._axis(sketch) if sketch is not None
                            else (plane.origin, plane.xdir))
        try:
            tool = kernel.revolve(profile, origin, axis_dir, angle)
        except KernelError as exc:
            raise FeatureError("%s: %s (does the profile cross the axis?)"
                               % (self.name, exc)) from exc
        ctx.apply(tool, self.operation, self.id, name=self.body_name)

    def _axis(self, sketch: Sketch) -> Tuple[Sequence[float], Sequence[float]]:
        plane = sketch.plane
        if self.axis == "entity" and self.axis_entity in sketch.entities:
            ent = sketch.entities[self.axis_entity]
            if ent.kind == "line" and len(ent.points) >= 2:
                a = sketch.points[ent.points[0]]
                b = sketch.points[ent.points[1]]
                p1 = plane.to_3d(a.x, a.y)
                p2 = plane.to_3d(b.x, b.y)
                d = tuple(p2[i] - p1[i] for i in range(3))
                if math.sqrt(sum(c * c for c in d)) > 1e-9:
                    return p1, d
        if self.axis == "Y":
            return plane.origin, plane.ydir
        return plane.origin, plane.xdir

    def summary(self) -> str:
        return "%s: %s deg about %s, %s" % (self.name, self.angle, self.axis,
                                            self.output_summary())

    def field_dict(self) -> Dict[str, Any]:
        return {
            "sketch_id": self.sketch_id,
            "profiles": self.profiles.to_list(),
            "angle": self.angle,
            "operation": self.operation, "body_name": self.body_name,
            "axis": self.axis,
            "axis_entity": self.axis_entity,
            "reversed": self.reversed,
        }

    def load_fields(self, data: Dict[str, Any]) -> None:
        self.sketch_id = int(data.get("sketch_id", 0))
        self.profiles = ProfileSelection.from_list(data.get("profiles"))
        self.angle = str(data.get("angle", "360"))
        self.operation = data.get("operation", JOIN)
        self.body_name = str(data.get("body_name", ""))
        self.axis = data.get("axis", "X")
        self.axis_entity = int(data.get("axis_entity", 0))
        self.reversed = bool(data.get("reversed", False))


@dataclass
class SweepFeature(Feature):
    """Drag a profile along a path drawn in another sketch."""

    type_name: str = "sweep"
    icon: str = "sweep"
    name: str = "Sweep"
    sketch_id: int = 0
    profiles: "ProfileSelection" = field(
        default_factory=lambda: ProfileSelection())
    path_id: int = 0
    operation: str = JOIN
    body_name: str = ""

    def depends_on(self) -> List[int]:
        ids = self.profiles.sketch_ids()
        ids = ids or ([self.sketch_id] if self.sketch_id else [])
        return ids + [self.path_id]

    def build(self, ctx: BuildContext) -> None:
        path_sketch = ctx.sketches.get(self.path_id)
        if path_sketch is None:
            raise FeatureError("%s: its path sketch is missing" % self.name)
        if self.path_id in self.profiles.sketch_ids():
            raise FeatureError("%s: the path must be a different sketch from "
                               "the profile" % self.name)

        profile, _plane = resolve_profile(ctx, self.profiles, self.name,
                                          self.sketch_id)
        try:
            path = kernel.sketch_path_wire(path_sketch)
            tool = kernel.sweep(profile, path)
        except KernelError as exc:
            raise FeatureError("%s: %s" % (self.name, exc)) from exc
        ctx.apply(tool, self.operation, self.id, name=self.body_name)

    def summary(self) -> str:
        return "%s: along a path, %s" % (self.name, self.output_summary())

    def field_dict(self) -> Dict[str, Any]:
        return {"sketch_id": self.sketch_id, "path_id": self.path_id,
                "profiles": self.profiles.to_list(),
                "operation": self.operation, "body_name": self.body_name}

    def load_fields(self, data: Dict[str, Any]) -> None:
        self.sketch_id = int(data.get("sketch_id", 0))
        self.profiles = ProfileSelection.from_list(data.get("profiles"))
        self.path_id = int(data.get("path_id", 0))
        self.operation = data.get("operation", JOIN)
        self.body_name = str(data.get("body_name", ""))


@dataclass
class LoftFeature(Feature):
    """Blend between two or more sketch profiles."""

    type_name: str = "loft"
    icon: str = "loft"
    name: str = "Loft"
    sections: List[int] = field(default_factory=list)
    operation: str = JOIN
    body_name: str = ""
    ruled: bool = False
    closed: bool = True

    def depends_on(self) -> List[int]:
        return list(self.sections)

    def build(self, ctx: BuildContext) -> None:
        profiles = []
        for sid in self.sections:
            sketch = ctx.sketches.get(sid)
            if sketch is None:
                raise FeatureError("%s: one of its sections is missing"
                                   % self.name)
            profiles.append(kernel.sketch_profile(sketch))
        if len(profiles) < 2:
            raise FeatureError("%s: pick at least two sections" % self.name)

        try:
            tool = kernel.loft(profiles, self.closed, self.ruled)
        except KernelError as exc:
            raise FeatureError("%s: %s" % (self.name, exc)) from exc
        ctx.apply(tool, self.operation, self.id, name=self.body_name)

    def summary(self) -> str:
        return "%s: %d sections, %s" % (self.name, len(self.sections),
                                        self.output_summary())

    def field_dict(self) -> Dict[str, Any]:
        return {"sections": list(self.sections), "operation": self.operation, "body_name": self.body_name,
                "ruled": self.ruled, "closed": self.closed}

    def load_fields(self, data: Dict[str, Any]) -> None:
        self.sections = [int(i) for i in data.get("sections", [])]
        self.operation = data.get("operation", JOIN)
        self.body_name = str(data.get("body_name", ""))
        self.ruled = bool(data.get("ruled", False))
        self.closed = bool(data.get("closed", True))


@dataclass
class HoleFeature(Feature):
    """Holes placed at the circle centres of a sketch."""

    type_name: str = "hole"
    icon: str = "hole"
    name: str = "Hole"
    sketch_id: int = 0
    diameter: str = "5"
    depth: str = "10"
    through: bool = True
    hole_type: str = "simple"      # simple / counterbore / countersink
    cb_diameter: str = "10"
    cb_depth: str = "4"
    cs_angle: str = "90"
    flip: bool = False

    def depends_on(self) -> List[int]:
        return [self.sketch_id]

    def build(self, ctx: BuildContext) -> None:
        body = ctx.require_shape(self.name)
        sketch = ctx.sketches.get(self.sketch_id)
        if sketch is None:
            raise FeatureError("%s: its sketch is missing or suppressed" % self.name)

        centres = [sketch.points[e.points[0]]
                   for e in sketch.entities.values()
                   if e.kind == "circle" and not e.construction]
        if not centres:
            raise FeatureError("%s: place circles in the sketch to mark hole "
                               "centres" % self.name)

        dia = ctx.evaluate(self.diameter, "%s diameter" % self.name)
        if dia <= 0:
            raise FeatureError("%s: diameter must be positive" % self.name)

        plane = sketch.plane
        drill = _drill_direction(body, plane, self.flip)
        reach = _through_all_reach(body, plane)

        if self.through:
            depth = reach * 2.0
            back = reach
        else:
            depth = ctx.evaluate(self.depth, "%s depth" % self.name)
            if depth <= 0:
                raise FeatureError("%s: depth must be positive" % self.name)
            back = 0.0

        tools = []
        for c in centres:
            base = plane.to_3d(c.x, c.y)
            # a through hole starts well clear of the body on the near side
            start = tuple(base[i] - drill[i] * back for i in range(3))
            tool = kernel.cylinder(dia / 2.0, depth, start, drill)

            # The head recess overlaps the shaft, so it has to be fused into
            # one solid - a compound of overlapping tools cuts unreliably.
            head = None
            if self.hole_type == "counterbore":
                cbd = ctx.evaluate(self.cb_diameter, "%s c'bore diameter" % self.name)
                cbz = ctx.evaluate(self.cb_depth, "%s c'bore depth" % self.name)
                if cbd > dia and cbz > 0:
                    head = kernel.cylinder(cbd / 2.0, cbz, base, drill)
            elif self.hole_type == "countersink":
                cbd = ctx.evaluate(self.cb_diameter, "%s c'sink diameter" % self.name)
                ang = ctx.evaluate(self.cs_angle, "%s c'sink angle" % self.name)
                if cbd > dia and 0 < ang < 180:
                    h = (cbd - dia) / 2.0 / math.tan(math.radians(ang / 2.0))
                    head = kernel.cone(cbd / 2.0, dia / 2.0, max(h, 1e-3),
                                       base, drill)
            if head is not None:
                tool = kernel.boolean(tool, head, "join")
            tools.append(tool)

        # separate holes do not touch, so a compound is fine here
        combined = kernel.compound(tools) if len(tools) > 1 else tools[0]
        ctx.apply(combined, "cut", self.id)

    def summary(self) -> str:
        depth = "through" if self.through else self.depth
        return "%s: %s dia %s, %s" % (self.name, self.diameter, depth,
                                      self.hole_type)

    def field_dict(self) -> Dict[str, Any]:
        return {
            "sketch_id": self.sketch_id, "diameter": self.diameter,
            "depth": self.depth, "through": self.through,
            "hole_type": self.hole_type, "cb_diameter": self.cb_diameter,
            "cb_depth": self.cb_depth, "cs_angle": self.cs_angle,
            "flip": self.flip,
        }

    def load_fields(self, data: Dict[str, Any]) -> None:
        self.sketch_id = int(data.get("sketch_id", 0))
        self.diameter = str(data.get("diameter", "5"))
        self.depth = str(data.get("depth", "10"))
        self.through = bool(data.get("through", True))
        self.hole_type = data.get("hole_type", "simple")
        self.cb_diameter = str(data.get("cb_diameter", "10"))
        self.cb_depth = str(data.get("cb_depth", "4"))
        self.cs_angle = str(data.get("cs_angle", "90"))
        self.flip = bool(data.get("flip", False))


@dataclass
class PrimitiveFeature(Feature):
    type_name: str = "primitive"
    icon: str = "box"
    name: str = "Box"
    kind: str = "box"           # box / cylinder / sphere / cone / torus
    a: str = "40"
    b: str = "30"
    c: str = "20"
    operation: str = JOIN
    body_name: str = ""
    centred: bool = False
    origin: Tuple[str, str, str] = ("0", "0", "0")

    def build(self, ctx: BuildContext) -> None:
        a = ctx.evaluate(self.a, "%s size" % self.name)
        b = ctx.evaluate(self.b, "%s size" % self.name)
        c = ctx.evaluate(self.c, "%s size" % self.name)
        o = tuple(ctx.evaluate(v, "%s origin" % self.name) for v in self.origin)

        if self.kind == "box":
            tool = kernel.box(a, b, c, o, self.centred)
        elif self.kind == "cylinder":
            org = (o[0], o[1], o[2] - (b / 2.0 if self.centred else 0.0))
            tool = kernel.cylinder(a, b, org)
        elif self.kind == "sphere":
            tool = kernel.sphere(a, o)
        elif self.kind == "cone":
            tool = kernel.cone(a, b, c, o)
        elif self.kind == "torus":
            tool = kernel.torus(a, b, o)
        else:
            raise FeatureError("%s: unknown primitive %r" % (self.name, self.kind))

        ctx.apply(tool, self.operation, self.id, name=self.body_name)

    def summary(self) -> str:
        return "%s: %s (%s, %s, %s)" % (self.name, self.kind, self.a, self.b,
                                        self.c)

    def field_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "a": self.a, "b": self.b, "c": self.c,
                "operation": self.operation, "body_name": self.body_name, "centred": self.centred,
                "origin": list(self.origin)}

    def load_fields(self, data: Dict[str, Any]) -> None:
        self.kind = data.get("kind", "box")
        self.a = str(data.get("a", "40"))
        self.b = str(data.get("b", "30"))
        self.c = str(data.get("c", "20"))
        self.operation = data.get("operation", JOIN)
        self.body_name = str(data.get("body_name", ""))
        self.centred = bool(data.get("centred", False))
        self.origin = tuple(str(v) for v in data.get("origin", ("0", "0", "0")))


@dataclass
class ImportFeature(Feature):
    """A STEP/IGES/BREP body brought in as the base of the model."""

    type_name: str = "import"
    icon: str = "import"
    name: str = "Imported Body"
    path: str = ""
    operation: str = JOIN
    body_name: str = ""
    _cache: Optional[TopoDS_Shape] = None

    def build(self, ctx: BuildContext) -> None:
        from . import fileio

        if self._cache is None:
            if not self.path:
                raise FeatureError("%s: no file path" % self.name)
            try:
                self._cache = fileio.read_shape(self.path)
            except Exception as exc:
                raise FeatureError("%s: %s" % (self.name, exc)) from exc
        ctx.apply(kernel.copy_shape(self._cache), self.operation, self.id, name=self.body_name)

    def summary(self) -> str:
        import os
        return "%s: %s" % (self.name, os.path.basename(self.path) or "(none)")

    def field_dict(self) -> Dict[str, Any]:
        return {"path": self.path, "operation": self.operation, "body_name": self.body_name}

    def load_fields(self, data: Dict[str, Any]) -> None:
        self.path = data.get("path", "")
        self.operation = data.get("operation", JOIN)
        self.body_name = str(data.get("body_name", ""))


# ==========================================================================
# modifier features
# ==========================================================================


@dataclass
class FilletFeature(Feature):
    type_name: str = "fillet"
    icon: str = "fillet"
    name: str = "Fillet"
    radius: str = "2"
    refs: RefSet = field(default_factory=RefSet)
    all_edges: bool = False

    def build(self, ctx: BuildContext) -> None:
        body = ctx.require_shape(self.name)
        r = ctx.evaluate(self.radius, "%s radius" % self.name)

        if self.all_edges:
            targets = kernel.edges(body)
        else:
            targets, lost = self.refs.resolve_all(body)
            if lost and not targets:
                raise FeatureError("%s: its edges no longer exist" % self.name)
            if lost:
                ctx.log.append("%s: %d edge(s) could not be found"
                               % (self.name, len(lost)))
        if not targets:
            raise FeatureError("%s: no edges selected" % self.name)

        try:
            # a modifying feature belongs to whichever body owns the edges
            # it was pointed at, not to "the" body
            owner = ctx.require_body(self.name, targets[0])
            owner.shape = kernel.fillet(
                owner.shape, [TopoDS.Edge_s(e) for e in targets], r)
        except KernelError as exc:
            raise FeatureError("%s: %s" % (self.name, exc)) from exc

    def summary(self) -> str:
        n = "all edges" if self.all_edges else "%d edge(s)" % len(self.refs)
        return "%s: R%s on %s" % (self.name, self.radius, n)

    def field_dict(self) -> Dict[str, Any]:
        return {"radius": self.radius, "refs": self.refs.to_list(),
                "all_edges": self.all_edges}

    def load_fields(self, data: Dict[str, Any]) -> None:
        self.radius = str(data.get("radius", "2"))
        self.refs = RefSet.from_list(data.get("refs", []))
        self.all_edges = bool(data.get("all_edges", False))


@dataclass
class ChamferFeature(Feature):
    type_name: str = "chamfer"
    icon: str = "chamfer"
    name: str = "Chamfer"
    distance: str = "1"
    refs: RefSet = field(default_factory=RefSet)
    all_edges: bool = False

    def build(self, ctx: BuildContext) -> None:
        body = ctx.require_shape(self.name)
        d = ctx.evaluate(self.distance, "%s distance" % self.name)

        if self.all_edges:
            targets = kernel.edges(body)
        else:
            targets, lost = self.refs.resolve_all(body)
            if lost:
                ctx.log.append("%s: %d edge(s) could not be found"
                               % (self.name, len(lost)))
        if not targets:
            raise FeatureError("%s: no edges selected" % self.name)

        try:
            owner = ctx.require_body(self.name, targets[0])
            owner.shape = kernel.chamfer(
                owner.shape, [TopoDS.Edge_s(e) for e in targets], d)
        except KernelError as exc:
            raise FeatureError("%s: %s" % (self.name, exc)) from exc

    def summary(self) -> str:
        n = "all edges" if self.all_edges else "%d edge(s)" % len(self.refs)
        return "%s: %s on %s" % (self.name, self.distance, n)

    def field_dict(self) -> Dict[str, Any]:
        return {"distance": self.distance, "refs": self.refs.to_list(),
                "all_edges": self.all_edges}

    def load_fields(self, data: Dict[str, Any]) -> None:
        self.distance = str(data.get("distance", "1"))
        self.refs = RefSet.from_list(data.get("refs", []))
        self.all_edges = bool(data.get("all_edges", False))


@dataclass
class ShellFeature(Feature):
    type_name: str = "shell"
    icon: str = "shell"
    name: str = "Shell"
    thickness: str = "2"
    refs: RefSet = field(default_factory=RefSet)

    def build(self, ctx: BuildContext) -> None:
        body = ctx.require_shape(self.name)
        t = ctx.evaluate(self.thickness, "%s thickness" % self.name)
        targets, lost = self.refs.resolve_all(body)
        if lost:
            ctx.log.append("%s: %d face(s) could not be found"
                           % (self.name, len(lost)))
        try:
            owner = ctx.require_body(self.name,
                                     targets[0] if targets else None)
            owner.shape = kernel.shell(
                owner.shape, [TopoDS.Face_s(f) for f in targets], t)
        except KernelError as exc:
            raise FeatureError("%s: %s" % (self.name, exc)) from exc

    def summary(self) -> str:
        return "%s: %s wall, %d open face(s)" % (self.name, self.thickness,
                                                 len(self.refs))

    def field_dict(self) -> Dict[str, Any]:
        return {"thickness": self.thickness, "refs": self.refs.to_list()}

    def load_fields(self, data: Dict[str, Any]) -> None:
        self.thickness = str(data.get("thickness", "2"))
        self.refs = RefSet.from_list(data.get("refs", []))


@dataclass
class MirrorFeature(Feature):
    type_name: str = "mirror"
    icon: str = "mirror"
    name: str = "Mirror"
    plane: str = "XY"
    scope: str = "body"          # body / features
    parents: List[int] = field(default_factory=list)

    def depends_on(self) -> List[int]:
        return list(self.parents) if self.scope == "features" else []

    def build(self, ctx: BuildContext) -> None:
        body = ctx.require_shape(self.name)
        plane = ctx.planes.get(self.plane) or STANDARD_PLANES["XY"]

        if self.scope == "body":
            ctx.shape = kernel.boolean(body, kernel.mirror(body, plane), "join")
            return

        applied = False
        for fid in self.parents:
            entry = ctx.tools.get(fid)
            if entry is None:
                continue
            tool, op = entry
            ctx.shape = kernel.boolean(ctx.shape, kernel.mirror(tool, plane),
                                       op if op != "new" else "join")
            applied = True
        if not applied:
            raise FeatureError("%s: no source features to mirror" % self.name)

    def summary(self) -> str:
        return "%s: about %s (%s)" % (self.name, self.plane, self.scope)

    def field_dict(self) -> Dict[str, Any]:
        return {"plane": self.plane, "scope": self.scope,
                "parents": list(self.parents)}

    def load_fields(self, data: Dict[str, Any]) -> None:
        self.plane = data.get("plane", "XY")
        self.scope = data.get("scope", "body")
        self.parents = [int(i) for i in data.get("parents", [])]


@dataclass
class PatternFeature(Feature):
    """Rectangular or circular pattern of earlier features."""

    type_name: str = "pattern"
    icon: str = "pattern"
    name: str = "Pattern"
    mode: str = "rectangular"       # rectangular / circular
    parents: List[int] = field(default_factory=list)
    count1: str = "3"
    spacing1: str = "20"
    dir1: str = "X"
    count2: str = "1"
    spacing2: str = "20"
    dir2: str = "Y"
    axis: str = "Z"
    angle: str = "360"
    full_circle: bool = True

    def depends_on(self) -> List[int]:
        return list(self.parents)

    def build(self, ctx: BuildContext) -> None:
        ctx.require_shape(self.name)
        sources = [(fid, ctx.tools[fid]) for fid in self.parents if fid in ctx.tools]
        if not sources:
            raise FeatureError("%s: select one or more features to pattern"
                               % self.name)

        placements = (self._rect_placements(ctx) if self.mode == "rectangular"
                      else self._circ_placements(ctx))
        if len(placements) <= 1:
            return

        for transform in placements[1:]:
            for _fid, (tool, op) in sources:
                moved = transform(tool)
                ctx.shape = kernel.boolean(ctx.shape, moved,
                                           op if op != "new" else "join")

    _AXES = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}

    def _rect_placements(self, ctx: BuildContext) -> List[Callable]:
        n1 = max(1, int(round(ctx.evaluate(self.count1, "%s count" % self.name))))
        n2 = max(1, int(round(ctx.evaluate(self.count2, "%s count" % self.name))))
        s1 = ctx.evaluate(self.spacing1, "%s spacing" % self.name)
        s2 = ctx.evaluate(self.spacing2, "%s spacing" % self.name)
        d1 = self._AXES.get(self.dir1, (1.0, 0.0, 0.0))
        d2 = self._AXES.get(self.dir2, (0.0, 1.0, 0.0))

        out = []
        for i in range(n1):
            for j in range(n2):
                dx = tuple(d1[k] * s1 * i + d2[k] * s2 * j for k in range(3))
                out.append(lambda shape, dx=dx: kernel.translate(shape, dx))
        return out

    def _circ_placements(self, ctx: BuildContext) -> List[Callable]:
        n = max(1, int(round(ctx.evaluate(self.count1, "%s count" % self.name))))
        total = 360.0 if self.full_circle else ctx.evaluate(
            self.angle, "%s angle" % self.name)
        axis = self._AXES.get(self.axis, (0.0, 0.0, 1.0))
        step = total / n if self.full_circle else (total / max(1, n - 1))

        out = []
        for i in range(n):
            ang = step * i
            out.append(lambda shape, a=ang: kernel.rotate(shape, (0, 0, 0),
                                                          axis, a))
        return out

    def summary(self) -> str:
        if self.mode == "rectangular":
            return "%s: %s x %s rectangular" % (self.name, self.count1, self.count2)
        return "%s: %s circular about %s" % (self.name, self.count1, self.axis)

    def field_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode, "parents": list(self.parents),
            "count1": self.count1, "spacing1": self.spacing1, "dir1": self.dir1,
            "count2": self.count2, "spacing2": self.spacing2, "dir2": self.dir2,
            "axis": self.axis, "angle": self.angle,
            "full_circle": self.full_circle,
        }

    def load_fields(self, data: Dict[str, Any]) -> None:
        self.mode = data.get("mode", "rectangular")
        self.parents = [int(i) for i in data.get("parents", [])]
        self.count1 = str(data.get("count1", "3"))
        self.spacing1 = str(data.get("spacing1", "20"))
        self.dir1 = data.get("dir1", "X")
        self.count2 = str(data.get("count2", "1"))
        self.spacing2 = str(data.get("spacing2", "20"))
        self.dir2 = data.get("dir2", "Y")
        self.axis = data.get("axis", "Z")
        self.angle = str(data.get("angle", "360"))
        self.full_circle = bool(data.get("full_circle", True))


@dataclass
class MoveFeature(Feature):
    """Translate / rotate the whole body."""

    type_name: str = "move"
    icon: str = "move"
    name: str = "Move Body"
    dx: str = "0"
    dy: str = "0"
    dz: str = "0"
    rx: str = "0"
    ry: str = "0"
    rz: str = "0"

    def build(self, ctx: BuildContext) -> None:
        body = ctx.require_shape(self.name)
        d = [ctx.evaluate(v, self.name) for v in (self.dx, self.dy, self.dz)]
        r = [ctx.evaluate(v, self.name) for v in (self.rx, self.ry, self.rz)]
        shape = body
        for i, axis in enumerate(((1, 0, 0), (0, 1, 0), (0, 0, 1))):
            if abs(r[i]) > 1e-9:
                shape = kernel.rotate(shape, (0, 0, 0), axis, r[i])
        if any(abs(v) > 1e-9 for v in d):
            shape = kernel.translate(shape, d)
        ctx.shape = shape

    def summary(self) -> str:
        return "%s: (%s, %s, %s)" % (self.name, self.dx, self.dy, self.dz)

    def field_dict(self) -> Dict[str, Any]:
        return {"dx": self.dx, "dy": self.dy, "dz": self.dz,
                "rx": self.rx, "ry": self.ry, "rz": self.rz}

    def load_fields(self, data: Dict[str, Any]) -> None:
        for k in ("dx", "dy", "dz", "rx", "ry", "rz"):
            setattr(self, k, str(data.get(k, "0")))


FEATURE_TYPES: Dict[str, type] = {
    cls.type_name: cls for cls in (
        SketchFeature, WorkPlaneFeature, ExtrudeFeature, RevolveFeature,
        SweepFeature, LoftFeature, HoleFeature, PrimitiveFeature,
        ImportFeature, FilletFeature, ChamferFeature, ShellFeature,
        MirrorFeature, PatternFeature, MoveFeature,
    )
}
