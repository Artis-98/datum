"""The 3D viewport: an OpenCASCADE V3d view hosted in a native Qt widget.

Everything drawn on screen - the solid, sketch geometry, dimensions, origin
planes - is an AIS object in this view.  The widget is a native window with
``WA_PaintOnScreen`` set, so Qt cannot paint over it; overlays are built from
OCCT presentations on the top Z-layers instead.
"""

from __future__ import annotations

import ctypes
import math
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

from OCP.AIS import (
    AIS_DisplayMode, AIS_InteractiveContext, AIS_InteractiveObject, AIS_Line,
    AIS_Plane, AIS_Point, AIS_SelectionScheme, AIS_Shape, AIS_TextLabel,
    AIS_ViewCube,
)
from OCP.Aspect import (
    Aspect_GradientFillMethod, Aspect_GridDrawMode, Aspect_GridType,
    Aspect_TypeOfLine, Aspect_TypeOfMarker, Aspect_TypeOfTriedronPosition,
)
from OCP.Aspect import Aspect_DisplayConnection
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace,
    BRepBuilderAPI_MakePolygon, BRepBuilderAPI_MakeVertex,
)
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCone
from OCP.Geom import Geom_CartesianPoint, Geom_Line, Geom_Plane
from OCP.Graphic3d import (
    Graphic3d_Camera, Graphic3d_ClipPlane, Graphic3d_MaterialAspect,
    Graphic3d_NameOfMaterial, Graphic3d_RenderingParams,
    Graphic3d_TypeOfShadingModel, Graphic3d_ZLayerId_Default,
    Graphic3d_ZLayerId_Top, Graphic3d_ZLayerId_TopOSD,
)
from OCP.OpenGl import OpenGl_GraphicDriver
from OCP.Prs3d import (
    Prs3d_Drawer, Prs3d_LineAspect, Prs3d_PointAspect, Prs3d_TextAspect,
    Prs3d_TypeOfHighlight,
)
from OCP.Quantity import Quantity_Color, Quantity_NOC_BLACK, Quantity_TOC_RGB
from OCP.TCollection import TCollection_ExtendedString
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_SHAPE, TopAbs_VERTEX
from OCP.TopoDS import TopoDS, TopoDS_Shape
from OCP.V3d import (
    V3d_AmbientLight, V3d_DirectionalLight, V3d_TypeOfOrientation, V3d_View,
    V3d_Viewer,
)
from OCP.WNT import WNT_Window
from OCP.gp import gp_Ax2, gp_Ax3, gp_Dir, gp_Lin, gp_Pln, gp_Pnt, gp_Vec

from ..core import kernel
from ..core.sketch import Sketch, SketchPlane
from .theme import C

# OCCT selection mode indices for AIS_Shape
SEL_SHAPE, SEL_VERTEX, SEL_EDGE, SEL_WIRE, SEL_FACE, SEL_SHELL, SEL_SOLID = range(7)

SELECTION_MODES = {
    "none": (),
    "solid": (SEL_SHAPE,),
    "face": (SEL_FACE,),
    "edge": (SEL_EDGE,),
    "vertex": (SEL_VERTEX,),
    # Assembly constraints are picked off faces *and* edges without the user
    # first saying which; OCCT happily has both modes live at once and
    # resolves it by what is nearest the cursor.
    "assembly": (SEL_FACE, SEL_EDGE),
}

VIEW_DIRECTIONS = {
    "iso": V3d_TypeOfOrientation.V3d_XposYnegZpos,
    "front": V3d_TypeOfOrientation.V3d_Yneg,
    "back": V3d_TypeOfOrientation.V3d_Ypos,
    "left": V3d_TypeOfOrientation.V3d_Xneg,
    "right": V3d_TypeOfOrientation.V3d_Xpos,
    "top": V3d_TypeOfOrientation.V3d_Zpos,
    "bottom": V3d_TypeOfOrientation.V3d_Zneg,
}

_PyCapsule_New = ctypes.pythonapi.PyCapsule_New
_PyCapsule_New.restype = ctypes.py_object
_PyCapsule_New.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p]


def _capsule(handle) -> Any:
    """Wrap a Win32 HWND from Qt into the PyCapsule that OCCT expects."""
    return _PyCapsule_New(ctypes.c_void_p(int(handle)), None, None)


def _vec3(point) -> Tuple[float, float, float]:
    return (point.X(), point.Y(), point.Z())


def _lerp3(a, b, t: float) -> Tuple[float, float, float]:
    return (a[0] + (b[0] - a[0]) * t,
            a[1] + (b[1] - a[1]) * t,
            a[2] + (b[2] - a[2]) * t)


def _col(value) -> Quantity_Color:
    if isinstance(value, str):
        c = QtGui.QColor(value)
        return Quantity_Color(c.redF(), c.greenF(), c.blueF(), Quantity_TOC_RGB)
    return Quantity_Color(value[0], value[1], value[2], Quantity_TOC_RGB)


class Viewport(QtWidgets.QWidget):
    """Interactive 3D view."""

    selection_changed = QtCore.Signal()
    plane_point_clicked = QtCore.Signal(float, float, object)   # u, v, modifiers
    plane_point_moved = QtCore.Signal(float, float, object)
    plane_double_clicked = QtCore.Signal(float, float)
    plane_key_pressed = QtCore.Signal(int, str)
    plane_drag_started = QtCore.Signal(float, float)
    plane_drag_moved = QtCore.Signal(float, float)
    plane_drag_finished = QtCore.Signal()
    escape_pressed = QtCore.Signal()
    delete_pressed = QtCore.Signal()
    ready = QtCore.Signal()
    plane_tool_pressed = QtCore.Signal()
    # a model edge picked while a sketch is open, for Project Geometry
    model_edge_picked = QtCore.Signal()
    plane_picker_dismissed = QtCore.Signal()
    axis_drag_moved = QtCore.Signal(float)
    axis_drag_finished = QtCore.Signal(float)
    # dragging a component about in an assembly
    component_drag_started = QtCore.Signal(int)             # occurrence id
    component_drag_moved = QtCore.Signal(object)            # 3 world floats
    component_drag_finished = QtCore.Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WA_PaintOnScreen, True)
        self.setAttribute(QtCore.Qt.WA_NoSystemBackground, True)
        self.setAttribute(QtCore.Qt.WA_NativeWindow, True)
        self.setAttribute(QtCore.Qt.WA_OpaquePaintEvent, True)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setMinimumSize(320, 240)

        self._ready = False
        self.viewer: Optional[V3d_Viewer] = None
        self.context: Optional[AIS_InteractiveContext] = None
        self.view: Optional[V3d_View] = None
        self._window = None
        self._cube: Optional[AIS_ViewCube] = None

        self._model_ais: Optional[AIS_Shape] = None
        # assembly occurrences and CAM parts, each its own selectable body
        self._component_ais: Dict[int, AIS_Shape] = {}
        # the ones that are outlines rather than solids, so the display mode
        # does not shade them into invisibility
        self._component_wires: set = set()
        self._origin_ais: List[AIS_InteractiveObject] = []
        self._overlay: List[AIS_InteractiveObject] = []
        self._preview: List[AIS_InteractiveObject] = []
        self._plane_picker: Dict[str, AIS_Shape] = {}
        self._plane_labels: List[AIS_InteractiveObject] = []
        self._plane_display: Dict[str, AIS_Shape] = {}
        self._plane_display_labels: List[AIS_InteractiveObject] = []
        self._profile_display: List[Tuple[int, AIS_Shape]] = []
        self._clip: Optional[Graphic3d_ClipPlane] = None

        self.display_mode = "shaded_edges"
        # faces by default, so hovering the model highlights the individual
        # flat faces you can start a sketch on rather than the whole body
        self.selection_mode = "face"
        self.show_origin = True
        self.grid_visible = False

        # sketch interaction
        self.sketch_plane: Optional[SketchPlane] = None
        self.plane_mode = False
        # While a sketch is open the left button belongs to the sketch, so
        # model geometry is not selectable.  Project Geometry needs it back
        # for as long as it is running, and only for as long as that.
        self.edge_picking = False

        self._last_pos = QtCore.QPoint()
        self._press_pos = QtCore.QPoint()
        self._button = QtCore.Qt.NoButton
        self._navigating = False
        self._dragging_plane = False
        self._drag_armed = False
        # Whether a press-and-move in the sketch means anything.  Only the
        # select tool drags things - a drawing tool wants every press to be
        # a click, however much the hand is moving at the time.
        self.plane_drag_allowed = True

        # pulling a work plane off a face
        self.plane_tool = False
        self._axis_active = False
        self._axis_origin = (0.0, 0.0, 0.0)
        self._axis_dir = (0.0, 0.0, 1.0)

        # shoving a component around an assembly: "move" slides it in the
        # plane of the screen, "rotate" spins it about the screen axes
        self.component_tool: Optional[str] = None
        self._component_drag: Optional[int] = None
        # where the last right-click landed, so the menu can ask what is
        # there after the selection has been rebuilt out from under it
        self._menu_pos: Optional[QtCore.QPoint] = None
        # dragging a box over empty space to select several components, the
        # same two directions a sketch uses
        # only an assembly wants this; a part has one body and
        # nothing to rubber-band over
        self.box_select_enabled = False
        self._sel_box_from: Optional[QtCore.QPoint] = None
        self._sel_band = None
        # Set by the assembly controller: given an occurrence id, says
        # whether that component is free to be pushed around with no tool
        # armed.  The viewport has no idea what is constrained, and the
        # controller has no idea what is under the cursor, so one asks the
        # other.
        self.component_freely_movable = None
        self._free_drag = False
        self._component_basis: Tuple[Tuple[float, float, float], ...] = ()

        self._animation: Optional[QtCore.QTimer] = None
        self._animation_step = 0
        self._animation_end: Optional[Tuple] = None

    # ------------------------------------------------------------------ Qt

    def paintEngine(self):
        return None

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        if not self._ready:
            self._init_viewer()
        # The OCCT window is sized from the widget at creation time, but the
        # layout is often still settling then - which left the view drawn
        # small in the corner with black margins until the first manual
        # resize. Re-sync once the event loop has caught up.
        for delay in (0, 30, 150):
            QtCore.QTimer.singleShot(delay, self.sync_size)

    def sync_size(self) -> None:
        """Make the OCCT view match the widget exactly."""
        if not self._ready or self.view is None:
            return
        try:
            self.view.MustBeResized()
            self.view.Invalidate()
        except Exception:
            pass
        self.redraw()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        if self._ready and self.view is not None:
            self.view.Redraw()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self.sync_size()

    # ------------------------------------------------------------- set-up

    def _init_viewer(self) -> None:
        self._display_connection = Aspect_DisplayConnection()
        self._driver = OpenGl_GraphicDriver(self._display_connection)

        self.viewer = V3d_Viewer(self._driver)
        self._setup_lights()

        self.context = AIS_InteractiveContext(self.viewer)
        self.context.SetAutoActivateSelection(True)
        self.context.SetDisplayMode(1, False)

        self._style_highlights()

        self.view = self.viewer.CreateView()
        self._window = WNT_Window(_capsule(self.winId()))
        self.view.SetWindow(self._window)
        if not self._window.IsMapped():
            self._window.Map()

        self.view.SetBgGradientColors(_col(C.bg_top), _col(C.bg_bottom),
                                      Aspect_GradientFillMethod.
                                      Aspect_GradientFillMethod_Vertical, True)
        self.view.SetShadingModel(
            Graphic3d_TypeOfShadingModel.Graphic3d_TypeOfShadingModel_Phong)

        try:
            params = self.view.ChangeRenderingParams()
            params.NbMsaaSamples = 8
            params.IsAntialiasingEnabled = True
        except Exception:
            pass

        self.view.TriedronDisplay(
            Aspect_TypeOfTriedronPosition.Aspect_TOTP_LEFT_LOWER,
            _col("#c8d0da"), 0.09)

        self._make_view_cube()
        self.set_projection(orthographic=True)
        self.set_view("iso")

        self.viewer.SetRectangularGridValues(0.0, 0.0, 10.0, 10.0, 0.0)
        self.viewer.SetRectangularGridGraphicValues(150.0, 150.0, 0.0)

        self._ready = True
        self.build_origin_geometry()
        self.view.MustBeResized()
        self.ready.emit()

    def _setup_lights(self) -> None:
        """A three-point rig instead of OCCT's single headlight.

        The default headlight points straight down the view axis, so any
        face square-on to the camera washes out to white - which is exactly
        what a front or top view is.  Two angled keys plus a fill keep every
        orientation readable.
        """
        try:
            self.viewer.SetLightOff()
            for light in (
                V3d_AmbientLight(_col((0.34, 0.35, 0.38))),
                V3d_DirectionalLight(gp_Dir(-0.35, -0.55, -0.75),
                                     _col((0.62, 0.63, 0.66)), True),
                V3d_DirectionalLight(gp_Dir(0.7, -0.3, -0.4),
                                     _col((0.32, 0.33, 0.36)), True),
                V3d_DirectionalLight(gp_Dir(0.1, 0.8, 0.35),
                                     _col((0.22, 0.23, 0.26)), True),
            ):
                self.viewer.AddLight(light)
                self.viewer.SetLightOn(light)
        except Exception:
            self.viewer.SetDefaultLights()
            self.viewer.SetLightOn()

    def _style_highlights(self) -> None:
        """Recolour OCCT's built-in highlight styles rather than replacing them.

        Building a fresh Prs3d_Drawer here would drop the aspects OCCT relies
        on, so the existing style objects are edited in place.
        """
        ctx = self.context
        assert ctx is not None

        wanted = {
            Prs3d_TypeOfHighlight.Prs3d_TypeOfHighlight_Selected: C.highlight,
            Prs3d_TypeOfHighlight.Prs3d_TypeOfHighlight_LocalSelected: C.highlight,
            Prs3d_TypeOfHighlight.Prs3d_TypeOfHighlight_Dynamic: C.preselect,
            Prs3d_TypeOfHighlight.Prs3d_TypeOfHighlight_LocalDynamic: C.preselect,
        }
        for kind, colour in wanted.items():
            try:
                style = ctx.HighlightStyle(kind)
                style.SetColor(_col(colour))
                style.SetDisplayMode(1)
                style.SetZLayer(Graphic3d_ZLayerId_Top)
            except Exception:
                pass

    def _make_view_cube(self) -> None:
        try:
            cube = AIS_ViewCube()
            # bigger, with a dark box behind near-white lettering - the stock
            # mid-grey cube makes FRONT / BACK / RIGHT almost unreadable
            cube.SetSize(64.0)
            cube.SetBoxColor(_col("#3a434f"))
            cube.SetTextColor(_col("#ffffff"))
            cube.SetFontHeight(12.0)
            cube.SetTransparency(0.0)
            cube.SetDrawAxes(True)
            cube.SetZLayer(Graphic3d_ZLayerId_TopOSD)
            try:
                from OCP.Graphic3d import (
                    Graphic3d_TransformPers, Graphic3d_TransModeFlags,
                    Graphic3d_Vec2i,
                )
                cube.SetTransformPersistence(Graphic3d_TransformPers(
                    Graphic3d_TransModeFlags.Graphic3d_TMF_TriedronPers,
                    Aspect_TypeOfTriedronPosition.Aspect_TOTP_RIGHT_UPPER,
                    Graphic3d_Vec2i(85, 85)))
            except Exception:
                pass
            self.context.Display(cube, False)
            self._cube = cube
        except Exception:
            self._cube = None

    # --------------------------------------------------------- model display

    def apply_appearance(self, ais, appearance=None) -> None:
        """Dress a body in an appearance.

        Roughness and metallic are the two knobs worth having.  OCCT's
        fixed-function shading does not take them directly, so they are
        turned into the ambient, specular and shininess it does take: a
        metal keeps a tight bright highlight and picks up its own colour in
        the ambient, while a matte plastic spreads the highlight out until
        it disappears.  Without that, every material looks like the same
        grey plastic in a different colour.
        """
        if appearance is None:
            colour, rough, metallic, opacity = C.material, 0.45, False, 1.0
        else:
            colour = appearance.colour
            rough = appearance.roughness
            metallic = appearance.metallic
            opacity = appearance.opacity

        base = _col(colour)
        material = Graphic3d_MaterialAspect(
            Graphic3d_NameOfMaterial.Graphic3d_NOM_PLASTER)

        # shininess runs the other way from roughness, and the useful range
        # is the bottom half: past about 0.6 everything reads as a mirror
        shine = max(0.02, min(0.9, (1.0 - rough) ** 2 * 0.85))
        if metallic:
            # a metal's highlight is its own colour, not white, which is
            # what stops steel looking like painted plastic
            specular = QtGui.QColor(colour).darker(140)
            material.SetSpecularColor(_col(specular.name()))
            material.SetAmbientColor(_col(QtGui.QColor(colour)
                                          .darker(320).name()))
        else:
            material.SetSpecularColor(_col((0.09, 0.10, 0.12)))
            material.SetAmbientColor(_col((0.26, 0.28, 0.31)))
        material.SetDiffuseColor(base)
        material.SetShininess(shine)

        ais.SetMaterial(material)
        ais.SetColor(base)
        if opacity < 0.999:
            ais.SetTransparency(1.0 - opacity)
        else:
            ais.SetTransparency(0.0)

    def set_shape(self, shape: Optional[TopoDS_Shape],
                  keep_camera: bool = True, appearance=None) -> None:
        """Show the document body, preserving selection-free camera state."""
        if not self._ready:
            return
        ctx = self.context
        assert ctx is not None

        if self._model_ais is not None:
            ctx.Remove(self._model_ais, False)
            self._model_ais = None

        if shape is not None and not shape.IsNull():
            ais = AIS_Shape(shape)
            self.apply_appearance(ais, appearance)

            drawer = ais.Attributes()
            drawer.SetFaceBoundaryDraw(True)
            boundary = Prs3d_LineAspect(_col(C.material_edge),
                                        Aspect_TypeOfLine.Aspect_TOL_SOLID, 1.4)
            drawer.SetFaceBoundaryAspect(boundary)
            drawer.SetLineAspect(boundary)
            drawer.SetWireAspect(boundary)
            drawer.SetIsoOnTriangulation(False)
            drawer.SetDeviationCoefficient(2.0e-4)
            drawer.SetDeviationAngle(0.14)

            ctx.Display(ais, False)
            self._model_ais = ais
            self._apply_display_mode()
            self._apply_selection_mode()

        if not keep_camera:
            self.fit_all()
        self.redraw()

    @property
    def model_ais(self) -> Optional[AIS_Shape]:
        return self._model_ais

    # -- assembly components -----------------------------------------------

    # Components are shown one AIS object per occurrence rather than as a
    # single compound, because an assembly needs to know *which* part was
    # clicked - for selecting it, for dragging it, and for working out which
    # body a picked face belongs to.

    COMPONENT_COLOURS = (
        (0.612, 0.667, 0.729),      # the part material, for the first one
        (0.733, 0.639, 0.514),
        (0.549, 0.678, 0.639),
        (0.694, 0.584, 0.647),
        (0.588, 0.639, 0.745),
        (0.749, 0.702, 0.541),
        (0.573, 0.710, 0.573),
    )

    @classmethod
    def component_colour(cls, index: int):
        """A stable tint per occurrence, so parts read apart in the view."""
        return cls.COMPONENT_COLOURS[index % len(cls.COMPONENT_COLOURS)]

    def set_components(self, items: Sequence[Dict[str, Any]],
                       keep_camera: bool = True) -> None:
        """Show a whole assembly.

        ``items`` are dicts of ``id``, ``shape`` and optionally ``colour``,
        ``transparency`` and ``highlight``.
        """
        self.clear_components()
        if not self._ready:
            return
        ctx = self.context

        for item in items:
            shape = item.get("shape")
            if shape is None or shape.IsNull():
                continue
            ais = AIS_Shape(shape)
            colour = item.get("colour") or C.material

            if item.get("wire"):
                # a CAM sheet's parts are outlines, not solids: shading a
                # wire draws nothing, so these stay in wireframe and get
                # their weight from the line aspect instead
                tint = _col(C.accent if item.get("highlight") else colour)
                ais.SetColor(tint)
                ais.SetWidth(2.4 if item.get("highlight") else 1.6)
                drawer = ais.Attributes()
                aspect = Prs3d_LineAspect(
                    tint, Aspect_TypeOfLine.Aspect_TOL_SOLID,
                    2.4 if item.get("highlight") else 1.6)
                drawer.SetLineAspect(aspect)
                drawer.SetWireAspect(aspect)
                drawer.SetSeenLineAspect(aspect)
                ctx.Display(ais, False)
                ctx.SetDisplayMode(ais, 0, False)
                self._component_ais[int(item["id"])] = ais
                self._component_wires.add(int(item["id"]))
                continue

            # the same dressing a single part gets, so a component looks
            # the same in the assembly as it does on its own
            self.apply_appearance(ais, item.get("appearance"))
            if item.get("appearance") is None and item.get("colour"):
                ais.SetColor(_col(colour))
            transparency = float(item.get("transparency", 0.0))
            if transparency:
                ais.SetTransparency(transparency)

            drawer = ais.Attributes()
            drawer.SetFaceBoundaryDraw(self.display_mode != "shaded")
            edge_colour = _col(C.accent if item.get("highlight")
                               else C.material_edge)
            boundary = Prs3d_LineAspect(
                edge_colour, Aspect_TypeOfLine.Aspect_TOL_SOLID,
                2.4 if item.get("highlight") else 1.4)
            drawer.SetFaceBoundaryAspect(boundary)
            drawer.SetLineAspect(boundary)
            drawer.SetWireAspect(boundary)
            drawer.SetIsoOnTriangulation(False)
            drawer.SetDeviationCoefficient(2.0e-4)
            drawer.SetDeviationAngle(0.14)

            ctx.Display(ais, False)
            ctx.SetDisplayMode(ais, 0 if self.display_mode == "wireframe"
                               else 1, False)
            self._component_ais[int(item["id"])] = ais

        self._apply_selection_mode()
        if not keep_camera:
            self.fit_all()
        self.redraw()

    def clear_components(self) -> None:
        if not self._ready:
            return
        for ais in self._component_ais.values():
            self.context.Remove(ais, False)
        self._component_ais = {}
        self._component_wires = set()

    @property
    def has_components(self) -> bool:
        return bool(self._component_ais)

    def _occurrence_of(self, obj) -> Optional[int]:
        for occurrence_id, ais in self._component_ais.items():
            if obj is ais:
                return occurrence_id
            try:
                if obj.IsEqual(ais):
                    return occurrence_id
            except Exception:
                continue
        return None

    def selected_components(self) -> List[int]:
        """Which occurrences the user has selected, in click order."""
        if not self._ready or not self._component_ais:
            return []
        ctx = self.context
        found: List[int] = []
        ctx.InitSelected()
        while ctx.MoreSelected():
            try:
                occurrence_id = self._occurrence_of(ctx.SelectedInteractive())
            except Exception:
                occurrence_id = None
            if occurrence_id is not None and occurrence_id not in found:
                found.append(occurrence_id)
            ctx.NextSelected()
        return found

    def select_components(self, occurrence_ids) -> None:
        """Put the selection back on these components.

        Showing an assembly rebuilds every AIS object, which throws the
        selection away.  Anything that reads the selection straight after
        a redraw - a context menu, a box select, the Delete key - then
        finds nothing.  Restoring it here means callers do not each have
        to work around the same thing.
        """
        if not self._ready or not self._component_ais:
            return
        ctx = self.context
        wanted = [self._component_ais.get(int(i)) for i in occurrence_ids]
        wanted = [ais for ais in wanted if ais is not None]
        if not wanted:
            return
        for ais in wanted:
            try:
                ctx.AddOrRemoveSelected(ais, False)
            except Exception:
                pass

    def selected_component_shapes(self) -> List[Tuple[int, TopoDS_Shape]]:
        """(occurrence, picked sub-shape) for everything currently selected.

        The sub-shape comes back located where it is drawn, which is what
        makes it possible to strip the placement off and recover the face as
        the part itself stores it.
        """
        if not self._ready or not self._component_ais:
            return []
        ctx = self.context
        out: List[Tuple[int, TopoDS_Shape]] = []
        ctx.InitSelected()
        while ctx.MoreSelected():
            try:
                occurrence_id = self._occurrence_of(ctx.SelectedInteractive())
                shape = ctx.SelectedShape()
            except Exception:
                occurrence_id, shape = None, None
            if (occurrence_id is not None and shape is not None
                    and not shape.IsNull()):
                out.append((occurrence_id, shape))
            ctx.NextSelected()
        return out

    # -- box selection ------------------------------------------------------

    # Dragged left to right, the box takes only what is completely inside
    # it; dragged right to left, it takes anything it touches.  That is the
    # convention every CAD package shares, and the two colours are what
    # tell you which one you are doing before you let go.
    BOX_WINDOW = "#4ea3f0"      # left to right: fully enclosed
    BOX_CROSSING = "#4ee08a"    # right to left: anything touched

    def _draw_selection_box(self, start: QtCore.QPoint,
                            now: QtCore.QPoint) -> None:
        from OCP.AIS import AIS_RubberBand
        from OCP.Graphic3d import Graphic3d_Vec2i
        from OCP.Quantity import Quantity_Color, Quantity_TOC_RGB

        crossing = now.x() < start.x()
        colour = QtGui.QColor(self.BOX_CROSSING if crossing
                              else self.BOX_WINDOW)
        line = Quantity_Color(colour.redF(), colour.greenF(), colour.blueF(),
                              Quantity_TOC_RGB)

        if self._sel_band is None:
            band = AIS_RubberBand(line, Aspect_TypeOfLine.Aspect_TOL_SOLID,
                                  line, 0.18, 1.6)
            band.SetZLayer(Graphic3d_ZLayerId_TopOSD)
            band.SetTransformPersistence(None)
            self._sel_band = band
            self.context.Display(band, False)
        else:
            self._sel_band.SetLineColor(line)
            self._sel_band.SetFillColor(line)

        height = self.height()
        # the rubber band counts from the bottom, the mouse from the top
        self._sel_band.SetRectangle(min(start.x(), now.x()),
                                    height - max(start.y(), now.y()),
                                    max(start.x(), now.x()),
                                    height - min(start.y(), now.y()))
        self.context.Redisplay(self._sel_band, False)
        self.view.RedrawImmediate()

    def _clear_selection_box(self) -> None:
        if self._sel_band is not None:
            try:
                self.context.Remove(self._sel_band, False)
            except Exception:
                pass
            self._sel_band = None
            self.view.Redraw()

    def select_in_box(self, start: QtCore.QPoint, end: QtCore.QPoint) -> None:
        """Select everything the dragged box asks for."""
        from OCP.Graphic3d import Graphic3d_Vec2i

        crossing = end.x() < start.x()
        selector = self.context.MainSelector()
        try:
            selector.AllowOverlapDetection(bool(crossing))
        except Exception:
            pass
        try:
            self.context.SelectRectangle(
                Graphic3d_Vec2i(min(start.x(), end.x()),
                                min(start.y(), end.y())),
                Graphic3d_Vec2i(max(start.x(), end.x()),
                                max(start.y(), end.y())),
                self.view)
        finally:
            try:
                selector.AllowOverlapDetection(False)
            except Exception:
                pass
        self.view.Redraw()
        self.selection_changed.emit()

    def menu_component(self) -> Optional[int]:
        """The component the last right-click was over.

        Asked for by position rather than read from the selection, because
        right-clicking a component selects it, and selecting one makes the
        assembly redraw, and redrawing rebuilds every AIS object and so
        throws the selection away.  By the time the menu is built there is
        nothing selected to ask about.  The cursor has not moved, though.
        """
        if self._menu_pos is None:
            return None
        return self.component_under(self._menu_pos.x(), self._menu_pos.y())

    def component_under(self, x: int, y: int) -> Optional[int]:
        """Which occurrence is under a screen point, without selecting it."""
        if not self._ready or not self._component_ais:
            return None
        self.context.MoveTo(int(x), int(y), self.view, False)
        if not self.context.HasDetected():
            return None
        try:
            return self._occurrence_of(self.context.DetectedInteractive())
        except Exception:
            return None

    def screen_basis(self) -> Tuple[Tuple[float, float, float], ...]:
        """The camera's right and up vectors, in world coordinates."""
        if not self._ready:
            return ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
        camera = self.view.Camera()
        up = camera.Up()
        direction = camera.Direction()
        forward = (direction.X(), direction.Y(), direction.Z())
        upv = (up.X(), up.Y(), up.Z())
        right = (forward[1] * upv[2] - forward[2] * upv[1],
                 forward[2] * upv[0] - forward[0] * upv[2],
                 forward[0] * upv[1] - forward[1] * upv[0])
        length = math.sqrt(sum(c * c for c in right)) or 1.0
        right = tuple(c / length for c in right)
        return (right, upv, forward)

    def _apply_display_mode(self) -> None:
        ctx = self.context
        mode = 0 if self.display_mode == "wireframe" else 1
        for ais in self._solids():
            ctx.SetDisplayMode(ais, mode, False)
            ais.Attributes().SetFaceBoundaryDraw(self.display_mode != "shaded")
            ais.SetTransparency(0.55 if self.display_mode == "xray" else 0.0)

    def _bodies(self) -> List[AIS_Shape]:
        """Everything pickable on screen - the part, or the components."""
        bodies = [] if self._model_ais is None else [self._model_ais]
        return bodies + list(self._component_ais.values())

    def _solids(self) -> List[AIS_Shape]:
        """Just the ones a shaded display mode means anything for."""
        bodies = [] if self._model_ais is None else [self._model_ais]
        return bodies + [ais for key, ais in self._component_ais.items()
                         if key not in self._component_wires]

    def set_display_mode(self, mode: str) -> None:
        self.display_mode = mode
        self._apply_display_mode()
        self.redraw()

    # ------------------------------------------------------------ selection

    def set_selection_mode(self, mode: str) -> None:
        self.selection_mode = mode
        self._apply_selection_mode()

    def _apply_selection_mode(self) -> None:
        if not self._ready:
            return
        ctx = self.context
        modes = SELECTION_MODES.get(self.selection_mode, ())
        for ais in self._bodies():
            ctx.Deactivate(ais)
            for index in modes:
                ctx.Activate(ais, index, True)

    def clear_selection(self) -> None:
        if self._ready:
            self.context.ClearSelected(True)
            self.selection_changed.emit()

    def selected_shapes(self) -> List[TopoDS_Shape]:
        """Model geometry the user has selected - never our own overlays."""
        if not self._ready:
            return []
        ctx = self.context
        out: List[TopoDS_Shape] = []
        ctx.InitSelected()
        while ctx.MoreSelected():
            try:
                if not self._is_helper(ctx.SelectedInteractive()):
                    out.append(ctx.SelectedShape())
            except Exception:
                pass
            ctx.NextSelected()
        return out

    def selected_faces(self) -> List[Any]:
        return [TopoDS.Face_s(s) for s in self.selected_shapes()
                if s.ShapeType() == TopAbs_FACE]

    def selected_edges(self) -> List[Any]:
        return [TopoDS.Edge_s(s) for s in self.selected_shapes()
                if s.ShapeType() == TopAbs_EDGE]

    # ---------------------------------------------------------- camera work

    def set_view(self, name: str) -> None:
        if not self._ready:
            return
        orient = VIEW_DIRECTIONS.get(name)
        if orient is None:
            return
        # an explicit view command wins over a move already under way
        self.finish_animation()
        self.view.SetProj(orient)
        try:
            self.view.SetTwist(0.0)
        except Exception:
            pass
        self.fit_all()

    def set_projection(self, orthographic: bool) -> None:
        if not self._ready:
            return
        cam = self.view.Camera()
        cam.SetProjectionType(
            Graphic3d_Camera.Projection_Orthographic if orthographic
            else Graphic3d_Camera.Projection_Perspective)
        self.redraw()

    def is_orthographic(self) -> bool:
        if not self._ready:
            return True
        return (self.view.Camera().ProjectionType()
                == Graphic3d_Camera.Projection_Orthographic)

    def fit_all(self) -> None:
        if not self._ready:
            return
        self.finish_animation()
        self.view.FitAll(0.06, False)
        try:
            self.view.ZFitAll()
        except Exception:
            pass
        self.redraw()

    def look_at_plane(self, plane: SketchPlane, fit: bool = True,
                      default_span: float = 180.0,
                      animate: bool = False) -> None:
        """Orient the camera square-on to a sketch plane."""
        if not self._ready:
            return

        def apply() -> None:
            n = plane.normal
            self.view.SetProj(n[0], n[1], n[2])
            y = plane.ydir
            try:
                self.view.SetUp(y[0], y[1], y[2])
            except Exception:
                pass
            if fit and self._model_ais is not None:
                self.fit_all()
            else:
                # nothing to fit to yet, so frame a sensible working area
                self.set_span(default_span)
            self.redraw()

        if animate:
            self.animate_to(apply)
        else:
            apply()

    def look_normal_to(self, plane: SketchPlane, animate: bool = True) -> None:
        """Face the camera straight at a plane, keeping the current zoom."""
        if not self._ready:
            return

        def apply() -> None:
            n = plane.normal
            self.view.SetProj(n[0], n[1], n[2])
            y = plane.ydir
            try:
                self.view.SetUp(y[0], y[1], y[2])
            except Exception:
                pass
            self.redraw()

        if animate:
            self.animate_to(apply)
        else:
            apply()

    # -- camera animation ---------------------------------------------------

    def animate_to(self, apply: Callable[[], None],
                   duration_ms: int = 320) -> None:
        """Ease the camera from where it is to wherever ``apply`` puts it.

        The eye, target and scale are interpolated directly rather than going
        through OCCT's animation classes, which keeps this independent of the
        camera's internal state machine.
        """
        camera = self.view.Camera()
        try:
            start = (_vec3(camera.Eye()), _vec3(camera.Center()),
                     _vec3(camera.Up()), float(camera.Scale()))
        except Exception:
            apply()
            return

        apply()

        try:
            end = (_vec3(camera.Eye()), _vec3(camera.Center()),
                   _vec3(camera.Up()), float(camera.Scale()))
        except Exception:
            return

        if all(abs(a - b) < 1e-9
               for pair in zip(start[:3], end[:3])
               for a, b in zip(pair[0], pair[1])) \
                and abs(start[3] - end[3]) < 1e-9:
            return                       # nothing actually moved

        self.finish_animation()

        if not self.isVisible() or duration_ms <= 0:
            return                       # nothing to animate against

        steps = max(2, int(duration_ms / 16))
        self._animation_step = 0
        self._animation_end = end

        def tick() -> None:
            self._animation_step += 1
            t = self._animation_step / steps
            if t >= 1.0:
                self.finish_animation()
                return
            # smoothstep, so the move settles instead of stopping dead
            e = t * t * (3.0 - 2.0 * t)
            self._set_camera(
                _lerp3(start[0], end[0], e),
                _lerp3(start[1], end[1], e),
                _lerp3(start[2], end[2], e),
                start[3] + (end[3] - start[3]) * e,
            )
            self.redraw()

        self._set_camera(*start)
        self.redraw()

        self._animation = QtCore.QTimer(self)
        self._animation.setInterval(16)
        self._animation.timeout.connect(tick)
        self._animation.start()

    def finish_animation(self) -> None:
        """Stop any camera move, leaving the camera where it was heading."""
        if self._animation is None:
            return
        self._animation.stop()
        self._animation = None
        if self._animation_end is not None:
            self._set_camera(*self._animation_end)
            self._animation_end = None
            self.refresh_grid()
            self.redraw()

    def _set_camera(self, eye, centre, up, scale: float) -> None:
        try:
            camera = self.view.Camera()
            camera.SetUp(gp_Dir(*up))
            camera.SetEyeAndCenter(gp_Pnt(*eye), gp_Pnt(*centre))
            camera.SetScale(scale)
        except Exception:
            pass

    def camera_state(self) -> Optional[Tuple]:
        """Everything needed to put this view back exactly as it is.

        Each open document keeps its own, so switching tabs returns you to
        the view you left rather than resetting to the home orientation.
        """
        if not self._ready:
            return None
        try:
            camera = self.view.Camera()
            eye, centre, up = camera.Eye(), camera.Center(), camera.Up()
            return ((eye.X(), eye.Y(), eye.Z()),
                    (centre.X(), centre.Y(), centre.Z()),
                    (up.X(), up.Y(), up.Z()),
                    camera.Scale(), self.is_orthographic())
        except Exception:
            return None

    def restore_camera(self, state: Optional[Tuple]) -> bool:
        if not self._ready or not state:
            return False
        self.finish_animation()
        eye, centre, up, scale, orthographic = state
        self.set_projection(bool(orthographic))
        self._set_camera(eye, centre, up, scale)
        try:
            self.view.ZFitAll()
        except Exception:
            pass
        self.refresh_grid()
        self.redraw()
        return True

    def set_span(self, millimetres: float) -> None:
        """Frame roughly ``millimetres`` across the viewport."""
        if not self._ready:
            return
        try:
            cam = self.view.Camera()
            cam.SetCenter(gp_Pnt(0, 0, 0))
            self.view.SetSize(float(millimetres))
        except Exception:
            pass

    def redraw(self) -> None:
        if self._ready and self.view is not None:
            self.view.Redraw()

    # ------------------------------------------------------------- overlays

    def build_origin_geometry(self) -> None:
        """Origin point, three axes and three planes, like Inventor's Origin."""
        if not self._ready:
            return
        ctx = self.context
        for obj in self._origin_ais:
            ctx.Remove(obj, False)
        self._origin_ais = []

        if not self.show_origin:
            self.redraw()
            return

        size = 32.0
        axes = (
            ((1, 0, 0), "#d1544f"),
            ((0, 1, 0), "#6fbf5f"),
            ((0, 0, 1), "#4f8fd1"),
        )
        for vector, colour in axes:
            edge = BRepBuilderAPI_MakeEdge(
                gp_Pnt(-vector[0] * size, -vector[1] * size, -vector[2] * size),
                gp_Pnt(vector[0] * size, vector[1] * size, vector[2] * size),
            ).Edge()
            ais = AIS_Shape(edge)
            ais.SetColor(_col(colour))
            ais.SetWidth(1.3)
            ais.SetInfiniteState(True)
            ctx.Display(ais, False)
            ctx.Deactivate(ais)
            self._origin_ais.append(ais)

        origin = AIS_Point(Geom_CartesianPoint(gp_Pnt(0, 0, 0)))
        origin.SetColor(_col("#e8ecef"))
        origin.Attributes().SetPointAspect(Prs3d_PointAspect(
            Aspect_TypeOfMarker.Aspect_TOM_O_PLUS, _col("#e8ecef"), 1.4))
        origin.SetInfiniteState(True)
        ctx.Display(origin, False)
        ctx.Deactivate(origin)
        self._origin_ais.append(origin)

        self.redraw()

    def set_show_origin(self, visible: bool) -> None:
        self.show_origin = visible
        self.build_origin_geometry()

    # -- origin plane picker ------------------------------------------------

    @staticmethod
    def plane_face(plane: SketchPlane, size: float,
                   centre: Tuple[float, float] = (0.0, 0.0)):
        """A square patch of ``plane``, used for previews and picking.

        ``centre`` is in sketch coordinates: a plane derived from a model face
        has its origin wherever the underlying surface is anchored, which is
        rarely the middle of the face, so the drawn square would otherwise sit
        off to one side.
        """
        cu, cv = centre
        corners = [plane.to_3d(cu - size, cv - size),
                   plane.to_3d(cu + size, cv - size),
                   plane.to_3d(cu + size, cv + size),
                   plane.to_3d(cu - size, cv + size)]
        builder = BRepBuilderAPI_MakePolygon()
        for c in corners:
            builder.Add(gp_Pnt(*c))
        builder.Close()
        if not builder.IsDone():
            return None
        maker = BRepBuilderAPI_MakeFace(builder.Wire())
        return maker.Face() if maker.IsDone() else None

    def draw_plane(self, plane: SketchPlane, size: float, colour: str,
                   preview: bool = False, transparency: float = 0.72,
                   centre: Tuple[float, float] = (0.0, 0.0)
                   ) -> Optional[AIS_Shape]:
        face = self.plane_face(plane, size, centre)
        if face is None:
            return None
        ais = AIS_Shape(face)
        tint = _col(colour)
        ais.SetColor(tint)
        ais.SetTransparency(transparency)
        ais.SetDisplayMode(1)
        drawer = ais.Attributes()
        drawer.SetFaceBoundaryDraw(True)
        drawer.SetFaceBoundaryAspect(Prs3d_LineAspect(
            tint, Aspect_TypeOfLine.Aspect_TOL_SOLID, 2.2))
        return self._add(ais, preview)

    # -- planes shown on the model all the time -----------------------------

    PLANE_COLOURS = {"XY": "#4f8fd1", "XZ": "#6fbf5f", "YZ": "#d1544f"}

    def show_planes(self, planes: Dict[str, SketchPlane],
                    size: float = 45.0) -> None:
        """Display datum and work planes as translucent, pickable sheets.

        They behave like faces: hovering highlights them and right-clicking
        one offers to sketch on it.  Feature dialogs filter them back out, so
        a plane can never be mistaken for model geometry.
        """
        self.hide_planes()
        if not self._ready:
            return

        for key, plane in planes.items():
            colour = self.PLANE_COLOURS.get(key, "#c8a24f")
            ais = self.draw_plane(plane, size, colour, transparency=0.86)
            if ais is None:
                continue
            if ais in self._overlay:
                self._overlay.remove(ais)
            ais.SetInfiniteState(False)
            self.context.Activate(ais, SEL_SHAPE, True)
            self._plane_display[key] = ais

            label = plane.to_3d(size * 0.66, size * 0.82)
            suffix = " Plane" if key in self.PLANE_COLOURS else ""
            text = self.draw_text("%s%s" % (key, suffix), label, colour, 13.0)
            if text in self._overlay:
                self._overlay.remove(text)
            self._plane_display_labels.append(text)

        self.redraw()

    def hide_planes(self) -> None:
        if not self._ready:
            return
        for ais in self._plane_display.values():
            self.context.Remove(ais, False)
        for label in self._plane_display_labels:
            self.context.Remove(label, False)
        self._plane_display = {}
        self._plane_display_labels = []

    def show_plane_picker(self, planes: Dict[str, SketchPlane],
                          size: float = 45.0) -> None:
        """Put translucent origin planes on screen so one can be clicked.

        This is what an empty part offers when you ask for a new sketch -
        there is no model face to pick yet, so the datum planes stand in.
        """
        self.hide_plane_picker()
        if not self._ready:
            return

        # the picker replaces the always-on sheets while it is up, so the two
        # sets cannot overlap and fight for the same clicks
        self._display_before_picker = dict(self._plane_display)
        self.hide_planes()

        colours = self.PLANE_COLOURS
        for key, plane in planes.items():
            colour = colours.get(key, "#c8a24f")
            ais = self.draw_plane(plane, size, colour)
            if ais is None:
                continue
            # picker planes are clickable, unlike ordinary overlay geometry
            if ais in self._overlay:
                self._overlay.remove(ais)
            ais.SetInfiniteState(False)
            self.context.Activate(ais, SEL_SHAPE, True)
            self._plane_picker[key] = ais

            label = plane.to_3d(size * 0.62, size * 0.78)
            suffix = " Plane" if key in colours else ""
            text = self.draw_text("%s%s" % (key, suffix), label, colour, 15.0)
            if text in self._overlay:
                self._overlay.remove(text)
            self._plane_labels.append(text)

        self.redraw()

    def hide_plane_picker(self) -> None:
        if not self._ready:
            return
        for ais in self._plane_picker.values():
            self.context.Remove(ais, False)
        for label in self._plane_labels:
            self.context.Remove(label, False)
        self._plane_picker = {}
        self._plane_labels = []
        self.plane_picker_dismissed.emit()
        self.redraw()

    @property
    def plane_picker_active(self) -> bool:
        return bool(self._plane_picker)

    def picked_plane(self) -> Optional[str]:
        """Which plane, if any, the user just clicked.

        Looks at the modal picker first and then the always-on sheets, so the
        same call works whether a plane was being asked for or simply clicked.
        """
        if not self._ready:
            return None
        pools = (self._plane_picker, self._plane_display)
        ctx = self.context
        ctx.InitSelected()
        while ctx.MoreSelected():
            shape = None
            try:
                shape = ctx.SelectedShape()
            except Exception:
                shape = None
            if shape is not None and not shape.IsNull():
                # Matching is done on the underlying face, not on the AIS
                # object: OCCT hands back a fresh Python wrapper for the same
                # C++ handle every call, so comparing wrappers finds nothing
                # and every plane looks unselectable.
                for pool in pools:
                    for key, ais in pool.items():
                        try:
                            if ais.Shape().IsSame(shape):
                                return key
                        except Exception:
                            continue
                centre = kernel.shape_centre(shape)
                for pool in pools:
                    for key, ais in pool.items():
                        try:
                            if math.dist(centre,
                                         kernel.shape_centre(ais.Shape())) < 1e-6:
                                return key
                        except Exception:
                            continue
            ctx.NextSelected()
        return None

    def _is_helper(self, obj) -> bool:
        """True for our own overlay objects rather than model geometry."""
        helpers = list(self._plane_picker.values())
        helpers += list(self._plane_display.values())
        helpers += [ais for _i, ais in self._profile_display]
        for ais in helpers:
            if obj is ais:
                return True
            try:
                if obj.IsEqual(ais):
                    return True
            except Exception:
                continue
        return False

    # -- profile picking ----------------------------------------------------

    def show_profiles(self, regions: List[Dict[str, Any]],
                      chosen: Optional[List[int]] = None) -> None:
        """Put every closed sketch region on screen as a clickable patch."""
        self.hide_profiles()
        if not self._ready:
            return

        chosen = set(chosen or [])
        for index, region in enumerate(regions):
            face = region.get("face")
            if face is None:
                continue
            # green for chosen, blue for available - deliberately not the
            # highlight yellow, which OCCT already uses for "under the cursor"
            picked = index in chosen
            ais = AIS_Shape(face)
            colour = _col(C.ok if picked else C.sketch_preview)
            ais.SetColor(colour)
            ais.SetTransparency(0.3 if picked else 0.72)
            ais.SetDisplayMode(1)
            drawer = ais.Attributes()
            drawer.SetFaceBoundaryDraw(True)
            drawer.SetFaceBoundaryAspect(Prs3d_LineAspect(
                colour, Aspect_TypeOfLine.Aspect_TOL_SOLID,
                2.6 if picked else 1.8))
            ais.SetZLayer(Graphic3d_ZLayerId_Top)
            self.context.Display(ais, False)
            self.context.Activate(ais, SEL_SHAPE, True)
            self._profile_display.append((index, ais))
        self.redraw()

    def hide_profiles(self) -> None:
        if not self._ready:
            return
        for _index, ais in self._profile_display:
            self.context.Remove(ais, False)
        self._profile_display = []

    @property
    def profile_picking(self) -> bool:
        return bool(self._profile_display)

    def picked_profiles(self) -> List[int]:
        """Indices of every region currently selected.

        Matching is done on the underlying face, not on the AIS object:
        OCCT hands back a fresh Python wrapper for the same C++ handle each
        time, so identity comparison between wrappers cannot be relied on.
        """
        if not self._ready or not self._profile_display:
            return []

        ctx = self.context
        found: List[int] = []
        ctx.InitSelected()
        while ctx.MoreSelected():
            shape = None
            try:
                shape = ctx.SelectedShape()
            except Exception:
                shape = None
            if shape is not None and not shape.IsNull():
                for index, ais in self._profile_display:
                    if index in found:
                        continue
                    try:
                        if ais.Shape().IsSame(shape):
                            found.append(index)
                            break
                    except Exception:
                        continue
                else:
                    # geometry fallback, for when the picked shape has been
                    # copied rather than shared
                    centre = kernel.shape_centre(shape)
                    for index, ais in self._profile_display:
                        if index in found:
                            continue
                        try:
                            if math.dist(centre,
                                         kernel.shape_centre(ais.Shape())) < 1e-6:
                                found.append(index)
                                break
                        except Exception:
                            continue
            ctx.NextSelected()
        return found

    def picked_profile(self) -> Optional[int]:
        picked = self.picked_profiles()
        return picked[0] if picked else None

    def clear_overlay(self) -> None:
        if not self._ready:
            return
        for obj in self._overlay:
            self.context.Remove(obj, False)
        self._overlay = []

    def clear_preview(self) -> None:
        if not self._ready:
            return
        for obj in self._preview:
            self.context.Remove(obj, False)
        self._preview = []

    def _add(self, ais: AIS_InteractiveObject, preview: bool,
             layer=Graphic3d_ZLayerId_Top,
             fittable: bool = False) -> AIS_InteractiveObject:
        if self.context is None:
            # The viewer is only built when the widget is first shown, and
            # something can ask to draw before that - a sketch reopened on a
            # tab that is not in front yet.  There is nothing to draw on and
            # the next render will put it there, so say so rather than dying.
            return ais
        ais.SetZLayer(layer)
        # Overlay geometry is normally marked infinite so Fit All ignores it -
        # dimension text and grid lines should not decide the zoom.  A CAM
        # sheet's stock outline is the exception: it is the thing you want in
        # view, so it asks to be counted.
        ais.SetInfiniteState(not fittable)
        self.context.Display(ais, False)
        self.context.Deactivate(ais)
        (self._preview if preview else self._overlay).append(ais)
        return ais

    def draw_edge(self, p1: Sequence[float], p2: Sequence[float],
                  colour: str, width: float = 1.8, preview: bool = False,
                  dashed: bool = False) -> Optional[AIS_InteractiveObject]:
        if math.dist(p1, p2) < 1e-9:
            return None
        try:
            edge = BRepBuilderAPI_MakeEdge(gp_Pnt(*p1), gp_Pnt(*p2)).Edge()
        except Exception:
            return None
        ais = AIS_Shape(edge)
        ais.SetColor(_col(colour))
        ais.SetWidth(width)
        if dashed:
            ais.Attributes().SetLineAspect(Prs3d_LineAspect(
                _col(colour), Aspect_TypeOfLine.Aspect_TOL_DASH, width))
            ais.Attributes().SetWireAspect(Prs3d_LineAspect(
                _col(colour), Aspect_TypeOfLine.Aspect_TOL_DASH, width))
        return self._add(ais, preview)

    def draw_arrow(self, origin: Sequence[float], direction: Sequence[float],
                   colour: str, length: float = 0.0, preview: bool = True,
                   width: float = 3.0) -> List[AIS_InteractiveObject]:
        """A shaft and a solid head, pointing out of a face.

        The default length is a fraction of what is on screen rather than a
        fixed number of millimetres, because the same arrow has to read on a
        12 mm panel and a 3 m frame.
        """
        norm = math.sqrt(sum(float(c) * float(c) for c in direction))
        if norm < 1e-9:
            return []
        unit = [float(c) / norm for c in direction]
        if length <= 0.0:
            length = max(2.0, self.view_span() * 0.11)

        head = length * 0.34
        radius = head * 0.42
        tip = [origin[i] + unit[i] * length for i in range(3)]
        neck = [origin[i] + unit[i] * (length - head) for i in range(3)]

        out: List[AIS_InteractiveObject] = []
        shaft = self.draw_edge(origin, neck, colour, width, preview)
        if shaft is not None:
            out.append(shaft)
        try:
            cone = BRepPrimAPI_MakeCone(
                gp_Ax2(gp_Pnt(*neck), gp_Dir(*unit)), radius, 0.0, head)
            cone.Build()
            ais = AIS_Shape(cone.Shape())
            ais.SetColor(_col(colour))
            ais.SetWidth(1.0)
            # solid, or the head reads as a wireframe cone rather than as
            # the point of an arrow
            ais.SetDisplayMode(int(AIS_DisplayMode.AIS_Shaded))
            out.append(self._add(ais, preview))
        except Exception:
            # without a head it is still a line pointing the right way, which
            # is most of the information
            pass
        # a dot at the tail so the face the arrow belongs to is unambiguous
        out.append(self.draw_point(origin, colour, 5.0, preview))
        return out

    def draw_shape(self, shape: TopoDS_Shape, colour: str, width: float = 1.8,
                   preview: bool = False, dashed: bool = False,
                   fittable: bool = False) -> AIS_InteractiveObject:
        ais = AIS_Shape(shape)
        ais.SetColor(_col(colour))
        ais.SetWidth(width)
        if dashed:
            aspect = Prs3d_LineAspect(_col(colour),
                                      Aspect_TypeOfLine.Aspect_TOL_DASH, width)
            ais.Attributes().SetLineAspect(aspect)
            ais.Attributes().SetWireAspect(aspect)
        return self._add(ais, preview, fittable=fittable)

    def draw_point(self, p: Sequence[float], colour: str, size: float = 3.0,
                   preview: bool = False,
                   marker=Aspect_TypeOfMarker.Aspect_TOM_O) -> AIS_InteractiveObject:
        ais = AIS_Point(Geom_CartesianPoint(gp_Pnt(*p)))
        ais.SetColor(_col(colour))
        ais.Attributes().SetPointAspect(Prs3d_PointAspect(marker, _col(colour),
                                                          size))
        return self._add(ais, preview)

    def draw_text(self, text: str, position: Sequence[float], colour: str,
                  height: float = 14.0,
                  preview: bool = False) -> AIS_InteractiveObject:
        label = AIS_TextLabel()
        # isMultiByte=True so the UTF-8 bytes of characters like "ø" survive
        label.SetText(TCollection_ExtendedString(str(text), True))
        label.SetPosition(gp_Pnt(*position))
        label.SetColor(_col(colour))
        label.SetHeight(height)
        try:
            label.SetZoomable(False)
        except Exception:
            pass
        return self._add(label, preview, Graphic3d_ZLayerId_TopOSD)

    # --------------------------------------------------------- sketch plane

    def over_cube(self, x: int, y: int) -> bool:
        """Is the ViewCube under this point?

        Sketching takes the mouse over completely - every press is a point on
        the plane - so the cube has to be asked about explicitly or it goes
        dead exactly when you most want to spin the model round.
        """
        if not self._ready or self._cube is None:
            return False
        try:
            self.context.MoveTo(int(x), int(y), self.view, False)
            if not self.context.HasDetected():
                return False
            return isinstance(self.context.DetectedInteractive(), AIS_ViewCube)
        except Exception:
            return False

    def cube_click(self, x: int, y: int) -> bool:
        """Give the ViewCube a click, and say whether it took it."""
        if not self.over_cube(x, y):
            return False
        try:
            self.context.SelectDetected()
            self.context.ClearSelected(False)
            self.view.Redraw()
        except Exception:
            return False
        return True

    def set_edge_picking(self, on: bool) -> None:
        """Let model edges be picked even though a sketch is open."""
        self.edge_picking = bool(on)
        self.set_selection_mode("edge" if on else "none")
        if not on:
            self.clear_selection()
        self.setCursor(QtCore.Qt.CrossCursor if on else QtCore.Qt.ArrowCursor)

    def enter_plane_mode(self, plane: SketchPlane, grid_step: float = 5.0,
                         show_grid: bool = False) -> None:
        self.sketch_plane = plane
        self.plane_mode = True
        # the grid is off by default - snapping does not need it drawn
        self.set_grid(show_grid, plane, grid_step)
        if not show_grid:
            self.grid_step = grid_step
        self.set_selection_mode("none")

    def leave_plane_mode(self) -> None:
        self.plane_mode = False
        self.edge_picking = False
        self.sketch_plane = None
        self.plane_drag_allowed = True
        self.set_grid(False)
        self.clear_overlay()
        self.clear_preview()
        # back to face picking, the resting mode - not the whole body
        self.set_selection_mode("face")
        self.redraw()

    def set_grid(self, visible: bool, plane: Optional[SketchPlane] = None,
                 step: float = 5.0) -> None:
        if not self._ready:
            return
        self.grid_visible = visible
        if visible:
            plane = plane or self.sketch_plane
            if plane is not None:
                ax = gp_Ax3(gp_Pnt(*plane.origin), gp_Dir(*plane.normal),
                            gp_Dir(*plane.xdir))
                self.viewer.SetPrivilegedPlane(ax)
            # keep the grid readable: roughly 30 divisions across the view,
            # rounded to a 1/2/5 step so the numbers stay familiar
            span = max(20.0, self.view_span())
            raw = span / 30.0
            magnitude = 10.0 ** math.floor(math.log10(raw))
            for nice in (1.0, 2.0, 5.0, 10.0):
                if raw <= nice * magnitude:
                    step = nice * magnitude
                    break
            self.grid_step = step
            self.viewer.SetRectangularGridValues(0.0, 0.0, step, step, 0.0)
            self.viewer.SetRectangularGridGraphicValues(span * 1.5, span * 1.5,
                                                        0.0)
            self.viewer.ActivateGrid(Aspect_GridType.Aspect_GT_Rectangular,
                                     Aspect_GridDrawMode.Aspect_GDM_Lines)
        else:
            self.viewer.DeactivateGrid()
        self.redraw()

    def refresh_grid(self) -> None:
        """Re-space the grid after the zoom level changed."""
        if self.grid_visible:
            self.set_grid(True, self.sketch_plane)

    # -- dragging along an axis (work planes pulled off a face) -------------

    def begin_axis_drag(self, origin: Sequence[float],
                        direction: Sequence[float]) -> None:
        """Start reporting how far the cursor is along a 3D axis."""
        self._axis_origin = tuple(float(c) for c in origin)
        length = math.sqrt(sum(c * c for c in direction)) or 1.0
        self._axis_dir = tuple(float(c) / length for c in direction)
        self._axis_active = True

    def end_axis_drag(self) -> None:
        self._axis_active = False

    @property
    def axis_dragging(self) -> bool:
        return self._axis_active

    def axis_distance(self, x: int, y: int) -> Optional[float]:
        """Where the cursor ray comes closest to the drag axis, as a distance.

        This is the classic closest-approach between two skew lines; the
        parameter along the axis is what the user is really choosing when
        they pull a plane off a face.
        """
        if not self._axis_active:
            return None
        ray = self.ray_at(x, y)
        if ray is None:
            return None
        eye, view_dir = ray

        d = self._axis_dir
        w0 = tuple(self._axis_origin[i] - eye[i] for i in range(3))
        a = sum(c * c for c in d)
        b = sum(d[i] * view_dir[i] for i in range(3))
        c_ = sum(c * c for c in view_dir)
        dd = sum(d[i] * w0[i] for i in range(3))
        ee = sum(view_dir[i] * w0[i] for i in range(3))

        denominator = a * c_ - b * b
        if abs(denominator) < 1e-12:
            return None                     # looking straight down the axis
        return (b * ee - c_ * dd) / denominator

    # -- dragging a component around an assembly ----------------------------

    def begin_component_tool(self, mode: Optional[str]) -> None:
        """Arm free move or free rotate; ``None`` disarms both."""
        self.component_tool = mode
        self._component_drag = None
        self.setCursor(QtCore.Qt.SizeAllCursor if mode
                       else QtCore.Qt.ArrowCursor)

    @property
    def component_dragging(self) -> Optional[int]:
        return self._component_drag

    def _drag_component(self, dx: int, dy: int) -> None:
        """Turn a mouse movement into a world-space move or turn.

        Both work in the plane of the screen, which is the only thing that
        makes free dragging feel predictable: the part follows the cursor,
        whatever angle the model happens to be at.
        """
        if not self._component_basis:
            return
        right, up, forward = self._component_basis

        if self.component_tool == "rotate":
            # sideways drag spins about the screen's up axis, vertical drag
            # about the screen's right axis - the same mapping as an orbit
            speed = 0.006
            turn = tuple(up[i] * (dx * speed) + right[i] * (dy * speed)
                         for i in range(3))
            self.component_drag_moved.emit(turn)
            return

        scale = self.pixel_scale()
        delta = tuple(right[i] * (dx * scale) - up[i] * (dy * scale)
                      for i in range(3))
        self.component_drag_moved.emit(delta)

    # -- geometry from screen coordinates -----------------------------------

    def ray_at(self, x: int, y: int) -> Optional[Tuple[Tuple[float, float, float],
                                                       Tuple[float, float, float]]]:
        if not self._ready:
            return None
        px, py, pz, vx, vy, vz = self.view.ConvertWithProj(int(x), int(y))
        return (px, py, pz), (vx, vy, vz)

    def plane_point(self, x: int, y: int,
                    plane: Optional[SketchPlane] = None
                    ) -> Optional[Tuple[float, float]]:
        """Where the cursor ray meets a sketch plane, in sketch coordinates."""
        plane = plane or self.sketch_plane
        if plane is None:
            return None
        ray = self.ray_at(x, y)
        if ray is None:
            return None
        origin, direction = ray
        n = plane.normal
        denom = sum(direction[i] * n[i] for i in range(3))
        if abs(denom) < 1e-12:
            return None
        diff = tuple(plane.origin[i] - origin[i] for i in range(3))
        t = sum(diff[i] * n[i] for i in range(3)) / denom
        hit = tuple(origin[i] + direction[i] * t for i in range(3))
        return plane.to_2d(hit)

    def project(self, p: Sequence[float]) -> Tuple[int, int]:
        """World point to widget pixel."""
        if not self._ready:
            return (0, 0)
        try:
            return self.view.Convert(float(p[0]), float(p[1]), float(p[2]))
        except Exception:
            return (0, 0)

    def view_span(self) -> float:
        """Width of the visible area in model units."""
        if not self._ready:
            return 100.0
        try:
            return abs(float(self.view.Camera().ViewDimensions().X()))
        except Exception:
            return max(1.0, self.pixel_scale() * max(1, self.width()))

    def pixel_scale(self) -> float:
        """Model units per screen pixel - used to size handles and snap radii."""
        if not self._ready:
            return 1.0
        try:
            a = self.view.Convert(0, 0)
            b = self.view.Convert(100, 0)
            return abs(b[0] - a[0]) / 100.0 or 1.0
        except Exception:
            return 1.0

    # ---------------------------------------------------------- 6DOF input

    def apply_spacemouse(self, tx: float, ty: float, tz: float,
                         rx: float, ry: float, rz: float,
                         pan_speed: float = 1.0, zoom_speed: float = 1.0,
                         rotate_speed: float = 1.0) -> None:
        """Drive the camera from one frame of SpaceMouse motion.

        Axes follow the 3Dconnexion object-mode convention: slide the puck to
        pan, pull or push it to zoom, tilt or twist it to orbit.  Everything
        is scaled by the current view size so the response feels the same on a
        5 mm part and a 500 mm one.
        """
        if not self._ready:
            return

        span = self.view_span() or 100.0
        moved = False

        # pan - the puck's X/Z slide
        pan_x = tx * pan_speed
        pan_y = tz * pan_speed
        if abs(pan_x) > 1e-6 or abs(pan_y) > 1e-6:
            self.view.Panning(-pan_x * span * 0.035, -pan_y * span * 0.035,
                              1.0, True)
            moved = True

        # zoom - push/pull
        if abs(ty) > 1e-6:
            factor = 1.0 - ty * zoom_speed * 0.06
            if factor > 0.05:
                self.view.SetZoom(factor, True)
                moved = True

        # orbit - tilt and twist, about the middle of the model
        if max(abs(rx), abs(ry), abs(rz)) > 1e-6:
            centre = self._orbit_centre()
            self.view.Rotate(
                -rx * rotate_speed * 0.055,
                -rz * rotate_speed * 0.055,
                -ry * rotate_speed * 0.055,
                centre[0], centre[1], centre[2], True,
            )
            moved = True

        if moved:
            try:
                self.view.ZFitAll()
            except Exception:
                pass
            self.refresh_grid()
            self.redraw()

    def _orbit_centre(self) -> Tuple[float, float, float]:
        """Rotate about the model, falling back to the view target."""
        if self._model_ais is not None:
            try:
                xmin, ymin, zmin, xmax, ymax, zmax = kernel.bounding_box(
                    self._model_ais.Shape())
                return ((xmin + xmax) / 2.0, (ymin + ymax) / 2.0,
                        (zmin + zmax) / 2.0)
            except Exception:
                pass
        try:
            return self.view.At()
        except Exception:
            return (0.0, 0.0, 0.0)

    # ------------------------------------------------------------- sections

    def set_section(self, enabled: bool, plane: Optional[SketchPlane] = None,
                    offset: float = 0.0) -> None:
        if not self._ready:
            return
        if self._clip is not None:
            self.view.RemoveClipPlane(self._clip)
            self._clip = None
        if enabled:
            plane = plane or SketchPlane((0, 0, 0), (0, 1, 0), (1, 0, 0))
            origin = tuple(plane.origin[i] + plane.normal[i] * offset
                           for i in range(3))
            clip = Graphic3d_ClipPlane(
                gp_Pln(gp_Pnt(*origin), gp_Dir(*plane.normal)))
            clip.SetCapping(True)
            clip.SetCappingColor(_col("#8fa0b4"))
            clip.SetUseObjectMaterial(False)
            self.view.AddClipPlane(clip)
            self._clip = clip
        self.redraw()

    # ---------------------------------------------------------------- input

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if not self._ready:
            return
        self.setFocus()
        self.finish_animation()      # touching the mouse takes over the camera
        pos = event.position().toPoint()
        self._last_pos = pos
        self._press_pos = pos
        self._button = event.button()
        mods = event.modifiers()

        if event.button() == QtCore.Qt.MiddleButton:
            self._navigating = True
            if mods & QtCore.Qt.ShiftModifier:
                self.view.StartRotation(pos.x(), pos.y())
        elif event.button() == QtCore.Qt.RightButton:
            self._navigating = True
            self.view.StartRotation(pos.x(), pos.y())
        elif event.button() == QtCore.Qt.LeftButton:
            if self.plane_mode:
                if self.cube_click(pos.x(), pos.y()):
                    return
                if self.edge_picking:
                    # Project Geometry is running: this click is choosing a
                    # model edge, not drawing on the plane
                    self.context.MoveTo(pos.x(), pos.y(), self.view, False)
                    self.context.SelectDetected()
                    self.view.Redraw()
                    self.model_edge_picked.emit()
                    return
                self._drag_armed = True
            elif self.component_tool:
                # find out what was grabbed before anything moves, so the
                # drag knows which component it is pushing around
                occurrence = self.component_under(pos.x(), pos.y())
                if occurrence is not None:
                    self._free_drag = False
                    self._component_drag = occurrence
                    self._component_basis = self.screen_basis()
                    self.component_drag_started.emit(occurrence)
            elif self.plane_tool:
                # resolve what is under the cursor straight away, so the drag
                # that follows can start from the picked face
                self.context.MoveTo(pos.x(), pos.y(), self.view, False)
                self.context.SelectDetected()
                self.view.Redraw()
                self.plane_tool_pressed.emit()
            else:
                # No tool armed.  A component that nothing is holding can
                # still be dragged straight off the screen, because having
                # to arm a tool to shove a loose part out of the way is a
                # step that earns nothing.  Anything constrained or grounded
                # is left alone here and needs Free Move, which says out
                # loud that the constraints are being ignored.
                grabbed = None
                if self.component_freely_movable is not None:
                    candidate = self.component_under(pos.x(), pos.y())
                    if (candidate is not None
                            and self.component_freely_movable(candidate)):
                        grabbed = candidate
                if grabbed is not None:
                    self._free_drag = True
                    self._component_drag = grabbed
                    self._component_basis = self.screen_basis()
                    self.component_drag_started.emit(grabbed)
                elif self.box_select_enabled and self.component_under(
                        pos.x(), pos.y()) is None:
                    # nothing under the cursor, so this drag is a box
                    self._sel_box_from = QtCore.QPoint(pos)
                else:
                    self.context.MoveTo(pos.x(), pos.y(), self.view, False)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if not self._ready:
            return
        pos = event.position().toPoint()
        dx = pos.x() - self._last_pos.x()
        dy = pos.y() - self._last_pos.y()
        mods = event.modifiers()

        if self._navigating:
            if self._button == QtCore.Qt.RightButton or (
                    self._button == QtCore.Qt.MiddleButton
                    and mods & QtCore.Qt.ShiftModifier):
                self.view.Rotation(pos.x(), pos.y())
            else:
                self.view.Pan(dx, -dy)
            self._last_pos = pos
            return

        if self._sel_box_from is not None:
            self._draw_selection_box(self._sel_box_from, pos)
            self._last_pos = pos
            return

        if self._component_drag is not None:
            if not self._moved_enough(pos):
                # a click that has not really moved is still a click, so
                # selecting a loose part does not nudge it
                return
            self._drag_component(dx, dy)
            self._last_pos = pos
            return

        if self._axis_active:
            distance = self.axis_distance(pos.x(), pos.y())
            if distance is not None:
                self.axis_drag_moved.emit(distance)
            self._last_pos = pos
            return

        if self.plane_mode:
            if not self._dragging_plane and not self._drag_armed:
                # selection is off while sketching, so the only thing this
                # can pre-highlight is the cube
                self.context.MoveTo(pos.x(), pos.y(), self.view, True)
            uv = self.plane_point(pos.x(), pos.y())
            if uv is not None:
                if (self._drag_armed and not self._dragging_plane
                        and self.plane_drag_allowed):
                    if self._moved_enough(pos):
                        self._dragging_plane = True
                        start = self.plane_point(self._press_pos.x(),
                                                 self._press_pos.y())
                        if start is not None:
                            self.plane_drag_started.emit(start[0], start[1])
                if self._dragging_plane:
                    self.plane_drag_moved.emit(uv[0], uv[1])
                else:
                    self.plane_point_moved.emit(uv[0], uv[1], mods)
            self._last_pos = pos
            return

        self.context.MoveTo(pos.x(), pos.y(), self.view, True)
        self._last_pos = pos

    def _moved_enough(self, pos: QtCore.QPoint) -> bool:
        """Whether a press has travelled far enough to count as a drag.

        Three pixels is not far enough to be sure of anything: a hand that
        is already moving covers that between the press and the release of
        an ordinary click, and the click is then thrown away as a drag.  The
        platform's own drag distance is the number every other application
        uses, so it is what people's hands are already calibrated to.
        """
        slack = max(4, QtWidgets.QApplication.startDragDistance())
        return (abs(pos.x() - self._press_pos.x()) > slack
                or abs(pos.y() - self._press_pos.y()) > slack)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if not self._ready:
            return
        pos = event.position().toPoint()
        moved = self._moved_enough(pos)

        if event.button() in (QtCore.Qt.MiddleButton, QtCore.Qt.RightButton):
            self._navigating = False
            if event.button() == QtCore.Qt.RightButton and not moved:
                # select whatever is under the cursor first, so the menu can
                # offer actions for that face rather than generic ones
                self._menu_pos = QtCore.QPoint(pos)
                if not self.plane_mode:
                    self.context.MoveTo(pos.x(), pos.y(), self.view, False)
                    self.context.SelectDetected()
                    self.view.Redraw()
                    self.selection_changed.emit()
                self._context_menu(pos)
            self._button = QtCore.Qt.NoButton
            return

        if (event.button() == QtCore.Qt.LeftButton
                and self._sel_box_from is not None):
            start = self._sel_box_from
            self._sel_box_from = None
            self._clear_selection_box()
            if abs(pos.x() - start.x()) > 3 or abs(pos.y() - start.y()) > 3:
                self.select_in_box(start, pos)
            else:
                # a click, not a drag: treat it as one
                self.context.MoveTo(pos.x(), pos.y(), self.view, False)
                self.context.SelectDetected()
                self.view.Redraw()
                self.selection_changed.emit()
            self._button = QtCore.Qt.NoButton
            return

        if (event.button() == QtCore.Qt.LeftButton
                and self._component_drag is not None):
            self._component_drag = None
            if self._free_drag and not moved:
                # it never actually moved, so treat it as the selection
                # click it was
                self._free_drag = False
                self.context.MoveTo(pos.x(), pos.y(), self.view, False)
                self.context.SelectDetected()
                self.view.Redraw()
                self.selection_changed.emit()
                self._button = QtCore.Qt.NoButton
                return
            self._free_drag = False
            self.component_drag_finished.emit()
            self._button = QtCore.Qt.NoButton
            return

        if event.button() == QtCore.Qt.LeftButton and self._axis_active:
            distance = self.axis_distance(pos.x(), pos.y()) or 0.0
            self._axis_active = False
            self.axis_drag_finished.emit(distance)
            self._button = QtCore.Qt.NoButton
            return

        if event.button() == QtCore.Qt.LeftButton:
            if self.plane_mode:
                if self._dragging_plane:
                    self.plane_drag_finished.emit()
                elif not moved or not self.plane_drag_allowed:
                    # a drawing tool has no drag to have been doing instead,
                    # so the press was a click whatever distance it covered
                    uv = self.plane_point(pos.x(), pos.y())
                    if uv is not None:
                        self.plane_point_clicked.emit(uv[0], uv[1],
                                                      event.modifiers())
                self._dragging_plane = False
                self._drag_armed = False
            elif not moved:
                scheme = (AIS_SelectionScheme.AIS_SelectionScheme_XOR
                          if event.modifiers() & QtCore.Qt.ControlModifier
                          else AIS_SelectionScheme.AIS_SelectionScheme_Replace)
                self.context.MoveTo(pos.x(), pos.y(), self.view, False)
                self.context.SelectDetected(scheme)
                self.view.Redraw()
                self.selection_changed.emit()
        self._button = QtCore.Qt.NoButton

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        if not self._ready:
            return
        delta = event.angleDelta().y()
        if not delta:
            return
        pos = event.position().toPoint()
        step = 60
        self.view.StartZoomAtPoint(pos.x(), pos.y())
        self.view.ZoomAtPoint(0, 0, int(step if delta > 0 else -step), 0)
        try:
            self.view.ZFitAll()
        except Exception:
            pass
        self.refresh_grid()
        self.redraw()

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent) -> None:
        if self.plane_mode and event.button() == QtCore.Qt.LeftButton:
            pos = event.position().toPoint()
            uv = self.plane_point(pos.x(), pos.y())
            if uv is not None:
                self.plane_double_clicked.emit(uv[0], uv[1])
                return
        super().mouseDoubleClickEvent(event)

    def event(self, event: QtCore.QEvent) -> bool:
        """Intercept Tab before Qt spends it on focus navigation.

        Tab moves between the heads-up dimension fields while sketching, so
        the widget has to claim it at the event level - keyPressEvent never
        sees Tab otherwise.
        """
        if (event.type() == QtCore.QEvent.KeyPress and self.plane_mode
                and event.key() in (QtCore.Qt.Key_Tab, QtCore.Qt.Key_Backtab)):
            self.plane_key_pressed.emit(QtCore.Qt.Key_Tab, "\t")
            return True
        return super().event(event)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        key = event.key()
        # while sketching, keystrokes belong to the heads-up dimension input
        if self.plane_mode and key not in (QtCore.Qt.Key_Escape,
                                           QtCore.Qt.Key_Delete):
            self.plane_key_pressed.emit(key, event.text())
            if key in (QtCore.Qt.Key_Tab, QtCore.Qt.Key_Backtab,
                       QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
                return
        if key == QtCore.Qt.Key_Escape:
            self.escape_pressed.emit()
        elif key in (QtCore.Qt.Key_Delete, QtCore.Qt.Key_Backspace):
            self.delete_pressed.emit()
        elif key == QtCore.Qt.Key_F6:
            self.set_view("iso")
        elif key == QtCore.Qt.Key_Home:
            self.fit_all()
        else:
            super().keyPressEvent(event)

    # ------------------------------------------------------------ context menu

    context_menu_requested = QtCore.Signal(QtCore.QPoint)

    def _context_menu(self, pos: QtCore.QPoint) -> None:
        self.context_menu_requested.emit(self.mapToGlobal(pos))

    # -------------------------------------------------------------- capture

    def grab_image(self, path: str) -> bool:
        """Render the current view straight to an image file."""
        if not self._ready:
            return False
        self.view.Redraw()
        return bool(self.view.Dump(path))
