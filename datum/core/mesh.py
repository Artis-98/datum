"""The triangles a body is drawn with, made on every core, once.

Drawing a body is almost entirely meshing it: the time from handing a
shape to the viewport to seeing it was within a few per cent of the time
it took to mesh, and OpenCASCADE was meshing on one core. Meshing now runs
across every core, face by face.

There was a second cost hiding behind that one. The viewport asks for its
own mesh quality, and OpenCASCADE's answer to a shape drawn with its own
quality is to throw away whatever mesh the shape already has and make it
again. An assembly is the same few parts placed many times, sharing their
faces, so a pin placed five times was meshed five times over, each time
wiping the last. Here the mesh is made once per part, parts that want
about the same quality are meshed together so small ones share the cores,
and the viewport is told to draw the mesh it is given.

Quality is unchanged. The tolerance is the one the viewport always asked
for: a deviation of 0.08 per cent of the body's largest side, and no more
than 8 degrees between neighbouring facets.

``DATUM_SERIAL_MESH`` puts meshing back on one core, for the day parallel
meshing turns out to matter on one machine in a thousand.
"""

from __future__ import annotations

import os
from collections import OrderedDict
from typing import Iterable, List, Optional, Tuple

from OCP.BRep import BRep_Builder, BRep_Tool
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.Bnd import Bnd_Box
from OCP.IMeshTools import IMeshTools_MeshAlgoType, IMeshTools_Parameters
from OCP.TopAbs import TopAbs_FACE
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS, TopoDS_Compound, TopoDS_Shape

# The viewport's quality, the same numbers it has always used: a relative
# deviation, which OpenCASCADE scales by four and by the largest side of
# the bounding box, and an angle in radians.
DEVIATION = 2.0e-4
ANGLE = 0.14

PARALLEL = not os.environ.get("DATUM_SERIAL_MESH")

# Delabella, OpenCASCADE's other triangulator, stays off unless asked for
# with DATUM_MESH_ALGO=delabella. On a 1200 mm plate with 900 holes it made
# the same triangles in 1.7 s that the default takes 15.5 s over, but on a
# 300 mm plate with 144 holes it took 56 s over what the default does in
# 0.7, and a regular grid of holes is exactly what real parts are made of.
# A triangulator that is sometimes eighty times slower is not a default.
DELABELLA = os.environ.get("DATUM_MESH_ALGO", "").lower() == "delabella"

# Parts whose tolerances are this close are meshed together in one pass,
# at the finer of the two.  Meshing together is what lets a dozen small
# parts share the cores instead of taking turns at them.
GROUP_RATIO = 1.5

# How many bodies to remember having meshed.  Each one held here is kept
# alive by being held, so this stays small: enough for an assembly's
# distinct parts and the last few versions of a part being edited.
REMEMBER = 64


def enable_parallel_default() -> None:
    """Make every mesh OpenCASCADE makes on its own a parallel one too.

    Most meshing goes through mesh() below, but anything that still asks
    OpenCASCADE to triangulate by itself, a selection or a preview, gets
    the cores as well.
    """
    try:
        BRepMesh_IncrementalMesh.SetParallelDefault_s(PARALLEL)
    except Exception:
        pass


def _unplaced(shape: TopoDS_Shape) -> TopoDS_Shape:
    """The part itself, wherever this copy of it has been put."""
    return shape.Located(TopLoc_Location())


def deflection(shape: TopoDS_Shape) -> float:
    """How far a facet may stray from the surface, for this body.

    Taken from the body as it was modelled, not as it is placed, so every
    copy of a part asks for the same mesh and one mesh serves them all.
    """
    box = Bnd_Box()
    try:
        BRepBndLib.Add_s(_unplaced(shape), box, False)
    except Exception:
        return 0.1
    if box.IsVoid():
        return 0.1
    x0, y0, z0, x1, y1, z1 = box.Get()
    size = max(x1 - x0, y1 - y0, z1 - z0)
    if not size > 0.0:
        return 0.1
    return size * DEVIATION * 4.0


def _faces(shape: TopoDS_Shape) -> Iterable:
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        yield TopoDS.Face_s(explorer.Current())
        explorer.Next()


def has_faces(shape: Optional[TopoDS_Shape]) -> bool:
    if shape is None or shape.IsNull():
        return False
    return TopExp_Explorer(shape, TopAbs_FACE).More()


# ---------------------------------------------------------------- memory
#
# Which bodies were meshed here, and how finely. The mesh itself cannot be
# asked: it stores the deviation the mesher says it achieved, and on a
# curved face that is often twice what it was asked for however many times
# it is asked, while a mesh made coarse by its angle can report a figure
# that looks fine. So a body this module meshed is taken on its word, and
# anything else has to show a stored figure at least as fine as wanted.

_MESHED: "OrderedDict[int, List[Tuple[TopoDS_Shape, float]]]" = OrderedDict()


def _remember(body: TopoDS_Shape, tolerance: float) -> None:
    key = hash(body)
    held = [entry for entry in _MESHED.pop(key, [])
            if not entry[0].IsPartner(body)]
    held.append((body, float(tolerance)))
    _MESHED[key] = held
    while len(_MESHED) > REMEMBER:
        _MESHED.popitem(last=False)


def _remembered(body: TopoDS_Shape, wanted: float) -> bool:
    key = hash(body)
    for held, tolerance in _MESHED.get(key, ()):
        if held.IsPartner(body) and tolerance <= wanted * 1.0001:
            _MESHED.move_to_end(key)
            return True
    return False


def forget() -> None:
    """Drop the memory, and the bodies it keeps alive."""
    _MESHED.clear()


def is_meshed(shape: Optional[TopoDS_Shape],
              wanted: Optional[float] = None) -> bool:
    """Whether every face has triangles fine enough to draw with."""
    if not has_faces(shape):
        return False
    wanted = deflection(shape) if wanted is None else wanted
    body = _unplaced(shape)
    ours = _remembered(body, wanted)
    location = TopLoc_Location()
    for face in _faces(body):
        triangles = BRep_Tool.Triangulation_s(face, location)
        if triangles is None or triangles.NbTriangles() == 0:
            return False
        if not ours and triangles.Deflection() > wanted * 1.01:
            return False
    return True


def _complete(body: TopoDS_Shape) -> bool:
    location = TopLoc_Location()
    for face in _faces(body):
        triangles = BRep_Tool.Triangulation_s(face, location)
        if triangles is None or triangles.NbTriangles() == 0:
            return False
    return True


# ---------------------------------------------------------------- meshing


def parameters(tolerance: float, delabella: bool = DELABELLA
               ) -> IMeshTools_Parameters:
    p = IMeshTools_Parameters()
    p.Deflection = float(tolerance)
    p.Angle = ANGLE
    p.Relative = False
    p.InParallel = PARALLEL
    p.MeshAlgo = (IMeshTools_MeshAlgoType.IMeshTools_MeshAlgoType_Delabella
                  if delabella else
                  IMeshTools_MeshAlgoType.IMeshTools_MeshAlgoType_Watson)
    return p


def _run(shape: TopoDS_Shape, tolerance: float,
         delabella: bool = DELABELLA) -> None:
    try:
        BRepMesh_IncrementalMesh(shape, parameters(tolerance, delabella))
    except Exception:
        pass


def mesh(shape: Optional[TopoDS_Shape],
         tolerance: Optional[float] = None) -> bool:
    """Mesh a body for drawing, if it is not already.  True when it is.

    False means some face would not mesh, and the viewport should be left
    to make its own attempt rather than draw a body with a hole in it.
    """
    if not has_faces(shape):
        return False
    tolerance = deflection(shape) if tolerance is None else tolerance
    if is_meshed(shape, tolerance):
        return True
    body = _unplaced(shape)
    _run(body, tolerance)
    if not _complete(body) and DELABELLA:
        # the face Delabella cannot do, the default gets a second try at
        _run(body, tolerance, False)
    if not _complete(body):
        return False
    _remember(body, tolerance)
    return True


def mesh_all(shapes: Iterable[Optional[TopoDS_Shape]]) -> int:
    """Mesh everything an assembly is about to draw, in as few passes as fit.

    Each part is meshed once however many times it is placed, and parts
    that want nearly the same tolerance are meshed together, so the cores
    are shared across the whole assembly rather than across one part's
    faces at a time. Returns how many distinct parts needed meshing.
    """
    seen: "OrderedDict[int, List[TopoDS_Shape]]" = OrderedDict()
    todo: List[Tuple[float, TopoDS_Shape]] = []
    for shape in shapes:
        if not has_faces(shape):
            continue
        body = _unplaced(shape)
        bucket = seen.setdefault(hash(body), [])
        if any(body.IsPartner(other) for other in bucket):
            continue
        bucket.append(body)
        wanted = deflection(body)
        if not is_meshed(body, wanted):
            todo.append((wanted, body))
    if not todo:
        return 0

    todo.sort(key=lambda item: item[0])
    groups: List[List[Tuple[float, TopoDS_Shape]]] = []
    for item in todo:
        if groups and item[0] <= groups[-1][0][0] * GROUP_RATIO:
            groups[-1].append(item)
        else:
            groups.append([item])

    builder = BRep_Builder()
    for group in groups:
        # the finest tolerance in the group, so nobody gets a coarser mesh
        # than they would have had alone
        tolerance = group[0][0]
        if len(group) == 1:
            mesh(group[0][1], tolerance)
            continue
        compound = TopoDS_Compound()
        builder.MakeCompound(compound)
        for _wanted, body in group:
            builder.Add(compound, body)
        _run(compound, tolerance)
        for wanted, body in group:
            if _complete(body):
                _remember(body, tolerance)
            else:
                mesh(body, wanted)
    return len(todo)
