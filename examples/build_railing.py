"""Build the staircase railing sample: a part that is mostly code.

A stair is the example everybody reaches for when they ask what a script
in a CAD model is for, because nothing about it is one size. Change the
number of steps and the treads, the stringers, every baluster and the
handrail all have to be worked out again, and no amount of parameters
on an extrude will do that.

So the part is two code features and a rule:

    Stair       treads and two stringers, from the parameters
    Railing     newel posts, balusters spaced to a maximum gap, and a
                handrail swept along the pitch with a rounded return
    Size        a rule with a form on it, pinned as a button, so the
                whole stair can be dragged to size

    python examples/build_railing.py

writes examples/railing/Stair Railing.pdat.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from datum.core import rules                                # noqa: E402
from datum.core.document import Document                    # noqa: E402
from datum.core.features import CodeFeature, NEW_BODY       # noqa: E402

OUT = os.path.join(HERE, "railing", "Stair Railing.pdat")

PARAMS = [
    ("steps", "12", "how many treads"),
    ("rise", "175", "height of one step"),
    ("going", "250", "depth of one step, nosing to nosing"),
    ("width", "900", "clear width of the stair"),
    ("tread", "40", "tread thickness"),
    ("rail_height", "900", "handrail height above the nosing line"),
    ("baluster_gap", "100", "most space allowed between balusters"),
    ("sides", "1", "railing on one side or both"),
]

STAIR = '''\
"""Treads and two stringers."""
n, rise, going = int(params.steps), params.rise, params.going
width, t = params.width, params.tread

def pitch(x):
    """Height of the nosing line at x."""
    return rise + x * rise / going

parts = []
for i in range(n):
    # each tread sits on its riser height, with a 25 mm nosing
    parts.append(box(going + 25, width, t).move(i * going - 25, 0,
                                                (i + 1) * rise - t))

# stringers run under the tread ends, cut level with the floor
run = n * going
for y in (-20, width + 20):
    s = beam((-going, y, pitch(-going) - 150), (run, y, pitch(run) - 150),
             40, 260)
    parts.append(s - box(run + 2 * going, 200, 1000).move(-2 * going, y - 100,
                                                           -1000))

result(union(parts))
log(n, "steps,", round(n * rise), "mm up,", round(run), "mm along")
'''

RAILING = '''"""Newels, balusters and a swept handrail, on one side or both."""
n, rise, going = int(params.steps), params.rise, params.going
H, gap = params.rail_height, params.baluster_gap
r_rail, r_bal, post = 25, 9, 80
top = n * rise                         # the last tread, where the rail levels

def pitch(x):
    """Height of the nosing line at x."""
    return rise + x * rise / going

def rail(x):
    """Height of the handrail's centre: up the pitch, then level."""
    return min(pitch(x), top) + H

first = going * 0.35                   # bottom newel, on the first tread
last = (n - 1) * going + going * 0.6   # top newel, on the last one
sides = [params.width - 45] if params.sides < 2 else [45, params.width - 45]

parts = []
for y in sides:
    # newel posts stand on their treads and finish just above the rail
    for x, step in ((first, 0), (last, n - 1)):
        foot = (step + 1) * rise
        parts.append(box(post, post, rail(x) + 60 - foot)
                     .move(x - post / 2, y - post / 2, foot)
                     .fillet(4))

    # balusters: as many per tread as it takes to keep under the gap,
    # each buried in the rail so none of them stops short of it
    per = max(1, math.ceil(going / gap))
    count = 0
    for i in range(n):
        for k in range(per):
            x = i * going + (k + 0.5) * going / per
            if x < first + post / 2 + gap / 2 or x > last - post / 2 - gap / 2:
                continue
            parts.append(rod((x, y, (i + 1) * rise), (x, y, rail(x)), r_bal))
            count += 1

    # the handrail runs newel to newel: up the pitch, a rounded bend at the
    # last nosing, then level into the top post.  Both ends finish inside
    # a post, so nothing sticks out past them.
    corner = (n - 1) * going
    parts.append(circle(r_rail).sweep(path(
        [(first, y, rail(first)), (corner, y, top + H), (last, y, top + H)],
        corner=120)))
    log("side at y =", round(y), ":", count, "balusters")

result(union(parts))
'''

SIZE = '''\
"""Drag the stair to size.  Pinned as a button in the dLogic panel."""
form("Stair",
     note("Everything else follows: treads, stringers, balusters, rail."),
     slider("steps", 3, 20, 1),
     slider("rise", 150, 220, 5),
     slider("going", 220, 320, 5),
     slider("width", 600, 1400, 50),
     slider("rail_height", 850, 1100, 10),
     slider("baluster_gap", 80, 150, 5),
     choice("sides", ["1", "2"]))
log("going + 2 x rise =", params.going + 2 * params.rise,
    "(comfortable is 600 to 650)")
'''

KEEP = '''\
"""Keep the stair comfortable: runs whenever a size changes."""
# the old carpenter's rule: two rises and a going make one stride
stride = params.going + 2 * params.rise
if stride < 600 or stride > 650:
    log("stride is", stride, "mm; 600 to 650 is comfortable")
props.Description = "Stair, %d steps, %d rise, %d going" % (
    params.steps, params.rise, params.going)
'''


def build() -> Document:
    doc = Document()
    for name, expression, comment in PARAMS:
        doc.params.add(name, expression)
        try:
            doc.params[name].comment = comment
        except Exception:
            pass
    doc.properties["Title"] = "Stair Railing"
    doc.properties["PartNumber"] = "DATUM-EX-STAIR"
    doc.material = "Steel, Mild"

    for name, source, body in (("Stair", STAIR, "Stair"),
                               ("Railing", RAILING, "Railing")):
        feature = CodeFeature()
        feature.name = name
        feature.source = source
        feature.operation = NEW_BODY
        feature.body_name = body
        doc.add_feature(feature)

    size = doc.rules.add("Size", SIZE)
    size.on_rebuild = False
    size.button = True
    keep = doc.rules.add("Keep it comfortable", KEEP)
    keep.on_rebuild = False
    keep.watch = ["rise", "going", "steps"]

    doc.rules.trusted = True
    doc.rebuild()
    return doc


if __name__ == "__main__":
    doc = build()
    report = doc.last_report
    for feature in doc.features:
        if feature.error:
            print("FAILED", feature.name, feature.error)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    doc.save(OUT)
    print("wrote", OUT, "ok" if report.ok else "WITH ERRORS")
