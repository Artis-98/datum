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


def _ping() -> Dict[str, Any]:
    return {"pid": os.getpid()}


OPERATIONS: Dict[str, Callable[..., Dict[str, Any]]] = {
    "build": _build,
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
