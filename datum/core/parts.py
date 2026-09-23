"""Bodies loaded from referenced files, cached until the file changes.

Both the assembly editor and the CAM sheet refer to parts on disk rather
than copying them, and both rebuild often enough that re-running a part's
feature tree each time would make them unusable.  The cache lives here so
neither has to depend on the other.
"""

from __future__ import annotations

import os
from typing import Callable, Dict, Optional, Tuple

from OCP.TopoDS import TopoDS_Shape

from . import fileformat
from .fileformat import ASSEMBLY, PART, FileFormatError

MAX_NESTING = 8


class PartLibrary:
    """Referenced bodies, keyed by path and modification time.

    Keying on the file's timestamp is what makes the link live: a part
    edited in another window comes through on the next rebuild without
    anything having to notice that it changed.
    """

    def __init__(self) -> None:
        self._cache: Dict[str, Tuple[float, Optional[TopoDS_Shape]]] = {}
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
        else:
            self._cache.pop(self._key(path), None)

    def shape(self, path: str, depth: int = 0) -> Optional[TopoDS_Shape]:
        """The body a referenced file builds to, or None when it has none."""
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
            return sub.shape
        if manifest.type == PART:
            from .document import Document

            part = Document.load(path)
            part.rebuild()
            return part.shape
        raise FileFormatError(
            "%s is a %s, which has no body to place"
            % (os.path.basename(path),
               fileformat.TYPE_LABELS.get(manifest.type, manifest.type)))
