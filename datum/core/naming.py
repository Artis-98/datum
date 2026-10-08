"""Persistent references to faces and edges across rebuilds.

Storing a raw index into the topology is what makes history-based modellers
brittle: insert a feature earlier in the tree and every downstream fillet
grabs the wrong edge.  A reference here keeps a *geometric fingerprint* -
where the sub-shape sits, how big it is, what kind of surface or curve it is -
and after a rebuild it re-binds to the best-scoring candidate.  The stored
index is only a tie-breaker of last resort.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.GeomAbs import GeomAbs_CurveType, GeomAbs_SurfaceType
from OCP.TopoDS import TopoDS, TopoDS_Shape

from . import kernel

# how far a candidate may have moved and still be considered the same entity
POSITION_TOLERANCE = 5.0
# how far a vertex may have shifted inside the part's own box and still
# be the same vertex, as a fraction of the box
VERTEX_TOLERANCE = 0.16
SIZE_TOLERANCE = 0.35  # relative


def _curve_kind(edge) -> str:
    try:
        return str(BRepAdaptor_Curve(edge).GetType())
    except Exception:
        return "?"


def _surface_kind(face) -> str:
    try:
        return str(BRepAdaptor_Surface(face).GetType())
    except Exception:
        return "?"


def fingerprint(shape: TopoDS_Shape, kind: str, index: int) -> Dict[str, Any]:
    """Describe a sub-shape well enough to find it again after a rebuild."""
    centre = kernel.shape_centre(shape)
    if kind == "edge":
        size = kernel.edge_length(TopoDS.Edge_s(shape))
        geom = _curve_kind(TopoDS.Edge_s(shape))
    elif kind == "face":
        size = kernel.face_area(TopoDS.Face_s(shape))
        geom = _surface_kind(TopoDS.Face_s(shape))
    else:
        size = 0.0
        geom = "vertex"
    return {
        "kind": kind,
        "index": index,
        "centre": [round(c, 6) for c in centre],
        "size": round(size, 6),
        "geom": geom,
    }


def _within_box(point, shape: TopoDS_Shape):
    """Where a point sits inside a shape's box, as 0..1 along each axis.

    This is what makes a corner recognisable across an edit.  A box's far
    corner is at (1, 0, 0) whether the box is sixty millimetres long or two
    hundred and fifty; its absolute position is not, and that is why
    matching on absolute position breaks the moment a part is resized.
    """
    if isinstance(shape, tuple):
        # already measured: resolving asks this once per candidate, and
        # measuring the whole part once per candidate was most of the cost
        x0, y0, z0, x1, y1, z1 = shape
    else:
        try:
            x0, y0, z0, x1, y1, z1 = kernel.bounding_box(shape)
        except Exception:
            return None
    size = (x1 - x0, y1 - y0, z1 - z0)
    out = []
    for i in range(3):
        extent = size[i]
        out.append((point[i] - (x0, y0, z0)[i]) / extent
                   if extent > 1e-9 else 0.5)
    return tuple(out)


def _span(shape: TopoDS_Shape) -> float:
    """How big a shape is overall, as the diagonal of its box."""
    try:
        x0, y0, z0, x1, y1, z1 = kernel.bounding_box(shape)
    except Exception:
        return POSITION_TOLERANCE
    return max(POSITION_TOLERANCE,
               math.sqrt((x1 - x0) ** 2 + (y1 - y0) ** 2 + (z1 - z0) ** 2))


@dataclass
class ShapeRef:
    """A rebindable reference to one face or edge of the model."""

    kind: str = "edge"
    index: int = 0
    centre: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    size: float = 0.0
    geom: str = "?"
    # where it sat inside the model's box, 0..1 per axis.  Only vertices
    # use it, and only when it was captured with the model to hand.
    local: Optional[Tuple[float, float, float]] = None
    resolved: bool = True
    # set for the duration of a resolve, never stored
    _parent_box: Any = None

    @classmethod
    def capture(cls, shape: TopoDS_Shape, kind: str, index: int,
                within: Optional[TopoDS_Shape] = None) -> "ShapeRef":
        fp = fingerprint(shape, kind, index)
        local = (_within_box(fp["centre"], within)
                 if within is not None else None)
        return cls(kind=kind, index=index, centre=tuple(fp["centre"]),
                   size=fp["size"], geom=fp["geom"], local=local)

    def same_as(self, other: "ShapeRef") -> bool:
        """Whether two references are to the one face or edge.

        Where it is centred is not enough on its own: the inner and outer
        rims of a washer share a centre, and taking them for one edge made
        the second pick quietly undo the first.  How big it is, and what
        kind of curve or surface, tells them apart.
        """
        return (self.kind == other.kind
                and math.dist(self.centre, other.centre) < 1e-6
                and abs(self.size - other.size)
                <= 1e-6 * max(1.0, abs(self.size))
                and (self.geom == other.geom or "?" in (self.geom, other.geom)))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "index": self.index,
            "centre": list(self.centre),
            "size": self.size,
            "geom": self.geom,
            "local": list(self.local) if self.local else None,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ShapeRef":
        return cls(
            kind=d.get("kind", "edge"),
            index=int(d.get("index", 0)),
            centre=tuple(d.get("centre", (0, 0, 0))),
            local=(tuple(d["local"]) if d.get("local") else None),
            size=float(d.get("size", 0.0)),
            geom=d.get("geom", "?"),
        )

    # -- matching -----------------------------------------------------------

    def _score(self, candidate: TopoDS_Shape, cand_index: int,
               centre=None) -> float:
        """Lower is better.  ``inf`` means "definitely not this one"."""
        if centre is None:
            centre = kernel.shape_centre(candidate)
        dist = math.dist(centre, self.centre)

        if self.kind == "vertex":
            # A vertex has no length, no area and no curve type - where it
            # is, is all it is.  Absolute position will not do: make a part
            # twice as long and its far corner has moved the whole way, and
            # it is still that corner.  So it is judged on where it sits
            # inside the part, which does not change with size, and only
            # falls back to raw distance for a reference captured before
            # this was recorded.
            if self.local is not None and self._parent_box is not None:
                here = _within_box(centre, self._parent_box)
                if here is None:
                    return dist
                offset = math.dist(here, self.local)
                return offset - (0.05 if cand_index == self.index else 0.0)
            return dist - (2.0 if cand_index == self.index else 0.0)

        if dist > POSITION_TOLERANCE:
            return math.inf

        if self.kind == "edge":
            size = kernel.edge_length(TopoDS.Edge_s(candidate))
            geom = _curve_kind(TopoDS.Edge_s(candidate))
        elif self.kind == "vertex":
            # a vertex has no size or shape of its own, so where it is has
            # to carry the whole judgement
            size, geom = 0.0, "vertex"
        else:
            size = kernel.face_area(TopoDS.Face_s(candidate))
            geom = _surface_kind(TopoDS.Face_s(candidate))

        ref = max(self.size, 1e-6)
        size_err = abs(size - self.size) / ref
        if size_err > SIZE_TOLERANCE and dist > 1e-6:
            return math.inf

        score = dist * 10.0 + size_err * 4.0
        if geom != self.geom:
            score += 6.0
        if cand_index == self.index:
            score -= 0.5  # tie-break towards the original position
        return score

    def _pool(self, shape: TopoDS_Shape):
        shared = getattr(self, "_shared", None)
        if shared is not None:
            return shared.pool(shape, self.kind)[0]
        if self.kind == "edge":
            return kernel.edges(shape)
        if self.kind == "vertex":
            return kernel.vertices(shape)
        return kernel.faces(shape)

    def _centre_of(self, shape: TopoDS_Shape, index: int, candidate):
        shared = getattr(self, "_shared", None)
        if shared is not None:
            return shared.centre(shape, self.kind, index)
        return kernel.shape_centre(candidate)

    def resolve(self, shape: TopoDS_Shape) -> Optional[TopoDS_Shape]:
        """Find this reference's sub-shape in ``shape`` after a rebuild."""
        # _score is called per candidate and needs the whole shape to work
        # out where each one sits inside it; handing it over here keeps the
        # signature everything else uses unchanged
        try:
            self._parent_box = tuple(kernel.bounding_box(shape))
        except Exception:
            self._parent_box = None
        pool = self._pool(shape)
        if not pool:
            self.resolved = False
            return None

        best, best_score = None, math.inf
        for i, cand in enumerate(pool):
            s = self._score(cand, i, self._centre_of(shape, i, cand))
            if s < best_score:
                best, best_score = cand, s

        if best is not None and self.kind == "vertex":
            # The nearest vertex always exists, so nearness alone would
            # accept any shape at all - point a drawing at a different part
            # and the dimension would go on reading a confident wrong
            # number.  A corner that is nowhere near the same place *within*
            # the part is not that corner.
            limit = (VERTEX_TOLERANCE if self.local is not None
                     and self._parent_box is not None else _span(shape))
            if best_score > limit:
                self.resolved = False
                return None

        if best is None:
            # last resort: the raw index, if it still exists
            if 0 <= self.index < len(pool):
                self.resolved = False
                return pool[self.index]
            self.resolved = False
            return None

        self.resolved = True
        return best

    def rebind(self, shape: TopoDS_Shape) -> Optional[TopoDS_Shape]:
        """Resolve, then refresh the fingerprint from what we actually found."""
        found = self.resolve(shape)
        if found is not None and self.resolved:
            pool = self._pool(shape)
            for i, cand in enumerate(pool):
                if cand.IsSame(found):
                    fp = fingerprint(found, self.kind, i)
                    self.index = i
                    self.centre = tuple(fp["centre"])
                    self.size = fp["size"]
                    self.geom = fp["geom"]
                    break
        return found


class _Shared:
    """Sub-shapes and their centres, worked out once for many references.

    Lives for one resolve_all and no longer, so there is no question of
    it going stale: the shape it describes cannot change while it exists.
    """

    def __init__(self) -> None:
        self._pools: Dict[Tuple[int, str], Tuple[Any, list, list]] = {}

    def pool(self, shape: TopoDS_Shape, kind: str):
        key = (id(shape), kind)
        entry = self._pools.get(key)
        if entry is None or entry[0] is not shape:
            items = (kernel.edges(shape) if kind == "edge"
                     else kernel.vertices(shape) if kind == "vertex"
                     else kernel.faces(shape))
            entry = (shape, items, [None] * len(items))
            self._pools[key] = entry
        return entry[1], entry[2]

    def centre(self, shape: TopoDS_Shape, kind: str, index: int):
        items, centres = self.pool(shape, kind)
        if centres[index] is None:
            centres[index] = kernel.shape_centre(items[index])
        return centres[index]


class RefSet:
    """An ordered collection of :class:`ShapeRef`, as used by fillets etc."""

    def __init__(self, refs: Optional[Sequence[ShapeRef]] = None) -> None:
        self.refs: List[ShapeRef] = list(refs or [])

    def __len__(self) -> int:
        return len(self.refs)

    def __iter__(self):
        return iter(self.refs)

    def add(self, ref: ShapeRef) -> None:
        for existing in self.refs:
            if existing.same_as(ref):
                return
        self.refs.append(ref)

    def capture_from(self, shape: TopoDS_Shape, kind: str,
                     picked: Sequence[TopoDS_Shape]) -> None:
        """Record references to ``picked`` sub-shapes, relative to ``shape``.

        The picked sub-shapes usually belong to ``shape``, but not always: a
        feature dialog previews its own result, so the user's next click lands
        on a body that is one operation ahead of the one the reference has to
        be stored against.  When the identity lookup misses, the fingerprint
        is taken from the picked sub-shape itself and only the index is
        recovered from the pool.
        """
        pool = kernel.edges(shape) if kind == "edge" else kernel.faces(shape)

        for target in picked:
            index = next((i for i, cand in enumerate(pool)
                          if cand.IsSame(target)), None)
            if index is not None:
                self.add(ShapeRef.capture(pool[index], kind, index))
                continue

            ref = ShapeRef.capture(target, kind, 0)
            found = ref.resolve(shape)
            if found is not None:
                ref.index = next((i for i, cand in enumerate(pool)
                                  if cand.IsSame(found)), 0)
            self.add(ref)

    def resolve_all(self, shape: TopoDS_Shape) -> Tuple[List[TopoDS_Shape], List[ShapeRef]]:
        """Returns (found sub-shapes, references that could not be rebound)."""
        found: List[TopoDS_Shape] = []
        lost: List[ShapeRef] = []
        # every reference looks through the same edges or faces of the same
        # shape, so they are listed and measured once for all of them
        shared = _Shared()
        for ref in self.refs:
            ref._shared = shared
            try:
                sub = ref.rebind(shape)
            finally:
                ref._shared = None
            if sub is None:
                lost.append(ref)
            else:
                if not any(sub.IsSame(f) for f in found):
                    found.append(sub)
                if not ref.resolved:
                    lost.append(ref)
        return found, lost

    def to_list(self) -> List[Dict[str, Any]]:
        return [r.to_dict() for r in self.refs]

    @classmethod
    def from_list(cls, data: Sequence[Dict[str, Any]]) -> "RefSet":
        return cls([ShapeRef.from_dict(d) for d in data or []])
