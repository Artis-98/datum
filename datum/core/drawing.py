"""The drawing document: sheets, views and annotations.

A drawing never holds geometry of its own.  It references parts and
assemblies by path and generates lines from them, so changing a model
changes every drawing of it.  What it *does* hold is the result of that
generation, cached, so a drawing opens and prints without loading a single
model - and a hash of each referenced file, so it knows when the cache has
gone stale and says so rather than quietly printing yesterday's part.

The shape of the file:

    header      format version, units, standard
    properties  title, author, revision, and whatever else is wanted
    styles      how lines, text and dimensions look
    resources   borders, title blocks and sheet formats, defined once
    sheets[]    each with a border, a title block, views and annotations

Resources are defined once and placed on many sheets: edit the definition
and every sheet using it follows.  That is the whole reason they are not
just fields on the sheet.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import bom, fileformat, hlr, prefs
from .fileformat import ComponentRef
from .naming import ShapeRef
from .params import ParameterTable

FORMAT_VERSION = 1

# --------------------------------------------------------------- sheet sizes

# width x height in millimetres, the long side first: a sheet is landscape
# unless it is asked to be otherwise.
SHEET_SIZES: Dict[str, Tuple[float, float]] = {
    "A0": (1189.0, 841.0),
    "A1": (841.0, 594.0),
    "A2": (594.0, 420.0),
    "A3": (420.0, 297.0),
    "A4": (297.0, 210.0),
    "Letter": (279.4, 215.9),
    "Legal": (355.6, 215.9),
    "Tabloid": (431.8, 279.4),
    "ANSI C": (558.8, 431.8),
    "ANSI D": (863.6, 558.8),
    "ANSI E": (1117.6, 863.6),
}
SHEET_ORDER = ("A4", "A3", "A2", "A1", "A0", "Letter", "Legal", "Tabloid",
               "ANSI C", "ANSI D", "ANSI E")

LANDSCAPE = "landscape"
PORTRAIT = "portrait"

FIRST_ANGLE = "first"
THIRD_ANGLE = "third"
STANDARDS = {"ISO": FIRST_ANGLE, "ANSI": THIRD_ANGLE}

# Scales offered in the view dialog.  Drawings are read at a glance, and a
# scale nobody recognises makes that harder, so these are the usual ones.
SCALES = (10.0, 5.0, 2.0, 1.0, 0.5, 0.2, 0.1, 0.05, 0.02, 0.01)


def scale_text(value: float) -> str:
    """A scale the way it is written on a drawing: 1:2, not 0.5."""
    if value <= 0:
        return "1:1"
    if abs(value - round(value)) < 1e-9 and value >= 1.0:
        return "%d:1" % round(value)
    inverse = 1.0 / value
    if abs(inverse - round(inverse)) < 1e-6:
        return "1:%d" % round(inverse)
    return "%.3g:1" % value


def fit_scale(available_w: float, available_h: float,
              model_w: float, model_h: float,
              fraction: float = 1.0) -> float:
    """The largest standard scale at which something this size still fits.

    Drawings are read at a glance and an odd scale makes that harder, so
    this picks from the scales people recognise rather than working out the
    exact ratio that would just fit.
    """
    if model_w <= 1e-9 or model_h <= 1e-9:
        return 1.0
    room_w = max(1e-6, available_w * fraction)
    room_h = max(1e-6, available_h * fraction)
    for scale in SCALES:
        if model_w * scale <= room_w and model_h * scale <= room_h:
            return scale
    return SCALES[-1]


# a view's label and scale sit under it, and want their own room
LABEL_ROOM = 11.0
VIEW_GAP = 16.0


def three_view_layout(frame: Sequence[float], span: Sequence[float],
                      reserve: Sequence[float] = (0.0, 0.0),
                      gap: float = VIEW_GAP) -> Dict[str, Any]:
    """Where to put a base view and its two projections, and at what scale.

    The classic arrangement: the base view top left, one projection under
    it and one beside it, with the isometric in the corner the other two
    leave empty.  ``span`` is the model's own size along x, y and z, and
    ``reserve`` is room that must be left clear at the bottom right - the
    title block, usually.

    Returned in sheet millimetres, as the centre of each view.
    """
    x0, y0, x1, y1 = [float(c) for c in frame[:4]]
    sx, sy, sz = [max(float(c), 1e-6) for c in span[:3]]
    room_w = (x1 - x0)
    room_h = (y1 - y0)

    # the block of three views, before scaling
    raw_w = sx + sy
    raw_h = sy + sz
    scale = fit_scale(room_w - gap - 6.0,
                      room_h - gap - LABEL_ROOM * 2 - 6.0,
                      raw_w, raw_h, fraction=0.92)

    width = sx * scale + gap + sy * scale
    height = sy * scale + gap + LABEL_ROOM + sz * scale

    # sit the block in the space the title block does not want
    left = x0 + max(6.0, (room_w - float(reserve[0]) - width) / 2.0)
    bottom = y0 + max(float(reserve[1]) + 6.0,
                      (room_h - height) / 2.0) + LABEL_ROOM

    base_cx = left + sx * scale / 2.0
    base_cy = bottom + sz * scale + gap + LABEL_ROOM + sy * scale / 2.0
    below_cy = bottom + sz * scale / 2.0
    side_cx = left + sx * scale + gap + sy * scale / 2.0

    # The isometric goes in the corner the orthographic views leave, and
    # that corner is only as wide as the side view.  At the main scale it
    # would spill across them, so it takes the next standard scale down -
    # which is what anyone laying this out by hand would do.
    iso_room = min(sy * scale, sz * scale + gap + sy * scale)
    iso_scale = fit_scale(sy * scale + gap, iso_room,
                          (sx + sy) * 0.72, (sy + sz) * 0.72, fraction=1.0)

    return {
        "scale": scale,
        "iso_scale": min(scale, max(iso_scale, SCALES[-1])),
        "base": (base_cx, base_cy),
        "below": (base_cx, below_cy),
        "side": (side_cx, base_cy),
        "iso": (side_cx, below_cy),
    }


def sheet_extent(size: str, orientation: str,
                 custom: Optional[Sequence[float]] = None
                 ) -> Tuple[float, float]:
    """The paper, in millimetres."""
    if size == "Custom" and custom and len(custom) >= 2:
        width, height = float(custom[0]), float(custom[1])
    else:
        width, height = SHEET_SIZES.get(size, SHEET_SIZES["A3"])
    if orientation == PORTRAIT:
        width, height = height, width
    return (width, height)


# ------------------------------------------------------------------ styles


@dataclass
class Style:
    """How one kind of line or text is drawn.  Widths are in millimetres."""

    name: str = "Default"
    line_width: float = 0.35
    text_height: float = 3.5
    arrow_size: float = 3.0
    colour: str = "#101010"

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "line_width": self.line_width,
                "text_height": self.text_height, "arrow_size": self.arrow_size,
                "colour": self.colour}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Style":
        base = cls()
        return cls(name=str(d.get("name", base.name)),
                   line_width=float(d.get("line_width", base.line_width)),
                   text_height=float(d.get("text_height", base.text_height)),
                   arrow_size=float(d.get("arrow_size", base.arrow_size)),
                   colour=str(d.get("colour", base.colour)))


def default_styles() -> Dict[str, Style]:
    """The pen widths an ISO drawing is made of."""
    return {
        "visible": Style("Visible", 0.5, 3.5, 3.0, "#101010"),
        "hidden": Style("Hidden", 0.25, 3.5, 3.0, "#404040"),
        "outline": Style("Outline", 0.35, 3.5, 3.0, "#101010"),
        "smooth": Style("Tangent", 0.18, 3.5, 3.0, "#606060"),
        "centre": Style("Centre", 0.18, 3.5, 3.0, "#8a2020"),
        "dimension": Style("Dimension", 0.25, 3.5, 3.0, "#101010"),
        "text": Style("Text", 0.25, 3.5, 3.0, "#101010"),
        "border": Style("Border", 0.7, 3.5, 3.0, "#101010"),
        "section": Style("Section", 0.5, 5.0, 4.0, "#101010"),
        "hatch": Style("Hatch", 0.18, 3.5, 3.0, "#101010"),
    }


# ------------------------------------------------------------------- hatching


@dataclass
class Hatch:
    """A fill pattern for the faces a section cut through.

    Angles are degrees anticlockwise from the paper's horizontal and spacing
    is paper millimetres, so a pattern looks the same however the view is
    scaled - which is the convention, because hatching says what a face is
    made of, not how big it is.
    """

    name: str = "Steel"
    angles: Tuple[float, ...] = (45.0,)
    spacing: float = 2.5
    solid: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "angles": list(self.angles),
                "spacing": self.spacing, "solid": self.solid}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Hatch":
        base = cls()
        return cls(name=str(d.get("name", base.name)),
                   angles=tuple(float(a) for a in
                                d.get("angles", base.angles)) or base.angles,
                   spacing=max(0.3, float(d.get("spacing", base.spacing))),
                   solid=bool(d.get("solid", base.solid)))


def hatch_patterns() -> Dict[str, Hatch]:
    """The material patterns ISO 128 part 50 actually asks for.

    Steel is the plain 45 degree run and is the default, because a drawing
    that does not say otherwise means metal.  The rest exist so a wooden
    tote and its steel bracket do not read as the same thing on one sheet.
    """
    return {
        "steel": Hatch("Steel", (45.0,), 2.5),
        "aluminium": Hatch("Aluminium", (45.0,), 1.4),
        "plastic": Hatch("Plastic", (45.0, 135.0), 3.0),
        "plywood": Hatch("Plywood", (45.0, 135.0), 2.0),
        "concrete": Hatch("Concrete", (45.0, 90.0, 135.0), 4.0),
        "glass": Hatch("Glass", (30.0, 150.0), 3.5),
        "solid": Hatch("Solid (thin part)", (), 1.0, solid=True),
        "none": Hatch("None", (), 1.0),
    }


def hatch_lines(loops: Sequence[Sequence[Tuple[float, float]]],
                pattern: Hatch
                ) -> List[Tuple[Tuple[float, float], Tuple[float, float]]]:
    """Where a pattern's lines fall inside a set of closed loops.

    Worked out here rather than left to the painter so that the screen, the
    PDF and the DXF are filling the faces with the same lines, not with
    three approximations of the same idea.

    The loops arrive in the view's own millimetres, one per wire, and a face
    with a hole in it arrives as two.  Which is inside which is never asked:
    a hatch line crosses the boundary an even number of times, so the spans
    between alternate crossings are the inside, and a hole falls out of that
    for free.  The pattern is anchored on the view's origin, so editing the
    model does not shuffle the lines about.
    """
    if pattern.solid or not pattern.angles:
        return []
    edges: List[Tuple[Tuple[float, float], Tuple[float, float]]] = []
    for loop in loops:
        points = list(loop)
        if len(points) < 3:
            continue
        if points[0] != points[-1]:
            points.append(points[0])
        edges.extend(zip(points, points[1:]))
    if not edges:
        return []

    spacing = max(0.3, pattern.spacing)
    out: List[Tuple[Tuple[float, float], Tuple[float, float]]] = []
    for angle in pattern.angles:
        radians = math.radians(angle)
        dx, dy = math.cos(radians), math.sin(radians)
        nx, ny = -dy, dx
        across = [p[0] * nx + p[1] * ny for edge in edges for p in edge]
        low = math.floor(min(across) / spacing) + 1
        high = math.ceil(max(across) / spacing)
        if high - low > 4000:          # a pathological spacing; give up gently
            continue
        for step in range(int(low), int(high)):
            offset = step * spacing
            hits: List[float] = []
            for (ax, ay), (bx, by) in edges:
                sa = ax * nx + ay * ny - offset
                sb = bx * nx + by * ny - offset
                # half-open, so a vertex sitting exactly on the line counts
                # once rather than twice or not at all
                if (sa <= 0.0 < sb) or (sb <= 0.0 < sa):
                    t = sa / (sa - sb)
                    hits.append((ax + (bx - ax) * t) * dx
                                + (ay + (by - ay) * t) * dy)
            if len(hits) < 2:
                continue
            hits.sort()
            for i in range(0, len(hits) - 1, 2):
                start, end = hits[i], hits[i + 1]
                if end - start < 1e-6:
                    continue
                out.append(((nx * offset + dx * start,
                             ny * offset + dy * start),
                            (nx * offset + dx * end,
                             ny * offset + dy * end)))
    return out


HATCH_ORDER = ("steel", "aluminium", "plastic", "plywood", "concrete",
               "glass", "solid", "none")

# A model saying what it is made of should not need saying twice, so a
# section looks at the part's Material property before falling back.
MATERIAL_HATCH = {
    "steel": "steel", "stainless": "steel", "iron": "steel",
    "mild steel": "steel", "s235": "steel", "s355": "steel",
    "aluminium": "aluminium", "aluminum": "aluminium", "alu": "aluminium",
    "brass": "aluminium", "copper": "aluminium", "bronze": "aluminium",
    "abs": "plastic", "pla": "plastic", "petg": "plastic", "nylon": "plastic",
    "plastic": "plastic", "pom": "plastic", "acrylic": "glass",
    "plywood": "plywood", "birch plywood": "plywood", "wood": "plywood",
    "mdf": "plywood", "oak": "plywood", "pine": "plywood",
    "concrete": "concrete", "glass": "glass",
}


def hatch_for_material(material: str) -> str:
    """Which pattern a material name asks for, or "" if it says nothing."""
    text = (material or "").strip().lower()
    if not text:
        return ""
    if text in MATERIAL_HATCH:
        return MATERIAL_HATCH[text]
    for key, pattern in MATERIAL_HATCH.items():
        if key in text:
            return pattern
    return ""


# --------------------------------------------------------------- title block


STATIC = "static"
PROPERTY = "property"
PROMPTED = "prompted"

# Where {Model.*} comes from is the first base view on the sheet; everything
# else is the drawing, the sheet or the view asking the question.
PROPERTY_KEYS = (
    "Model.Name", "Model.PartNumber", "Model.Material", "Model.Mass",
    "Model.Volume", "Model.Area", "Model.Units",
    "Drawing.Title", "Drawing.Author", "Drawing.Revision", "Drawing.Date",
    "Drawing.Company", "Drawing.FileName",
    "Sheet.Name", "Sheet.Number", "Sheet.Count", "Sheet.Size",
    "Sheet.Scale", "Sheet.Standard",
)


@dataclass
class TextField:
    """One piece of text in a title block.

    ``kind`` decides where the text comes from: typed in and left alone,
    resolved from a property every time it is drawn, or asked for once when
    the block is placed.
    """

    name: str = ""
    kind: str = STATIC
    text: str = ""              # static text, or the property key
    value: str = ""             # what a prompted field was answered with
    x: float = 0.0              # millimetres from the block's own origin
    y: float = 0.0
    height: float = 3.5
    bold: bool = False
    align: str = "left"         # left, centre, right

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "kind": self.kind, "text": self.text,
                "value": self.value, "x": self.x, "y": self.y,
                "height": self.height, "bold": self.bold, "align": self.align}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TextField":
        return cls(name=str(d.get("name", "")), kind=str(d.get("kind", STATIC)),
                   text=str(d.get("text", "")), value=str(d.get("value", "")),
                   x=float(d.get("x", 0.0)), y=float(d.get("y", 0.0)),
                   height=float(d.get("height", 3.5)),
                   bold=bool(d.get("bold", False)),
                   align=str(d.get("align", "left")))


@dataclass
class TitleBlock:
    """A reusable block of lines and fields, anchored to a sheet corner."""

    name: str = "Standard"
    width: float = 180.0
    height: float = 46.0
    # lines within the block, in its own millimetres from the bottom left
    lines: List[List[float]] = field(default_factory=list)
    fields: List[TextField] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "width": self.width, "height": self.height,
                "lines": [list(v) for v in self.lines],
                "fields": [f.to_dict() for f in self.fields]}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TitleBlock":
        return cls(name=str(d.get("name", "Standard")),
                   width=float(d.get("width", 180.0)),
                   height=float(d.get("height", 46.0)),
                   lines=[[float(v) for v in line]
                          for line in d.get("lines", []) if len(line) >= 4],
                   fields=[TextField.from_dict(f)
                           for f in d.get("fields", [])])


def standard_title_block() -> TitleBlock:
    """The block the shipped templates use: ISO-ish, and readable."""
    w, h = 180.0, 46.0
    block = TitleBlock(name="Standard", width=w, height=h)
    block.lines = [
        [0.0, 0.0, w, 0.0], [0.0, h, w, h],
        [0.0, 0.0, 0.0, h], [w, 0.0, w, h],
        [0.0, 30.0, w, 30.0],          # above: title, below: the details
        [0.0, 15.0, w, 15.0],
        [110.0, 30.0, 110.0, h],       # the drawn-by box
        [45.0, 0.0, 45.0, 30.0],
        [90.0, 0.0, 90.0, 30.0],
        [135.0, 0.0, 135.0, 30.0],
    ]
    small = 2.5
    block.fields = [
        TextField("title", PROPERTY, "Drawing.Title", x=4.0, y=36.0,
                  height=6.0, bold=True),
        TextField("company", PROPERTY, "Drawing.Company", x=114.0, y=38.0,
                  height=3.5, bold=True),
        TextField("drawn_label", STATIC, "DRAWN", x=114.0, y=32.5,
                  height=small),
        TextField("author", PROPERTY, "Drawing.Author", x=134.0, y=32.5,
                  height=small),

        TextField("material_label", STATIC, "MATERIAL", x=2.0, y=25.0,
                  height=small),
        TextField("material", PROPERTY, "Model.Material", x=2.0, y=18.0),
        TextField("mass_label", STATIC, "MASS", x=47.0, y=25.0, height=small),
        TextField("mass", PROPERTY, "Model.Mass", x=47.0, y=18.0),
        TextField("scale_label", STATIC, "SCALE", x=92.0, y=25.0,
                  height=small),
        TextField("scale", PROPERTY, "Sheet.Scale", x=92.0, y=18.0),
        TextField("sheet_label", STATIC, "SHEET", x=137.0, y=25.0,
                  height=small),
        TextField("sheet", PROPERTY, "Sheet.Number", x=137.0, y=18.0),

        TextField("part_label", STATIC, "PART NUMBER", x=2.0, y=10.0,
                  height=small),
        TextField("part", PROPERTY, "Model.PartNumber", x=2.0, y=3.0,
                  height=5.0, bold=True),
        TextField("rev_label", STATIC, "REV", x=137.0, y=10.0, height=small),
        TextField("revision", PROPERTY, "Drawing.Revision", x=137.0, y=3.0,
                  height=5.0, bold=True),
        TextField("size_label", STATIC, "SIZE", x=92.0, y=10.0, height=small),
        TextField("size", PROPERTY, "Sheet.Size", x=92.0, y=3.0),
        TextField("date_label", STATIC, "DATE", x=47.0, y=10.0, height=small),
        TextField("date", PROPERTY, "Drawing.Date", x=47.0, y=3.0),
    ]
    return block


@dataclass
class Border:
    """The frame round a sheet, with zone letters and numbers up the edges."""

    name: str = "Standard"
    margin: float = 10.0
    left_margin: float = 20.0       # room for a filing punch
    zones: bool = True
    zone_size: float = 6.0

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "margin": self.margin,
                "left_margin": self.left_margin, "zones": self.zones,
                "zone_size": self.zone_size}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Border":
        base = cls()
        return cls(name=str(d.get("name", base.name)),
                   margin=float(d.get("margin", base.margin)),
                   left_margin=float(d.get("left_margin", base.left_margin)),
                   zones=bool(d.get("zones", base.zones)),
                   zone_size=float(d.get("zone_size", base.zone_size)))

    def frame(self, width: float, height: float
              ) -> Tuple[float, float, float, float]:
        """The rectangle the drawing lives inside, on a sheet this size."""
        return (self.left_margin, self.margin,
                width - self.margin, height - self.margin)


# ------------------------------------------------------------------- views

BASE = "base"
PROJECTED = "projected"
SECTION = "section"
DETAIL = "detail"
AUXILIARY = "auxiliary"

VIEW_LABELS = {BASE: "Base View", PROJECTED: "Projected View",
               SECTION: "Section View", DETAIL: "Detail View",
               AUXILIARY: "Auxiliary View"}

# display styles
VISIBLE_ONLY = "visible"
WITH_HIDDEN = "hidden"
SHADED = "shaded"
DISPLAY_LABELS = {VISIBLE_ONLY: "Visible lines only",
                  WITH_HIDDEN: "Hidden lines shown"}
# SHADED stays defined so a file that already names it still loads, but it
# is not offered: hidden line removal gives edges, and a shaded view needs
# filled faces, which is a different piece of work.  Offering a setting
# that quietly does nothing is worse than not having it.


@dataclass
class View:
    """One view on a sheet.

    A base view names a model; every other kind takes its geometry from a
    parent and differs in how it is derived - turned ninety degrees, cut
    open, or enlarged.
    """

    id: int = 0
    kind: str = BASE
    name: str = ""
    parent: int = 0
    ref: ComponentRef = field(default_factory=ComponentRef)

    x: float = 0.0              # centre, in sheet millimetres
    y: float = 0.0
    scale: float = 0.0          # 0 means "whatever the parent uses"
    orientation: str = "front"
    direction: List[float] = field(default_factory=list)   # custom, if given
    up: List[float] = field(default_factory=list)
    display: str = ""           # "" means inherit
    label_visible: bool = True
    scale_visible: bool = False

    # section and detail
    letter: str = ""
    cut: List[float] = field(default_factory=list)      # x1,y1,x2,y2 on parent
    centre: List[float] = field(default_factory=list)   # detail centre
    radius: float = 25.0
    depth: float = 0.0          # how far behind the cut line to look
    hatch: bool = True          # fill the faces the cut passed through
    hatch_pattern: str = ""     # "" asks the model's material

    # filled in by a rebuild; never written to the drawing.json
    projection: Optional[hlr.Projection] = None
    error: str = ""
    stale: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "kind": self.kind, "name": self.name,
            "parent": self.parent, "ref": self.ref.to_dict(),
            "x": self.x, "y": self.y, "scale": self.scale,
            "orientation": self.orientation,
            "direction": list(self.direction), "up": list(self.up),
            "display": self.display,
            "label_visible": self.label_visible,
            "scale_visible": self.scale_visible,
            "letter": self.letter, "cut": list(self.cut),
            "centre": list(self.centre), "radius": self.radius,
            "depth": self.depth, "hatch": self.hatch,
            "hatch_pattern": self.hatch_pattern,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "View":
        return cls(
            id=int(d.get("id", 0)), kind=str(d.get("kind", BASE)),
            name=str(d.get("name", "")), parent=int(d.get("parent", 0)),
            ref=ComponentRef.from_dict(d.get("ref", {})),
            x=float(d.get("x", 0.0)), y=float(d.get("y", 0.0)),
            scale=float(d.get("scale", 0.0)),
            orientation=str(d.get("orientation", "front")),
            direction=[float(v) for v in d.get("direction", [])],
            up=[float(v) for v in d.get("up", [])],
            display=str(d.get("display", "")),
            label_visible=bool(d.get("label_visible", True)),
            scale_visible=bool(d.get("scale_visible", False)),
            letter=str(d.get("letter", "")),
            hatch=bool(d.get("hatch", True)),
            hatch_pattern=str(d.get("hatch_pattern", "")),
            cut=[float(v) for v in d.get("cut", [])],
            centre=[float(v) for v in d.get("centre", [])],
            radius=float(d.get("radius", 25.0)),
            depth=float(d.get("depth", 0.0)))

    @property
    def label(self) -> str:
        if self.kind == SECTION and self.letter:
            return "SECTION %s-%s" % (self.letter, self.letter)
        if self.kind == DETAIL and self.letter:
            return "DETAIL %s" % self.letter
        return self.name or VIEW_LABELS.get(self.kind, "View")


# ------------------------------------------------------------- annotations

LINEAR = "linear"
ALIGNED = "aligned"
ANGULAR = "angular"
RADIUS = "radius"
DIAMETER = "diameter"
ORDINATE = "ordinate"
BALLOON = "balloon"
NOTE = "note"
LEADER = "leader"
CENTRE_MARK = "centre_mark"
CENTRELINE = "centreline"

ANNOTATION_LABELS = {
    LINEAR: "Linear", ALIGNED: "Aligned", ANGULAR: "Angular",
    RADIUS: "Radius", DIAMETER: "Diameter", ORDINATE: "Ordinate",
    BALLOON: "Balloon", NOTE: "Text", LEADER: "Leader",
    CENTRE_MARK: "Centre Mark", CENTRELINE: "Centreline",
}

DIMENSIONS = (LINEAR, ALIGNED, ANGULAR, RADIUS, DIAMETER, ORDINATE)


@dataclass
class Annotation:
    """Something written on a view.

    Points are stored in the view's own millimetres - the same space the
    projection is in - so the annotation stays put when the view is moved or
    rescaled, and lands in a sensible place when the model changes.  If what
    it was attached to disappears, it is marked ``sick`` and drawn in a
    colour that says so, rather than being thrown away with the geometry.
    """

    id: int = 0
    kind: str = LINEAR
    view: int = 0
    points: List[List[float]] = field(default_factory=list)
    # One reference per point, to the model vertex it was snapped to.  With
    # these the dimension follows the model when it changes; without them
    # the points are wherever they were dropped and stay there.
    anchors: List[Optional[ShapeRef]] = field(default_factory=list)
    offset: List[float] = field(default_factory=lambda: [0.0, 8.0])
    text: str = ""              # override; empty means the measured value
    prefix: str = ""
    suffix: str = ""
    value: float = 0.0          # what it measured when last generated
    sick: bool = False
    # balloons only: which file this one points at, and the item number the
    # parts list gave it.  The number is worked out from the component every
    # time, so renumbering the list renumbers the balloons with it.
    component: str = ""
    item: int = 0
    shape: str = ""             # "" is round; "hex" and "square" also exist

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "view": self.view,
                "points": [[float(c) for c in p] for p in self.points],
                "anchors": [a.to_dict() if a else None
                            for a in self.anchors],
                "offset": list(self.offset), "text": self.text,
                "prefix": self.prefix, "suffix": self.suffix,
                "value": self.value, "sick": self.sick,
                "component": self.component, "item": self.item,
                "shape": self.shape}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Annotation":
        return cls(id=int(d.get("id", 0)), kind=str(d.get("kind", LINEAR)),
                   view=int(d.get("view", 0)),
                   points=[[float(c) for c in p] for p in d.get("points", [])],
                   anchors=[ShapeRef.from_dict(a) if a else None
                            for a in d.get("anchors", [])],
                   offset=[float(v) for v in d.get("offset", [0.0, 8.0])],
                   text=str(d.get("text", "")),
                   prefix=str(d.get("prefix", "")),
                   suffix=str(d.get("suffix", "")),
                   value=float(d.get("value", 0.0)),
                   sick=bool(d.get("sick", False)),
                   component=str(d.get("component", "")),
                   item=int(d.get("item", 0)),
                   shape=str(d.get("shape", "")))

    @property
    def anchored(self) -> bool:
        """Whether this is tied to the model or just sitting on the paper."""
        return any(a is not None for a in self.anchors)

    def measure(self) -> float:
        """What this dimension reads, from the points it holds."""
        p = self.points
        if self.kind in (LINEAR, ALIGNED) and len(p) >= 2:
            dx, dy = p[1][0] - p[0][0], p[1][1] - p[0][1]
            if self.kind == ALIGNED:
                return math.hypot(dx, dy)
            # linear measures along whichever axis the pair is spread on,
            # which is what the person placing it meant by dragging that way
            return abs(dx) if abs(dx) >= abs(dy) else abs(dy)
        if self.kind == ORDINATE and len(p) >= 2:
            # measured from the datum point, along whichever axis the pair
            # is spread on - the same rule a linear dimension follows
            dx, dy = p[1][0] - p[0][0], p[1][1] - p[0][1]
            return abs(dx) if abs(dx) >= abs(dy) else abs(dy)
        if self.kind in (RADIUS, DIAMETER) and len(p) >= 2:
            r = math.hypot(p[1][0] - p[0][0], p[1][1] - p[0][1])
            return r * (2.0 if self.kind == DIAMETER else 1.0)
        if self.kind == ANGULAR and len(p) >= 3:
            ax, ay = p[0][0] - p[1][0], p[0][1] - p[1][1]
            bx, by = p[2][0] - p[1][0], p[2][1] - p[1][1]
            return abs(math.degrees(math.atan2(ax * by - ay * bx,
                                               ax * bx + ay * by)))
        return 0.0

    def caption(self, scale: float = 1.0) -> str:
        """The text drawn, measured back to model size."""
        if self.text:
            return self.text
        if self.kind == BALLOON:
            return str(self.item) if self.item else "?"
        raw = self.measure() / (scale or 1.0)
        if self.kind == ANGULAR:
            return "%s%.1f°%s" % (self.prefix, self.measure(),
                                       self.suffix)
        symbol = {RADIUS: "R", DIAMETER: "⌀"}.get(self.kind, "")
        return "%s%s%s%s" % (self.prefix, symbol, _trim(raw), self.suffix)


def _trim(value: float) -> str:
    text = "%.2f" % value
    return text.rstrip("0").rstrip(".") if "." in text else text


# ------------------------------------------------------------------ sheets


@dataclass
class PartsList:
    """A table of what the sheet's assembly is made of.

    It holds where the table goes and which columns it shows, not the rows
    themselves: the rows are read from the assembly every time the drawing
    is rebuilt, so a component added upstairs turns up here without anybody
    having to remember to come and add it.
    """

    id: int = 0
    view: int = 0               # which view's model it lists; 0 is the base
    x: float = 0.0              # the corner it grows from, in sheet mm
    y: float = 0.0
    corner: str = "bottom-right"        # which corner (x, y) is
    columns: List[str] = field(default_factory=lambda: list(bom.DEFAULT_COLUMNS))
    widths: Dict[str, float] = field(default_factory=dict)
    row_height: float = 8.0
    text_height: float = 3.0
    heading: bool = True
    recurse: bool = False       # count the parts inside sub-assemblies
    # filled in by a rebuild
    rows: List[Any] = field(default_factory=list)
    error: str = ""

    def width(self) -> float:
        return sum(self.column_width(c) for c in self.columns)

    def column_width(self, column: str) -> float:
        return float(self.widths.get(column,
                                     bom.DEFAULT_WIDTHS.get(column, 30.0)))

    def height(self) -> float:
        lines = len(self.rows) + (1 if self.heading else 0)
        return max(1, lines) * self.row_height

    def origin(self) -> Tuple[float, float]:
        """The bottom-left of the table, whichever corner was placed."""
        w, h = self.width(), self.height()
        x = self.x - w if "right" in self.corner else self.x
        y = self.y if "bottom" in self.corner else self.y - h
        return (x, y)

    def box(self) -> Tuple[float, float, float, float]:
        x, y = self.origin()
        return (x, y, x + self.width(), y + self.height())

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "view": self.view, "x": self.x, "y": self.y,
                "corner": self.corner, "columns": list(self.columns),
                "widths": dict(self.widths), "row_height": self.row_height,
                "text_height": self.text_height, "heading": self.heading,
                "recurse": self.recurse,
                # written out so a drawing opened away from its models can
                # still show the list it had when it was last built
                "rows": [r.to_dict() for r in self.rows]}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PartsList":
        base = cls()
        return cls(id=int(d.get("id", 0)), view=int(d.get("view", 0)),
                   x=float(d.get("x", 0.0)), y=float(d.get("y", 0.0)),
                   corner=str(d.get("corner", base.corner)),
                   columns=[str(c) for c in d.get("columns", base.columns)]
                   or list(base.columns),
                   widths={str(k): float(v)
                           for k, v in (d.get("widths") or {}).items()},
                   row_height=float(d.get("row_height", base.row_height)),
                   text_height=float(d.get("text_height", base.text_height)),
                   heading=bool(d.get("heading", True)),
                   recurse=bool(d.get("recurse", False)),
                   rows=[bom.Row.from_dict(r) for r in d.get("rows", [])])


@dataclass
class Sheet:
    """One page, with everything on it."""

    id: int = 0
    name: str = "Sheet1"
    size: str = "A3"
    orientation: str = LANDSCAPE
    custom: List[float] = field(default_factory=lambda: [420.0, 297.0])
    border: str = "Standard"        # names a Border resource, "" for none
    title_block: str = "Standard"   # names a TitleBlock resource
    field_values: Dict[str, str] = field(default_factory=dict)
    views: List[View] = field(default_factory=list)
    annotations: List[Annotation] = field(default_factory=list)
    parts_lists: List[PartsList] = field(default_factory=list)

    def extent(self) -> Tuple[float, float]:
        return sheet_extent(self.size, self.orientation, self.custom)

    def view(self, view_id: int) -> Optional[View]:
        return next((v for v in self.views if v.id == view_id), None)

    def children_of(self, view_id: int) -> List[View]:
        return [v for v in self.views if v.parent == view_id]

    def base_view(self) -> Optional[View]:
        """The view the {Model.*} fields speak for: the first base on it."""
        return next((v for v in self.views if v.kind == BASE), None)

    def annotations_for(self, view_id: int) -> List[Annotation]:
        return [a for a in self.annotations if a.view == view_id]

    def parts_list(self, list_id: int) -> Optional["PartsList"]:
        return next((p for p in self.parts_lists if p.id == list_id), None)

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "name": self.name, "size": self.size,
                "orientation": self.orientation, "custom": list(self.custom),
                "border": self.border, "title_block": self.title_block,
                "field_values": dict(self.field_values),
                "views": [v.to_dict() for v in self.views],
                "annotations": [a.to_dict() for a in self.annotations],
                "parts_lists": [p.to_dict() for p in self.parts_lists]}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Sheet":
        return cls(id=int(d.get("id", 0)), name=str(d.get("name", "Sheet1")),
                   size=str(d.get("size", "A3")),
                   orientation=str(d.get("orientation", LANDSCAPE)),
                   custom=[float(v) for v in d.get("custom", [420.0, 297.0])],
                   border=str(d.get("border", "Standard")),
                   title_block=str(d.get("title_block", "Standard")),
                   field_values={str(k): str(v) for k, v
                                 in (d.get("field_values") or {}).items()},
                   views=[View.from_dict(v) for v in d.get("views", [])],
                   annotations=[Annotation.from_dict(a)
                                for a in d.get("annotations", [])],
                   parts_lists=[PartsList.from_dict(p)
                                for p in d.get("parts_lists", [])])


# ------------------------------------------------------------- the document


class DrawingDocument:
    """A drawing: sheets of views generated from models it does not own."""

    doc_type = fileformat.DRAWING

    def __init__(self) -> None:
        self.format_version = FORMAT_VERSION
        self.units = "mm"
        self.standard = "ISO"
        self.properties: Dict[str, str] = {
            "Title": "", "Author": "", "Company": "", "Revision": "A",
            "Date": "",
        }
        # a new drawing already knows who is drawing it
        prefs.prefs().stamp(self.properties, "Author")
        self.params = ParameterTable()
        self.styles: Dict[str, Style] = default_styles()
        self.borders: Dict[str, Border] = {"Standard": Border()}
        self.title_blocks: Dict[str, TitleBlock] = {
            "Standard": standard_title_block()}
        self.sheets: List[Sheet] = []
        self.active_sheet: int = 0

        self.path = ""
        self.created = fileformat.now()
        self.modified = False
        self.thumbnail: Optional[bytes] = None
        self.material = "Generic"
        self.density = 1.0

        # what each referenced file looked like when its views were generated
        self.hashes: Dict[str, str] = {}
        self.shape = None            # a drawing has no 3D body of its own
        self.last_report = DrawingReport()

        self._next_id = 1
        self._undo: List[str] = []
        self._redo: List[str] = []

    # ------------------------------------------------------------ identity

    @property
    def title(self) -> str:
        if self.path:
            return os.path.splitext(os.path.basename(self.path))[0]
        return "Drawing1"

    @property
    def base_dir(self) -> str:
        return os.path.dirname(os.path.abspath(self.path)) if self.path else ""

    @property
    def angle(self) -> str:
        return STANDARDS.get(self.standard, FIRST_ANGLE)

    def new_id(self) -> int:
        i = self._next_id
        self._next_id += 1
        return i

    # -------------------------------------------------------------- sheets

    def sheet(self, sheet_id: int) -> Optional[Sheet]:
        return next((s for s in self.sheets if s.id == sheet_id), None)

    def active(self) -> Optional[Sheet]:
        return self.sheet(self.active_sheet) or (self.sheets[0]
                                                 if self.sheets else None)

    def unique_sheet_name(self, base: str = "Sheet") -> str:
        taken = {s.name for s in self.sheets}
        i = 1
        while "%s%d" % (base, i) in taken:
            i += 1
        return "%s%d" % (base, i)

    def add_sheet(self, size: str = "A3", orientation: str = LANDSCAPE,
                  name: str = "") -> Sheet:
        sheet = Sheet(id=self.new_id(), name=name or self.unique_sheet_name(),
                      size=size, orientation=orientation)
        sheet.custom = list(SHEET_SIZES.get(size, (420.0, 297.0)))
        self.sheets.append(sheet)
        if not self.active_sheet:
            self.active_sheet = sheet.id
        self.modified = True
        return sheet

    def remove_sheet(self, sheet_id: int) -> bool:
        """Drop a sheet, refusing to leave the drawing with none."""
        if len(self.sheets) <= 1:
            return False
        self.sheets = [s for s in self.sheets if s.id != sheet_id]
        if self.active_sheet == sheet_id and self.sheets:
            self.active_sheet = self.sheets[0].id
        self.modified = True
        return True

    def move_sheet(self, sheet_id: int, to: int) -> None:
        order = [s.id for s in self.sheets]
        if sheet_id not in order:
            return
        at = order.index(sheet_id)
        sheet = self.sheets.pop(at)
        self.sheets.insert(max(0, min(len(self.sheets), to)), sheet)
        self.modified = True

    def duplicate_sheet(self, sheet_id: int) -> Optional[Sheet]:
        original = self.sheet(sheet_id)
        if original is None:
            return None
        copied = Sheet.from_dict(original.to_dict())
        copied.id = self.new_id()
        copied.name = self.unique_sheet_name()
        # every view and annotation needs an id of its own, and the views'
        # parent links have to be rewritten to the copies
        remap: Dict[int, int] = {}
        for view in copied.views:
            remap[view.id] = self.new_id()
        for view in copied.views:
            view.id = remap[view.id]
            view.parent = remap.get(view.parent, 0)
        for note in copied.annotations:
            note.id = self.new_id()
            note.view = remap.get(note.view, 0)
        self.sheets.append(copied)
        self.modified = True
        return copied

    # --------------------------------------------------------------- views

    def add_view(self, sheet: Sheet, view: View) -> View:
        view.id = self.new_id()
        if not view.name:
            view.name = self.unique_view_name(sheet, view.kind)
        sheet.views.append(view)
        self.modified = True
        return view

    def unique_view_name(self, sheet: Sheet, kind: str) -> str:
        stem = {BASE: "View", PROJECTED: "View", SECTION: "Section",
                DETAIL: "Detail", AUXILIARY: "Auxiliary"}.get(kind, "View")
        taken = {v.name for v in sheet.views}
        i = 1
        while "%s%d" % (stem, i) in taken:
            i += 1
        return "%s%d" % (stem, i)

    def next_letter(self) -> str:
        """The next free label letter for a section or detail, across sheets."""
        used = {v.letter for s in self.sheets for v in s.views if v.letter}
        for code in range(ord("A"), ord("Z") + 1):
            if chr(code) not in used:
                return chr(code)
        return "A"

    def remove_view(self, sheet: Sheet, view_id: int,
                    children: bool = True) -> List[int]:
        """Delete a view, and by default everything derived from it."""
        gone: List[int] = []
        queue = [view_id]
        while queue:
            current = queue.pop()
            gone.append(current)
            if children:
                queue.extend(v.id for v in sheet.children_of(current))
        sheet.views = [v for v in sheet.views if v.id not in gone]
        sheet.annotations = [a for a in sheet.annotations
                             if a.view not in gone]
        # anything orphaned by a partial delete becomes a view in its own
        # right rather than a child pointing at nothing
        for view in sheet.views:
            if view.parent in gone:
                view.parent = 0
        self.modified = True
        return gone

    def view_scale(self, sheet: Sheet, view: View) -> float:
        """A view's scale, following the chain up to whoever sets one."""
        seen = set()
        current: Optional[View] = view
        while current is not None and current.id not in seen:
            if current.scale > 0:
                return current.scale
            seen.add(current.id)
            current = sheet.view(current.parent)
        return 1.0

    def view_display(self, sheet: Sheet, view: View) -> str:
        seen = set()
        current: Optional[View] = view
        while current is not None and current.id not in seen:
            if current.display:
                return current.display
            seen.add(current.id)
            current = sheet.view(current.parent)
        return VISIBLE_ONLY

    def view_model(self, sheet: Sheet, view: View) -> Optional[View]:
        """The base view a derived one ultimately takes its model from."""
        seen = set()
        current: Optional[View] = view
        while current is not None and current.id not in seen:
            if current.kind == BASE:
                return current
            seen.add(current.id)
            current = sheet.view(current.parent)
        return None

    # ---------------------------------------------------------- annotations

    def add_annotation(self, sheet: Sheet, note: Annotation) -> Annotation:
        note.id = self.new_id()
        sheet.annotations.append(note)
        self.modified = True
        return note

    def remove_annotation(self, sheet: Sheet, note_id: int) -> None:
        sheet.annotations = [a for a in sheet.annotations if a.id != note_id]
        self.modified = True

    # ----------------------------------------------------------- parts lists

    def add_parts_list(self, sheet: Sheet, table: PartsList) -> PartsList:
        table.id = self.new_id()
        if not table.view:
            base = sheet.base_view()
            table.view = base.id if base else 0
        sheet.parts_lists.append(table)
        self.modified = True
        return table

    def remove_parts_list(self, sheet: Sheet, list_id: int) -> None:
        sheet.parts_lists = [p for p in sheet.parts_lists if p.id != list_id]
        self.modified = True

    def rows_for(self, sheet: Sheet, table: PartsList) -> List[Any]:
        """The table's rows as they stand, whether or not it has been built.

        A drawing opened without its models still has to draw its parts
        list, so the rows generated last time are kept on the table and
        used until a rebuild replaces them.
        """
        return list(table.rows)

    def balloon_number(self, sheet: Sheet, note: Annotation) -> int:
        """Which item number a balloon should be showing.

        Read from whichever parts list on the sheet counts its component,
        so renumbering the list renumbers the balloons and nobody has to
        keep the two in step by hand.
        """
        if note.kind != BALLOON or not note.component:
            return note.item
        for table in sheet.parts_lists:
            row = bom.row_for_key(table.rows, note.component)
            if row is not None:
                return row.item
        return note.item

    # ------------------------------------------------------------ references

    @property
    def components(self) -> List[ComponentRef]:
        out, seen = [], set()
        for sheet in self.sheets:
            for view in sheet.views:
                if view.kind != BASE or not view.ref.path:
                    continue
                key = os.path.normcase(view.ref.path)
                if key not in seen:
                    seen.add(key)
                    out.append(view.ref)
        return out

    def broken_links(self, base_dir: Optional[str] = None):
        base = base_dir if base_dir is not None else self.base_dir
        if not base:
            return []
        return fileformat.check_links(
            self.components, base,
            allowed=(fileformat.PART, fileformat.ASSEMBLY))

    # ------------------------------------------------------- serialisation

    def to_dict(self) -> Dict[str, Any]:
        return {
            "format_version": self.format_version,
            "units": self.units,
            "standard": self.standard,
            "properties": dict(self.properties),
            "parameters": self.params.to_list(),
            "styles": {k: v.to_dict() for k, v in self.styles.items()},
            "resources": {
                "borders": {k: v.to_dict() for k, v in self.borders.items()},
                "title_blocks": {k: v.to_dict()
                                 for k, v in self.title_blocks.items()},
            },
            "sheets": [s.to_dict() for s in self.sheets],
            "active_sheet": self.active_sheet,
            "next_id": self._next_id,
            "material": self.material,
            "hashes": dict(self.hashes),
            "components": [r.to_dict() for r in self.components],
        }

    def load_dict(self, data: Dict[str, Any]) -> None:
        self.format_version = int(data.get("format_version", FORMAT_VERSION))
        self.units = str(data.get("units", "mm"))
        self.standard = str(data.get("standard", "ISO"))
        self.properties = {str(k): str(v) for k, v
                           in (data.get("properties") or {}).items()}
        self.params = ParameterTable()
        self.params.load(data.get("parameters", []))

        self.styles = default_styles()
        for key, raw in (data.get("styles") or {}).items():
            self.styles[str(key)] = Style.from_dict(raw)

        resources = data.get("resources") or {}
        borders = resources.get("borders") or {}
        self.borders = ({str(k): Border.from_dict(v)
                         for k, v in borders.items()}
                        or {"Standard": Border()})
        blocks = resources.get("title_blocks") or {}
        self.title_blocks = ({str(k): TitleBlock.from_dict(v)
                              for k, v in blocks.items()}
                             or {"Standard": standard_title_block()})

        self.sheets = [Sheet.from_dict(s) for s in data.get("sheets", [])]
        if not self.sheets:
            self.add_sheet()
        self.active_sheet = int(data.get("active_sheet", 0)) or self.sheets[0].id
        self.material = str(data.get("material", "Generic"))
        self.hashes = {str(k): str(v) for k, v
                       in (data.get("hashes") or {}).items()}

        known = [0]
        for sheet in self.sheets:
            known.append(sheet.id)
            known.extend(v.id for v in sheet.views)
            known.extend(a.id for a in sheet.annotations)
        self._next_id = max(int(data.get("next_id", 1)), max(known) + 1)

    def mass_properties(self) -> Dict[str, Any]:
        """A drawing has no solid of its own, so it has no mass."""
        return {}

    def property_rows(self) -> List[Tuple[str, str]]:
        """What the properties panel shows instead of mass and volume."""
        report = self.last_report
        sheet = self.active()
        views_total = sum(len(s.views) for s in self.sheets)
        notes = sum(len(s.annotations) for s in self.sheets)
        rows = [
            ("Standard", "%s (%s angle)" % (self.standard, self.angle)),
            ("Sheets", str(len(self.sheets))),
            ("Views", str(views_total)),
            ("Annotations", str(notes)),
            ("Models", str(len(self.components))),
        ]
        if sheet is not None:
            width, height = sheet.extent()
            rows.insert(1, ("Sheet", "%s  %.0f x %.0f mm"
                            % (sheet.name, width, height)))
        if report.stale:
            rows.append(("Out of date", "%d view(s)" % len(report.stale)))
        if report.missing:
            rows.append(("Missing models", str(len(report.missing))))
        return rows

    def snapshot(self) -> str:
        return json.dumps(self.to_dict())

    # ------------------------------------------------------------ edit stack

    def push_undo(self) -> None:
        self._undo.append(self.snapshot())
        del self._undo[:-60]
        self._redo.clear()

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(self.snapshot())
        self.load_dict(json.loads(self._undo.pop()))
        self.modified = True
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(self.snapshot())
        self.load_dict(json.loads(self._redo.pop()))
        self.modified = True
        return True

    # -------------------------------------------------------------- files

    CACHE_NAME = "cache.json"

    def cache_dict(self) -> Dict[str, Any]:
        """Every generated projection, so the file opens without the models."""
        out: Dict[str, Any] = {}
        for sheet in self.sheets:
            for view in sheet.views:
                if view.projection is not None and view.projection.ok:
                    out[str(view.id)] = view.projection.to_dict()
        return {"views": out}

    def load_cache(self, data: Dict[str, Any]) -> None:
        views = (data or {}).get("views") or {}
        for sheet in self.sheets:
            for view in sheet.views:
                raw = views.get(str(view.id))
                if raw:
                    view.projection = hlr.Projection.from_dict(raw)

    def save(self, path: Optional[str] = None,
             thumbnail: Optional[bytes] = None) -> str:
        from .. import APP_NAME, __version__

        path = path or self.path
        if not path:
            raise ValueError("no path given")

        base = os.path.dirname(os.path.abspath(
            fileformat.ensure_extension(path, self.doc_type)))
        self._rebase(base)

        written = fileformat.write(
            path, self.doc_type, self.to_dict(), units=self.units,
            thumbnail=thumbnail if thumbnail is not None else self.thumbnail,
            created=self.created,
            application="%s %s" % (APP_NAME, __version__),
            references=[r.to_dict() for r in self.components],
            extra={self.CACHE_NAME: self.cache_dict()},
        )
        self.path = written
        self.modified = False
        return written

    @classmethod
    def load(cls, path: str) -> "DrawingDocument":
        data = fileformat.read(path, expected_type=fileformat.DRAWING)
        doc = cls()
        doc.path = os.path.abspath(path)
        doc.created = data.manifest.created
        doc.units = data.manifest.units
        doc.load_dict(data.geometry)
        doc.thumbnail = data.thumbnail
        doc.load_cache(data.extra.get(cls.CACHE_NAME) or {})
        doc.modified = False
        return doc

    def _rebase(self, base: str) -> None:
        """Rewrite every reference relative to where the drawing now lives."""
        if not base:
            return
        old = self.base_dir
        for sheet in self.sheets:
            for view in sheet.views:
                if view.kind != BASE or not view.ref.path:
                    continue
                absolute = (view.ref.resolve(old) if old
                            else (view.ref.path
                                  if os.path.isabs(view.ref.path) else None))
                if absolute:
                    view.ref.path = fileformat.relative_path(absolute, base)


def properties_for(doc: "DrawingDocument", sheet: Sheet,
                   model: Optional[Dict[str, str]] = None
                   ) -> Dict[str, str]:
    """Everything a title block field can ask for, on this sheet.

    The {Model.*} half is whatever the caller resolved from the sheet's
    first base view, because that is the model the sheet is about.  The rest
    the drawing knows on its own.
    """
    index = next((i for i, s in enumerate(doc.sheets) if s.id == sheet.id), 0)
    base = sheet.base_view()
    scale = doc.view_scale(sheet, base) if base is not None else 1.0

    out: Dict[str, str] = {
        "Drawing.Title": doc.properties.get("Title", "") or doc.title,
        "Drawing.Author": doc.properties.get("Author", ""),
        "Drawing.Company": doc.properties.get("Company", ""),
        "Drawing.Revision": doc.properties.get("Revision", ""),
        "Drawing.Date": doc.properties.get("Date", "")
                        or time.strftime("%Y-%m-%d"),
        "Drawing.FileName": os.path.basename(doc.path) if doc.path else "",
        "Sheet.Name": sheet.name,
        "Sheet.Number": str(index + 1),
        "Sheet.Count": str(len(doc.sheets)),
        "Sheet.Size": sheet.size,
        "Sheet.Scale": scale_text(scale),
        "Sheet.Standard": doc.standard,
    }
    for key in PROPERTY_KEYS:
        out.setdefault(key, "")
    out.update({k: v for k, v in (model or {}).items() if v})
    # whatever the user typed into this sheet's own fields wins over all of
    # it: a prompted field is an answer, not a suggestion
    out.update({k: v for k, v in sheet.field_values.items() if v})
    return out


def resolve_field(item: TextField, values: Dict[str, str],
                  sheet: Optional[Sheet] = None) -> str:
    """The text a field actually shows."""
    if item.kind == STATIC:
        return item.text
    if item.kind == PROMPTED:
        if sheet is not None and item.name in sheet.field_values:
            return sheet.field_values[item.name]
        return item.value or item.text
    return values.get(item.text, "")


@dataclass
class DrawingReport:
    """What happened the last time the views were generated."""

    ok: bool = True
    generated: int = 0
    cached: int = 0
    stale: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    duration: float = 0.0

    @property
    def message(self) -> str:
        if self.missing:
            return "%d view(s) cannot find their model" % len(self.missing)
        if self.errors:
            return "%d view(s) could not be drawn" % len(self.errors)
        if self.stale:
            return ("%d view(s) are out of date - press Update"
                    % len(self.stale))
        bits = []
        if self.generated:
            bits.append("%d view(s) generated" % self.generated)
        if self.cached:
            bits.append("%d from cache" % self.cached)
        return ", ".join(bits) or "Empty drawing - place a base view"


def file_hash(path: str) -> str:
    """A cheap fingerprint of a model file, to notice it has changed."""
    try:
        with open(path, "rb") as handle:
            return hashlib.blake2b(handle.read(), digest_size=16).hexdigest()
    except OSError:
        return ""
