"""Copies of DATUM that do geometry on the cores the window is not using.

Python runs one thread at a time, and the library DATUM talks to
OpenCASCADE through holds that lock for the whole of every call, so
threads cannot build two parts at once. Processes can: each has its own
lock. This keeps a few of them, started on demand, fed one request at a
time each, and hands back a future per request.

What they cost is the reason for the numbers below. A worker is about
230 MB once the geometry libraries are loaded and takes a second or two
to start, so there are never more of them than the machine has spare
cores or memory for, they start only when there is work worth starting
them for, they run below normal priority so the window never waits on
them, and they go away after twenty minutes with nothing to do.

``DATUM_NO_WORKERS`` switches all of it off: everything then runs in the
window's own process, as it always did.
"""

from __future__ import annotations

import atexit
import itertools
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import Future
from typing import Any, Dict, Iterable, List, Optional

from .worker import MARK

ENABLED = not os.environ.get("DATUM_NO_WORKERS")

# a worker with nothing to do for this long is let go, memory and all
IDLE_SECONDS = 1200
# how long a worker is given to load before it is written off
START_SECONDS = 60
# how many workers a request may try, should the first die under it
RETRIES = 2


def _memory_gb() -> float:
    try:
        if sys.platform == "win32":
            import ctypes

            class Status(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong),
                            ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            status = Status()
            status.dwLength = ctypes.sizeof(Status)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
            return status.ullTotalPhys / 1024 ** 3
        pages = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
        return pages / 1024 ** 3
    except Exception:
        return 8.0


def capacity() -> int:
    """How many workers this machine can carry.

    One core is left for the window. A worker wants about a quarter of a
    gigabyte and the models it holds want more, so one per three
    gigabytes of memory, and never more than six: past that, building
    the parts is no longer what anybody is waiting for.
    """
    forced = os.environ.get("DATUM_WORKERS")
    if forced:
        try:
            return max(0, int(forced))
        except ValueError:
            pass
    cores = max(1, (os.cpu_count() or 2) - 1)
    return max(1, min(cores, int(_memory_gb() // 3), 6))


def _command() -> List[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--worker"]
    return [sys.executable, "-m", "datum.core.worker"]


def _environment() -> Dict[str, str]:
    env = dict(os.environ)
    if not getattr(sys, "frozen", False):
        # the package, found from wherever this copy of it is
        root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    return env


class WorkerLost(RuntimeError):
    """The worker stopped before it answered, and took the request with it."""


class _Worker:
    """One process, one request at a time."""

    def __init__(self) -> None:
        flags = 0
        if sys.platform == "win32":
            flags = (subprocess.CREATE_NO_WINDOW
                     | subprocess.BELOW_NORMAL_PRIORITY_CLASS)
        log = os.environ.get("DATUM_WORKER_LOG")
        self.process = subprocess.Popen(
            _command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=open(log, "a") if log else subprocess.DEVNULL,
            env=_environment(), creationflags=flags)
        self.ready = threading.Event()
        self.pending: Dict[int, Future] = {}
        self.lock = threading.Lock()
        self.alive = True
        self.pid = self.process.pid
        threading.Thread(target=self._read, name="datum-worker-%d" % self.pid,
                         daemon=True).start()

    @property
    def load(self) -> int:
        return len(self.pending)

    def _read(self) -> None:
        stream = self.process.stdout
        try:
            for raw in iter(stream.readline, b""):
                line = raw.decode("utf-8", "replace")
                start = line.find(MARK)
                if start < 0:
                    continue
                try:
                    message = json.loads(line[start + len(MARK):])
                except ValueError:
                    continue
                if message.get("ready"):
                    self.ready.set()
                    continue
                with self.lock:
                    future = self.pending.pop(message.get("id"), None)
                if future is None or future.done():
                    continue
                if message.get("ok"):
                    future.set_result(message.get("result"))
                else:
                    future.set_exception(RuntimeError(
                        message.get("error") or "the worker failed"))
        except Exception:
            pass
        self._lost()

    def _lost(self) -> None:
        self.alive = False
        self.ready.set()
        with self.lock:
            orphans = list(self.pending.values())
            self.pending.clear()
        for future in orphans:
            if not future.done():
                future.set_exception(WorkerLost(
                    "worker %d stopped before it answered" % self.pid))

    def send(self, ident: int, op: str, args: Dict[str, Any],
             future: Future) -> None:
        line = json.dumps({"id": ident, "op": op, "args": args}) + "\n"
        with self.lock:
            self.pending[ident] = future
        try:
            self.process.stdin.write(line.encode("utf-8"))
            self.process.stdin.flush()
        except OSError:
            self._lost()

    def stop(self) -> None:
        self.alive = False
        try:
            self.process.stdin.close()
        except OSError:
            pass
        try:
            self.process.wait(timeout=2)
        except Exception:
            try:
                self.process.kill()
            except Exception:
                pass


class Pool:
    """The workers, started when there is work and let go when there is not."""

    def __init__(self, size: Optional[int] = None) -> None:
        self.size = capacity() if size is None else size
        self._workers: List[_Worker] = []
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._last_used = time.monotonic()
        self._reaper: Optional[threading.Thread] = None
        # work that should go back to the same worker, which keeps state
        # for it: a document's copy, say
        self._affinity: Dict[str, _Worker] = {}

    # -- lifecycle ---------------------------------------------------------

    @property
    def running(self) -> int:
        return sum(1 for w in self._workers if w.alive)

    def start(self, count: Optional[int] = None) -> None:
        """Bring the pool up to strength, without waiting for anybody."""
        wanted = self.size if count is None else min(self.size, count)
        with self._lock:
            self._workers = [w for w in self._workers if w.alive]
            while len(self._workers) < wanted:
                try:
                    self._workers.append(_Worker())
                except OSError:
                    break
            if self._reaper is None:
                # the first start in this window: results a crashed one
                # never collected can go
                from .worker import sweep_bodies
                sweep_bodies()
                self._reaper = threading.Thread(
                    target=self._reap, name="datum-worker-reaper",
                    daemon=True)
                self._reaper.start()
        self._last_used = time.monotonic()

    def wait_ready(self, timeout: float = START_SECONDS) -> int:
        """Wait for the workers to finish loading.  Returns how many did."""
        deadline = time.monotonic() + timeout
        for worker in list(self._workers):
            worker.ready.wait(max(0.0, deadline - time.monotonic()))
        return sum(1 for w in self._workers if w.alive and w.ready.is_set())

    def shutdown(self) -> None:
        with self._lock:
            workers, self._workers = self._workers, []
        for worker in workers:
            worker.stop()

    def _reap(self) -> None:
        while True:
            time.sleep(15)
            busy = any(w.load for w in self._workers)
            if not busy and self._workers and \
                    time.monotonic() - self._last_used > IDLE_SECONDS:
                self.shutdown()

    # -- work --------------------------------------------------------------

    def submit(self, op: str, **args: Any) -> Future:
        """Send one request to the least busy worker."""
        return self._dispatch(None, op, args, RETRIES)

    @property
    def ready(self) -> int:
        """How many workers have loaded and can take work right now."""
        return sum(1 for w in self._workers if w.alive and w.ready.is_set())

    def _keys_on(self, worker: "_Worker") -> int:
        """How many keys a worker is keeping state for."""
        return sum(1 for w in self._affinity.values() if w is worker)

    def bind(self, affinity: str, avoid: Iterable[str] = ()) -> bool:
        """Give a key a worker of its own, away from the workers of others.

        A key already on a live worker that is not one of theirs stays
        where it is, with whatever that worker is keeping for it. Otherwise
        it goes to a ready worker none of the ``avoid`` keys are on, the
        least busy and least burdened. False when there is no such worker.
        """
        self.start()
        with self._lock:
            taken = set()
            for key in avoid:
                held = self._affinity.get(key)
                if held is not None and held.alive:
                    taken.add(id(held))
            current = self._affinity.get(affinity)
            if current is not None and current.alive and \
                    id(current) not in taken:
                return True
            choice = [w for w in self._workers if w.alive
                      and w.ready.is_set() and id(w) not in taken]
            if not choice:
                return False
            self._affinity[affinity] = min(
                choice, key=lambda w: (w.load, self._keys_on(w)))
            return True

    def release(self, keys: Iterable[str]) -> None:
        """Done with these keys: their workers can let go of what they keep.

        Nothing is waited for, and a worker that has gone kept nothing.
        """
        with self._lock:
            held: Dict[int, Any] = {}
            for key in keys:
                worker = self._affinity.pop(key, None)
                if worker is not None and worker.alive:
                    held.setdefault(id(worker), (worker, []))[1].append(key)
            ident = [next(self._ids) for _ in held]
        for number, (worker, names) in zip(ident, held.values()):
            try:
                worker.send(number, "forget", {"keys": names}, Future())
            except Exception:
                pass

    def submit_to(self, affinity: str, op: str, **args: Any) -> Future:
        """Send a request to the worker that took this key's last one.

        A worker keeping a document's copy builds only what changed; any
        other worker would start from nothing. A new key, or one whose
        worker has gone, goes to whichever is least busy and stays there.
        """
        return self._dispatch(affinity, op, args, RETRIES)

    def _dispatch(self, affinity: Optional[str], op: str,
                  args: Dict[str, Any], attempts: int,
                  outer: Optional[Future] = None) -> Future:
        """Send a request, and send it again elsewhere if its worker dies.

        A worker can die between being chosen and answering, and the
        request dies with it. Everything a worker does can be done again,
        so the request goes to another one rather than failing, once.
        """
        self.start()
        outer = outer if outer is not None else Future()
        attempt: Future = Future()
        with self._lock:
            worker = self._affinity.get(affinity) if affinity else None
            if worker is None or not worker.alive:
                live = [w for w in self._workers if w.alive]
                if not live:
                    outer.set_exception(WorkerLost("no worker could start"))
                    return outer
                worker = min(live, key=lambda w: (w.load, self._keys_on(w)))
                if affinity:
                    self._affinity[affinity] = worker
            ident = next(self._ids)

        def finished(done: Future) -> None:
            if outer.done():
                return
            error = done.exception()
            if isinstance(error, WorkerLost) and attempts > 1:
                self._dispatch(affinity, op, args, attempts - 1, outer)
            elif error is not None:
                outer.set_exception(error)
            else:
                outer.set_result(done.result())

        attempt.add_done_callback(finished)
        self._last_used = time.monotonic()
        worker.send(ident, op, args, attempt)
        return outer

    def map(self, op: str, requests: Iterable[Dict[str, Any]],
            timeout: Optional[float] = None) -> List[Any]:
        """Run many requests and wait for them all, in order.

        A request that failed, or whose worker was lost, comes back as the
        exception rather than stopping the others: the caller decides
        what to do about one bad part in fifty.
        """
        futures = [self.submit(op, **r) for r in requests]
        out: List[Any] = []
        for future in futures:
            try:
                out.append(future.result(timeout=timeout))
            except Exception as exc:                    # noqa: BLE001
                out.append(exc)
        self._last_used = time.monotonic()
        return out


_POOL: Optional[Pool] = None


def pool() -> Optional[Pool]:
    """The one pool, or None when workers are switched off."""
    global _POOL
    if not ENABLED or capacity() < 1:
        return None
    if _POOL is None:
        _POOL = Pool()
        atexit.register(_POOL.shutdown)
    return _POOL


def warm(count: Optional[int] = None) -> None:
    """Start workers loading in the background, for work about to come."""
    held = pool()
    if held is not None:
        held.start(count)
