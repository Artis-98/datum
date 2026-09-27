"""A STEP file opened the way Inventor opens one: as an assembly.

A STEP file from another CAD system is usually an assembly, even when it
is called a part: its products, which parts are used where and how many
times, their names and their colours are all in it. Reading it as one
solid threw that away. One Inventor export, 176 placed parts made of 65
distinct ones in eleven sub-assemblies, came in as a single body of
fifteen thousand faces, with every screw its own copy of the same screw.

Here every distinct product becomes a DATUM part and every sub-assembly
an assembly, each placement becomes a component where Inventor had it,
and names and colours come across. A part used nineteen times is stored
and meshed once. The files land in a folder of their own:

    Milk Dryer/
        Milk Dryer.adat             the top of the tree
        A2-2025-283-F01.adat        each sub-assembly
        Parts/
            P-2025-283-009.pdat     each distinct part
            P-2025-283-009.brep     and its geometry, read by the part

The geometry sits beside each part as a plain OpenCASCADE BREP file,
which the part imports by a path relative to itself, so the folder can
be moved or shared whole. A file with no product structure, or an IGES
file, becomes an assembly of one part per solid.

Placements are rigid. A mirrored placement, which STEP allows and a
DATUM placement cannot express, is baked into a mirrored copy of the
part instead.
"""

from __future__ import annotations

import math
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.BRepTools import BRepTools
from OCP.gp import gp_Trsf
from OCP.IFSelect import IFSelect_ReturnStatus
from OCP.Quantity import Quantity_Color, Quantity_TypeOfColor
from OCP.TCollection import TCollection_ExtendedString
from OCP.TDataStd import TDataStd_Name
from OCP.TDF import TDF_Label, TDF_LabelSequence
from OCP.TDocStd import TDocStd_Document
from OCP.TopAbs import TopAbs_FACE, TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS_Shape

from . import kernel

Progress = Callable[[str, int, int], None]

# the extensions a foreign file can arrive with
FOREIGN = (".step", ".stp", ".iges", ".igs", ".brep", ".brp")


class ImportError_(RuntimeError):
    """A file that could not be turned into an assembly."""


# ------------------------------------------------------------------ model


@dataclass
class Product:
    """One distinct thing in the file: a part, or an assembly of things."""

    key: int
    name: str
    shape: Optional[TopoDS_Shape] = None        # a part's own geometry
    colour: str = ""                            # "#rrggbb", or ""
    # an assembly's contents: (product, placement, instance name)
    children: List[Tuple["Product", gp_Trsf, str]] = field(
        default_factory=list)

    @property
    def is_assembly(self) -> bool:
        return self.shape is None


def _hex(colour: Quantity_Color) -> str:
    # STEP colours are sRGB; OpenCASCADE keeps them linear
    r, g, b = colour.Values(Quantity_TypeOfColor.Quantity_TOC_sRGB)
    return "#%02x%02x%02x" % tuple(
        max(0, min(255, int(round(v * 255)))) for v in (r, g, b))


def _count(shape: TopoDS_Shape, kind) -> int:
    explorer = TopExp_Explorer(shape, kind)
    n = 0
    while explorer.More():
        n += 1
        explorer.Next()
    return n


# ---------------------------------------------------------------- reading


def survey(path: str) -> Tuple[int, int]:
    """(placements, solids) a STEP file declares, without translating it.

    A glance at the text, to decide whether to offer an assembly before
    spending the seconds a real read takes.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".step", ".stp"):
        return 0, 0
    placements = solids = 0
    try:
        with open(path, "r", encoding="latin-1", errors="replace") as fh:
            for line in fh:
                if "NEXT_ASSEMBLY_USAGE_OCCURRENCE" in line:
                    placements += 1
                if "MANIFOLD_SOLID_BREP" in line or "BREP_WITH_VOIDS" in line:
                    solids += 1
    except OSError:
        pass
    return placements, solids


def read(path: str, progress: Optional[Progress] = None) -> List[Product]:
    """The top-level products of a file, with everything under them."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".step", ".stp"):
        return _read_step(path, progress)
    from . import fileio
    shape = fileio.read_shape(path)
    stem = _stem(path)
    return [_split_solids(stem, shape, 1)]


def _read_step(path: str, progress: Optional[Progress]) -> List[Product]:
    from OCP.STEPCAFControl import STEPCAFControl_Reader
    from OCP.XCAFDoc import (XCAFDoc_ColorTool, XCAFDoc_ColorType,
                             XCAFDoc_DocumentTool, XCAFDoc_ShapeTool)

    if progress:
        progress("Reading %s" % os.path.basename(path), 0, 0)
    document = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))
    reader = STEPCAFControl_Reader()
    reader.SetNameMode(True)
    reader.SetColorMode(True)
    if reader.ReadFile(path) != IFSelect_ReturnStatus.IFSelect_RetDone:
        raise ImportError_("could not read %s as STEP"
                           % os.path.basename(path))
    if not reader.Transfer(document):
        raise ImportError_("%s has nothing in it DATUM can use"
                           % os.path.basename(path))

    shapes = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
    colours = XCAFDoc_DocumentTool.ColorTool_s(document.Main())
    kinds = (XCAFDoc_ColorType.XCAFDoc_ColorSurf,
             XCAFDoc_ColorType.XCAFDoc_ColorGen)
    made: Dict[int, Product] = {}

    def name_of(label: TDF_Label, fallback: str) -> str:
        found = TDataStd_Name()
        if label.FindAttribute(TDataStd_Name.GetID_s(), found):
            text = found.Get().ToExtString().strip()
            if text:
                return text
        return fallback

    def colour_of(label: TDF_Label, shape: TopoDS_Shape) -> str:
        colour = Quantity_Color()
        for kind in kinds:
            if XCAFDoc_ColorTool.GetColor_s(label, kind, colour):
                return _hex(colour)
        # else whatever most of it is painted: sub-shapes, then faces
        tally: Counter = Counter()
        subs = TDF_LabelSequence()
        XCAFDoc_ShapeTool.GetSubShapes_s(label, subs)
        for i in range(1, subs.Length() + 1):
            for kind in kinds:
                if XCAFDoc_ColorTool.GetColor_s(subs.Value(i), kind, colour):
                    tally[_hex(colour)] += 1
                    break
        if not tally:
            explorer = TopExp_Explorer(shape, TopAbs_FACE)
            looked = 0
            while explorer.More() and looked < 400:
                for kind in kinds:
                    if colours.GetColor(explorer.Current(), kind, colour):
                        tally[_hex(colour)] += 1
                        break
                explorer.Next()
                looked += 1
        return tally.most_common(1)[0][0] if tally else ""

    def product(label: TDF_Label) -> Product:
        key = label.Tag()
        if key in made:
            return made[key]
        if XCAFDoc_ShapeTool.IsAssembly_s(label):
            node = Product(key=key, name=name_of(label, "Assembly %d" % key))
            made[key] = node
            parts = TDF_LabelSequence()
            XCAFDoc_ShapeTool.GetComponents_s(label, parts)
            for i in range(1, parts.Length() + 1):
                component = parts.Value(i)
                referred = TDF_Label()
                XCAFDoc_ShapeTool.GetReferredShape_s(component, referred)
                child = product(referred)
                where = XCAFDoc_ShapeTool.GetLocation_s(component)
                node.children.append((child, where.Transformation(),
                                      name_of(component, child.name)))
            return node
        shape = XCAFDoc_ShapeTool.GetShape_s(label)
        node = Product(key=key, name=name_of(label, "Part %d" % key),
                       shape=shape, colour=colour_of(label, shape))
        made[key] = node
        return node

    free = TDF_LabelSequence()
    shapes.GetFreeShapes(free)
    roots = [product(free.Value(i)) for i in range(1, free.Length() + 1)]
    roots = [r for r in roots if r.is_assembly or _has_faces(r.shape)]
    if not roots:
        raise ImportError_("%s has no geometry in it"
                           % os.path.basename(path))
    # a file that is one part with many solids in it is an assembly of
    # those solids, which is how anybody would want to work with it
    return [_split_solids(r.name, r.shape, r.key, r.colour)
            if not r.is_assembly else r for r in roots]


def _has_faces(shape: Optional[TopoDS_Shape]) -> bool:
    return shape is not None and not shape.IsNull() and \
        TopExp_Explorer(shape, TopAbs_FACE).More()


def _split_solids(name: str, shape: TopoDS_Shape, key: int,
                  colour: str = "") -> Product:
    solids = kernel.explore(shape, TopAbs_SOLID)
    if len(solids) <= 1:
        return Product(key=key, name=name, shape=shape, colour=colour)
    top = Product(key=key, name=name)
    width = len(str(len(solids)))
    for i, solid in enumerate(solids, 1):
        part = Product(key=-(key * 100000 + i),
                       name="%s Body %0*d" % (name, width, i),
                       shape=solid, colour=colour)
        top.children.append((part, gp_Trsf(), part.name))
    return top


def _stem(path: str) -> str:
    stem = os.path.splitext(os.path.basename(path))[0]
    # "Dryer.ipt.stp" and "Dryer.iam.step" are exports named for where
    # they came from, not what they are called
    for tail in (".ipt", ".iam", ".sldprt", ".sldasm", ".prt", ".asm"):
        if stem.lower().endswith(tail):
            return stem[:-len(tail)]
    return stem


# ---------------------------------------------------------------- writing


_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"con", "prn", "aux", "nul"} | {"com%d" % i for i in range(1, 10)} \
    | {"lpt%d" % i for i in range(1, 10)}


def file_name(name: str) -> str:
    """A product name made safe to be a file name on any system."""
    text = _FORBIDDEN.sub("_", name).strip().rstrip(".")
    if not text:
        text = "Part"
    if text.lower() in _RESERVED:
        text = "_" + text
    return text[:100]


def _is_rigid(trsf: gp_Trsf) -> bool:
    return not trsf.IsNegative() and abs(trsf.ScaleFactor() - 1.0) < 1e-9


def _placement(trsf: gp_Trsf):
    """A rigid transform as a DATUM placement: position and rotation."""
    import numpy as np
    from .constraints3d import Placement, rotation_vector

    matrix = np.array([[trsf.Value(r, c) for c in (1, 2, 3)]
                       for r in (1, 2, 3)])
    move = trsf.TranslationPart()
    return Placement([move.X(), move.Y(), move.Z()],
                     list(rotation_vector(matrix)))


def default_folder(path: str) -> str:
    """Where an import of this file goes when nobody says otherwise."""
    base = os.path.join(os.path.dirname(os.path.abspath(path)), _stem(path))
    folder, n = base, 2
    while os.path.exists(folder):
        folder = "%s (%d)" % (base, n)
        n += 1
    return folder


def import_assembly(path: str, folder: Optional[str] = None,
                    progress: Optional[Progress] = None) -> str:
    """Turn a STEP or IGES file into DATUM parts and assemblies.

    Returns the path of the assembly at the top. Everything is written
    into ``folder``, made if it is missing; by default a new folder beside
    the file, named for it.
    """
    roots = read(path, progress)
    if len(roots) == 1 and roots[0].is_assembly:
        top = roots[0]
    else:
        top = Product(key=0, name=_stem(path))
        for root in roots:
            top.children.append((root, gp_Trsf(), root.name))
    if not top.name or top.name.startswith("Assembly "):
        top.name = _stem(path)
    top.name = _strip_origin(top.name)
    return write(top, folder or default_folder(path), progress)


def write(top: Product, folder: str,
          progress: Optional[Progress] = None) -> str:
    """Write a product tree out as DATUM parts and assemblies.

    Returns the path of the assembly at the top.
    """
    from . import bodycache, fileio, mesh
    from .assembly import AssemblyDocument
    from .document import Document
    from .features import ImportFeature, NEW_BODY

    parts_dir = os.path.join(folder, "Parts")
    os.makedirs(parts_dir, exist_ok=True)

    taken: Dict[str, int] = {}

    def unique(name: str) -> str:
        base = file_name(name)
        n = taken.get(base.lower(), 0)
        taken[base.lower()] = n + 1
        return base if n == 0 else "%s (%d)" % (base, n + 1)

    written: Dict[Tuple[int, bool], str] = {}
    all_parts = _distinct_parts(top)
    done = [0]
    # Meshed together, before anything is written: one part at a time,
    # sixty small parts cannot keep twelve cores busy, and the meshing was
    # a third of the import. Each part still gets its own tolerance.
    if progress:
        progress("Meshing %d parts" % len(all_parts), 0, 0)
    mesh.mesh_all(p.shape for p in all_parts)

    def write_part(node: Product, mirrored: Optional[gp_Trsf] = None) -> str:
        key = (node.key, mirrored is not None)
        if mirrored is None and key in written:
            return written[key]
        shape = node.shape
        name = node.name
        if mirrored is not None:
            shape = BRepBuilderAPI_Transform(node.shape, mirrored,
                                             True).Shape()
            name = "%s (mirrored)" % node.name
        stem = unique(name)
        brep = os.path.join(parts_dir, stem + ".brep")
        BRepTools.Write_s(shape, brep)

        part = Document()
        part.properties["Title"] = name
        part.properties["PartNumber"] = name
        if node.colour:
            part.appearance = node.colour
        feature = ImportFeature()
        feature.name = name
        feature.path = stem + ".brep"          # beside the part, relative
        feature.operation = NEW_BODY
        feature.body_name = name
        part.add_feature(feature)
        target = os.path.join(parts_dir, stem + ".pdat")
        part.save(target)

        # Read back, meshed and kept, now rather than on first open: the
        # assembly is about to be opened, and translating was the slow part
        try:
            mesh.mesh(shape)
            bodycache.store(bodycache.key_for(brep), shape)
            fileio.remember(brep, shape)
        except Exception:
            pass
        if mirrored is None:
            written[key] = target
            done[0] += 1
            if progress:
                progress("Writing parts", done[0], len(all_parts))
        return target

    assemblies: Dict[int, str] = {}

    def write_assembly(node: Product, is_top: bool = False) -> str:
        if node.key in assemblies:
            return assemblies[node.key]
        # children first, so every file an assembly names exists
        children = []
        for child, where, label in node.children:
            if child.is_assembly:
                target = write_assembly(child)
                children.append((target, where, label))
            elif _is_rigid(where):
                children.append((write_part(child), where, label))
            else:
                children.append((write_part(child, where), gp_Trsf(), label))
        stem = file_name(node.name) if is_top else unique(node.name)
        target = os.path.join(folder, stem + ".adat")
        doc = AssemblyDocument()
        doc.path = target
        names = set()
        for part_path, where, label in children:
            occurrence = doc.place(part_path, label=label)
            # Inventor's own instance names, "Bolt:3", kept as they were:
            # they are what the drawings and the BOM back home call them
            if label and label not in names:
                occurrence.name = label
            names.add(occurrence.name)
            occurrence.placement = _placement(where)
            # placed where the file had it, and held there: nothing has
            # constrained it yet, and a drifting import helps nobody
            occurrence.grounded = True
        doc.save(target)
        assemblies[node.key] = target
        return target

    result = write_assembly(top, is_top=True)
    if progress:
        progress("Done", len(all_parts), len(all_parts))
    return result


def _strip_origin(name: str) -> str:
    for tail in (".ipt", ".iam"):
        if name.lower().endswith(tail):
            return name[:-len(tail)]
    return name


def _distinct_parts(top: Product) -> List[Product]:
    seen: Dict[int, Product] = {}

    def walk(node: Product) -> None:
        for child, _where, _label in node.children:
            if child.is_assembly:
                walk(child)
            else:
                seen.setdefault(child.key, child)
    walk(top)
    return list(seen.values())
