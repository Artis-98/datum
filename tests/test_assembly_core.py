"""The assembly document and its 3D constraint solver, without any UI."""

import math
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402,F401


from datum.core import constraints3d, fileformat, kernel          # noqa: E402
from datum.core.assembly import (                                 # noqa: E402
    AssemblyConstraint, AssemblyDocument, Occurrence, open_any,
)
from datum.core.constraints3d import (                            # noqa: E402
    FLUSH, INSERT, MATE, Placement, frame_from_shape,
)
from datum.core.document import Document                          # noqa: E402
from datum.core.features import PrimitiveFeature                   # noqa: E402

FAILED = []


def check(label, ok, extra=""):
    print(("  ok   " if ok else "  FAIL ") + label
          + ("" if ok else "   <- %s" % (extra,)))
    if not ok:
        FAILED.append(label)


WORK = tempfile.mkdtemp(prefix="datum-asm-")


def make_part(kind, a, b, c, name):
    doc = Document()
    feature = PrimitiveFeature()
    feature.kind = kind
    feature.a, feature.b, feature.c = str(a), str(b), str(c)
    doc.add_feature(feature)
    doc.rebuild()
    return doc.save(os.path.join(WORK, name))


# ==========================================================================
print("rotation vectors round-trip")

for w in ((0.0, 0.0, 0.0), (0.0, 0.0, math.pi / 2), (0.3, -1.1, 0.7),
          (0.0, math.pi, 0.0)):
    back = constraints3d.rotation_vector(constraints3d.rotation_matrix(w))
    same = all(abs(a - b) < 1e-6 for a, b in zip(w, back))
    if not same:
        # a half turn has two equal-length answers; either is correct
        same = all(abs(a + b) < 1e-6 for a, b in zip(w, back))
    check("rotation vector %s survives" % (w,), same, back)

check("a long turn is shortened",
      abs(math.sqrt(sum(c * c for c in
                        constraints3d.shorten((0.0, 0.0, 3.0 * math.pi))))
          - math.pi) < 1e-9)

p = Placement([10.0, 0.0, 0.0], [0.0, 0.0, math.pi / 2])
moved = p.apply_point((1.0, 0.0, 0.0))
check("a placement rotates then translates",
      abs(moved[0] - 10.0) < 1e-9 and abs(moved[1] - 1.0) < 1e-9, moved)

trsf = p.trsf()
check("the OCCT transform agrees",
      abs(trsf.TranslationPart().X() - 10.0) < 1e-9,
      trsf.TranslationPart().X())


# ==========================================================================
print("frames are read off real geometry")

box = kernel.box(40, 20, 10)
planes = [frame_from_shape(f) for f in kernel.faces(box)]
check("every box face is a plane",
      all(f is not None and f.kind == constraints3d.PLANE for f in planes))
normals = [f.direction for f in planes]
check("the normals point six different ways",
      len({tuple(round(c, 3) for c in n) for n in normals}) == 6, normals)

top = max(zip(planes, kernel.faces(box)), key=lambda pair: pair[0].origin[2])[0]
check("the top face looks up", top.direction[2] > 0.9, top.direction)

cyl = kernel.cylinder(5.0, 30.0)
axes = [f for f in (frame_from_shape(f) for f in kernel.faces(cyl))
        if f is not None and f.kind == constraints3d.AXIS]
check("the cylinder gives an axis frame", len(axes) == 1, axes)
check("with the right radius", abs(axes[0].radius - 5.0) < 1e-9)
check("pointing along Z", abs(abs(axes[0].direction[2]) - 1.0) < 1e-9)

circles = [f for f in (frame_from_shape(e) for e in kernel.edges(cyl))
           if f is not None and f.kind == constraints3d.CIRCLE]
check("its rims are circular edges", len(circles) == 2, len(circles))


# ==========================================================================
print("the solver satisfies a plane mate")

a_frame = constraints3d.Frame(constraints3d.PLANE, (0, 0, 10), (0, 0, 1))
b_frame = constraints3d.Frame(constraints3d.PLANE, (0, 0, 0), (0, 0, -1))

placements = {1: Placement(), 2: Placement([25.0, 12.0, 40.0], [0.4, 0.2, 0.1])}
constraint = constraints3d.ResolvedConstraint(
    id=1, kind=MATE, occ_a=1, occ_b=2, frame_a=a_frame, frame_b=b_frame)
report = constraints3d.solve(placements, [constraint], [2])
check("it converges", report.ok, report.message)

world_o = placements[2].apply_point(b_frame.origin)
world_d = placements[2].apply_direction(b_frame.direction)
check("the faces end up touching", abs(world_o[2] - 10.0) < 1e-5, world_o)
check("and facing each other", abs(world_d[2] + 1.0) < 1e-5, world_d)
check("the grounded part never moved",
      placements[1].position == [0.0, 0.0, 0.0], placements[1].position)
check("a mate alone leaves 3 DOF", report.dof == 3, report.dof)

print("an offset mate holds the gap")
constraint.offset = 7.5
placements[2] = Placement([5.0, 5.0, 30.0])
constraints3d.solve(placements, [constraint], [2])
world_o = placements[2].apply_point(b_frame.origin)
check("the gap is the offset", abs(world_o[2] - 17.5) < 1e-5, world_o)

print("flush turns both faces the same way")
flush = constraints3d.ResolvedConstraint(
    id=2, kind=FLUSH, occ_a=1, occ_b=2, frame_a=a_frame, frame_b=b_frame)
placements[2] = Placement([5.0, 5.0, 30.0], [0.1, 0.1, 0.0])
constraints3d.solve(placements, [flush], [2])
world_d = placements[2].apply_direction(b_frame.direction)
check("normals now agree", abs(world_d[2] - 1.0) < 1e-5, world_d)


# ==========================================================================
print("insert drops a pin into a hole")

hole = constraints3d.Frame(constraints3d.CIRCLE, (12.0, 8.0, 10.0), (0, 0, 1),
                           3.0)
pin = constraints3d.Frame(constraints3d.CIRCLE, (0.0, 0.0, 0.0), (0, 0, 1), 3.0)
placements = {1: Placement(), 2: Placement([40.0, -20.0, 60.0], [0.9, 0.3, 0.2])}
insert = constraints3d.ResolvedConstraint(
    id=3, kind=INSERT, occ_a=1, occ_b=2, frame_a=hole, frame_b=pin)
report = constraints3d.solve(placements, [insert], [2])
check("it converges", report.ok, report.message)

seat = placements[2].apply_point(pin.origin)
axis = placements[2].apply_direction(pin.direction)
check("the pin lands in the hole",
      all(abs(seat[i] - hole.origin[i]) < 1e-4 for i in range(3)), seat)
check("pointing the opposite way", abs(axis[2] + 1.0) < 1e-4, axis)
check("insert leaves one DOF (spin)", report.dof == 1, report.dof)

print("insert can be flipped to point the same way")
insert.flip = True
placements[2] = Placement([40.0, -20.0, 60.0], [0.9, 0.3, 0.2])
constraints3d.solve(placements, [insert], [2])
axis = placements[2].apply_direction(pin.direction)
check("now aligned", abs(axis[2] - 1.0) < 1e-4, axis)

print("a concentric mate still slides")
concentric = constraints3d.ResolvedConstraint(
    id=4, kind=MATE, occ_a=1, occ_b=2,
    frame_a=constraints3d.Frame(constraints3d.AXIS, (12, 8, 0), (0, 0, 1), 3.0),
    frame_b=constraints3d.Frame(constraints3d.AXIS, (0, 0, 0), (0, 0, 1), 3.0))
placements[2] = Placement([30.0, 30.0, 18.0])
report = constraints3d.solve(placements, [concentric], [2])
check("the axes line up",
      abs(placements[2].position[0] - 12.0) < 1e-5
      and abs(placements[2].position[1] - 8.0) < 1e-5, placements[2].position)
check("but the slide is untouched",
      abs(placements[2].position[2] - 18.0) < 1e-5, placements[2].position)

print("an impossible pair is reported, not silently ignored")
clash_a = constraints3d.ResolvedConstraint(
    id=5, kind=MATE, occ_a=1, occ_b=2, frame_a=a_frame, frame_b=b_frame,
    offset=0.0)
clash_b = constraints3d.ResolvedConstraint(
    id=6, kind=MATE, occ_a=1, occ_b=2, frame_a=a_frame, frame_b=b_frame,
    offset=25.0)
placements[2] = Placement()
report = constraints3d.solve(placements, [clash_a, clash_b], [2])
check("the solve says no", not report.ok, report.message)
check("and names the offenders", len(report.unsatisfied) >= 1,
      report.unsatisfied)


# ==========================================================================
print("an assembly places real parts")

plate = make_part("box", 60, 40, 8, "plate")
peg = make_part("cylinder", 5, 25, 0, "peg")

asm = AssemblyDocument()
asm.path = os.path.join(WORK, "rig.adat")
first = asm.place(plate)
second = asm.place(peg)

check("the first component is grounded", first.grounded)
check("the second is not", not second.grounded)
check("occurrences are named Inventor-style",
      first.name == "plate:1" and second.name == "peg:1",
      (first.name, second.name))
check("the legacy component view still works",
      [c.name for c in asm.components] == ["plate.pdat", "peg.pdat"],
      [c.name for c in asm.components])

report = asm.rebuild()
check("both bodies load", report.placed == 2, report.message)
check("nothing is missing", not report.missing, report.missing)
check("the assembly has a shape", asm.shape is not None)
check("free component keeps 6 DOF", report.dof == 6, report.dof)

print("constraining the peg to the plate")
plate_faces = kernel.faces(first.shape)
top_face = max(plate_faces, key=lambda f: kernel.shape_centre(f)[2])
peg_faces = kernel.faces(second.shape)
peg_bottom = min(peg_faces, key=lambda f: kernel.shape_centre(f)[2])

attach_a = asm.attach(first, "face", top_face)
attach_b = asm.attach(second, "face", peg_bottom)
check("the plate face became an attachment", attach_a is not None)
check("it remembers a plane", attach_a and attach_a.frame.kind == "plane")

asm.add_constraint(AssemblyConstraint(kind=MATE, a=attach_a, b=attach_b))
check("the constraint is named", asm.constraints[0].name == "Mate:1",
      asm.constraints[0].name)

report = asm.rebuild()
check("it still builds", report.ok, report.message)
seat = second.placement.apply_point(attach_b.frame.origin)
check("the peg now sits on the plate", abs(seat[2] - 8.0) < 1e-4, seat)
check("three DOF are left", report.dof == 3, report.dof)


# ==========================================================================
print("it round-trips through the archive")

written = asm.save(asm.path)
check("saved as .adat", written.endswith(".adat"), written)
manifest = fileformat.peek(written)
check("the manifest lists both references", len(manifest.references) == 2)

back = AssemblyDocument.load(written)
check("occurrences come back", len(back.occurrences) == 2)
check("so do constraints", len(back.constraints) == 1)
check("grounding survives", back.occurrences[0].grounded)
check("the placement survives",
      abs(back.occurrences[1].placement.position[2]
          - second.placement.position[2]) < 1e-9)
check("open_any recognises it", isinstance(open_any(written), AssemblyDocument))

report = back.rebuild()
check("and it rebuilds from disk", report.ok, report.message)
seat = back.occurrences[1].placement.apply_point(back.constraints[0].b.frame.origin)
check("to the same place", abs(seat[2] - 8.0) < 1e-4, seat)


# ==========================================================================
print("the reference follows the part when it changes")

edited = Document.load(plate)
edited.features[0].c = "14"          # the plate gets thicker
edited.rebuild()
edited.save(plate)
back.library.forget()
report = back.rebuild()
seat = back.occurrences[1].placement.apply_point(back.constraints[0].b.frame.origin)
check("the peg rides up with the face", abs(seat[2] - 14.0) < 1e-4, seat)


# ==========================================================================
print("a missing part is reported, and the rest still builds")

os.remove(peg)
back.library.forget()
report = back.rebuild()
check("it says what is missing", report.missing, report.missing)
check("the message names the component", "peg" in report.message.lower(),
      report.message)
check("the plate still placed", report.placed == 1, report.placed)
check("and the assembly still has a body", back.shape is not None)


# ==========================================================================
print("deleting an occurrence takes its constraints with it")

asm2 = AssemblyDocument()
asm2.path = os.path.join(WORK, "two.adat")
one = asm2.place(plate)
two = asm2.place(plate)
asm2.rebuild()
asm2.add_constraint(AssemblyConstraint(
    kind=MATE,
    a=asm2.attach(one, "face", kernel.faces(one.shape)[0]),
    b=asm2.attach(two, "face", kernel.faces(two.shape)[0])))
check("two instances of one file", len(asm2.occurrences) == 2)
check("named apart", asm2.occurrences[1].name == "plate:2",
      asm2.occurrences[1].name)
asm2.remove_occurrence(two.id)
check("the constraint went too", asm2.constraints == [], asm2.constraints)

print("undo restores the whole assembly")
asm2.push_undo()
asm2.remove_occurrence(one.id)
check("it is gone", not asm2.occurrences)
check("undo works", asm2.undo())
check("and brings it back", len(asm2.occurrences) == 1)

print("save-as into another folder keeps the links")
elsewhere = os.path.join(WORK, "deeper")
os.makedirs(elsewhere, exist_ok=True)
asm2.save(os.path.join(elsewhere, "two.adat"))
check("paths were rebased",
      asm2.components[0].path == "../plate.pdat", asm2.components[0].path)
check("and still resolve",
      asm2.broken_links() == [], asm2.broken_links())


# ==========================================================================
# ==========================================================================
print("parallel holds a direction and nothing else")

PARALLEL = constraints3d.PARALLEL
check("it is offered as a type", PARALLEL in constraints3d.TYPES)
check("it takes faces and axes, not points",
      constraints3d.compatible(a_frame, b_frame, PARALLEL)
      and not constraints3d.compatible(
          a_frame, constraints3d.Frame(constraints3d.POINT, (0, 0, 0),
                                       (0, 0, 1)), PARALLEL))
top = constraints3d.Frame(constraints3d.PLANE, (0, 0, 10), (0, 0, 1))
side = constraints3d.Frame(constraints3d.PLANE, (5, 0, 0), (1, 0, 0))
parallel = constraints3d.ResolvedConstraint(
    id=5, kind=PARALLEL, occ_a=1, occ_b=2, frame_a=top, frame_b=side)
start = [30.0, -12.0, 44.0]
placements = {1: Placement(), 2: Placement(list(start), [0.3, -0.5, 0.2])}
report = constraints3d.solve(placements, [parallel], [2])
check("it converges", report.ok, report.message)
world_d = placements[2].apply_direction(side.direction)
check("the faces end up looking the same way",
      abs(world_d[2] - 1.0) < 1e-6, world_d)
check("without being pulled across to it",
      max(abs(placements[2].position[i] - start[i]) for i in range(3)) < 1.0,
      placements[2].position)
check("it leaves 4 DOF: every slide and the spin", report.dof == 4,
      report.dof)

parallel.flip = True
placements[2] = Placement(list(start), [0.3, -0.5, 0.2])
constraints3d.solve(placements, [parallel], [2])
world_d = placements[2].apply_direction(side.direction)
check("opposed turns them to face each other",
      abs(world_d[2] + 1.0) < 1e-6, world_d)

shaft = constraints3d.Frame(constraints3d.AXIS, (0, 0, 0), (0, 0, 1), 3.0)
rod = constraints3d.Frame(constraints3d.AXIS, (0, 0, 0), (1, 0, 0), 2.0)
axes = constraints3d.ResolvedConstraint(
    id=6, kind=PARALLEL, occ_a=1, occ_b=2, frame_a=shaft, frame_b=rod)
placements[2] = Placement([40.0, 10.0, 0.0], [0.2, 0.4, 0.0])
constraints3d.solve(placements, [axes], [2])
world_d = placements[2].apply_direction(rod.direction)
check("two axes end up parallel, either way along",
      abs(abs(world_d[2]) - 1.0) < 1e-6, world_d)

saved = AssemblyConstraint(kind=PARALLEL, flip=True)
check("its summary has no value in it",
      saved.summary() == "Parallel  (opposed)", saved.summary())

shutil.rmtree(WORK, ignore_errors=True)
print()
if FAILED:
    print("%d FAILED" % len(FAILED))
    for name in FAILED:
        print("   - %s" % name)
    sys.exit(1)
print("all assembly core checks passed")
