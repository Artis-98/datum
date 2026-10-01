"""A straight slot is one shape with a middle you can constrain.

It gets what Inventor gives one: sides tangent to the ends, equal ends, a
construction centre line between the arc centres, and a point held at the
middle of that line.  Coincident between that point and the origin is how
a slot gets centred.
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datum.core.sketch import Sketch                               # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def slot_parts(s, ids):
    lines = [e for e in ids if s.entities[e].kind == "line"
             and not s.entities[e].construction]
    arcs = [e for e in ids if s.entities[e].kind == "arc"]
    centre_line = [e for e in ids if s.entities[e].construction]
    return lines, arcs, centre_line


print("what a slot is made of")
s = Sketch()
ids = s.add_slot((10.0, 5.0), (50.0, 5.0), 12.0)
lines, arcs, centre_line = slot_parts(s, ids)
check("two sides, two ends, one centre line",
      (len(lines), len(arcs), len(centre_line)) == (2, 2, 1),
      (len(lines), len(arcs), len(centre_line)))
cl = s.entities[centre_line[0]]
check("the centre line joins the two arc centres",
      set(cl.points) == {s.entities[a].points[0] for a in arcs})
middle = [c.points[0] for c in s.constraints.values()
          if c.kind == "midpoint" and c.entities == centre_line]
check("a point is held at its middle", len(middle) == 1)
mid = s.points[middle[0]]
check("and it starts in the middle", abs(mid.x - 30.0) < 1e-9
      and abs(mid.y - 5.0) < 1e-9, (mid.x, mid.y))
s.solve()
check("a fresh slot solves without moving",
      abs(s.points[middle[0]].x - 30.0) < 1e-6 and not s.conflicting)

print()
print("centring it")
# sized first, the way a slot is drawn for real: an undimensioned sketch
# is free to change shape on the way, and the solver will use that freedom
s.add_constraint("radius", entities=[arcs[0]], value=6.0)
s.add_constraint("distance", points=list(cl.points), value=40.0)
s.solve()
s.add_constraint("coincident", points=[middle[0], s.ensure_origin_point()])
s.solve()
mid = s.points[middle[0]]
check("coincident with the origin puts its middle there",
      abs(mid.x) < 1e-6 and abs(mid.y) < 1e-6, (mid.x, mid.y))
a, b = (s.points[s.entities[e].points[0]] for e in arcs)
check("with the two ends either side",
      abs(a.x + b.x) < 1e-6 and abs(a.y + b.y) < 1e-6
      and abs(math.dist(a.as_tuple(), b.as_tuple()) - 40.0) < 1e-4,
      (a.as_tuple(), b.as_tuple()))
r = [s.entities[e].radius for e in arcs]
check("still the same width", all(abs(v - 6.0) < 1e-4 for v in r), r)

print()
print("it moves as one")
side = s.entities[lines[0]]
p = s.points[side.points[0]]
s.points[side.points[0]].y += 4.0
s.solve()
r = [s.entities[e].radius for e in arcs]
check("the ends stay the same size", abs(r[0] - r[1]) < 1e-6, r)
for line in lines:
    (ax, ay), (bx, by) = (s.points[q].as_tuple()
                          for q in s.entities[line].points)
    for arc in arcs:
        c = s.points[s.entities[arc].points[0]]
        dist = abs((c.x - ax) * (by - ay) - (c.y - ay) * (bx - ax)) / (
            math.hypot(bx - ax, by - ay) or 1.0)
        check("side %d still touches end %d" % (line, arc),
              abs(dist - s.entities[arc].radius) < 1e-5,
              dist - s.entities[arc].radius)

print()
print("FAILED: " + ", ".join(FAILS) if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
