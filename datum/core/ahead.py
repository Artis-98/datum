"""Building ahead: the values a slider is heading for, built before it gets there.

A dLogic form's slider rebuilds the part every time it moves. With cores
to spare, the values just past where it is are built in workers while it
is still moving, each worker on a copy of the part it keeps, so when the
slider lands on one of them the model is already made and only has to be
shown.

Nothing here changes what a rebuild gives, only when it is done. A result
is kept under a key made of everything a build reads, the whole part as a
worker is sent it, and is used only for exactly that part. A rule that
moves another parameter, a feature edited in the meantime, anything at all
different, and the key does not match, so the part is built the usual way.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import time
from collections import OrderedDict
from concurrent.futures import Future
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

# a part that rebuilds quicker than this is shown fast enough already
AHEAD_AFTER = 0.08
# results held at once; past this the oldest finished ones are let go
KEEP = 24


# what of a part a rebuild reads; its material, its properties and which
# planes are hidden make no difference to its shape
GEOMETRY = ("units", "parameters", "features", "rollback")


def digest(request: Dict[str, Any]) -> str:
    """Everything a rebuild reads, as one key."""
    data = request.get("data") or {}
    text = json.dumps([{k: data.get(k) for k in GEOMETRY},
                       request.get("rollback"), request.get("trusted"),
                       request.get("path")],
                      sort_keys=True, default=str)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def discard(result: Optional[Dict[str, Any]]) -> None:
    """Delete the file a result came in, when it is never going to be read."""
    pack = (result or {}).get("pack")
    if pack:
        try:
            os.remove(pack)
        except OSError:
            pass


def _drop(future: Future) -> None:
    """Let go of a result, now if it is here, or as soon as it arrives."""
    def gone(done: Future) -> None:
        try:
            discard(done.result())
        except Exception:
            pass
    future.add_done_callback(gone)


class BuildAhead:
    """Rebuilds of one part's nearby values, on the cores the window is not using.

    One slot per spare worker, each its own copy of the part in that
    worker, and never the worker that does the part's real rebuilds: that
    one has to be free the moment the slider stops somewhere nobody guessed.
    """

    def __init__(self, document, helpers) -> None:
        self.document = document
        self.pool = helpers
        spare = max(0, int(helpers.size) - 1)
        self.slots = ["%s@ahead%d" % (document.remote_key, i)
                      for i in range(spare)]
        self._running: Dict[str, Future] = {}
        self._held: "OrderedDict[str, Tuple[Dict[str, Any], Future]]" = \
            OrderedDict()
        # bumped from a worker's thread whenever a slot comes free
        self.freed = 0
        # whether a change to these parameters is slow enough to bother
        self._worth: Dict[Tuple[str, ...], Tuple[bool, float]] = {}
        self.started = 0
        self.used = 0
        self.closed = False

    # -- asking ------------------------------------------------------------

    def worth_it(self, names: Iterable[str] = ()) -> bool:
        """Whether the cores are there, and a change to these is slow enough.

        Slow meaning what changing them costs, the features from the first
        one that reads them onward: a slider on the last hole of a big
        import is worth building ahead for even though the import is not
        rebuilt, and one on a parameter nothing uses is not.
        """
        if self.closed or not self.slots:
            return False
        if self.pool.ready < 2:
            return False
        names = tuple(sorted(names))
        if not names:
            return True
        # asked on every slider move, and the answer only changes as the
        # features' build times do, so it is kept for a moment
        now = time.monotonic()
        known = self._worth.get(names)
        if known is None or now - known[1] > 2.0:
            known = (self.document.change_cost(names) >= AHEAD_AFTER, now)
            self._worth[names] = known
        return known[0]

    def free(self) -> int:
        """How many slots have nothing running."""
        return sum(1 for s in self.slots
                   if s not in self._running or self._running[s].done())

    def offer(self, variants: Iterable[Dict[str, Any]]) -> int:
        """Build these variants of the part, in the order given, on free slots.

        A variant is parameter names and the values they would have, with
        the rest of the part as it stands. Only as many start as there are
        free slots; one already built or building is passed over. Returns
        how many were started.
        """
        if not self.worth_it():
            return 0
        free = [s for s in self.slots
                if s not in self._running or self._running[s].done()]
        if not free:
            return 0
        base = self.document.remote_request()
        now = digest(base)
        started = 0
        for changes in variants:
            if not free:
                break
            if not self.worth_it(changes):
                continue
            request = self._variant(base, changes)
            if request is None:
                continue
            key = digest(request)
            if key == now or key in self._held:
                continue
            slot = free[0]
            if not self.pool.bind(slot, avoid=(self.document.remote_key,)):
                break
            free.pop(0)
            future = self.pool.submit_to(slot, "rebuild", **request)
            future.add_done_callback(self._came_free)
            self._running[slot] = future
            self._held[key] = (request, future)
            started += 1
        self.started += started
        self._trim()
        return started

    def _came_free(self, _future: Future) -> None:
        self.freed += 1

    @staticmethod
    def _variant(base: Dict[str, Any],
                 changes: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        data = copy.deepcopy(base["data"])
        found = 0
        for param in data.get("parameters", []):
            name = param.get("name")
            if name in changes:
                # written exactly as a form writes it, or the key would
                # never match the part the slider actually makes
                param["expression"] = repr(float(changes[name]))
                found += 1
        if found != len(changes):
            return None
        request = dict(base)
        request["data"] = data
        return request

    # -- using -------------------------------------------------------------

    def _entry(self, document) -> Tuple[Optional[str], Any]:
        if not self._held or self.closed:
            return None, None
        key = digest(document.remote_request())
        return key, self._held.get(key)

    def holds(self, document) -> bool:
        """Whether the part as it stands is built, or being built, ahead."""
        return self._entry(document)[1] is not None

    def ready(self, document) -> bool:
        """Whether the part as it stands is built ahead and waiting."""
        entry = self._entry(document)[1]
        return (entry is not None and entry[1].done()
                and entry[1].exception() is None)

    def take(self, document,
             wait: Optional[Callable[[Future], Any]] = None):
        """The part as it stands, from a build done ahead, or None.

        ``wait`` is how to wait for one still being built; without it only
        a finished one is taken. None means build it the usual way: nothing
        was built ahead for this, it failed, or it names shapes the part no
        longer holds.
        """
        key, entry = self._entry(document)
        if entry is None:
            return None
        request, future = entry
        if not future.done() and wait is None:
            return None
        del self._held[key]
        try:
            result = future.result() if future.done() else wait(future)
        except Exception:
            return None
        try:
            report = document.apply_remote(result, request)
        except Exception:
            discard(result)
            return None
        self.used += 1
        return report

    # -- letting go --------------------------------------------------------

    def _trim(self) -> None:
        while len(self._held) > KEEP:
            oldest = next((k for k, (_r, f) in self._held.items()
                           if f.done()), None)
            if oldest is None:
                break
            _request, future = self._held.pop(oldest)
            _drop(future)

    def close(self) -> None:
        """Let everything go: what is built now, what is building as it lands."""
        self.closed = True
        for _request, future in self._held.values():
            _drop(future)
        self._held.clear()
        self._running.clear()
        # the copies of the part the workers kept for this are no use now,
        # and a big part's copy is not small
        try:
            self.pool.release(self.slots)
        except Exception:
            pass

    def __len__(self) -> int:
        return len(self._held)


def around(tick: int, stride: int, top: int, count: int = 8) -> List[int]:
    """Slider positions worth building next, most likely first.

    Further along the way it is going, at the pace it is going, then a
    step either side, then back the way it came.
    """
    heading = 1 if stride >= 0 else -1
    pace = max(1, abs(stride))
    order: List[int] = []
    for k in range(1, 4):
        order.append(tick + heading * pace * k)
    order += [tick + heading, tick - heading, tick + 2 * heading,
              tick - heading * pace, tick - 2 * heading]
    out: List[int] = []
    for t in order:
        if 0 <= t <= top and t != tick and t not in out:
            out.append(t)
    return out[:count]
