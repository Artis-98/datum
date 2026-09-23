"""Minimal DXF writer, enough to hand a flat face to a laser or router.

OpenCASCADE has no DXF exporter in these bindings, so this writes AutoCAD
R12 ASCII directly: LINE, CIRCLE, ARC and LWPOLYLINE are all a 2D profile
needs, and R12 is the dialect every CAM package still reads.

A face is flattened onto its own plane first, so the DXF comes out in true
size with the face's own origin at 0,0 - which is what you want when the
face was never parallel to a world plane.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.GCPnts import GCPnts_QuasiUniformDeflection
from OCP.GeomAbs import GeomAbs_CurveType
from OCP.TopAbs import TopAbs_EDGE, TopAbs_REVERSED
from OCP.TopoDS import TopoDS, TopoDS_Face, TopoDS_Shape

from . import kernel
from .kernel import KernelError
from .sketch import SketchPlane

DEFLECTION = 0.01          # mm, how finely splines are approximated


def _pair(code: int, value: Any) -> str:
    return "%d\n%s\n" % (code, value)


class DxfDocument:
    """Accumulates R12 entities, then renders the whole file."""

    def __init__(self, units: str = "mm") -> None:
        self.entities: List[str] = []
        self.units = units
        self.min = [1e18, 1e18]
        self.max = [-1e18, -1e18]
        # every layer written has to be declared, or strict readers reject
        # the file rather than inventing the layer
        self.layers: List[str] = ["0"]

    def _layer(self, name: str) -> str:
        if name not in self.layers:
            self.layers.append(name)
        return name

    # -- bookkeeping --------------------------------------------------------

    def _touch(self, x: float, y: float) -> None:
        self.min[0] = min(self.min[0], x)
        self.min[1] = min(self.min[1], y)
        self.max[0] = max(self.max[0], x)
        self.max[1] = max(self.max[1], y)

    # -- entities -----------------------------------------------------------

    def line(self, a: Sequence[float], b: Sequence[float],
             layer: str = "0") -> None:
        self._touch(*a)
        self._touch(*b)
        self.entities.append(
            _pair(0, "LINE") + _pair(8, self._layer(layer))
            + _pair(10, "%.6f" % a[0]) + _pair(20, "%.6f" % a[1])
            + _pair(30, "0.0")
            + _pair(11, "%.6f" % b[0]) + _pair(21, "%.6f" % b[1])
            + _pair(31, "0.0"))

    def text(self, at: Sequence[float], height: float, content: str,
             layer: str = "0", align: str = "left") -> None:
        """An R12 TEXT entity, placed by its baseline.

        Written as one entity per line: R12 has no multi-line text, and a
        reader that meets a newline inside a TEXT string does something
        different in every application.
        """
        content = (content or "").replace(chr(13), "")
        if not content:
            return
        justify = {"left": 0, "centre": 1, "center": 1, "right": 2}.get(
            align, 0)
        for index, piece in enumerate(content.split(chr(10))):
            if not piece:
                continue
            y = at[1] - index * height * 1.6
            self._touch(at[0], y)
            self._touch(at[0] + height * 0.7 * len(piece), y + height)
            body = (_pair(0, "TEXT") + _pair(8, self._layer(layer))
                    + _pair(10, "%.6f" % at[0]) + _pair(20, "%.6f" % y)
                    + _pair(30, "0.0") + _pair(40, "%.6f" % height)
                    + _pair(1, piece))
            if justify:
                # a justified TEXT needs its alignment point as well, or the
                # code 72 is quietly ignored
                body += (_pair(72, justify)
                         + _pair(11, "%.6f" % at[0]) + _pair(21, "%.6f" % y)
                         + _pair(31, "0.0"))
            self.entities.append(body)

    def circle(self, centre: Sequence[float], radius: float,
               layer: str = "0") -> None:
        self._touch(centre[0] - radius, centre[1] - radius)
        self._touch(centre[0] + radius, centre[1] + radius)
        self.entities.append(
            _pair(0, "CIRCLE") + _pair(8, self._layer(layer))
            + _pair(10, "%.6f" % centre[0]) + _pair(20, "%.6f" % centre[1])
            + _pair(30, "0.0") + _pair(40, "%.6f" % radius))

    def arc(self, centre: Sequence[float], radius: float,
            start_deg: float, end_deg: float, layer: str = "0") -> None:
        self._touch(centre[0] - radius, centre[1] - radius)
        self._touch(centre[0] + radius, centre[1] + radius)
        self.entities.append(
            _pair(0, "ARC") + _pair(8, self._layer(layer))
            + _pair(10, "%.6f" % centre[0]) + _pair(20, "%.6f" % centre[1])
            + _pair(30, "0.0") + _pair(40, "%.6f" % radius)
            + _pair(50, "%.6f" % (start_deg % 360.0))
            + _pair(51, "%.6f" % (end_deg % 360.0)))

    def polyline(self, points: Sequence[Sequence[float]], closed: bool = False,
                 layer: str = "0") -> None:
        points = [p for p in points]
        if len(points) < 2:
            return
        for p in points:
            self._touch(p[0], p[1])
        body = (_pair(0, "LWPOLYLINE") + _pair(8, self._layer(layer))
                + _pair(100, "AcDbEntity") + _pair(100, "AcDbPolyline")
                + _pair(90, len(points)) + _pair(70, 1 if closed else 0))
        for p in points:
            body += _pair(10, "%.6f" % p[0]) + _pair(20, "%.6f" % p[1])
        self.entities.append(body)

    # -- output -------------------------------------------------------------

    def render(self) -> str:
        if self.min[0] > self.max[0]:
            self.min, self.max = [0.0, 0.0], [0.0, 0.0]

        header = (
            _pair(0, "SECTION") + _pair(2, "HEADER")
            + _pair(9, "$ACADVER") + _pair(1, "AC1009")
            + _pair(9, "$INSUNITS") + _pair(70, 4)          # 4 = millimetres
            + _pair(9, "$EXTMIN") + _pair(10, "%.6f" % self.min[0])
            + _pair(20, "%.6f" % self.min[1]) + _pair(30, "0.0")
            + _pair(9, "$EXTMAX") + _pair(10, "%.6f" % self.max[0])
            + _pair(20, "%.6f" % self.max[1]) + _pair(30, "0.0")
            + _pair(0, "ENDSEC"))

        colours = [7, 1, 3, 5, 2, 4, 6]
        entries = "".join(
            _pair(0, "LAYER") + _pair(2, name) + _pair(70, 0)
            + _pair(62, colours[i % len(colours)]) + _pair(6, "CONTINUOUS")
            for i, name in enumerate(self.layers))
        tables = (
            _pair(0, "SECTION") + _pair(2, "TABLES")
            + _pair(0, "TABLE") + _pair(2, "LAYER")
            + _pair(70, len(self.layers)) + entries
            + _pair(0, "ENDTAB") + _pair(0, "ENDSEC"))

        body = (_pair(0, "SECTION") + _pair(2, "ENTITIES")
                + "".join(self.entities) + _pair(0, "ENDSEC"))

        return header + tables + body + _pair(0, "EOF")

    def save(self, path: str) -> str:
        if not path.lower().endswith(".dxf"):
            path += ".dxf"
        with open(path, "w", encoding="ascii", errors="replace",
                  newline="\r\n") as handle:
            handle.write(self.render())
        return path


# --------------------------------------------------------------------------
# faces
# --------------------------------------------------------------------------


def plane_of_face(face: TopoDS_Face) -> SketchPlane:
    """The face's own coordinate system, used to flatten it."""
    from .features import plane_from_face

    plane = plane_from_face(face)
    if plane is None:
        raise KernelError("that face is not flat, so it cannot go to DXF")
    # put the origin at the middle of the face, so the DXF is centred
    centre = kernel.shape_centre(face)
    u, v = plane.to_2d(centre)
    origin = plane.to_3d(u, v)
    return SketchPlane(origin, plane.normal, plane.xdir, "Face")


def face_to_dxf(face: TopoDS_Face, path: str,
                plane: Optional[SketchPlane] = None) -> str:
    """Flatten one planar face onto its own plane and write it out."""
    plane = plane or plane_of_face(face)
    document = DxfDocument()

    for edge in kernel.edges(face):
        _write_edge(document, TopoDS.Edge_s(edge), plane)

    if not document.entities:
        raise KernelError("that face produced no curves to export")
    return document.save(path)


def _write_edge(document: DxfDocument, edge, plane: SketchPlane,
                layer: str = "0") -> None:
    """Emit one edge, keeping circles and arcs as true DXF primitives."""
    try:
        curve = BRepAdaptor_Curve(edge)
        kind = curve.GetType()
    except Exception:
        return

    def flat(point) -> Tuple[float, float]:
        return plane.to_2d((point.X(), point.Y(), point.Z()))

    first, last = curve.FirstParameter(), curve.LastParameter()

    if kind == GeomAbs_CurveType.GeomAbs_Line:
        document.line(flat(curve.Value(first)), flat(curve.Value(last)),
                      layer)
        return

    if kind == GeomAbs_CurveType.GeomAbs_Circle:
        circle = curve.Circle()
        axis = circle.Axis().Direction()
        # only a circle lying in the face's plane can stay a DXF circle
        if abs(abs(axis.X() * plane.normal[0] + axis.Y() * plane.normal[1]
                   + axis.Z() * plane.normal[2]) - 1.0) < 1e-6:
            centre = flat(circle.Location())
            radius = circle.Radius()
            if abs((last - first) - 2 * math.pi) < 1e-6:
                document.circle(centre, radius, layer)
            else:
                start = flat(curve.Value(first))
                end = flat(curve.Value(last))
                a0 = math.degrees(math.atan2(start[1] - centre[1],
                                             start[0] - centre[0]))
                a1 = math.degrees(math.atan2(end[1] - centre[1],
                                             end[0] - centre[0]))
                # a DXF arc always runs anticlockwise, so a clockwise edge
                # has to be written with its ends the other way round
                if edge.Orientation() == TopAbs_REVERSED:
                    a0, a1 = a1, a0
                document.arc(centre, radius, a0, a1, layer)
            return

    # anything else - splines, ellipses, skewed circles - is approximated
    try:
        sampler = GCPnts_QuasiUniformDeflection(curve, DEFLECTION)
        if sampler.IsDone() and sampler.NbPoints() >= 2:
            points = [flat(sampler.Value(i))
                      for i in range(1, sampler.NbPoints() + 1)]
            document.polyline(points, layer=layer)
            return
    except Exception:
        pass

    document.line(flat(curve.Value(first)), flat(curve.Value(last)), layer)


def toolpath_to_dxf(report, path: str) -> str:
    """Write a CAM toolpath out: the offset centrelines and nothing else.

    The geometry is already compensated, already in millimetres and already
    in the XY plane, so this is a straight transcription.  Entities are
    emitted in cutting order - holes before the outline that frees the part
    - because entity order is the only sequencing a DXF carries, and MyPlasm
    follows it.
    """
    from .sketch import STANDARD_PLANES

    plane = STANDARD_PLANES["XY"]
    document = DxfDocument()

    for cut in sorted(report.cuts, key=lambda c: c.order):
        layer = "CUT_%s" % ("OUTER" if cut.outer else "INNER")
        for edge in cut.edges():
            _write_edge(document, TopoDS.Edge_s(edge), plane, layer)

    if not document.entities:
        raise KernelError("this toolpath produced no curves to export")
    return document.save(path)


def sketch_to_dxf(sketch, path: str) -> str:
    """Write a whole sketch out flat, for when the profile is 2D already."""
    document = DxfDocument()
    for eid, entity in sketch.entities.items():
        if entity.construction:
            continue
        if entity.kind == "line":
            a = sketch.points[entity.points[0]]
            b = sketch.points[entity.points[1]]
            document.line((a.x, a.y), (b.x, b.y))
        elif entity.kind == "circle":
            c = sketch.points[entity.points[0]]
            document.circle((c.x, c.y), entity.radius)
        elif entity.kind == "arc":
            c = sketch.points[entity.points[0]]
            a0, a1 = sketch.arc_angles(eid)
            document.arc((c.x, c.y), entity.radius,
                         math.degrees(a0), math.degrees(a1))
        else:
            document.polyline(sketch.entity_polyline(eid, 64))
    if not document.entities:
        raise KernelError("the sketch has nothing to export")
    return document.save(path)


DXF_FILTER = "DXF (*.dxf);;All files (*)"
