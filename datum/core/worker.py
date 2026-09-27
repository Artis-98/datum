"""The other end of the pipe: one worker process, doing what it is sent.

DATUM runs its heavy work on every core by starting a few copies of
itself in this mode. Python cannot do it with threads: the library that
talks to OpenCASCADE holds Python's lock for the whole of every call, so
two threads building two parts take turns rather than working together.
Separate processes each have their own lock.

A worker reads one request per line on stdin and answers one line per
request on its own channel. OpenCASCADE writes to the console now and
then, a STEP writer announcing "Sending all data" for instance, so the
worker's real stdout is sent to nowhere and answers go out on a private
copy of it, each line marked so nothing else can be mistaken for one.

    python -m datum.core.worker          from source
    DATUM.exe --worker                   from the installed build

Nothing here imports Qt. A worker is only geometry.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from typing import Any, Callable, Dict

MARK = "\x1eDATUM "


def _build(path: str, trusted=()) -> Dict[str, Any]:
    """Build a part or assembly file into the caches the window reads.

    The worker's answer is the body cache on disk, not the body itself:
    the window was going to look there anyway, and a body sent back down a
    pipe would be copied twice on the way for nothing.
    """
    from . import rules
    from .parts import PartLibrary

    for place in trusted or ():
        rules.trust_path(place)
    started = time.perf_counter()
    shape = PartLibrary().shape(path)
    return {"built": shape is not None,
            "seconds": round(time.perf_counter() - started, 3)}


_LIBRARY = None


def _project(path: str, direction, up, hidden: bool = True,
             trusted=()) -> Dict[str, Any]:
    """One drawing view: the model at ``path``, seen along ``direction``.

    The library is kept between requests, so the second view of the same
    model does not load it again. The lines go back at full precision:
    a view drawn here must be the view that would have been drawn there.
    """
    global _LIBRARY
    from . import hlr, rules
    from .parts import PartLibrary

    for place in trusted or ():
        rules.trust_path(place)
    if _LIBRARY is None:
        _LIBRARY = PartLibrary()
    shape = _LIBRARY.shape(path)
    projection = hlr.project(shape, tuple(direction), tuple(up),
                             hidden=hidden)
    return {"box": list(projection.box), "centre": list(projection.centre),
            "error": projection.error, "hatch": projection.hatch,
            "lines": [{"k": line.kind, "p": [list(p) for p in line.points]}
                      for line in projection.lines]}


# Documents this worker keeps a copy of, with what it has already sent
# back of each: key -> (document, {body key: shape}).  The copy keeps its
# own results between requests, so a rebuild sent here builds only what
# changed since the last one.
_MIRRORS: Dict[str, Any] = {}
_MIRROR_KEEP = 4
# a body of at least this many pieces is sent a piece at a time
PIECES_MIN = 8


def _bodies_folder() -> str:
    import tempfile
    folder = os.path.join(tempfile.gettempdir(), "datum-bodies")
    os.makedirs(folder, exist_ok=True)
    return folder


def _rebuild(key: str, path: str, data: Dict[str, Any], trusted: bool,
             rollback=None, have=(), prime: bool = False) -> Dict[str, Any]:
    """Rebuild a document the window sent, and say what came of it.

    Bodies go back as files, and only those the window has not already
    got: a sketch added at the end of a long tree sends nothing heavy
    back at all. ``prime`` builds without sending anything, to have the
    copy ready before the first real request.
    """
    import json
    import uuid
    from collections import OrderedDict
    from OCP.BinTools import BinTools
    from . import mesh
    from .document import Document
    from .features import CodeFeature

    global _MIRRORS
    if not isinstance(_MIRRORS, OrderedDict):
        _MIRRORS = OrderedDict(_MIRRORS)
    held = _MIRRORS.pop(key, None) or (Document(), {})
    document, sent = held
    _MIRRORS[key] = held
    while len(_MIRRORS) > _MIRROR_KEEP:
        _MIRRORS.popitem(last=False)

    before = {f.get("id"): json.dumps(f, sort_keys=True)
              for f in data.get("features", [])}
    document.path = path or ""
    document.load_dict(data)
    document.rules.trusted = bool(trusted)
    document.rollback_index = rollback
    report = document.rebuild()
    if prime:
        return {"primed": True, "duration": report.duration}

    features = []
    built = set(document._built_now)
    for feature in document.features:
        state: Dict[str, Any] = {"id": feature.id, "error": feature.error}
        if isinstance(feature, CodeFeature):
            state["output"] = getattr(feature, "output", "")
        if feature.id in built:
            state["cost"] = document._costs.get(feature.id)
        now = feature.to_dict()
        if json.dumps(now, sort_keys=True) != before.get(feature.id):
            state["definition"] = now
        features.append(state)

    from . import kernel
    from .document import add_name, name_of, shape_names

    have = set(have or ())
    named = shape_names(document)
    # a shape no feature can be named for keeps the name this copy made
    # up for it last time, so it is not sent again either
    for key_, shape_ in sent.items():
        add_name(named, shape_, key_)
    kept: Dict[str, Any] = {}
    # Everything the window has not got goes into one file, in order.
    # Three hundred small files cost three and a half seconds to read,
    # one file with the same in it well under half a second.
    packed: list = []
    packed_at: Dict[str, int] = {}

    def send(shape_) -> Dict[str, Any]:
        """One shape: named if the window has it, else packed for it."""
        name = name_of(named, shape_) or uuid.uuid4().hex
        kept[name] = shape_
        if name in have:
            return {"key": name}
        if name not in packed_at:
            packed_at[name] = len(packed)
            packed.append(shape_)
        return {"key": name, "pack": packed_at[name]}

    bodies = []
    for body in document.bodies:
        if body.shape is None or body.shape.IsNull():
            continue
        parts = kernel.pieces(body.shape)
        before = len(packed)
        if len(parts) >= PIECES_MIN:
            # A body of many pieces goes back a piece at a time, and only
            # the pieces the window has not got: a hole through a big
            # import sends three solids, not three hundred.
            bodies.append({"name": body.name,
                           "pieces": [send(p) for p in parts]})
            fresh = packed[before:]
            if fresh:
                # Only what goes across needs triangles, the window has the
                # rest drawn already; made to the whole body's tolerance, as
                # the window makes a new piece of it. Meshing the whole body
                # walked fifteen thousand faces to find the forty new ones.
                mesh.mesh(kernel.compound(fresh), mesh.deflection(body.shape))
        else:
            entry = send(body.shape)
            if len(packed) > before:
                mesh.mesh(body.shape)
            entry["name"] = body.name
            bodies.append(entry)
    sent.clear()
    sent.update(kept)
    pack = ""
    if packed:
        pack = os.path.join(_bodies_folder(), uuid.uuid4().hex + ".bin")
        BinTools.Write_s(kernel.compound(packed), pack)

    return {
        "pack": pack,
        "features": features,
        "bodies": bodies,
        "sketches": list(document._sketch_cache),
        "planes": {name: plane.to_dict()
                   for name, plane in document.planes.items()},
        "errors": [list(e) for e in report.errors],
        "warnings": list(report.warnings),
        "duration": report.duration,
        "feature_count": report.feature_count,
        "chain": document._chain,
    }


def sweep_bodies(age: float = 86400.0) -> None:
    """Delete results nobody collected, left by a window that crashed."""
    try:
        folder = _bodies_folder()
        cutoff = time.time() - age
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
            except OSError:
                pass
    except OSError:
        pass


def _measure(path: str, indices=()) -> Dict[str, Any]:
    """Integrate some of the pieces of a body the window wrote out.

    Each comes back as its index, volume, first moment and area, which is
    what the window adds up; the pieces keep their placement in the file,
    so the numbers are the ones the window would have got.
    """
    from OCP.BinTools import BinTools
    from OCP.TopoDS import TopoDS_Iterator, TopoDS_Shape
    from . import kernel

    whole = TopoDS_Shape()
    BinTools.Read_s(whole, path)
    parts = []
    children = TopoDS_Iterator(whole)
    while children.More():
        parts.append(children.Value())
        children.Next()
    out = []
    for index in indices:
        volume, moment = kernel._measure(parts[index], "volume")
        area = kernel._measure(parts[index], "area")[0]
        out.append([index, volume, list(moment), area])
    return {"measures": out}


def _translate(path: str) -> Dict[str, Any]:
    """Translate a STEP or IGES file into the body cache, meshed.

    The window then reads the cached translation, which takes a fraction
    of the time, instead of translating it with everything else waiting.
    """
    from . import fileio
    fileio.read_shape(path)
    return {"translated": True}


def _import_assembly(path: str, folder: str,
                     progress: str = "") -> Dict[str, Any]:
    """Read a STEP file and write it out as parts and assemblies.

    How far it has got goes into the file ``progress`` names, for the
    window to show while it waits. The parts are left for the window to
    have meshed, on every worker at once.
    """
    from . import stepimport

    last = [0.0]

    def report(message: str, done: int, total: int) -> None:
        if not progress or (time.monotonic() - last[0] < 0.1
                            and done != total):
            return
        last[0] = time.monotonic()
        try:
            with open(progress + ".new", "w", encoding="utf-8") as handle:
                json.dump({"message": message, "done": done,
                           "total": total}, handle)
            os.replace(progress + ".new", progress)
        except OSError:
            pass

    top = stepimport.import_assembly(path, folder, report, cache=False)
    # the parts, heaviest first by what they import, measured from here:
    # the window looking at files just written waits on the virus scanner
    parts_dir = os.path.join(folder, "Parts")
    parts = []
    for name in os.listdir(parts_dir):
        stem, ext = os.path.splitext(name)
        if ext.lower() != ".pdat":
            continue
        weight = os.path.getsize(os.path.join(parts_dir, name))
        brep = os.path.join(parts_dir, stem + ".brep")
        if os.path.exists(brep):
            weight += os.path.getsize(brep)
        parts.append((weight, os.path.join(parts_dir, name)))
    parts.sort(reverse=True)
    return {"top": top, "parts": [p for _w, p in parts]}


def _forget(keys=()) -> Dict[str, Any]:
    """Let go of the copies kept for these keys: their part has closed."""
    gone = 0
    for key in keys or ():
        if _MIRRORS.pop(key, None) is not None:
            gone += 1
    return {"forgotten": gone}


def _ping() -> Dict[str, Any]:
    return {"pid": os.getpid()}


OPERATIONS: Dict[str, Callable[..., Dict[str, Any]]] = {
    "build": _build,
    "project": _project,
    "rebuild": _rebuild,
    "forget": _forget,
    "import_assembly": _import_assembly,
    "translate": _translate,
    "measure": _measure,
    "ping": _ping,
}


def _lower_priority() -> None:
    """Work at below-normal priority, so the window is never the one waiting."""
    if sys.platform == "win32":
        try:
            import ctypes
            BELOW_NORMAL = 0x00004000
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            ctypes.windll.kernel32.SetPriorityClass(handle, BELOW_NORMAL)
        except Exception:
            pass
    else:
        try:
            os.nice(5)
        except Exception:
            pass


def serve() -> int:
    """Answer requests until stdin closes."""
    answers = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
    try:
        quiet = os.open(os.devnull, os.O_WRONLY)
        os.dup2(quiet, 1)
        sys.stdout = open(os.devnull, "w")
    except OSError:
        pass
    _lower_priority()

    # the expensive imports, once, before saying ready
    from . import document, kernel, mesh, parts  # noqa: F401

    def say(message: Dict[str, Any]) -> None:
        answers.write(MARK + json.dumps(message) + "\n")
        answers.flush()

    say({"id": 0, "ready": True, "pid": os.getpid()})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            continue
        ident = request.get("id")
        operation = OPERATIONS.get(request.get("op", ""))
        if operation is None:
            say({"id": ident, "ok": False,
                 "error": "unknown operation %r" % request.get("op")})
            continue
        try:
            result = operation(**(request.get("args") or {}))
            say({"id": ident, "ok": True, "result": result})
        except Exception as exc:                    # noqa: BLE001
            say({"id": ident, "ok": False,
                 "error": "%s: %s" % (type(exc).__name__, exc),
                 "trace": traceback.format_exc(limit=6)})
    return 0


if __name__ == "__main__":
    sys.exit(serve())
