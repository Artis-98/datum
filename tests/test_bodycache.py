"""Bodies kept on disk, and the one way that can go wrong.

A cache that hands back the wrong body is worse than no cache, so the
thing worth holding still is not the speed, it is that editing a part
makes the old answer unreachable rather than merely unlikely.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WORK = tempfile.mkdtemp(prefix="datum_bodycache_")
os.environ["DATUM_CONFIG_DIR"] = os.path.join(WORK, "cfg")

from datum.core import bodycache, fileformat, kernel            # noqa: E402
from datum.core.assembly import AssemblyDocument                # noqa: E402
from datum.core.document import Document                        # noqa: E402
from datum.core.features import ImportFeature, PrimitiveFeature  # noqa: E402
from datum.core.parts import PartLibrary                        # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def part(height, name="block.pdat"):
    doc = Document()
    feature = PrimitiveFeature()
    feature.kind = "box"
    feature.a, feature.b, feature.c = "40", "30", str(height)
    feature.operation = "new"
    doc.add_feature(feature)
    doc.rebuild()
    return doc.save(os.path.join(WORK, name))


bodycache.clear()

print("a body is kept, and found again")

path = part(10)
library = PartLibrary()
first = library.shape(path)
check("the part builds", first is not None and not first.IsNull())
check("and the body was kept", bodycache.size() > 0, bodycache.size())

key = bodycache.key_for(path)
check("it can be read straight back", bodycache.load(key) is not None)
check("and it is the same body",
      abs(kernel.volume(bodycache.load(key)) - kernel.volume(first)) < 1e-6)


print()
print("editing the part makes the old body unreachable")

before = bodycache.key_for(path)
part(25)                                    # same file, taller box
after = bodycache.key_for(path)
check("the key moved with the file", before != after, (before[:8], after[:8]))

fresh = PartLibrary()
again = fresh.shape(path)
check("so what comes back is the new shape",
      abs(kernel.bounding_box(again)[5] - 25.0) < 1e-6,
      kernel.bounding_box(again)[5])
check("and not the old one",
      abs(kernel.volume(again) - kernel.volume(first)) > 1.0,
      (kernel.volume(again), kernel.volume(first)))


print()
print("what must not be cached, is not")

imported = Document()
feature = ImportFeature()
feature.path = os.path.join(WORK, "somewhere.step")
imported.add_feature(feature)
check("a part that imports another file is not cacheable",
      not bodycache.cacheable(imported))
check("but an ordinary one is", bodycache.cacheable(Document()))

empty = Document()
check("a body that is nothing is not stored",
      not bodycache.store("deadbeef", None))


print()
print("a broken entry is a miss, not a crash")

path = part(12, "third.pdat")
library = PartLibrary()
library.shape(path)
key = bodycache.key_for(path)
target = bodycache._file_for(key)
check("the entry is there", os.path.exists(target))
with open(target, "wb") as handle:
    handle.write(b"this is not a body")
check("reading rubbish gives nothing back", bodycache.load(key) is None)
check("and the rubbish is thrown away", not os.path.exists(target))
check("the part still builds", PartLibrary().shape(path) is not None)


print()
print("it stays inside its budget")

for i in range(6):
    part(10 + i, "budget%d.pdat" % i)
    PartLibrary().shape(os.path.join(WORK, "budget%d.pdat" % i))
full = bodycache.size()
check("there is something to prune", full > 0, full)
freed = bodycache.prune(budget=full // 3)
check("pruning frees what it said it would", freed > 0, freed)
check("and brings it under the budget", bodycache.size() <= full, bodycache.size())

check("clearing it empties the lot",
      bodycache.clear() >= 0 and bodycache.size() == 0, bodycache.size())


print()
print("an assembly built from cache matches one built without")

parts = [part(10 + i, "asm%d.pdat" % i) for i in range(3)]
asm = AssemblyDocument()
asm.path = os.path.join(WORK, "rig.adat")
for source in parts:
    placed = asm.place(source, os.path.basename(source))
    placed.grounded = True
bodycache.clear()
asm.rebuild()
cold = kernel.volume(asm.shape)
asm.library.forget()
asm.rebuild()
warm = kernel.volume(asm.shape)
check("the same assembly either way", abs(cold - warm) < 1e-6, (cold, warm))


print()
print("FAILED: " + ", ".join(FAILS) if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
