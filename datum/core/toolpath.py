"""Cutter compensation: profiles in, toolpath centrelines out.

MyPlasm cannot offset by the radii this workshop's cutters need, so the
geometry we export is already the path the tool centre follows.  Outer
profiles grow by the tool radius, holes and slots shrink by it.

The offset itself is OCCT's, not a polygon offsetter's, so a 300 mm arc
comes out the other side as a 300 mm arc rather than two hundred chords.
The direction is worked out by *measuring* rather than by trusting a sign:
a wire's orientation depends on how the face that owns it was built, so the
same signed offset grows one part and shrinks the next.  Both directions are
computed and the one that actually changed the enclosed area the right way
is kept.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace
from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffset
from OCP.BRepTools import BRepTools_WireExplorer
from OCP.GeomAbs import GeomAbs_JoinType
from OCP.TopAbs import TopAbs_REVERSED, TopAbs_WIRE
from OCP.TopoDS import TopoDS, TopoDS_Edge, TopoDS_Shape, TopoDS_Wire
from OCP.gp import gp_Pnt

from . import kernel, sheet
from .sheet import OUTER_KEY, Loop, Profile

OUTSIDE = "outside"
INSIDE = "inside"
SIDES = (OUTSIDE, INSIDE)
SIDE_LABELS = {OUTSIDE: "Outside", INSIDE: "Inside"}

# an offset result this small has collapsed; the tool does not fit
COLLAPSE_AREA = 1e-6
# how much two placed parts may overlap before it is called an overlap
OVERLAP_AREA = 1e-3


@dataclass
class Tool:
    """What is doing the cutting."""

    diameter: float = 6.0
    plunge: bool = True
    name: str = ""

    @property
    def radius(self) -> float:
        return self.diameter * 0.5

    def label(self) -> str:
        return self.name or "%.3g mm cutter" % self.diameter


@dataclass
class Sheet:
    """The stock it is being cut from."""

    width: float = 1500.0
    height: float = 3000.0
    thickness: float = 3.0
    margin: float = 15.0        # kept clear at the edges for clamps

    def inner(self) -> Tuple[float, float, float, float]:
        return (self.margin, self.margin,
                self.width - self.margin, self.height - self.margin)


@dataclass
class Job:
    """One placed part, as the toolpath generator needs to see it."""

    part_id: int = 0
    name: str = ""
    profile: Optional[Profile] = None
    position: Tuple[float, float] = (0.0, 0.0)
    rotation: float = 0.0
    mirror: bool = False
    sides: Dict[str, str] = field(default_factory=dict)

    def side_for(self, loop: Loop) -> str:
        return self.sides.get(loop.key, loop.default_side)


@dataclass
class Pass:
    """One closed cut, in the order it should be made."""

    part_id: int = 0
    part_name: str = ""
    key: str = OUTER_KEY
    label: str = ""
    side: str = OUTSIDE
    outer: bool = False
    wire: Optional[TopoDS_Wire] = None
    lead_in: Optional[TopoDS_Edge] = None
    lead_out: Optional[TopoDS_Edge] = None
    order: int = 0
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and self.wire is not None

    def edges(self) -> List[TopoDS_Edge]:
        """Everything this pass draws, leads included, in cutting order."""
        out: List[TopoDS_Edge] = []
        if self.lead_in is not None:
            out.append(self.lead_in)
        if self.wire is not None:
            out.extend(_ordered_edges(self.wire))
        if self.lead_out is not None:
            out.append(self.lead_out)
        return out

    def summary(self) -> str:
        if self.error:
            return self.error
        return "%s, cut %s" % (self.label or self.key,
                               SIDE_LABELS.get(self.side, self.side).lower())


@dataclass
class ToolpathReport:
    passes: List[Pass] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and all(p.ok for p in self.passes)

    @property
    def cuts(self) -> List[Pass]:
        return [p for p in self.passes if p.ok]

    @property
    def message(self) -> str:
        if self.errors:
            return "%d problem(s) - %s" % (len(self.errors), self.errors[0])
        if self.warnings:
            return self.warnings[0]
        if not self.passes:
            return "Nothing to cut yet - add a part to the sheet"
        return "%d cut(s) ready" % len(self.cuts)


# --------------------------------------------------------------------------
# wire helpers
# --------------------------------------------------------------------------


def _wires_of(shape: TopoDS_Shape) -> List[TopoDS_Wire]:
    if shape is None or shape.IsNull():
        return []
    if shape.ShapeType() == TopAbs_WIRE:
        return [TopoDS.Wire_s(shape)]
    return [TopoDS.Wire_s(w) for w in kernel.explore(shape, TopAbs_WIRE)]


def _area(wire: TopoDS_Wire) -> float:
    try:
        maker = BRepBuilderAPI_MakeFace(wire, True)
        if maker.IsDone():
            return abs(kernel.face_area(maker.Face()))
    except Exception:
        pass
    xmin, ymin, _z0, xmax, ymax, _z1 = kernel.bounding_box(wire)
    return abs(xmax - xmin) * abs(ymax - ymin)


def _face_of(wire: TopoDS_Wire):
    try:
        maker = BRepBuilderAPI_MakeFace(wire, True)
        return maker.Face() if maker.IsDone() else None
    except Exception:
        return None


def _ordered_edges(wire: TopoDS_Wire) -> List[TopoDS_Edge]:
    """The wire's edges in the order it is traversed, not topology order."""
    out: List[TopoDS_Edge] = []
    try:
        explorer = BRepTools_WireExplorer(wire)
        while explorer.More():
            out.append(explorer.Current())
            explorer.Next()
    except Exception:
        return [TopoDS.Edge_s(e) for e in kernel.edges(wire)]
    return out or [TopoDS.Edge_s(e) for e in kernel.edges(wire)]


def _start_of(wire: TopoDS_Wire
              ) -> Optional[Tuple[Tuple[float, float], Tuple[float, float]]]:
    """Where a pass begins, and which way it sets off."""
    edges = _ordered_edges(wire)
    if not edges:
        return None
    edge = edges[0]
    try:
        curve = BRepAdaptor_Curve(edge)
    except Exception:
        return None
    reversed_edge = edge.Orientation() == TopAbs_REVERSED
    parameter = (curve.LastParameter() if reversed_edge
                 else curve.FirstParameter())
    point = gp_Pnt()
    tangent = kernel.vec((0.0, 0.0, 0.0))
    try:
        curve.D1(parameter, point, tangent)
    except Exception:
        return None
    direction = (tangent.X(), tangent.Y())
    if reversed_edge:
        direction = (-direction[0], -direction[1])
    length = math.hypot(direction[0], direction[1])
    if length < 1e-12:
        return None
    return ((point.X(), point.Y()),
            (direction[0] / length, direction[1] / length))


def _segment(a: Sequence[float], b: Sequence[float]) -> Optional[TopoDS_Edge]:
    if math.dist(a, b) < 1e-9:
        return None
    try:
        maker = BRepBuilderAPI_MakeEdge(gp_Pnt(a[0], a[1], 0.0),
                                        gp_Pnt(b[0], b[1], 0.0))
        return maker.Edge() if maker.IsDone() else None
    except Exception:
        return None


# --------------------------------------------------------------------------
# offsetting
# --------------------------------------------------------------------------


def offset_wire(wire: TopoDS_Wire, distance: float, outward: bool
                ) -> Tuple[Optional[TopoDS_Wire], str]:
    """Offset a closed planar wire by ``distance``, outward or inward.

    Returns the offset wire and an empty message, or ``None`` and the reason
    it could not be done.
    """
    if distance <= 1e-9:
        return wire, ""

    original = _area(wire)
    best: Optional[Tuple[float, TopoDS_Wire, float]] = None

    for value in (distance, -distance):
        try:
            maker = BRepOffsetAPI_MakeOffset()
            maker.Init(GeomAbs_JoinType.GeomAbs_Arc)
            maker.AddWire(wire)
            maker.Perform(value)
            if not maker.IsDone():
                continue
            result = maker.Shape()
        except Exception:
            continue

        candidates = _wires_of(result)
        if not candidates:
            continue
        # a concave inward offset can split into several loops; the biggest
        # is the one that still describes the opening
        candidate = max(candidates, key=_area)
        area = _area(candidate)
        grew = area > original
        if grew != outward:
            continue
        if best is None or (area > best[2]) == outward:
            best = (value, candidate, area)

    if best is None:
        return (None,
                "the tool does not fit - offsetting %s by %.3g mm leaves "
                "nothing to cut" % ("outward" if outward else "inward",
                                    distance))
    if best[2] < COLLAPSE_AREA:
        return (None, "this opening closes up completely at %.3g mm offset"
                      % distance)
    return (best[1], "")


def lead_edges(wire: TopoDS_Wire, length: float
               ) -> Tuple[Optional[TopoDS_Edge], Optional[TopoDS_Edge]]:
    """Tangential lead in and lead out at the start of a closed pass."""
    if length <= 1e-9:
        return (None, None)
    start = _start_of(wire)
    if start is None:
        return (None, None)
    (px, py), (tx, ty) = start
    before = (px - tx * length, py - ty * length)
    after = (px + tx * length, py + ty * length)
    return (_segment(before, (px, py)), _segment((px, py), after))


# --------------------------------------------------------------------------
# generation
# --------------------------------------------------------------------------


def generate(jobs: Sequence[Job], tool: Tool, stock: Sheet,
             lead_length: float = 0.0) -> ToolpathReport:
    """Turn placed profiles into an ordered, checked list of cuts."""
    report = ToolpathReport()
    order = 0

    for job in jobs:
        if job.profile is None:
            continue
        _check_thickness(job, stock, report)

        # holes before the outline, or the part drops out of the sheet with
        # its holes still uncut
        loops = sorted(job.profile.loops, key=lambda l: (l.outer, -l.area))
        for loop in loops:
            if loop.wire is None:
                continue
            order += 1
            side = job.side_for(loop)
            cut = Pass(part_id=job.part_id, part_name=job.name, key=loop.key,
                       label=loop.label, side=side, outer=loop.outer,
                       order=order)

            placed = sheet.place(loop.wire, job.position, job.rotation,
                                 job.mirror)
            # the epsilon is for the bounding box's own gap, not slop: a
            # 6 mm hole measures 6.0000002 and a 6 mm cutter still will not
            # go in, and saying which tool is too big beats a generic
            # "offset failed" from the kernel
            if side == INSIDE and loop.width <= tool.diameter + 1e-3:
                cut.error = ("%.2f mm across is narrower than the %.3g mm "
                             "tool" % (loop.width, tool.diameter))
                report.passes.append(cut)
                continue

            offset, problem = offset_wire(placed, tool.radius,
                                          outward=(side == OUTSIDE))
            if offset is None:
                cut.error = problem
                report.passes.append(cut)
                continue

            cut.wire = offset
            cut.lead_in, cut.lead_out = lead_edges(offset, lead_length)
            report.passes.append(cut)

    _check_sheet(report, stock)
    _check_overlaps(report)

    for cut in report.passes:
        if cut.error:
            report.errors.append("%s: %s" % (cut.part_name or cut.label,
                                             cut.error))
    return report


def _check_thickness(job: Job, stock: Sheet, report: ToolpathReport) -> None:
    thickness = job.profile.thickness if job.profile else 0.0
    if thickness <= 0 or stock.thickness <= 0:
        return
    if abs(thickness - stock.thickness) > max(0.05, stock.thickness * 0.02):
        report.warnings.append(
            "%s is %.2f mm thick but the sheet is %.2f mm - check the stock"
            % (job.name, thickness, stock.thickness))


def _check_sheet(report: ToolpathReport, stock: Sheet) -> None:
    """Nothing may run outside the clamped area, or off the stock entirely."""
    x0, y0, x1, y1 = stock.inner()
    for cut in report.passes:
        if not cut.outer or cut.wire is None or cut.error:
            continue
        bx0, by0, _z0, bx1, by1, _z1 = kernel.bounding_box(cut.wire)
        if (bx1 - bx0) > (x1 - x0) or (by1 - by0) > (y1 - y0):
            cut.error = ("%.0f x %.0f mm does not fit the %.0f x %.0f mm "
                         "sheet" % (bx1 - bx0, by1 - by0, stock.width,
                                    stock.height))
            continue
        if bx0 < x0 - 1e-6 or by0 < y0 - 1e-6 or bx1 > x1 + 1e-6 \
                or by1 > y1 + 1e-6:
            cut.error = ("this part hangs over the sheet edge, or into the "
                         "%.0f mm clamp margin" % stock.margin)


def _check_overlaps(report: ToolpathReport) -> None:
    """Two parts whose toolpaths touch would cut into each other."""
    outers = [cut for cut in report.passes
              if cut.outer and cut.wire is not None and not cut.error]
    boxes = [kernel.bounding_box(cut.wire) for cut in outers]

    for i in range(len(outers)):
        for j in range(i + 1, len(outers)):
            a, b = boxes[i], boxes[j]
            if (a[3] < b[0] or b[3] < a[0] or a[4] < b[1] or b[4] < a[1]):
                continue                      # bounding boxes miss entirely
            if not _really_overlaps(outers[i].wire, outers[j].wire):
                continue
            names = (outers[i].part_name or "a part",
                     outers[j].part_name or "another part")
            message = "%s and %s overlap once the tool offset is applied" % names
            for cut in (outers[i], outers[j]):
                if not cut.error:
                    cut.error = message


def _really_overlaps(a: TopoDS_Wire, b: TopoDS_Wire) -> bool:
    """Bounding boxes can touch when rotated parts do not; this is the truth."""
    face_a, face_b = _face_of(a), _face_of(b)
    if face_a is None or face_b is None:
        return True          # cannot prove they are clear, so say they are not
    try:
        common = BRepAlgoAPI_Common(face_a, face_b)
        common.Build()
        if not common.IsDone():
            return False
        shared = common.Shape()
    except Exception:
        return False
    if shared is None or shared.IsNull():
        return False
    return sum(kernel.face_area(f) for f in kernel.faces(shared)) > OVERLAP_AREA
