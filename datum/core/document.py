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
from .rules import RuleSet
from .features import (
    FEATURE_TYPES, Body, BuildContext, CodeFeature, Feature, FeatureError,
    ImportFeature, SketchFeature,
)
from .params import referenced_names
from .fileformat import FileFormatError, UnsupportedVersionError
from .params import ParameterTable
from .sketch import STANDARD_PLANES, Sketch

FILE_VERSION = fileformat.SCHEMA_VERSION
FILE_EXTENSION = fileformat.EXTENSIONS[fileformat.PART]

# Rebuilds reuse what did not change; DATUM_FULL_REBUILD builds everything
# every time, for comparing the two or for the day this turns out wrong.
REUSE_RESULTS = not os.environ.get("DATUM_FULL_REBUILD")
# results kept per feature: this rebuild's and the one before, which is
# what makes an undo or a redo straight after an edit free
KEEP_RESULTS = 2


def _fingerprint(*parts: Any) -> str:
    import hashlib

    digest = hashlib.sha1()
    for part in parts:
        digest.update(repr(part).encode("utf-8", "replace"))
        digest.update(b"\x00")
    return digest.hexdigest()


def _strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def _definition(feature: Feature, scope: Dict[str, float],
                base_dir: str = "") -> str:
    """Everything a feature's build reads, as one string.

    Its own definition, and the value of every parameter it names. A code
    feature can read any parameter, so it is given all of them. An import
    reads a file, so the file's size and time are part of it.
    """
    data = feature.to_dict()
    if isinstance(feature, CodeFeature):
        names = sorted(scope)
    else:
        found = set()
        for text in _strings(data):
            if len(text) > 200 or not text.strip():
                continue
            for name in referenced_names(text):
                if name in scope:
                    found.add(name)
        names = sorted(found)
    values = [(name, scope.get(name)) for name in names]
    extra: Any = None
    if isinstance(feature, ImportFeature) and feature.path:
        try:
            stat = os.stat(feature.resolved(base_dir))
            extra = (stat.st_size, stat.st_mtime_ns)
        except OSError:
            extra = "missing"
    return json.dumps(data, sort_keys=True, default=str) + repr(values) + \
        repr(extra)


class _Built:
    """What the rebuild held after one feature: enough to carry on from."""

    __slots__ = ("bodies", "sketches", "planes", "tools", "log", "error")

    @classmethod
    def take(cls, ctx: BuildContext, error: str) -> "_Built":
        built = cls()
        # the bodies by name and shape, not the Body objects: a later
        # feature changes a body in place, and this has to stay as it was
        built.bodies = [(b.name, b.shape) for b in ctx.bodies]
        built.sketches = dict(ctx.sketches)
        built.planes = dict(ctx.planes)
        built.tools = dict(ctx.tools)
        built.log = list(ctx.log)
        built.error = error
        return built

    def restore(self, ctx: BuildContext) -> None:
        ctx.bodies = [Body(name, shape) for name, shape in self.bodies]
        ctx.sketches = dict(self.sketches)
        ctx.planes = dict(self.planes)
        ctx.tools = dict(self.tools)
        ctx.log = list(self.log)


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
        # dLogic: rules belonging to this document.  They never run until
        # the document is trusted, and trust is not something a file can
        # carry in from somewhere else.
        self.rules = RuleSet()
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
        # what each feature built last time, and from what; see rebuild()
        self._built: Dict[int, List[Tuple[str, "_Built"]]] = {}
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
        # code features run only on a document somebody has trusted, and
        # the file itself has no say in that
        from .rules import document_trusted
        ctx.code_allowed = document_trusted(self)
        ctx.base_dir = (os.path.dirname(os.path.abspath(self.path))
                        if self.path else "")
        report = RebuildReport()

        limit = (len(self.features) if self.rollback_index is None
                 else self.rollback_index)

        # Every feature's result is kept with a fingerprint of what made
        # it: the feature as defined, the parameters it reads, and the
        # fingerprint of everything before it. A feature whose fingerprint
        # matches is not built again, its result is put back. So a sketch
        # drawn at the end of a long tree costs the sketch, not the tree,
        # and a hole drilled through a big import is drilled once rather
        # than after every command. The first feature that did change, and
        # everything after it, builds as it always has.
        scope = ctx.scope
        chain = _fingerprint("start", ctx.code_allowed)
        reuse = REUSE_RESULTS
        kept: Dict[int, List[Tuple[str, "_Built"]]] = {}

        for i, feature in enumerate(self.features):
            feature.error = ""
            if i >= limit:
                continue
            if feature.suppressed:
                chain = _fingerprint(chain, "suppressed", feature.id)
                continue
            if reuse:
                key = _fingerprint(chain, _definition(feature, scope,
                                                      ctx.base_dir))
                held = next((b for k, b in self._built.get(feature.id, ())
                             if k == key), None)
                if held is not None:
                    held.restore(ctx)
                    feature.error = held.error
                    if held.error:
                        report.errors.append((feature.id, held.error))
                    chain = key
                    kept.setdefault(feature.id, []).append((key, held))
                    continue
            try:
                feature.build(ctx)
            except (FeatureError, kernel.KernelError) as exc:
                feature.error = str(exc)
                report.errors.append((feature.id, str(exc)))
            except Exception as exc:  # a kernel crash must not kill the app
                feature.error = "%s: %s" % (type(exc).__name__, exc)
                report.errors.append((feature.id, feature.error))
            if reuse:
                # fingerprinted as the feature stands after building: a
                # sketch solves, a reference refreshes what it last found,
                # and the next rebuild will see it as it is now
                key = _fingerprint(chain, _definition(feature, scope,
                                                      ctx.base_dir))
                chain = key
                entries = kept.setdefault(feature.id, [])
                entries.append((key, _Built.take(ctx, feature.error)))

        if reuse:
            # this rebuild's results first, then the last one's, so an
            # undo straight after an edit finds what it is going back to
            for fid, entries in kept.items():
                for key, built in self._built.get(fid, ()):
                    if len(entries) >= KEEP_RESULTS:
                        break
                    if all(key != k for k, _b in entries):
                        entries.append((key, built))
            self._built = kept

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

    def forget_results(self) -> None:
        """Build every feature from scratch next time."""
        self._built = {}

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
        """Volume, area, mass, centre and box of the part as it stands.

        The geometry is measured once per body and kept until the body
        changes: a new material changes the mass and nothing else, and
        measuring a large import again for it cost seconds.
        """
        if self.shape is None:
            return {}
        held = getattr(self, "_measured", None)
        if held is None or not kernel.same_shape(held[0], self.shape):
            held = (self.shape, kernel.geometry_properties(self.shape))
            self._measured = held
        props = dict(held[1])
        props["mass_g"] = props["volume_mm3"] / 1000.0 * self.density
        return props

    def known_volume(self) -> Optional[float]:
        """The volume, if it has been measured for this body already."""
        held = getattr(self, "_measured", None)
        if held is None or self.shape is None \
                or not kernel.same_shape(held[0], self.shape):
            return None
        return held[1]["volume_mm3"]

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
            "rules": self.rules.to_list(),
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
        trusted = self.rules.trusted
        self.rules.load(data.get("rules"))
        # an undo step is this document's own history, not a new file, so
        # it does not take back the trust the user has already given
        self.rules.trusted = trusted
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
