"""Reading and writing geometry: STEP, IGES, STL, BREP."""

from __future__ import annotations

import os
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


def read_shape(path: str) -> TopoDS_Shape:
    ext = _ext(path)
    if not os.path.exists(path):
        raise KernelError("file not found: %s" % path)
    if ext in (".step", ".stp"):
        return read_step(path)
    if ext in (".iges", ".igs"):
        return read_iges(path)
    if ext in (".brep", ".brp"):
        return read_brep(path)
    raise KernelError("unsupported import format: %s" % ext)


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
