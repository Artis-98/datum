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

from . import fileformat, kernel, materials, modelparams, modelstates, prefs
from .rules import RuleSet
from .features import (
    FEATURE_TYPES, STANDARD_AXES, Body, BuildContext, CodeFeature, Feature,
    FeatureError, ImportFeature, SketchFeature,
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

# Set by the window: a function that rebuilds a document in a worker and
# waits for it without freezing, returning its report, or None to have it
# done here after all. Left unset, as in every script and test, everything
# is built here.
REMOTE: Optional[Callable[["Document"], Optional["RebuildReport"]]] = None
# a rebuild expected to take longer than this goes to a worker
REMOTE_AFTER = 0.3
# what a feature never built here is guessed to cost
UNKNOWN_COST = 0.05
# how many worker results' shapes a document holds on to, by name
REMOTE_KEEP = 4
# Set by the window, as REMOTE is: how to wait for a worker without the
# window going dead. Left unset, waiting simply blocks.
WAIT: Optional[Callable[[Any], Any]] = None


def measure_everywhere(shape: Optional[TopoDS_Shape]) -> None:
    """Have the workers measure a big body's pieces, if there are workers."""
    if shape is None or shape.IsNull():
        return
    try:
        from . import workers
        workers.measure(shape, WAIT)
    except Exception:
        pass


def _fingerprint(*parts: Any) -> str:
    import hashlib

    digest = hashlib.sha1()
    for part in parts:
        digest.update(repr(part).encode("utf-8", "replace"))
        digest.update(b"\x00")
    return digest.hexdigest()


def _unpack(result: Dict[str, Any]) -> List[TopoDS_Shape]:
    """The shapes a worker packed into one file, in order; the file goes."""
    from OCP.BinTools import BinTools
    from OCP.TopoDS import TopoDS_Iterator

    pack = result.get("pack")
    if not pack:
        return []
    whole = TopoDS_Shape()
    try:
        BinTools.Read_s(whole, pack)
    finally:
        try:
            os.remove(pack)
        except OSError:
            pass
    out: List[TopoDS_Shape] = []
    children = TopoDS_Iterator(whole)
    while children.More():
        out.append(children.Value())
        children.Next()
    return out


def shape_names(document: "Document") -> Dict[int, list]:
    """Every shape a rebuild made, named for what made it.

    The name is the fingerprint of the tree up to the first feature that
    held the shape, with the body and the piece it is. Anything building
    the same tree, a worker or the window, arrives at the same names, so a
    piece the window already has is never sent to it again, whichever
    core built it, and what is on screen of it stays drawn.
    """
    named: Dict[int, list] = {}
    limit = (len(document.features) if document.rollback_index is None
             else document.rollback_index)
    for feature in document.features[:limit]:
        entries = document._built.get(feature.id)
        if not entries:
            continue
        # this rebuild's result comes first, the one kept from before after
        chain, built = entries[0]
        for body, shape in built.bodies:
            if shape is None or shape.IsNull():
                continue
            add_name(named, shape, _fingerprint(chain, body))
            parts = kernel.pieces(shape)
            if len(parts) > 1:
                for index, part in enumerate(parts):
                    add_name(named, part, _fingerprint(chain, body, index))
    return named


def add_name(named: Dict[int, list], shape: TopoDS_Shape, name: str) -> None:
    """Name a shape, unless it has a name already: the first one stands."""
    bucket = named.setdefault(hash(shape), [])
    if not any(held.IsEqual(shape) for held, _n in bucket):
        bucket.append((shape, name))


def name_of(named: Dict[int, list], shape: TopoDS_Shape) -> Optional[str]:
    for held, name in named.get(hash(shape), ()):
        if held.IsEqual(shape):
            return name
    return None


def _strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def _reads(feature: Feature, scope: Dict[str, float],
           data: Optional[Dict[str, Any]] = None) -> List[str]:
    """The parameters a feature's build reads, by name."""
    if isinstance(feature, CodeFeature):
        return sorted(scope)
    found = set()
    for text in _strings(feature.to_dict() if data is None else data):
        if len(text) > 200 or not text.strip():
            continue
        for name in referenced_names(text):
            if name in scope:
                found.add(name)
    return sorted(found)


def _definition(feature: Feature, scope: Dict[str, float],
                base_dir: str = "") -> str:
    """Everything a feature's build reads, as one string.

    Its own definition, and the value of every parameter it names. A code
    feature can read any parameter, so it is given all of them. An import
    reads a file, so the file's size and time are part of it.
    """
    data = feature.to_dict()
    names = _reads(feature, scope, data)
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

    __slots__ = ("bodies", "sketches", "planes", "axes", "tools", "log",
                 "error")

    @classmethod
    def take(cls, ctx: BuildContext, error: str) -> "_Built":
        built = cls()
        # the bodies by name and shape, not the Body objects: a later
        # feature changes a body in place, and this has to stay as it was
        built.bodies = [(b.name, b.shape) for b in ctx.bodies]
        built.sketches = dict(ctx.sketches)
        built.planes = dict(ctx.planes)
        built.axes = dict(ctx.axes)
        built.tools = dict(ctx.tools)
        built.log = list(ctx.log)
        built.error = error
        return built

    def restore(self, ctx: BuildContext) -> None:
        ctx.bodies = [Body(name, shape) for name, shape in self.bodies]
        ctx.sketches = dict(self.sketches)
        ctx.planes = dict(self.planes)
        ctx.axes = dict(self.axes)
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
        # the last parameter scope worked out, and from what; see
        # modelparams.evaluate_document
        self._param_cache: Dict[str, Any] = {}
        self.params = ParameterTable()
        # user parameters are worked out together with the model ones
        self.params.bind(self._resolve_parameters)
        self.features: List[Feature] = []
        self.shape: Optional[TopoDS_Shape] = None
        # a part may hold more than one solid; ``shape`` is all of them
        # together, which is what everything downstream still wants
        self.bodies: List[Any] = []
        self.planes: Dict[str, Any] = dict(STANDARD_PLANES)
        self.axes: Dict[str, Any] = dict(STANDARD_AXES)
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
        # Primary, and any other variations of this part; see modelstates
        self.model_states = modelstates.ModelStates()
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
        # how long each feature took to build, last time it was built, which
        # is how a rebuild knows in advance whether it will be slow
        self._costs: Dict[int, float] = {}
        self._built_now: List[int] = []
        # a rebuild done in a worker: see rebuild() and apply_remote()
        import uuid
        self.remote_key = uuid.uuid4().hex
        self._remote_shapes: Dict[str, TopoDS_Shape] = {}
        self._remote_recent: List[List[str]] = []
        # rebuilds of nearby parameter values done on spare cores while a
        # dLogic form is open; see ahead.BuildAhead
        self.ahead = None
        self._chain: Optional[str] = None
        self._applied_chain: Optional[str] = None
        self._named_for: Optional[str] = None
        self._rebuilding = False
        self._again = False
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
        modelparams.assign_names(self)
        self.modified = True
        return feature

    def bodies_before(self, feature_id: int) -> List[str]:
        """The solids there are just before a feature, by name, in order.

        What a feature's Solids choice and a body pattern offer: the
        bodies it could work on, not ones made further down the tree.
        Read from what the last rebuild kept of the feature before it.
        """
        index = self.index_of(feature_id)
        if index < 0:
            return [b.name for b in self.bodies if b.valid]
        for earlier in reversed(self.features[:index]):
            held = self._built.get(earlier.id)
            if held:
                return [name for name, shape in held[0][1].bodies
                        if kernel.is_valid(shape)]
            if not earlier.suppressed:
                break
        if index == 0:
            return []
        # nothing kept to read it from: what the part has now will do
        return [b.name for b in self.bodies if b.valid]

    # -- parameters ---------------------------------------------------------

    def _resolve_parameters(self) -> Dict[str, float]:
        """Work out every parameter, user and model, in one go.

        The user parameters' values and errors are written back onto them,
        so the table and the rules read the same answer the rebuild uses.
        """
        scope, errors, _model = modelparams.evaluate_document(
            self, self._param_cache)
        for param in self.params:
            param.error = errors.get(param.name, "")
            param.value = 0.0 if param.error else scope.get(param.name, 0.0)
        return scope

    def parameter_scope(self) -> Dict[str, float]:
        """Every parameter's value by name: what an expression can read."""
        return self._resolve_parameters()

    def model_parameters(self) -> List["modelparams.ModelParam"]:
        """Every dimension and feature value, named and worked out."""
        return modelparams.evaluate_document(self, self._param_cache)[2]

    def parameter_view(self) -> "modelparams.ParameterView":
        return modelparams.ParameterView(self)

    def set_parameter(self, name: str, expression: str) -> None:
        """Write an expression into any parameter, user or model."""
        modelparams.set_expression(self, name, expression)
        # the user parameters' values and errors may read it
        self.params.evaluate_all()
        self.modified = True

    def rename_parameter(self, old: str, new: str) -> None:
        modelparams.rename(self, old, new)
        self.params.evaluate_all()
        self.modified = True

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
        """Bring the body up to date with the tree.

        Where the work is done depends on how much of it there is. A
        rebuild that will take a moment is done here, as it always was. One
        that will take longer is handed to a worker when the window has
        installed one (REMOTE), and the window goes on drawing, orbiting
        and taking slider drags while it waits. Asked for again while that
        is under way, which a slider does, the request is remembered and
        answered once with whatever the tree is by then, rather than
        queued up behind itself.
        """
        if self._rebuilding:
            self._again = True
            return self.last_report
        # every dimension and every feature value gets its own name before
        # anything reads one, however the feature arrived
        modelparams.assign_names(self)
        while True:
            walked = None
            if self._applied_chain is not None and REUSE_RESULTS:
                walked = self._walk()
                if walked[0] == self._applied_chain:
                    # What a worker last sent is still the answer: all that
                    # changed since is something no feature reads, like the
                    # description a rule writes after every rebuild. Built
                    # here instead, it would be built from nothing.
                    return self._unchanged()
            ahead = self.ahead if REUSE_RESULTS else None
            if ahead is not None:
                # built already, on a core with nothing else to do, because
                # a slider was heading this way
                report = ahead.take(self)
                if report is not None:
                    return report
            remote = REMOTE
            report = None
            if remote is not None and REUSE_RESULTS:
                cost = (walked or self._walk())[1]
                if cost >= REMOTE_AFTER or (ahead is not None
                                            and ahead.holds(self)):
                    self._again = False
                    self._rebuilding = True
                    try:
                        report = remote(self)
                    except Exception:
                        report = None
                    finally:
                        self._rebuilding = False
            if report is None:
                # Built here, but guarded all the same: a build here can
                # wait for a worker too, a file being translated say, with
                # the window live, and a slider moved meanwhile must not
                # start a second build inside this one.
                self._again = False
                self._rebuilding = True
                try:
                    report = self._rebuild_here()
                finally:
                    self._rebuilding = False
            if not self._again:
                return report
            # something changed while it built: build that too

    def estimate(self) -> float:
        """Roughly how long the next rebuild will take, in seconds.

        The features whose results will be put back cost nothing; the first
        that will not, and everything after it, cost what they took last
        time. A feature never built here yet is guessed small.
        """
        return self._walk()[1]

    def _walk(self) -> Tuple[Optional[str], float]:
        """The fingerprint of the whole tree as it stands, and the estimate."""
        try:
            scope = self.parameter_scope()
        except Exception:
            return None, 0.0
        from .rules import document_trusted
        base_dir = (os.path.dirname(os.path.abspath(self.path))
                    if self.path else "")
        chain = _fingerprint("start", document_trusted(self))
        limit = (len(self.features) if self.rollback_index is None
                 else self.rollback_index)
        missed = False
        cost = 0.0
        # a feature never built here costs more on a bigger body: a cut
        # through fifteen thousand faces is not a cut through fifty; the
        # faces are counted only when there is such a feature
        unknown = None
        for i, feature in enumerate(self.features[:limit]):
            if feature.suppressed:
                chain = _fingerprint(chain, "suppressed", feature.id)
                continue
            key = _fingerprint(chain, _definition(feature, scope, base_dir))
            if not missed and not any(
                    k == key for k, _b in self._built.get(feature.id, ())):
                missed = True
            if missed:
                if feature.id not in self._costs and unknown is None:
                    unknown = UNKNOWN_COST + self._weight() * 0.5 / 10000.0
                cost += self._costs.get(feature.id, unknown)
            chain = key
        return chain, cost

    def _unchanged(self) -> RebuildReport:
        """The report of a rebuild that had nothing to do."""
        last = self.last_report
        report = RebuildReport(ok=last.ok, errors=list(last.errors),
                               warnings=list(last.warnings), duration=0.0,
                               feature_count=last.feature_count)
        self.last_report = report
        return report

    def change_cost(self, names) -> float:
        """Roughly how long a rebuild takes after these parameters change.

        Everything from the first feature that reads one of them, or reads
        a parameter worked out from one of them, to the end of the tree, at
        what each took last time it was built.
        """
        try:
            scope = self.parameter_scope()
        except Exception:
            return 0.0
        moved = modelparams.dependents(self, names)
        limit = (len(self.features) if self.rollback_index is None
                 else self.rollback_index)
        cost = 0.0
        reached = False
        for feature in self.features[:limit]:
            if feature.suppressed:
                continue
            if not reached:
                reached = bool(moved.intersection(_reads(feature, scope)))
            if reached:
                cost += self._costs.get(feature.id, UNKNOWN_COST)
        return cost

    def _weight(self) -> int:
        """How many faces the body has, measured once per body."""
        shape = self.shape
        if shape is None or shape.IsNull():
            return 0
        held = getattr(self, "_weighed", None)
        if held is not None and held[0].IsEqual(shape):
            return held[1]
        count = len(kernel.faces(shape))
        self._weighed = (shape, count)
        return count

    def _rebuild_here(self) -> RebuildReport:
        start = time.perf_counter()
        self._built_now = []

        ctx = BuildContext(self.parameter_scope())
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
            began = time.perf_counter()
            try:
                feature.build(ctx)
            except (FeatureError, kernel.KernelError) as exc:
                feature.error = str(exc)
                report.errors.append((feature.id, str(exc)))
            except Exception as exc:  # a kernel crash must not kill the app
                feature.error = "%s: %s" % (type(exc).__name__, exc)
                report.errors.append((feature.id, feature.error))
            self._costs[feature.id] = time.perf_counter() - began
            self._built_now.append(feature.id)
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
        # sent back by a worker, so the window knows what its answer is for
        self._chain = chain if reuse else None
        self._applied_chain = None

        for name, err in [(p.name, p.error) for p in self.params if p.error]:
            report.errors.append((-1, "parameter %s: %s" % (name, err)))
        # a model parameter in error is already on its feature's own
        # failure, or held at its last good value; the table shows it

        self.bodies = list(ctx.bodies)
        self.shape = ctx.shape
        self._sketch_cache = dict(ctx.sketches)
        self.planes = dict(ctx.planes)
        self.axes = dict(ctx.axes)
        report.warnings = list(ctx.log)
        report.ok = not report.errors
        report.duration = time.perf_counter() - start
        report.feature_count = min(limit, len(self.features))
        self.last_report = report
        return report

    def forget_results(self) -> None:
        """Build every feature from scratch next time."""
        self._built = {}

    # -- rebuilt somewhere else ---------------------------------------------

    def remote_request(self, prime: bool = False) -> Dict[str, Any]:
        """Everything a worker needs to rebuild this document as it is."""
        from .rules import document_trusted
        if self._chain is not None and self._named_for != self._chain:
            # Built here last. What it holds is named the way a worker
            # names it, so the worker sends back only what is new: the
            # first cut through a big import sent all three hundred
            # solids, redrawn from scratch, to change three of them.
            named = shape_names(self)
            self._keep_remote({name: shape for bucket in named.values()
                               for shape, name in bucket})
            self._named_for = self._chain
        return {"key": self.remote_key, "path": self.path or "",
                "data": self.to_dict(), "trusted": document_trusted(self),
                "rollback": self.rollback_index,
                "have": sorted(self._remote_shapes), "prime": prime}

    def apply_remote(self, result: Dict[str, Any],
                     request: Dict[str, Any]) -> RebuildReport:
        """Take a worker's rebuild as this document's own.

        Bodies come back as files only when they changed; one the document
        already holds is named, not sent. What a build wrote back into a
        feature, a solved sketch or a reference that followed its face, is
        taken only if nobody has edited that feature in the meantime.

        The shapes are gathered before anything is changed: a result that
        names a shape this document has since let go of raises LookupError
        and leaves the document as it was, to be rebuilt some other way.
        """
        from . import mesh
        from .features import Body, CodeFeature

        unpacked = _unpack(result)
        shapes: Dict[str, TopoDS_Shape] = {}

        def take(entry) -> TopoDS_Shape:
            key = entry["key"]
            shape = self._remote_shapes.get(key)
            if shape is None:
                if "pack" not in entry:
                    raise LookupError("a shape this document no longer has")
                shape = unpacked[entry["pack"]]
                # meshed in the worker before it was sent, so a face
                # without triangles is one the mesher gave up on there
                mesh.accept_stored(shape)
            shapes[key] = shape
            return shape

        bodies = []
        for entry in result.get("bodies", []):
            if "pieces" in entry:
                # a body sent a piece at a time: the pieces the window
                # already had are the very same objects, so what is drawn
                # of them stays drawn
                parts = [take(p) for p in entry["pieces"]]
                shape = kernel.compound(parts)
            else:
                shape = take(entry)
            bodies.append(Body(entry["name"], shape))

        sent = {f.get("id"): json.dumps(f, sort_keys=True)
                for f in request["data"].get("features", [])}
        for state in result.get("features", []):
            feature = self.feature(state.get("id"))
            if feature is None:
                continue
            feature.error = state.get("error", "")
            if isinstance(feature, CodeFeature) and "output" in state:
                feature.output = state.get("output", "")
            if state.get("cost") is not None:
                self._costs[feature.id] = float(state["cost"])
            written = state.get("definition")
            if written and json.dumps(feature.to_dict(), sort_keys=True) \
                    == sent.get(feature.id):
                feature.load_fields(written)

        self._keep_remote(shapes)
        # the tree this answer is for; see rebuild()
        self._applied_chain = result.get("chain")
        self.bodies = bodies
        live = [b for b in bodies if b.valid]
        self.shape = (None if not live else live[0].shape if len(live) == 1
                      else kernel.compound([b.shape for b in live]))
        self._sketch_cache = {
            fid: self.feature(fid).sketch for fid in result.get("sketches", [])
            if self.feature(fid) is not None
            and hasattr(self.feature(fid), "sketch")}
        from .sketch import SketchPlane
        self.planes = {name: SketchPlane.from_dict(data)
                       for name, data in result.get("planes", {}).items()}
        from .features import STANDARD_AXES, WorkAxis
        self.axes = dict(STANDARD_AXES)
        self.axes.update({name: WorkAxis.from_dict(data)
                          for name, data in result.get("axes", {}).items()})
        report = RebuildReport()
        report.errors = [tuple(e) for e in result.get("errors", [])]
        report.warnings = list(result.get("warnings", []))
        report.ok = not report.errors
        report.duration = float(result.get("duration", 0.0))
        report.feature_count = int(result.get("feature_count", 0))
        self.last_report = report
        return report

    def _keep_remote(self, shapes: Dict[str, TopoDS_Shape]) -> None:
        """Hold on to the shapes of the last few results, by name.

        Not just the last one: a result built ahead was asked for a moment
        ago, and names what the window held then. Anything no recent result
        names is let go.
        """
        self._remote_recent.insert(0, list(shapes))
        del self._remote_recent[REMOTE_KEEP:]
        pool = dict(self._remote_shapes)
        pool.update(shapes)
        wanted = set()
        for keys in self._remote_recent:
            wanted.update(keys)
        self._remote_shapes = {k: pool[k] for k in wanted if k in pool}

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
            measure_everywhere(self.shape)
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
            "model_states": self.model_states.to_dict(self),
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
        self.model_states.load(data.get("model_states"))
        # a part saved when every sketch counted from d1 is given names
        # that run across the whole part, and the parameters worked out
        # again now that the features they may read are here
        modelparams.assign_names(self)
        self.params.evaluate_all()

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
