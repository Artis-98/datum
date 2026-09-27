"""Bodies kept on disk, so opening an assembly does not rebuild the world.

A part file holds a feature tree and nothing else, which is the right
thing to store: it is what the user authored, it is small, and it is the
only version of the part that can be edited. The cost of that choice is
paid every time an assembly opens, because every component has to be
built again from its tree before it can be placed. On the excavator, 26
parts, that is five and a half seconds of watching a progress-free window.

Building a part and reading its finished body back are not remotely the
same price. Measured across six of the heavier parts in that assembly:

    rebuild from the tree   1.55 s
    read the body back      0.07 s       21x

So the bodies are kept. Not in the part file, which would make a document
carry a copy of itself and go stale the moment anybody edited it
elsewhere, but beside the user's own settings, keyed on the exact bytes
of the file they came from. Change the part by so much as a millimetre
and the key changes with it, so a stale body cannot be read back by
construction rather than by being careful.

What is deliberately not cached: assemblies, whose shape depends on files
other than themselves, and any part that imports external geometry, for
the same reason. A key that does not cover everything the answer depends
on is worse than no key at all.
"""

from __future__ import annotations

import hashlib
import os
import time
from typing import Optional

from OCP.BinTools import BinTools
from OCP.TopoDS import TopoDS_Shape

from . import prefs

# Bumped when anything that changes the shape of a build changes: a new
# kernel, or a fix that makes a feature build differently. Old entries
# then simply stop matching and get pruned in time.  2: bodies are kept
# with the triangles they are drawn with, so a cached part is not only
# not rebuilt but not meshed either.
CACHE_VERSION = 2

FOLDER = "bodies"
# how much disk this may use before the oldest of it goes
BUDGET = 512 * 1024 * 1024

ENABLED = not os.environ.get("DATUM_NO_BODY_CACHE")


def folder() -> str:
    return prefs.config_path(FOLDER)


def key_for(path: str) -> str:
    """A name for this exact file, as it is on disk right now.

    The whole file is hashed rather than its modification time. A part
    copied from somewhere else, or restored from a backup, keeps whatever
    timestamp it arrived with, and two files that are byte for byte the
    same build to the same body whatever their timestamps say.
    """
    digest = hashlib.sha256()
    digest.update(b"datum-body-%d\n" % CACHE_VERSION)
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 16), b""):
            digest.update(block)
    return digest.hexdigest()


def _file_for(key: str) -> str:
    return os.path.join(folder(), key[:2], key[2:] + ".bin")


def load(key: str) -> Optional[TopoDS_Shape]:
    """The body for that key, or None if it is not here or not readable."""
    if not ENABLED:
        return None
    target = _file_for(key)
    if not os.path.exists(target):
        return None
    shape = TopoDS_Shape()
    try:
        BinTools.Read_s(shape, target)
    except Exception:
        # a truncated or unreadable entry is not worth a word to anybody:
        # drop it and let the caller build
        try:
            os.remove(target)
        except OSError:
            pass
        return None
    if shape.IsNull():
        return None
    try:
        os.utime(target, None)          # it was useful, so it is recent
    except OSError:
        pass
    return shape


def store(key: str, shape: Optional[TopoDS_Shape]) -> bool:
    """Keep a body under that key.  Failure here is never worth raising."""
    if not ENABLED or shape is None or shape.IsNull():
        return False
    target = _file_for(key)
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        # written beside and moved into place, so a half written file is
        # never found by anybody
        scratch = target + ".part"
        BinTools.Write_s(shape, scratch)
        os.replace(scratch, target)
    except Exception:
        return False
    return True


def cacheable(document) -> bool:
    """Whether this document's body depends only on this document.

    An imported body comes from a file this one does not hash, so its
    build is not decided by its own bytes and it cannot be keyed on them.
    """
    for feature in getattr(document, "features", ()) or ():
        if getattr(feature, "path", ""):
            return False
    return True


def size() -> int:
    total = 0
    for base, _dirs, files in os.walk(folder()):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(base, name))
            except OSError:
                pass
    return total


def prune(budget: int = BUDGET) -> int:
    """Drop the least recently used until it fits.  Returns bytes freed."""
    entries = []
    for base, _dirs, files in os.walk(folder()):
        for name in files:
            full = os.path.join(base, name)
            try:
                stat = os.stat(full)
            except OSError:
                continue
            entries.append((stat.st_atime, stat.st_size, full))
    total = sum(e[1] for e in entries)
    if total <= budget:
        return 0
    freed = 0
    for _when, bytes_, full in sorted(entries):
        if total - freed <= budget:
            break
        try:
            os.remove(full)
            freed += bytes_
        except OSError:
            pass
    return freed


def clear() -> int:
    """Throw all of it away.  Nothing is lost: it all rebuilds."""
    freed = size()
    for base, _dirs, files in os.walk(folder()):
        for name in files:
            try:
                os.remove(os.path.join(base, name))
            except OSError:
                pass
    return freed


_last_prune = 0.0


def housekeep() -> None:
    """Keep the cache inside its budget, occasionally rather than always."""
    global _last_prune
    now = time.time()
    if now - _last_prune < 300:
        return
    _last_prune = now
    prune()
