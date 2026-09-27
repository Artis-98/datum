"""Reading and writing geometry: STEP, IGES, STL, BREP."""

from __future__ import annotations

import os
from collections import OrderedDict
from typing import Optional

from OCP.BRep import BRep_Builder
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRepTools import BRepTools
from OCP.IFSelect import IFSelect_ReturnStatus
from OCP.IGESControl import IGESControl_Controller, IGESControl_Reader, IGESControl_Writer
from OCP.Interface import Interface_Static
from OCP.STEPControl import (
    STEPControl_AsIs, STEPControl_Reader, STEPControl_Writer,
)
from OCP.StlAPI import StlAPI_Writer
from OCP.TopoDS import TopoDS_Shape

from . import kernel
from .kernel import KernelError


def _ext(path: str) -> str:
    return os.path.splitext(path)[1].lower()


# -- reading ---------------------------------------------------------------


# Files already read this session, by path, size and time: an undo, a redo
# or reopening the part that imports one would otherwise read it again,
# and a 15 MB STEP file takes five seconds to read. Handing back the same
# shape also means the same faces, so their mesh and what is on screen
# are kept rather than made again. A handful at most: each one held here
# is a whole model kept in memory.
_READ: "OrderedDict[tuple, TopoDS_Shape]" = OrderedDict()
_READ_KEEP = 6


def _key(path: str):
    stat = os.stat(path)
    return (os.path.normcase(os.path.abspath(path)), stat.st_size,
            stat.st_mtime_ns)


def forget() -> None:
    """Let go of the files read this session; the disk cache still has them."""
    _READ.clear()


def remember(path: str, shape: TopoDS_Shape) -> None:
    """This file reads as this shape: it was just written from it."""
    try:
        _READ[_key(path)] = shape
    except OSError:
        return
    while len(_READ) > _READ_KEEP:
        _READ.popitem(last=False)


def read_shape(path: str) -> TopoDS_Shape:
    ext = _ext(path)
    if not os.path.exists(path):
        raise KernelError("file not found: %s" % path)
    key = _key(path)
    held = _READ.get(key)
    if held is not None:
        _READ.move_to_end(key)
        return held
    if ext not in (".step", ".stp", ".iges", ".igs", ".brep", ".brp"):
        raise KernelError("unsupported import format: %s" % ext)
    shape = _from_disk_cache(path)
    if shape is None and ext not in (".brep", ".brp"):
        shape = _translate_elsewhere(path)
    if shape is None:
        if ext in (".step", ".stp"):
            shape = read_step(path)
        elif ext in (".iges", ".igs"):
            shape = read_iges(path)
        else:
            shape = read_brep(path)
        _to_disk_cache(path, shape)
    _READ[key] = shape
    while len(_READ) > _READ_KEEP:
        _READ.popitem(last=False)
    return shape


def _translate_elsewhere(path: str) -> Optional[TopoDS_Shape]:
    """Have a worker translate a file into the cache, the window live meanwhile.

    Translating a 15 MB STEP export and meshing it is nine seconds that
    the window used to spend frozen. A worker does it instead and leaves
    it in the body cache, and reading it from there takes half a second.
    Only in the window, which says how it waits (document.WAIT): a script,
    or a worker, translates for itself. None if no worker could.
    """
    from . import document, workers
    if document.WAIT is None:
        return None
    helpers = workers.pool()
    if helpers is None:
        return None
    try:
        # one still starting is worth waiting for: a second or two, against
        # the whole translation with the window frozen
        document.WAIT(helpers.submit("translate", path=os.path.abspath(path)))
    except Exception:
        return None
    return _from_disk_cache(path)


def _from_disk_cache(path: str) -> Optional[TopoDS_Shape]:
    """The model a file translated to last time, if it is the same file.

    Translating STEP is slow and the answer only depends on the file's
    bytes: five seconds for a 15 MB Inventor export, every time a part
    that imports it is opened. The translation is kept in the body cache,
    keyed on those bytes and stored with its mesh, so opening it again
    reads a binary file instead of translating and meshing.
    """
    from . import bodycache, mesh
    try:
        shape = bodycache.load(bodycache.key_for(path))
    except OSError:
        return None
    if shape is not None:
        mesh.accept_stored(shape)
    return shape


def _to_disk_cache(path: str, shape: TopoDS_Shape) -> None:
    from . import bodycache, mesh
    try:
        key = bodycache.key_for(path)
    except OSError:
        return
    # meshed first, so the copy kept carries its triangles; the viewport
    # was about to do this anyway
    mesh.mesh(shape)
    bodycache.store(key, shape)
    bodycache.housekeep()


def read_step(path: str) -> TopoDS_Shape:
    reader = STEPControl_Reader()
    if reader.ReadFile(path) != IFSelect_ReturnStatus.IFSelect_RetDone:
        raise KernelError("could not read STEP file")
    reader.TransferRoots()
    shapes = [reader.Shape(i) for i in range(1, reader.NbShapes() + 1)]
    shapes = [s for s in shapes if kernel.is_valid(s)]
    if not shapes:
        raise KernelError("STEP file contained no geometry")
    return shapes[0] if len(shapes) == 1 else kernel.compound(shapes)


def read_iges(path: str) -> TopoDS_Shape:
    IGESControl_Controller.Init_s()
    reader = IGESControl_Reader()
    if reader.ReadFile(path) != IFSelect_ReturnStatus.IFSelect_RetDone:
        raise KernelError("could not read IGES file")
    reader.TransferRoots()
    shape = reader.OneShape()
    if not kernel.is_valid(shape):
        raise KernelError("IGES file contained no geometry")
    return shape


def read_brep(path: str) -> TopoDS_Shape:
    shape = TopoDS_Shape()
    builder = BRep_Builder()
    if not BRepTools.Read_s(shape, path, builder):
        raise KernelError("could not read BREP file")
    return shape


# -- writing ---------------------------------------------------------------


def write_shape(shape: TopoDS_Shape, path: str, **kwargs) -> None:
    ext = _ext(path)
    if ext in (".step", ".stp"):
        write_step(shape, path)
    elif ext in (".iges", ".igs"):
        write_iges(shape, path)
    elif ext == ".stl":
        write_stl(shape, path, **kwargs)
    elif ext in (".brep", ".brp"):
        write_brep(shape, path)
    else:
        raise KernelError("unsupported export format: %s" % ext)


def write_step(shape: TopoDS_Shape, path: str,
               schema: str = "AP214IS") -> None:
    writer = STEPControl_Writer()
    Interface_Static.SetCVal_s("write.step.schema", schema)
    Interface_Static.SetCVal_s("write.step.unit", "MM")
    if writer.Transfer(shape, STEPControl_AsIs) != IFSelect_ReturnStatus.IFSelect_RetDone:
        raise KernelError("STEP transfer failed")
    if writer.Write(path) != IFSelect_ReturnStatus.IFSelect_RetDone:
        raise KernelError("could not write STEP file")


def write_iges(shape: TopoDS_Shape, path: str) -> None:
    IGESControl_Controller.Init_s()
    writer = IGESControl_Writer("MM", 0)
    writer.AddShape(shape)
    writer.ComputeModel()
    if not writer.Write(path):
        raise KernelError("could not write IGES file")


def write_stl(shape: TopoDS_Shape, path: str, deflection: float = 0.05,
              angular: float = 0.3, ascii_mode: bool = False) -> None:
    BRepMesh_IncrementalMesh(shape, deflection, False, angular, True)
    writer = StlAPI_Writer()
    writer.ASCIIMode = ascii_mode
    if not writer.Write(shape, path):
        raise KernelError("could not write STL file")


def write_brep(shape: TopoDS_Shape, path: str) -> None:
    if not BRepTools.Write_s(shape, path):
        raise KernelError("could not write BREP file")


IMPORT_FILTER = (
    "CAD files (*.step *.stp *.iges *.igs *.brep);;"
    "STEP (*.step *.stp);;IGES (*.iges *.igs);;BREP (*.brep);;All files (*)"
)
EXPORT_FILTER = (
    "STEP (*.step);;STL (*.stl);;IGES (*.iges);;BREP (*.brep);;All files (*)"
)
