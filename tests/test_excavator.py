"""The excavator sample opens, rebuilds and is still put together.

A sample is documentation that runs, which means it rots like any other
code: a change to the kernel, to placement or to the materials library can
leave it opening to an empty view or a pile of parts in the wrong place,
and nobody would notice until it was in front of somebody.  So this checks
the things a person would check by looking at it - that every component is
there, that the linkage still closes, and that the paint is still on.
"""
import collections
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datum.core import kernel                                  # noqa: E402
from datum.core.assembly import open_any                       # noqa: E402
from datum.core.materials import library                       # noqa: E402

FAILS = []
SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "examples", "excavator")


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


print("the assembly")

asm = open_any(os.path.join(SRC, "Excavator.adat"))
report = asm.rebuild()
check("it rebuilds without error", report.ok and not report.errors,
      report.message)
check("all 48 components are there", len(asm.occurrences) == 48,
      len(asm.occurrences))
check("every one of them built a shape",
      all(o.shape is not None for o in asm.occurrences),
      [o.name for o in asm.occurrences if o.shape is None])

counts = collections.Counter(os.path.basename(o.ref.path)
                             for o in asm.occurrences)
check("24 unique parts", len(counts) == 24, len(counts))
check("one pin does eleven joints", counts.get("Pivot Pin.pdat") == 11,
      counts.get("Pivot Pin.pdat"))
check("the bucket has its five teeth", counts.get("Bucket Tooth.pdat") == 5,
      counts.get("Bucket Tooth.pdat"))

box = kernel.bounding_box(asm.shape)
length, height = box[3] - box[0], box[5] - box[2]
check("it is about 4.7 m over the bucket", 4500 < length < 5000, length)
check("and about 2.5 m tall", 2300 < height < 2700, height)


print()
print("the linkage still closes")

# The bucket link is the tell-tale for the whole linkage: it is the one
# part whose length was worked out from where two others put their holes,
# so if placement has drifted it is the first thing to come apart.
held = {o.name: o for o in asm.occurrences}
links = [o for name, o in held.items() if name.startswith("Bucket Link")]
check("both links are placed", len(links) == 2, len(links))

# occurrence names carry their number, so "Bucket" is "Bucket:1"
bucket = next((o for o in asm.occurrences
               if o.name.split(":")[0] == "Bucket"), None)
check("the bucket is in there", bucket is not None)
if bucket is not None and len(links) == 2:
    ear = bucket.placement.apply_point((200, 0, 170))
    for link in links:
        # The far hole should land on the ear pin's axis, 160 mm across
        # from the pin's centre, which is exactly how far outboard the
        # link was placed.  Measuring the gap in 3D rather than in plan is
        # the point: the slew turns the machine's Y into the world's, so a
        # check written flat would fail on a perfectly good linkage.
        far = link.placement.apply_point((380, 0, 0))
        gap = abs(math.dist(far, ear) - 160.0)
        check("%s reaches the bucket's ear" % link.name, gap < 0.001, gap)

# A ram is two parts aimed at each other and nothing else holds them in
# line, so the barrel pointing anywhere but at its own rod means the aim
# has gone wrong.
for stem in ("Boom Ram", "Arm Ram", "Bucket Ram"):
    pairs = [(b, r) for name, b in held.items()
             if name.startswith(stem + " Barrel")
             for rname, r in held.items()
             if "Rod" in rname and rname.replace("Rod", "Barrel") == name]
    check("%s is a barrel and a rod" % stem, len(pairs) >= 1, len(pairs))
    for barrel, rod in pairs:
        along = barrel.placement.apply_direction((1, 0, 0))
        to_rod = [r - b for r, b in zip(rod.placement.position,
                                        barrel.placement.position)]
        reach = math.sqrt(sum(c * c for c in to_rod))
        off = max(abs(a - t / reach) for a, t in zip(along, to_rod))
        check("  %s looks straight at its rod" % barrel.name, off < 1e-9, off)
        check("  and they are %.0f mm apart" % reach, 500 < reach < 1400,
              reach)


print()
print("the paint is still on")

lib = library()
wanted = {
    "Boom.pdat": "Paint, Machine Yellow",
    "Rubber Track.pdat": "Rubber, Black",
    "Cab Glazing.pdat": "Glass, Clear",
    "Ram Rod.pdat": "Steel, Polished",
    "Counterweight.pdat": "Paint, Machine Charcoal",
}
for name, appearance in sorted(wanted.items()):
    doc = open_any(os.path.join(SRC, name))
    check("%s is %s" % (name[:-5], appearance), doc.appearance == appearance,
          doc.appearance)
    check("  and %s is a real appearance" % appearance,
          appearance in lib.appearances)

glazing = open_any(os.path.join(SRC, "Cab Glazing.pdat"))
check("the glass is see-through",
      glazing.material_appearance.opacity < 0.5,
      glazing.material_appearance.opacity)

track = open_any(os.path.join(SRC, "Rubber Track.pdat"))
check("and rubber is not steel", abs(track.density - 1.15) < 0.01,
      track.density)


print()
print("FAILED: " + ", ".join(FAILS) if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
