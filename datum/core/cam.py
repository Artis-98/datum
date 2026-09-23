"""The CAM sheet: flat parts laid out on stock, offset, and sent to DXF.

A CAM document is a sheet of material, a tool, and a list of parts laid on
it.  The parts are *referenced*, never copied - each one keeps a relative
path to its .pdat, and every rebuild re-reads that file, re-finds the cut
face and regenerates the profile.  Edit the part, reopen the sheet, and the
toolpath has changed with it.  That link is the whole point of the module
and it deliberately owes nothing to the assembly editor.

Two things are remembered rather than recomputed, because recomputing them
would throw away decisions the user made:

    the cut face      a persistent named reference, the same mechanism a
                      fillet uses.  Re-detection only happens when it fails
                      to rebind, and then it says so.
    the layout        positions and rotations belong to the placement, not
                      to the geometry, so regenerating never moves a part
                      the user put somewhere on purpose.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from OCP.TopoDS import TopoDS, TopoDS_Face, TopoDS_Shape

from . import fileformat, kernel, sheet as sheetlib, toolpath
from .fileformat import CAM, PART, BrokenLink, ComponentRef, FileFormatError
from .naming import ShapeRef
from .params import ExpressionError, ParameterTable, evaluate
from .parts import PartLibrary
from .sheet import OUTER_KEY, Detection, Profile
from .toolpath import INSIDE, OUTSIDE, Job, Sheet, Tool, ToolpathReport

# a gap of this many tool diameters between parts, so the cutter never runs
# through one part on its way round another
GAP_DIAMETERS = 1.5


def _number(expression: str, scope: Dict[str, float], default: float) -> float:
    try:
        return evaluate(str(expression), scope)
    except ExpressionError:
        return default


@dataclass
class CutFace:
    """Which face of a part is the one lying on the sheet."""

    ref: Optional[ShapeRef] = None
    thickness: float = 0.0
    flip: bool = False
    manual: bool = False        # the user picked it; detection stays away
    # filled in by a rebuild
    warning: str = ""
    error: str = ""
    face: Optional[TopoDS_Face] = None

    @property
    def known(self) -> bool:
        return self.ref is not None

    def describe(self) -> str:
        if self.error:
            return self.error
        if not self.known:
            return "not found yet"
        return "%.2f mm thick%s%s" % (
            self.thickness, ", flipped" if self.flip else "",
            ", picked by hand" if self.manual else "")

    def to_dict(self) -> Dict[str, Any]:
        return {"ref": self.ref.to_dict() if self.ref else None,
                "thickness": self.thickness, "flip": self.flip,
                "manual": self.manual}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CutFace":
        raw = data.get("ref")
        return cls(ref=ShapeRef.from_dict(raw) if raw else None,
                   thickness=float(data.get("thickness", 0.0)),
                   flip=bool(data.get("flip", False)),
                   manual=bool(data.get("manual", False)))


@dataclass
class PlacedPart:
    """One part lying on the sheet."""

    id: int = 0
    ref: ComponentRef = field(default_factory=ComponentRef)
    name: str = ""
    position: List[float] = field(default_factory=lambda: [0.0, 0.0])
    rotation: float = 0.0                       # degrees
    mirror: bool = False
    manual: bool = False                        # moved by hand
    laid_out: bool = False                      # has ever been given a spot
    suppressed: bool = False
    cut_face: CutFace = field(default_factory=CutFace)
    sides: Dict[str, str] = field(default_factory=dict)
    # filled in by a rebuild
    shape: Optional[TopoDS_Shape] = None
    profile: Optional[Profile] = None
    error: str = ""

    @property
    def label(self) -> str:
        return self.name or self.ref.label or self.ref.name or "Part"

    @property
    def icon(self) -> str:
        return "box"

    def side_for(self, key: str, default: str) -> str:
        return self.sides.get(key, default)

    def set_side(self, key: str, side: str) -> None:
        self.sides[key] = side

    def summary(self) -> str:
        if self.error:
            return self.error
        bits = ["at %.1f, %.1f" % (self.position[0], self.position[1])]
        if abs(self.rotation) > 1e-9:
            bits.append("%.4g deg" % self.rotation)
        if self.mirror:
            bits.append("mirrored")
        if self.manual:
            bits.append("placed by hand")
        if self.profile is not None:
            bits.append("%.1f x %.1f mm" % (self.profile.width,
                                            self.profile.height))
        return "%s  -  %s" % (self.ref.name or self.ref.path, ", ".join(bits))

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "name": self.name, "ref": self.ref.to_dict(),
                "position": [float(c) for c in self.position],
                "rotation": float(self.rotation), "mirror": self.mirror,
                "manual": self.manual, "laid_out": self.laid_out,
                "suppressed": self.suppressed,
                "cut_face": self.cut_face.to_dict(), "sides": dict(self.sides)}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PlacedPart":
        return cls(id=int(data.get("id", 0)),
                   ref=ComponentRef.from_dict(data.get("ref", {})),
                   name=str(data.get("name", "")),
                   position=[float(c) for c in
                             data.get("position", (0.0, 0.0))][:2],
                   rotation=float(data.get("rotation", 0.0)),
                   mirror=bool(data.get("mirror", False)),
                   manual=bool(data.get("manual", False)),
                   laid_out=bool(data.get("laid_out", True)),
                   suppressed=bool(data.get("suppressed", False)),
                   cut_face=CutFace.from_dict(data.get("cut_face", {})),
                   sides={str(k): str(v)
                          for k, v in (data.get("sides") or {}).items()})


@dataclass
class CamReport:
    ok: bool = True
    placed: int = 0
    missing: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    duration: float = 0.0
    toolpath: ToolpathReport = field(default_factory=ToolpathReport)

    @property
    def message(self) -> str:
        if self.missing:
            return "%d part(s) missing - %s" % (len(self.missing),
                                                self.missing[0])
        if self.errors:
            return "%d problem(s) - %s" % (len(self.errors), self.errors[0])
        if self.warnings:
            return self.warnings[0]
        if not self.placed:
            return "Empty sheet - add a part to lay out"
        return "%d part(s), %s" % (self.placed, self.toolpath.message)


class CamDocument:
    """A sheet of stock with parts laid out on it, ready to cut."""

    doc_type = CAM

    def __init__(self) -> None:
        self.parts: List[PlacedPart] = []
        self.params = ParameterTable()

        # everything the operator sets, as expressions so the parameter
        # table drives a sheet the same way it drives a part
        self.sheet_width = "1500"
        self.sheet_height = "3000"
        self.sheet_thickness = "3"
        self.margin = "15"
        self.tool_diameter = "6"
        self.tool_name = ""
        self.tool_plunge = True
        self.lead_length = "0"

        self.units = "mm"
        self.path = ""
        self.created = fileformat.now()
        self.modified = False
        self.thumbnail: Optional[bytes] = None
        self.migrated_from: Optional[int] = None
        self.material = "Generic"
        self.density = 1.0

        self.library = PartLibrary()
        self.shape: Optional[TopoDS_Shape] = None
        self.last_report = CamReport()

        self._next_id = 1
        self._undo: List[str] = []
        self._redo: List[str] = []

    # ------------------------------------------------------------ identity

    @property
    def title(self) -> str:
        if self.path:
            return os.path.splitext(os.path.basename(self.path))[0]
        return "Sheet1"

    @property
    def base_dir(self) -> str:
        return os.path.dirname(os.path.abspath(self.path)) if self.path else ""

    def new_id(self) -> int:
        i = self._next_id
        self._next_id += 1
        return i

    def unique_name(self, base: str) -> str:
        taken = {p.name for p in self.parts}
        i = 1
        while "%s:%d" % (base, i) in taken:
            i += 1
        return "%s:%d" % (base, i)

    # ------------------------------------------------- settings, evaluated

    def scope(self) -> Dict[str, float]:
        return self.params.scope()

    def stock(self) -> Sheet:
        scope = self.scope()
        return Sheet(width=_number(self.sheet_width, scope, 1500.0),
                     height=_number(self.sheet_height, scope, 3000.0),
                     thickness=_number(self.sheet_thickness, scope, 3.0),
                     margin=_number(self.margin, scope, 15.0))

    def tool(self) -> Tool:
        return Tool(diameter=_number(self.tool_diameter, self.scope(), 6.0),
                    plunge=self.tool_plunge, name=self.tool_name)

    def leads(self) -> float:
        return max(0.0, _number(self.lead_length, self.scope(), 0.0))

    # ------------------------------------------------------------ contents

    @property
    def components(self) -> List[ComponentRef]:
        return [p.ref for p in self.parts]

    def part(self, part_id: int) -> Optional[PlacedPart]:
        return next((p for p in self.parts if p.id == part_id), None)

    def add_part(self, target_path: str, label: str = "",
                 base_dir: Optional[str] = None) -> PlacedPart:
        """Reference a .pdat file and lay it on the sheet."""
        base = base_dir if base_dir is not None else self.base_dir
        stem = os.path.splitext(os.path.basename(target_path))[0]
        ref = ComponentRef(
            path=(fileformat.relative_path(target_path, base) if base
                  else os.path.abspath(target_path).replace(os.sep, "/")),
            name=os.path.basename(target_path),
            label=label or stem)
        placed = PlacedPart(id=self.new_id(), ref=ref,
                            name=self.unique_name(label or stem))
        self.parts.append(placed)
        self.modified = True
        return placed

    def remove_part(self, part_id: int) -> None:
        self.parts = [p for p in self.parts if p.id != part_id]
        self.modified = True

    def active(self) -> List[PlacedPart]:
        return [p for p in self.parts if not p.suppressed]

    def part_path(self, placed: PlacedPart,
                  base_dir: Optional[str] = None) -> Optional[str]:
        base = base_dir if base_dir is not None else self.base_dir
        if not base:
            candidate = placed.ref.path
            return candidate if candidate and os.path.exists(candidate) else None
        return placed.ref.resolve(base)

    def broken_links(self, base_dir: Optional[str] = None) -> List[BrokenLink]:
        base = base_dir if base_dir is not None else self.base_dir
        if not base:
            return []
        # a CAM sheet cuts parts; an assembly is not a flat thing
        return fileformat.check_links(self.components, base, allowed=(PART,))

    # --------------------------------------------------------------- rebuild

    def rebuild(self, base_dir: Optional[str] = None) -> CamReport:
        """Re-read every part, re-find its cut face, and regenerate the path."""
        started = time.perf_counter()
        report = CamReport()
        base = base_dir if base_dir is not None else self.base_dir

        for placed in self.parts:
            placed.error = ""
            placed.shape = None
            placed.profile = None
            placed.cut_face.warning = ""
            placed.cut_face.error = ""
            placed.cut_face.face = None
            if placed.suppressed:
                continue

            path = self.part_path(placed, base)
            if path is None:
                placed.error = ("file not found: %s"
                                % (placed.ref.path or "(no path)"))
                report.missing.append(placed.label)
                continue
            try:
                placed.shape = self.library.shape(path)
            except FileFormatError as exc:
                placed.error = str(exc)
                report.errors.append("%s: %s" % (placed.label, exc))
                continue
            except Exception as exc:
                placed.error = "could not build %s: %s" % (
                    os.path.basename(path), exc)
                report.errors.append(placed.error)
                continue
            if placed.shape is None:
                placed.error = "%s has no body to cut" % placed.label
                report.errors.append(placed.error)
                continue

            self._resolve_cut_face(placed, report)

        jobs = [Job(part_id=p.id, name=p.label, profile=p.profile,
                    position=(p.position[0], p.position[1]),
                    rotation=p.rotation, mirror=p.mirror, sides=dict(p.sides))
                for p in self.active() if p.profile is not None]
        report.toolpath = toolpath.generate(jobs, self.tool(), self.stock(),
                                            self.leads())
        report.warnings.extend(report.toolpath.warnings)
        report.errors.extend(report.toolpath.errors)

        self.shape = self.preview_shape()
        report.placed = sum(1 for p in self.active() if p.profile is not None)
        report.ok = not report.missing and not report.errors
        report.duration = time.perf_counter() - started
        self.last_report = report
        return report

    def _resolve_cut_face(self, placed: PlacedPart, report: CamReport) -> None:
        """Rebind the stored face, or detect one and say that is what happened.

        The stored reference is trusted first and only re-detected when it
        genuinely cannot be found, because silently moving to a different
        face is the sort of thing that gets discovered after the material is
        cut backwards.
        """
        shape = placed.shape
        cut = placed.cut_face
        face: Optional[TopoDS_Face] = None

        if cut.ref is not None:
            try:
                found = cut.ref.rebind(shape)
            except Exception:
                found = None
            if found is not None and cut.ref.resolved:
                try:
                    candidate = TopoDS.Face_s(found)
                except Exception:
                    candidate = None
                if candidate is not None and sheetlib._plane_of(candidate):
                    face = candidate
            if face is None:
                cut.warning = (
                    "the cut face this sheet remembered is no longer on %s, "
                    "so it has been found again - check it is the right one"
                    % placed.label)

        if face is None:
            detection = sheetlib.detect_cut_face(shape)
            if not detection.ok:
                cut.error = detection.reason
                placed.error = detection.reason
                report.errors.append("%s: %s" % (placed.label,
                                                 detection.reason))
                return
            face = detection.face
            cut.ref = self._capture(shape, face)
            if cut.manual:
                # it was hand picked and that pick is gone; do not pretend
                # the new one was chosen on purpose
                cut.manual = False

        opposite = sheetlib.opposite_face(shape, face)
        if cut.flip and opposite is not None:
            # Flip is applied when the face is read, never written back into
            # the reference.  Storing the flipped face would make the toggle
            # flip again next rebuild and land back where it started.
            face, opposite = opposite, face

        cut.face = face
        cut.thickness = sheetlib.thickness_between(face, opposite)
        if cut.thickness <= 0:
            cut.thickness = sheetlib.detect_cut_face(shape).thickness

        try:
            placed.profile = sheetlib.flatten(shape, face, cut.thickness)
        except Exception as exc:
            cut.error = str(exc)
            placed.error = str(exc)
            report.errors.append("%s: %s" % (placed.label, exc))
            return

        if cut.warning:
            report.warnings.append(cut.warning)
        self._prune_sides(placed)

    @staticmethod
    def _capture(shape: TopoDS_Shape, face: TopoDS_Face) -> ShapeRef:
        pool = kernel.faces(shape)
        index = next((i for i, candidate in enumerate(pool)
                      if candidate.IsSame(face)), 0)
        return ShapeRef.capture(face, "face", index)

    @staticmethod
    def _prune_sides(placed: PlacedPart) -> None:
        """Forget cut sides for loops the part no longer has."""
        if placed.profile is None:
            return
        keys = {loop.key for loop in placed.profile.loops}
        placed.sides = {k: v for k, v in placed.sides.items() if k in keys}

    def set_cut_face(self, placed: PlacedPart, face: TopoDS_Face) -> bool:
        """Take a face the user picked as the cut face from now on."""
        if placed.shape is None or sheetlib._plane_of(face) is None:
            return False
        placed.cut_face.ref = self._capture(placed.shape, face)
        placed.cut_face.manual = True
        placed.cut_face.flip = False
        self.modified = True
        return True

    # ---------------------------------------------------------------- layout

    def gap(self) -> float:
        return GAP_DIAMETERS * self.tool().diameter

    def auto_arrange(self, only_new: bool = False) -> int:
        """Lay parts out on a grid by bounding box.

        Rows are filled left to right and wrap at the sheet width, keeping
        one and a half tool diameters between boxes so the cutter never
        crosses a neighbour, plus the clamp margin at the edges.

        ``only_new`` walks the same grid but assigns a slot only to parts
        that have never been positioned, so adding a part to a sheet that
        was already laid out by hand does not rearrange the whole thing.
        """
        stock = self.stock()
        gap = self.gap()
        # the clamp margin has to be clear of the *tool*, not just of the
        # part, and an outside cut runs a radius wider than the outline
        radius = self.tool().radius
        x0, y0, x1, _y1 = stock.inner()
        x0, y0, x1 = x0 + radius, y0 + radius, x1 - radius

        cursor_x, cursor_y, row_height, arranged = x0, y0, 0.0, 0
        for placed in self.active():
            if placed.profile is None:
                continue
            bx0, by0, bx1, by1 = sheetlib.placed_bbox(
                placed.profile, (0.0, 0.0), placed.rotation, placed.mirror)
            width, height = bx1 - bx0, by1 - by0

            if cursor_x > x0 and cursor_x + width > x1:
                cursor_x = x0
                cursor_y += row_height + gap
                row_height = 0.0

            if not (only_new and placed.laid_out):
                placed.position = [cursor_x - bx0, cursor_y - by0]
                placed.laid_out = True
                if not only_new:
                    placed.manual = False
                arranged += 1

            cursor_x += width + gap
            row_height = max(row_height, height)

        self.modified = True
        return arranged

    def move_part(self, part_id: int, position, rotation=None,
                  mirror=None) -> None:
        """Put a part somewhere by hand, and remember that it was by hand."""
        placed = self.part(part_id)
        if placed is None:
            return
        if position is not None:
            placed.position = [float(position[0]), float(position[1])]
            placed.laid_out = True
        if rotation is not None:
            placed.rotation = float(rotation) % 360.0
        if mirror is not None:
            placed.mirror = bool(mirror)
        placed.manual = True
        self.modified = True

    # -------------------------------------------------------------- preview

    def sheet_outline(self):
        """The stock, and the clamp margin inside it, as wires to draw."""
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon
        from OCP.gp import gp_Pnt

        stock = self.stock()
        out = []
        for x0, y0, x1, y1 in ((0.0, 0.0, stock.width, stock.height),
                               stock.inner()):
            builder = BRepBuilderAPI_MakePolygon()
            for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
                builder.Add(gp_Pnt(x, y, 0.0))
            builder.Close()
            if builder.IsDone():
                out.append(builder.Wire())
        return out

    def part_wires(self) -> List[Tuple[int, Any]]:
        """Every part outline where it lies, for drawing under the toolpath."""
        out: List[Tuple[int, Any]] = []
        for placed in self.active():
            if placed.profile is None:
                continue
            for loop in placed.profile.loops:
                if loop.wire is None:
                    continue
                out.append((placed.id, sheetlib.place(
                    loop.wire, placed.position, placed.rotation,
                    placed.mirror)))
        return out

    def preview_shape(self) -> Optional[TopoDS_Shape]:
        """One compound of everything on the sheet, for fitting the view."""
        shapes = [wire for _pid, wire in self.part_wires()]
        shapes.extend(self.sheet_outline())
        return kernel.compound(shapes) if shapes else None

    def mass_properties(self) -> Dict[str, Any]:
        """A sheet has no solid, so it has no mass properties."""
        return {}

    def property_rows(self) -> List[Tuple[str, str]]:
        """What the properties panel should show instead of mass and volume."""
        stock, tool = self.stock(), self.tool()
        report = self.last_report
        used = 0.0
        for placed in self.active():
            if placed.profile is not None:
                used += placed.profile.width * placed.profile.height
        area = stock.width * stock.height
        return [
            ("Sheet", "%.0f x %.0f mm" % (stock.width, stock.height)),
            ("Thickness", "%.2f mm" % stock.thickness),
            ("Clamp margin", "%.1f mm" % stock.margin),
            ("Tool", tool.label()),
            ("Lead in / out", "%.2f mm" % self.leads()),
            ("Parts", str(len(self.active()))),
            ("Cuts", str(len(report.toolpath.cuts))),
            ("Sheet used", "%.1f %%" % (100.0 * used / area) if area else "-"),
        ]

    # ---------------------------------------------------------------- export

    def export_dxf(self, path: str, force: bool = False) -> str:
        """Write the toolpath out, refusing a nest that is not sound.

        A sheet with a failed cut would export as the cuts that *did* work,
        which on the machine looks like a part that was never freed from the
        stock.  Better to refuse and say which one, unless the operator has
        deliberately asked for the rest anyway.
        """
        from . import dxf

        report = self.last_report.toolpath
        if not report.cuts:
            raise FileFormatError(
                "there is no toolpath to export - lay out at least one part "
                "that generates a cut")
        if not force and report.errors:
            raise FileFormatError(
                "%d cut(s) could not be generated, so this nest is "
                "incomplete:\n\n  %s"
                % (len(report.errors), "\n  ".join(report.errors[:6])))
        return dxf.toolpath_to_dxf(report, path)

    # ------------------------------------------------------------ edit stack

    def snapshot(self) -> str:
        return json.dumps(self.to_dict())

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

    # ------------------------------------------------------- serialisation

    def to_dict(self) -> Dict[str, Any]:
        return {
            "units": self.units,
            "next_id": self._next_id,
            "parameters": self.params.to_list(),
            "sheet": {"width": self.sheet_width, "height": self.sheet_height,
                      "thickness": self.sheet_thickness,
                      "margin": self.margin},
            "tool": {"diameter": self.tool_diameter, "name": self.tool_name,
                     "plunge": self.tool_plunge,
                     "lead_length": self.lead_length},
            "parts": [p.to_dict() for p in self.parts],
            "material": self.material,
            # the plain reference list, so anything that only wants to know
            # what this file depends on can read it without the rest
            "components": [p.ref.to_dict() for p in self.parts],
        }

    def load_dict(self, data: Dict[str, Any]) -> None:
        self.units = data.get("units", "mm")
        self.material = data.get("material", "Generic")
        self.params = ParameterTable()
        self.params.load(data.get("parameters", []))

        stock = data.get("sheet", {})
        self.sheet_width = str(stock.get("width", "1500"))
        self.sheet_height = str(stock.get("height", "3000"))
        self.sheet_thickness = str(stock.get("thickness", "3"))
        self.margin = str(stock.get("margin", "15"))

        tool = data.get("tool", {})
        self.tool_diameter = str(tool.get("diameter", "6"))
        self.tool_name = str(tool.get("name", ""))
        self.tool_plunge = bool(tool.get("plunge", True))
        self.lead_length = str(tool.get("lead_length", "0"))

        self.parts = [PlacedPart.from_dict(p) for p in data.get("parts", [])]
        for placed in self.parts:
            if not placed.name:
                placed.name = self.unique_name(
                    placed.ref.label
                    or os.path.splitext(placed.ref.name)[0] or "Part")

        known = [p.id for p in self.parts]
        self._next_id = max(int(data.get("next_id", 1)),
                            (max(known) + 1) if known else 1)
        for placed in self.parts:
            if not placed.id:
                placed.id = self.new_id()

    def save(self, path: Optional[str] = None,
             thumbnail: Optional[bytes] = None) -> str:
        from .. import APP_NAME, __version__

        path = path or self.path
        if not path:
            raise ValueError("no path given")

        previous = self.base_dir
        written_path = fileformat.ensure_extension(path, CAM)
        new_base = os.path.dirname(os.path.abspath(written_path))
        if not previous:
            self._make_relative(new_base)
        elif os.path.normcase(previous) != os.path.normcase(new_base):
            self._rebase(previous, new_base)

        written = fileformat.write(
            path, CAM, self.to_dict(), units=self.units, thumbnail=thumbnail,
            created=self.created,
            application="%s %s" % (APP_NAME, __version__),
            references=[{"path": c.path, "name": c.name}
                        for c in self.components])
        self.path = written
        self.modified = False
        return written

    def _rebase(self, old_base: str, new_base: str) -> None:
        """Save As into another folder must not break the part links."""
        for ref in self.components:
            if not ref.path or os.path.isabs(ref.path):
                continue
            absolute = os.path.normpath(os.path.join(old_base, ref.path))
            ref.path = fileformat.relative_path(absolute, new_base)

    def _make_relative(self, base: str) -> None:
        for ref in self.components:
            if ref.path and os.path.isabs(ref.path):
                ref.path = fileformat.relative_path(ref.path, base)

    @classmethod
    def load(cls, path: str) -> "CamDocument":
        opened = fileformat.read(path, expected_type=CAM)
        doc = cls()
        doc.path = path
        doc.load_dict(opened.geometry)
        doc.units = opened.manifest.units or doc.units
        doc.created = opened.manifest.created or doc.created
        doc.thumbnail = opened.thumbnail
        doc.migrated_from = opened.migrated_from
        doc.modified = opened.migrated_from is not None
        return doc
