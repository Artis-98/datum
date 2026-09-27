"""Work on every core: the worker processes, and what they build.

The one promise that matters is that it makes no difference to the answer
whether a part was built here or in a worker, and that a worker dying
halfway loses nothing: whatever it did not finish is built here instead.
"""
import glob
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WORK = tempfile.mkdtemp(prefix="datum_workers_")
os.environ["DATUM_CONFIG_DIR"] = os.path.join(WORK, "cfg")
os.environ.pop("DATUM_NO_WORKERS", None)
os.environ["DATUM_WORKERS"] = "3"

from datum.core import bodycache, kernel, workers         # noqa: E402
from datum.core.assembly import AssemblyDocument          # noqa: E402
from datum.core.parts import PartLibrary, leaf_parts      # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXCAVATOR = os.path.join(ROOT, "examples", "excavator", "Excavator.adat")
FAILS = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


print("the pool")
helpers = workers.pool()
check("there is one, of the size asked for", helpers is not None
      and helpers.size == 3)
helpers.start()
check("its workers start", helpers.wait_ready(timeout=90) == 3)
answers = helpers.map("ping", [{}] * 6)
check("they answer, spread across all of them",
      len({a["pid"] for a in answers}) == 3, answers)
bad = helpers.submit("no_such_thing")
try:
    bad.result(timeout=30)
    check("a request it cannot do is refused", False)
except RuntimeError as exc:
    check("a request it cannot do is refused", "unknown" in str(exc))


print()
print("building an assembly's parts on every core")
parts = leaf_parts(EXCAVATOR)
check("the parts under an assembly are found from the files alone",
      len(set(parts)) == 26, len(set(parts)))
library = PartLibrary()
check("none are built yet", not any(library.is_fresh(p) for p in parts))
started = time.perf_counter()
built = library.prefetch(parts, force=True)
check("the workers built every one", built == 26, built)
check("  and left them in the cache",
      all(bodycache.has(bodycache.key_for_part(p)) for p in set(parts)))

parallel = AssemblyDocument.load(EXCAVATOR)
parallel.rebuild()
os.environ["DATUM_CONFIG_DIR"] = os.path.join(WORK, "cfg-serial")
workers.ENABLED = False
serial = AssemblyDocument.load(EXCAVATOR)
serial.rebuild()
workers.ENABLED = True
os.environ["DATUM_CONFIG_DIR"] = os.path.join(WORK, "cfg")
vp, cp = kernel.volume_and_centre(parallel.shape)
vs, cs = kernel.volume_and_centre(serial.shape)
check("the assembly is the same whether its parts were built here or there",
      abs(vp - vs) < 1e-6 * vs
      and max(abs(a - b) for a, b in zip(cp, cs)) < 1e-6, (vp, vs))


print()
print("a worker that dies loses nothing")
victim = helpers._workers[0]
slow = []
# a queue deep enough that the victim is certainly holding some of it
for path in parts[:9]:
    slow.append(helpers.submit("build", path=os.path.abspath(path)))
victim.process.kill()
outcomes = []
for future in slow:
    try:
        outcomes.append(future.result(timeout=60))
    except Exception as exc:                                    # noqa
        outcomes.append(exc)
check("what it was holding is done by another worker instead",
      all(isinstance(o, dict) and o.get("built") for o in outcomes),
      outcomes)
again = helpers.submit("ping").result(timeout=90)
check("and the pool carries on, starting a replacement",
      helpers.running == 3 and again.get("pid"), helpers.running)


print()
print("switched off, nothing changes but where the work is done")
workers.ENABLED = False
check("no pool at all", workers.pool() is None)
check("  and prefetch simply does nothing",
      PartLibrary().prefetch(parts, force=True) == 0)
workers.ENABLED = True

helpers.shutdown()
check("shutting down lets them all go", helpers.running == 0)


print()
print("FAILED: " + ", ".join(FAILS) if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
