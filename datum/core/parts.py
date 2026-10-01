"""Bodies loaded from referenced files, cached until the file changes.

Both the assembly editor and the CAM sheet refer to parts on disk rather
than copying them, and both rebuild often enough that re-running a part's
feature tree each time would make them unusable.  The cache lives here so
neither has to depend on the other.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from OCP.TopoDS import TopoDS_Shape

from . import fileformat
from .fileformat import ASSEMBLY, PART, FileFormatError

MAX_NESTING = 8


# Below this many parts to build, and with no workers running, the build
# stays in the window: starting workers would cost more than it saved.
PREFETCH_START = 30


def _weight(path: str) -> int:
    """Roughly how much building a part is: its file and what it imports."""
    from . import bodycache
    try:
        total = os.path.getsize(path)
        for place in bodycache.imported_files(path):
            if os.path.exists(place):
                total += os.path.getsize(place)
        return total
    except Exception:
        return 0


def leaf_parts(path: str, depth: int = 0, seen=None) -> List[str]:
    """Every part file an assembly places, sub-assemblies opened up.

    Read from the files alone, without building anything, so the parts
    can be handed out to be built before the assembly is.
    """
    seen = set() if seen is None else seen
    key = os.path.normcase(os.path.abspath(path))
    if key in seen or depth > MAX_NESTING or not os.path.isfile(path):
        return []
    seen.add(key)
    if not path.lower().endswith(".adat"):
        return [path]
    try:
        geometry = fileformat.read(path).geometry
    except Exception:
        return []
    base = os.path.dirname(os.path.abspath(path))
    out: List[str] = []
    for component in geometry.get("components", []) or []:
        place = str(component.get("path") or "")
        if not place:
            continue
        if not os.path.isabs(place):
            place = os.path.normpath(os.path.join(base, place))
        out += leaf_parts(place, depth + 1, seen)
    return out


class PartLibrary:
    """Referenced bodies, keyed by path and modification time.

    Keying on the file's timestamp is what makes the link live: a part
    edited in another window comes through on the next rebuild without
    anything having to notice that it changed.
    """

    def __init__(self) -> None:
        self._cache: Dict[str, Tuple[float, Optional[TopoDS_Shape]]] = {}
        # appearances, keyed the same way: an assembly wants the
        # colour without rebuilding the whole feature tree for it
        self._looks: Dict[str, Tuple[float, Any]] = {}
        # for an assembly: the files its bodies came from, in order
        self._members: Dict[str, List[Optional[str]]] = {}
        # Set by the window when documents are open in tabs: a part being
        # edited in its own tab is what an assembly should use, not the older
        # copy sitting on disk.  The provider decides *when* that edit becomes
        # visible, which is what makes Local Update a deliberate act rather
        # than the assembly twitching on every keystroke next door.
        self.provider: Optional[Callable[[str], Optional[TopoDS_Shape]]] = None

    @staticmethod
    def _key(path: str) -> str:
        return os.path.normcase(os.path.abspath(path))

    def forget(self, path: Optional[str] = None) -> None:
        if path is None:
            self._cache.clear()
            self._looks.clear()
        else:
            key = self._key(path)
            self._cache.pop(key, None)
            self._looks.pop(key, None)
            # and the same part in any of its model states
            states = key + "\x00"
            for held in [k for k in self._cache if k.startswith(states)]:
                self._cache.pop(held, None)

    def appearance(self, path: str):
        """What the part in this file is meant to look like.

        Read from the file rather than by rebuilding it, because an
        assembly only needs the colour and rebuilding every component's
        feature tree to find one out would be absurd.  Cached on the
        timestamp the same way bodies are, so recolouring a part shows up
        in the assembly on the next rebuild.
        """
        from . import fileformat, materials

        key = self._key(path)
        try:
            stamp = os.path.getmtime(path)
        except OSError:
            return materials.library().appearance("Default")

        hit = self._looks.get(key)
        if hit is not None and hit[0] == stamp:
            return hit[1]

        look = materials.library().appearance("Default")
        try:
            geometry = fileformat.read(path).geometry
            named = str(geometry.get("appearance") or "")
            if named:
                look = materials.library().appearance(named)
            else:
                look = materials.library().appearance_for(
                    str(geometry.get("material") or "Generic"))
        except Exception:
            pass
        self._looks[key] = (stamp, look)
        return look

    def is_fresh(self, path: str) -> bool:
        """Whether this file's body is already to hand, here or on disk."""
        from . import bodycache

        hit = self._cache.get(self._key(path))
        try:
            if hit is not None and hit[0] == os.path.getmtime(path):
                return True
        except OSError:
            return False
        try:
            return bodycache.has(bodycache.key_for_part(path))
        except Exception:
            return False

    def prefetch(self, paths: Iterable[str], force: bool = False,
                 wait=None, each=None) -> int:
        """Build the parts that are not built yet, on every core at once.

        Parts are independent of each other, so an assembly's missing
        bodies can all be built together, each in its own worker, instead
        of one after another in the window. The workers leave their bodies
        in the body cache, which the ordinary build then reads in a moment.
        Returns how many were built this way; nothing is lost when it is
        none, because the ordinary build still builds whatever is missing.
        The biggest go first, so no core is left with a big one at the end
        while the others are done. ``wait`` and ``each`` are as for
        Pool.map: how to wait, and who to tell how far it has got.
        """
        from . import rules, workers

        wanted, seen = [], set()
        for path in paths:
            key = self._key(path)
            if key in seen or not os.path.isfile(path):
                continue
            seen.add(key)
            if path.lower().endswith(".pdat") and not self.is_fresh(path):
                wanted.append(os.path.abspath(path))
        held = workers.pool()
        if held is None or not wanted:
            return 0
        # Starting workers takes a second or two, so it is only worth it
        # for a good number of parts, unless they are running already.
        if not force and held.running == 0 and len(wanted) < PREFETCH_START:
            return 0
        trusted = sorted(rules.trusted_paths())
        wanted.sort(key=_weight, reverse=True)
        results = held.map("build", [{"path": p, "trusted": trusted}
                                     for p in wanted], wait=wait, each=each)
        return sum(1 for r in results if isinstance(r, dict) and r.get("built"))

    def looks(self, path: str, depth: int = 0):
        """What an assembly's parts look like, in the order of its bodies.

        A list with an appearance for each part and a nested list for each
        sub-assembly, matching the compound the assembly builds, so whoever
        draws it can colour each part as itself. None for a part, which
        has one look and is asked for it with appearance().
        """
        members = self._members.get(self._key(path))
        if members is None or depth > MAX_NESTING:
            return None
        out = []
        for member in members:
            if not member:
                out.append(None)
            elif self._key(member) in self._members:
                out.append(self.looks(member, depth + 1) or None)
            else:
                out.append(self.appearance(member))
        return out

    def shape(self, path: str, depth: int = 0,
              state: str = "") -> Optional[TopoDS_Shape]:
        """The body a referenced file builds to, or None when it has none.

        ``state`` asks for one of a part's model states rather than the one
        it was saved in; see modelstates.
        """
        if state:
            return self._state_shape(path, state)
        if self.provider is not None:
            live = self.provider(path)
            if live is not None:
                return live

        key = self._key(path)
        try:
            stamp = os.path.getmtime(path)
        except OSError:
            self._cache.pop(key, None)
            return None

        hit = self._cache.get(key)
        if hit is not None and hit[0] == stamp:
            return hit[1]

        shape = self._build(path, depth)
        self._cache[key] = (stamp, shape)
        return shape

    def _state_shape(self, path: str, state: str) -> Optional[TopoDS_Shape]:
        """A part as one of its model states, built and kept like any other.

        Kept under the part's own key with the state's name added, so the
        same file in three states is three bodies in the cache, each built
        once.
        """
        import hashlib
        from . import bodycache, mesh
        from .modelstates import PRIMARY

        key = self._key(path) + "\x00" + state
        try:
            stamp = os.path.getmtime(path)
        except OSError:
            self._cache.pop(key, None)
            return None
        hit = self._cache.get(key)
        if hit is not None and hit[0] == stamp:
            return hit[1]

        stored = ""
        try:
            stored = hashlib.sha256(
                (bodycache.key_for_part(path) + "|state:" + state)
                .encode("utf-8")).hexdigest()
        except Exception:
            stored = ""
        shape = bodycache.load(stored) if stored else None
        if shape is not None:
            mesh.accept_stored(shape)
        else:
            from .document import Document

            opened = fileformat.read(path, expected_type=PART)
            part = Document()
            part.load_dict(opened.geometry)
            part.path = path
            if state not in part.model_states:
                raise FileFormatError(
                    "%s has no model state called %s"
                    % (os.path.basename(path), state))
            if state != part.model_states.active:
                part.model_states.activate(part, state if state else PRIMARY)
            part.rebuild()
            shape = part.shape
            mesh.mesh(shape)
            if stored and part.last_report.ok and bodycache.cacheable(part):
                bodycache.store(stored, shape)
        self._cache[key] = (stamp, shape)
        return shape

    def _build(self, path: str, depth: int) -> Optional[TopoDS_Shape]:
        if depth > MAX_NESTING:
            raise FileFormatError(
                "%s is nested more than %d assemblies deep - check for a "
                "component that places itself" % (os.path.basename(path),
                                                  MAX_NESTING))
        manifest = fileformat.peek(path)
        if manifest.type == ASSEMBLY:
            # imported here rather than at the top: an assembly is made of
            # parts, so the other direction would be a cycle
            from .assembly import AssemblyDocument

            sub = AssemblyDocument.load(path)
            sub.library = self
            sub.rebuild(depth=depth + 1)
            self._members[self._key(path)] = list(
                getattr(sub, "members", []))
            return sub.shape
        if manifest.type == PART:
            from . import bodycache, mesh
            from .document import Document

            # The finished body, if this exact file has been built before.
            # Rebuilding a part from its tree costs about twenty times
            # what reading the answer back does, and an assembly pays that
            # for every component it places.
            key = ""
            try:
                key = bodycache.key_for_part(path)
            except Exception:
                key = ""
            if key:
                held = bodycache.load(key)
                if held is not None:
                    mesh.accept_stored(held)
                    return held

            # load builds it; building it again was half of every cold open
            part = Document.load(path)
            # meshed now, on every core, so the triangles are kept with the
            # body and the next open neither builds nor meshes it
            mesh.mesh(part.shape)
            if key and part.last_report.ok and bodycache.cacheable(part):
                bodycache.store(key, part.shape)
                bodycache.housekeep()
            return part.shape
        raise FileFormatError(
            "%s is a %s, which has no body to place"
            % (os.path.basename(path),
               fileformat.TYPE_LABELS.get(manifest.type, manifest.type)))
