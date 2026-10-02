"""The view cube beside a base view being placed, as Inventor has it.

Turning the cube is how a base view is pointed: click a face to look
straight at it, an edge to look along it, a corner for an isometric from
that corner.  The arrows round it turn the view a quarter at a time, to
the face beyond, and the curved ones roll it; the house goes back to the
isometric.  The cube is drawn exactly as the view is pointed: from the
front it is one square, the front, whose corners give the isometrics and
whose edges the views along them, and the arrows reach the rest.

Painted with QPainter on the sheet, not the 3D viewport's cube: the sheet
is paper, and the cube belongs to the view on it.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

from PySide6 import QtCore, QtGui

Vec = Tuple[float, float, float]

# a face by its outward normal, the way hlr.ORIENTATIONS looks at it: the
# front view looks along +Y, so the face it sees faces -Y
FACES: Dict[str, Tuple[Vec, str]] = {
    "front": ((0.0, -1.0, 0.0), "FRONT"),
    "back": ((0.0, 1.0, 0.0), "BACK"),
    "left": ((-1.0, 0.0, 0.0), "LEFT"),
    "right": ((1.0, 0.0, 0.0), "RIGHT"),
    "top": ((0.0, 0.0, 1.0), "TOP"),
    "bottom": ((0.0, 0.0, -1.0), "BOTTOM"),
}

ISO: Tuple[Vec, Vec] = ((-1.0, 1.0, -1.0), (0.0, 0.0, 1.0))



def _unit(v: Sequence[float]) -> Vec:
    n = math.sqrt(sum(float(c) * float(c) for c in v))
    if n < 1e-12:
        return (0.0, 0.0, 1.0)
    return tuple(_clean(float(c) / n) for c in v)        # type: ignore


def _clean(value: float) -> float:
    value = round(value, 12)
    return 0.0 if value == 0.0 else value


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(float(a[i]) * float(b[i]) for i in range(3))


def _cross(a: Sequence[float], b: Sequence[float]) -> Vec:
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _neg(v: Sequence[float]) -> Vec:
    return (-v[0], -v[1], -v[2])


def right_of(direction: Sequence[float], up: Sequence[float]) -> Vec:
    """Across the page to the right, for a view looking this way."""
    return _unit(_cross(up, _neg(direction)))


def square_up(direction: Sequence[float], up: Sequence[float]) -> Vec:
    """``up`` made square to ``direction``, or something that is."""
    d = _unit(direction)
    u = _unit(up)
    along = _dot(u, d)
    if abs(along) > 0.999:
        u = (0.0, 1.0, 0.0) if abs(d[1]) < 0.9 else (0.0, 0.0, 1.0)
        along = _dot(u, d)
    return _unit([u[i] - along * d[i] for i in range(3)])


def looking_at(point: Sequence[float]) -> Tuple[Vec, Vec]:
    """Look from a point on the cube at its centre, Z kept up."""
    d = _unit(_neg(point))
    return (d, square_up(d, (0.0, 0.0, 1.0)))


def face_view(name: str) -> Tuple[Vec, Vec]:
    """Straight at a face, the way the drawing's own orientations do."""
    normal = FACES[name][0]
    d = _unit(_neg(normal))
    up = (0.0, 1.0, 0.0) if name in ("top", "bottom") else (0.0, 0.0, 1.0)
    return (d, up)


def turned(direction: Sequence[float], up: Sequence[float],
           how: str) -> Tuple[Vec, Vec]:
    """The view a quarter turn on: "right", "left", "up", "down" bring the
    face that way round to the front; "cw" and "ccw" roll the page."""
    d = _unit(direction)
    u = square_up(d, up)
    r = right_of(d, u)
    if how == "right":
        return (_unit(_neg(r)), u)
    if how == "left":
        return (r, u)
    if how == "up":
        return (_unit(_neg(u)), d)
    if how == "down":
        return (u, _unit(_neg(d)))
    if how == "cw":
        return (d, _unit(_neg(r)))
    if how == "ccw":
        return (d, r)
    return (d, u)


def _face_corners(normal: Vec) -> List[Vec]:
    axis = next(i for i in range(3) if normal[i] != 0.0)
    sign = normal[axis]
    a, b = [i for i in range(3) if i != axis]
    out = []
    for s, t in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        corner = [0.0, 0.0, 0.0]
        corner[axis] = sign
        corner[a] = float(s)
        corner[b] = float(t)
        out.append(tuple(corner))
    return out


class OrientationCube:
    """The cube, where it was last drawn, and what a click on it means."""

    SIZE = 34.0          # half the cube's width, in pixels, near enough

    def __init__(self) -> None:
        self.direction: Vec = (0.0, 1.0, 0.0)
        self.up: Vec = (0.0, 0.0, 1.0)
        self.hover: Optional[str] = None
        # filled in by draw(): what is where on the screen
        self._faces: List[Tuple[str, QtGui.QPolygonF, float]] = []
        self._corners: List[Tuple[Vec, QtCore.QPointF]] = []
        self._edges: List[Tuple[Vec, QtCore.QPointF]] = []
        self._buttons: List[Tuple[str, QtGui.QPolygonF]] = []
        self.centre = QtCore.QPointF()

    def set_view(self, direction: Sequence[float],
                 up: Sequence[float]) -> None:
        self.direction = _unit(direction)
        self.up = square_up(self.direction, up)

    # -- drawing ----------------------------------------------------------

    def _camera(self) -> Tuple[Vec, Vec, Vec]:
        # straight on, the way the view itself looks: no tilt to puzzle out
        d, u = self.direction, self.up
        return d, u, right_of(d, u)

    def _to_screen(self, point: Sequence[float], look, cam_up, cam_right,
                   scale: float) -> QtCore.QPointF:
        return QtCore.QPointF(self.centre.x() + _dot(point, cam_right) * scale,
                              self.centre.y() - _dot(point, cam_up) * scale)

    def draw(self, painter: QtGui.QPainter, centre: QtCore.QPointF) -> None:
        self.centre = QtCore.QPointF(centre)
        look, cam_up, cam_right = self._camera()
        scale = self.SIZE / 1.3
        project = lambda p: self._to_screen(p, look, cam_up,  # noqa: E731
                                            cam_right, scale)

        visible = [(name, normal, label) for name, (normal, label)
                   in FACES.items() if _dot(normal, look) < -1e-6]
        # far faces first, so the near ones are painted over them
        visible.sort(key=lambda f: _dot(f[1], look), reverse=True)

        self._faces = []
        seen_corners: Dict[Vec, QtCore.QPointF] = {}
        seen_edges: Dict[Vec, QtCore.QPointF] = {}
        painter.save()
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        # a panel of its own, so the cube reads over whatever is drawn
        # under it
        room = self.SIZE + 34.0
        painter.setPen(QtGui.QPen(QtGui.QColor("#c9d0d8"), 1.0))
        painter.setBrush(QtGui.QColor(255, 255, 255, 235))
        painter.drawRoundedRect(QtCore.QRectF(
            self.centre.x() - room, self.centre.y() - room,
            room * 2.0, room * 2.0), 8.0, 8.0)
        facing = _unit(_neg(self.direction))
        font = QtGui.QFont(painter.font())
        font.setPixelSize(22)
        font.setBold(True)
        for name, normal, label in visible:
            corners = _face_corners(normal)
            polygon = QtGui.QPolygonF([project(c) for c in corners])
            self._faces.append((name, polygon, -_dot(normal, look)))
            straight_on = _dot(normal, facing) > 0.999
            if self.hover == name:
                fill = QtGui.QColor("#9fc3ea")
            elif straight_on:
                fill = QtGui.QColor("#cfe0f2")
            else:
                fill = QtGui.QColor("#e8ebef")
            painter.setPen(QtGui.QPen(QtGui.QColor("#4a5563"), 1.2))
            painter.setBrush(fill)
            painter.drawPolygon(polygon)
            # the name lies on the face, the way it would on a real cube,
            # so a face seen at a slant still says which it is; one seen
            # nearly edge on is left blank rather than squashed
            if -_dot(normal, look) > 0.25:
                self._label(painter, font, normal, name, label, project)
            for i, corner in enumerate(corners):
                seen_corners[corner] = polygon[i]
                nxt = corners[(i + 1) % 4]
                mid = tuple((corner[k] + nxt[k]) / 2.0 for k in range(3))
                seen_edges[mid] = (polygon[i] + polygon[(i + 1) % 4]) / 2.0
        self._corners = list(seen_corners.items())
        self._edges = list(seen_edges.items())

        # the corner or edge under the cursor, so it is clear one can be had
        hot = None
        if self.hover and self.hover.startswith("corner:"):
            hot = dict(self._corners).get(_parse(self.hover))
        elif self.hover and self.hover.startswith("edge:"):
            hot = dict(self._edges).get(_parse(self.hover))
        if hot is not None:
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QColor("#3b82d6"))
            painter.drawEllipse(hot, 4.0, 4.0)

        self._draw_buttons(painter)
        painter.restore()

    def _label(self, painter: QtGui.QPainter, font: QtGui.QFont,
               normal: Vec, name: str, label: str, project) -> None:
        upward = (0.0, 1.0, 0.0) if name in ("top", "bottom")             else (0.0, 0.0, 1.0)
        across = right_of(_neg(normal), upward)
        corner = lambda a, b: project(  # noqa: E731
            [normal[i] + a * across[i] * 0.86 + b * upward[i] * 0.86
             for i in range(3)])
        target = QtGui.QPolygonF([corner(-1, 1), corner(1, 1),
                                  corner(1, -1), corner(-1, -1)])
        source = QtGui.QPolygonF([QtCore.QPointF(0.0, 0.0),
                                  QtCore.QPointF(100.0, 0.0),
                                  QtCore.QPointF(100.0, 100.0),
                                  QtCore.QPointF(0.0, 100.0)])
        transform = QtGui.QTransform()
        if not QtGui.QTransform.quadToQuad(source, target, transform):
            return
        painter.save()
        painter.setTransform(transform, True)
        painter.setFont(font)
        painter.setPen(QtGui.QColor("#2b333d"))
        painter.drawText(QtCore.QRectF(0.0, 0.0, 100.0, 100.0),
                         QtCore.Qt.AlignCenter, label)
        painter.restore()

    def _draw_buttons(self, painter: QtGui.QPainter) -> None:
        c = self.centre
        reach = self.SIZE + 16.0
        tip, half = 8.0, 6.0
        # named turn:..., not "right" and "left": those are faces, and an
        # arrow taken for the face it points at only ever went one step
        shapes = {
            "turn:right": [(reach + tip, 0), (reach, -half), (reach, half)],
            "turn:left": [(-reach - tip, 0), (-reach, -half), (-reach, half)],
            "turn:up": [(0, -reach - tip), (-half, -reach), (half, -reach)],
            "turn:down": [(0, reach + tip), (-half, reach), (half, reach)],
        }
        self._buttons = []
        for name, points in shapes.items():
            polygon = QtGui.QPolygonF([QtCore.QPointF(c.x() + x, c.y() + y)
                                       for x, y in points])
            self._buttons.append((name, polygon))
        # the house, back to the isometric, and the two rolls
        corner = QtCore.QPointF(c.x() - reach, c.y() - reach)
        house = QtGui.QPolygonF([QtCore.QPointF(corner.x() + x, corner.y() + y)
                                 for x, y in ((0, -7), (7, 0), (4, 0), (4, 6),
                                              (-4, 6), (-4, 0), (-7, 0))])
        self._buttons.append(("home", house))
        for name, dx in (("turn:ccw", reach - 20.0), ("turn:cw", reach + 2.0)):
            box = QtCore.QRectF(c.x() + dx - 8.0, c.y() - reach - 8.0,
                                16.0, 16.0)
            self._buttons.append((name, QtGui.QPolygonF(box)))

        symbol_font = QtGui.QFont(painter.font())
        symbol_font.setPixelSize(15)
        symbol_font.setBold(False)
        for name, polygon in self._buttons:
            hot = self.hover == name
            colour = QtGui.QColor("#3b82d6" if hot else "#6b7684")
            if name in ("turn:cw", "turn:ccw"):
                painter.save()
                painter.setFont(symbol_font)
                painter.setPen(colour)
                painter.drawText(polygon.boundingRect(), QtCore.Qt.AlignCenter,
                                 "↻" if name == "turn:cw" else "↺")
                painter.restore()
                continue
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(colour)
            painter.drawPolygon(polygon)

    # -- clicking ----------------------------------------------------------

    def element_at(self, pos: QtCore.QPointF) -> Optional[str]:
        """What is under the cursor: a corner, an edge, a face or a button."""
        for name, polygon in self._buttons:
            grown = polygon.boundingRect().adjusted(-3, -3, 3, 3)
            if grown.contains(pos):
                return name
        best, best_d = None, 6.5
        for corner, at in self._corners:
            d = math.hypot(at.x() - pos.x(), at.y() - pos.y())
            if d < best_d:
                best, best_d = "corner:%s" % _key(corner), d
        if best:
            return best
        best_d = 5.5
        for mid, at in self._edges:
            d = math.hypot(at.x() - pos.x(), at.y() - pos.y())
            if d < best_d:
                best, best_d = "edge:%s" % _key(mid), d
        if best:
            return best
        # nearest the viewer wins where faces overlap on screen
        for name, polygon, _near in sorted(self._faces, key=lambda f: -f[2]):
            if polygon.containsPoint(pos, QtCore.Qt.OddEvenFill):
                return name
        return None

    def view_for(self, element: str) -> Optional[Tuple[Vec, Vec]]:
        """Where a click on this element points the view."""
        if element in FACES:
            return face_view(element)
        if element.startswith("corner:") or element.startswith("edge:"):
            return looking_at(_parse(element))
        if element == "home":
            return (_unit(ISO[0]), square_up(ISO[0], ISO[1]))
        if element.startswith("turn:"):
            return turned(self.direction, self.up, element[5:])
        return None

    def contains(self, pos: QtCore.QPointF) -> bool:
        """Whether a point is on the cube's panel at all."""
        reach = self.SIZE + 34.0
        return (abs(pos.x() - self.centre.x()) <= reach
                and abs(pos.y() - self.centre.y()) <= reach)


def _key(v: Sequence[float]) -> str:
    return ",".join("%g" % c for c in v)


def _parse(text: str) -> Vec:
    return tuple(float(c) for c in text.split(":", 1)[1].split(","))  # type: ignore


def _polygon_area(polygon: QtGui.QPolygonF) -> float:
    total = 0.0
    n = polygon.count()
    for i in range(n):
        a, b = polygon[i], polygon[(i + 1) % n]
        total += a.x() * b.y() - b.x() * a.y()
    return total / 2.0
