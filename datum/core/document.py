"""The document: a parameter table plus an ordered feature tree.

Rebuilding is all-or-nothing per feature - a feature that throws is marked
with an error and skipped, and the rest of the tree still builds on whatever
body existed before it.  That is deliberate: a model with one broken fillet
should still show you the other twelve features, not a blank screen.
"""

from __future__ import annotations

import copy
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from OCP.TopoDS import TopoDS_Shape

from . import fileformat, kernel, materials, prefs
from .features import (
    FEATURE_TYPES, BuildContext, Feature, FeatureError, SketchFeature,
)
from .fileformat import FileFormatError, UnsupportedVersionError
from .params import ParameterTable
from .sketch import STANDARD_PLANES, Sketch

FILE_VERSION = fileformat.SCHEMA_VERSION
FILE_EXTENSION = fileformat.EXTENSIONS[fileformat.PART]


@dataclass
class RebuildReport:
    ok: bool = True
    errors: List[Tuple[int, str]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    duration: float = 0.0
    feature_count: int = 0

    @property
    def message(self) -> str:
        if self.errors:
            return "%d feature(s) failed - %s" % (len(self.errors),
                                                  self.errors[0][1])
        if self.warnings:
            return self.warnings[0]
        return "Rebuilt %d feature(s) in %.0f ms" % (self.feature_count,
                                                     self.duration * 1000)


class Document:
    """One part file."""

    def __init__(self) -> None:
        self.params = ParameterTable()
        self.features: List[Feature] = []
        self.shape: Optional[TopoDS_Shape] = None
        # a part may hold more than one solid; ``shape`` is all of them
        # together, which is what everything downstream still wants
        self.bodies: List[Any] = []
        self.planes: Dict[str, Any] = dict(STANDARD_PLANES)
        self.path: str = ""
        # planes the user has switched off in the browser, by name; the three
        # origin planes start visible on a new part
        self.hidden_planes: set = set()
        # once a body exists the origin planes have done their job, so they
        # are put away automatically - but only ever once, so a user who
        # turns them back on keeps them
        self.origin_autohidden = False
        self.units = "mm"
        self.doc_type = fileformat.PART
        self.created = fileformat.now()
        self.material = "Generic"
        # Title, PartNumber, Designer, Description, Revision, Company: what
        # a title block and a parts list ask of a part.  A new one is
        # stamped with whoever is at the keyboard; a loaded one keeps
        # whatever its file says, because who drew it is a fact about the
        # part and not about the machine it was opened on.
        self.properties: Dict[str, str] = prefs.prefs().stamp({})
        # "" means "whatever the material comes in", which is what somebody
        # means when they pick a material and nothing else.  Setting it is
        # an override, and it changes not one gram.
        self.appearance = ""
        self._density_override = None
        self.modified = False
        self.rollback_index: Optional[int] = None
        self._next_id = 1
        self._undo: List[str] = []
        self._redo: List[str] = []
        self.last_report = RebuildReport()
        self._sketch_cache: Dict[int, Sketch] = {}
        # set when the document came from an older schema, so the UI can say so
        self.migrated_from: Optional[int] = None
        self.thumbnail: Optional[bytes] = None

    # -- identity -----------------------------------------------------------

    @property
    def title(self) -> str:
        if self.path:
            return os.path.splitext(os.path.basename(self.path))[0]
        return "Part1"

    def new_id(self) -> int:
        i = self._next_id
        self._next_id += 1
        return i

    def unique_name(self, base: str) -> str:
        existing = {f.name for f in self.features}
        if base not in existing:
            return base
        i = 1
        while "%s%d" % (base, i) in existing:
            i += 1
        return "%s%d" % (base, i)

    # -- feature management -------------------------------------------------

    def add_feature(self, feature: Feature, index: Optional[int] = None) -> Feature:
        feature.id = feature.id or self.new_id()
        feature.name = self.unique_name(feature.name)
        if index is None:
            self.features.append(feature)
        else:
            self.features.insert(index, feature)
        self.modified = True
        return feature

    def remove_feature(self, feature_id: int) -> None:
        self.features = [f for f in self.features if f.id != feature_id]
        # drop references from patterns / mirrors that pointed at it
        for f in self.features:
            parents = getattr(f, "parents", None)
            if isinstance(parents, list) and feature_id in parents:
                parents.remove(feature_id)
        self.modified = True

    def feature(self, feature_id: int) -> Optional[Feature]:
        for f in self.features:
            if f.id == feature_id:
                return f
        return None

    def index_of(self, feature_id: int) -> int:
        for i, f in enumerate(self.features):
            if f.id == feature_id:
                return i
        return -1

    def move_feature(self, feature_id: int, delta: int) -> bool:
        i = self.index_of(feature_id)
        if i < 0:
            return False
        j = max(0, min(len(self.features) - 1, i + delta))
        if i == j:
            return False
        self.features.insert(j, self.features.pop(i))
        self.modified = True
        return True

    def sketch_features(self) -> List[SketchFeature]:
        return [f for f in self.features if isinstance(f, SketchFeature)]

    def sketch(self, feature_id: int) -> Optional[Sketch]:
        f = self.feature(feature_id)
        return f.sketch if isinstance(f, SketchFeature) else None

    def dependents_of(self, feature_id: int) -> List[Feature]:
        """Features that would break if ``feature_id`` disappeared."""
        return [f for f in self.features if feature_id in f.depends_on()]

    # -- rebuild ------------------------------------------------------------

    def rebuild(self) -> RebuildReport:
        start = time.perf_counter()
        self.params.evaluate_all()

        ctx = BuildContext(self.params.scope())
        report = RebuildReport()

        limit = (len(self.features) if self.rollback_index is None
                 else self.rollback_index)

        for i, feature in enumerate(self.features):
            feature.error = ""
            if i >= limit:
                continue
            if feature.suppressed:
                continue
            try:
                feature.build(ctx)
            except (FeatureError, kernel.KernelError) as exc:
                feature.error = str(exc)
                report.errors.append((feature.id, str(exc)))
            except Exception as exc:  # a kernel crash must not kill the app
                feature.error = "%s: %s" % (type(exc).__name__, exc)
                report.errors.append((feature.id, feature.error))

        for name, err in [(p.name, p.error) for p in self.params if p.error]:
            report.errors.append((-1, "parameter %s: %s" % (name, err)))

        self.bodies = list(ctx.bodies)
        self.shape = ctx.shape
        self._sketch_cache = dict(ctx.sketches)
        self.planes = dict(ctx.planes)
        report.warnings = list(ctx.log)
        report.ok = not report.errors
        report.duration = time.perf_counter() - start
        report.feature_count = min(limit, len(self.features))
        self.last_report = report
        return report

    def all_sketches(self) -> Dict[int, Sketch]:
        return self._sketch_cache

    def plane_names(self) -> List[str]:
        return list(self.planes)

    def plane_visible(self, name: str) -> bool:
        return name not in self.hidden_planes

    def set_plane_visible(self, name: str, visible: bool) -> None:
        if visible:
            self.hidden_planes.discard(name)
        else:
            self.hidden_planes.add(name)
        self.modified = True

    def visible_planes(self) -> Dict[str, Any]:
        return {name: plane for name, plane in self.planes.items()
                if name not in self.hidden_planes}

    def autohide_origin_planes(self) -> bool:
        """Put the origin planes away once they have served their purpose.

        That is the moment the part gets its first sketch - not its first
        body.  Waiting for a body meant they came back between finishing a
        sketch and committing the feature that used it, which just flickers.

        Returns True the one time it actually does something, so the caller
        knows to redraw.
        """
        if self.origin_autohidden:
            return False
        if self.shape is None and not self.sketch_features():
            return False
        self.origin_autohidden = True
        self.hidden_planes.update(("XY", "XZ", "YZ"))
        return True

    # -- measurements -------------------------------------------------------

    @property
    def density(self) -> float:
        """g/cm3, from the named material.

        Kept as a property rather than a stored number so a part cannot
        drift into saying it is steel while weighing like plastic.  Files
        written before materials existed carry their own density, and that
        is honoured until the material is set to something real.
        """
        if self._density_override is not None:
            return self._density_override
        return materials.library().material(self.material).density

    @density.setter
    def density(self, value: float) -> None:
        self._density_override = max(0.0, float(value))

    @property
    def material_appearance(self):
        """The appearance this document should be drawn with."""
        lib = materials.library()
        if self.appearance:
            return lib.appearance(self.appearance)
        return lib.appearance_for(self.material)

    def mass_properties(self) -> Dict[str, Any]:
        if self.shape is None:
            return {}
        vol = kernel.volume(self.shape)
        area = kernel.surface_area(self.shape)
        cx, cy, cz = kernel.centre_of_mass(self.shape)
        xmin, ymin, zmin, xmax, ymax, zmax = kernel.bounding_box(self.shape)
        return {
            "volume_mm3": vol,
            "area_mm2": area,
            "mass_g": vol / 1000.0 * self.density,
            "centre": (cx, cy, cz),
            "bbox": (xmax - xmin, ymax - ymin, zmax - zmin),
            "bbox_min": (xmin, ymin, zmin),
            "bbox_max": (xmax, ymax, zmax),
            "faces": len(kernel.faces(self.shape)),
            "edges": len(kernel.edges(self.shape)),
        }

    # -- undo / redo --------------------------------------------------------

    def snapshot(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    def push_undo(self) -> None:
        self._undo.append(self.snapshot())
        if len(self._undo) > 80:
            self._undo.pop(0)
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

    # -- serialisation ------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "units": self.units,
            "properties": dict(self.properties),
            "material": self.material,
            "appearance": self.appearance,
            # written so a file still weighs the right thing on a machine
            # whose library does not have this material
            "density": self.density,
            "next_id": self._next_id,
            "rollback": self.rollback_index,
            "hidden_planes": sorted(self.hidden_planes),
            "origin_autohidden": self.origin_autohidden,
            "parameters": self.params.to_list(),
            "features": [f.to_dict() for f in self.features],
        }

    def load_dict(self, data: Dict[str, Any]) -> None:
        self.units = data.get("units", "mm")
        self.properties = {str(k): str(v)
                           for k, v in (data.get("properties") or {}).items()}
        self.material = data.get("material", "Generic")
        self.appearance = str(data.get("appearance", ""))
        self._density_override = None
        stored = data.get("density")
        known = materials.library().materials.get(self.material)
        if stored is not None and (known is None
                                   or abs(float(stored) - known.density)
                                   > 1e-9):
            # either a material this machine has never heard of, or one
            # whose density somebody has since changed: believe the file
            self._density_override = float(stored)
        self._next_id = int(data.get("next_id", 1))
        self.rollback_index = data.get("rollback")
        self.hidden_planes = set(data.get("hidden_planes", []))
        self.origin_autohidden = bool(data.get("origin_autohidden", False))
        self.params.load(data.get("parameters", []))
        self.features = []
        for entry in data.get("features", []):
            try:
                self.features.append(Feature.from_dict(entry))
            except FeatureError:
                continue
        highest = max([f.id for f in self.features] + [0])
        self._next_id = max(self._next_id, highest + 1)

    def save(self, path: Optional[str] = None,
             thumbnail: Optional[bytes] = None) -> str:
        """Write the part as a DATUM archive, preview included."""
        from .. import APP_NAME, __version__

        path = path or self.path
        if not path:
            raise ValueError("no path given")

        written = fileformat.write(
            path, self.doc_type, self.to_dict(), units=self.units,
            thumbnail=thumbnail, created=self.created,
            application="%s %s" % (APP_NAME, __version__),
        )
        self.path = written
        self.modified = False
        return written

    @classmethod
    def load(cls, path: str) -> "Document":
        """Open a DATUM part, or a legacy .forge file, and rebuild it."""
        opened = fileformat.read(path, expected_type=fileformat.PART)
        doc = cls()
        doc.load_dict(opened.geometry)
        doc.units = opened.manifest.units or doc.units
        doc.created = opened.manifest.created or doc.created
        doc.path = path if opened.migrated_from is None else ""
        doc.modified = opened.migrated_from is not None
        doc.migrated_from = opened.migrated_from
        doc.thumbnail = opened.thumbnail
        doc.rebuild()
        return doc
