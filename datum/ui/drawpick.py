"""What on a drawing view can be picked to dimension.

A view is drawn as polylines, one per edge of the model as it was seen,
already flattened onto the paper.  To dimension it the way Inventor does,
those have to be known again for what they were: a straight edge is a
line, with two ends that are points; a circle or an arc is a centre and a
radius; anything else, a spline seen side on, is at least its two ends.
That is worked out once per drawn view and kept with it.

Everything here is in the view's own millimetres, the paper space its
lines are in, centred on the view.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

Point = Tuple[float, float]

POINT = "point"
LINE = "line"
CIRCLE = "circle"


@dataclass(frozen=True)
class Item:
    """One pickable thing on one view."""

    kind: str
    view: int
    a: Point                    # the point; a line's start; a circle's centre
    b: Point = (0.0, 0.0)       # a line's end
    radius: float = 0.0
    closed: bool = False        # a whole circle rather than an arc

    def same(self, other: Optional["Item"]) -> bool:
        if other is None or other.kind != self.kind or other.view != self.view:
            return False
        return (math.dist(self.a, other.a) < 1e-6
                and math.dist(self.b, other.b) < 1e-6
                and abs(self.radius - other.radius) < 1e-6)


def _circle_through(a: Point, b: Point, c: Point):
    ax, ay = a
    bx, by = b
    cx, cy = c
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return None
    a2, b2, c2 = ax * ax + ay * ay, bx * bx + by * by, cx * cx + cy * cy
    ux = (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / d
    uy = (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / d
    return (ux, uy), math.dist((ux, uy), a)


def classify(points: Sequence[Point]):
    """("line", a, b), ("circle", centre, radius, closed) or ("curve", a, b)."""
    pts = [(float(p[0]), float(p[1])) for p in points]
    first, last = pts[0], pts[-1]
    if len(pts) == 2:
        return (LINE, first, last)
    chord = math.dist(first, last)
    if chord > 1e-9:
        dx, dy = (last[0] - first[0]) / chord, (last[1] - first[1]) / chord
        off = max(abs((p[0] - first[0]) * dy - (p[1] - first[1]) * dx)
                  for p in pts)
        if off <= max(1e-4, chord * 1e-4):
            return (LINE, first, last)
    n = len(pts)
    closed = chord < 1e-6
    picks = ((pts[0], pts[n // 3], pts[(2 * n) // 3]) if closed
             else (pts[0], pts[n // 2], pts[-1]))
    fitted = _circle_through(*picks)
    if fitted is not None:
        centre, radius = fitted
        if radius > 1e-6 and all(abs(math.dist(p, centre) - radius)
                                 <= max(1e-3, radius * 3e-3) for p in pts):
            return (CIRCLE, centre, radius, closed)
    return ("curve", first, last)


_CACHE: Dict[int, Tuple[object, List[Item]]] = {}


def items(view) -> List[Item]:
    """Everything on a drawn view that can be picked, worked out once."""
    projection = getattr(view, "projection", None)
    if projection is None:
        return []
    held = _CACHE.get(id(projection))
    if held is not None and held[0] is projection:
        return held[1]
    out: List[Item] = []
    points: List[Point] = []

    def known(item: Item) -> bool:
        # a hidden edge right under a visible one, the bottom of a hole
        # under its top, is the same thing to pick, and offered once
        for other in out:
            if other.kind != item.kind:
                continue
            if item.kind == LINE and (
                    (math.dist(item.a, other.a) < 1e-6
                     and math.dist(item.b, other.b) < 1e-6)
                    or (math.dist(item.a, other.b) < 1e-6
                        and math.dist(item.b, other.a) < 1e-6)):
                return True
            if item.kind == CIRCLE and math.dist(item.a, other.a) < 1e-6                     and abs(item.radius - other.radius) < 1e-6                     and item.closed == other.closed:
                return True
        return False

    def add_point(p: Point) -> None:
        for q in points:
            if math.dist(p, q) < 1e-6:
                return
        points.append(p)

    for line in projection.lines:
        if len(line.points) < 2:
            continue
        found = classify(line.points)
        if found[0] == LINE:
            item = Item(LINE, view.id, found[1], found[2])
            if not known(item):
                out.append(item)
            add_point(found[1])
            add_point(found[2])
        elif found[0] == CIRCLE:
            _kind, centre, radius, closed = found
            item = Item(CIRCLE, view.id, centre, radius=radius,
                        closed=closed)
            if not known(item):
                out.append(item)
            add_point(centre)
            if not closed:
                add_point(tuple(line.points[0][:2]))
                add_point(tuple(line.points[-1][:2]))
        else:
            add_point(found[1])
            add_point(found[2])
    out.extend(Item(POINT, view.id, p) for p in points)
    if len(_CACHE) > 64:
        _CACHE.clear()
    _CACHE[id(projection)] = (projection, out)
    return out


def _to_segment(p: Point, a: Point, b: Point) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = dx * dx + dy * dy
    if length < 1e-18:
        return math.dist(p, a)
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length))
    return math.dist(p, (a[0] + dx * t, a[1] + dy * t))


def nearest(sheet, point: Point, tolerance: float,
            only_view: Optional[int] = None,
            kinds: Sequence[str] = (POINT, LINE, CIRCLE)) -> Optional[Item]:
    """What is under a point on the sheet, a point before a line or circle.

    ``point`` is in sheet millimetres; the item comes back in its view's
    own.  A point within reach beats any line, the way a corner is what
    is meant when the cursor is on one.
    """
    best_point, best_point_d = None, tolerance
    best_other, best_other_d = None, tolerance
    for view in reversed(sheet.views):
        if only_view is not None and view.id != only_view:
            continue
        projection = view.projection
        if projection is None or not projection.box:
            continue
        local = (point[0] - view.x, point[1] - view.y)
        box = projection.box
        if not (box[0] - tolerance <= local[0] <= box[2] + tolerance
                and box[1] - tolerance <= local[1] <= box[3] + tolerance):
            continue
        for item in items(view):
            if item.kind not in kinds:
                continue
            if item.kind == POINT:
                d = math.dist(local, item.a)
                if d < best_point_d:
                    best_point, best_point_d = item, d
            elif item.kind == LINE:
                d = _to_segment(local, item.a, item.b)
                if d < best_other_d:
                    best_other, best_other_d = item, d
            else:
                d = abs(math.dist(local, item.a) - item.radius)
                if d < best_other_d:
                    best_other, best_other_d = item, d
    return best_point or best_other


def foot(point: Point, a: Point, b: Point) -> Point:
    """Where a point meets a line square to it, the line run on forever."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = dx * dx + dy * dy
    if length < 1e-18:
        return a
    t = ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / length
    return (a[0] + dx * t, a[1] + dy * t)


def parallel(first: Item, second: Item, degrees: float = 0.5) -> bool:
    ux, uy = first.b[0] - first.a[0], first.b[1] - first.a[1]
    vx, vy = second.b[0] - second.a[0], second.b[1] - second.a[1]
    lu, lv = math.hypot(ux, uy), math.hypot(vx, vy)
    if lu < 1e-12 or lv < 1e-12:
        return False
    return abs(ux * vy - uy * vx) / (lu * lv) <= math.sin(math.radians(degrees))


def axis_of(item: Item) -> str:
    """"x" for a line across the page, "y" for one up it, else ""."""
    dx, dy = item.b[0] - item.a[0], item.b[1] - item.a[1]
    length = math.hypot(dx, dy)
    if length < 1e-12:
        return ""
    if abs(dy) / length < 1e-6:
        return "x"
    if abs(dx) / length < 1e-6:
        return "y"
    return ""
