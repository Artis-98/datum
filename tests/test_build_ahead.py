"""Building ahead: nearby values built on spare cores before they are asked for.

What has to hold: a result built ahead is used only for exactly the part
it was built for, and then gives the model a rebuild here would; a part
that has moved on in any other way is built the usual way; the pieces a
worker leaves alone come back under the same names from any worker, so
the window keeps its own copies of them on screen; and nothing built
ahead and never used is left lying about on disk.
"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WORK = tempfile.mkdtemp(prefix="datum_ahead_")
os.environ["DATUM_CONFIG_DIR"] = os.path.join(WORK, "cfg")
os.environ.pop("DATUM_NO_WORKERS", None)
os.environ["DATUM_WORKERS"] = "3"

from datum.core import ahead as ahead_module                 # noqa: E402
from datum.core import document as core_document             # noqa: E402
from datum.core import fileio, geometry, kernel, workers  # noqa
from datum.core.document import Document                     # noqa: E402
from datum.core.features import ImportFeature, PrimitiveFeature  # noqa

FAILS = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


MANY = os.path.join(WORK, "twelve.brep")
fileio.write_shape(geometry.box(10, 10, 10).repeat(12, x=30).shape, MANY)


def make():
    """Twelve blocks brought in, and a hole through one of them."""
    doc = Document()
    doc.params.add("r", "3")
    doc.params.add("hx", "35")
    brought = ImportFeature()
    brought.path = MANY
    brought.operation = "new"
    doc.add_feature(brought)
    hole = PrimitiveFeature()
    hole.kind = "cylinder"
    hole.a, hole.b, hole.c = "r", "20", "0"
    hole.origin = ("hx", "5", "-5")
    hole.operation = "cut"
    doc.add_feature(hole)
    return doc


def copy_of(doc):
    here = Document()
    here.load_dict(json.loads(json.dumps(doc.to_dict())))
    return here


def local(doc):
    """The volume a from-scratch build of the part as it stands gives."""
    here = copy_of(doc)
    here.rebuild()
    return round(kernel.volume(here.shape), 6)


def volume(doc):
    return round(kernel.volume(doc.shape), 6)


def names_of(doc):
    named = core_document.shape_names(doc)
    return {name for bucket in named.values() for _s, name in bucket}


print("names that do not depend on which worker built it")
a = make()
a.rebuild()
b = copy_of(a)
b.rebuild()
check("two builds of the same part name their shapes alike",
      names_of(a) == names_of(b) and len(names_of(a)) >= 13,
      (len(names_of(a)), len(names_of(b))))
b.params.set_expression("r", "4")
b.rebuild()
named_a = core_document.shape_names(a)
named_b = core_document.shape_names(b)
pieces_a = {core_document.name_of(named_a, p) for p in kernel.pieces(a.shape)}
pieces_b = {core_document.name_of(named_b, p) for p in kernel.pieces(b.shape)}
check("a bigger hole renames the block it cuts and no other",
      len(pieces_a & pieces_b) == 11 and None not in pieces_b,
      len(pieces_a & pieces_b))

print()
print("which slider positions are worth building")
check("further the way it is going, then either side",
      ahead_module.around(5, 2, 20)[:4] == [7, 9, 11, 6],
      ahead_module.around(5, 2, 20))
check("never off the end of the slider",
      all(0 <= t <= 10 for t in ahead_module.around(0, -3, 10))
      and all(0 <= t <= 10 for t in ahead_module.around(10, 3, 10)))


print()
print("built ahead in workers")
helpers = workers.pool()
helpers.start()
check("workers are ready", helpers.wait_ready(timeout=120) == 3)

own = make()
own.rebuild()
before = kernel.pieces(own.shape)
own.params.set_expression("r", "4")
request = own.remote_request()
result = helpers.submit_to(own.remote_key, "rebuild",
                           **request).result(timeout=120)
packed = [p for body in result["bodies"] for p in body.get("pieces", [])
          if "pack" in p]
check("built here, then in a worker: only the block it cuts comes back",
      len(packed) == 1, len(packed))
own.apply_remote(result, request)
after = kernel.pieces(own.shape)
check("  the rest are the very blocks the window built itself",
      sum(1 for p in after if any(p.IsEqual(q) for q in before)) == 11)
check("  and the model is right", volume(own) == local(own))
from datum.core import mesh  # noqa: E402
new = [p for p in after if not any(p.IsEqual(q) for q in before)]
check("  the block that came back came meshed, ready to draw",
      len(new) == 1 and mesh.is_meshed(new[0], mesh.deflection(own.shape)))

went = []


def remote(document):
    """The window's part of it, without a window: wait by blocking."""
    ahead = document.ahead
    if ahead is not None:
        report = ahead.take(document, wait=lambda f: f.result(timeout=120))
        if report is not None:
            went.append("ahead")
            return report
    request = document.remote_request()
    went.append("main")
    result = helpers.submit_to(document.remote_key, "rebuild",
                               **request).result(timeout=120)
    return document.apply_remote(result, request)


core_document.REMOTE = remote
core_document.REMOTE_AFTER = 0.0
ahead_module.AHEAD_AFTER = 0.0

doc = make()
doc.rebuild()
check("the part is built in its own worker first", went == ["main"], went)
ahead = ahead_module.BuildAhead(doc, helpers)
doc.ahead = ahead
check("a slot on every worker but the part's own", len(ahead.slots) == 2)
started = ahead.offer([{"r": 4.0}, {"r": 5.0}, {"r": 6.0}])
check("as many start as there are spare workers", started == 2, started)
own = helpers._affinity[doc.remote_key]
slots = [helpers._affinity[s] for s in ahead.slots]
check("  none of them on the part's own worker, and one each",
      all(w is not own for w in slots) and slots[0] is not slots[1])
for _request, future in list(ahead._held.values()):
    future.result(timeout=120)
check("the part as it stands is not one of them", not ahead.ready(doc))

before = kernel.pieces(doc.shape)
doc.params.set_expression("r", repr(4.0))
check("moved to one of them, it is ready", ahead.ready(doc))
went.clear()
doc.rebuild()
check("the rebuild is the one built ahead, nothing more",
      ahead.used == 1 and went == [], (ahead.used, went))
check("  and the model a build here makes", volume(doc) == local(doc),
      (volume(doc), local(doc)))
after = kernel.pieces(doc.shape)
kept = sum(1 for p in after if any(p.IsEqual(q) for q in before))
check("  the blocks it did not touch are the window's own, not copies",
      len(after) == 12 and kept == 11, (len(after), kept))

doc.params.set_expression("r", repr(7.5))
went.clear()
doc.rebuild()
check("a value nobody built ahead is built the usual way",
      went == ["main"] and volume(doc) == local(doc), went)

ahead.offer([{"r": 8.0}])
for _request, future in list(ahead._held.values()):
    future.result(timeout=120)
doc.params.set_expression("hx", "95")
doc.params.set_expression("r", repr(8.0))
check("moved on in some other way too, what was built is not used",
      not ahead.ready(doc))
went.clear()
doc.rebuild()
check("  so it is built the usual way, as it now is",
      went == ["main"] and volume(doc) == local(doc), went)

used = ahead.used
ahead.offer([{"r": 9.0}])
doc.params.set_expression("r", repr(9.0))
check("one still being built counts as held", ahead.holds(doc))
went.clear()
doc.rebuild()
check("  and is waited for rather than started again",
      ahead.used == used + 1 and "main" not in went, (ahead.used, went))
check("  giving the model a build here makes", volume(doc) == local(doc))

ahead.offer([{"r": 10.0}])
for _request, future in list(ahead._held.values()):
    future.result(timeout=120)
held = dict(doc._remote_shapes)
doc._remote_shapes = {}
doc.params.set_expression("r", repr(10.0))
went.clear()
doc.rebuild()
check("a result naming shapes the part has let go of is not used",
      went == ["main"] and volume(doc) == local(doc), went)

print()
print("letting go")
ahead.offer([{"r": 11.0}, {"r": 12.0}])
futures = [f for _r, f in ahead._held.values()]
check("there is something being built to let go of", len(futures) >= 1)
ahead.close()
doc.ahead = None
results = [f.result(timeout=120) for f in futures]
time.sleep(0.5)
packs = [r.get("pack") for r in results if r.get("pack")]
check("what was built and never used is deleted, even arriving late",
      packs and not any(os.path.exists(p) for p in packs), packs)
check("closed, it builds nothing more",
      ahead.offer([{"r": 13.0}]) == 0 and len(ahead) == 0)
check("  and its workers are no longer tied to it",
      not any(s in helpers._affinity for s in ahead.slots))
answer = helpers.submit_to(doc.remote_key, "forget",
                           keys=[doc.remote_key]).result(timeout=60)
check("a worker lets go of a part's copy when asked",
      answer.get("forgotten") == 1, answer)

print()
print("only where it is worth it")
doc.params.add("spare", "1")
doc._costs = {doc.features[0].id: 0.5, doc.features[1].id: 0.01}
check("a change to what nothing reads costs nothing",
      doc.change_cost(["spare"]) == 0.0)
check("a change to the hole costs the hole, not the import before it",
      abs(doc.change_cost(["r"]) - 0.01) < 1e-9, doc.change_cost(["r"]))
doc.params.add("across", "r * 2")
doc.features[1].a = "across / 2"
check("  and a parameter worked out from it moves what reads that",
      abs(doc.change_cost(["r"]) - 0.01) < 1e-9, doc.change_cost(["r"]))
doc.features[1].a = "r"
quick = ahead_module.BuildAhead(doc, helpers)
ahead_module.AHEAD_AFTER = 0.08
check("a slider whose change rebuilds in no time is not built ahead",
      quick.offer([{"r": 5.0}]) == 0)
doc._costs[doc.features[1].id] = 0.3
quick._worth.clear()
check("  one whose change takes a while is",
      quick.offer([{"r": 5.0}]) == 1)
quick.close()

core_document.REMOTE = None
helpers.shutdown()

print()
print("FAILED: " + ", ".join(FAILS) if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
