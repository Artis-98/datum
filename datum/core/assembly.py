"""Assembly and drawing documents.

Both are real DATUM documents with the same archive layout as a part; what
differs is what ``geometry.json`` holds.  An assembly holds references to
other files rather than geometry of its own, which is why link checking
lives here.

An assembly is a list of *occurrences* - placed instances of a part or of
another assembly - and a list of constraints between them.  Nothing is
copied: the occurrence keeps a relative path, the body is loaded and cached
from the file on disk, and editing that part changes every assembly that
places it, exactly as Inventor works.

Rebuilding an assembly means three things in order: resolve every reference
to a body, re-find the faces and edges the constraints attach to, and solve
the constraints for the placements.  Each step degrades rather than fails -
a missing part marks its occurrence and the rest still builds, a constraint
whose face has been modelled away is marked and skipped.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from OCP.TopoDS import TopoDS_Shape

from . import constraints3d, fileformat, kernel, materials, prefs
from .constraints3d import (
    Frame, KIND_LABELS, Placement, ResolvedConstraint, frame_from_shape,
)
from .fileformat import (
    ASSEMBLY, DRAWING, BrokenLink, ComponentRef, FileFormatError,
)
from .naming import ShapeRef
from .rules import RuleSet
from .params import ParameterTable, evaluate, ExpressionError
from .parts import MAX_NESTING, PartLibrary          # noqa: F401  (re-export)

# how far a rebound frame's direction may have turned in the part's own
# coordinates and still be taken for the same geometry
DIRECTION_TOLERANCE = 0.9
RADIUS_TOLERANCE = 0.25          # relative


def _same_sort(cached: Frame, found: Frame) -> bool:
    """Whether a rebound frame is plausibly the one that was stored."""
    if cached is None or not cached.kind:
        return True                  # nothing to compare against yet
    if cached.kind != found.kind:
        return False
    if cached.radius > 1e-9 and abs(found.radius - cached.radius) > (
            RADIUS_TOLERANCE * cached.radius):
        return False
    if cached.kind == constraints3d.POINT:
        return True
    dot = sum(a * b for a, b in zip(cached.direction, found.direction))
    return dot > DIRECTION_TOLERANCE


def _refresh(ref: ShapeRef, shape: TopoDS_Shape,
             found: TopoDS_Shape) -> None:
    """Re-fingerprint a reference from what it actually bound to.

    Without this a face that creeps a little on every edit eventually drifts
    outside the match tolerance and the reference is lost for good.
    """
    pool = kernel.edges(shape) if ref.kind == "edge" else kernel.faces(shape)
    for i, candidate in enumerate(pool):
        if candidate.IsSame(found):
            fresh = ShapeRef.capture(found, ref.kind, i)
            ref.index = i
            ref.centre = fresh.centre
            ref.size = fresh.size
            ref.geom = fresh.geom
            ref.resolved = True
            return


# --------------------------------------------------------------------------
# the part library
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# occurrences
# --------------------------------------------------------------------------


@dataclass
class Occurrence:
    """One placed instance of a referenced file."""

    id: int = 0
    ref: ComponentRef = field(default_factory=ComponentRef)
    name: str = ""
    placement: Placement = field(default_factory=Placement)
    grounded: bool = False
    suppressed: bool = False
    visible: bool = True
    # filled in by a rebuild
    error: str = ""
    shape: Optional[TopoDS_Shape] = None

    @property
    def label(self) -> str:
        return self.name or self.ref.label or self.ref.name or "Component"

    @property
    def icon(self) -> str:
        return "pattern" if self.ref.path.lower().endswith(".adat") else "box"

    def summary(self) -> str:
        if self.error:
            return self.error
        state = []
        if self.grounded:
            state.append("grounded")
        if self.suppressed:
            state.append("suppressed")
        if not self.visible:
            state.append("hidden")
        p = self.placement.position
        state.append("at %.3g, %.3g, %.3g" % (p[0], p[1], p[2]))
        return "%s  -  %s" % (self.ref.name or self.ref.path, ", ".join(state))

    def placed(self) -> Optional[TopoDS_Shape]:
        """The body where it actually sits, without copying its geometry."""
        if self.shape is None or self.shape.IsNull():
            return None
        return self.shape.Moved(self.placement.location())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "ref": self.ref.to_dict(),
            "placement": self.placement.to_dict(),
            "grounded": self.grounded,
            "suppressed": self.suppressed,
            "visible": self.visible,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Occurrence":
        ref = ComponentRef.from_dict(data.get("ref", {}))
        placement = Placement.from_dict(data.get("placement", {}))
        if "placement" not in data and ref.transform:
            # the transform list is the older way of storing a placement
            t = list(ref.transform) + [0.0] * 6
            placement = Placement(t[0:3], t[3:6])
        return cls(id=int(data.get("id", 0)), ref=ref,
                   name=str(data.get("name", "")), placement=placement,
                   grounded=bool(data.get("grounded", False)),
                   suppressed=bool(data.get("suppressed", False)),
                   visible=bool(data.get("visible", True)))


@dataclass
class Attachment:
    """Which piece of which component a constraint holds on to."""

    occurrence: int = 0
    kind: str = "face"                       # face or edge
    ref: Optional[ShapeRef] = None
    frame: Frame = field(default_factory=Frame)

    @property
    def valid(self) -> bool:
        return self.occurrence > 0 and self.frame is not None

    def to_dict(self) -> Dict[str, Any]:
        return {"occurrence": self.occurrence, "kind": self.kind,
                "ref": self.ref.to_dict() if self.ref else None,
                "frame": self.frame.to_dict()}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Attachment":
        raw = data.get("ref")
        return cls(occurrence=int(data.get("occurrence", 0)),
                   kind=str(data.get("kind", "face")),
                   ref=ShapeRef.from_dict(raw) if raw else None,
                   frame=Frame.from_dict(data.get("frame", {})))


@dataclass
class AssemblyConstraint:
    """One relationship between two occurrences."""

    id: int = 0
    kind: str = constraints3d.MATE
    name: str = ""
    a: Attachment = field(default_factory=Attachment)
    b: Attachment = field(default_factory=Attachment)
    offset: str = "0"
    flip: bool = False
    suppressed: bool = False
    # filled in by a rebuild
    error: str = ""

    @property
    def icon(self) -> str:
        return {constraints3d.MATE: "c_coincident",
                constraints3d.FLUSH: "c_parallel",
                constraints3d.ANGLE: "dimension",
                constraints3d.TANGENT: "c_tangent",
                constraints3d.INSERT: "c_concentric"}.get(self.kind,
                                                          "c_coincident")

    def value(self, scope: Optional[Dict[str, float]] = None) -> float:
        try:
            return evaluate(self.offset or "0", scope or {})
        except ExpressionError:
            return 0.0

    def uses(self, occurrence_id: int) -> bool:
        return occurrence_id in (self.a.occurrence, self.b.occurrence)

    def summary(self) -> str:
        if self.error:
            return self.error
        unit = "deg" if self.kind == constraints3d.ANGLE else "mm"
        return "%s  %s %s%s" % (KIND_LABELS.get(self.kind, self.kind),
                                self.offset or "0", unit,
                                "  (flipped)" if self.flip else "")

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "name": self.name,
                "a": self.a.to_dict(), "b": self.b.to_dict(),
                "offset": self.offset, "flip": self.flip,
                "suppressed": self.suppressed}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AssemblyConstraint":
        return cls(id=int(data.get("id", 0)),
                   kind=str(data.get("kind", constraints3d.MATE)),
                   name=str(data.get("name", "")),
                   a=Attachment.from_dict(data.get("a", {})),
                   b=Attachment.from_dict(data.get("b", {})),
                   offset=str(data.get("offset", "0")),
                   flip=bool(data.get("flip", False)),
                   suppressed=bool(data.get("suppressed", False)))


@dataclass
class AssemblyReport:
    ok: bool = True
    dof: int = 0
    placed: int = 0
    missing: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    duration: float = 0.0
    solver: str = ""

    @property
    def message(self) -> str:
        if self.missing:
            return "%d component(s) missing - %s" % (len(self.missing),
                                                     self.missing[0])
        if self.errors:
            return self.errors[0]
        if not self.placed:
            return "Empty assembly - place a component to begin"
        return "%d component(s), %s" % (self.placed, self.solver or "placed")


# --------------------------------------------------------------------------
# the assembly document
# --------------------------------------------------------------------------


def in_frame_of(doc: "AssemblyDocument", occurrence_id: int
                ) -> List[Tuple[int, TopoDS_Shape]]:
    """Every other component, moved into one component's own frame.

    This is what lets a part be edited inside the assembly it sits in.
    The part itself must stay in its own coordinates, because that is
    where its sketches, its planes and every feature it has were built,
    and a part that moved when you opened it would put every one of them
    somewhere else. So the context moves instead: each other component is
    placed relative to the one being edited rather than to the assembly's
    origin, which looks identical on screen and leaves the part alone.

    Cheap, because nothing is copied: a location is a transform hung on a
    shape, not new geometry.
    """
    target = doc.occurrence(occurrence_id)
    if target is None:
        return []
    back = target.placement.location().Inverted()
    out: List[Tuple[int, TopoDS_Shape]] = []
    for occurrence in doc.occurrences:
        if occurrence.id == occurrence_id or occurrence.suppressed:
            continue
        if not occurrence.visible:
            continue
        shape = occurrence.shape
        if shape is None or shape.IsNull():
            continue
        out.append((occurrence.id,
                    shape.Moved(back.Multiplied(
                        occurrence.placement.location()))))
    return out


class AssemblyDocument:
    """A set of placed components, referenced by relative path."""

    doc_type = ASSEMBLY

    def __init__(self) -> None:
        self.occurrences: List[Occurrence] = []
        self.constraints: List[AssemblyConstraint] = []
        self.params = ParameterTable()
        self.units = "mm"
        self.path = ""
        self.created = fileformat.now()
        self.modified = False
        self.thumbnail: Optional[bytes] = None
        self.migrated_from: Optional[int] = None
        self.properties: Dict[str, str] = prefs.prefs().stamp({})
        self.rules = RuleSet()
        self.material = "Generic"
        # "" means "whatever the material comes in", which is what somebody
        # means when they pick a material and nothing else.  Setting it is
        # an override, and it changes not one gram.
        self.appearance = ""
        self._density_override = None

        self.library = PartLibrary()
        self.shape: Optional[TopoDS_Shape] = None
        self.last_report = AssemblyReport()

        self._next_id = 1
        self._undo: List[str] = []
        self._redo: List[str] = []

    # ------------------------------------------------------------ identity

    @property
    def title(self) -> str:
        if self.path:
            return os.path.splitext(os.path.basename(self.path))[0]
        return "Assembly1"

    @property
    def base_dir(self) -> str:
        return os.path.dirname(os.path.abspath(self.path)) if self.path else ""

    def new_id(self) -> int:
        i = self._next_id
        self._next_id += 1
        return i

    def unique_name(self, base: str) -> str:
        """Inventor's occurrence naming: Bracket:1, Bracket:2, and so on."""
        taken = {o.name for o in self.occurrences}
        i = 1
        while "%s:%d" % (base, i) in taken:
            i += 1
        return "%s:%d" % (base, i)

    # ------------------------------------------------------------ contents

    @property
    def components(self) -> List[ComponentRef]:
        """The referenced files, in placement order."""
        return [o.ref for o in self.occurrences]

    def occurrence(self, occurrence_id: int) -> Optional[Occurrence]:
        return next((o for o in self.occurrences if o.id == occurrence_id),
                    None)

    def constraint(self, constraint_id: int) -> Optional[AssemblyConstraint]:
        return next((c for c in self.constraints if c.id == constraint_id),
                    None)

    def constraints_on(self, occurrence_id: int) -> List[AssemblyConstraint]:
        return [c for c in self.constraints if c.uses(occurrence_id)]

    def place(self, target_path: str, label: str = "",
              base_dir: Optional[str] = None) -> Occurrence:
        """Place a file in the assembly, grounding the very first one.

        Inventor grounds the first component placed, and it is the right
        default: without one fixed body the whole assembly is free to drift
        wherever the solver happens to leave it.
        """
        base = base_dir if base_dir is not None else self.base_dir
        stem = os.path.splitext(os.path.basename(target_path))[0]
        ref = ComponentRef(
            # an unsaved assembly has no folder to be relative to, so the
            # absolute path is kept until the first save turns it relative
            path=(fileformat.relative_path(target_path, base) if base
                  else os.path.abspath(target_path).replace(os.sep, "/")),
            name=os.path.basename(target_path),
            label=label or stem,
        )
        occurrence = Occurrence(id=self.new_id(), ref=ref,
                                name=self.unique_name(label or stem),
                                grounded=not self.occurrences)
        self.occurrences.append(occurrence)
        self.modified = True
        return occurrence

    # the older component-level API, kept because a reference is still the
    # right unit for link checking and for the manifest
    def add_component(self, target_path: str, label: str = "",
                      base_dir: Optional[str] = None) -> ComponentRef:
        return self.place(target_path, label, base_dir).ref

    def remove_component(self, ref: ComponentRef) -> None:
        match = next((o for o in self.occurrences if o.ref is ref), None)
        if match is not None:
            self.remove_occurrence(match.id)

    def remove_occurrence(self, occurrence_id: int) -> None:
        """Drop an occurrence, and every constraint that mentioned it."""
        self.occurrences = [o for o in self.occurrences
                            if o.id != occurrence_id]
        self.constraints = [c for c in self.constraints
                            if not c.uses(occurrence_id)]
        self.modified = True

    def add_constraint(self, constraint: AssemblyConstraint
                       ) -> AssemblyConstraint:
        constraint.id = constraint.id or self.new_id()
        if not constraint.name:
            existing = sum(1 for c in self.constraints
                           if c.kind == constraint.kind) + 1
            constraint.name = "%s:%d" % (
                KIND_LABELS.get(constraint.kind, constraint.kind), existing)
        self.constraints.append(constraint)
        self.modified = True
        return constraint

    def remove_constraint(self, constraint_id: int) -> None:
        self.constraints = [c for c in self.constraints
                            if c.id != constraint_id]
        self.modified = True

    def ground(self, occurrence_id: int, grounded: bool) -> None:
        occurrence = self.occurrence(occurrence_id)
        if occurrence is not None:
            occurrence.grounded = grounded
            self.modified = True

    # ------------------------------------------------------------ link state

    def broken_links(self, base_dir: Optional[str] = None) -> List[BrokenLink]:
        base = base_dir if base_dir is not None else self.base_dir
        if not base:
            return []
        return fileformat.check_links(self.components, base)

    def resolved_components(self, base_dir: Optional[str] = None
                            ) -> List[tuple]:
        """(reference, absolute path or None) for every component."""
        base = base_dir if base_dir is not None else self.base_dir
        return [(ref, ref.resolve(base) if base else None)
                for ref in self.components]

    def component_path(self, occurrence: Occurrence,
                       base_dir: Optional[str] = None) -> Optional[str]:
        base = base_dir if base_dir is not None else self.base_dir
        if not base:
            # an unsaved assembly has nothing to be relative to, so the stored
            # path is already absolute
            candidate = occurrence.ref.path
            return candidate if candidate and os.path.exists(candidate) else None
        return occurrence.ref.resolve(base)

    # --------------------------------------------------------------- rebuild

    def active(self) -> List[Occurrence]:
        return [o for o in self.occurrences if not o.suppressed]

    def rebuild(self, base_dir: Optional[str] = None,
                depth: int = 0) -> AssemblyReport:
        """Load the bodies, re-find the constrained geometry, solve, place."""
        started = time.perf_counter()
        report = AssemblyReport()
        base = base_dir if base_dir is not None else self.base_dir

        if depth == 0:
            # every part not built yet, built on every core before the
            # assembly goes looking for them one at a time
            from .parts import leaf_parts
            wanted = []
            for occurrence in self.occurrences:
                if occurrence.suppressed:
                    continue
                path = self.component_path(occurrence, base)
                if path:
                    wanted += leaf_parts(path)
            try:
                self.library.prefetch(wanted)
            except Exception:
                pass            # the ordinary build below still builds them

        for occurrence in self.occurrences:
            occurrence.error = ""
            occurrence.shape = None
            if occurrence.suppressed:
                continue
            path = self.component_path(occurrence, base)
            if path is None:
                occurrence.error = ("file not found: %s"
                                    % (occurrence.ref.path or "(no path)"))
                report.missing.append(occurrence.label)
                continue
            try:
                occurrence.shape = self.library.shape(path, depth)
            except FileFormatError as exc:
                occurrence.error = str(exc)
                report.errors.append("%s: %s" % (occurrence.label, exc))
            except Exception as exc:                 # a part that will not build
                occurrence.error = "could not build %s: %s" % (
                    os.path.basename(path), exc)
                report.errors.append(occurrence.error)

        resolved = self._resolve_constraints(report)

        placements = {o.id: o.placement.copy() for o in self.active()
                      if o.shape is not None}
        free = [o.id for o in self.active()
                if o.shape is not None and not o.grounded]
        solved = constraints3d.solve(placements, resolved, free)
        for occurrence in self.active():
            if occurrence.id in placements:
                occurrence.placement = placements[occurrence.id]

        for constraint in self.constraints:
            if constraint.id in solved.unsatisfied and not constraint.error:
                constraint.error = "cannot be satisfied with the others"

        bodies, members = [], []
        for occurrence in self.active():
            if not occurrence.visible:
                continue
            placed = occurrence.placed()
            if placed is None:
                continue
            bodies.append(placed)
            members.append(self.component_path(occurrence, base))
        self.shape = kernel.compound(bodies) if bodies else None
        # which file each body in that compound came from, in its order, so
        # an assembly placed in another can still show every part in its
        # own colour rather than all of them in one
        self.members = members

        report.placed = sum(1 for o in self.active() if o.shape is not None)
        report.dof = solved.dof
        report.solver = solved.message
        report.ok = (not report.missing and not report.errors and solved.ok)
        report.duration = time.perf_counter() - started
        self.last_report = report
        return report

    def _resolve_constraints(self, report: AssemblyReport
                             ) -> List[ResolvedConstraint]:
        """Re-find each constrained face or edge on the body as it is now.

        When the reference rebinds, the stored frame is refreshed from what
        was found - so editing a part moves the constraint with the geometry.
        When it does not, the last known frame is kept and used anyway: a
        stale frame still holds the assembly together, which beats dropping
        the constraint the moment somebody adds a fillet.
        """
        scope = self.params.scope()
        out: List[ResolvedConstraint] = []

        for constraint in self.constraints:
            constraint.error = ""
            if constraint.suppressed:
                continue

            frames = []
            broken = False
            for attachment in (constraint.a, constraint.b):
                occurrence = self.occurrence(attachment.occurrence)
                if occurrence is None or occurrence.suppressed:
                    constraint.error = "a component it uses is not here"
                    broken = True
                    break
                if occurrence.shape is None:
                    constraint.error = "%s has no body" % occurrence.label
                    broken = True
                    break
                frames.append(self._frame_for(attachment, occurrence.shape))

            if broken:
                continue
            if any(f is None for f in frames):
                constraint.error = "the geometry it held on to is gone"
                report.errors.append("%s: %s" % (constraint.name,
                                                 constraint.error))
                continue
            if not constraints3d.compatible(frames[0], frames[1],
                                            constraint.kind):
                constraint.error = constraints3d.describe(
                    constraint.kind, frames[0], frames[1])
                report.errors.append("%s: %s" % (constraint.name,
                                                 constraint.error))
                continue

            out.append(ResolvedConstraint(
                id=constraint.id, kind=constraint.kind,
                occ_a=constraint.a.occurrence, occ_b=constraint.b.occurrence,
                frame_a=frames[0], frame_b=frames[1],
                offset=constraint.value(scope), flip=constraint.flip))
        return out

    def world_frame(self, attachment: Attachment) -> Optional[Frame]:
        """Where an attachment's geometry actually is, in the assembly.

        Frames are stored in the part's own coordinates, because that is
        what survives the component being moved; the solver applies each
        placement as it goes.  Anything that wants to *draw* the geometry -
        an arrow showing which way a face looks - needs the same sum done
        once, in world space.
        """
        if not attachment or not attachment.valid:
            return None
        occurrence = self.occurrence(attachment.occurrence)
        if occurrence is None or occurrence.shape is None:
            return None
        local = self._frame_for(attachment, occurrence.shape)
        if local is None:
            return None
        placement = occurrence.placement
        return Frame(local.kind,
                     placement.apply_point(local.origin),
                     placement.apply_direction(local.direction),
                     local.radius, local.label)

    @staticmethod
    def _frame_for(attachment: Attachment,
                   shape: TopoDS_Shape) -> Optional[Frame]:
        """The constrained geometry on the body as it stands now.

        The fingerprint match is tried first.  When it misses - a face that
        moved further than the naming tolerance allows, which a part getting
        thicker does easily - the stored index is taken instead, but only if
        what it lands on is still recognisably the same sort of thing: same
        frame kind, same radius, still facing the same way in the part's own
        coordinates.  That check is what keeps an index fallback from quietly
        mating the wrong face.
        """
        cached = attachment.frame
        ref = attachment.ref
        if ref is None:
            return cached if cached else None

        try:
            found = ref.resolve(shape)
        except Exception:
            found = None
        if found is None:
            return cached if cached else None

        frame = frame_from_shape(found)
        if frame is None or not _same_sort(cached, frame):
            return cached if cached else None

        attachment.frame = frame
        _refresh(ref, shape, found)
        return frame

    def attach(self, occurrence: Occurrence, kind: str,
               local_subshape: TopoDS_Shape) -> Optional[Attachment]:
        """Build an attachment from a picked face or edge of a component.

        ``local_subshape`` is in the component's own coordinates - the caller
        strips the placement first, which is free because the placed body
        shares its geometry with the cached original.
        """
        frame = frame_from_shape(local_subshape)
        if frame is None or occurrence.shape is None:
            return None
        pool = (kernel.edges(occurrence.shape) if kind == "edge"
                else kernel.faces(occurrence.shape))
        index = next((i for i, candidate in enumerate(pool)
                      if candidate.IsSame(local_subshape)), None)
        ref = (ShapeRef.capture(pool[index], kind, index)
               if index is not None
               else ShapeRef.capture(local_subshape, kind, 0))
        return Attachment(occurrence=occurrence.id, kind=kind, ref=ref,
                          frame=frame)

    @property
    def density(self) -> float:
        """g/cm3, from the named material, unless the file overrode it."""
        if self._density_override is not None:
            return self._density_override
        return materials.library().material(self.material).density

    @density.setter
    def density(self, value: float) -> None:
        self._density_override = max(0.0, float(value))

    @property
    def material_appearance(self):
        lib = materials.library()
        if self.appearance:
            return lib.appearance(self.appearance)
        return lib.appearance_for(self.material)

    def mass_properties(self) -> Dict[str, Any]:
        """Same shape of answer as a part, so the panel needs no special case."""
        if self.shape is None:
            return {}
        from .document import measure_everywhere
        measure_everywhere(self.shape)
        props = kernel.geometry_properties(self.shape)
        props["mass_g"] = props["volume_mm3"] / 1000.0 * self.density
        return props

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
            "properties": dict(self.properties),
            "rules": self.rules.to_list(),
            "next_id": self._next_id,
            "parameters": self.params.to_list(),
            "occurrences": [o.to_dict() for o in self.occurrences],
            "constraints": [c.to_dict() for c in self.constraints],
            "material": self.material,
            "appearance": self.appearance,
            "density": self.density,
            # a mirror of the occurrence list in the plain reference form, so
            # anything that only wants to know what this file depends on can
            # read it without understanding placements
            "components": [o.ref.to_dict() for o in self.occurrences],
        }

    def load_dict(self, data: Dict[str, Any]) -> None:
        self.units = data.get("units", "mm")
        self.properties = {str(k): str(v)
                           for k, v in (data.get("properties") or {}).items()}
        trusted = self.rules.trusted
        self.rules.load(data.get("rules"))
        self.rules.trusted = trusted
        self.material = data.get("material", "Generic")
        self.appearance = str(data.get("appearance", ""))
        self._density_override = None
        stored = data.get("density")
        known = materials.library().materials.get(self.material)
        if stored is not None and (known is None
                                   or abs(float(stored) - known.density)
                                   > 1e-9):
            self._density_override = float(stored)
        self.params = ParameterTable()
        self.params.load(data.get("parameters", []))

        raw = data.get("occurrences")
        if raw is None:
            # an assembly written before placements existed: every reference
            # becomes an occurrence at the origin
            raw = [{"ref": c} for c in data.get("components", [])]
        self.occurrences = [Occurrence.from_dict(o) for o in raw]
        self.constraints = [AssemblyConstraint.from_dict(c)
                            for c in data.get("constraints", [])]

        for occurrence in self.occurrences:
            if not occurrence.name:
                occurrence.name = self.unique_name(
                    occurrence.ref.label
                    or os.path.splitext(occurrence.ref.name)[0] or "Component")

        known = ([o.id for o in self.occurrences]
                 + [c.id for c in self.constraints])
        self._next_id = max(int(data.get("next_id", 1)),
                            (max(known) + 1) if known else 1)
        for occurrence in self.occurrences:
            if not occurrence.id:
                occurrence.id = self.new_id()

    def save(self, path: Optional[str] = None,
             thumbnail: Optional[bytes] = None) -> str:
        from .. import APP_NAME, __version__

        path = path or self.path
        if not path:
            raise ValueError("no path given")

        previous = self.base_dir
        written_path = fileformat.ensure_extension(path, ASSEMBLY)
        new_base = os.path.dirname(os.path.abspath(written_path))
        if not previous:
            # the assembly has never been saved, so its components were stored
            # as absolute paths; now there is a folder to be relative to
            self._make_relative(new_base)
        elif os.path.normcase(previous) != os.path.normcase(new_base):
            self._rebase(previous, new_base)

        # the manifest carries the reference list too, so a broken assembly
        # can be diagnosed without unpacking the model data
        written = fileformat.write(
            path, ASSEMBLY, self.to_dict(), units=self.units,
            thumbnail=thumbnail, created=self.created,
            application="%s %s" % (APP_NAME, __version__),
            references=[{"path": c.path, "name": c.name}
                        for c in self.components],
        )
        self.path = written
        self.modified = False
        return written

    def _rebase(self, old_base: str, new_base: str) -> None:
        """Keep relative component paths pointing at the same files.

        Save As into another folder would otherwise silently break every
        link, which is the single most annoying thing an assembly can do.
        """
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
    def load(cls, path: str) -> "AssemblyDocument":
        opened = fileformat.read(path, expected_type=ASSEMBLY)
        doc = cls()
        doc.path = path
        doc.load_dict(opened.geometry)
        doc.units = opened.manifest.units or doc.units
        doc.created = opened.manifest.created or doc.created
        doc.thumbnail = opened.thumbnail
        doc.migrated_from = opened.migrated_from
        doc.modified = opened.migrated_from is not None
        return doc


class DrawingDocument:
    """Sheets of views taken from a part or an assembly."""

    doc_type = DRAWING

    def __init__(self) -> None:
        self.units = "mm"
        self.sheet_size = "A3"
        self.scale = "1:1"
        self.source = ComponentRef()
        self.views: List[Dict[str, Any]] = []
        self.path = ""
        self.created = fileformat.now()
        self.modified = False
        self.thumbnail: Optional[bytes] = None
        self.migrated_from: Optional[int] = None

    @property
    def title(self) -> str:
        if self.path:
            return os.path.splitext(os.path.basename(self.path))[0]
        return "Drawing1"

    @property
    def base_dir(self) -> str:
        return os.path.dirname(os.path.abspath(self.path)) if self.path else ""

    def set_source(self, target_path: str,
                   base_dir: Optional[str] = None) -> ComponentRef:
        base = base_dir if base_dir is not None else self.base_dir
        self.source = ComponentRef(
            path=(fileformat.relative_path(target_path, base) if base
                  else os.path.basename(target_path)),
            name=os.path.basename(target_path),
            label=os.path.splitext(os.path.basename(target_path))[0],
        )
        self.modified = True
        return self.source

    def broken_links(self, base_dir: Optional[str] = None) -> List[BrokenLink]:
        base = base_dir if base_dir is not None else self.base_dir
        if not base or not self.source.path:
            return []
        return fileformat.check_links([self.source], base)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "units": self.units,
            "sheet_size": self.sheet_size,
            "scale": self.scale,
            "source": self.source.to_dict(),
            "views": list(self.views),
        }

    def load_dict(self, data: Dict[str, Any]) -> None:
        self.units = data.get("units", "mm")
        self.sheet_size = data.get("sheet_size", "A3")
        self.scale = data.get("scale", "1:1")
        self.source = ComponentRef.from_dict(data.get("source", {}))
        self.views = list(data.get("views", []))

    def save(self, path: Optional[str] = None,
             thumbnail: Optional[bytes] = None) -> str:
        from .. import APP_NAME, __version__

        path = path or self.path
        if not path:
            raise ValueError("no path given")
        references = ([{"path": self.source.path, "name": self.source.name}]
                      if self.source.path else [])
        written = fileformat.write(
            path, DRAWING, self.to_dict(), units=self.units,
            thumbnail=thumbnail, created=self.created,
            application="%s %s" % (APP_NAME, __version__),
            references=references,
        )
        self.path = written
        self.modified = False
        return written

    @classmethod
    def load(cls, path: str) -> "DrawingDocument":
        opened = fileformat.read(path, expected_type=DRAWING)
        doc = cls()
        doc.load_dict(opened.geometry)
        doc.units = opened.manifest.units or doc.units
        doc.created = opened.manifest.created or doc.created
        doc.path = path
        doc.thumbnail = opened.thumbnail
        doc.migrated_from = opened.migrated_from
        doc.modified = opened.migrated_from is not None
        return doc


def open_any(path: str):
    """Open whichever document type the file declares."""
    manifest = fileformat.peek(path)
    if manifest.type == ASSEMBLY:
        return AssemblyDocument.load(path)
    if manifest.type == DRAWING:
        return DrawingDocument.load(path)
    if manifest.type == fileformat.CAM:
        from .cam import CamDocument

        return CamDocument.load(path)
    from .document import Document
    return Document.load(path)
