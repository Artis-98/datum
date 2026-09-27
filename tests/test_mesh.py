"""Speed work that must not change an answer.

Everything here was made faster, and each check is that it gives what
the slow version gave: the same sub-shapes in the same order, the same
references found, the same body drawn. Where it matters it also checks
the thing that made it slow is gone, because a speedup that quietly
comes undone is the sort nobody notices for a year.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WORK = tempfile.mkdtemp(prefix="datum_mesh_")
os.environ["DATUM_CONFIG_DIR"] = os.path.join(WORK, "cfg")

from OCP.BRep import BRep_Tool                                 # noqa: E402
from OCP.BRepMesh import BRepMesh_IncrementalMesh              # noqa: E402
from OCP.BRepTools import BRepTools                            # noqa: E402
from OCP.TopAbs import (TopAbs_EDGE, TopAbs_FACE,              # noqa: E402
                        TopAbs_VERTEX, TopAbs_WIRE)
from OCP.TopExp import TopExp_Explorer                         # noqa: E402
from OCP.TopLoc import TopLoc_Location                         # noqa: E402
from OCP.gp import gp_Trsf, gp_Vec                             # noqa: E402

from datum.core import bodycache, geometry, kernel, mesh       # noqa: E402
from datum.core.document import Document                       # noqa: E402
from datum.core.features import CodeFeature, NEW_BODY          # noqa: E402
from datum.core.naming import RefSet, ShapeRef                 # noqa: E402
from datum.core.parts import PartLibrary                       # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def moved(shape, x):
    t = gp_Trsf()
    t.SetTranslation(gp_Vec(x, 0, 0))
    return shape.Moved(TopLoc_Location(t))


def first_face_deflection(shape):
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    from OCP.TopoDS import TopoDS
    tri = BRep_Tool.Triangulation_s(TopoDS.Face_s(explorer.Current()),
                                    TopLoc_Location())
    return None if tri is None else tri.Deflection()


print("meshing on every core")

check("OpenCASCADE meshes in parallel by default now",
      BRepMesh_IncrementalMesh.IsParallelDefault_s() == mesh.PARALLEL)
body = geometry.box(40, 30, 20).fillet(4).shape
check("a fresh body has no mesh", not mesh.is_meshed(body))
check("mesh() meshes it", mesh.mesh(body) and mesh.is_meshed(body))
copy = moved(body, 500)
check("a placed copy wants the same mesh as the part",
      abs(mesh.deflection(copy) - mesh.deflection(body)) < 1e-12)
check("  and already has it, being the same faces", mesh.is_meshed(copy))

coarse = geometry.cylinder(20, 50).shape
BRepMesh_IncrementalMesh(coarse, 5.0, False, 0.5, True)
check("a mesh made for something else, too coarse to draw, is not taken",
      not mesh.is_meshed(coarse))
mesh.mesh(coarse)
check("  and is made fine enough", mesh.is_meshed(coarse))

parts = [geometry.box(10, 10, 10).fillet(1).shape,
         geometry.cylinder(5, 40).shape,
         geometry.sphere(8).shape]
placed = [moved(p, i * 100) for i in range(4) for p in parts]
check("an assembly's parts are meshed once each, however often placed",
      mesh.mesh_all(placed) == 3)
check("  and every copy is then drawable",
      all(mesh.is_meshed(p) for p in placed))
check("  and asking again does nothing", mesh.mesh_all(placed) == 0)
check("a wire has nothing to mesh and says so",
      not mesh.mesh(kernel.edges(body)[0]))


print()
print("the same sub-shapes, in the same order, without the wait")


def slow_explore(shape, kind):
    out = []
    explorer = TopExp_Explorer(shape, kind)
    while explorer.More():
        current = explorer.Current()
        if not any(current.IsSame(s) for s in out):
            out.append(current)
        explorer.Next()
    return out


plate = (geometry.box(200, 200, 5) - geometry.cylinder(3, 5)
         .repeat(8, x=20).repeat(8, y=20).move(20, 20)).shape
for shape, label in ((body, "a filleted block"), (plate, "a drilled plate")):
    for kind in (TopAbs_FACE, TopAbs_EDGE, TopAbs_VERTEX, TopAbs_WIRE):
        slow, fast = slow_explore(shape, kind), kernel.explore(shape, kind)
        same = len(slow) == len(fast) and all(
            a.IsSame(b) and a.Orientation() == b.Orientation()
            for a, b in zip(slow, fast))
        check("%s, %s" % (label, str(kind).split("_")[-1].lower()), same,
              (len(slow), len(fast)))


print()
print("references found as before, several at once")

edges = kernel.edges(plate)
verts = kernel.vertices(plate)
together = RefSet()
together.capture_from(plate, "edge", edges[::7])
corners = [ShapeRef.capture(verts[i], "vertex", i, within=plate)
           for i in range(0, len(verts), 13)]
together.refs.extend(corners)
found, lost = together.resolve_all(plate)
alone = []
for ref in RefSet.from_list(together.to_list()):
    got = ref.rebind(plate)
    if got is not None and not any(got.IsSame(f) for f in alone):
        alone.append(got)
check("resolving together finds what resolving one by one finds",
      len(found) == len(alone) and all(a.IsSame(b)
                                       for a, b in zip(found, alone)),
      (len(found), len(alone)))
check("  and loses nothing", not lost, len(lost))
check("  and leaves nothing behind on the references",
      all(getattr(r, "_shared", None) is None for r in together))


print()
print("a cached body comes back ready to draw")

part_path = os.path.join(WORK, "posts.pdat")
doc = Document()
feature = CodeFeature()
feature.source = "result(cylinder(10, 120).fillet(2).repeat(6, x=40))"
feature.operation = NEW_BODY
doc.add_feature(feature)
doc.rules.trusted = True
doc.rebuild()
doc.save(part_path)
from datum.core import rules                                    # noqa: E402
rules.trust_path(part_path)

library = PartLibrary()
shape = library.shape(part_path)
check("a part built for an assembly is meshed as it is cached",
      shape is not None and mesh.is_meshed(shape))
again = bodycache.load(bodycache.key_for(part_path))
check("  and the mesh is kept with the cached body",
      again is not None and mesh.is_meshed(again))
check("the cache was bumped for it", bodycache.CACHE_VERSION >= 2)


print()
print("FAILED: " + ", ".join(FAILS) if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
