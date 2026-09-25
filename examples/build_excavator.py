"""Build a tracked mini excavator: 20 parts, 36 components, one assembly.

This is the presentation model.  The plywood tote next door exists to show
the CAM side - a real sheet, a real nest, a real cutter - and it is a box.
This one exists to show what the modeller can carry: a machine made of
plate, castings, glass, rubber and polished rod, where every part is a
separate file with its own material, and where the boom actually reaches
where the linkage says it does.

Everything goes through DATUM's own API, the same calls the buttons make,
so the result is a set of documents you can open, edit and take apart.

How it is laid out
------------------
The assembly frame is Z up, +X the way the machine faces, +Y to its left,
with the ground at Z 0.  Each part is modelled in whichever local frame
makes its own sketches easy to read - a boom lying along its own +X, a
cylinder along its own axis - and then placed.

The boom, arm and bucket are placed by angle, and every lug, pin and
cylinder mount after them is found by asking the placement where a local
point ended up.  Nothing here has a hand-worked coordinate in it that the
geometry could disagree with, so changing the boom angle by one number
moves the cylinders that drive it with it.

The hydraulic cylinders are two parts, a barrel and a rod, aimed at each
other from their two mounts.  The rod slides into the barrel by however
much the geometry leaves over, which is what a real one does, and it means
no cylinder needs its stroke worked out by hand to look right.
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from datum.core import kernel
from datum.core.assembly import AssemblyDocument
from datum.core.constraints3d import Placement, rotation_vector
from datum.core.document import Document
from datum.core.features import (
    NEW_BODY, CUT, JOIN, BuildContext, ExtrudeFeature, HoleFeature,
    PrimitiveFeature, RevolveFeature, SketchFeature, collect_regions,
)
from datum.core.sketch import STANDARD_PLANES, Sketch, SketchPlane

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "excavator")
os.makedirs(OUT, exist_ok=True)

# --------------------------------------------------------------------------
# the numbers the whole machine comes from
# --------------------------------------------------------------------------

TRACK_L = 2100.0      # overall track length
TRACK_R = 240.0       # radius of the loop at each end, so the track is 480 tall
TRACK_W = 300.0       # width of one rubber track
TRACK_GAUGE = 1200.0  # centre to centre of the two tracks
BELT_T = 52.0         # thickness of the rubber

SLEW_Z = 560.0        # top of the undercarriage: where the house turns
# High, because a boom ram has to reach the boom's underside from below
# without crossing its root, and off to one side, because the cab is in the
# way of everything else - which is exactly why real machines offset it.
BOOM_PIVOT = (600.0, -140.0, 1150.0)
BOOM_ANGLE = 38.0     # degrees above horizontal
ARM_ANGLE = -60.0     # degrees, hanging forward and down from the boom tip
BUCKET_ANGLE = -25.0  # where the mouth points: below horizontal, ready to dig
SLEW = 18.0           # the house turned off centre, because square is dull
BOOM_RAM_BASE = (560.0, 740.0)   # in the bracket clevis, below the pivot
LINK = 380.0          # centres of the bucket link

YELLOW = "Paint, Machine Yellow"
CHARCOAL = "Paint, Machine Charcoal"
STEEL = "Steel, Mild"


# --------------------------------------------------------------------------
# helpers - thin wrappers over the same API the dialogs drive
# --------------------------------------------------------------------------


def new_part(material=STEEL, appearance=""):
    doc = Document()
    doc.material = material
    doc.appearance = appearance
    return doc


# A sketch can sit on any plane, not only the three datums, and saying so
# with a plane rather than a work-plane feature keeps these parts readable:
# "the circle 90 mm along the axis" instead of six lines of scaffolding.
def xy_at(z):
    return SketchPlane((0.0, 0.0, z), (0, 0, 1), (1, 0, 0), "XY %+.0f" % z)


def xz_at(y):
    return SketchPlane((0.0, y, 0.0), (0, -1, 0), (1, 0, 0), "XZ %+.0f" % y)


def yz_at(x):
    return SketchPlane((x, 0.0, 0.0), (1, 0, 0), (0, 1, 0), "YZ %+.0f" % x)


def sketch_on(doc, name, plane="XZ"):
    feature = SketchFeature()
    feature.name = name
    feature.sketch = Sketch(
        STANDARD_PLANES[plane] if isinstance(plane, str) else plane, name)
    doc.add_feature(feature)
    return feature


def loop(feature, points):
    """A closed polyline.  Points weld to each other, so the loop closes."""
    pts = list(points)
    for i in range(len(pts)):
        feature.sketch.add_line(pts[i], pts[(i + 1) % len(pts)])
    return feature


def regions_of(doc, sketch_id):
    """Every closed region of one sketch, as the extrude dialog sees them."""
    ctx = BuildContext(doc.params.scope())
    ctx.sketches = dict(doc.all_sketches())
    return [r for r in collect_regions(ctx) if r["sketch_id"] == sketch_id]


def extrude(doc, feature, distance, operation=NEW_BODY, name="",
            extent="symmetric", keep=None, biggest=False, reverse=False):
    """Extrude some or all regions of a sketch.

    ``keep`` filters the regions by their centre, which is how a sketch can
    hold both the outline and the holes and have only one of them used.
    """
    doc.rebuild()
    regions = regions_of(doc, feature.id)
    if biggest:
        regions = [max(regions, key=lambda r: r["area"])]
    if keep is not None:
        regions = [r for r in regions if keep(r["centre"], r["area"])]
    if not regions:
        raise SystemExit("%s: no region found in %s" % (doc, feature.name))

    ex = ExtrudeFeature()
    ex.name = feature.name.replace("Sketch", "").strip() or "Extrude"
    ex.distance = str(distance)
    ex.operation = operation
    ex.body_name = name
    ex.reversed = reverse
    ex.extent = "through_all" if operation == CUT and extent == "all" else extent
    for region in regions:
        ex.profiles.add(feature.id, region["centre"])
    doc.add_feature(ex)
    return ex


def revolve(doc, feature, name="", operation=NEW_BODY, angle=360, axis="X"):
    doc.rebuild()
    regions = regions_of(doc, feature.id)
    rev = RevolveFeature()
    rev.name = feature.name.replace("Sketch", "").strip() or "Revolve"
    rev.angle = str(angle)
    rev.axis = axis
    rev.operation = operation
    rev.body_name = name
    for region in regions:
        rev.profiles.add(feature.id, region["centre"])
    doc.add_feature(rev)
    return rev


def box(doc, size, origin, operation=JOIN, name="", label="Box"):
    prim = PrimitiveFeature()
    prim.name = label
    prim.kind = "box"
    prim.a, prim.b, prim.c = [str(v) for v in size]
    prim.origin = tuple(str(v) for v in origin)
    prim.operation = operation
    prim.body_name = name
    doc.add_feature(prim)
    return prim


def cylinder(doc, radius, length, origin, operation=JOIN, name="",
             label="Cylinder"):
    """A cylinder standing on +Z from ``origin`` - turned by placement later."""
    prim = PrimitiveFeature()
    prim.name = label
    prim.kind = "cylinder"
    prim.a, prim.b = str(radius), str(length)
    prim.origin = tuple(str(v) for v in origin)
    prim.operation = operation
    prim.body_name = name
    doc.add_feature(prim)
    return prim


def drill(doc, name, holes, plane="XZ", diameter=90.0):
    """Holes through, from circles marking their centres."""
    marks = sketch_on(doc, name, plane)
    for centre in holes:
        marks.sketch.add_circle(centre, diameter / 2.0)
    hole = HoleFeature()
    hole.name = name.replace("Sketch", "").strip() or "Hole"
    hole.sketch_id = marks.id
    hole.diameter = str(diameter)
    hole.through = True
    doc.add_feature(hole)
    return hole


def bore(doc, name, plane, centres, diameter, depth, reverse=False):
    """Holes that stop.

    A through hole is right on a part that is only as wide as the joint,
    and wrong on one that is not: a bracket wants its pin hole through its
    two lugs and nothing else, and drilling "through all" would tunnel the
    whole body.  So these are cut to a depth from a known plane instead.
    """
    marks = sketch_on(doc, name, plane)
    for centre in centres:
        marks.sketch.add_circle(centre, diameter / 2.0)
    return extrude(doc, marks, depth, CUT, extent="distance",
                   reverse=reverse)


def save(doc, name):
    doc.rebuild()
    if not doc.last_report.ok:
        raise SystemExit("%s failed: %s" % (name, doc.last_report.message))
    path = doc.save(os.path.join(OUT, name))
    bb = kernel.bounding_box(doc.shape)
    grams = doc.material_mass(kernel.volume(doc.shape)) \
        if hasattr(doc, "material_mass") else \
        kernel.volume(doc.shape) / 1000.0 * doc.density
    print("  %-22s %7.0f x %6.0f x %6.0f   %3d faces  %8.2f kg  %s"
          % (os.path.basename(path), bb[3] - bb[0], bb[4] - bb[1],
             bb[5] - bb[2], len(kernel.faces(doc.shape)), grams / 1000.0,
             doc.appearance or doc.material))
    return path


# ==========================================================================
# the undercarriage
# ==========================================================================

print("undercarriage")

# ---- rubber track: a stadium loop with lugs cut into the outside ---------
# Modelled about its own centre and extruded symmetrically, so the part is
# centred on its own width and placing it is a translation to the gauge.

TRACK_CENTRES = TRACK_L / 2.0 - TRACK_R          # where the two end arcs sit


def stadium(feature, radius):
    """The closed loop of a track belt: two lines and two half circles."""
    sk = feature.sketch
    c = TRACK_CENTRES
    sk.add_line((-c, radius), (c, radius))
    sk.add_arc((c, 0.0), radius, -math.pi / 2.0, math.pi / 2.0)
    sk.add_line((c, -radius), (-c, -radius))
    sk.add_arc((-c, 0.0), radius, math.pi / 2.0, 3.0 * math.pi / 2.0)
    return feature


def on_perimeter(s):
    """A point and its outward normal, ``s`` mm along the belt's outside.

    Walking the perimeter rather than placing lugs by quadrant is what lets
    one pitch carry round the ends and along the straights without the
    corners getting a lug of their own shape.
    """
    straight = 2.0 * TRACK_CENTRES
    arc = math.pi * TRACK_R
    s = s % (2.0 * straight + 2.0 * arc)
    if s < straight:                                    # along the top
        return (-TRACK_CENTRES + s, TRACK_R), math.pi / 2.0
    s -= straight
    if s < arc:                                         # round the front
        a = math.pi / 2.0 - s / TRACK_R
        return (TRACK_CENTRES + TRACK_R * math.cos(a),
                TRACK_R * math.sin(a)), a
    s -= arc
    if s < straight:                                    # back along the base
        return (TRACK_CENTRES - s, -TRACK_R), -math.pi / 2.0
    s -= straight
    a = 3.0 * math.pi / 2.0 - s / TRACK_R               # round the back
    return (-TRACK_CENTRES + TRACK_R * math.cos(a),
            TRACK_R * math.sin(a)), a


track = new_part("Rubber, EPDM", "Rubber, Black")
stadium(sketch_on(track, "Belt Outline"), TRACK_R)
extrude(track, track.features[-1], TRACK_W, NEW_BODY, "Rubber Track")
stadium(sketch_on(track, "Belt Bore"), TRACK_R - BELT_T)
extrude(track, track.features[-1], TRACK_W, CUT, extent="all")

lugs = sketch_on(track, "Track Lugs")
PITCH = 122.0
LUG_W, LUG_DEEP = 30.0, 19.0
count = int(round((2.0 * (2.0 * TRACK_CENTRES) + 2.0 * math.pi * TRACK_R)
                  / PITCH))
for i in range(count):
    (px, pz), ang = on_perimeter(i * PITCH)
    nx, nz = math.cos(ang), math.sin(ang)
    tx, tz = -nz, nx                     # along the belt, 90 deg from out
    corners = []
    for along, out in ((-1, -LUG_DEEP), (1, -LUG_DEEP), (1, 14.0),
                       (-1, 14.0)):
        corners.append((px + tx * along * LUG_W / 2.0 + nx * out,
                        pz + tz * along * LUG_W / 2.0 + nz * out))
    loop(lugs, corners)
extrude(track, lugs, TRACK_W, CUT, extent="all")
TRACK = save(track, "Rubber Track")
print("       %d lugs at %.0f mm pitch" % (count, PITCH))

# ---- track frame: an H, with a tub for the slew bearing on top ----------
frame = new_part(STEEL, CHARCOAL)
plan = sketch_on(frame, "Frame Plan", "XY")
plan.sketch.add_rectangle((-960, -660), (960, -540))      # right rail
plan.sketch.add_rectangle((-960, 540), (960, 660))        # left rail
plan.sketch.add_rectangle((-220, -660), (220, 660))       # the cross bar
plan.sketch.add_rectangle((-520, -430), (520, 430))       # centre section
plan.sketch.add_rectangle((400, -300), (900, 300))        # blade arm mounts
extrude(frame, plan, 300, NEW_BODY, "Track Frame", extent="distance")
box(frame, (860, 700, 215), (-430, -350, 30), CUT, label="Frame Pocket")
box(frame, (1660, 60, 200), (-830, -630, 40), CUT, label="Right Rail Pocket")
box(frame, (1660, 60, 200), (-830, 570, 40), CUT, label="Left Rail Pocket")
cylinder(frame, 400, 85, (0, 0, 300), JOIN, label="Slew Tub")
cylinder(frame, 250, 90, (0, 0, 320), CUT, label="Slew Tub Bore")
FRAME = save(frame, "Track Frame")

# ---- drive sprocket: a rim with teeth cut into it -----------------------
sprocket = new_part(STEEL, CHARCOAL)
sketch_on(sprocket, "Rim").sketch.add_circle((0, 0), 190)
extrude(sprocket, sprocket.features[-1], 230, NEW_BODY, "Sprocket")

teeth = sketch_on(sprocket, "Tooth Gaps")
TEETH = 18
for i in range(TEETH):
    a = 2.0 * math.pi * i / TEETH
    nx, nz = math.cos(a), math.sin(a)
    tx, tz = -nz, nx
    corners = []
    for along, out in ((-1, 150.0), (1, 150.0), (1, 215.0), (-1, 215.0)):
        corners.append((nx * out + tx * along * 21.0,
                        nz * out + tz * along * 21.0))
    loop(teeth, corners)
extrude(sprocket, teeth, 230, CUT, extent="all")

sketch_on(sprocket, "Hub").sketch.add_circle((0, 0), 78)
extrude(sprocket, sprocket.features[-1], 300, JOIN)
drill(sprocket, "Hub Bore", [(0, 0)], diameter=96)
drill(sprocket, "Bolt Holes",
      [(56 * math.cos(2 * math.pi * i / 6), 56 * math.sin(2 * math.pi * i / 6))
       for i in range(6)], diameter=26)
SPROCKET = save(sprocket, "Drive Sprocket")

# ---- idler: the smooth wheel at the other end ---------------------------
idler = new_part(STEEL, CHARCOAL)
sketch_on(idler, "Rim").sketch.add_circle((0, 0), 168)
extrude(idler, idler.features[-1], 210, NEW_BODY, "Idler Wheel")
flange = sketch_on(idler, "Flange")
flange.sketch.add_circle((0, 0), 186)
extrude(idler, flange, 40, JOIN)
sketch_on(idler, "Hub").sketch.add_circle((0, 0), 74)
extrude(idler, idler.features[-1], 280, JOIN)
drill(idler, "Hub Bore", [(0, 0)], diameter=96)
IDLER = save(idler, "Idler Wheel")

# ---- dozer blade: a rolled plate with a bolted cutting edge -------------
blade = new_part(STEEL, YELLOW)
face = sketch_on(blade, "Blade Section")
face.sketch.add_arc_slot((300, 190), 305, math.radians(150),
                         math.radians(212), 30)
face.sketch.add_rectangle((26, 0), (74, 44))              # cutting edge
extrude(blade, face, 1520, NEW_BODY, "Blade")
# Pads on the back for the push arms.  A rolled plate is 30 mm thick and
# an arm's pin boss is 124 across, so without somewhere to land the arm
# simply comes out the front of the blade.
for side in (1, -1):
    pad = sketch_on(blade, "Arm Pad", xz_at(side * 360))
    pad.sketch.add_circle((-62, 210), 60)
    extrude(blade, pad, 120, JOIN, extent="distance", reverse=side < 0)
BLADE = save(blade, "Dozer Blade")

# ---- blade arm: tapered box section, a pin at each end ------------------
arm_p = new_part(STEEL, CHARCOAL)
loop(sketch_on(arm_p, "Arm Profile"),
     [(0, -78), (620, -46), (620, 46), (0, 78)])
extrude(arm_p, arm_p.features[-1], 110, NEW_BODY, "Blade Arm")
bosses = sketch_on(arm_p, "Pin Bosses")
bosses.sketch.add_circle((0, 0), 78)
bosses.sketch.add_circle((620, 0), 62)
extrude(arm_p, bosses, 110, JOIN)
drill(arm_p, "Pin Holes", [(0, 0), (620, 0)], diameter=96)
BLADE_ARM = save(arm_p, "Blade Arm")


# ==========================================================================
# the upper works
#
# These are modelled in the machine's own frame rather than in a local one,
# because they all share a datum that means something: the slew axis at
# ground level.  It costs nothing - they are placed at the origin - and it
# buys the one thing that matters here, which is that the whole upper group
# can be swung by putting the same rotation about Z on every one of them.
# ==========================================================================

print()
print("upper works")

# ---- slew ring: what the house turns on ---------------------------------
ring = new_part(STEEL, "Steel, Mill Finish")
sketch_on(ring, "Ring", xy_at(490)).sketch.add_circle((0, 0), 400)
extrude(ring, ring.features[-1], 70, NEW_BODY, "Slew Ring", extent="distance")
sketch_on(ring, "Bore", xy_at(490)).sketch.add_circle((0, 0), 300)
extrude(ring, ring.features[-1], 70, CUT, extent="all")
drill(ring, "Ring Bolts",
      [(355 * math.cos(2 * math.pi * i / 12), 355 * math.sin(2 * math.pi * i / 12))
       for i in range(12)], plane=xy_at(490), diameter=30)
RING = save(ring, "Slew Ring")

# ---- house: deck, engine hood and the boom bracket ----------------------
house = new_part(STEEL, YELLOW)
deck = sketch_on(house, "Deck Plate", xy_at(560))
deck.sketch.add_rectangle((-560, -760), (620, 760))
extrude(house, deck, 140, NEW_BODY, "House", extent="distance")
box(house, (520, 1400, 460), (-560, -700, 700), JOIN, label="Engine Hood")
# both the deck and the hood are plate, not billet, so the inside comes out
box(house, (1100, 1440, 100), (-520, -720, 580), CUT, label="Deck Pocket")
box(house, (440, 1320, 380), (-520, -660, 700), CUT, label="Hood Pocket")
box(house, (220, 480, 700), (480, -380, 560), JOIN, label="Boom Bracket")
nose = sketch_on(house, "Bracket Nose", xz_at(100))
nose.sketch.add_circle((BOOM_PIVOT[0], BOOM_PIVOT[2]), 175)
extrude(house, nose, 480, JOIN, extent="distance")
# and the slot that makes it a clevis: the boom's root sits in the gap,
# and so does the eye of the ram that lifts it
box(house, (300, 320, 820), (400, -300, 560), CUT, label="Bracket Slot")
bore(house, "Bracket Bores", xz_at(105),
     [(BOOM_PIVOT[0], BOOM_PIVOT[2]), BOOM_RAM_BASE], 96, 490)
# engine vents, both sides of the hood
for side in (1, -1):
    vents = sketch_on(house, "Hood Vents", xz_at(side * 700))
    for i in range(6):
        vents.sketch.add_rectangle((-470, 860 + i * 50), (-130, 886 + i * 50))
    extrude(house, vents, 40, CUT, extent="distance", reverse=side > 0)
HOUSE = save(house, "House")

# ---- counterweight: the faceted tail --------------------------------------
weight = new_part(STEEL, CHARCOAL)
loop(sketch_on(weight, "Counterweight Plan", xy_at(560)),
     [(-560, -760), (-560, 760), (-880, 420), (-880, -420)])
extrude(weight, weight.features[-1], 600, NEW_BODY, "Counterweight",
        extent="distance")
WEIGHT = save(weight, "Counterweight")

# ---- cab: four posts, a roof, and panels up to the glass line -----------
CAB = dict(x0=-200.0, x1=660.0, y0=120.0, y1=740.0, z0=700.0, z1=2050.0)
cab = new_part(STEEL, YELLOW)
posts = sketch_on(cab, "Cab Posts", xy_at(CAB["z0"]))
for px in (CAB["x0"], CAB["x1"] - 80.0):
    for py in (CAB["y0"], CAB["y1"] - 80.0):
        posts.sketch.add_rectangle((px, py), (px + 80.0, py + 80.0))
extrude(cab, posts, CAB["z1"] - CAB["z0"], NEW_BODY, "Cab", extent="distance")
box(cab, (900, 660, 40), (-220, 100, CAB["z1"]), JOIN, label="Roof")
box(cab, (860, 620, 30), (-200, 120, 700), JOIN, label="Floor")
box(cab, (24, 620, 1290), (-200, 120, 760), JOIN, label="Rear Panel")
box(cab, (24, 620, 300), (636, 120, 760), JOIN, label="Front Panel")
box(cab, (860, 24, 300), (-200, 120, 760), JOIN, label="Right Panel")
box(cab, (860, 24, 300), (-200, 716, 760), JOIN, label="Left Panel")
box(cab, (70, 24, 1290), (110, 716, 760), JOIN, label="Door Post")
CAB_PART = save(cab, "Cab Frame")

# ---- glazing: four panes, and the only transparent part in the machine --
glass = new_part("Glass", "Glass, Clear")
box(glass, (16, 540, 960), (620, 160, 1060), NEW_BODY, "Cab Glazing",
    label="Windscreen")
box(glass, (760, 16, 960), (-170, 700, 1060), JOIN, label="Door Glass")
box(glass, (760, 16, 960), (-170, 144, 1060), JOIN, label="Right Glass")
box(glass, (16, 540, 960), (-176, 160, 1060), JOIN, label="Rear Glass")
GLASS = save(glass, "Cab Glazing")

# ---- seat ---------------------------------------------------------------
seat = new_part("ABS", "Plastic, Matte Black")
box(seat, (280, 320, 300), (60, 300, 760), NEW_BODY, "Seat", label="Pedestal")
box(seat, (460, 480, 110), (0, 220, 1060), JOIN, label="Cushion")
box(seat, (130, 480, 520), (-60, 220, 1170), JOIN, label="Backrest")
SEAT = save(seat, "Seat")

# ---- work light ---------------------------------------------------------
light = new_part("ABS", "Plastic, Matte Black")
box(light, (110, 96, 96), (0, -48, -48), NEW_BODY, "Work Light",
    label="Housing")
box(light, (18, 76, 76), (104, -38, -38), JOIN, label="Lens")
cylinder(light, 16, 60, (40, 0, -104), JOIN, label="Stalk")
LIGHT = save(light, "Work Light")

# ---- exhaust ------------------------------------------------------------
stack = new_part(STEEL, "Steel, Mill Finish")
cylinder(stack, 42, 300, (-300, -520, 1160), NEW_BODY, "Exhaust",
         label="Stack")
cylinder(stack, 56, 26, (-300, -520, 1434), JOIN, label="Rain Cap")
STACK = save(stack, "Exhaust Stack")


# ==========================================================================
# the boom group
#
# Each of these is modelled lying along its own +X with its main pivot on
# its own origin, which is the frame its drawings would use and the frame
# that makes placing it one angle instead of three numbers.
# ==========================================================================

print()
print("boom group")

# ---- boom: a bent plate weldment with four pin bosses -------------------
boom = new_part(STEEL, YELLOW)
loop(sketch_on(boom, "Boom Profile"),
     [(0, -115), (1150, 430), (2260, 165), (2260, 300), (1150, 700), (0, 115)])
extrude(boom, boom.features[-1], 300, NEW_BODY, "Boom")

sketch_on(boom, "Root Boss").sketch.add_circle((0, 0), 115)
extrude(boom, boom.features[-1], 300, JOIN)

# The tip is wider than the web and then slotted, so the arm's root sits
# inside it rather than through it.  Every joint on this machine is a
# clevis and a tongue; two bosses of the same width at one pin would be
# two parts trying to be in the same place.
sketch_on(boom, "Tip Boss").sketch.add_circle((2260, 232), 100)
extrude(boom, boom.features[-1], 380, JOIN)
box(boom, (300, 250, 300), (2110, -125, 82), CUT, label="Tip Clevis")

# the boom ram pushes on a boss slung under the web, in its own clevis
sketch_on(boom, "Ram Boss").sketch.add_circle((700, 100), 150)
extrude(boom, boom.features[-1], 300, JOIN)
box(boom, (300, 100, 200), (550, -50, -10), CUT, label="Ram Clevis")

# and the arm ram sits in a clevis on top
sketch_on(boom, "Arm Ram Boss").sketch.add_circle((1350, 700), 100)
extrude(boom, boom.features[-1], 300, JOIN)
box(boom, (200, 120, 260), (1250, -60, 560), CUT, label="Arm Ram Clevis")

drill(boom, "Pin Bores",
      [(0, 0), (2260, 232), (700, 100), (1350, 700)], diameter=96)
BOOM = save(boom, "Boom")

# ---- arm: the same again, shorter, with the linkage lugs ----------------
arm = new_part(STEEL, YELLOW)
loop(sketch_on(arm, "Arm Profile"),
     [(0, -130), (1250, -75), (1250, 75), (0, 130)])
extrude(arm, arm.features[-1], 240, NEW_BODY, "Arm")

ends = sketch_on(arm, "Pivot Bosses")
ends.sketch.add_circle((0, 0), 130)
ends.sketch.add_circle((1250, 0), 95)
extrude(arm, ends, 240, JOIN)

sketch_on(arm, "Bucket Ram Boss").sketch.add_circle((230, 210), 105)
extrude(arm, arm.features[-1], 240, JOIN)
box(arm, (200, 100, 180), (130, -50, 150), CUT, label="Clevis Slot")

# The heel: the arm reaches back past its own pivot, and the arm ram
# pulls on the end of it.  Without that the ram would have to reach a lug
# on the near side of the pivot, which means crossing the boom to get to
# it - and a ram drawn through the boom is the first thing anyone sees.
heel = sketch_on(arm, "Heel")
heel.sketch.add_slot((0, 0), (-220, 330), 200)
extrude(arm, heel, 240, JOIN)
box(arm, (220, 90, 220), (-330, -45, 220), CUT, label="Heel Clevis")

# the two links straddle this one, so it is narrower than the arm
sketch_on(arm, "Link Boss").sketch.add_circle((1080, 160), 85)
extrude(arm, arm.features[-1], 220, JOIN)

drill(arm, "Pin Bores",
      [(0, 0), (1250, 0), (230, 210), (-220, 330), (1080, 160)], diameter=96)
ARM = save(arm, "Arm")

# ---- bucket: a shell cut out of a solid, with an ear plate on the back --
bucket = new_part(STEEL, YELLOW)
loop(sketch_on(bucket, "Bucket Section"),
     [(0, 0), (-70, -300), (-40, -560), (260, -680), (640, -610), (680, -540)])
extrude(bucket, bucket.features[-1], 620, NEW_BODY, "Bucket")

# The pocket runs past the mouth on purpose: the last edge of it sits
# outside the solid, which is what opens the bucket rather than leaving a
# lid on it.  Cut 520 of the 620 width, so 50 mm of side plate stays.
loop(sketch_on(bucket, "Bucket Pocket"),
     [(55, -70), (-10, -300), (15, -530), (270, -630), (615, -565),
      (700, -500), (90, 80)])
extrude(bucket, bucket.features[-1], 520, CUT)

ear = sketch_on(bucket, "Ear Plate")
ear.sketch.add_slot((0, 0), (200, 170), 190)
ear.sketch.add_slot((-40, -300), (0, 0), 170)
extrude(bucket, ear, 240, JOIN)
drill(bucket, "Pin Bores", [(0, 0), (200, 170)], diameter=96)
BUCKET = save(bucket, "Bucket")

# ---- bucket tooth ------------------------------------------------------
tooth = new_part(STEEL, "Steel, Mill Finish")
loop(sketch_on(tooth, "Tooth Profile"),
     [(0, -62), (200, -26), (245, 0), (200, 26), (0, 62)])
extrude(tooth, tooth.features[-1], 96, NEW_BODY, "Bucket Tooth")
TOOTH = save(tooth, "Bucket Tooth")

# ---- ram barrel and rod, aimed at each other from their two mounts -----
#
# A ram is two parts, and they are not constrained to each other: the barrel
# is placed at one mount pointing at the other, the rod at the other mount
# pointing back, and how far the rod is into the barrel is whatever the
# distance between the mounts leaves over.  That is what a real one does,
# and it means no ram here has its stroke worked out by hand.
#
# Two sizes, because one will not stretch: the boom rams are over a metre
# between eyes and the bucket's is little more than half that.


def ram_barrel(name, appearance, eye_r, tube_r, tube_from, tube_len):
    doc = new_part(STEEL, appearance)
    eye = sketch_on(doc, "Barrel Eye")
    eye.sketch.add_circle((0, 0), eye_r)
    eye.sketch.add_rectangle((0, -eye_r * 0.6), (tube_from + 30, eye_r * 0.6))
    extrude(doc, eye, tube_r * 1.45, NEW_BODY, name)
    sketch_on(doc, "Tube", yz_at(tube_from)).sketch.add_circle((0, 0), tube_r)
    extrude(doc, doc.features[-1], tube_len, JOIN, extent="distance")
    gland = tube_from + tube_len - 50.0
    sketch_on(doc, "Gland", yz_at(gland)).sketch.add_circle((0, 0), tube_r + 6)
    extrude(doc, doc.features[-1], 56, JOIN, extent="distance")
    for at in (tube_from + 100.0, tube_from + tube_len - 80.0):
        cylinder(doc, 20, 44, (at, 0, tube_r - 12), JOIN, label="Port Boss")
    drill(doc, "Eye Bore", [(0, 0)], diameter=96)
    return save(doc, name)


def ram_rod(name, eye_r, rod_r, rod_from, rod_len, piston_r):
    doc = new_part(STEEL, "Steel, Polished")
    eye = sketch_on(doc, "Rod Eye")
    eye.sketch.add_circle((0, 0), eye_r)
    eye.sketch.add_rectangle((0, -eye_r * 0.53), (rod_from + 30, eye_r * 0.53))
    extrude(doc, eye, rod_r * 2.2, NEW_BODY, name)
    sketch_on(doc, "Rod", yz_at(rod_from)).sketch.add_circle((0, 0), rod_r)
    extrude(doc, doc.features[-1], rod_len, JOIN, extent="distance")
    head = rod_from + rod_len - 46.0
    sketch_on(doc, "Piston", yz_at(head)).sketch.add_circle((0, 0), piston_r)
    extrude(doc, doc.features[-1], 46, JOIN, extent="distance")
    drill(doc, "Eye Bore", [(0, 0)], diameter=96)
    return save(doc, name)


# big: eyes 906 to 1416 apart.  small: 550 to 890.
BARREL = ram_barrel("Ram Barrel", CHARCOAL, 80, 62, 120, 560)
ROD = ram_rod("Ram Rod", 68, 32, 80, 700, 58)
BARREL_S = ram_barrel("Bucket Ram Barrel", CHARCOAL, 64, 52, 90, 340)
ROD_S = ram_rod("Bucket Ram Rod", 54, 26, 60, 400, 48)
BIG_RAM = (0.0 + 120, 120.0 + 560)      # the tube the big piston must stay in
SMALL_RAM = (90.0, 90.0 + 340)
BIG_ROD, SMALL_ROD = 780.0, 460.0       # rod eye to the back of the piston

# ---- two pins, ten joints -----------------------------------------------
#
# One pin for every joint sounds tidy until you look at it: the main
# pivots are held in brackets 380 to 480 wide and the ram eyes in clevises
# half that, so a pin long enough for the boom stands out of a ram eye by
# the length of your hand.  Two sizes, and each still does five joints.


def pivot_pin(name, radius, length, head_r):
    doc = new_part(STEEL, "Steel, Polished")
    sketch_on(doc, "Pin").sketch.add_circle((0, 0), radius)
    extrude(doc, doc.features[-1], length, NEW_BODY, name)
    for side in (1, -1):
        at = length / 2.0 + 22.0
        sketch_on(doc, "Head", xz_at(side * at)).sketch.add_circle(
            (0, 0), head_r)
        extrude(doc, doc.features[-1], 22, JOIN, extent="distance",
                reverse=side < 0)
    return save(doc, name)


PIN = pivot_pin("Pivot Pin", 48, 360, 62)
RAM_PIN = pivot_pin("Ram Pin", 40, 240, 52)

# ---- bucket link -------------------------------------------------------
link = new_part(STEEL, CHARCOAL)
sketch_on(link, "Link Plate").sketch.add_slot((0, 0), (LINK, 0), 160)
extrude(link, link.features[-1], 70, NEW_BODY, "Bucket Link")
drill(link, "Pin Bores", [(0, 0), (LINK, 0)], diameter=96)
LINK_PLATE = save(link, "Bucket Link")


# ==========================================================================
# the assembly
#
# Three things are worked out here rather than typed in: where the boom,
# arm and bucket put their own pin centres, where the linkage lands, and
# which way each ram has to look to join its two mounts.  Everything else
# is a mount point on a part that was drawn with that mount on it.
# ==========================================================================

print()
print("assembly")


def turn_y(deg):
    """Rotate about Y: a positive angle tips the part's own +X downwards."""
    return [0.0, math.radians(deg), 0.0]


def turn_z(deg):
    return [0.0, 0.0, math.radians(deg)]


def compose(outer, inner):
    """``inner`` placed, and then moved again by ``outer``.

    This is what lets the whole upper works be built square to the world
    and then swung: the slew goes on the outside of every placement in the
    group, so the boom, its rams and all eleven pins turn with the house
    and stay where the linkage put them.
    """
    ro = outer.matrix()
    where = ro @ np.asarray(inner.position, dtype=float) \
        + np.asarray(outer.position, dtype=float)
    return Placement(list(where),
                     list(rotation_vector(ro @ inner.matrix())))


def aim(frm, to):
    """The rotation that swings a part's own +X onto the line frm -> to.

    Every ram in this machine lies in the plane the boom swings through, so
    this only ever needs to turn about Y, which is the one turn a rotation
    vector can express without any trigonometry going astray.
    """
    return turn_y(math.degrees(math.atan2(-(to[2] - frm[2]), to[0] - frm[0])))


def span(a, b):
    return math.dist((a[0], a[2]), (b[0], b[2]))


asm = AssemblyDocument()
asm.path = os.path.join(OUT, "Excavator.adat")
placed_count = [0]


def put(path, where, turn=None, label="", outer=None):
    """Place one component, grounded, at a worked-out position."""
    occurrence = asm.place(path, label)
    inner = Placement([float(c) for c in where], list(turn or [0.0, 0.0, 0.0]))
    occurrence.placement = compose(outer, inner) if outer is not None else inner
    occurrence.grounded = True      # placed by construction, not by solving
    placed_count[0] += 1
    return occurrence


# ---- undercarriage: square to the world ---------------------------------
put(FRAME, (0, 0, 105), label="Track Frame")
for side in (1, -1):
    y = side * TRACK_GAUGE / 2.0
    put(TRACK, (0, y, TRACK_R), label="Rubber Track")
    put(SPROCKET, (-TRACK_CENTRES, y, TRACK_R), label="Drive Sprocket")
    put(IDLER, (TRACK_CENTRES, y, TRACK_R), label="Idler Wheel")
    put(BLADE_ARM, (680, side * 300, 250), label="Blade Arm")
put(BLADE, (1370, 0, 40), label="Dozer Blade")
put(RING, (0, 0, 0), label="Slew Ring")

# ---- the upper works, all of it swung by the same rotation --------------
slew = Placement([0.0, 0.0, 0.0], turn_z(SLEW))
for path, label in ((HOUSE, "House"), (WEIGHT, "Counterweight"),
                    (CAB_PART, "Cab Frame"), (GLASS, "Cab Glazing"),
                    (SEAT, "Seat"), (STACK, "Exhaust Stack")):
    put(path, (0, 0, 0), label=label, outer=slew)

for y in (200, 640):
    put(LIGHT, (596, y, 2138), label="Work Light", outer=slew)

# ---- the boom group, by angle -------------------------------------------
boom_at = Placement(list(BOOM_PIVOT), turn_y(-BOOM_ANGLE))
arm_pivot = boom_at.apply_point((2260, 0, 232))
arm_at = Placement(list(arm_pivot), turn_y(-ARM_ANGLE))
bucket_pivot = arm_at.apply_point((1250, 0, 0))
bucket_at = Placement(list(bucket_pivot), turn_y(-BUCKET_ANGLE))

put(BOOM, boom_at.position, boom_at.rotation, "Boom", outer=slew)
put(ARM, arm_at.position, arm_at.rotation, "Arm", outer=slew)
put(BUCKET, bucket_at.position, bucket_at.rotation, "Bucket", outer=slew)

for i in range(5):
    put(TOOTH, (620, -240 + i * 120, -570), turn_y(20), "Bucket Tooth",
        outer=compose(slew, bucket_at))

# the linkage: the ear on the bucket, and the joint one link away from it
ear = bucket_at.apply_point((200, 0, 170))
back = np.asarray(arm_pivot) - np.asarray(ear)
back = back / math.hypot(back[0], back[2])
joint = tuple(np.asarray(ear) + back * LINK)
for side in (1, -1):
    # 160 either side of the joint, which is not on the centreline: the
    # whole boom group is offset, so the links follow it
    put(LINK_PLATE, (joint[0], joint[1] + side * 160, joint[2]),
        aim(joint, ear), "Bucket Link", outer=slew)

# the rams, each one placed from its two mounts and no other number
RAMS = [
    ("Boom Ram", (BOOM_RAM_BASE[0], BOOM_PIVOT[1], BOOM_RAM_BASE[1]),
     boom_at.apply_point((700, 0, 100)), BARREL, ROD, BIG_RAM, BIG_ROD),
    ("Arm Ram", boom_at.apply_point((1350, 0, 700)),
     arm_at.apply_point((-220, 0, 330)), BARREL, ROD, BIG_RAM, BIG_ROD),
    ("Bucket Ram", arm_at.apply_point((230, 0, 210)), joint,
     BARREL_S, ROD_S, SMALL_RAM, SMALL_ROD),
]
for label, base, head, barrel_path, rod_path, tube, rodlen in RAMS:
    reach = span(base, head)
    piston = reach - rodlen
    fit = "ok" if tube[0] < piston < tube[1] else "OUT OF STROKE"
    print("  %-11s %6.0f mm between eyes, piston %4.0f mm into the tube  %s"
          % (label, reach, piston, fit))
    put(barrel_path, base, aim(base, head), label + " Barrel", outer=slew)
    put(rod_path, head, aim(head, base), label + " Rod", outer=slew)

# ---- and the pins: the long one through the brackets, the short one
# through the ram eyes ----------------------------------------------------
for where in (BOOM_PIVOT, arm_pivot, bucket_pivot, joint, ear):
    put(PIN, where, label="Pivot Pin", outer=slew)
for where in ((BOOM_RAM_BASE[0], BOOM_PIVOT[1], BOOM_RAM_BASE[1]),
              boom_at.apply_point((700, 0, 100)),
              boom_at.apply_point((1350, 0, 700)),
              arm_at.apply_point((-220, 0, 330)),
              arm_at.apply_point((230, 0, 210))):
    put(RAM_PIN, where, label="Ram Pin", outer=slew)

report = asm.rebuild()
print("  %s" % report.message)
if report.errors:
    print("  errors:", report.errors)
bb = kernel.bounding_box(asm.shape)
print("  %d components from %d parts"
      % (placed_count[0], len({o.ref.path for o in asm.occurrences})))
print("  %.0f long, %.0f wide, %.0f tall"
      % (bb[3] - bb[0], bb[4] - bb[1], bb[5] - bb[2]))
asm.save(asm.path)
print()
print("all written to %s" % OUT)
