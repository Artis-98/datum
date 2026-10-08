"""Interactive sketch editing inside the 3D view.

The editor turns cursor positions on the sketch plane into geometry, keeps the
constraint solver fed, and re-renders the sketch as OCCT overlay objects.  It
owns no widgets of its own - the ribbon drives it through :meth:`set_tool` and
the constraint/dimension commands.
"""

from __future__ import annotations

import json
import math
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

from OCP.Aspect import Aspect_TypeOfMarker
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge

from ..core import kernel
from ..core import units as unitlib
from ..core.params import ExpressionError, ParameterTable, evaluate
from ..core.sketch import Constraint, Entity, Sketch, SketchPoint
from .sketchinput import LiveDimensionBar, ValuePopup
from .theme import C

# tools that offer heads-up dimension entry while the shape is dragged out
LIVE_FIELDS = {
    "rect": ("W", "H"),
    "circle": ("ø",),
    "line": ("L", "∠"),
    "offset": ("D",),
}

# the heads-up fields that are angles, in degrees from the sketch's
# horizontal, rather than lengths in the part's units
LIVE_ANGLES = {
    "line": (1,),
}

# What each constraint shows as when its geometry is selected.  Short and
# unmistakable beats pretty: these are read at a glance beside a line, often
# several in a row, and they have to survive any font.
CONSTRAINT_BADGES = {
    "coincident": "o",
    "concentric": "@",
    "horizontal": "H",
    "vertical": "V",
    "parallel": "//",
    "collinear": "--",
    "perpendicular": "L",
    "tangent": "T",
    "equal": "=",
    "midpoint": "M",
    "point_on": "P",
    "symmetric": "S",
    "offset": "Of",
    "fix": "X",
    "ground": "X",
}

BADGE_PIXELS = 16.0          # how far apart the glyphs sit, on screen
BADGE_PICK_PIXELS = 11.0     # how near the cursor has to be to grab one

SNAP_PIXELS = 10.0
PICK_PIXELS = 8.0
AXIS_SNAP_DEG = 2.5

# other sketches left visible: drawn faintly, and their dimensions with them
REFERENCE_LINE = "#7f8894"
REFERENCE_DIM = "#95a0ad"
REFERENCE_PICK_PIXELS = 14.0

# Inventor folds the rectangle, slot and polygon variants into one
# drop-down.  Each variant is its own tool here, dispatched by name.
RECT_TOOLS = ("rect", "rect3", "rect_centre", "rect3_centre")
SLOT_TOOLS = ("slot", "slot_overall", "slot_centre", "slot_arc3",
              "slot_arc_centre")

# the variants whose rubber band is worked out by _preview_variant
VARIANT_PREVIEWS = ("rect3", "rect_centre", "rect3_centre", "slot_overall",
                    "slot_centre", "slot_arc3", "slot_arc_centre")

TOOLS = (("select", "line", "circle", "arc", "spline", "point", "fillet2d",
          "trim", "offset", "dimension", "polygon", "project")
         + RECT_TOOLS + SLOT_TOOLS)

TOOL_HINTS = {
    "select": "Select: click geometry, drag points. Ctrl+click to add to the selection.",
    "line": "Line: click the start point, then each following point. Esc ends the chain.",
    "rect": "Rectangle: click two opposite corners.",
    "rect3": "Rectangle, three point: click two corners of one side, then "
             "a third point setting the width.",
    "rect_centre": "Rectangle, two point centre: click the centre, then a "
                   "corner.",
    "rect3_centre": "Rectangle, three point centre: click the centre, then "
                    "the middle of one side, then a point setting the width.",
    "circle": "Circle: click the centre, then a point on the circumference.",
    "arc": "Arc: click the centre, then the start point, then the end point.",
    "polygon": "Polygon: click the centre, then a corner.",
    "slot": "Slot, centre to centre: click both centres, then a point "
            "setting the width.",
    "slot_overall": "Slot, overall: click both ends of the whole slot, then "
                    "a point setting the width.",
    "slot_centre": "Slot, centre point: click the middle of the slot, then "
                   "one end centre, then a point setting the width.",
    "slot_arc3": "Slot, three point arc: click the start, the end, then a "
                 "point on the arc, then a point setting the width.",
    "slot_arc_centre": "Slot, centre point arc: click the arc centre, the "
                       "start, the end, then a point setting the width.",
    "spline": "Spline: click each point. Enter or double-click finishes.",
    "point": "Point: click to place a sketch point.",
    "fillet2d": "Fillet: click two lines that meet.",
    "trim": "Trim: click the piece of geometry to remove.",
    "offset": "Offset: click a line, arc or circle and its whole loop "
              "follows the cursor. Click to place it, or type the distance "
              "and press Enter.",
    "project": "Project Geometry: click the model edges you want on this "
               "sketch. Esc when you are done.",
    "dimension": "Dimension: click one thing for its own size, or two for "
                 "the distance or angle between them. Click clear of them "
                 "to place the label.",
}


def _same_shape_index(pool, wanted):
    """Which entry of ``pool`` is the same piece of geometry as ``wanted``."""
    for index, candidate in enumerate(pool):
        try:
            if candidate.IsSame(wanted):
                return index
        except Exception:
            continue
    return None


def _circle_through(a, b, c):
    """Centre and radius of the circle through three points, or None.

    Three points in a line have no circle through them, and saying so beats
    dividing by a determinant that is nearly zero and drawing something
    enormous.
    """
    ax, ay = a
    bx, by = b
    cx, cy = c
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-9:
        return None
    a2 = ax * ax + ay * ay
    b2 = bx * bx + by * by
    c2 = cx * cx + cy * cy
    ux = (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / d
    uy = (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / d
    return ((ux, uy), math.dist((ux, uy), a))


def _heading(a, b) -> float:
    """The direction from a to b in degrees, 0 to 360, from the horizontal."""
    return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 360.0


def _dist_point_segment(p, a, b) -> float:
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    denom = dx * dx + dy * dy
    if denom < 1e-18:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / denom))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


class SketchEditor(QtCore.QObject):
    """Drives sketch creation and editing in the viewport."""

    changed = QtCore.Signal()
    hint_changed = QtCore.Signal(str)
    status_changed = QtCore.Signal(str)
    tool_finished = QtCore.Signal()
    constraint_armed = QtCore.Signal(str)

    def __init__(self, viewport, parent=None) -> None:
        super().__init__(parent)
        self.viewport = viewport
        self.sketch: Optional[Sketch] = None
        self.params: Optional[ParameterTable] = None

        self.tool = "select"
        self.polygon_sides = 6
        self.snap_grid = True
        self.show_grid = False
        self.construction = False

        # heads-up input, shown over the viewport at the cursor
        self.live = LiveDimensionBar(viewport)
        self.value_popup = ValuePopup(viewport)
        self.value_popup.accepted.connect(self._value_entered)
        self.value_popup.cancelled.connect(self._value_cancelled)
        self._value_apply: Optional[Callable[[str], None]] = None
        # which dimension the value box is editing, so it cannot be
        # written in terms of itself
        self._editing_dimension: Optional[int] = None

        # dimension placement
        self._dim_target: Optional[Dict[str, Any]] = None
        # the picks that produced _dim_target, kept so a further pick can
        # reconsider what is being measured instead of placing the label
        self._dim_picks: List[Tuple[str, int]] = []
        self._dim_offset: Tuple[float, float] = (0.0, 0.0)
        # whether a first dimension may scale the sketch, which the window
        # allows while there is no body yet
        self.autoscale = True
        # the loop the offset tool picked, and what it last made
        self._offset_chain = None
        self._offset_made = None

        # a constraint waiting for the user to pick its geometry
        self._pending_constraint: Optional[str] = None
        self.auto_constrain = True
        self._start_target: Optional[Tuple[str, int, Tuple[float, float]]] = None

        # sketch-local undo, so Ctrl+Z steps through sketch edits instead of
        # dropping the whole sketch off the document stack
        self._undo_stack: List[str] = []
        self._redo_stack: List[str] = []

        self._pending: List[Tuple[float, float]] = []
        self._cursor: Tuple[float, float] = (0.0, 0.0)
        self._snap_info = ""
        self._chain_from: Optional[int] = None

        self.selected_entities: List[int] = []
        self.selected_points: List[int] = []
        # lines picked by their middle rather than anywhere along them, so
        # Coincident can tell "attach to this line" from "attach to the
        # middle of this line" the way Inventor's midpoint snap does
        self._midpoint_picks: set = set()
        self._drag_point: Optional[int] = None
        # rubber-band selection: where it started, and where it is now
        self._box_start: Optional[Tuple[float, float]] = None
        self._box_end: Optional[Tuple[float, float]] = None
        # the point under the cursor, and the one a dragged point has
        # magnetised onto and is about to be mated with
        self._hover_point: Optional[int] = None
        self._hover_entity: Optional[int] = None
        # the constraint glyphs currently on screen, and which one is picked
        self._badges: List[Tuple[int, Tuple[float, float], str]] = []
        self._hover_badge: Optional[int] = None
        self.selected_constraint: Optional[int] = None
        self._magnet: Optional[int] = None
        self._drag_dimension: Optional[int] = None

        # the part's other visible sketches, drawn faintly behind this one,
        # and where their dimension labels landed, so one can be clicked
        self.references: List[Sketch] = []
        self._reference_labels: List[Tuple[str, Tuple[float, float, float],
                                           str]] = []
        # the unit a reference sketch is drawn in from outside a sketch
        self._units_override: Optional[str] = None

        viewport.plane_point_clicked.connect(self._on_click)
        viewport.plane_point_moved.connect(self._on_move)
        viewport.plane_drag_started.connect(self._on_drag_start)
        viewport.plane_drag_moved.connect(self._on_drag_move)
        viewport.plane_drag_finished.connect(self._on_drag_end)
        viewport.escape_pressed.connect(self.escape)
        viewport.delete_pressed.connect(self.delete_selected)
        viewport.plane_double_clicked.connect(self._on_double_click)
        viewport.plane_key_pressed.connect(self._on_key)

    # ------------------------------------------------------------- lifecycle

    def begin(self, sketch: Sketch, params) -> None:
        """Start editing a sketch.

        ``params`` is what the part offers by way of parameters: anything
        with a ``scope()``, and, for a part, the names already taken, so a
        new dimension here is not a second d1.
        """
        self.sketch = sketch
        self.params = params
        sketch.name_pool = getattr(params, "taken", None)
        self._undo_stack = []
        self._redo_stack = []
        # every sketch is anchored to its plane origin, however it was made
        sketch.ensure_origin_point()
        self.selected_entities = []
        self.selected_points = []
        self._box_start = self._box_end = None
        self._hover_point = None
        self._hover_entity = None
        self._hover_badge = None
        self.selected_constraint = None
        self._badges = []
        self._magnet = None
        self._pending = []
        self._chain_from = None
        self._dim_target = None
        self._dim_picks = []
        self.set_tool("select")
        self.viewport.grid_unit = self.units
        self.viewport.enter_plane_mode(sketch.plane, self.grid_step,
                                       self.show_grid)
        # a fit takes in the sketch, which OCCT does not count as scenery
        self.viewport.fit_extra = self._fit_points
        self.solve()
        self.render()

    def _fit_points(self) -> List[Tuple[float, float, float]]:
        """The corners of everything drawn on the sketch, in space."""
        s = self.sketch
        if s is None or not s.entities:
            return []
        umin, vmin, umax, vmax = s.bounds()
        return [s.plane.to_3d(u, v) for u in (umin, umax)
                for v in (vmin, vmax)]

    def end(self) -> None:
        if self.sketch is not None:
            self.sketch.name_pool = None
        self.viewport.fit_extra = None
        self.sketch = None
        self.references = []
        self._reference_labels = []
        self._pending = []
        self._dim_target = None
        self._dim_picks = []
        self.live.dismiss()
        self.value_popup.hide()
        self.viewport.leave_plane_mode()

    def set_show_grid(self, visible: bool) -> None:
        self.show_grid = visible
        if self.active:
            self.viewport.set_grid(visible, self.sketch.plane, self.grid_step)

    @property
    def grid_step(self) -> float:
        """The grid spacing snapped to, in millimetres.

        The viewport's own, which follows the zoom and the part's units, so
        the grid drawn and the grid snapped to can never disagree.
        """
        return getattr(self.viewport, "grid_step", 0.0) or 5.0

    def units_changed(self) -> None:
        """The part's units changed while the sketch is open: re-space."""
        if self.active:
            self.viewport.grid_unit = self.units
            self.viewport.refresh_grid()
            self.render()

    @property
    def active(self) -> bool:
        return self.sketch is not None

    # ----------------------------------------------------------------- tools

    def set_tool(self, tool: str) -> None:
        if tool not in TOOLS:
            return
        self.tool = tool
        # only the select tool drags anything - points, dimension labels, a
        # selection box.  With a drawing tool up, a press that travels is
        # still a click, so the viewport is told not to look for drags.
        self.viewport.plane_drag_allowed = (tool == "select")
        # Project Geometry is the one tool that picks the model rather than
        # the sketch, so the viewport hands model edges back for as long as
        # it runs.  Hanging it off set_tool means every existing way out -
        # Escape, another tool, finishing the sketch - already ends it.
        self.viewport.set_edge_picking(tool == "project")
        self._pending = []
        self._chain_from = None
        self._dim_target = None
        self._dim_picks = []
        self.live.dismiss()
        self.viewport.clear_preview()
        self.hint_changed.emit(TOOL_HINTS.get(tool, ""))
        self.render()

    @property
    def busy(self) -> bool:
        """True when a tool is running, so there is something to finish."""
        return self.active and self.tool != "select"

    def ok(self) -> None:
        """Finish whatever is running and go back to selecting.

        Escape peels back one layer at a time, which is what you want when
        undoing a misstep.  OK is the other half: you are done drawing, keep
        what is there and stop - one click instead of Escape, Escape.
        """
        if not self.active:
            return
        # a spline is the one tool holding work worth keeping when it stops
        if self.tool == "spline" and len(self._pending) >= 2:
            self.finish_spline()

        self._pending = []
        self._chain_from = None
        self._start_target = None
        self._pending_constraint = None
        self._dim_target = None
        self._dim_picks = []
        self.live.dismiss()
        self.viewport.clear_preview()
        self.constraint_armed.emit("")
        if self.tool != "select":
            self.set_tool("select")
        self.tool_finished.emit()
        self.hint_changed.emit("")
        self.status_changed.emit(self._status_text())
        self.render()

    def escape(self) -> None:
        """Back out of the innermost thing, never out of the sketch itself."""
        if not self.active:
            return
        if self._pending_constraint is not None:
            self._pending_constraint = None
            self.clear_selection()
            self.hint_changed.emit(TOOL_HINTS.get(self.tool, ""))
            self.constraint_armed.emit("")
            return
        if self._dim_target is not None:
            self._dim_target = None
            self._dim_picks = []
            self.viewport.clear_preview()
            self.render()
            return
        if self._pending:
            self._pending = []
            self._chain_from = None
            self._offset_chain = None
            self.live.dismiss()
            self.viewport.clear_preview()
            self.render()
            return
        if self.tool != "select":
            self.set_tool("select")
            self.tool_finished.emit()
            return
        if self.selected_entities or self.selected_points:
            self.clear_selection()

    # ------------------------------------------------------------- snapping

    def _degree(self, point_id: int) -> int:
        """How much geometry already meets at a point.

        Used to bias snapping: a junction several entities share is almost
        always what the user is aiming at, so it wins over a bare point.
        """
        if self.sketch is None:
            return 0
        count = sum(1 for e in self.sketch.entities.values()
                    if point_id in e.points)
        count += sum(1 for c in self.sketch.constraints.values()
                     if c.kind == "coincident" and point_id in c.points)
        if self.sketch.points.get(point_id) is not None:
            if self.sketch.points[point_id].origin:
                count += 3          # the grounded origin is the strongest
            elif self.sketch.points[point_id].fixed:
                count += 1
        return count

    def _snap_candidate(self, u: float, v: float, tol: float
                        ) -> Optional[Tuple[str, int, Tuple[float, float]]]:
        """The one thing a cursor at (u, v) latches onto, or None.

        (kind, id, position): a point, a line's midpoint, a circle's centre,
        a spot on a curve, or the origin.  Snapping the cursor and
        constraining to what it snapped to both ask this, so what gets
        constrained is always what the cursor visibly latched onto.
        """
        best: Optional[Tuple[str, int, Tuple[float, float]]] = None
        best_score = tol

        # Existing points beat anything else, and a point several pieces of
        # geometry already meet at beats a lone one - so a line drawn across
        # a circle latches onto the endpoint that is already joined to it
        # rather than to some arbitrary spot on the circumference.
        for p in self.sketch.points.values():
            d = math.hypot(p.x - u, p.y - v)
            if d > tol:
                continue
            score = d * 0.55 - min(0.35 * tol, 0.12 * tol * self._degree(p.id))
            if score < best_score:
                best, best_score = ("point", p.id, (p.x, p.y)), score

        for eid, ent in self.sketch.entities.items():
            if ent.kind == "line":
                a = self.sketch.points[ent.points[0]]
                b = self.sketch.points[ent.points[1]]
                mid = ((a.x + b.x) / 2.0, (a.y + b.y) / 2.0)
                d = math.hypot(mid[0] - u, mid[1] - v)
                if d <= tol and d * 0.8 < best_score:
                    best, best_score = ("midpoint", eid, mid), d * 0.8
            elif ent.kind in ("circle", "arc"):
                c = self.sketch.points[ent.points[0]]
                d = math.hypot(c.x - u, c.y - v)
                if d <= tol and d * 0.7 < best_score:
                    best, best_score = ("centre", c.id, (c.x, c.y)), d * 0.7
                on = math.hypot(u - c.x, v - c.y)
                if on > 1e-9:
                    k = ent.radius / on
                    cand = (c.x + (u - c.x) * k, c.y + (v - c.y) * k)
                    dd = math.hypot(cand[0] - u, cand[1] - v)
                    # on-curve is the weakest kind of snap: it is a whole
                    # curve's worth of candidates, so it must not outrank a
                    # real point that is nearly as close
                    if dd <= tol and dd * 1.6 < best_score:
                        best, best_score = ("on curve", eid, cand), dd * 1.6

        # origin gets its own, slightly stickier, snap
        if math.hypot(u, v) < tol * 1.2:
            origin = next((p.id for p in self.sketch.points.values()
                           if p.origin), -1)
            best = ("origin", origin, (0.0, 0.0))
        return best

    def _snap_target(self, u: float, v: float
                     ) -> Optional[Tuple[str, int, Tuple[float, float]]]:
        """What the last snap latched onto, so it can be constrained to.

        Snapping and constraining are the same intent: if the cursor locked
        to a point, the middle of a line or an edge, the geometry drawn
        there should stay attached to it rather than merely starting at the
        same coordinates.
        """
        if self.sketch is None:
            return None
        tol = SNAP_PIXELS * self.viewport.pixel_scale()

        hit = self._snap_candidate(u, v, tol)
        if hit is not None:
            kind, oid, position = hit
            if kind in ("point", "centre", "origin"):
                return ("point", oid, position) if oid in self.sketch.points \
                    else None
            if kind == "midpoint":
                return ("midpoint", oid, position)
            return ("entity", oid, position)

        best = None
        best_score = tol
        for eid in self.sketch.entities:
            polyline = self.sketch.entity_polyline(eid, 48)
            for i in range(len(polyline) - 1):
                distance = _dist_point_segment((u, v), polyline[i],
                                               polyline[i + 1])
                if distance <= tol and distance * 1.6 < best_score:
                    best = ("entity", eid, (u, v))
                    best_score = distance * 1.6
        return best

    def _attach(self, point_id: Optional[int],
                target: Optional[Tuple[str, int, Tuple[float, float]]]) -> None:
        """Tie a freshly created point to whatever it was snapped to."""
        if not self.auto_constrain or point_id is None or target is None:
            return
        kind, oid, _position = target
        s = self.sketch
        if kind == "point":
            if oid == point_id or oid not in s.points:
                return
            s.add_constraint("coincident", points=[point_id, oid])
        elif kind == "midpoint" and oid in s.entities:
            if point_id in s.entities[oid].points:
                return
            s.add_constraint("midpoint", points=[point_id], entities=[oid])
        elif kind == "entity" and oid in s.entities:
            if point_id in s.entities[oid].points:
                return
            s.add_constraint("point_on", points=[point_id], entities=[oid])

    def _endpoint_of(self, entity_id: int, position: Tuple[float, float]
                     ) -> Optional[int]:
        """The point of a just-created entity nearest ``position``."""
        entity = self.sketch.entities.get(entity_id)
        if entity is None:
            return None
        best, best_distance = None, 1e-4
        for pid in entity.points:
            point = self.sketch.points[pid]
            distance = math.hypot(point.x - position[0], point.y - position[1])
            if distance <= best_distance:
                best, best_distance = pid, distance
        return best

    def _snap(self, u: float, v: float,
              modifiers=QtCore.Qt.NoModifier) -> Tuple[float, float]:
        """Snap the raw cursor position to nearby geometry, then the grid."""
        if self.sketch is None:
            return (u, v)
        scale = self.viewport.pixel_scale()
        tol = SNAP_PIXELS * scale
        self._snap_info = ""

        if modifiers & QtCore.Qt.AltModifier:
            return (u, v)   # Alt suspends every snap

        hit = self._snap_candidate(u, v, tol)
        if hit is not None:
            self._snap_info = hit[0]
            return hit[2]

        # axis alignment against the chain start
        if self._pending:
            ax, ay = self._pending[-1]
            if abs(v - ay) < tol * 0.8:
                self._snap_info = "horizontal"
                v = ay
            elif abs(u - ax) < tol * 0.8:
                self._snap_info = "vertical"
                u = ax

        if self.snap_grid and not (modifiers & QtCore.Qt.ControlModifier):
            # the grid on screen and the grid snapped to are the same one,
            # in the part's own units, so 0.1 in reads 0.1 and not 0.0984
            step = self.grid_step
            gu = round(u / step) * step
            gv = round(v / step) * step
            if math.hypot(gu - u, gv - v) < tol * 0.7:
                self._snap_info = self._snap_info or "grid"
                return (gu, gv)
        return (u, v)

    # ----------------------------------------------------------- 2D picking

    def pick(self, u: float, v: float) -> Tuple[Optional[int], Optional[int]]:
        """Return (entity id, point id) nearest the cursor, either may be None."""
        if self.sketch is None:
            return (None, None)
        tol = PICK_PIXELS * self.viewport.pixel_scale()

        point_id, point_d = None, tol
        for p in self.sketch.points.values():
            d = math.hypot(p.x - u, p.y - v)
            if d < point_d:
                point_id, point_d = p.id, d

        entity_id, entity_d = None, tol
        for eid in self.sketch.entities:
            poly = self.sketch.entity_polyline(eid, 48)
            for i in range(len(poly) - 1):
                d = _dist_point_segment((u, v), poly[i], poly[i + 1])
                if d < entity_d:
                    entity_id, entity_d = eid, d
        return (entity_id, point_id)

    def pick_dimension(self, u: float, v: float) -> Optional[int]:
        if self.sketch is None:
            return None
        tol = 12.0 * self.viewport.pixel_scale()
        for c in self.sketch.constraints.values():
            if not c.is_dimension:
                continue
            anchor = self._dimension_anchor(c)
            if anchor is None:
                continue
            pos = (anchor[0] + c.label_offset[0], anchor[1] + c.label_offset[1])
            if math.hypot(pos[0] - u, pos[1] - v) < tol:
                return c.id
        return None

    def _picked_midpoint(self, eid: int, u: float, v: float) -> bool:
        """Whether this click landed on a line's middle rather than its run.

        Held to the same reach as the drawing snap, so the spot that shows
        a midpoint marker while drawing is the spot that means the midpoint
        while constraining.
        """
        entity = self.sketch.entities.get(eid)
        if entity is None or entity.kind != "line":
            return False
        a = self.sketch.points[entity.points[0]]
        b = self.sketch.points[entity.points[1]]
        mid = ((a.x + b.x) / 2.0, (a.y + b.y) / 2.0)
        tol = SNAP_PIXELS * self.viewport.pixel_scale()
        return math.hypot(mid[0] - u, mid[1] - v) <= tol

    def clear_selection(self) -> None:
        self.selected_entities = []
        self.selected_points = []
        self._midpoint_picks = set()
        self._box_start = self._box_end = None
        self._hover_point = None
        self._hover_entity = None
        self._hover_badge = None
        self.selected_constraint = None
        self._badges = []
        self._magnet = None
        self.render()

    # ------------------------------------------------------------ solving

    @property
    def units(self) -> str:
        """The part's length unit: what dimensions read in and are typed in."""
        return unitlib.known(self._units_override
                             or getattr(self.params, "units", "mm") or "mm")

    def _shown(self, c_kind: str, value: float) -> str:
        """A dimension's value as the part's units write it, no unit."""
        if c_kind == "angle":
            return unitlib.fmt(value)
        return unitlib.fmt(unitlib.to_unit(value, self.units))

    def _typed(self, kind: str, text: str) -> Tuple[float, str]:
        """(value in mm, expression) for what was typed into a dimension.

        A plain number is the dimension's value, in the part's units; an
        expression is kept with its lengths' units written in, so it
        means the same if the part's units change.  Raises ExpressionError.
        """
        unit_kind = unitlib.ANGLE if kind == "angle" else unitlib.LENGTH
        plain = unitlib.plain_value(text, self.units, unit_kind)
        if plain is not None:
            return plain, ""
        stored = unitlib.for_storage(text, self.units, unit_kind)
        return evaluate(stored, self.scope()), stored

    def scope(self) -> Dict[str, float]:
        """Named parameters, plus this sketch's own dimensions by name.

        Everything that reads a typed value goes through here, so d2 means
        the same thing in a new dimension's box as it does in one being
        edited and as it does to the solver.
        """
        base = self.params.scope() if self.params else {}
        return self.sketch.dimension_scope(base) if self.sketch else base

    def solve(self) -> None:
        if self.sketch is None:
            return
        scope = self.params.scope() if self.params else {}
        self.sketch.solve(scope)
        self.status_changed.emit(self._status_text())

    def _status_text(self) -> str:
        if self.sketch is None:
            return ""
        msg = self.sketch.solve_message
        if self.sketch.conflicting:
            return "Sketch: %s - %d conflicting constraint(s)" % (
                msg, len(self.sketch.conflicting))
        return "Sketch: %s" % msg

    # -- undo inside the sketch --------------------------------------------

    def begin_change(self) -> None:
        """Record the sketch before an edit, so Ctrl+Z can step back one."""
        if self.sketch is None:
            return
        self._undo_stack.append(json.dumps(self.sketch.to_dict(),
                                           separators=(",", ":")))
        if len(self._undo_stack) > 200:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

    def discard_change(self) -> None:
        """Drop the last recorded state - the edit turned out to be a no-op."""
        if self._undo_stack:
            self._undo_stack.pop()

    @property
    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    def undo(self) -> bool:
        """Step back one sketch edit, staying inside the sketch."""
        if not self.active or not self._undo_stack:
            return False
        self._redo_stack.append(json.dumps(self.sketch.to_dict(),
                                           separators=(",", ":")))
        self._restore(self._undo_stack.pop())
        return True

    def redo(self) -> bool:
        if not self.active or not self._redo_stack:
            return False
        self._undo_stack.append(json.dumps(self.sketch.to_dict(),
                                           separators=(",", ":")))
        self._restore(self._redo_stack.pop())
        return True

    def _restore(self, blob: str) -> None:
        restored = Sketch.from_dict(json.loads(blob))
        target = self.sketch
        target.points = restored.points
        target.entities = restored.entities
        target.constraints = restored.constraints
        target._next_id = restored._next_id
        self._pending = []
        self._dim_target = None
        self._dim_picks = []
        self.clear_selection()
        self.solve()
        self.render()
        self.changed.emit()

    def _touch(self) -> None:
        self.solve()
        self.render()
        self.changed.emit()

    # ------------------------------------------------------------- events

    def _on_move(self, u: float, v: float, modifiers) -> None:
        if not self.active:
            return
        self._cursor = self._snap(u, v, modifiers)

        if self._dim_target is not None:
            self._retarget(self._dim_target, self._cursor)
            self._dim_offset = self._dimension_offset(self._dim_target,
                                                      self._cursor)
        else:
            self._update_live()
        self._track_hover(u, v)
        self._render_preview()

    # tools where the next click picks something, so it is worth showing
    # what that something would be
    PICKING_TOOLS = ("select", "dimension", "trim", "fillet2d", "offset")

    def _picking(self) -> bool:
        return (self.tool in self.PICKING_TOOLS
                or self._pending_constraint is not None)

    def _track_hover(self, u: float, v: float) -> None:
        """Light up whatever the next click would take.

        Sketch points are not drawn at all until something is going on with
        them - a sketch full of dots at every line end is noise.  One comes
        up green when the cursor is near enough to grab it, which is also
        the only way to know it is there.  Lines do the same, so there is no
        need for a marker riding along on the cursor to say where it is.
        """
        if self.sketch is None or self._drag_point is not None:
            return
        badge = self.pick_badge(u, v) if self._picking() else None
        eid, pid = self.pick(u, v)
        if badge is not None:
            pid = eid = None
        # a point is the more specific pick, so it wins over the line it
        # sits on - the same rule the click itself follows
        entity = eid if (pid is None and self._picking()) else None
        if (pid != self._hover_point or entity != self._hover_entity
                or badge != self._hover_badge):
            self._hover_point = pid
            self._hover_entity = entity
            self._hover_badge = badge
            self.render()

    def _update_live(self) -> None:
        """Show or refresh the heads-up dimension fields for the active tool."""
        captions = LIVE_FIELDS.get(self.tool)
        if captions is None or not self._pending:
            if self.live.isVisible():
                self.live.dismiss()
            return
        if not self.live.isVisible() or len(self.live.fields) != len(captions):
            self.live.configure(captions)
        values = self._live_values(self._resolved_cursor(self._cursor))
        if values:
            # lengths show in the part's units, angles in degrees
            angles = LIVE_ANGLES.get(self.tool, ())
            self.live.track([v if i in angles else unitlib.to_unit(v, self.units)
                             for i, v in enumerate(values)],
                            QtGui.QCursor.pos())

    def _on_click(self, u: float, v: float, modifiers) -> None:
        if not self.active:
            return

        # While a value is being typed, a click on another dimension writes
        # its name into the box instead of doing whatever the current tool
        # would have done.  That is how one dimension comes to be expressed
        # in terms of another without anybody having to remember that the
        # one over there is called d4.
        if self.value_popup.isVisible():
            cid = self.pick_dimension(u, v)
            if cid is not None and self._insert_dimension_name(cid):
                return
            # or on one of another visible sketch's, so a new sketch can
            # take a size from an old one
            if self._insert_reference_name(u, v):
                return

        point = self._snap(u, v, modifiers)
        self._cursor = point

        # a typed heads-up value wins over wherever the cursor happens to be
        if (self.live.isVisible() and self.live.any_locked()
                and self._pending and self.tool in LIVE_FIELDS):
            self._commit_live()
            return

        handler = getattr(self, "_tool_" + self.tool, None)
        if handler is not None:
            before = self._change_signature()
            self.begin_change()
            handler(point, modifiers)
            if self._change_signature() == before:
                self.discard_change()     # the click did not alter anything
            self._update_live()

    def _change_signature(self):
        """Cheap fingerprint used to tell whether an action changed the sketch."""
        if self.sketch is None:
            return None
        return (len(self.sketch.points), len(self.sketch.entities),
                len(self.sketch.constraints), self.sketch._next_id)

    def _on_drag_start(self, u: float, v: float) -> None:
        if not self.active or self.tool != "select":
            return
        dim = self.pick_dimension(u, v)
        if dim is not None:
            self._drag_dimension = dim
            return
        eid, pid = self.pick(u, v)
        self._drag_point = pid
        if pid is None and eid is None:
            # nothing under the cursor, so the drag is a selection box
            self._box_start = (u, v)
            self._box_end = (u, v)

    def _on_drag_move(self, u: float, v: float) -> None:
        if not self.active:
            return
        if self._box_start is not None:
            self._box_end = (u, v)
            self.status_changed.emit(
                "Crossing: everything the box touches"
                if self._box_crossing()
                else "Window: only what is completely inside")
            self._render_preview()
            return
        if self._drag_dimension is not None:
            c = self.sketch.constraints.get(self._drag_dimension)
            if c is not None:
                # slides perpendicular to what it measures, on the plane
                target = {"kind": c.kind, "points": list(c.points),
                          "entities": list(c.entities), "current": c.value}
                c.label_offset = self._dimension_offset(target, (u, v))
                self.render()
            return
        if self._drag_point is not None and self.sketch is not None:
            scope = self.params.scope() if self.params else {}
            target = self._magnet_target(self._drag_point, (u, v))
            self._magnet = target[0] if target else None
            anchor = target[1] if target else (u, v)
            self.sketch.solve(scope, anchors={self._drag_point: anchor})
            if self._magnet is not None:
                self.status_changed.emit(
                    "Release to make these two points coincident.")
            else:
                self.status_changed.emit(self._status_text())
            self.render()

    def _magnet_target(self, pid: int, cursor: Tuple[float, float]):
        """The point a dragged one has come close enough to snap onto.

        Returning its exact position rather than the cursor's is what makes
        the pull feel magnetic: the point stops following the mouse and sits
        on its target, so what you see before you let go is what you get.
        """
        s = self.sketch
        if s is None or not self.auto_constrain:
            return None
        point = s.points.get(pid)
        if point is None or point.origin:
            return None

        own = {pid}
        for entity in s.entities.values():
            if pid in entity.points:
                own.update(entity.points)

        tol = SNAP_PIXELS * self.viewport.pixel_scale()
        best, best_d = None, tol
        for other in s.points.values():
            if other.id in own:
                continue
            d = math.hypot(other.x - cursor[0], other.y - cursor[1])
            if d < best_d:
                best, best_d = other, d
        if best is None:
            return None
        return (best.id, (best.x, best.y))

    # -- rubber-band selection ---------------------------------------------

    def _box_crossing(self) -> bool:
        """True for a right-to-left drag, which takes whatever it touches.

        Every CAD package agrees on this: drag left to right and you get a
        window that only takes what is wholly inside; drag right to left and
        you get a crossing box that takes anything it so much as clips.
        """
        if self._box_start is None or self._box_end is None:
            return False
        return self._box_end[0] < self._box_start[0]

    def _box_rect(self) -> Optional[Tuple[float, float, float, float]]:
        if self._box_start is None or self._box_end is None:
            return None
        x0, x1 = sorted((self._box_start[0], self._box_end[0]))
        y0, y1 = sorted((self._box_start[1], self._box_end[1]))
        return (x0, y0, x1, y1)

    @staticmethod
    def _inside(point, rect) -> bool:
        return (rect[0] <= point[0] <= rect[2]
                and rect[1] <= point[1] <= rect[3])

    @classmethod
    def _segment_hits(cls, a, b, rect) -> bool:
        """Does a segment touch an axis-aligned box? Liang-Barsky clipping.

        Testing only the sample points would miss a line that crosses the
        box with both ends outside it - which is exactly the line a crossing
        selection is usually after.
        """
        if cls._inside(a, rect) or cls._inside(b, rect):
            return True
        dx, dy = b[0] - a[0], b[1] - a[1]
        t0, t1 = 0.0, 1.0
        for delta, distance in ((-dx, a[0] - rect[0]), (dx, rect[2] - a[0]),
                                (-dy, a[1] - rect[1]), (dy, rect[3] - a[1])):
            if abs(delta) < 1e-12:
                if distance < 0:
                    return False
                continue
            t = distance / delta
            if delta < 0:
                if t > t1:
                    return False
                t0 = max(t0, t)
            else:
                if t < t0:
                    return False
                t1 = min(t1, t)
        return t0 <= t1

    def _entity_in_box(self, eid: int, rect, crossing: bool) -> bool:
        try:
            samples = self.sketch.entity_polyline(eid, 48)
        except (KeyError, IndexError):
            return False
        if len(samples) < 2:
            return bool(samples) and self._inside(samples[0], rect)
        if not crossing:
            return all(self._inside(s, rect) for s in samples)
        return any(self._segment_hits(samples[i], samples[i + 1], rect)
                   for i in range(len(samples) - 1))

    def _apply_box(self) -> None:
        """Turn the box that was just dragged out into a selection."""
        rect = self._box_rect()
        crossing = self._box_crossing()
        self._box_start = self._box_end = None
        self._hover_point = None
        self._hover_entity = None
        self._hover_badge = None
        self.selected_constraint = None
        self._badges = []
        self._magnet = None
        if rect is None or self.sketch is None:
            self._render_preview()
            return

        entities = [eid for eid in self.sketch.entities
                    if self._entity_in_box(eid, rect, crossing)]
        taken = set()
        for eid in entities:
            taken.update(self.sketch.entities[eid].points)

        # A point that belongs to an entity already selected is part of that
        # entity, not a separate pick - otherwise a window over one whole
        # line would offer its own two ends to a coincident constraint.
        points = [pid for pid, point in self.sketch.points.items()
                  if pid not in taken and not point.origin
                  and self._inside((point.x, point.y), rect)]

        self.selected_entities = entities
        self.selected_points = points
        self.render()
        self._check_pending_constraint()
        # after the render, which posts the degrees-of-freedom line and would
        # otherwise wipe this the instant it appeared
        self.status_changed.emit(
            "%d entit%s and %d point(s) selected."
            % (len(entities), "y" if len(entities) == 1 else "ies",
               len(points))
            if (entities or points) else "Nothing in the box.")

    def _on_double_click(self, u: float, v: float) -> None:
        """Double-clicking a dimension reopens it for editing."""
        if not self.active:
            return
        cid = self.pick_dimension(u, v)
        if cid is None:
            return
        c = self.sketch.constraints[cid]
        current = (unitlib.for_display(
            c.expression, self.units,
            unitlib.ANGLE if c.kind == "angle" else unitlib.LENGTH)
            if c.expression else self._shown(c.kind,
                                             self._dimension_value(c)))
        self._editing_dimension = cid
        self._ask_value(current,
                        lambda text, k=cid: self._edit_dimension(k, text),
                        caption="Edit %s" % (c.name or ""))

    def _edit_dimension(self, cid: int, text: str) -> None:
        c = self.sketch.constraints.get(cid)
        if c is None:
            return
        self.begin_change()
        before = (c.value, c.expression)
        text = text.strip()
        try:
            c.value, c.expression = self._typed(c.kind, text)
        except ExpressionError as exc:
            self._complain("That is not a valid value: %s" % exc)
            return
        self.solve()
        if self.sketch.solve_message.startswith("over"):
            c.value, c.expression = before
            self.solve()
            self._complain("The sketch cannot satisfy that value.")
        self._touch()

    def _on_key(self, key: int, text: str) -> None:
        """Keystrokes during a drag go to the heads-up dimension fields."""
        if not self.active:
            return

        if key in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            if self.live.isVisible():
                self._commit_live()
            elif self.tool == "spline":
                self.finish_spline()
            return

        if self.live.isVisible() and self.live.type_key(key, text):
            self._render_preview()

    def _live_values(self, cursor) -> Optional[List[float]]:
        """Current dimensions of the shape being dragged out."""
        if not self._pending:
            return None
        start = self._pending[0]
        if self.tool == "rect":
            return [abs(cursor[0] - start[0]), abs(cursor[1] - start[1])]
        if self.tool == "circle":
            return [2.0 * math.dist(start, cursor)]
        if self.tool == "line":
            anchor = self._pending[-1]
            return [math.dist(anchor, cursor), _heading(anchor, cursor)]
        if self.tool == "offset" and getattr(self, "_offset_chain", None):
            return [abs(self.sketch.offset_distance(self._offset_chain[0],
                                                    cursor))]
        return None

    def _resolved_cursor(self, cursor) -> Tuple[float, float]:
        """Where the shape actually ends, once typed values take over."""
        if not self._pending or not self.live.any_locked():
            return cursor
        scope = self.params.scope() if self.params else {}

        def number(text, fallback, kind=unitlib.LENGTH):
            try:
                return evaluate(unitlib.for_storage(text, self.units, kind),
                                scope)
            except (ExpressionError, TypeError):
                return fallback

        start = self._pending[0]
        locked = self.live.locked_values()
        if self.tool == "rect":
            x, y = cursor
            if locked[0]:
                width = number(locked[0], abs(cursor[0] - start[0]))
                x = start[0] + math.copysign(width, cursor[0] - start[0] or 1.0)
            if len(locked) > 1 and locked[1]:
                height = number(locked[1], abs(cursor[1] - start[1]))
                y = start[1] + math.copysign(height, cursor[1] - start[1] or 1.0)
            return (x, y)
        if self.tool == "circle" and locked[0]:
            radius = number(locked[0], 2.0 * math.dist(start, cursor)) / 2.0
            dx, dy = cursor[0] - start[0], cursor[1] - start[1]
            length = math.hypot(dx, dy) or 1.0
            return (start[0] + dx / length * radius,
                    start[1] + dy / length * radius)
        typed_angle = locked[1] if len(locked) > 1 else None
        if self.tool == "line" and (locked[0] or typed_angle):
            # a typed angle fixes the direction and the line runs along it
            # as far as the cursor reaches; a typed length fixes how far
            anchor = self._pending[-1]
            dx, dy = cursor[0] - anchor[0], cursor[1] - anchor[1]
            reach = math.hypot(dx, dy)
            if typed_angle:
                angle = math.radians(number(typed_angle,
                                            _heading(anchor, cursor),
                                            unitlib.ANGLE))
                ux, uy = math.cos(angle), math.sin(angle)
                reach = max(0.0, dx * ux + dy * uy)
            else:
                ux, uy = (dx / reach, dy / reach) if reach > 1e-12 else (1.0, 0.0)
            if locked[0]:
                reach = number(locked[0], reach)
            return (anchor[0] + ux * reach, anchor[1] + uy * reach)
        return cursor

    def _commit_live(self) -> None:
        """Enter with heads-up values typed: build the shape and dimension it."""
        if not self._pending:
            return
        point = self._resolved_cursor(self._cursor)
        locked = self.live.locked_values()
        tool = self.tool
        start = self._pending[0]

        handler = getattr(self, "_tool_" + tool, None)
        if handler is None:
            return
        before_entities = set(self.sketch.entities)
        handler(point, QtCore.Qt.NoModifier)
        created = [e for e in self.sketch.entities if e not in before_entities]
        if not created:
            return

        self._dimension_created(tool, created, start, point, locked)
        self.live.dismiss()
        self._touch()

    def _dimension_created(self, tool: str, created: List[int],
                           start, end, locked: List[Optional[str]]) -> None:
        """Turn typed heads-up values into real driving dimensions."""
        s = self.sketch
        if tool == "rect" and len(created) >= 4:
            bottom = s.entities[created[0]].points
            left = s.entities[created[3]].points
            if locked and locked[0]:
                self._add_dimension("distance_x", [bottom[0], bottom[1]], [],
                                    abs(end[0] - start[0]), locked[0])
            if len(locked) > 1 and locked[1]:
                self._add_dimension("distance_y", [left[1], left[0]], [],
                                    abs(end[1] - start[1]), locked[1])
        elif tool == "circle" and created:
            if locked and locked[0]:
                self._add_dimension("diameter", [], [created[0]],
                                    2.0 * math.dist(start, end), locked[0])
        elif tool == "offset" and created and locked and locked[0]:
            self._dimension_offset_made(locked[0])
        elif tool == "line" and created:
            pts = s.entities[created[0]].points
            if locked and locked[0]:
                self._add_dimension("distance", [pts[0], pts[1]], [],
                                    math.dist(start, end), locked[0])
            if len(locked) > 1 and locked[1]:
                self._dimension_line_angle(created[0], locked[1])

    def _dimension_offset_made(self, text: str) -> None:
        """Put the typed gap on the first curve of an offset just made."""
        made = getattr(self, "_offset_made", None)
        if not made or self.sketch is None:
            return
        s = self.sketch
        source, copy = made["pairs"][0]
        d = made["d"]
        if s.entities[source].kind == "line":
            a, b = s.entities[source].points
            # held on the side it was made on: the measured side follows
            # the order the line's ends are given in
            ends = [a, b] if made["signs"][0] * d > 0 else [b, a]
            self._add_dimension("distance_pl",
                                [s.entities[copy].points[0]] + ends, [],
                                abs(d), text)
        else:
            inner, outer = ((source, copy)
                            if s.entities[copy].radius > s.entities[source].radius
                            else (copy, source))
            self._add_dimension("radial_gap", [], [inner, outer], abs(d), text)

    def _dimension_line_angle(self, eid: int, text: str) -> None:
        """Hold a just-drawn line at the angle typed for it.

        Measured from the sketch's horizontal.  A plain 0, 90, 180 or 270
        is already held by the horizontal or vertical constraint drawing it
        gave the line, so that is left to do the job.  Anything else drops
        that constraint, which drawing may have guessed from a line only a
        degree or two off square, before the angle goes on.
        """
        s = self.sketch
        unit_kind = unitlib.ANGLE
        plain = unitlib.plain_value(text, self.units, unit_kind)
        if plain is not None and abs((plain + 45.0) % 90.0 - 45.0) < 1e-9:
            return
        for cid, c in list(s.constraints.items()):
            if c.kind in ("horizontal", "vertical") and c.entities == [eid]:
                s.remove_constraint(cid)
        cid = self._add_dimension("angle", [], [eid],
                                  plain if plain is not None else 0.0, text)
        if cid is None:
            return
        # the label sits inside the angle, a little way out from the corner
        a, b = (s.points[pid] for pid in s.entities[eid].points)
        reach = 0.45 * math.hypot(b.x - a.x, b.y - a.y)
        half = math.radians(_heading((a.x, a.y), (b.x, b.y))) / 2.0
        s.constraints[cid].label_offset = (reach * math.cos(half),
                                           reach * math.sin(half))

    def _on_drag_end(self) -> None:
        if self._box_start is not None:
            self._apply_box()
            return
        if self._drag_point is not None:
            self._weld_dragged_point(self._drag_point)
        self._magnet = None
        if self._drag_point is not None or self._drag_dimension is not None:
            self._drag_point = None
            self._drag_dimension = None
            self._touch()

    def _weld_dragged_point(self, pid: int) -> None:
        """Dropping a point onto another point or edge attaches it there."""
        if not self.auto_constrain or self.sketch is None:
            return
        point = self.sketch.points.get(pid)
        if point is None or point.origin:
            return

        # if it magnetised onto something while being dragged, that is the
        # one to mate it with - not whatever else happens to be near now
        if self._magnet is not None and self._magnet in self.sketch.points:
            self.sketch.add_constraint("coincident",
                                       points=[pid, self._magnet])
            return

        tol = SNAP_PIXELS * self.viewport.pixel_scale()

        own = {pid}
        for entity in self.sketch.entities.values():
            if pid in entity.points:
                own.update(entity.points)

        for other in self.sketch.points.values():
            if other.id in own:
                continue
            if math.hypot(other.x - point.x, other.y - point.y) < tol:
                self.sketch.add_constraint("coincident",
                                           points=[pid, other.id])
                return

        for eid, entity in self.sketch.entities.items():
            if pid in entity.points:
                continue
            polyline = self.sketch.entity_polyline(eid, 48)
            for i in range(len(polyline) - 1):
                if _dist_point_segment((point.x, point.y), polyline[i],
                                       polyline[i + 1]) < tol:
                    self.sketch.add_constraint("point_on", points=[pid],
                                               entities=[eid])
                    return

    # ------------------------------------------------------------- tools

    # -- constraint glyphs ---------------------------------------------------

    def _badge_layout(self) -> List[Tuple[int, Tuple[float, float], str]]:
        """Where to draw a glyph for every constraint on the selection.

        Selecting a line and being told nothing about why it will not move is
        the single most confusing thing a sketcher can do.  These say what is
        holding it, and clicking one lets it go - without having to delete the
        line to be rid of a constraint that was never meant to be there.
        """
        s = self.sketch
        if s is None:
            return []
        scale = self.viewport.pixel_scale()
        step = BADGE_PIXELS * scale
        lift = BADGE_PIXELS * scale

        anchors: List[Tuple[Tuple[float, float], Tuple[float, float],
                            List[int]]] = []
        placed: set = set()

        def constraints_touching(entities=(), points=()) -> List[int]:
            out = []
            for cid, c in s.constraints.items():
                if c.is_dimension or cid in placed:
                    continue
                if (any(e in c.entities for e in entities)
                        or any(pt in c.points for pt in points)):
                    out.append(cid)
                    placed.add(cid)
            return out

        for eid in self.selected_entities:
            ent = s.entities.get(eid)
            if ent is None:
                continue
            # what holds a line includes what holds its ends: a coincident
            # at a corner is as much a reason the line will not move as the
            # horizontal on the line itself
            found = constraints_touching(entities=[eid], points=ent.points)
            if not found:
                continue
            mid, direction, normal = self._entity_frame(eid)
            anchors.append(((mid[0] + normal[0] * lift,
                             mid[1] + normal[1] * lift), direction, found))

        for pid in self.selected_points:
            point = s.points.get(pid)
            if point is None:
                continue
            found = constraints_touching(points=[pid])
            if not found:
                continue
            anchors.append(((point.x + lift * 0.7, point.y + lift * 0.7),
                            (1.0, 0.0), found))

        out: List[Tuple[int, Tuple[float, float], str]] = []
        for start, direction, found in anchors:
            for i, cid in enumerate(found):
                kind = s.constraints[cid].kind
                out.append((cid,
                            (start[0] + direction[0] * step * i,
                             start[1] + direction[1] * step * i),
                            CONSTRAINT_BADGES.get(kind, "?")))
        return out

    def _entity_frame(self, eid: int):
        """An entity's middle, the way it runs, and the way out of it."""
        s = self.sketch
        ent = s.entities[eid]
        if ent.kind in ("circle", "arc"):
            centre = s.points[ent.points[0]]
            return ((centre.x, centre.y + ent.radius), (1.0, 0.0), (0.0, 1.0))
        try:
            polyline = s.entity_polyline(eid, 16)
        except (KeyError, IndexError):
            polyline = []
        if len(polyline) < 2:
            point = s.points[ent.points[0]]
            return ((point.x, point.y), (1.0, 0.0), (0.0, 1.0))
        a, b = polyline[0], polyline[-1]
        # a straight line tessellates to exactly two points, and the middle
        # index of those is the far end - which put the glyphs at the tip of
        # the line instead of halfway along it
        mid = (polyline[len(polyline) // 2] if len(polyline) > 2
               else ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0))
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy) or 1.0
        direction = (dx / length, dy / length)
        return (mid, direction, (-direction[1], direction[0]))

    def pick_badge(self, u: float, v: float) -> Optional[int]:
        """The constraint glyph under the cursor, if any."""
        tol = BADGE_PICK_PIXELS * self.viewport.pixel_scale()
        best, best_d = None, tol
        for cid, position, _symbol in self._badges:
            d = math.hypot(position[0] - u, position[1] - v)
            if d < best_d:
                best, best_d = cid, d
        return best

    def delete_constraint(self, cid: int) -> bool:
        """Drop one constraint, leaving the geometry it held alone."""
        if self.sketch is None or cid not in self.sketch.constraints:
            return False
        kind = self.sketch.constraints[cid].kind
        self.begin_change()
        self.sketch.remove_constraint(cid)
        if self.selected_constraint == cid:
            self.selected_constraint = None
        self._hover_badge = None
        self._touch()
        self.status_changed.emit("Removed the %s constraint." % kind)
        return True

    def _tool_select(self, point, modifiers) -> None:
        u, v = point
        # a glyph sits on top of the geometry it belongs to, so it gets the
        # click first - otherwise it could never be hit
        badge = self.pick_badge(u, v)
        if badge is not None:
            self.selected_constraint = (None if self.selected_constraint == badge
                                        else badge)
            self.render()
            if self.selected_constraint is not None:
                self.status_changed.emit(
                    "%s constraint selected - press Delete to remove it."
                    % self.sketch.constraints[badge].kind.replace(
                        "_", " ").title())
            return

        # a dimension is a constraint too, and its label is the only part of
        # it there is to click
        dim = self.pick_dimension(u, v)
        if dim is not None:
            self.selected_constraint = (None if self.selected_constraint == dim
                                        else dim)
            self.render()
            if self.selected_constraint is not None:
                c = self.sketch.constraints[dim]
                self.status_changed.emit(
                    "%s selected - press Delete to remove it."
                    % (c.name or self.DIMENSION_CAPTIONS.get(c.kind,
                                                             "Dimension")))
            return
        self.selected_constraint = None

        eid, pid = self.pick(u, v)
        # while a constraint is waiting for its geometry, each click adds -
        # holding Ctrl to build up a selection would be busywork
        additive = (bool(modifiers & QtCore.Qt.ControlModifier)
                    or self._pending_constraint is not None)
        if not additive:
            self.selected_entities = []
            self.selected_points = []
            self._midpoint_picks = set()
        if pid is not None:
            if pid in self.selected_points:
                self.selected_points.remove(pid)
            else:
                self.selected_points.append(pid)
        elif eid is not None:
            if eid in self.selected_entities:
                self.selected_entities.remove(eid)
                self._midpoint_picks.discard(eid)
            else:
                self.selected_entities.append(eid)
                if self._picked_midpoint(eid, u, v):
                    self._midpoint_picks.add(eid)
        self.render()
        self._check_pending_constraint()

    def _tool_line(self, point, modifiers) -> None:
        if not self._pending:
            self._pending = [point]
            self._start_target = self._snap_target(*point)
            return
        start = self._pending[-1]
        if math.dist(start, point) < 1e-7:
            return
        end_target = self._snap_target(*point)
        eid = self.sketch.add_line(start, point)
        self.sketch.auto_constrain_new([eid], AXIS_SNAP_DEG)
        self._attach(self._endpoint_of(eid, start), self._start_target)
        self._attach(self._endpoint_of(eid, point), end_target)
        self._pending = [point]
        self._start_target = end_target
        self._touch()

    def _tool_rect(self, point, modifiers) -> None:
        if not self._pending:
            self._pending = [point]
            self._start_target = self._snap_target(*point)
            return
        p1 = self._pending[0]
        if abs(p1[0] - point[0]) < 1e-7 or abs(p1[1] - point[1]) < 1e-7:
            return
        end_target = self._snap_target(*point)
        ids = self.sketch.add_rectangle(p1, point)
        if ids:
            self._attach(self._endpoint_of(ids[0], p1), self._start_target)
            self._attach(self._endpoint_of(ids[1], point), end_target)
        self._pending = []
        self._start_target = None
        self._touch()

    def _tool_circle(self, point, modifiers) -> None:
        if not self._pending:
            self._pending = [point]
            self._start_target = self._snap_target(*point)
            return
        centre = self._pending[0]
        radius = math.dist(centre, point)
        if radius < 1e-7:
            return
        eid = self.sketch.add_circle(centre, radius)
        self._attach(self.sketch.entities[eid].points[0], self._start_target)
        self._pending = []
        self._start_target = None
        self._touch()

    def _tool_arc(self, point, modifiers) -> None:
        self._pending.append(point)
        if len(self._pending) < 3:
            return
        centre, start, end = self._pending[:3]
        radius = math.dist(centre, start)
        if radius < 1e-7:
            self._pending = []
            return
        a0 = math.atan2(start[1] - centre[1], start[0] - centre[0])
        a1 = math.atan2(end[1] - centre[1], end[0] - centre[0])
        self.sketch.add_arc(centre, radius, a0, a1)
        self._pending = []
        self._touch()

    def _tool_polygon(self, point, modifiers) -> None:
        if not self._pending:
            self._pending = [point]
            return
        centre = self._pending[0]
        radius = math.dist(centre, point)
        if radius < 1e-7:
            return
        rotation = math.atan2(point[1] - centre[1], point[0] - centre[0])
        self.sketch.add_polygon(centre, radius, self.polygon_sides, rotation)
        self._pending = []
        self._touch()

    def _tool_slot(self, point, modifiers) -> None:
        self._pending.append(point)
        if len(self._pending) < 3:
            return
        p1, p2, edge = self._pending[:3]
        width = 2.0 * _dist_point_segment(edge, p1, p2)
        if width < 1e-6:
            self._pending = []
            return
        self.sketch.add_slot(p1, p2, width)
        self._pending = []
        self._touch()

    # -- rectangle variants ------------------------------------------------

    @staticmethod
    def _rect_corners(a, b, through) -> list:
        """Four corners from one edge and a point setting the width."""
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        if length < 1e-7:
            return []
        ux, uy = dx / length, dy / length
        nx, ny = -uy, ux
        width = (through[0] - a[0]) * nx + (through[1] - a[1]) * ny
        if abs(width) < 1e-7:
            return []
        return [a, b, (b[0] + nx * width, b[1] + ny * width),
                (a[0] + nx * width, a[1] + ny * width)]

    def _tool_rect3(self, point, modifiers) -> None:
        self._pending.append(point)
        if len(self._pending) < 3:
            return
        corners = self._rect_corners(*self._pending[:3])
        self._pending = []
        if corners:
            self.sketch.add_quad(corners)
            self._touch()

    def _tool_rect_centre(self, point, modifiers) -> None:
        if not self._pending:
            self._pending = [point]
            self._start_target = self._snap_target(*point)
            return
        centre = self._pending[0]
        half = (point[0] - centre[0], point[1] - centre[1])
        self._pending = []
        self._start_target = None
        if abs(half[0]) < 1e-7 or abs(half[1]) < 1e-7:
            return
        self.sketch.add_rectangle(
            (centre[0] - half[0], centre[1] - half[1]),
            (centre[0] + half[0], centre[1] + half[1]))
        self._touch()

    def _tool_rect3_centre(self, point, modifiers) -> None:
        self._pending.append(point)
        if len(self._pending) < 3:
            return
        centre, edge, through = self._pending[:3]
        self._pending = []
        dx, dy = edge[0] - centre[0], edge[1] - centre[1]
        half = math.hypot(dx, dy)
        if half < 1e-7:
            return
        ux, uy = dx / half, dy / half
        nx, ny = -uy, ux
        wide = abs((through[0] - centre[0]) * nx + (through[1] - centre[1]) * ny)
        if wide < 1e-7:
            return
        corners = [
            (centre[0] - ux * half - nx * wide, centre[1] - uy * half - ny * wide),
            (centre[0] + ux * half - nx * wide, centre[1] + uy * half - ny * wide),
            (centre[0] + ux * half + nx * wide, centre[1] + uy * half + ny * wide),
            (centre[0] - ux * half + nx * wide, centre[1] - uy * half + ny * wide),
        ]
        self.sketch.add_quad(corners)
        self._touch()

    # -- slot variants -------------------------------------------------------

    def _tool_slot_overall(self, point, modifiers) -> None:
        """Both clicks are the outer ends, so the width shortens the centres."""
        self._pending.append(point)
        if len(self._pending) < 3:
            return
        p1, p2, edge = self._pending[:3]
        self._pending = []
        width = 2.0 * _dist_point_segment(edge, p1, p2)
        length = math.dist(p1, p2)
        if width < 1e-6 or length <= width:
            self.status_changed.emit(
                "Slot: the overall length has to be longer than the width.")
            return
        r = width / 2.0
        ux, uy = (p2[0] - p1[0]) / length, (p2[1] - p1[1]) / length
        self.sketch.add_slot((p1[0] + ux * r, p1[1] + uy * r),
                             (p2[0] - ux * r, p2[1] - uy * r), width)
        self._touch()

    def _tool_slot_centre(self, point, modifiers) -> None:
        """First click is the middle of the slot, second is one end centre."""
        self._pending.append(point)
        if len(self._pending) < 3:
            return
        centre, end, edge = self._pending[:3]
        self._pending = []
        other = (2.0 * centre[0] - end[0], 2.0 * centre[1] - end[1])
        width = 2.0 * _dist_point_segment(edge, other, end)
        if width < 1e-6 or math.dist(other, end) < 1e-6:
            return
        self.sketch.add_slot(other, end, width)
        self._touch()

    def _tool_slot_arc3(self, point, modifiers) -> None:
        self._pending.append(point)
        if len(self._pending) < 4:
            return
        start, end, through, edge = self._pending[:4]
        self._pending = []
        self._build_arc_slot(_circle_through(start, through, end), start, end,
                             edge)

    def _tool_slot_arc_centre(self, point, modifiers) -> None:
        self._pending.append(point)
        if len(self._pending) < 4:
            return
        centre, start, end, edge = self._pending[:4]
        self._pending = []
        radius = math.dist(centre, start)
        if radius < 1e-7:
            return
        self._build_arc_slot((centre, radius), start, end, edge)

    def _build_arc_slot(self, circle, start, end, edge) -> None:
        if circle is None:
            self.status_changed.emit(
                "Slot: those three points are in a straight line, so there "
                "is no arc through them.")
            return
        centre, radius = circle
        width = 2.0 * abs(math.dist(centre, edge) - radius)
        if width < 1e-6 or radius <= width / 2.0:
            self.status_changed.emit(
                "Slot: that width does not fit on an arc of this radius.")
            return
        a0, a1 = self._slot_angles(centre, start, end)
        if self.sketch.add_arc_slot(centre, radius, a0, a1, width):
            self._touch()

    @staticmethod
    def _slot_angles(centre, start, end) -> Tuple[float, float]:
        """The start and end angle of an arc slot, the short way round."""
        a0 = math.atan2(start[1] - centre[1], start[0] - centre[0])
        a1 = math.atan2(end[1] - centre[1], end[0] - centre[0])
        if a1 < a0:
            a1 += 2.0 * math.pi
        if a1 - a0 > math.pi:
            a0, a1 = a1 - 2.0 * math.pi, a0
        return a0, a1

    def _tool_spline(self, point, modifiers) -> None:
        self._pending.append(point)
        self._render_preview()

    def finish_spline(self) -> None:
        if self.active and self.tool == "spline" and len(self._pending) >= 2:
            self.sketch.add_spline(self._pending)
            self._pending = []
            self._touch()

    def _tool_point(self, point, modifiers) -> None:
        self.sketch.add_point(point[0], point[1])
        self._touch()

    def _tool_trim(self, point, modifiers) -> None:
        eid, _pid = self.pick(*point)
        if eid is None:
            return
        self._trim_entity(eid, point)
        self._touch()

    def _tool_offset(self, point, modifiers) -> None:
        """Pick a curve, then place its loop's copy where the cursor is.

        The first click takes the line, arc or circle and everything joined
        to it end to end, the way Inventor's offset takes the loop.  From
        then on the copy rides along under the cursor on whichever side it
        is pulled to, so how it will look is seen before it is made.  A
        click puts it there; a distance typed in the box beside the cursor
        fixes the gap and Enter puts it there with that dimension on it.
        """
        if not self._pending:
            eid, _ = self.pick(*point)
            if eid is None:
                return
            if self.sketch.entities[eid].kind not in ("line", "arc", "circle"):
                self._complain("Offset works on lines, arcs and circles.")
                return
            chain, closed = self.sketch.offset_chain(eid)
            if not chain:
                return
            self._offset_chain = (chain, closed)
            self._pending = [point]
            return
        held = getattr(self, "_offset_chain", None)
        if held is None:
            self._pending = []
            return
        chain, closed = held
        d = self._offset_gap(point)
        if abs(d) < 1e-9:
            return
        made = self.sketch.add_offset(chain, closed, d)
        if made is None:
            self._complain("That is too far in for a copy of the same shape.")
            return
        self._offset_made = made
        self._pending = []
        self._offset_chain = None
        self._touch()

    def _offset_gap(self, cursor) -> float:
        """The offset the cursor asks for, or the one typed, on its side."""
        held = getattr(self, "_offset_chain", None)
        if held is None or self.sketch is None:
            return 0.0
        d = self.sketch.offset_distance(held[0], cursor)
        locked = self.live.locked_values() if self.live.isVisible() else []
        if locked and locked[0]:
            try:
                typed = evaluate(unitlib.for_storage(locked[0], self.units,
                                                     unitlib.LENGTH),
                                 self.params.scope() if self.params else {})
            except (ExpressionError, TypeError):
                return d
            return math.copysign(abs(typed), d or 1.0)
        return d

    def _preview_offset(self, colour) -> None:
        """The copy as it would be made, riding on the cursor."""
        held = getattr(self, "_offset_chain", None)
        if held is None or self.sketch is None:
            return
        chain, closed = held
        s = self.sketch
        # the loop being copied, so it is plain what was picked
        for eid, _ in chain:
            pts = s.entity_polyline(eid)
            for a, b in zip(pts, pts[1:]):
                self.viewport.draw_edge(self._to3d(a), self._to3d(b),
                                        C.sketch_hover, 2.2, preview=True)
        d = self._offset_gap(self._cursor)
        layout = s.offset_layout(chain, closed, d) if abs(d) > 1e-9 else None
        if layout is None:
            return
        joints, radii = layout["joints"], layout["radii"]
        if not joints:
            centre = s.points[s.entities[chain[0][0]].points[0]].as_tuple()
            self._preview_circle(centre, radii[0], colour)
            return
        for k, (eid, forward) in enumerate(chain):
            a = joints[k]
            b = joints[(k + 1) % len(joints)] if closed else joints[k + 1]
            entity = s.entities[eid]
            if entity.kind == "line":
                self.viewport.draw_edge(self._to3d(a), self._to3d(b), colour,
                                        1.8, preview=True)
            else:
                centre = s.points[entity.points[0]].as_tuple()
                start, end = (a, b) if forward else (b, a)
                self._preview_arc(centre, radii[k], start, end, colour)

    def _tool_fillet2d(self, point, modifiers) -> None:
        eid, _pid = self.pick(*point)
        if eid is None or self.sketch.entities[eid].kind != "line":
            return
        self._pending.append(eid)      # entity ids, not coordinates
        if len(self._pending) < 2:
            return
        first, second = self._pending[0], self._pending[1]
        self._pending = []
        if first == second:
            return
        radius, ok = QtWidgets.QInputDialog.getDouble(
            self.viewport, "Sketch Fillet", "Radius:", 3.0, 0.001, 1e6, 3)
        if ok:
            self._fillet_lines(first, second, radius)
        self._touch()

    def _tool_dimension(self, point, modifiers) -> None:
        """Pick what to measure, then place the label, then type the value."""
        if self._dim_target is not None:
            # A line on its own is a length, so one pick is already something
            # measurable and the tool arms immediately.  That made everything
            # needing two picks unreachable: a line and a point, or two lines,
            # could never be asked for, because the second pick was read as
            # placing the label.  So a pick that lands on geometry the current
            # target does not already cover means they are still choosing what
            # to measure, and only a click clear of it places the label.
            more = self._extra_dimension_pick(point)
            if more is not None:
                self._pending = self._dim_picks + [more]
                target = self._dimension_target()
                if target is not None:
                    self._dim_picks = list(self._pending)
                    self._pending = []
                    self._dim_target = target
                    self._retarget(target, point)
                    self._dim_offset = self._dimension_offset(target, point)
                    self.hint_changed.emit(self._dimension_hint(target))
                else:
                    self._pending = []
                    self.hint_changed.emit(
                        "Those two cannot be dimensioned together. Pick two "
                        "points, two lines, or a line and a point.")
                return

            # a click clear of the geometry: put the label there
            self._dim_offset = self._dimension_offset(self._dim_target, point)
            target = self._dim_target
            self._ask_value(
                self._shown(target["kind"], target["current"]),
                lambda text, t=target, off=self._dim_offset:
                    self._apply_new_dimension(t, off, text),
                caption=self.DIMENSION_CAPTIONS.get(target["kind"],
                                                    "Distance"))
            self._dim_target = None
            self._dim_picks = []
            return

        eid, pid = self.pick(*point)
        if pid is not None:
            self._pending.append(("point", pid))
        elif eid is not None:
            self._pending.append(("entity", eid))
        else:
            return

        target = self._dimension_target()
        if target is None and len(self._pending) >= 2:
            self._pending = []
            self.hint_changed.emit(
                "Those two cannot be dimensioned together. Pick two points, "
                "two parallel lines, a line and a point, or one line, arc or "
                "circle.")
            return

        if target is not None:
            self._dim_picks = list(self._pending)
            self._pending = []
            self._dim_target = target
            self._retarget(target, point)
            self._dim_offset = self._dimension_offset(target, point)
            self.hint_changed.emit(self._dimension_hint(target))

    def _extra_dimension_pick(self, point):
        """A pick that adds to what is being measured, or None to place.

        Only geometry the armed target does not already account for counts.
        Clicking the very line whose length is being dimensioned is somebody
        putting the label on it, not asking to measure it against itself.
        """
        if not self._dim_picks:
            return None
        eid, pid = self.pick(*point)
        if pid is None and eid is None:
            return None

        seen_entities = {i for k, i in self._dim_picks if k == "entity"}
        seen_points = {i for k, i in self._dim_picks if k == "point"}
        # the ends of a line already picked are part of that line, so
        # clicking one of them is not a new thing to measure against
        for e in seen_entities:
            entity = self.sketch.entities.get(e)
            if entity is not None:
                seen_points.update(entity.points)

        if pid is not None and pid not in seen_points:
            return ("point", pid)
        if pid is None and eid is not None and eid not in seen_entities:
            return ("entity", eid)
        return None

    # -- what the current selection can be dimensioned as --------------------

    def _dimension_target(self) -> Optional[Dict[str, Any]]:
        """Work out what the picks so far describe, or None if not yet."""
        s = self.sketch
        kinds = [k for k, _ in self._pending]

        if kinds == ["entity"]:
            ent = s.entities[self._pending[0][1]]
            if ent.kind in ("circle", "arc"):
                return {"kind": "diameter", "points": [],
                        "entities": [ent.id], "axial": False,
                        "current": ent.radius * 2.0}
            if ent.kind == "line":
                # a single line is its own two ends, so it switches between
                # aligned, horizontal and vertical like any pair of points
                return self._point_pair(ent.points[0], ent.points[1])
            return None

        if len(self._pending) < 2:
            return None

        if kinds == ["point", "point"]:
            return self._point_pair(self._pending[0][1], self._pending[1][1])

        if kinds in (["entity", "point"], ["point", "entity"]):
            first_is_entity = kinds[0] == "entity"
            eid = self._pending[0][1] if first_is_entity else self._pending[1][1]
            pid = self._pending[1][1] if first_is_entity else self._pending[0][1]
            if s.entities[eid].kind != "line":
                return None
            return self._point_to_line(pid, eid)

        if kinds == ["entity", "entity"]:
            first, second = self._pending[0][1], self._pending[1][1]
            if first == second:
                return None
            round_kinds = ("circle", "arc")
            kind_a, kind_b = s.entities[first].kind, s.entities[second].kind
            if kind_a in round_kinds and kind_b in round_kinds:
                return self._between_circles(first, second)
            if "line" in (kind_a, kind_b) and (kind_a in round_kinds
                                               or kind_b in round_kinds):
                # a circle and a line: its centre square onto the line
                line, circle = ((first, second) if kind_a == "line"
                                else (second, first))
                return self._point_to_line(s.entities[circle].points[0], line)
            if kind_a != "line" or kind_b != "line":
                return None
            if self._parallel(first, second):
                # two parallel lines measure across the gap, from one line's
                # end square onto the other
                return self._point_to_line(s.entities[second].points[0], first)
            # ordered so the angle reads between 0 and 180: the same
            # arrangement either way round, but the number shown is the one
            # people expect to see
            angle = self._angle_between(first, second)
            if angle > 180.0:
                first, second = second, first
                angle = self._angle_between(first, second)
            return {"kind": "angle", "points": [],
                    "entities": [first, second], "axial": False,
                    "current": angle}

        return None

    def _between_circles(self, first: int, second: int) -> Dict[str, Any]:
        """Two circles or arcs: across the ring if they share a centre.

        Concentric circles measure the gap between them, outer radius less
        inner, the way two parallel lines measure across theirs; the inner
        one goes first, so the value typed is the value seen and neither
        circle jumps through the other to make it. Two that do not share a
        centre measure from centre to centre.
        """
        s = self.sketch
        a, b = s.entities[first], s.entities[second]
        ca, cb = s.points[a.points[0]], s.points[b.points[0]]
        size = max(a.radius, b.radius, 1.0)
        if a.points[0] == b.points[0] or \
                math.hypot(ca.x - cb.x, ca.y - cb.y) <= 1e-6 * size:
            inner, outer = ((first, second) if a.radius <= b.radius
                            else (second, first))
            gap = s.entities[outer].radius - s.entities[inner].radius
            return {"kind": "radial_gap", "points": [],
                    "entities": [inner, outer], "axial": False,
                    "current": gap}
        return self._point_pair(a.points[0], b.points[0])

    def _point_pair(self, first: int, second: int) -> Dict[str, Any]:
        a, b = self.sketch.points[first], self.sketch.points[second]
        return {"kind": "distance", "points": [first, second], "entities": [],
                "axial": True,
                "current": math.hypot(b.x - a.x, b.y - a.y)}

    def _point_to_line(self, pid: int, eid: int) -> Dict[str, Any]:
        """Measure a point square onto a line.

        The line goes in as its two ends, ordered so the point comes out on
        the positive side.  That way the value the user types is the value
        they see, and the point is held where they put it rather than
        jumping through the line to the same distance on the far side.
        """
        ends = list(self.sketch.entities[eid].points)
        if self._perpendicular(pid, ends[0], ends[1]) < 0:
            ends.reverse()
        return {"kind": "distance_pl", "points": [pid, ends[0], ends[1]],
                "entities": [], "axial": False,
                "current": self._perpendicular(pid, ends[0], ends[1])}

    def _perpendicular(self, pid: int, first: int, second: int) -> float:
        """Signed distance from a point to the line through two others."""
        s = self.sketch
        p = s.points[pid]
        a, b = s.points[first], s.points[second]
        dx, dy = b.x - a.x, b.y - a.y
        length = math.hypot(dx, dy)
        if length < 1e-9:
            return 0.0
        return ((p.x - a.x) * dy - (p.y - a.y) * dx) / length

    def _line_direction(self, eid: int) -> Tuple[float, float]:
        s = self.sketch
        ent = s.entities[eid]
        a, b = s.points[ent.points[0]], s.points[ent.points[1]]
        dx, dy = b.x - a.x, b.y - a.y
        length = math.hypot(dx, dy) or 1.0
        return (dx / length, dy / length)

    def _parallel(self, first: int, second: int,
                  tolerance_deg: float = 1.0) -> bool:
        ux, uy = self._line_direction(first)
        vx, vy = self._line_direction(second)
        return abs(ux * vy - uy * vx) <= math.sin(math.radians(tolerance_deg))

    def _angle_between(self, first: int, second: int) -> float:
        """The directed angle from the first line to the second, 0 to 360.

        Directed, because that is what the constraint holds: folding it to
        the acute angle for display would mean typing the number shown and
        watching the geometry swing round to some other arrangement that
        also measures it.
        """
        ux, uy = self._line_direction(first)
        vx, vy = self._line_direction(second)
        return math.degrees(math.atan2(ux * vy - uy * vx,
                                       ux * vx + uy * vy)) % 360.0

    # -- which linear dimension the placement is asking for ------------------

    def _retarget(self, target: Dict[str, Any],
                  cursor: Tuple[float, float]) -> None:
        """Switch between aligned, horizontal and vertical as you place it.

        Inventor decides from where the label is going, not from a mode set
        beforehand: drag out above or below the two points and you get the
        horizontal distance, out to one side and you get the vertical, and
        anywhere else the direct one between them.  The span checks stop it
        offering a measurement that is flat zero.
        """
        if not target.get("axial"):
            return
        s = self.sketch
        a, b = s.points[target["points"][0]], s.points[target["points"][1]]
        x0, x1 = sorted((a.x, b.x))
        y0, y1 = sorted((a.y, b.y))
        span_x, span_y = x1 - x0, y1 - y0
        inside_x = x0 <= cursor[0] <= x1
        inside_y = y0 <= cursor[1] <= y1

        kind = "distance"
        if inside_x and not inside_y and span_x > 1e-6:
            kind = "distance_x"
        elif inside_y and not inside_x and span_y > 1e-6:
            kind = "distance_y"

        # A horizontal or vertical dimension measures b minus a, which is
        # signed, so the pair has to be ordered to match the number being
        # shown.  Pick the two points right to left and the label still reads
        # a positive 10, but the constraint asks for b - a = +10 and drags
        # the geometry through to the other side to get it - the jump that
        # only a typed -10 used to undo.
        first, second = target["points"]
        if kind == "distance_x" and b.x < a.x:
            first, second = second, first
        elif kind == "distance_y" and b.y < a.y:
            first, second = second, first
        target["points"] = [first, second]

        target["kind"] = kind
        if kind == "distance_x":
            target["current"] = span_x
        elif kind == "distance_y":
            target["current"] = span_y
        else:
            target["current"] = math.hypot(b.x - a.x, b.y - a.y)

    DIMENSION_CAPTIONS = {
        "diameter": "Diameter",
        "angle": "Angle",
        "distance_x": "Horizontal",
        "distance_y": "Vertical",
        "distance_pl": "Perpendicular",
        "radial_gap": "Distance",
        "distance": "Distance",
    }

    def _dimension_hint(self, target: Dict[str, Any]) -> str:
        if target.get("axial"):
            return ("Move to place the dimension - above or below for the "
                    "horizontal, out to the side for the vertical, anywhere "
                    "else for the direct one. Click to set it.")
        if target["kind"] == "distance_pl":
            return "Perpendicular distance. Move to place it, then click."
        if target["kind"] == "angle":
            return "Angle between the two lines. Move to place it, then click."
        if target["kind"] == "radial_gap":
            return ("Distance between the two circles. Move to place it, "
                    "then click.")
        return "Move to place the dimension, then click to set it."

    def _dimension_offset(self, target: Dict[str, Any],
                          cursor: Tuple[float, float]) -> Tuple[float, float]:
        """Where the label goes, constrained to the sketch plane.

        A linear dimension only slides perpendicular to the line it measures,
        which is what keeps the witness lines square; a diameter follows the
        cursor radially out from the centre.
        """
        s = self.sketch
        try:
            if target["kind"] == "diameter":
                ent = s.entities[target["entities"][0]]
                centre = s.points[ent.points[0]]
                dx, dy = cursor[0] - centre.x, cursor[1] - centre.y
                length = math.hypot(dx, dy)
                if length < 1e-9:
                    return (ent.radius, 0.0)
                reach = max(length, ent.radius * 0.6)
                return (dx / length * reach, dy / length * reach)

            if target["kind"] == "angle":
                ent = s.entities[target["entities"][0]]
                a = s.points[ent.points[0]]
                return (cursor[0] - a.x, cursor[1] - a.y)

            if target["kind"] == "radial_gap":
                outer = s.entities[target["entities"][1]]
                centre = s.points[outer.points[0]]
                dx, dy = cursor[0] - centre.x, cursor[1] - centre.y
                length = math.hypot(dx, dy)
                if length < 1e-9:
                    return (outer.radius * 1.15, 0.0)
                reach = max(length, outer.radius * 1.15)
                return (dx / length * reach, dy / length * reach)

            if target["kind"] == "distance_pl":
                # the label slides along the line it measures from, keeping
                # the witness lines square to it
                points = target["points"]
                ux, uy = self._points_direction(points[1], points[2])
                mid = self._perpendicular_mid(points)
                reach = (cursor[0] - mid[0]) * ux + (cursor[1] - mid[1]) * uy
                return (ux * reach, uy * reach)

            a = s.points[target["points"][0]]
            b = s.points[target["points"][1]]
            mid = ((a.x + b.x) / 2.0, (a.y + b.y) / 2.0)

            # a horizontal dimension is drawn above or below what it
            # measures, so it slides vertically; a vertical one the reverse
            if target["kind"] == "distance_x":
                return (0.0, cursor[1] - mid[1])
            if target["kind"] == "distance_y":
                return (cursor[0] - mid[0], 0.0)

            dx, dy = b.x - a.x, b.y - a.y
            length = math.hypot(dx, dy)
            if length < 1e-9:
                return (0.0, 6.0)
            nx, ny = -dy / length, dx / length
            reach = (cursor[0] - mid[0]) * nx + (cursor[1] - mid[1]) * ny
            return (nx * reach, ny * reach)
        except (KeyError, IndexError):
            return (0.0, 6.0)

    def _perpendicular_ends(self, points):
        """The point being measured, and its foot on the line."""
        s = self.sketch
        p = s.points[points[0]]
        a, b = s.points[points[1]], s.points[points[2]]
        dx, dy = b.x - a.x, b.y - a.y
        length = math.hypot(dx, dy)
        if length < 1e-9:
            return ((p.x, p.y), (a.x, a.y))
        t = ((p.x - a.x) * dx + (p.y - a.y) * dy) / (length * length)
        return ((p.x, p.y), (a.x + dx * t, a.y + dy * t))

    def _perpendicular_mid(self, points):
        ends = self._perpendicular_ends(points)
        return ((ends[0][0] + ends[1][0]) / 2.0,
                (ends[0][1] + ends[1][1]) / 2.0)

    def _points_direction(self, first: int, second: int):
        s = self.sketch
        a, b = s.points[first], s.points[second]
        dx, dy = b.x - a.x, b.y - a.y
        length = math.hypot(dx, dy) or 1.0
        return (dx / length, dy / length)

    def _apply_new_dimension(self, target: Dict[str, Any],
                             offset: Tuple[float, float], text: str) -> None:
        factor = self._autoscale_factor(target, text)
        if factor is not None:
            self._apply_scaled_dimension(target, offset, text, factor)
            return
        cid = self._add_dimension(target["kind"], target["points"],
                                  target["entities"], target["current"], text)
        if cid is not None:
            self.sketch.constraints[cid].label_offset = offset
            self._touch()

    # dimensions that are a length, the kind a first one can size a sketch by
    AUTOSCALE_KINDS = ("distance", "distance_x", "distance_y", "distance_pl",
                       "radius", "diameter", "radial_gap")

    def _autoscale_factor(self, target: Dict[str, Any],
                          text: str) -> Optional[float]:
        """How much the first dimension of a fresh sketch scales it by.

        Inventor's way: a first sketch is drawn at whatever size the screen
        happened to show, so the first length typed into it says what size
        it was meant to be, and the whole sketch grows or shrinks to that,
        keeping its shape.  A 10 m wall typed onto a 40 mm square then
        comes out a 10 m square, framed on the screen, instead of one line
        stretched out of sight.  Only the first dimension, only while there
        is no body yet to keep in proportion with, and never a sketch with
        anything held in place.
        """
        s = self.sketch
        if (s is None or not self.autoscale
                or target.get("kind") not in self.AUTOSCALE_KINDS):
            return None
        current = float(target.get("current") or 0.0)
        if current <= 1e-9:
            return None
        if any(c.is_dimension for c in s.constraints.values()):
            return None
        if s.projected_entities() or s.projections:
            return None
        if any(p.fixed and not p.origin for p in s.points.values()):
            return None
        try:
            value, _ = self._typed(target["kind"], (text or "").strip())
        except (ExpressionError, TypeError, ValueError):
            return None
        if value <= 1e-9:
            return None
        factor = value / current
        return None if abs(factor - 1.0) < 1e-6 else factor

    def _scale_sketch(self, factor: float) -> None:
        """Grow or shrink the whole sketch about its origin."""
        s = self.sketch
        for point in s.points.values():
            if not point.origin:
                point.x *= factor
                point.y *= factor
        for entity in s.entities.values():
            if entity.kind in ("circle", "arc"):
                entity.radius *= factor
        for c in s.constraints.values():
            c.label_offset = (c.label_offset[0] * factor,
                              c.label_offset[1] * factor)

    def _apply_scaled_dimension(self, target: Dict[str, Any],
                                offset: Tuple[float, float], text: str,
                                factor: float) -> None:
        """Scale the sketch to its first dimension, then put that on."""
        self.begin_change()              # one undo takes both back
        self._scale_sketch(factor)
        cid = self._add_dimension(target["kind"], target["points"],
                                  target["entities"],
                                  target["current"] * factor, text)
        if cid is None:
            self._scale_sketch(1.0 / factor)
            self.discard_change()
            self.render()
            return
        self.discard_change()            # the dimension's own step
        self.sketch.constraints[cid].label_offset = (offset[0] * factor,
                                                     offset[1] * factor)
        self._touch()
        self.viewport.fit_all()
        self.viewport.refresh_grid()
        self.render()

    def _add_dimension(self, kind: str, points, entities, current: float,
                       text: str) -> Optional[int]:
        """Create a driving dimension, backing out if it over-constrains."""
        text = (text or "").strip()
        if not text:
            return None
        self.begin_change()
        try:
            value, expression = self._typed(kind, text)
        except ExpressionError as exc:
            self._complain("That is not a valid value: %s" % exc)
            return None

        cid = self.sketch.add_constraint(kind, points=list(points or []),
                                         entities=list(entities or []),
                                         value=value, expression=expression)
        self.solve()
        if self.sketch.solve_message.startswith("over"):
            self.sketch.remove_constraint(cid)
            self.solve()
            self.discard_change()
            self._complain("That dimension conflicts with the constraints "
                           "already on the sketch, so it was not added.")
            return None
        return cid

    # -- the value popup ----------------------------------------------------

    def _insert_dimension_name(self, cid: int) -> bool:
        """Put a dimension's name into the value box at the cursor."""
        c = self.sketch.constraints.get(cid) if self.sketch else None
        if c is None or not c.name:
            return False
        if c.id == getattr(self, "_editing_dimension", None):
            self.hint_changed.emit(
                "A dimension cannot be written in terms of itself.")
            return True
        self.value_popup.insert(c.name)
        self.hint_changed.emit(
            "%s is %s. Finish the expression and press Enter."
            % (c.name, self._dimension_text(c)))
        return True

    def _dimension_text(self, c) -> str:
        value = self._dimension_value(c)
        if c.kind == "angle":
            return "%s deg" % unitlib.fmt(value)
        return unitlib.length_text(value, self.units)

    def _ask_value(self, initial: str, apply: Callable[[str], None],
                   caption: str = "") -> None:
        self._value_apply = apply
        self.value_popup.ask(initial, QtGui.QCursor.pos(), caption)

    def _value_entered(self, text: str) -> None:
        apply = self._value_apply
        self._value_apply = None
        self._editing_dimension = None
        if apply is not None:
            apply(text)
        self.viewport.setFocus()

    def _value_cancelled(self) -> None:
        self._value_apply = None
        self._editing_dimension = None
        self._dim_target = None
        self._dim_picks = []
        self.viewport.clear_preview()
        self.viewport.setFocus()
        self.render()

    # ------------------------------------------------- geometry operations

    def _fillet_lines(self, e1: int, e2: int, radius: float) -> None:
        """Round the corner where two lines meet, trimming both back."""
        s = self.sketch
        a, b = s.entities[e1], s.entities[e2]
        shared = set(a.points) & set(b.points)
        if not shared:
            return
        corner_id = shared.pop()
        corner = s.points[corner_id]
        far_a = s.points[[p for p in a.points if p != corner_id][0]]
        far_b = s.points[[p for p in b.points if p != corner_id][0]]

        def unit(p):
            dx, dy = p.x - corner.x, p.y - corner.y
            n = math.hypot(dx, dy)
            return (dx / n, dy / n) if n > 1e-12 else (0.0, 0.0)

        ua, ub = unit(far_a), unit(far_b)
        cosang = max(-1.0, min(1.0, ua[0] * ub[0] + ua[1] * ub[1]))
        angle = math.acos(cosang)
        if angle < 1e-4 or abs(angle - math.pi) < 1e-4:
            return
        setback = radius / math.tan(angle / 2.0)
        if setback >= math.dist((corner.x, corner.y), (far_a.x, far_a.y)) or \
           setback >= math.dist((corner.x, corner.y), (far_b.x, far_b.y)):
            return

        ta = (corner.x + ua[0] * setback, corner.y + ua[1] * setback)
        tb = (corner.x + ub[0] * setback, corner.y + ub[1] * setback)
        bisector = ((ua[0] + ub[0]) / 2.0, (ua[1] + ub[1]) / 2.0)
        bl = math.hypot(*bisector)
        if bl < 1e-12:
            return
        centre_dist = radius / math.sin(angle / 2.0)
        centre = (corner.x + bisector[0] / bl * centre_dist,
                  corner.y + bisector[1] / bl * centre_dist)

        # move each line's corner endpoint back to its tangent point
        pa = s.add_point(*ta)
        pb = s.add_point(*tb)
        a.points = [p if p != corner_id else pa for p in a.points]
        b.points = [p if p != corner_id else pb for p in b.points]

        a0 = math.atan2(ta[1] - centre[1], ta[0] - centre[0])
        a1 = math.atan2(tb[1] - centre[1], tb[0] - centre[0])
        sweep = (a1 - a0) % (2 * math.pi)
        arc = s.add_arc(centre, radius, a0, a1)
        if sweep > math.pi:
            s.entities[arc].ccw = False

        arc_ent = s.entities[arc]
        s.add_constraint("coincident", points=[arc_ent.points[1], pa])
        s.add_constraint("coincident", points=[arc_ent.points[2], pb])
        s.add_constraint("tangent", entities=[e1, arc])
        s.add_constraint("tangent", entities=[e2, arc])
        s._prune_points()

    def _trim_entity(self, eid: int, point) -> None:
        """Split the entity at its intersections and drop the clicked piece."""
        s = self.sketch
        ent = s.entities[eid]
        if ent.kind == "line":
            a = s.points[ent.points[0]]
            b = s.points[ent.points[1]]
            cuts = sorted(self._line_cuts(eid))
            if not cuts:
                s.remove_entity(eid)
                return
            dx, dy = b.x - a.x, b.y - a.y
            length2 = dx * dx + dy * dy
            t_click = ((point[0] - a.x) * dx + (point[1] - a.y) * dy) / length2
            bounds = [0.0] + cuts + [1.0]
            keep = []
            for i in range(len(bounds) - 1):
                lo, hi = bounds[i], bounds[i + 1]
                if hi - lo < 1e-6:
                    continue
                if lo <= t_click <= hi:
                    continue
                keep.append((lo, hi))
            s.remove_entity(eid)
            for lo, hi in keep:
                s.add_line((a.x + dx * lo, a.y + dy * lo),
                           (a.x + dx * hi, a.y + dy * hi), weld=False)
        else:
            s.remove_entity(eid)

    def _line_cuts(self, eid: int) -> List[float]:
        """Parameters along a line where other sketch entities cross it."""
        s = self.sketch
        ent = s.entities[eid]
        a = s.points[ent.points[0]]
        b = s.points[ent.points[1]]
        dx, dy = b.x - a.x, b.y - a.y
        cuts: List[float] = []

        for other_id, other in s.entities.items():
            if other_id == eid:
                continue
            poly = s.entity_polyline(other_id, 64)
            for i in range(len(poly) - 1):
                cx, cy = poly[i]
                ex, ey = poly[i + 1]
                denom = dx * (cy - ey) - dy * (cx - ex)
                if abs(denom) < 1e-12:
                    continue
                t = ((a.x - cx) * (cy - ey) - (a.y - cy) * (cx - ex)) / denom
                u = (dx * (a.y - cy) - dy * (a.x - cx)) / denom
                if 1e-6 < t < 1 - 1e-6 and -1e-6 <= u <= 1 + 1e-6:
                    if all(abs(t - c) > 1e-6 for c in cuts):
                        cuts.append(t)
        return cuts

    # ------------------------------------------------------- constraints

    # -- constraint requirements, so a tool can wait for the right picks ----

    NEEDS = {
        "horizontal": ("a line, or two points", 1, 0),
        "vertical": ("a line, or two points", 1, 0),
        "parallel": ("two lines", 2, 0),
        "collinear": ("two lines", 2, 0),
        "perpendicular": ("two lines", 2, 0),
        "equal": ("two edges", 2, 0),
        "tangent": ("a line and a circle, or two circles", 2, 0),
        "concentric": ("two circles or arcs", 2, 0),
        "coincident": ("two points, or a point and a line", 0, 2),
        "midpoint": ("a point and a line", 1, 1),
        "point_on": ("a point and an edge", 1, 1),
        "symmetric": ("two points and a mirror line", 1, 2),
        "ground": ("geometry to ground", 0, 0),
    }

    def start_constraint(self, kind: str) -> None:
        """Run a constraint, either on what is selected or on what comes next.

        Both orders work: pick the geometry then the constraint, or the
        constraint then the geometry. The second is what a tool palette
        normally implies, and it is what people reach for without thinking.
        """
        if not self.active or kind not in self.NEEDS:
            return
        if self._selection_satisfies(kind):
            self.apply_constraint(kind)
            self.clear_selection()

        # it stays armed for the next pair and the one after, until Esc,
        # because a sketch wants the same constraint a dozen times over
        self._pending_constraint = kind
        self.set_tool("select")
        self._pending_constraint = kind      # set_tool clears it, so re-arm
        self._constraint_hint(kind)
        self.constraint_armed.emit(kind)

    def _constraint_hint(self, kind: str) -> None:
        description, _e, _p = self.NEEDS[kind]
        self.hint_changed.emit("%s: select %s. It stays on for the next; "
                               "Esc when done."
                               % (kind.replace("_", " ").title(), description))

    def _selection_satisfies(self, kind: str) -> bool:
        need_entities, need_points = self.NEEDS[kind][1], self.NEEDS[kind][2]
        entities, points = len(self.selected_entities), len(self.selected_points)
        if kind == "ground":
            return bool(entities or points)
        if kind in ("horizontal", "vertical"):
            # a line, or two points to level with each other.  Lines picked
            # alongside points win: a line is what it would mean in Inventor
            return (any(self.sketch.entities[e].kind == "line"
                        for e in self.selected_entities
                        if e in self.sketch.entities)
                    or points >= 2)
        if kind == "coincident":
            # Inventor's Coincident does two jobs, and people reach for it
            # expecting both: two points merge, and a point against a curve
            # lands on it.  Taking only the first left the tool armed and
            # silent for half of what it was asked to do.
            return points >= 2 or (points >= 1 and entities >= 1)
        if need_points and need_entities:
            return entities >= need_entities and points >= need_points
        if need_points:
            return points >= need_points
        return entities >= need_entities

    def _check_pending_constraint(self) -> None:
        """After a pick, fire the armed constraint once it has enough."""
        kind = self._pending_constraint
        if kind is None:
            return
        if self._selection_satisfies(kind):
            self.apply_constraint(kind)
            self.clear_selection()
            # still armed: the next pick starts the next one
            self._pending_constraint = kind
            self._constraint_hint(kind)

    def apply_constraint(self, kind: str) -> None:
        """Apply a constraint to the current sketch selection."""
        if not self.active:
            return
        self.begin_change()
        s = self.sketch
        ents = list(self.selected_entities)
        pts = list(self.selected_points)
        before = s.to_dict()

        try:
            if kind in ("horizontal", "vertical"):
                lines = [e for e in ents if s.entities[e].kind == "line"]
                if lines:
                    for e in lines:
                        s.add_constraint(kind, entities=[e])
                elif len(pts) >= 2:
                    # every point levelled with the first one picked
                    for p in pts[1:]:
                        s.add_constraint(kind, points=[pts[0], p])
                else:
                    self._complain("Select a line, or two points.")
                    return
            elif kind in ("parallel", "collinear", "perpendicular", "equal",
                          "tangent", "concentric"):
                if len(ents) < 2:
                    self._complain("Select two pieces of geometry first.")
                    return
                for e in ents[1:]:
                    s.add_constraint(kind, entities=[ents[0], e])
            elif kind == "coincident":
                if len(pts) >= 2:
                    for p in pts[1:]:
                        s.add_constraint("coincident", points=[pts[0], p])
                elif len(pts) == 1 and ents:
                    # A point against a curve is point_on, which is the same
                    # thing Inventor calls Coincident when one side is an
                    # edge.  Picked on the line's middle it means the middle,
                    # which is a different constraint and the one people
                    # reach for when centring a rib or a hole on a face.
                    for e in ents:
                        if e in self._midpoint_picks:
                            s.add_constraint("midpoint", points=[pts[0]],
                                             entities=[e])
                        else:
                            s.add_constraint("point_on", points=[pts[0]],
                                             entities=[e])
                else:
                    self._complain(
                        "Select two points, or a point and a line.")
                    return
            elif kind == "midpoint":
                if len(pts) != 1 or len(ents) != 1:
                    self._complain("Select one point and one line.")
                    return
                s.add_constraint("midpoint", points=pts, entities=ents)
            elif kind == "point_on":
                if len(pts) != 1 or len(ents) != 1:
                    self._complain("Select one point and one edge.")
                    return
                s.add_constraint("point_on", points=pts, entities=ents)
            elif kind == "symmetric":
                if len(pts) != 2 or len(ents) != 1:
                    self._complain("Select two points and the mirror line.")
                    return
                s.add_constraint("symmetric", points=pts, entities=ents)
            elif kind in ("fix", "ground"):
                targets = pts or [p for e in ents for p in s.entities[e].points]
                if not targets:
                    self._complain("Select geometry to ground.")
                    return
                for p in targets:
                    point = s.points[p]
                    if point.origin:
                        continue        # the sketch origin is always grounded
                    point.fixed = not point.fixed
            else:
                return
        except ValueError as exc:
            self._complain(str(exc))
            return

        self.solve()
        if self.sketch.solve_message.startswith("over"):
            restored = Sketch.from_dict(before)
            self.sketch.points = restored.points
            self.sketch.entities = restored.entities
            self.sketch.constraints = restored.constraints
            self.solve()
            self._complain("That constraint conflicts with the existing ones.")
        self.render()
        self.changed.emit()

    def _complain(self, message: str) -> None:
        QtWidgets.QMessageBox.information(self.viewport, "Constraint", message)

    def delete_selected(self) -> None:
        if not self.active:
            return
        # a picked constraint goes on its own, leaving the geometry intact -
        # which is the whole point of being able to see them
        if self.selected_constraint is not None:
            self.delete_constraint(self.selected_constraint)
            return
        if not self.selected_entities and not self.selected_points:
            return
        self.begin_change()
        for eid in list(self.selected_entities):
            self.sketch.remove_entity(eid)
        for pid in list(self.selected_points):
            attached = [e for e in self.sketch.entities.values()
                        if pid in e.points]
            for e in attached:
                self.sketch.remove_entity(e.id)
        self.clear_selection()
        self._touch()

    def project_one(self, sub_shape, construction: bool = False,
                    body=None) -> int:
        """Project a single picked edge or face onto the sketch.

        The same flattening the whole-body version does, handed one piece
        of geometry instead of all of it, which is what makes Project
        Geometry a thing you point at rather than a thing that happens to
        everything at once.
        """
        if not self.active or sub_shape is None:
            return 0
        self.begin_change()
        source = self._reference_for(sub_shape, body)
        made = self._project_geometry(sub_shape, construction, source)
        if made:
            self._touch()
        else:
            self.discard_change()
        return made

    def _reference_for(self, sub_shape, body):
        """A ShapeRef dict naming this edge on the body it belongs to.

        Without it the projection is a one-off copy; with it the sketch can
        find the same edge again after the model has moved and re-cast the
        shadow onto its new position.
        """
        if body is None or sub_shape is None:
            return None
        try:
            from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
            from ..core.naming import ShapeRef

            kind = "edge"
            pool = kernel.explore(body, TopAbs_EDGE)
            index = _same_shape_index(pool, sub_shape)
            if index is None:
                kind = "face"
                pool = kernel.explore(body, TopAbs_FACE)
                index = _same_shape_index(pool, sub_shape)
            if index is None:
                return None
            return ShapeRef.capture(pool[index], kind, index,
                                    within=body).to_dict()
        except Exception:
            return None

    def project_geometry(self, shape, construction: bool = False) -> int:  # noqa: D401
        self.begin_change()
        return self._project_geometry(shape, construction)

    def _project_geometry(self, shape, construction: bool = False,
                          source=None) -> int:
        """Bring model edges onto the sketch plane as real sketch geometry.

        Everything is flattened along the plane normal, so an edge sitting at
        an angle projects to its shadow on the sketch - the same thing you
        would get by looking straight down at the model.
        """
        if not self.active or shape is None:
            return 0

        s = self.sketch
        created: List[int] = []
        for kind, data in kernel.project_to_plane(shape, s.plane):
            if kind == "line":
                a, b = data
                created.append(s.add_line(a, b, construction=construction))
            elif kind == "circle":
                centre, radius = data
                created.append(s.add_circle(centre, radius,
                                            construction=construction))
            elif kind == "arc":
                centre, radius, a0, a1 = data
                created.append(s.add_arc(centre, radius, a0, a1,
                                         construction=construction))
            elif kind == "polyline":
                points = data
                for i in range(len(points) - 1):
                    if math.dist(points[i], points[i + 1]) > 1e-7:
                        created.append(s.add_line(
                            points[i], points[i + 1],
                            construction=construction))

        if created:
            # projected geometry is reference geometry: pin it so the solver
            # treats it as given rather than something it may move
            for eid in created:
                for pid in s.entities[eid].points:
                    s.points[pid].fixed = True
            # Remember where it was cast from, so every later rebuild can
            # cast it again instead of leaving a shadow of a shape that has
            # since changed. Recorded even with no source to cast from: the
            # record is also what tells the solver this is a projection,
            # whose circles keep their radius.
            s.add_projection(source or {}, created, construction)
            self._touch()
        return len(created)

    def toggle_construction(self) -> None:
        if not self.active or not self.selected_entities:
            return
        self.begin_change()
        for eid in self.selected_entities:
            ent = self.sketch.entities[eid]
            ent.construction = not ent.construction
        self._touch()

    # -------------------------------------------------------------- render

    def _to3d(self, uv) -> Tuple[float, float, float]:
        return self.sketch.plane.to_3d(uv[0], uv[1])

    def render(self) -> None:
        vp = self.viewport
        vp.clear_overlay()
        if self.sketch is None:
            vp.redraw()
            return

        self._render_references()
        s = self.sketch
        conflicting = set()
        for cid in s.conflicting:
            c = s.constraints.get(cid)
            if c:
                conflicting.update(c.entities)

        for eid, ent in s.entities.items():
            # white once the geometry is pinned down, purple while it can
            # still move - the same convention Inventor uses
            if ent.construction:
                colour, width = C.sketch_construction, 1.4
            elif s.entity_constrained(eid):
                colour, width = C.sketch_line, 2.0
            else:
                colour, width = C.sketch_free, 2.0
            if eid in self.selected_entities:
                colour, width = C.sketch_picked, 2.8
            elif eid in conflicting:
                colour, width = C.error, 2.4
            elif eid == self._hover_entity:
                colour, width = C.sketch_hover, 2.6
            edge = kernel._sketch_edge(s, eid)
            if edge is not None:
                vp.draw_shape(edge, colour, width, dashed=ent.construction)

        # Points are drawn only when they matter: under the cursor, picked,
        # being dragged, or grounded.  Inventor shows a bare sketch as lines
        # alone, and a dot at every end is the difference between reading a
        # sketch and squinting at it.  Two kinds always matter, as they do
        # in Inventor: the centre of a circle or an arc, drawn as a cross,
        # and a point placed on its own, which is nothing but its dot.
        centres = {ent.points[0] for ent in s.entities.values()
                   if ent.kind in ("circle", "arc") and ent.points}
        used = {pid for ent in s.entities.values() for pid in ent.points}
        for pid, p in s.points.items():
            colour, size = None, 3.2
            marker = (Aspect_TypeOfMarker.Aspect_TOM_O_PLUS if p.origin
                      else Aspect_TypeOfMarker.Aspect_TOM_O)
            if pid == self._drag_point:
                magnetised = self._magnet is not None
                colour = C.sketch_magnet if magnetised else C.sketch_drag
                size = 6.5 if magnetised else 5.5
            elif pid in self.selected_points:
                colour, size = C.sketch_picked, 5.0
            elif pid == self._hover_point:
                colour, size = C.sketch_hover, 4.5
            elif p.origin:
                colour, size = C.sketch_ground, 6.0
            elif p.fixed:
                colour, size = C.sketch_fixed, 4.5
            elif pid in centres:
                colour = (C.sketch_line if s.point_constrained(pid)
                          else C.sketch_free)
                size, marker = 3.5, Aspect_TypeOfMarker.Aspect_TOM_PLUS
            elif pid not in used:
                colour = (C.sketch_line if s.point_constrained(pid)
                          else C.sketch_free)
                size, marker = 5.0, Aspect_TypeOfMarker.Aspect_TOM_POINT
            if colour is None:
                continue
            vp.draw_point(self._to3d((p.x, p.y)), colour, size, marker=marker)

        # a ring round the point being mated to, so it is obvious which one
        if self._magnet is not None and self._magnet in s.points:
            target = s.points[self._magnet]
            self._draw_magnet_ring((target.x, target.y))

        for c in s.constraints.values():
            if c.is_dimension:
                self._render_dimension(c)

        self._badges = self._badge_layout()
        for cid, position, symbol in self._badges:
            if cid == self.selected_constraint:
                colour = C.sketch_picked
            elif cid == self._hover_badge:
                colour = C.sketch_hover
            else:
                colour = C.sketch_dim
            vp.draw_text(symbol, self._to3d(position), colour, 14.0)

        vp.redraw()

    def _draw_magnet_ring(self, centre: Tuple[float, float]) -> None:
        """A ring round the point about to be mated, drawn at a fixed size."""
        radius = 7.0 * self.viewport.pixel_scale()
        steps = 20
        previous = None
        for i in range(steps + 1):
            angle = 2.0 * math.pi * i / steps
            here = (centre[0] + radius * math.cos(angle),
                    centre[1] + radius * math.sin(angle))
            if previous is not None:
                self.viewport.draw_edge(self._to3d(previous), self._to3d(here),
                                        C.sketch_magnet, 1.6)
            previous = here

    def _dimension_anchor(self, c: Constraint) -> Optional[Tuple[float, float]]:
        s = self.sketch
        if s is None:
            return None
        try:
            if c.kind in ("radius", "diameter", "radial_gap"):
                ent = s.entities[c.entities[0]]
                centre = s.points[ent.points[0]]
                return (centre.x, centre.y)
            if c.kind == "distance_pl":
                return self._perpendicular_mid(c.points)
            if c.kind == "angle":
                ent = s.entities[c.entities[0]]
                a = s.points[ent.points[0]]
                return (a.x, a.y)
            a = s.points[c.points[0]]
            b = s.points[c.points[1]]
            return ((a.x + b.x) / 2.0, (a.y + b.y) / 2.0)
        except (KeyError, IndexError):
            return None

    def _dimension_value(self, c: Constraint) -> float:
        s = self.sketch
        try:
            if c.kind in ("radius", "diameter"):
                ent = s.entities[c.entities[0]]
                return ent.radius * (2.0 if c.kind == "diameter" else 1.0)
            if c.kind == "radial_gap":
                return abs(s.entities[c.entities[1]].radius
                           - s.entities[c.entities[0]].radius)
            if c.kind == "distance_pl":
                return abs(self._perpendicular(c.points[0], c.points[1],
                                               c.points[2]))
            if c.kind == "angle":
                if len(c.entities) == 1:
                    ux, uy = self._line_direction(c.entities[0])
                    return math.degrees(math.atan2(uy, ux)) % 360.0
                return self._angle_between(c.entities[0], c.entities[1])
            a = s.points[c.points[0]]
            b = s.points[c.points[1]]
            if c.kind == "distance_x":
                return abs(b.x - a.x)
            if c.kind == "distance_y":
                return abs(b.y - a.y)
            return math.hypot(b.x - a.x, b.y - a.y)
        except (KeyError, IndexError):
            return c.value

    def _render_dimension(self, c: Constraint, offset=None,
                          preview: bool = False,
                          colour: Optional[str] = None):
        """Draw one dimension; returns where its label went, in 3D."""
        s = self.sketch
        vp = self.viewport
        anchor = self._dimension_anchor(c)
        if anchor is None:
            return None
        off = offset if offset is not None else c.label_offset
        label_pos = (anchor[0] + off[0], anchor[1] + off[1])
        colour = colour or (C.sketch_preview if preview else C.sketch_dim)

        if c.kind in ("radius", "diameter"):
            ent = s.entities[c.entities[0]]
            centre = s.points[ent.points[0]]
            reach = math.hypot(off[0], off[1]) or ent.radius
            direction = (off[0] / reach, off[1] / reach) if reach else (1.0, 0.0)
            near = (centre.x + direction[0] * ent.radius,
                    centre.y + direction[1] * ent.radius)
            vp.draw_edge(self._to3d((centre.x, centre.y)), self._to3d(near),
                         colour, 1.1, preview=preview, dashed=True)
            vp.draw_edge(self._to3d(near), self._to3d(label_pos),
                         colour, 1.3, preview=preview)
            prefix = "ø" if c.kind == "diameter" else "R"
        elif c.kind == "radial_gap":
            # measured along one ray from the shared centre, from the inner
            # circle out to the outer, with the label further out on it
            inner = s.entities[c.entities[0]]
            outer = s.entities[c.entities[1]]
            centre = s.points[inner.points[0]]
            reach = math.hypot(off[0], off[1]) or outer.radius
            direction = ((off[0] / reach, off[1] / reach) if reach
                         else (1.0, 0.0))
            near = (centre.x + direction[0] * inner.radius,
                    centre.y + direction[1] * inner.radius)
            far = (centre.x + direction[0] * outer.radius,
                   centre.y + direction[1] * outer.radius)
            vp.draw_edge(self._to3d(near), self._to3d(far),
                         colour, 1.3, preview=preview)
            vp.draw_edge(self._to3d(far), self._to3d(label_pos),
                         colour, 1.1, preview=preview, dashed=True)
            prefix = ""
        elif c.kind == "angle":
            try:
                first = self._line_direction(c.entities[0])
                ent = s.entities[c.entities[0]]
                corner = s.points[ent.points[0]]
            except (KeyError, IndexError):
                return None
            for eid in c.entities:
                other = s.entities[eid]
                p1, p2 = s.points[other.points[0]], s.points[other.points[1]]
                vp.draw_edge(self._to3d((p1.x, p1.y)), self._to3d((p2.x, p2.y)),
                             colour, 0.9, preview=preview, dashed=True)
                if len(c.entities) == 1:
                    # what a lone line's angle is measured from: the
                    # horizontal through its start, as long as the line
                    reach = math.hypot(p2.x - p1.x, p2.y - p1.y)
                    vp.draw_edge(self._to3d((p1.x, p1.y)),
                                 self._to3d((p1.x + reach, p1.y)),
                                 colour, 0.9, preview=preview, dashed=True)
            vp.draw_edge(self._to3d((corner.x, corner.y)),
                         self._to3d(label_pos), colour, 1.1, preview=preview,
                         dashed=True)
            prefix = ""
        else:
            try:
                if c.kind == "distance_pl":
                    ends = self._perpendicular_ends(c.points)
                    ax, ay = ends[0]
                    bx, by = ends[1]
                else:
                    a = s.points[c.points[0]]
                    b = s.points[c.points[1]]
                    ax, ay, bx, by = a.x, a.y, b.x, b.y
            except (KeyError, IndexError):
                return None

            # A horizontal or vertical dimension is not drawn along the line
            # between the two points - it is drawn along the axis it
            # measures, with the witness lines running out to meet it.
            if c.kind == "distance_x":
                line_y = (ay + by) / 2.0 + off[1]
                first = (ax, line_y)
                second = (bx, line_y)
            elif c.kind == "distance_y":
                line_x = (ax + bx) / 2.0 + off[0]
                first = (line_x, ay)
                second = (line_x, by)
            else:
                first = (ax + off[0], ay + off[1])
                second = (bx + off[0], by + off[1])

            vp.draw_edge(self._to3d((ax, ay)), self._to3d(first),
                         colour, 0.9, preview=preview, dashed=True)
            vp.draw_edge(self._to3d((bx, by)), self._to3d(second),
                         colour, 0.9, preview=preview, dashed=True)
            vp.draw_edge(self._to3d(first), self._to3d(second),
                         colour, 1.3, preview=preview)
            prefix = ""

        value = self._dimension_value(c)
        text = "%s%s%s" % (prefix, self._shown(c.kind, value),
                           " deg" if c.kind == "angle" else "")
        if c.expression:
            text = "%s  (%s)" % (text, unitlib.for_display(
                c.expression, self.units,
                unitlib.ANGLE if c.kind == "angle" else unitlib.LENGTH))
        where = self._to3d(label_pos)
        vp.draw_text(text, where, colour, 15.0, preview=preview)
        return where

    # -- other sketches, shown for reference -------------------------------

    def draw_reference(self, sketch: Sketch, units: Optional[str] = None
                       ) -> List[Tuple[str, Tuple[float, float, float], str]]:
        """Draw another sketch faintly, dimensions and all.

        A sketch left visible keeps its dimensions on show, as in Inventor,
        so a size can be read straight off it, or clicked while typing a
        value in the sketch being edited to reuse it.  Drawn with the
        editor's own dimension code, on the other sketch's own plane, by
        lending it that sketch for the length of the call.

        Returns (name, label position in 3D, value as text) for each
        dimension drawn, which is what clicking one needs.
        """
        vp = self.viewport
        for eid, ent in sketch.entities.items():
            edge = kernel._sketch_edge(sketch, eid)
            if edge is not None:
                vp.draw_shape(edge, C.sketch_construction if ent.construction
                              else REFERENCE_LINE, 1.2, dashed=ent.construction)

        labels = []
        own, own_units = self.sketch, self._units_override
        self.sketch = sketch
        if units:
            self._units_override = units
        try:
            for c in sketch.constraints.values():
                if not c.is_dimension:
                    continue
                try:
                    where = self._render_dimension(c, colour=REFERENCE_DIM)
                    text = self._dimension_text(c)
                except (KeyError, IndexError):
                    continue
                if where is not None and c.name:
                    labels.append((c.name, where, text))
        finally:
            self.sketch = own
            self._units_override = own_units
        return labels

    def _render_references(self) -> None:
        self._reference_labels = []
        for sketch in self.references:
            if sketch is not self.sketch:
                self._reference_labels += self.draw_reference(sketch)

    def _insert_reference_name(self, u: float, v: float) -> bool:
        """Put the name of another sketch's dimension, clicked, in the box."""
        if not self._reference_labels:
            return False
        vp = self.viewport
        x, y = vp.project(self._to3d((u, v)))
        for name, where, text in self._reference_labels:
            lx, ly = vp.project(where)
            if math.hypot(lx - x, ly - y) <= REFERENCE_PICK_PIXELS:
                self.value_popup.insert(name)
                self.hint_changed.emit(
                    "%s is %s. Finish the expression and press Enter."
                    % (name, text))
                return True
        return False

    def _render_preview(self) -> None:
        vp = self.viewport
        vp.clear_preview()
        if self.sketch is None:
            vp.redraw()
            return

        if self._dim_target is not None:
            self._preview_dimension()
            vp.draw_point(self._to3d(self._cursor), C.sketch_preview, 5.0,
                          preview=True)
            vp.redraw()
            return

        if self._box_start is not None and self._box_end is not None:
            self._preview_box()
            vp.redraw()
            return

        cur = self._resolved_cursor(self._cursor)
        colour = C.sketch_preview

        # every rubber-band edge below must be preview=True, or it lands in
        # the persistent overlay and leaves a ghost behind on the next move
        if self.tool == "line" and self._pending:
            vp.draw_edge(self._to3d(self._pending[-1]), self._to3d(cur),
                         colour, 1.8, preview=True)
        elif self.tool == "rect" and self._pending:
            a = self._pending[0]
            corners = [a, (cur[0], a[1]), cur, (a[0], cur[1])]
            for i in range(4):
                vp.draw_edge(self._to3d(corners[i]),
                             self._to3d(corners[(i + 1) % 4]), colour, 1.6,
                             preview=True)
        elif self.tool == "circle" and self._pending:
            self._preview_circle(self._pending[0], math.dist(self._pending[0], cur),
                                 colour)
        elif self.tool == "polygon" and self._pending:
            centre = self._pending[0]
            r = math.dist(centre, cur)
            rot = math.atan2(cur[1] - centre[1], cur[0] - centre[0])
            pts = [(centre[0] + r * math.cos(rot + 2 * math.pi * i / self.polygon_sides),
                    centre[1] + r * math.sin(rot + 2 * math.pi * i / self.polygon_sides))
                   for i in range(self.polygon_sides)]
            for i in range(self.polygon_sides):
                vp.draw_edge(self._to3d(pts[i]),
                             self._to3d(pts[(i + 1) % self.polygon_sides]),
                             colour, 1.6, preview=True)
        elif self.tool == "arc" and self._pending:
            if len(self._pending) == 1:
                vp.draw_edge(self._to3d(self._pending[0]), self._to3d(cur),
                             colour, 1.4, preview=True)
            else:
                centre, start = self._pending[0], self._pending[1]
                self._preview_arc(centre, math.dist(centre, start), start, cur,
                                  colour)
        elif self.tool == "slot" and self._pending:
            if len(self._pending) == 1:
                vp.draw_edge(self._to3d(self._pending[0]), self._to3d(cur),
                             colour, 1.6, preview=True)
            else:
                p1, p2 = self._pending[0], self._pending[1]
                width = 2.0 * _dist_point_segment(cur, p1, p2)
                self._preview_slot(p1, p2, width, colour)
        elif self.tool in VARIANT_PREVIEWS and self._pending:
            self._preview_variant(cur, colour)
        elif self.tool == "offset" and self._pending:
            self._preview_offset(colour)
        elif self.tool == "spline" and self._pending:
            pts = self._pending + [cur]
            for i in range(len(pts) - 1):
                vp.draw_edge(self._to3d(pts[i]), self._to3d(pts[i + 1]),
                             colour, 1.4, preview=True, dashed=True)

        # A snap is shown by a small cross on the exact spot it took, which
        # matters most for what is not geometry you can see - a midpoint, a
        # point on a curve, a grid point.  Small, because a ring the size of
        # a fingertip hides the very spot it is pointing at.
        if self._snap_info:
            vp.draw_point(self._to3d(cur), C.sketch_hover, 2.6, preview=True,
                          marker=Aspect_TypeOfMarker.Aspect_TOM_X)
        vp.redraw()

    def _preview_box(self) -> None:
        """Draw the rubber band - green and dashed for a crossing box."""
        rect = self._box_rect()
        if rect is None:
            return
        crossing = self._box_crossing()
        colour = C.ok if crossing else C.accent
        x0, y0, x1, y1 = rect
        corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        for i in range(4):
            self.viewport.draw_edge(
                self._to3d(corners[i]), self._to3d(corners[(i + 1) % 4]),
                colour, 1.3, preview=True, dashed=crossing)

    def _preview_dimension(self) -> None:
        """Show the dimension following the cursor before it is placed."""
        target = self._dim_target
        if target is None:
            return
        ghost = Constraint(id=-1, kind=target["kind"],
                           points=list(target["points"]),
                           entities=list(target["entities"]),
                           value=target["current"])
        self._render_dimension(ghost, self._dim_offset, preview=True)

    def _preview_circle(self, centre, radius, colour) -> None:
        if radius < 1e-7:
            return
        segments = 48
        prev = None
        for i in range(segments + 1):
            a = 2 * math.pi * i / segments
            p = (centre[0] + radius * math.cos(a), centre[1] + radius * math.sin(a))
            if prev is not None:
                self.viewport.draw_edge(self._to3d(prev), self._to3d(p),
                                        colour, 1.6, preview=True)
            prev = p

    def _preview_arc(self, centre, radius, start, end, colour) -> None:
        if radius < 1e-7:
            return
        a0 = math.atan2(start[1] - centre[1], start[0] - centre[0])
        a1 = math.atan2(end[1] - centre[1], end[0] - centre[0])
        while a1 <= a0:
            a1 += 2 * math.pi
        segments = 40
        prev = None
        for i in range(segments + 1):
            a = a0 + (a1 - a0) * i / segments
            p = (centre[0] + radius * math.cos(a), centre[1] + radius * math.sin(a))
            if prev is not None:
                self.viewport.draw_edge(self._to3d(prev), self._to3d(p),
                                        colour, 1.6, preview=True)
            prev = p

    def _preview_polygon(self, corners, colour) -> None:
        for i in range(len(corners)):
            self.viewport.draw_edge(self._to3d(corners[i]),
                                    self._to3d(corners[(i + 1) % len(corners)]),
                                    colour, 1.6, preview=True)

    def _preview_guide(self, a, b, colour) -> None:
        """A thin line for a click still to come: a centre line, a radius."""
        if math.dist(a, b) > 1e-9:
            self.viewport.draw_edge(self._to3d(a), self._to3d(b), colour, 1.2,
                                    preview=True, dashed=True)

    def _preview_arc_angles(self, centre, radius, a0, a1, colour) -> None:
        if radius < 1e-7:
            return
        segments = 40
        prev = None
        for i in range(segments + 1):
            a = a0 + (a1 - a0) * i / segments
            p = (centre[0] + radius * math.cos(a),
                 centre[1] + radius * math.sin(a))
            if prev is not None:
                self.viewport.draw_edge(self._to3d(prev), self._to3d(p),
                                        colour, 1.6, preview=True)
            prev = p

    def _preview_arc_through(self, centre, radius, start, through, end,
                             colour) -> None:
        """The arc from start to end that passes the middle point."""
        a0 = math.atan2(start[1] - centre[1], start[0] - centre[0])
        am = math.atan2(through[1] - centre[1], through[0] - centre[0])
        a1 = math.atan2(end[1] - centre[1], end[0] - centre[0])
        turn = 2.0 * math.pi
        to_middle, to_end = (am - a0) % turn, (a1 - a0) % turn
        if to_middle <= to_end:
            self._preview_arc_angles(centre, radius, a0, a0 + to_end, colour)
        else:
            self._preview_arc_angles(centre, radius, a0, a0 - (turn - to_end),
                                     colour)

    def _preview_arc_slot(self, centre, radius, start, end, edge,
                          colour) -> None:
        """The slot _build_arc_slot would make, or its centre arc so far."""
        a0, a1 = self._slot_angles(centre, start, end)
        r = abs(math.dist(centre, edge) - radius)
        if r < 1e-7 or radius <= r:
            self._preview_arc_angles(centre, radius, a0, a1, colour)
            return
        self._preview_arc_angles(centre, radius + r, a0, a1, colour)
        self._preview_arc_angles(centre, radius - r, a0, a1, colour)
        for angle, outward in ((a0, False), (a1, True)):
            cap = (centre[0] + radius * math.cos(angle),
                   centre[1] + radius * math.sin(angle))
            if outward:
                self._preview_arc_angles(cap, r, angle, angle + math.pi,
                                         colour)
            else:
                self._preview_arc_angles(cap, r, angle + math.pi,
                                         angle + 2.0 * math.pi, colour)

    def _preview_variant(self, cur, colour) -> None:
        """Live shape for the rectangle and slot variants.

        Each one draws what the next click would make, worked out the same
        way its tool works it out, so nothing is ever clicked blind.
        """
        tool, pending = self.tool, self._pending
        if tool == "rect3":
            if len(pending) == 1:
                self.viewport.draw_edge(self._to3d(pending[0]),
                                        self._to3d(cur), colour, 1.6,
                                        preview=True)
                return
            corners = self._rect_corners(pending[0], pending[1], cur)
            if corners:
                self._preview_polygon(corners, colour)
        elif tool == "rect_centre":
            c = pending[0]
            half = (cur[0] - c[0], cur[1] - c[1])
            self._preview_polygon(
                [(c[0] - half[0], c[1] - half[1]),
                 (c[0] + half[0], c[1] - half[1]),
                 (c[0] + half[0], c[1] + half[1]),
                 (c[0] - half[0], c[1] + half[1])], colour)
        elif tool == "rect3_centre":
            centre = pending[0]
            edge = pending[1] if len(pending) > 1 else cur
            mirror = (2.0 * centre[0] - edge[0], 2.0 * centre[1] - edge[1])
            if len(pending) == 1:
                self._preview_guide(mirror, cur, colour)
                return
            dx, dy = edge[0] - centre[0], edge[1] - centre[1]
            half = math.hypot(dx, dy)
            if half < 1e-7:
                return
            ux, uy = dx / half, dy / half
            nx, ny = -uy, ux
            wide = abs((cur[0] - centre[0]) * nx + (cur[1] - centre[1]) * ny)
            if wide < 1e-7:
                self._preview_guide(mirror, edge, colour)
                return
            self._preview_polygon([
                (centre[0] - ux * half - nx * wide,
                 centre[1] - uy * half - ny * wide),
                (centre[0] + ux * half - nx * wide,
                 centre[1] + uy * half - ny * wide),
                (centre[0] + ux * half + nx * wide,
                 centre[1] + uy * half + ny * wide),
                (centre[0] - ux * half + nx * wide,
                 centre[1] - uy * half + ny * wide)], colour)
        elif tool == "slot_overall":
            if len(pending) == 1:
                self._preview_guide(pending[0], cur, colour)
                return
            p1, p2 = pending[0], pending[1]
            width = 2.0 * _dist_point_segment(cur, p1, p2)
            length = math.dist(p1, p2)
            if width < 1e-6 or length <= width:
                self._preview_guide(p1, p2, colour)
                return
            r = width / 2.0
            ux, uy = (p2[0] - p1[0]) / length, (p2[1] - p1[1]) / length
            self._preview_slot((p1[0] + ux * r, p1[1] + uy * r),
                               (p2[0] - ux * r, p2[1] - uy * r), width,
                               colour)
        elif tool == "slot_centre":
            centre = pending[0]
            end = pending[1] if len(pending) > 1 else cur
            other = (2.0 * centre[0] - end[0], 2.0 * centre[1] - end[1])
            if len(pending) == 1:
                self._preview_guide(other, cur, colour)
                return
            width = 2.0 * _dist_point_segment(cur, other, end)
            if width < 1e-6:
                self._preview_guide(other, end, colour)
                return
            self._preview_slot(other, end, width, colour)
        elif tool == "slot_arc3":
            if len(pending) == 1:
                self._preview_guide(pending[0], cur, colour)
                return
            start, end = pending[0], pending[1]
            through = pending[2] if len(pending) > 2 else cur
            circle = _circle_through(start, through, end)
            if circle is None:
                self._preview_guide(start, end, colour)
                return
            centre, radius = circle
            if len(pending) == 2:
                self._preview_arc_through(centre, radius, start, through,
                                          end, colour)
            else:
                self._preview_arc_slot(centre, radius, start, end, cur,
                                       colour)
        elif tool == "slot_arc_centre":
            centre = pending[0]
            if len(pending) == 1:
                self._preview_guide(centre, cur, colour)
                return
            start = pending[1]
            radius = math.dist(centre, start)
            if radius < 1e-7:
                return
            if len(pending) == 2:
                # the end is only a direction: it lands on the arc
                self._preview_guide(centre, cur, colour)
                a0, a1 = self._slot_angles(centre, start, cur)
                self._preview_arc_angles(centre, radius, a0, a1, colour)
                return
            self._preview_arc_slot(centre, radius, start, pending[2], cur,
                                   colour)

    def _preview_slot(self, p1, p2, width, colour) -> None:
        r = width / 2.0
        dx, dy = p2[0] - p1[0], p2[1] - p1[1]
        length = math.hypot(dx, dy)
        if length < 1e-7 or r < 1e-7:
            return
        nx, ny = -dy / length * r, dx / length * r
        self.viewport.draw_edge(self._to3d((p1[0] + nx, p1[1] + ny)),
                                self._to3d((p2[0] + nx, p2[1] + ny)),
                                colour, 1.6, preview=True)
        self.viewport.draw_edge(self._to3d((p1[0] - nx, p1[1] - ny)),
                                self._to3d((p2[0] - nx, p2[1] - ny)),
                                colour, 1.6, preview=True)
        # each round end bulges away from the slot: counter-clockwise from
        # one rail to the other, starting on the side that puts it outside
        self._preview_arc(p2, r, (p2[0] - nx, p2[1] - ny),
                          (p2[0] + nx, p2[1] + ny), colour)
        self._preview_arc(p1, r, (p1[0] + nx, p1[1] + ny),
                          (p1[0] - nx, p1[1] - ny), colour)
