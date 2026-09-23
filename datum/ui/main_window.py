"""The application shell: ribbon, model browser, viewport, status bar."""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, List, Optional  # noqa: F401

from PySide6 import QtCore, QtGui, QtWidgets

from .. import APP_NAME, __version__
from ..core import fileformat, fileio, kernel
from ..core.assembly import AssemblyDocument
from ..core.cam import CamDocument
from ..core.constraints3d import (
    ANGLE, FLUSH, INSERT, KIND_HINTS, KIND_LABELS, MATE, TANGENT,
)
from ..core.document import FILE_EXTENSION, Document, RebuildReport
from ..core.features import (
    ChamferFeature, ExtrudeFeature, Feature, FilletFeature, HoleFeature,
    ImportFeature, LoftFeature, MirrorFeature, MoveFeature, PatternFeature,
    PrimitiveFeature, RevolveFeature, ShellFeature, SketchFeature,
    SweepFeature, WorkPlaneFeature, plane_from_face,
)
from ..core.naming import RefSet, ShapeRef
from ..core.sketch import STANDARD_PLANES, Sketch, SketchPlane
from . import dialogs, icons
from . import doctabs, session, updater
from .assembly_ui import AssemblyController
from .browser import ModelBrowser
from .cam_ui import CamController
from .drawing_ui import DrawingController
from .sheet_canvas import SheetCanvas
from .panels import (
    MeasureDialog, ParametersDialog, PropertiesPanel, SpaceMouseDialog,
)
from .ribbon import Ribbon
from .sketcher import SketchEditor
from .startpage import StartPage
from .spacemouse import SpaceMouse, SpaceMouseSettings
from .theme import C, stylesheet
from .viewport import Viewport

TAB_MODEL = "3D Model"
TAB_SKETCH = "Sketch"
TAB_ASSEMBLE = "Assemble"
TAB_CAM = "CAM"
TAB_DRAWING = "Drawing"
TAB_INSPECT = "Inspect"
TAB_MANAGE = "Manage"
TAB_VIEW = "View"

# which ribbon tabs belong to which kind of document
PART_TABS = (TAB_MODEL, TAB_INSPECT, TAB_MANAGE, TAB_VIEW)
ASSEMBLY_TABS = (TAB_ASSEMBLE, TAB_INSPECT, TAB_VIEW)
CAM_TABS = (TAB_CAM, TAB_INSPECT, TAB_VIEW)
DRAWING_TABS = (TAB_DRAWING,)
ALL_TABS = (TAB_MODEL, TAB_ASSEMBLE, TAB_CAM, TAB_DRAWING, TAB_INSPECT,
            TAB_MANAGE,
            TAB_VIEW)



# What sits behind the Rectangle button: the same list Inventor shows, in
# the same order, titles and all.
RECT_OPTIONS = [
    ("rect", "rect", "Rectangle", "Two Point"),
    ("rect3", "rect3", "Rectangle", "Three Point"),
    ("rect_centre", "rect_centre", "Rectangle", "Two Point Center"),
    ("rect3_centre", "rect3_centre", "Rectangle", "Three Point Center"),
    ("slot", "slot", "Slot", "Center to Center"),
    ("slot_overall", "slot_overall", "Slot", "Overall"),
    ("slot_centre", "slot_centre", "Slot", "Center Point"),
    ("slot_arc3", "slot_arc3", "Slot", "Three Point Arc"),
    ("slot_arc_centre", "slot_arc_centre", "Slot", "Center Point Arc"),
    ("polygon", "polygon", "Polygon", "Polygon"),
]

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.document = Document()
        # set while an assembly or a CAM sheet is the open document; the part
        # document stays around untouched so nothing that reaches for it has
        # to be guarded
        self.assembly: Optional[AssemblyDocument] = None
        self.cam: Optional[CamDocument] = None
        # every file open at once, behind the tab strip along the bottom
        self.session = session.Session()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(icons.app_icon())
        self.resize(1500, 940)

        self._active_dialog: Optional[dialogs.FeatureDialog] = None
        self.measure_dialog: Optional[MeasureDialog] = None
        self._sketch_feature_id: Optional[int] = None
        self._pending_plane_pick = False
        self._sketch_overlay_ids: List[int] = []
        self._plane_tool_active = False
        self._plane_base = None
        self._profile_dialog = None
        self._profile_regions: List[Dict[str, Any]] = []

        self.viewport = Viewport(self)
        self.editor = SketchEditor(self.viewport, self)
        self.spacemouse = SpaceMouse(SpaceMouseSettings(), self)
        self.spacemouse_dialog: Optional[SpaceMouseDialog] = None
        self.assembly_ui = AssemblyController(self)
        self.cam_ui = CamController(self)
        # a sheet is paper, not a scene, so it gets its own canvas
        # beside the 3D viewport rather than being drawn through it
        self.sheet_canvas = SheetCanvas(self)
        self.drawing = None
        self.drawing_ui = DrawingController(self)

        self._build_ui()
        self._connect()

        self.viewport.ready.connect(self._on_viewport_ready)
        # A cold start is Home and nothing else, the way Inventor opens: an
        # empty part nobody asked for is a tab you have to close later.  The
        # ribbon still starts on the part layout, so it is in a known state
        # the moment a document arrives.
        self._set_workspace(fileformat.PART)
        self.show_start_page()

    # ==================================================================== UI

    def _build_ui(self) -> None:
        # The ribbon goes in the menu-widget slot so it spans the full window
        # width above the docks, the way Inventor lays its ribbon out.
        self.ribbon = Ribbon(self)
        self.setMenuWidget(self.ribbon)

        # the start page and the model view share the central area, with the
        # open-document tabs pinned under both - Inventor's layout, and the
        # reason every file lives in one window instead of its own copy of
        # the application
        self.start_page = StartPage(self)
        self.stack = QtWidgets.QStackedWidget(self)
        self.stack.addWidget(self.start_page)
        self.stack.addWidget(self.viewport)
        self.stack.addWidget(self.sheet_canvas)

        self.doc_tabs = doctabs.DocumentTabs(self)

        self.updater = updater.UpdateController(self)

        centre = QtWidgets.QWidget(self)
        layout = QtWidgets.QVBoxLayout(centre)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        # the update banner sits above everything and is hidden until it
        # has something to say, so it costs no space the rest of the time
        layout.addWidget(self.updater.banner, 0)
        layout.addWidget(self.stack, 1)
        layout.addWidget(self.doc_tabs, 0)
        self.setCentralWidget(centre)

        self.doc_tabs.document_selected.connect(self._tab_selected)
        self.doc_tabs.close_requested.connect(self.close_key)
        self.doc_tabs.save_requested.connect(self.save_key)
        self.doc_tabs.close_others_requested.connect(self.close_others)

        self.start_page.new_requested.connect(self._start_new)
        self.start_page.open_requested.connect(self.open_document)
        self.start_page.open_path_requested.connect(self.open_path)

        self._build_ribbon()
        self._build_docks()
        self._build_status_bar()
        self._build_shortcuts()

    # -- ribbon -------------------------------------------------------------

    def _build_ribbon(self) -> None:
        r = self.ribbon
        self.actions_needing_body: List[QtWidgets.QToolButton] = []

        self._build_file_menu()

        # quick access toolbar, on its own row above the tabs
        r.add_quick_action("new", "New part (Ctrl+N)").clicked.connect(
            lambda: self.new_document())
        r.add_quick_action("open", "Open (Ctrl+O)").clicked.connect(
            self.open_document)
        r.add_quick_action("save", "Save (Ctrl+S)").clicked.connect(
            lambda: self.save_document())
        r.add_quick_separator()
        # Inventor's lightning bolt: pull in the edits made to parts open in
        # other tabs.  Lit only when there is something to pull in.
        self.qat_update = r.add_quick_action("update", "Local Update (Ctrl+U)")
        self.qat_update.clicked.connect(self.local_update)
        self.qat_update.setEnabled(False)
        r.add_quick_separator()
        self.qat_undo = r.add_quick_action("undo", "Undo (Ctrl+Z)")
        self.qat_undo.clicked.connect(self.undo)
        self.qat_redo = r.add_quick_action("redo", "Redo (Ctrl+Y)")
        self.qat_redo.clicked.connect(self.redo)
        r.add_quick_separator()
        r.add_quick_action("iso", "Home view (F6)").clicked.connect(
            lambda: self.viewport.set_view("iso"))
        r.add_quick_action("fit", "Fit all (Home)").clicked.connect(
            lambda: self.viewport.fit_all())
        r.add_quick_separator()
        r.add_quick_action("params", "Parameters (Ctrl+P)").clicked.connect(
            self.edit_parameters)
        r.add_quick_action("measure", "Measure").clicked.connect(
            self.open_measure)

        # -------------------------------------------------- 3D Model: features
        model = r.add_tab(TAB_MODEL)

        panel = model.add_panel("Sketch")
        panel.add_big("sketch", "Start\n2D Sketch",
                      "Create a sketch on a plane or a flat face (S)"
                      ).clicked.connect(self.start_sketch)

        panel = model.add_panel("Create")
        panel.add_big("extrude", "Extrude", "Extrude a sketch profile (E)"
                      ).clicked.connect(lambda: self.new_feature(ExtrudeFeature))
        panel.add_big("revolve", "Revolve", "Revolve a profile about an axis (R)"
                      ).clicked.connect(lambda: self.new_feature(RevolveFeature))
        panel.add_small("sweep", "Sweep", "Sweep a profile along a path"
                        ).clicked.connect(lambda: self.new_feature(SweepFeature))
        panel.add_small("loft", "Loft", "Blend between sketch sections"
                        ).clicked.connect(lambda: self.new_feature(LoftFeature))
        panel.add_small("import", "Import", "Bring in a STEP / IGES body"
                        ).clicked.connect(self.import_geometry)
        panel.add_small("box", "Box").clicked.connect(
            lambda: self.new_primitive("box"))
        panel.add_small("cylinder", "Cylinder").clicked.connect(
            lambda: self.new_primitive("cylinder"))
        panel.add_small("sphere", "Sphere").clicked.connect(
            lambda: self.new_primitive("sphere"))

        panel = model.add_panel("Modify")
        panel.add_big("hole", "Hole", "Place holes at sketch circle centres (H)"
                      ).clicked.connect(lambda: self.new_feature(HoleFeature))
        panel.add_big("fillet", "Fillet", "Round selected edges (F)"
                      ).clicked.connect(lambda: self.new_feature(FilletFeature))
        panel.add_small("chamfer", "Chamfer", "Chamfer selected edges"
                        ).clicked.connect(lambda: self.new_feature(ChamferFeature))
        panel.add_small("shell", "Shell", "Hollow the body out"
                        ).clicked.connect(lambda: self.new_feature(ShellFeature))
        panel.add_small("move", "Move Body").clicked.connect(
            lambda: self.new_feature(MoveFeature))

        panel = model.add_panel("Work Features")
        panel.add_big("plane", "Plane",
                      "Click a flat face or datum plane and drag to pull an "
                      "offset work plane off it"
                      ).clicked.connect(self.start_work_plane)

        panel = model.add_panel("Pattern")
        panel.add_small("pattern", "Rectangular Pattern").clicked.connect(
            lambda: self.new_feature(PatternFeature, mode="rectangular"))
        panel.add_small("pattern_circ", "Circular Pattern").clicked.connect(
            lambda: self.new_feature(PatternFeature, mode="circular"))
        panel.add_small("mirror", "Mirror").clicked.connect(
            lambda: self.new_feature(MirrorFeature))

        # ------------------------------------------------------------ Sketch
        sketch = r.add_tab(TAB_SKETCH, contextual=True)

        panel = sketch.add_panel("Draw")
        self.tool_buttons: Dict[str, QtWidgets.QToolButton] = {}
        for key, icon_name, label, tip in (
                ("line", "line", "Line", "Chained lines (L)"),
        ):
            btn = panel.add_big(icon_name, label, tip, checkable=True)
            btn.clicked.connect(lambda _=False, k=key: self.set_sketch_tool(k))
            self.tool_buttons[key] = btn

        # Rectangle, slot and polygon share one button with a drop-down, the
        # way Inventor groups them.  The button remembers the variant last
        # used, so the common two-point rectangle stays a single click.
        self.rect_button = panel.add_split(RECT_OPTIONS,
                                           "Rectangles, slots and polygons")
        self.rect_button.chosen.connect(self.set_sketch_tool)
        for key in self.rect_button.keys:
            self.tool_buttons[key] = self.rect_button

        for key, icon_name, label, tip in (
                ("circle", "circle", "Circle", "Centre and radius (C)"),
                ("arc", "arc", "Arc", "Centre, start, end (A)"),
        ):
            btn = panel.add_big(icon_name, label, tip, checkable=True)
            btn.clicked.connect(lambda _=False, k=key: self.set_sketch_tool(k))
            self.tool_buttons[key] = btn

        for key, icon_name, label in (("spline", "spline", "Spline"),
                                      ("point", "point", "Point")):
            btn = panel.add_small(icon_name, label, checkable=True)
            btn.clicked.connect(lambda _=False, k=key: self.set_sketch_tool(k))
            self.tool_buttons[key] = btn

        panel = sketch.add_panel("Modify")
        for key, icon_name, label in (("fillet2d", "fillet2d", "Fillet"),
                                      ("trim", "trim", "Trim"),
                                      ("offset", "offset", "Offset")):
            btn = panel.add_small(icon_name, label, checkable=True)
            btn.clicked.connect(lambda _=False, k=key: self.set_sketch_tool(k))
            self.tool_buttons[key] = btn

        panel = sketch.add_panel("Constrain")
        btn = panel.add_big("dimension", "Dimension",
                            "Add a driving dimension (D)", checkable=True)
        btn.clicked.connect(lambda _=False: self.set_sketch_tool("dimension"))
        self.tool_buttons["dimension"] = btn

        for key, icon_name, label in (
                ("coincident", "c_coincident", "Coincident"),
                ("horizontal", "c_horizontal", "Horizontal"),
                ("vertical", "c_vertical", "Vertical"),
                ("parallel", "c_parallel", "Parallel"),
                ("collinear", "c_collinear", "Collinear"),
                ("perpendicular", "c_perpendicular", "Perpendicular"),
                ("tangent", "c_tangent", "Tangent"),
                ("equal", "c_equal", "Equal"),
                ("concentric", "c_concentric", "Concentric"),
                ("midpoint", "c_midpoint", "Midpoint"),
                ("point_on", "c_pointon", "Point On"),
                ("symmetric", "c_symmetric", "Symmetric"),
                ("ground", "c_fix", "Grounded")):
            b = panel.add_small(icon_name, label)
            # works either way round: pick geometry first, or the constraint
            b.clicked.connect(lambda _=False, k=key: self.editor.start_constraint(k))

        panel = sketch.add_panel("Reference")
        panel.add_big("offset", "Project\nGeometry",
                      "Bring model edges onto this sketch plane"
                      ).clicked.connect(self.project_geometry)
        panel.add_small("export", "Export DXF").clicked.connect(
            self.export_sketch_dxf)

        panel = sketch.add_panel("Format")
        self.construction_btn = panel.add_small("offset", "Construction")
        self.construction_btn.clicked.connect(self.editor.toggle_construction)
        self.snap_btn = panel.add_small("grid", "Snap to Grid", checkable=True)
        self.snap_btn.setChecked(True)
        self.snap_btn.toggled.connect(self._set_snap)
        self.grid_btn = panel.add_small("grid", "Show Grid", checkable=True)
        self.grid_btn.setChecked(False)
        self.grid_btn.toggled.connect(self.editor.set_show_grid)
        panel.add_small("delete", "Delete").clicked.connect(
            self.editor.delete_selected)

        panel = sketch.add_panel("Exit")
        panel.add_big("finish", "Finish\nSketch", "Leave sketch mode (Esc Esc)"
                      ).clicked.connect(self.finish_sketch)

        # ---------------------------------------------------------- Assemble
        assemble = r.add_tab(TAB_ASSEMBLE)

        panel = assemble.add_panel("Component")
        panel.add_big("import", "Place", "Bring a part or sub-assembly in (P)"
                      ).clicked.connect(self.assembly_ui.place_component)
        panel.add_small("box", "Create Part", "Start a new part for this "
                        "assembly").clicked.connect(self.new_part_for_assembly)
        panel.add_small("delete", "Delete").clicked.connect(
            self.delete_selected_component)
        panel.add_small("edit", "Open Part").clicked.connect(
            self.open_selected_component)

        panel = assemble.add_panel("Position")
        self.move_button = panel.add_big(
            "move", "Free\nMove", "Drag a component in the view (M)",
            checkable=True)
        self.move_button.clicked.connect(
            lambda: self.toggle_component_tool("move"))
        self.rotate_button = panel.add_big(
            "revolve", "Free\nRotate", "Spin a component in the view",
            checkable=True)
        self.rotate_button.clicked.connect(
            lambda: self.toggle_component_tool("rotate"))
        panel.add_small("params", "Move To...", "Type an exact placement"
                        ).clicked.connect(self.assembly_ui.move_selected)
        panel.add_small("c_fix", "Ground",
                        "Pin the selected component so the solver leaves it"
                        ).clicked.connect(self.ground_selected_component)

        panel = assemble.add_panel("Relationships")
        panel.add_big("c_coincident", "Constrain",
                      "Place a constraint between two components (C)"
                      ).clicked.connect(lambda: self.assembly_ui.constrain(MATE))
        for kind, icon_name in ((FLUSH, "c_parallel"), (INSERT, "c_concentric"),
                                (ANGLE, "dimension"), (TANGENT, "c_tangent")):
            panel.add_small(icon_name, KIND_LABELS[kind], KIND_HINTS[kind]
                            ).clicked.connect(
                lambda _=False, k=kind: self.assembly_ui.constrain(k))

        panel = assemble.add_panel("Manage")
        panel.add_small("rollback", "Update All",
                        "Reload every component from disk"
                        ).clicked.connect(self.reload_components)
        panel.add_small("params", "Parameters", "Named parameters (Ctrl+P)"
                        ).clicked.connect(self.edit_parameters)
        panel.add_small("material", "Properties", "Mass and bounding box"
                        ).clicked.connect(self.show_properties)

        # --------------------------------------------------------------- CAM
        cam = r.add_tab(TAB_CAM)

        panel = cam.add_panel("Sheet")
        panel.add_big("rect", "Sheet\nand Tool",
                      "Stock size, thickness and the cutter (Ctrl+E)"
                      ).clicked.connect(self.cam_ui.sheet_dialog)
        panel.add_small("rollback", "Regenerate",
                        "Re-read every part from disk, keeping the layout"
                        ).clicked.connect(self.cam_ui.regenerate)
        panel.add_small("params", "Parameters", "Named parameters (Ctrl+P)"
                        ).clicked.connect(self.edit_parameters)

        panel = cam.add_panel("Parts")
        panel.add_big("import", "Add\nPart", "Put a part on the sheet (P)"
                      ).clicked.connect(self.cam_ui.add_part)
        panel.add_small("shell", "Cut Face...",
                        "Which face of the part lies on the sheet"
                        ).clicked.connect(self.cut_face_of_selected)
        panel.add_small("edit", "Open Part").clicked.connect(
            self.open_selected_cam_part)
        panel.add_small("delete", "Delete").clicked.connect(
            self.delete_selected_cam_part)

        panel = cam.add_panel("Layout")
        panel.add_big("pattern", "Auto\nArrange",
                      "Lay every part out on a grid with a tool-sized gap"
                      ).clicked.connect(self.cam_ui.auto_arrange)
        self.cam_move_button = panel.add_big(
            "move", "Move\nParts", "Drag parts around the sheet (M)",
            checkable=True)
        self.cam_move_button.clicked.connect(
            lambda: self.toggle_cam_tool("move"))
        panel.add_small("revolve", "Rotate 90").clicked.connect(
            lambda: self.rotate_selected_cam_part(90.0))
        panel.add_small("mirror", "Mirror").clicked.connect(
            self.mirror_selected_cam_part)
        panel.add_small("params", "Move To...").clicked.connect(
            self.move_selected_cam_part)

        panel = cam.add_panel("Toolpath")
        panel.add_big("export", "Export\nDXF",
                      "Write the compensated toolpath for MyPlasm"
                      ).clicked.connect(self.cam_ui.export_dxf)
        self.cam_paths_button = panel.add_small(
            "offset", "Show Toolpath",
            "Draw the offset paths over the parts", checkable=True)
        self.cam_paths_button.setChecked(True)
        self.cam_paths_button.toggled.connect(self.cam_ui.toggle_paths)
        panel.add_small("top", "Look Down").clicked.connect(
            lambda: self.viewport.set_view("top"))

        # ----------------------------------------------------------- Drawing
        drawing_tab = r.add_tab(TAB_DRAWING)

        panel = drawing_tab.add_panel("Sheet")
        panel.add_big("export", "Sheet\nSetup", "Size, border and title block"
                      ).clicked.connect(self.drawing_ui.sheet_setup)
        panel.add_small("new", "New Sheet").clicked.connect(
            self.drawing_ui.new_sheet)
        panel.add_small("pattern", "Duplicate").clicked.connect(
            self.drawing_ui.duplicate_sheet)
        panel.add_small("delete", "Delete Sheet").clicked.connect(
            self.drawing_ui.delete_sheet)

        panel = drawing_tab.add_panel("Views")
        panel.add_big("box", "Base\nView",
                      "Put a part or assembly on the sheet"
                      ).clicked.connect(self.drawing_ui.place_base_view)
        panel.add_small("pattern", "Projected",
                        "A view turned from the selected one"
                        ).clicked.connect(self.drawing_ui.add_projected)
        panel.add_small("section", "Section",
                        "Cut the selected view open"
                        ).clicked.connect(self.drawing_ui.add_section)
        panel.add_small("measure", "Detail",
                        "Enlarge part of the selected view"
                        ).clicked.connect(self.drawing_ui.add_detail)
        panel.add_small("edit", "Properties...").clicked.connect(
            self.drawing_ui.view_properties)

        panel = drawing_tab.add_panel("Annotate")
        panel.add_big("dimension", "Dimension",
                      "Measure the selected view"
                      ).clicked.connect(lambda: self.drawing_ui.add_dimension())
        panel.add_small("c_coincident", "Centre Mark").clicked.connect(
            self.drawing_ui.add_centre_mark)
        panel.add_small("dimension", "Text...").clicked.connect(
            self.drawing_ui.add_note)

        panel = drawing_tab.add_panel("Parts")
        panel.add_big("material", "Parts\nList",
                      "List what the assembly on this sheet is made of"
                      ).clicked.connect(self.drawing_ui.add_parts_list)
        panel.add_small("c_coincident", "Balloon",
                        "One item number on the selected view"
                        ).clicked.connect(self.drawing_ui.add_balloon)
        panel.add_small("pattern", "Auto Balloon",
                        "A balloon on every component of the selected view"
                        ).clicked.connect(self.drawing_ui.auto_balloon)

        panel = drawing_tab.add_panel("Manage")
        self.drawing_update_button = panel.add_small(
            "rollback", "Update",
            "Redraw every view from the models as they are now")
        self.drawing_update_button.clicked.connect(
            lambda: self.drawing_ui.rebuild(force=True))
        panel.add_small("material", "Properties").clicked.connect(
            self.show_properties)

        panel = drawing_tab.add_panel("Output")
        panel.add_big("export", "Export\nPDF", "Every sheet, at true size"
                      ).clicked.connect(self.drawing_ui.export_pdf)
        panel.add_small("export", "DXF...").clicked.connect(
            self.drawing_ui.export_dxf)
        panel.add_small("export", "SVG...").clicked.connect(
            self.drawing_ui.export_svg)
        panel.add_small("export", "Print...").clicked.connect(
            self.drawing_ui.print_drawing)

        # ----------------------------------------------------------- Inspect
        inspect = r.add_tab(TAB_INSPECT)
        panel = inspect.add_panel("Measure")
        panel.add_big("measure", "Measure", "Measure faces, edges and distances"
                      ).clicked.connect(self.open_measure)
        panel.add_big("material", "Properties", "Mass and bounding box"
                      ).clicked.connect(self.show_properties)

        panel = inspect.add_panel("Selection Filter")
        for key, icon_name, label in (("solid", "box", "Body"),
                                      ("face", "shell", "Face"),
                                      ("edge", "line", "Edge"),
                                      ("vertex", "point", "Vertex")):
            b = panel.add_small(icon_name, label, checkable=True)
            b.clicked.connect(lambda _=False, k=key: self.set_pick_mode(k))

        # ------------------------------------------------------------ Manage
        manage = r.add_tab(TAB_MANAGE)
        panel = manage.add_panel("Parameters")
        panel.add_big("params", "Parameters", "Named parameters (Ctrl+P)"
                      ).clicked.connect(self.edit_parameters)

        panel = manage.add_panel("Update")
        panel.add_big("rollback", "Roll to\nEnd", "Rebuild the whole tree"
                      ).clicked.connect(lambda: self.set_rollback(None))
        panel.add_small("undo", "Undo", "Ctrl+Z").clicked.connect(self.undo)
        panel.add_small("redo", "Redo", "Ctrl+Y").clicked.connect(self.redo)
        panel.add_small("delete", "Delete Feature").clicked.connect(
            self.delete_selected_feature)

        # -------------------------------------------------------------- View
        view = r.add_tab(TAB_VIEW)
        panel = view.add_panel("Navigate")
        panel.add_big("fit", "Fit All", "Zoom to the whole model (Home)"
                      ).clicked.connect(self.viewport.fit_all)
        panel.add_big("iso", "Home", "Isometric view (F6)"
                      ).clicked.connect(lambda: self.viewport.set_view("iso"))
        panel.add_small("front", "Front").clicked.connect(
            lambda: self.viewport.set_view("front"))
        panel.add_small("top", "Top").clicked.connect(
            lambda: self.viewport.set_view("top"))
        panel.add_small("right", "Right").clicked.connect(
            lambda: self.viewport.set_view("right"))
        panel.add_small("front", "Normal To",
                        "Look straight at the selected face"
                        ).clicked.connect(self.normal_to_selected_face)

        panel = view.add_panel("Appearance")
        for key, icon_name, label in (("shaded_edges", "shaded_edges",
                                       "Shaded + Edges"),
                                      ("shaded", "shaded", "Shaded"),
                                      ("wireframe", "wireframe", "Wireframe"),
                                      ("xray", "section", "X-Ray")):
            b = panel.add_small(icon_name, label, checkable=True)
            b.clicked.connect(lambda _=False, k=key:
                              self.viewport.set_display_mode(k))

        panel = view.add_panel("Display")
        self.ortho_btn = panel.add_small("ortho", "Orthographic", checkable=True)
        self.ortho_btn.setChecked(True)
        self.ortho_btn.toggled.connect(self.viewport.set_projection)
        self.origin_btn = panel.add_small("plane", "Origin", checkable=True)
        self.origin_btn.setChecked(True)
        self.origin_btn.toggled.connect(self.viewport.set_show_origin)
        self.section_btn = panel.add_small("section", "Section", checkable=True)
        self.section_btn.toggled.connect(self._toggle_section)

        panel = view.add_panel("Navigation")
        self.spacemouse_btn = panel.add_big(
            "spacemouse", "Space\nMouse", "3Dconnexion settings",
            checkable=True)
        self.spacemouse_btn.clicked.connect(self._spacemouse_clicked)

    def _build_file_menu(self) -> None:
        menu = QtWidgets.QMenu(self)
        menu.setMinimumWidth(230)

        def item(icon_name: str, text: str, shortcut: str, slot) -> None:
            # The key hint is drawn into the label rather than set as a real
            # shortcut, so it cannot clash with the application-wide ones.
            label = "%s\t%s" % (text, shortcut) if shortcut else text
            action = menu.addAction(icons.icon(icon_name, 16), label)
            action.triggered.connect(slot)

        item("new", "New", "Ctrl+N", lambda: self.new_document())
        item("open", "Open...", "Ctrl+O", self.open_document)
        menu.addSeparator()
        item("save", "Save", "Ctrl+S", lambda: self.save_document())
        item("save", "Save As...", "Ctrl+Shift+S",
             lambda: self.save_document(as_new=True))
        menu.addSeparator()
        item("import", "Import...", "", self.import_geometry)
        item("export", "Export...", "", self.export_geometry)
        menu.addSeparator()
        item("params", "Parameters...", "Ctrl+P", self.edit_parameters)
        item("open", "Projects...", "", self.choose_project)
        item("rollback", "Check for Updates...", "",
             lambda: self.updater.check(quiet=False))
        item("dimension", "Save as Template...", "",
             lambda: self.drawing_ui.save_as_template())
        menu.addSeparator()
        item("exit", "Exit", "", self.close)

        self.file_menu = menu
        self.ribbon.set_file_menu(menu)

    # -- docks --------------------------------------------------------------

    def _build_docks(self) -> None:
        # one dock, two trees: the feature tree for a part, the component tree
        # for an assembly.  Swapping the page keeps the dock's size and
        # position, which is what the user expects when they open a file.
        self.browser = ModelBrowser(self)
        self.browser_stack = QtWidgets.QStackedWidget(self)
        self.browser_stack.addWidget(self.browser)
        self.browser_stack.addWidget(self.assembly_ui.browser)
        self.browser_stack.addWidget(self.cam_ui.browser)
        self.browser_stack.addWidget(self.drawing_ui.browser)

        dock = QtWidgets.QDockWidget("Model", self)
        dock.setObjectName("ModelDock")
        dock.setWidget(self.browser_stack)
        dock.setFeatures(QtWidgets.QDockWidget.DockWidgetMovable
                         | QtWidgets.QDockWidget.DockWidgetFloatable)
        self.addDockWidget(QtCore.Qt.LeftDockWidgetArea, dock)
        dock.setMinimumWidth(230)
        self.browser_dock = dock

        self.properties = PropertiesPanel(self)
        pdock = QtWidgets.QDockWidget("Properties", self)
        pdock.setObjectName("PropertiesDock")
        pdock.setWidget(self.properties)
        pdock.setFeatures(QtWidgets.QDockWidget.DockWidgetMovable
                          | QtWidgets.QDockWidget.DockWidgetFloatable
                          | QtWidgets.QDockWidget.DockWidgetClosable)
        self.addDockWidget(QtCore.Qt.LeftDockWidgetArea, pdock)
        self.properties_dock = pdock
        self.resizeDocks([dock, pdock], [520, 300], QtCore.Qt.Vertical)

    # -- status bar ---------------------------------------------------------

    def _build_status_bar(self) -> None:
        bar = self.statusBar()
        self.status_message = QtWidgets.QLabel("Ready")
        bar.addWidget(self.status_message, 1)

        self.status_sketch = QtWidgets.QLabel("")
        self.status_sketch.setStyleSheet("color: %s; padding-right: 12px;"
                                         % C.accent)
        bar.addPermanentWidget(self.status_sketch)

        self.status_update = QtWidgets.QLabel("")
        self.status_update.setStyleSheet("color: %s; padding-right: 12px;"
                                         % C.warn)
        self.status_update.setToolTip(
            "A part this document places has been edited in its own tab. "
            "Press Local Update to take the change.")
        bar.addPermanentWidget(self.status_update)

        self.status_build = QtWidgets.QLabel("")
        self.status_build.setStyleSheet("padding-right: 12px;")
        bar.addPermanentWidget(self.status_build)

        self.status_units = QtWidgets.QLabel("mm")
        self.status_units.setStyleSheet("color: %s; padding-right: 8px;"
                                        % C.text_dim)
        bar.addPermanentWidget(self.status_units)

    # -- shortcuts ----------------------------------------------------------

    def _build_shortcuts(self) -> None:
        def add(sequence: str, slot: Callable) -> None:
            action = QtGui.QAction(self)
            action.setShortcut(QtGui.QKeySequence(sequence))
            action.setShortcutContext(QtCore.Qt.ApplicationShortcut)
            action.triggered.connect(slot)
            self.addAction(action)

        add("Ctrl+N", lambda: self.new_document())
        add("Ctrl+O", self.open_document)
        add("Ctrl+S", self.save_document)
        add("Ctrl+Shift+S", lambda: self.save_document(as_new=True))
        add("Ctrl+Z", self.undo)
        add("Ctrl+Y", self.redo)
        add("Ctrl+Shift+Z", self.redo)
        add("Ctrl+P", self.edit_parameters)
        add("S", self.start_sketch)
        add("E", lambda: self.new_feature(ExtrudeFeature))
        add("R", lambda: self.new_feature(RevolveFeature))
        add("H", lambda: self.new_feature(HoleFeature))
        add("F", lambda: self.new_feature(FilletFeature))
        add("L", lambda: self.set_sketch_tool("line"))
        # the same keys mean the assembly commands while an assembly is open,
        # which is how Inventor's contextual shortcuts read
        add("C", lambda: (self.assembly_ui.constrain(MATE) if self.in_assembly
                          else self.set_sketch_tool("circle")))
        add("P", self._place_shortcut)
        add("M", self._move_shortcut)
        add("A", lambda: self.set_sketch_tool("arc"))
        add("Ctrl+E", lambda: (self.cam_ui.sheet_dialog() if self.in_cam
                               else None))
        add("Ctrl+U", self.local_update)
        add("Ctrl+W", lambda: self.close_entry(self.session.active))
        add("Ctrl+Tab", lambda: self.cycle_documents(1))
        add("Ctrl+Shift+Tab", lambda: self.cycle_documents(-1))
        add("D", lambda: self.set_sketch_tool("dimension"))
        add("Ctrl+R", lambda: self.set_sketch_tool("rect"))
        # Return is deliberately NOT an application shortcut: it belongs to
        # whatever is being typed into, and an app-wide one swallowed it
        # before the sketcher's heads-up fields ever saw it.

    def _place_shortcut(self) -> None:
        if self.in_cam:
            self.cam_ui.add_part()
        elif self.in_assembly:
            self.assembly_ui.place_component()

    def _move_shortcut(self) -> None:
        if self.in_cam:
            self.toggle_cam_tool("move")
        elif self.in_assembly:
            self.toggle_component_tool("move")

    # -- signal wiring ------------------------------------------------------

    def _connect(self) -> None:
        b = self.browser
        b.feature_activated.connect(self.edit_feature)
        b.feature_selected.connect(self._browser_selected)
        b.plane_activated.connect(self.start_sketch_on_plane)
        b.delete_requested.connect(self.delete_feature)
        b.rename_requested.connect(self.rename_feature)
        b.suppress_toggled.connect(self.toggle_suppress)
        b.share_toggled.connect(self.toggle_share)
        b.plane_visibility_toggled.connect(self.toggle_plane_visibility)
        b.sketch_on_plane_requested.connect(self.start_sketch_on_plane)
        b.move_requested.connect(self.move_feature)
        b.reorder_requested.connect(self.reorder_feature)
        b.rollback_requested.connect(self.set_rollback)
        b.visibility_toggled.connect(lambda _fid: self._draw_visible_sketches())

        self.viewport.selection_changed.connect(self._on_viewport_selection)
        self.viewport.escape_pressed.connect(self._on_escape)
        self.viewport.context_menu_requested.connect(self._viewport_menu)
        self.viewport.plane_tool_pressed.connect(self._plane_tool_pressed)
        self.viewport.plane_picker_dismissed.connect(self._draw_visible_planes)
        self.viewport.axis_drag_moved.connect(self._plane_drag_moved)
        self.viewport.axis_drag_finished.connect(self._plane_drag_finished)

        self.editor.changed.connect(self._on_sketch_changed)
        self.editor.status_changed.connect(self.status_sketch.setText)
        self.editor.hint_changed.connect(self.ribbon.set_hint)
        self.editor.tool_finished.connect(self._sync_tool_buttons)

        self.properties.material.currentIndexChanged.connect(
            lambda _i: self.properties.update_from(self.document))

    def _on_viewport_ready(self) -> None:
        self.rebuild(keep_camera=False)
        self._start_spacemouse()

    # ---------------------------------------------------------- SpaceMouse

    def _start_spacemouse(self) -> None:
        self.spacemouse.moved.connect(self._spacemouse_moved)
        self.spacemouse.connected.connect(self._spacemouse_connected)
        self.spacemouse.disconnected.connect(self._spacemouse_disconnected)
        if not self.spacemouse.start():
            self.spacemouse_btn.setEnabled(False)
            self.spacemouse_btn.setToolTip(
                "Install hidapi, or comtypes with 3DxWare, to use a "
                "3Dconnexion device")

    def _spacemouse_moved(self, tx, ty, tz, rx, ry, rz) -> None:
        if self.editor.active:
            return          # sketching stays on the plane, not orbiting
        s = self.spacemouse.settings
        self.viewport.apply_spacemouse(tx, ty, tz, rx, ry, rz,
                                       s.pan_speed, s.zoom_speed,
                                       s.rotate_speed)

    def _spacemouse_connected(self, name: str) -> None:
        self.spacemouse_btn.setChecked(True)
        self.spacemouse_btn.setToolTip("%s - click for settings" % name)
        self.status_message.setStyleSheet("")
        self.status_message.setText("%s connected." % name)

    def _spacemouse_disconnected(self) -> None:
        self.spacemouse_btn.setChecked(False)
        self.spacemouse_btn.setToolTip("No 3Dconnexion device found")

    def _spacemouse_clicked(self) -> None:
        if self.spacemouse_dialog is None:
            self.spacemouse_dialog = SpaceMouseDialog(self.spacemouse, self)
        self.spacemouse_dialog.show()
        self.spacemouse_dialog.raise_()
        self.spacemouse_btn.setChecked(self.spacemouse.is_connected
                                       and self.spacemouse.settings.enabled)

    # ============================================================== document

    # ------------------------------------------------------- the start page

    def show_start_page(self) -> None:
        self.start_page.refresh()
        self.stack.setCurrentWidget(self.start_page)
        self.ribbon.setEnabled(False)
        self.browser_dock.setVisible(False)
        self.properties_dock.setVisible(False)
        self.doc_tabs.select(doctabs.HOME)
        self.status_message.setText(
            "Start a new part, or open one you saved earlier."
            if not len(self.session)
            else "%d document(s) open - pick one from the tabs below."
                 % len(self.session))

    def show_model(self) -> None:
        # a drawing lives on the sheet canvas; everything else on the viewport
        if self.in_drawing:
            self.stack.setCurrentWidget(self.sheet_canvas)
        else:
            self.stack.setCurrentWidget(self.viewport)
            self.viewport.sync_size()
        self.ribbon.setEnabled(True)
        self.browser_dock.setVisible(True)
        self.properties_dock.setVisible(True)

    @property
    def on_start_page(self) -> bool:
        return self.stack.currentWidget() is self.start_page

    @property
    def in_assembly(self) -> bool:
        return self.assembly is not None

    @property
    def in_cam(self) -> bool:
        return self.cam is not None

    @property
    def in_drawing(self) -> bool:
        return self.drawing is not None

    @property
    def in_part(self) -> bool:
        """True when the modelling commands actually apply."""
        return (self.assembly is None and self.cam is None
                and self.drawing is None)

    @property
    def active_document(self):
        """Whichever document the window is actually editing."""
        if self.cam is not None:
            return self.cam
        if self.assembly is not None:
            return self.assembly
        return self.document

    def _set_workspace(self, doc_type: str) -> None:
        """Point the ribbon, the browser and the viewport at one document kind."""
        wanted = {fileformat.ASSEMBLY: ASSEMBLY_TABS,
                  fileformat.CAM: CAM_TABS,
                  fileformat.DRAWING: DRAWING_TABS}.get(doc_type, PART_TABS)
        for title in ALL_TABS:
            self.ribbon.set_tab_visible(title, title in wanted)
        self.ribbon.set_tab_visible(TAB_SKETCH, False)
        self.ribbon.show_tab(wanted[0])

        browser = {fileformat.ASSEMBLY: self.assembly_ui.browser,
                   fileformat.CAM: self.cam_ui.browser,
                   fileformat.DRAWING: self.drawing_ui.browser}.get(
                       doc_type, self.browser)
        self.browser_stack.setCurrentWidget(browser)
        self.browser_dock.setWindowTitle(
            {fileformat.ASSEMBLY: "Assembly", fileformat.CAM: "Sheet",
             fileformat.DRAWING: "Drawing"}.get(doc_type, "Model"))

        if doc_type == fileformat.PART:
            self.toggle_component_tool(None)
            self.toggle_cam_tool(None)
            self.viewport.clear_components()
        else:
            self.viewport.set_shape(None, keep_camera=True)

    # -------------------------------------------------- the open documents

    def _adopt(self, document, doc_type: str) -> session.OpenDocument:
        """Put a freshly made or loaded document into a tab and show it."""
        entry = self.session.add(document, doc_type)
        if hasattr(document, "library"):
            # a part being edited in another tab is what this should place,
            # once Local Update has let it through
            document.library.provider = self.session.live_shape
        self.refresh_tabs()
        self.activate(entry, keep_camera=False)
        # Opening a part is not editing it: publish straight away so nothing
        # that places it claims to be out of date before anything happened.
        if entry.is_part:
            entry.publish()
        self._sync_update_button()
        return entry

    def activate(self, entry: Optional[session.OpenDocument],
                 keep_camera: bool = True) -> None:
        """Make one open document the one the whole window is editing."""
        if entry is None:
            self.session.active = None
            self.show_start_page()
            self.refresh_tabs()
            return
        if self.session.active is entry and not self.on_start_page:
            return

        if self.editor.active:
            self.finish_sketch()
        self.close_dialogs()
        self._stash_camera()

        self.session.active = entry
        self.document = entry.document if entry.is_part else Document()
        self.assembly = (entry.document
                         if entry.doc_type == fileformat.ASSEMBLY else None)
        self.cam = (entry.document
                    if entry.doc_type == fileformat.CAM else None)
        self.drawing = (entry.document
                        if entry.doc_type == fileformat.DRAWING else None)

        self._set_workspace(entry.doc_type)
        self.show_model()

        if entry.is_part:
            self.browser.set_document(self.document)
        elif self.in_assembly:
            self.assembly_ui.browser.set_document(self.assembly)
        else:
            self.cam_ui.browser.set_document(self.cam)

        restored = self.viewport.restore_camera(entry.camera) if keep_camera \
            else False
        self.rebuild(keep_camera=restored)
        if not restored:
            if self.in_cam:
                self.viewport.set_view("top")
            self.viewport.fit_all()
        self._update_title()
        self.refresh_tabs()

    def _stash_camera(self) -> None:
        if self.session.active is not None and not self.on_start_page:
            state = self.viewport.camera_state()
            if state is not None:
                self.session.active.camera = state

    def refresh_tabs(self) -> None:
        current = (self.session.active.key if self.session.active
                   and not self.on_start_page else doctabs.HOME)
        self.doc_tabs.reset(self.session.tab_entries(), current)
        self.doc_tabs.setVisible(True)
        self._sync_update_button()

    def _tab_selected(self, key: str) -> None:
        if key == doctabs.HOME:
            self._stash_camera()
            self.show_start_page()
            return
        entry = self.session.by_key(key)
        if entry is not None:
            self.activate(entry)

    def _start_new(self, doc_type: str) -> None:
        if doc_type == fileformat.ASSEMBLY:
            self.new_assembly()
            return
        if doc_type == fileformat.CAM:
            self.new_cam()
            return
        if doc_type == fileformat.DRAWING:
            self.new_drawing()
            return
        if doc_type != fileformat.PART:
            QtWidgets.QMessageBox.information(
                self, fileformat.TYPE_LABELS.get(doc_type, doc_type),
                "The %s document (%s) is implemented in the file format, but "
                "this build has no editor for it yet."
                % (fileformat.TYPE_LABELS.get(doc_type, doc_type).lower(),
                   fileformat.extension_for(doc_type)))
            return
        self.new_document()

    def new_document(self, prompt: bool = True) -> None:
        # ``prompt`` is kept for callers written when only one document could
        # be open at a time; a new document now opens beside the others, so
        # there is nothing to discard.
        document = Document()
        document.params.add("d1", "50", comment="example parameter")
        self._adopt(document, fileformat.PART)
        self.status_message.setText(
            "New part. Start a sketch on a plane, or drop in a primitive.")

    def _offer_resolve(self, document, broken, what: str) -> bool:
        """Ask whether to go and find files that have moved."""
        from .resolvelink import resolve

        answer = QtWidgets.QMessageBox.question(
            self, "Missing %s" % what,
            "%d referenced %s could not be found.\n\nFind them now?"
            % (len(broken), what),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        if answer != QtWidgets.QMessageBox.Yes:
            return False
        fixed = resolve(self, broken, document.base_dir)
        if fixed:
            document.modified = True
            self.status_message.setStyleSheet("")
            self.status_message.setText("Repointed %d reference(s)." % fixed)
        return bool(fixed)

    def new_drawing(self, prompt: bool = True) -> None:
        from ..core import templates
        from .drawing_ui import TemplateDialog

        template = None
        if prompt:
            dialog = TemplateDialog(self, self.project_folder())
            if dialog.exec() != QtWidgets.QDialog.Accepted:
                return
            template = dialog.chosen()
        document = templates.new_from(template)
        if not document.properties.get("Author"):
            document.properties["Author"] = os.environ.get("USERNAME", "")
        self._adopt(document, fileformat.DRAWING)
        self.drawing_ui.rebuild(keep_view=False)
        self.status_message.setStyleSheet("")
        self.status_message.setText(
            "New drawing. Place a base view to put a model on the sheet.")

    def open_drawing(self, path: str) -> bool:
        """Open a .ddat, reporting any model it can no longer find."""
        from ..core.drawing import DrawingDocument

        try:
            document = DrawingDocument.load(path)
        except fileformat.FileFormatError as exc:
            QtWidgets.QMessageBox.critical(self, "Cannot open", str(exc))
            return False
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Open failed", str(exc))
            return False

        self._adopt(document, fileformat.DRAWING)
        self.start_page.remember(path, fileformat.DRAWING)
        report = self.drawing_ui.rebuild(keep_view=False)

        broken = document.broken_links()
        if broken and self._offer_resolve(document, broken, "models"):
            report = self.drawing_ui.rebuild(force=True)
            broken = document.broken_links()
        if broken:
            QtWidgets.QMessageBox.warning(
                self, "Missing models",
                "%s could not find %d of the models it draws:\n\n  %s\n\n"
                "The views it already had are still on the sheet."
                % (os.path.basename(path), len(broken),
                   "\n  ".join(b.describe() for b in broken[:8])))
        else:
            self.status_message.setStyleSheet("")
            self.status_message.setText(
                "Opened %s  -  %s" % (os.path.basename(path),
                                      report.message if report else ""))
        return True

    def new_cam(self, prompt: bool = True) -> None:
        self._adopt(CamDocument(), fileformat.CAM)
        self.status_message.setStyleSheet("")
        self.status_message.setText(
            "New CAM sheet. Set the stock and tool, then add the flat parts "
            "to cut.")

    def new_assembly(self, prompt: bool = True) -> None:
        self._adopt(AssemblyDocument(), fileformat.ASSEMBLY)
        self.status_message.setStyleSheet("")
        self.status_message.setText(
            "New assembly. Place a component to begin - the first one is "
            "grounded automatically.")

    def new_part_for_assembly(self) -> None:
        """Start a part in its own tab, to be placed in this assembly."""
        self.new_document(prompt=False)
        self.status_message.setText(
            "New part in its own tab. Save it, then place it in the assembly.")

    def choose_project(self) -> None:
        """Pick which project the work goes into, from anywhere in the app."""
        self.start_page.choose_project()

    def project_folder(self) -> str:
        """Where the active project keeps its documents.

        Every file dialog starts here, so saving a new part lands it in the
        project rather than wherever the last unrelated dialog happened to
        be pointed.
        """
        try:
            return self.start_page.projects.active().folder
        except Exception:
            return ""

    def in_project(self, filename: str) -> str:
        folder = self.project_folder()
        return os.path.join(folder, filename) if folder else filename

    def open_document(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open", self.project_folder(), fileformat.open_filter())
        if not path:
            return
        self.open_path(path)

    def open_path(self, path: str) -> bool:
        """Open a DATUM document, reporting exactly why if we cannot.

        A file that is already open is not loaded twice: the window switches
        to its tab.  That is what makes editing a part from inside an
        assembly land you in the part you already had open rather than on a
        second, diverging copy of it.
        """
        already = self.session.by_path(path)
        if already is not None:
            self.activate(already)
            self.status_message.setStyleSheet("")
            self.status_message.setText(
                "%s is already open." % os.path.basename(path))
            return True

        try:
            manifest = (fileformat.peek(path)
                        if not path.lower().endswith(fileformat.LEGACY_EXTENSION)
                        else None)
        except fileformat.UnsupportedVersionError as exc:
            QtWidgets.QMessageBox.warning(self, "Newer file", str(exc))
            return False
        except fileformat.FileFormatError as exc:
            QtWidgets.QMessageBox.critical(self, "Cannot open", str(exc))
            return False

        if manifest is not None and manifest.type == fileformat.ASSEMBLY:
            return self.open_assembly(path)
        if manifest is not None and manifest.type == fileformat.CAM:
            return self.open_cam(path)
        if manifest is not None and manifest.type == fileformat.DRAWING:
            return self.open_drawing(path)

        if manifest is not None and manifest.type != fileformat.PART:
            label = fileformat.TYPE_LABELS.get(manifest.type, manifest.type)
            QtWidgets.QMessageBox.information(
                self, "Not a part",
                "%s is a DATUM %s.\n\nThis build edits parts (%s), assemblies "
                "(%s) and CAM sheets (%s). The file itself is valid and will "
                "open once %s editing is added."
                % (os.path.basename(path), label,
                   fileformat.EXTENSIONS[fileformat.PART],
                   fileformat.EXTENSIONS[fileformat.ASSEMBLY],
                   fileformat.EXTENSIONS[fileformat.CAM], label.lower()))
            return False

        try:
            document = Document.load(path)
        except fileformat.FileFormatError as exc:
            QtWidgets.QMessageBox.critical(self, "Cannot open", str(exc))
            return False
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Open failed", str(exc))
            return False

        self._adopt(document, fileformat.PART)
        self.start_page.remember(path, fileformat.PART)

        if document.migrated_from is not None:
            source = ("a FORGE part" if document.migrated_from == 0
                      else "schema %d" % document.migrated_from)
            self.status_message.setText(
                "Opened %s and brought it forward from %s - use Save As to "
                "store it as a DATUM part."
                % (os.path.basename(path), source))
        else:
            self.status_message.setText("Opened %s" % os.path.basename(path))
        return True

    def open_assembly(self, path: str) -> bool:
        """Open an .adat, reporting broken links rather than hiding them."""
        try:
            document = AssemblyDocument.load(path)
        except fileformat.FileFormatError as exc:
            QtWidgets.QMessageBox.critical(self, "Cannot open", str(exc))
            return False
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Open failed", str(exc))
            return False

        self._adopt(document, fileformat.ASSEMBLY)
        report = document.last_report
        self.start_page.remember(path, fileformat.ASSEMBLY)

        broken = document.broken_links()
        if broken and self._offer_resolve(document, broken, "components"):
            self.assembly_ui.rebuild(keep_camera=False)
            broken = document.broken_links()
        if broken:
            QtWidgets.QMessageBox.warning(
                self, "Missing components",
                "%s could not find %d of its components:\n\n  %s\n\nThe rest "
                "of the assembly has opened. Use Replace on a component to "
                "point it at the file's new home."
                % (os.path.basename(path), len(broken),
                   "\n  ".join(b.describe() for b in broken[:8])))
        else:
            self.status_message.setStyleSheet("")
            self.status_message.setText(
                "Opened %s  -  %s" % (os.path.basename(path), report.message))
        return True

    def open_cam(self, path: str) -> bool:
        """Open a .cdat, reporting any part it can no longer find."""
        try:
            document = CamDocument.load(path)
        except fileformat.FileFormatError as exc:
            QtWidgets.QMessageBox.critical(self, "Cannot open", str(exc))
            return False
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Open failed", str(exc))
            return False

        self._adopt(document, fileformat.CAM)
        report = document.last_report
        self.start_page.remember(path, fileformat.CAM)

        broken = document.broken_links()
        if broken and self._offer_resolve(document, broken, "parts"):
            self.cam_ui.rebuild(keep_camera=False)
            broken = document.broken_links()
        if broken:
            QtWidgets.QMessageBox.warning(
                self, "Missing parts",
                "%s could not find %d of the parts it cuts:\n\n  %s\n\nThe "
                "rest of the sheet has opened."
                % (os.path.basename(path), len(broken),
                   "\n  ".join(b.describe() for b in broken[:8])))
        else:
            self.status_message.setStyleSheet("")
            self.status_message.setText(
                "Opened %s  -  %s" % (os.path.basename(path), report.message))
        return True

    def save_document(self, as_new: bool = False,
                      entry: Optional[session.OpenDocument] = None) -> bool:
        entry = entry or self.session.active
        if entry is None:
            return False
        if entry is not self.session.active:
            self.activate(entry)

        document = entry.document
        path = document.path
        if as_new or not path:
            suggested = self.in_project(
                document.title + fileformat.extension_for(document.doc_type))
            path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, "Save %s" % fileformat.TYPE_LABELS.get(
                    document.doc_type, "Part"), suggested,
                fileformat.save_filter(document.doc_type))
            if not path:
                return False
        try:
            written = document.save(path, thumbnail=self._thumbnail())
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Save failed", str(exc))
            return False
        self._update_title()
        self.start_page.remember(written, document.doc_type)
        self.refresh_tabs()
        self.status_message.setText("Saved %s" % os.path.basename(written))
        return True

    def save_key(self, key: str) -> None:
        self.save_document(entry=self.session.by_key(key))

    # ------------------------------------------------------------- closing

    def close_key(self, key: str) -> None:
        self.close_entry(self.session.by_key(key))

    def close_entry(self, entry: Optional[session.OpenDocument]) -> bool:
        """Close one tab, offering to save it first."""
        if entry is None:
            return True
        if entry.modified:
            self.activate(entry)
            answer = QtWidgets.QMessageBox.question(
                self, APP_NAME, "Save changes to %s?" % entry.label,
                QtWidgets.QMessageBox.Save | QtWidgets.QMessageBox.Discard
                | QtWidgets.QMessageBox.Cancel)
            if answer == QtWidgets.QMessageBox.Cancel:
                return False
            if (answer == QtWidgets.QMessageBox.Save
                    and not self.save_document(entry=entry)):
                return False

        dependents = self.session.dependents_of(entry)
        was_active = self.session.active is entry
        self.session.remove(entry)

        # anything that placed it has to fall back to the file on disk
        for other in dependents:
            library = getattr(other.document, "library", None)
            if library is not None and entry.path:
                library.forget(entry.path)

        if was_active:
            remaining = self.session.documents
            self.activate(remaining[-1] if remaining else None)
        else:
            self.refresh_tabs()
        return True

    def cycle_documents(self, step: int) -> None:
        """Ctrl+Tab through the open documents, the way a browser does."""
        entries = self.session.documents
        if not entries:
            return
        if self.session.active is None or self.on_start_page:
            self.activate(entries[0 if step > 0 else -1])
            return
        index = entries.index(self.session.active)
        self.activate(entries[(index + step) % len(entries)])

    def close_others(self, key: str) -> None:
        keep = self.session.by_key(key)
        for entry in list(self.session.documents):
            if entry is not keep and not self.close_entry(entry):
                return
        if keep is not None:
            self.activate(keep)

    # ------------------------------------------------------- local update

    def _sync_update_button(self) -> None:
        """Light the Local Update button when what is on screen is behind."""
        entry = self.session.active
        stale = (entry is not None and not entry.is_part
                 and self.session.is_stale(entry))
        self.qat_update.setEnabled(stale)
        names = ", ".join(sorted({e.label
                                  for e in self.session.stale_sources(entry)})
                          ) if stale else ""
        self.qat_update.setToolTip(
            "Local Update - bring %s up to date with %s" % (
                entry.label if entry else "this document", names)
            if stale else
            "Local Update - nothing has changed in the parts this uses")
        self.status_update.setText("Out of date: %s" % names if stale else "")

    def local_update(self) -> None:
        """Take the edits made in other tabs into this document.

        Inventor's lightning bolt.  Parts edited in their own tabs do not
        push themselves into every assembly that places them - you say when,
        because rebuilding a large assembly is not free and because a part
        halfway through an edit is not something you want propagating.
        """
        entry = self.session.active
        if entry is None or entry.is_part:
            return
        stale = self.session.stale_sources(entry)
        if not stale:
            self.status_message.setStyleSheet("")
            self.status_message.setText("Already up to date.")
            return

        self.session.publish_all()
        library = getattr(entry.document, "library", None)
        if library is not None:
            for source in stale:
                if source.path:
                    library.forget(source.path)
        self.rebuild(keep_camera=True)
        self.status_message.setStyleSheet("")
        self.status_message.setText(
            "Updated with %s." % ", ".join(sorted({s.label for s in stale})))

    def _thumbnail(self) -> Optional[bytes]:
        """Render the viewport to a 256x256 PNG for the archive."""
        import tempfile

        # a drawing's picture is its sheet, not the 3D view behind it
        if self.in_drawing:
            return self.drawing_ui.thumbnail()
        if not self.viewport._ready:
            return None
        handle, temporary = tempfile.mkstemp(suffix=".png")
        os.close(handle)
        try:
            if not self.viewport.grab_image(temporary):
                return None
            image = QtGui.QImage(temporary)
            if image.isNull():
                return None
            side = min(image.width(), image.height())
            square = image.copy((image.width() - side) // 2,
                                (image.height() - side) // 2, side, side)
            preview = square.scaled(
                fileformat.THUMBNAIL_SIZE, fileformat.THUMBNAIL_SIZE,
                QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
            buffer = QtCore.QBuffer()
            buffer.open(QtCore.QIODevice.WriteOnly)
            preview.save(buffer, "PNG")
            return bytes(buffer.data())
        except Exception:
            return None
        finally:
            try:
                os.remove(temporary)
            except OSError:
                pass

    def import_geometry(self) -> None:
        if self.in_cam:
            self.cam_ui.add_part()
            return
        if self.in_assembly:
            self.assembly_ui.place_component()
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Import Geometry", self.project_folder(),
            fileio.IMPORT_FILTER)
        if not path:
            return
        self.document.push_undo()
        feature = ImportFeature()
        feature.path = path
        feature.name = os.path.splitext(os.path.basename(path))[0]
        feature.operation = "new" if self.document.shape is None else "join"
        self.document.add_feature(feature)
        report = self.rebuild(keep_camera=False)
        if not report.ok:
            QtWidgets.QMessageBox.warning(self, "Import", report.message)

    def export_geometry(self) -> None:
        if self.in_cam:
            # a sheet's deliverable is its toolpath, not a solid
            self.cam_ui.export_dxf()
            return
        document = self.active_document
        if document.shape is None:
            QtWidgets.QMessageBox.information(self, "Export",
                                              "There is no body to export.")
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export Geometry",
            self.in_project(document.title + ".step"), fileio.EXPORT_FILTER)
        if not path:
            return
        try:
            fileio.write_shape(document.shape, path)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Export failed", str(exc))
            return
        self.status_message.setText("Exported %s" % os.path.basename(path))

    def _confirm_discard(self) -> bool:
        """Offer to save every modified document. False means "stop"."""
        for entry in list(self.session.modified()):
            self.activate(entry)
            answer = QtWidgets.QMessageBox.question(
                self, APP_NAME, "Save changes to %s?" % entry.label,
                QtWidgets.QMessageBox.Save | QtWidgets.QMessageBox.Discard
                | QtWidgets.QMessageBox.Cancel)
            if answer == QtWidgets.QMessageBox.Cancel:
                return False
            if (answer == QtWidgets.QMessageBox.Save
                    and not self.save_document(entry=entry)):
                return False
        return True

    def _update_title(self) -> None:
        document = self.active_document
        entry = self.session.active
        mark = " *" if document.modified else ""
        name = entry.label if entry is not None else document.title
        self.setWindowTitle("%s  -  %s%s" % (APP_NAME, name, mark))
        self.ribbon.set_document_label("%s%s" % (name, mark))
        self.qat_undo.setEnabled(document.can_undo)
        self.qat_redo.setEnabled(document.can_redo)
        if entry is not None:
            self.doc_tabs.setTabText(
                self.doc_tabs.currentIndex(), "%s%s" % (entry.label, mark))
        self._sync_update_button()

    # the assembly controller lives outside this class, so it needs a name
    # that does not start with an underscore
    update_title = _update_title

    # =============================================================== rebuild

    def rebuild(self, keep_camera: bool = True):
        if self.in_cam:
            report = self.cam_ui.rebuild(keep_camera)
            self._after_rebuild()
            return report
        if self.in_assembly:
            report = self.assembly_ui.rebuild(keep_camera)
            self._after_rebuild()
            return report
        report = self.document.rebuild()
        # the origin planes step aside as soon as the part has a body
        self.document.autohide_origin_planes()
        # A part whose contents actually changed leaves anything placing it
        # behind.  Those documents are told, not updated.  This comes after
        # the auto-hide, which writes to the document too: reading the
        # signature before it would see a change on the *next* rebuild that
        # nobody made.
        entry = self.session.by_document(self.document)
        if entry is not None:
            entry.note_change()
        self.viewport.set_shape(self.document.shape, keep_camera=keep_camera)
        self._draw_visible_planes()
        self._draw_visible_sketches()
        self.browser.refresh()
        self.properties.update_from(self.document)
        self._update_title()

        self.status_build.setText(report.message)
        if report.errors:
            self.status_build.setStyleSheet("color: %s;" % C.error)
        elif report.warnings:
            self.status_build.setStyleSheet("color: %s;" % C.warn)
        else:
            self.status_build.setStyleSheet("color: %s;" % C.text_dim)
        self._after_rebuild()
        return report

    def _after_rebuild(self) -> None:
        """Note what an assembly or sheet was built against, and re-check."""
        entry = self.session.active
        if entry is not None and not entry.is_part:
            self.session.record_sources(entry)
        self._sync_update_button()

    def pick_shape(self):
        """The shape currently on screen - what the user is clicking on."""
        return self.document.shape

    def toggle_plane_visibility(self, name: str) -> None:
        self.document.set_plane_visible(name,
                                        not self.document.plane_visible(name))
        self._draw_visible_planes()
        self.browser.refresh()
        self._update_title()

    def _draw_visible_planes(self) -> None:
        """Keep the switched-on datum and work planes on screen.

        Sketch mode clears them: once you are drawing on a plane, the other
        sheets are just clutter floating through the view.
        """
        if self.editor.active or self.viewport.plane_picker_active:
            self.viewport.hide_planes()
            self.viewport.redraw()
            return
        self.viewport.show_planes(self.document.visible_planes(),
                                  self._picker_size())

    def _draw_visible_sketches(self) -> None:
        """Show finished sketches faintly, the way Inventor keeps them around."""
        if self.editor.active:
            return
        self.viewport.clear_overlay()
        for feature in self.document.sketch_features():
            if self.browser.is_sketch_hidden(feature.id):
                continue
            if feature.suppressed:
                continue
            sketch = feature.sketch
            for eid, ent in sketch.entities.items():
                edge = kernel._sketch_edge(sketch, eid)
                if edge is not None:
                    self.viewport.draw_shape(
                        edge, C.sketch_construction if ent.construction
                        else "#7f8894", 1.2, dashed=ent.construction)
        self.viewport.redraw()

    # ============================================================ assemblies

    def toggle_component_tool(self, mode: Optional[str]) -> None:
        """Turn Free Move or Free Rotate on, or off when it is already on."""
        if not self.in_assembly:
            mode = None
        if mode is not None and self.assembly_ui.tool == mode:
            mode = None
        self.assembly_ui.begin_tool(mode)

    def sync_assembly_tools(self) -> None:
        tool = self.assembly_ui.tool
        self.move_button.setChecked(tool == "move")
        self.rotate_button.setChecked(tool == "rotate")

    def selected_component(self) -> Optional[int]:
        picked = (self.assembly_ui.browser.selected_occurrence_ids()
                  or self.viewport.selected_components())
        return picked[0] if picked else None

    def delete_selected_component(self) -> None:
        occurrence_id = self.selected_component()
        if occurrence_id is not None:
            self.assembly_ui.delete_occurrence(occurrence_id)

    def open_selected_component(self) -> None:
        occurrence_id = self.selected_component()
        if occurrence_id is not None:
            self.assembly_ui.open_component(occurrence_id)

    def ground_selected_component(self) -> None:
        occurrence_id = self.selected_component()
        if occurrence_id is not None:
            self.assembly_ui.toggle_ground(occurrence_id)

    # ================================================================== CAM

    def toggle_cam_tool(self, mode: Optional[str]) -> None:
        """Turn dragging parts around the sheet on, or off when already on."""
        if not self.in_cam:
            mode = None
        if mode is not None and self.cam_ui.tool == mode:
            mode = None
        self.cam_ui.begin_tool(mode)

    def sync_cam_tools(self) -> None:
        self.cam_move_button.setChecked(self.cam_ui.tool == "move")

    def cut_face_of_selected(self) -> None:
        part_id = self.cam_ui.selected_part()
        if part_id is None:
            QtWidgets.QMessageBox.information(
                self, "Cut Face",
                "Select a part first, in the browser or on the sheet.")
            return
        self.cam_ui.cut_face_dialog(part_id)

    def open_selected_cam_part(self) -> None:
        part_id = self.cam_ui.selected_part()
        if part_id is not None:
            self.cam_ui.open_part(part_id)

    def delete_selected_cam_part(self) -> None:
        part_id = self.cam_ui.selected_part()
        if part_id is not None:
            self.cam_ui.delete_part(part_id)

    def rotate_selected_cam_part(self, delta: float) -> None:
        part_id = self.cam_ui.selected_part()
        if part_id is not None:
            self.cam_ui.rotate_part(part_id, delta)

    def mirror_selected_cam_part(self) -> None:
        part_id = self.cam_ui.selected_part()
        if part_id is not None:
            self.cam_ui.toggle_mirror(part_id)

    def move_selected_cam_part(self) -> None:
        part_id = self.cam_ui.selected_part()
        if part_id is not None:
            self.cam_ui.move_dialog(part_id)

    def reload_components(self) -> None:
        """Pick up part edits made outside this window."""
        if not self.in_assembly:
            return
        self.assembly.library.forget()
        report = self.assembly_ui.rebuild()
        self.status_message.setStyleSheet("")
        self.status_message.setText("Reloaded every component.  %s"
                                    % report.message)

    # ============================================================== features

    def new_feature(self, cls, **preset) -> None:
        if not self.in_part:
            return
        if self.editor.active:
            self.finish_sketch()
        needs_sketch = (ExtrudeFeature, RevolveFeature, HoleFeature,
                        SweepFeature, LoftFeature)
        if cls in needs_sketch:
            wanted = 2 if cls in (SweepFeature, LoftFeature) else 1
            if len(self.document.sketch_features()) < wanted:
                QtWidgets.QMessageBox.information(
                    self, cls.type_name.title(),
                    "This feature needs %s. Create %s first."
                    % ("two sketches" if wanted == 2 else "a sketch profile",
                       "them" if wanted == 2 else "one"))
                return
        if cls in (FilletFeature, ChamferFeature, ShellFeature, PatternFeature,
                   MirrorFeature, MoveFeature) and self.document.shape is None:
            QtWidgets.QMessageBox.information(
                self, cls.type_name.title(),
                "There is no body yet. Create one first.")
            return

        snapshot = self.document.snapshot()
        self.document.push_undo()

        feature = cls()
        sketches = self.document.sketch_features()
        if cls in needs_sketch:
            # profile-driven features start blank on purpose - the user picks
            # the regions rather than the app guessing a sketch
            if cls is HoleFeature:
                feature.sketch_id = sketches[-1].id
            if cls is SweepFeature and len(sketches) >= 2:
                feature.path_id = sketches[-1].id
            if cls is LoftFeature:
                feature.sections = [s.id for s in sketches[-2:]]
            if cls is not HoleFeature and self.document.shape is not None:
                feature.operation = "join"
        for key, value in preset.items():
            setattr(feature, key, value)
        if cls is PatternFeature:
            feature.name = ("Circular Pattern" if feature.mode == "circular"
                            else "Rectangular Pattern")
        self.document.add_feature(feature, self._insert_index())
        self._open_dialog(feature, is_new=True, snapshot=snapshot)

    def new_primitive(self, kind: str) -> None:
        if not self.in_part:
            return
        if self.editor.active:
            self.finish_sketch()
        snapshot = self.document.snapshot()
        self.document.push_undo()
        feature = PrimitiveFeature()
        feature.kind = kind
        feature.name = kind.title()
        if kind == "cylinder":
            feature.a, feature.b, feature.c = "20", "40", "0"
        elif kind == "sphere":
            feature.a, feature.b, feature.c = "25", "0", "0"
        if self.document.shape is not None:
            feature.operation = "join"
        self.document.add_feature(feature, self._insert_index())
        self._open_dialog(feature, is_new=True, snapshot=snapshot)

    def _insert_index(self) -> Optional[int]:
        return self.document.rollback_index

    def edit_feature(self, feature_id: int) -> None:
        feature = self.document.feature(feature_id)
        if feature is None:
            return
        if isinstance(feature, SketchFeature):
            self.edit_sketch(feature_id)
            return
        self.document.push_undo()
        self._open_dialog(feature, is_new=False)

    def _open_dialog(self, feature: Feature, is_new: bool,
                     snapshot: Optional[str] = None) -> None:
        self.close_dialogs()
        dialog = dialogs.dialog_for(self, feature, is_new, snapshot)
        if dialog is None:
            self.rebuild()
            return
        self._active_dialog = dialog
        dialog.committed.connect(lambda fid: self._after_dialog(fid))
        dialog.cancelled.connect(lambda: self._after_dialog(None))
        self._position_dialog(dialog)
        dialog.show()
        dialog.raise_()

    def _position_dialog(self, dialog: QtWidgets.QDialog) -> None:
        geo = self.viewport.geometry()
        top_right = self.mapToGlobal(QtCore.QPoint(
            geo.x() + geo.width() - dialog.sizeHint().width() - 24,
            geo.y() + 18))
        dialog.move(top_right)

    position_dialog = _position_dialog

    def _after_dialog(self, feature_id: Optional[int]) -> None:
        self.set_pick_mode(None)
        self.rebuild()
        if feature_id is not None:
            self.browser.select_features([feature_id])

    def dialog_finished(self, dialog) -> None:
        if self._active_dialog is dialog:
            self._active_dialog = None
        if self._profile_dialog is dialog:
            self.set_profile_pick(None)
        self.set_pick_mode(None)

    def close_dialogs(self) -> None:
        self.assembly_ui.close_dialogs()
        self.cam_ui.close_dialogs()
        if self._active_dialog is not None:
            dialog = self._active_dialog
            self._active_dialog = None
            dialog.close()

    def delete_feature(self, feature_id: int) -> None:
        feature = self.document.feature(feature_id)
        if feature is None:
            return
        dependents = self.document.dependents_of(feature_id)
        text = "Delete %s?" % feature.name
        if dependents:
            text += "\n\nThese features depend on it and will fail:\n  %s" % (
                "\n  ".join(f.name for f in dependents))
        if QtWidgets.QMessageBox.question(
                self, "Delete", text) != QtWidgets.QMessageBox.Yes:
            return
        self.document.push_undo()
        self.document.remove_feature(feature_id)
        self.rebuild()

    def delete_selected_feature(self) -> None:
        for fid in self.browser.selected_feature_ids():
            self.delete_feature(fid)
            return

    def rename_feature(self, feature_id: int, name: str) -> None:
        feature = self.document.feature(feature_id)
        if feature is None:
            return
        self.document.push_undo()
        feature.name = name
        self.document.modified = True
        self.browser.refresh()

    def toggle_suppress(self, feature_id: int) -> None:
        feature = self.document.feature(feature_id)
        if feature is None:
            return
        self.document.push_undo()
        feature.suppressed = not feature.suppressed
        self.rebuild()

    def toggle_share(self, feature_id: int) -> None:
        feature = self.document.feature(feature_id)
        if not isinstance(feature, SketchFeature):
            return
        self.document.push_undo()
        feature.shared = not feature.shared
        self.document.modified = True
        self.browser.refresh()
        self._update_title()
        self.status_message.setStyleSheet("")
        self.status_message.setText(
            "%s is now %s." % (feature.name,
                               "shared - it stays at the top of the tree"
                               if feature.shared
                               else "nested under the feature that uses it"))

    def move_feature(self, feature_id: int, delta: int) -> None:
        self.document.push_undo()
        if self.document.move_feature(feature_id, delta):
            self.rebuild()

    def reorder_feature(self, feature_id: int, new_index: int) -> None:
        current = self.document.index_of(feature_id)
        if current < 0 or current == new_index:
            return
        self.move_feature(feature_id, new_index - current)

    def set_rollback(self, index: Optional[int]) -> None:
        self.document.rollback_index = index
        self.rebuild()
        if index is None:
            self.status_message.setText("Rolled to the end of the part.")
        else:
            self.status_message.setText(
                "Rolled back to feature %d. New features insert here." % (index + 1))

    def _browser_selected(self, feature_id: int) -> None:
        feature = self.document.feature(feature_id)
        if feature is not None and feature.error:
            self.status_message.setText(feature.error)
            self.status_message.setStyleSheet("color: %s;" % C.error)
        else:
            self.status_message.setStyleSheet("")
            if feature is not None:
                self.status_message.setText(feature.summary())

    # =========================================================== work planes

    def start_work_plane(self) -> None:
        """Pick a flat face or datum plane, then drag the new plane off it."""
        if not self.in_part:
            return
        if self.editor.active:
            self.finish_sketch()
        self.close_dialogs()

        self._plane_base = None
        self._plane_tool_active = True
        self.viewport.plane_tool = True
        self.set_pick_mode("face")
        self.viewport.show_plane_picker(self.document.planes,
                                        self._picker_size())
        self.status_message.setStyleSheet("")
        self.status_message.setText(
            "Work plane: press on a flat face or a datum plane and drag to "
            "pull the plane off it. Release to set the offset. Esc to cancel.")

    def _picker_size(self) -> float:
        size = 45.0
        if self.document.shape is not None:
            xmin, ymin, zmin, xmax, ymax, zmax = kernel.bounding_box(
                self.document.shape)
            size = max(xmax - xmin, ymax - ymin, zmax - zmin) * 0.75 or size
        return max(size, 25.0)

    def _plane_tool_pressed(self) -> None:
        """A face or datum plane was pressed - begin the pull-off drag."""
        if not self._plane_tool_active:
            return

        datum = self.viewport.picked_plane()
        if datum is not None:
            source = self.document.planes.get(datum) or STANDARD_PLANES["XY"]
            base = SketchPlane(source.origin, source.normal, source.xdir,
                               source.name)
            ref = None
            centre = (0.0, 0.0)
        else:
            faces = self.viewport.selected_faces()
            if not faces:
                return
            base = plane_from_face(faces[0])
            if base is None:
                self.status_message.setStyleSheet("color: %s;" % C.warn)
                self.status_message.setText(
                    "That face is not flat - pick a planar face.")
                return
            refs = RefSet()
            refs.capture_from(self.document.shape, "face", [faces[0]])
            ref = refs.refs[0] if refs.refs else None
            # draw the preview over the face itself, not the surface anchor
            centre = base.to_2d(kernel.shape_centre(faces[0]))

        self._plane_base = (base, ref, datum, centre)
        self.viewport.hide_plane_picker()
        self.viewport.clear_selection()
        self.viewport.begin_axis_drag(base.origin, base.normal)
        self._plane_drag_moved(0.0)

    def _plane_drag_moved(self, distance: float) -> None:
        if self._plane_base is None:
            return
        base, _ref, _datum, centre = self._plane_base
        n = base.normal
        origin = tuple(base.origin[i] + n[i] * distance for i in range(3))
        preview = SketchPlane(origin, base.normal, base.xdir, "Work Plane")

        size = self._picker_size() * 0.7
        anchor = base.to_3d(centre[0], centre[1])
        moved = tuple(anchor[i] + n[i] * distance for i in range(3))

        self.viewport.clear_preview()
        self.viewport.draw_plane(preview, size, C.warn, preview=True,
                                 transparency=0.62, centre=centre)
        self.viewport.draw_edge(anchor, moved, C.warn, 1.6, preview=True,
                                dashed=True)
        self.viewport.draw_text(
            "%.2f mm" % distance,
            preview.to_3d(centre[0] + size * 0.08, centre[1] + size * 0.08),
            C.warn, 16.0, preview=True)
        self.viewport.redraw()
        self.status_message.setText("Offset %.2f mm - release to place." % distance)

    def _plane_drag_finished(self, distance: float) -> None:
        if self._plane_base is None:
            return
        base, ref, datum, _centre = self._plane_base
        self._end_plane_tool()

        snapshot = self.document.snapshot()
        self.document.push_undo()

        feature = WorkPlaneFeature()
        feature.face_ref = ref
        feature.base = datum or "XY"
        # a plain click without dragging gets a sensible starting offset
        feature.offset = "%.3g" % (distance if abs(distance) > 0.05 else 10.0)
        self.document.add_feature(feature, self._insert_index())
        self._open_dialog(feature, is_new=True, snapshot=snapshot)

    def _end_plane_tool(self) -> None:
        self._plane_tool_active = False
        self._plane_base = None
        self.viewport.plane_tool = False
        self.viewport.end_axis_drag()
        self.viewport.hide_plane_picker()
        self.viewport.clear_preview()
        self.set_pick_mode(None)

    # ================================================================ sketch

    def start_sketch(self) -> None:
        """Offer the datum planes (and any flat model face) to sketch on."""
        if not self.in_part:
            return
        if self.editor.active:
            self.finish_sketch()
            return

        self._pending_plane_pick = True
        self.set_pick_mode("face")
        # every datum *and* work plane is offered, so a plane you built
        # earlier can be sketched on directly
        self.viewport.show_plane_picker(self.document.planes,
                                        self._picker_size())

        if self.document.shape is None:
            self.viewport.set_view("iso")
        self.status_message.setStyleSheet("")
        self.status_message.setText(
            "Pick a plane to sketch on - the three origin planes are shown, "
            "or click any flat face of the model. Esc to cancel.")

    def start_sketch_on_plane(self, key: str) -> None:
        plane = STANDARD_PLANES.get(key) or self.document.planes.get(key)
        if plane is None:
            return
        self._begin_sketch(SketchPlane(plane.origin, plane.normal, plane.xdir,
                                       plane.name), None)

    def _begin_sketch(self, plane: SketchPlane, face_ref) -> None:
        self.close_dialogs()
        self._pending_plane_pick = False
        self.viewport.hide_plane_picker()
        self.document.push_undo()
        feature = SketchFeature()
        feature.sketch = Sketch(plane, "Sketch")
        feature.face_ref = face_ref
        self.document.add_feature(feature, self._insert_index())
        feature.sketch.name = feature.name
        self.rebuild()
        self.edit_sketch(feature.id)

    def edit_sketch(self, feature_id: int) -> None:
        feature = self.document.feature(feature_id)
        if not isinstance(feature, SketchFeature):
            return
        self.close_dialogs()
        self._pending_plane_pick = False
        self._sketch_feature_id = feature_id

        # roll the model back so the sketch is edited against the body that
        # existed when it was created, exactly like Inventor
        self._saved_rollback = self.document.rollback_index
        self.document.rollback_index = self.document.index_of(feature_id) + 1
        self.document.rebuild()
        self.viewport.set_shape(self.document.shape, keep_camera=True)

        self.editor.begin(feature.sketch, self.document.params)
        self._draw_visible_planes()      # clears them while sketching
        # swing round to the plane rather than snapping, so it stays obvious
        # which way the model turned
        self.viewport.look_at_plane(feature.sketch.plane, fit=True,
                                    animate=True)
        self.viewport.refresh_grid()
        self.editor.grid_step = self.viewport.grid_step
        self.ribbon.set_tab_visible(TAB_SKETCH, True)
        self.ribbon.show_tab(TAB_SKETCH)
        self.browser.refresh()
        self.status_message.setText("Editing %s" % feature.name)
        self._sync_tool_buttons()

    def finish_sketch(self) -> None:
        if not self.editor.active:
            return
        self.editor.end()
        self._sketch_feature_id = None
        self.document.rollback_index = getattr(self, "_saved_rollback", None)
        self.ribbon.show_tab(TAB_MODEL)
        self.ribbon.set_tab_visible(TAB_SKETCH, False)
        self.ribbon.set_hint("")
        self.status_sketch.setText("")
        self.status_message.setStyleSheet("")
        self.status_message.setText("Sketch finished.")
        self.rebuild()
        self.viewport.set_view("iso")
        self._sync_tool_buttons()

    def build_sketch_menu(self, menu: QtWidgets.QMenu) -> QtWidgets.QMenu:
        """The right-click menu while a sketch is open.

        Built apart from the event that shows it, so what the user gets can
        be read back without having to put a menu on screen.
        """
        # OK comes first, because the common thing to want after drawing a
        # run of lines is to stop.  It is only offered when a tool is
        # actually running - with nothing going on there is nothing to OK.
        if self.editor.busy:
            done = menu.addAction(icons.icon("finish", 16), "OK")
            done.setToolTip("Finish this tool and go back to selecting")
            done.triggered.connect(self.ok_sketch_tool)
            menu.addSeparator()

        project = menu.addAction(icons.icon("offset", 16), "Project Geometry")
        project.setToolTip("Bring model edges onto this sketch plane")
        project.triggered.connect(self.project_geometry)
        menu.addAction(icons.icon("export", 16), "Export Sketch as DXF"
                       ).triggered.connect(self.export_sketch_dxf)
        menu.addSeparator()
        menu.addAction(icons.icon("finish", 16),
                       "Finish Sketch").triggered.connect(self.finish_sketch)
        return menu

    def ok_sketch_tool(self) -> None:
        """Finish the running sketch tool, from the right-click menu."""
        self.editor.ok()
        self._sync_tool_buttons()

    def set_sketch_tool(self, tool: str) -> None:
        if not self.editor.active:
            return
        self.editor.set_tool(tool)
        self._sync_tool_buttons()

    def _sync_tool_buttons(self) -> None:
        # one button can stand for several tools, so work out each button's
        # state from every key that points at it rather than the last one
        lit: Dict[int, bool] = {}
        for key, btn in self.tool_buttons.items():
            on = self.editor.active and self.editor.tool == key
            lit[id(btn)] = lit.get(id(btn), False) or on
        for key, btn in self.tool_buttons.items():
            btn.setChecked(lit.get(id(btn), False))
        current = self.editor.tool if self.editor.active else ""
        if current in self.rect_button.keys:
            self.rect_button.set_current(current)

    def _set_snap(self, on: bool) -> None:
        self.editor.snap_grid = on

    def _on_sketch_changed(self) -> None:
        self.document.modified = True
        self._update_title()

    # ============================================================ inspection

    # ------------------------------------------------------ profile picking

    def set_profile_pick(self, dialog) -> None:
        """Turn viewport profile picking on for a dialog, or off entirely."""
        self._profile_dialog = dialog
        if dialog is None:
            self.viewport.hide_profiles()
            self._profile_regions = []
            self.set_pick_mode(None)
            self.viewport.redraw()
            return

        # Only profiles are selectable while picking them. Otherwise the
        # preview solid sits over its own profile and you cannot click the
        # region again to drop it.
        self.viewport.set_selection_mode("none")
        self.refresh_profile_overlay()
        self.status_message.setStyleSheet("")
        self.status_message.setText(
            "Click the closed regions to use. Click one again to drop it.")

    def select_all_profiles(self) -> int:
        """Give the open feature every available region.

        A shortcut for scripting and for the common case of a sketch that
        holds exactly the profile you meant - the dialog itself still starts
        blank and proposes nothing.
        """
        dialog = self._active_dialog
        if dialog is None or not hasattr(dialog, "profiles"):
            return 0
        for region in self.available_regions():
            if not dialog.feature.profiles.contains(region["sketch_id"],
                                                    region["centre"]):
                dialog.on_profile_clicked(region["sketch_id"],
                                          region["centre"])
        return len(dialog.feature.profiles)

    def available_regions(self) -> List[Dict[str, Any]]:
        """Every closed region in the document's sketches."""
        from ..core.features import BuildContext, collect_regions

        ctx = BuildContext(self.document.params.scope())
        ctx.sketches = dict(self.document.all_sketches())
        return collect_regions(ctx)

    def refresh_profile_overlay(self) -> None:
        if self._profile_dialog is None:
            return
        self.viewport.set_selection_mode("none")
        self._profile_regions = self.available_regions()
        selection = self._profile_dialog.feature.profiles
        chosen = [i for i, region in enumerate(self._profile_regions)
                  if selection.contains(region["sketch_id"], region["centre"])]
        self.viewport.show_profiles(self._profile_regions, chosen)

    def _on_profile_selection(self) -> bool:
        """Route a viewport click to the dialog waiting for profiles."""
        if self._profile_dialog is None:
            return False
        picked = [i for i in self.viewport.picked_profiles()
                  if i < len(self._profile_regions)]
        if not picked:
            return False

        # Every click toggles, so profiles build up without holding Ctrl -
        # and the viewport selection is dropped straight away so the next
        # click starts clean.
        self.viewport.clear_selection()
        for index in picked:
            region = self._profile_regions[index]
            self._profile_dialog.on_profile_clicked(region["sketch_id"],
                                                    region["centre"])
        return True

    def set_pick_mode(self, mode: Optional[str]) -> None:
        # faces are the resting state: hovering the model highlights the flat
        # faces you can sketch on rather than the body as a whole
        self.viewport.set_selection_mode(mode or "face")
        if mode is None:
            self.viewport.clear_selection()

    def _on_viewport_selection(self) -> None:
        if self.in_cam:
            self.cam_ui.on_selection()
            return
        if self.in_assembly:
            self.assembly_ui.on_selection()
            return
        if self._on_profile_selection():
            return
        if self._pending_plane_pick:
            datum = self.viewport.picked_plane()
            if datum is not None:
                self._pending_plane_pick = False
                self.viewport.hide_plane_picker()
                self.set_pick_mode(None)
                self.start_sketch_on_plane(datum)
                return

            faces = self.viewport.selected_faces()
            if faces:
                plane = plane_from_face(faces[0])
                if plane is None:
                    self.status_message.setText(
                        "That face is not flat - pick a planar face.")
                    return
                from ..core.naming import RefSet, ShapeRef
                refs = RefSet()
                refs.capture_from(self.document.shape, "face", [faces[0]])
                ref = refs.refs[0] if refs.refs else None
                self._pending_plane_pick = False
                self.viewport.hide_plane_picker()
                self.set_pick_mode(None)
                plane.name = "Face"
                self._begin_sketch(plane, ref)
            return

        if self._active_dialog is not None:
            self._active_dialog.on_selection()
        if self.measure_dialog is not None:
            self.measure_dialog.refresh()

    def open_measure(self) -> None:
        if self.measure_dialog is None:
            self.measure_dialog = MeasureDialog(self, self)
        self.measure_dialog.show()
        self.measure_dialog.raise_()

    def show_properties(self) -> None:
        self.properties_dock.show()
        self.properties_dock.raise_()
        self.properties.update_from(self.active_document)

    def edit_parameters(self) -> None:
        dialog = ParametersDialog(self.active_document, self)
        dialog.changed.connect(lambda: self.rebuild(keep_camera=True))
        dialog.exec()
        self.rebuild(keep_camera=True)

    def _toggle_section(self, on: bool) -> None:
        self.viewport.set_section(on, STANDARD_PLANES["XZ"], 0.0)

    # ============================================================ edit stack

    def undo(self) -> None:
        if self.in_cam:
            if self.cam.undo():
                self.cam_ui.rebuild()
                self.status_message.setText("Undo")
            return
        if self.in_assembly:
            if self.assembly.undo():
                self.assembly_ui.rebuild()
                self.status_message.setText("Undo")
            return
        # inside a sketch, undo steps back through sketch edits; it should
        # never throw you out of the sketch you are working in
        if self.editor.active:
            if self.editor.undo():
                self.status_message.setStyleSheet("")
                self.status_message.setText("Undo (sketch)")
            else:
                self.status_message.setText("Nothing left to undo in this sketch.")
            return
        if self.document.undo():
            self.rebuild()
            self.status_message.setText("Undo")

    def redo(self) -> None:
        if self.in_cam:
            if self.cam.redo():
                self.cam_ui.rebuild()
                self.status_message.setText("Redo")
            return
        if self.in_assembly:
            if self.assembly.redo():
                self.assembly_ui.rebuild()
                self.status_message.setText("Redo")
            return
        if self.editor.active:
            if self.editor.redo():
                self.status_message.setText("Redo (sketch)")
            return
        if self.document.redo():
            self.rebuild()
            self.status_message.setText("Redo")

    # ============================================================== plumbing

    def _on_escape(self) -> None:
        # cancel the innermost operation first; Esc never leaves a sketch
        if self.in_cam:
            if self.cam_ui.on_escape():
                return
            self.viewport.clear_selection()
            return
        if self.in_assembly:
            if self.assembly_ui.on_escape():
                return
            self.viewport.clear_selection()
            return
        if self._active_dialog is not None:
            self._active_dialog.cancel()
            return
        if self._plane_tool_active:
            self._end_plane_tool()
            self.status_message.setText("Cancelled.")
            return
        if self._pending_plane_pick:
            self._pending_plane_pick = False
            self.viewport.hide_plane_picker()
            self.set_pick_mode(None)
            self.status_message.setText("Cancelled.")
            return
        if self.editor.active:
            return   # the editor handles its own Esc
        self.viewport.clear_selection()

    def _viewport_menu(self, global_pos: QtCore.QPoint) -> None:
        menu = QtWidgets.QMenu(self)
        if self.in_cam:
            self.cam_ui.context_menu(menu)
            menu.exec(global_pos)
            return
        if self.in_assembly:
            self.assembly_ui.context_menu(menu)
            menu.exec(global_pos)
            return
        if self.editor.active:
            self.build_sketch_menu(menu)
        else:
            plane_name = self.viewport.picked_plane()
            if plane_name is not None:
                self._add_plane_menu(menu, plane_name)
                menu.addSeparator()

            faces = self.viewport.selected_faces()
            if faces:
                # the face under the cursor was just picked, so lead with the
                # thing you most likely right-clicked it for
                flat = plane_from_face(faces[0]) is not None
                action = menu.addAction(icons.icon("sketch", 16),
                                        "New Sketch on this Face")
                action.setEnabled(flat)
                if not flat:
                    action.setText("New Sketch (face is not flat)")
                action.triggered.connect(self._sketch_on_selected_face)

                plane = menu.addAction(icons.icon("plane", 16),
                                       "Work Plane from this Face")
                plane.setEnabled(flat)
                plane.triggered.connect(self._plane_from_selected_face)

                normal = menu.addAction(icons.icon("front", 16), "Normal To")
                normal.setEnabled(flat)
                normal.setToolTip("Look straight at this face")
                normal.triggered.connect(self.normal_to_selected_face)

                export = menu.addAction(icons.icon("export", 16),
                                        "Export Face as DXF...")
                export.setEnabled(flat)
                export.triggered.connect(self.export_face_dxf)
                menu.addSeparator()

            menu.addAction(icons.icon("sketch", 16), "Start Sketch"
                           ).triggered.connect(self.start_sketch)
            menu.addSeparator()
            menu.addAction(icons.icon("fit", 16), "Fit All").triggered.connect(
                self.viewport.fit_all)
            menu.addAction(icons.icon("iso", 16), "Home View").triggered.connect(
                lambda: self.viewport.set_view("iso"))
        menu.exec(global_pos)

    def _add_plane_menu(self, menu: QtWidgets.QMenu, name: str) -> None:
        """Menu entries for a plane clicked in the viewport."""
        sketch = menu.addAction(icons.icon("sketch", 16),
                                "New Sketch on %s" % name)
        sketch.triggered.connect(
            lambda: self.start_sketch_on_plane(name))

        visible = menu.addAction("Visible")
        visible.setCheckable(True)
        visible.setChecked(self.document.plane_visible(name))
        visible.triggered.connect(
            lambda: self.toggle_plane_visibility(name))

        feature = next((f for f in self.document.features
                        if isinstance(f, WorkPlaneFeature) and f.name == name),
                       None)
        if feature is not None:
            edit = menu.addAction(icons.icon("edit", 16), "Edit Plane")
            edit.triggered.connect(
                lambda: self.edit_feature(feature.id))
            delete = menu.addAction(icons.icon("delete", 16), "Delete Plane")
            delete.triggered.connect(
                lambda: self.delete_feature(feature.id))

    def _sketch_on_selected_face(self) -> None:
        self._pending_plane_pick = True
        self._on_viewport_selection()

    def normal_to_selected_face(self) -> None:
        """Swing the camera round to look straight at the picked face."""
        faces = self.viewport.selected_faces()
        if not faces:
            return
        plane = plane_from_face(faces[0])
        if plane is None:
            self.status_message.setStyleSheet("color: %s;" % C.warn)
            self.status_message.setText("That face is not flat.")
            return
        self.viewport.look_normal_to(plane, animate=True)
        self.status_message.setStyleSheet("")
        self.status_message.setText("Looking normal to the selected face.")

    def export_face_dxf(self) -> None:
        """Write one flat face out as a 2D DXF profile."""
        from ..core import dxf

        faces = self.viewport.selected_faces()
        if not faces:
            return
        suggested = os.path.join(
            os.path.dirname(self.document.path) if self.document.path
            else self.project_folder(),
            "%s-face.dxf" % self.document.title)
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export Face as DXF", suggested, dxf.DXF_FILTER)
        if not path:
            return
        try:
            written = dxf.face_to_dxf(faces[0], path)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Export failed", str(exc))
            return
        self.status_message.setStyleSheet("")
        self.status_message.setText("Exported %s" % os.path.basename(written))

    def export_sketch_dxf(self) -> None:
        from ..core import dxf

        if not self.editor.active:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export Sketch as DXF",
            self.in_project("%s.dxf" % self.editor.sketch.name),
            dxf.DXF_FILTER)
        if not path:
            return
        try:
            written = dxf.sketch_to_dxf(self.editor.sketch, path)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Export failed", str(exc))
            return
        self.status_message.setText("Exported %s" % os.path.basename(written))

    def project_geometry(self) -> None:
        """Project the model's edges onto the sketch being edited."""
        if not self.editor.active:
            return
        shape = self.document.shape
        if shape is None:
            QtWidgets.QMessageBox.information(
                self, "Project Geometry",
                "There is no body to project from yet.")
            return
        self.document.push_undo()
        count = self.editor.project_geometry(shape)
        self.status_message.setStyleSheet("")
        if count:
            self.status_message.setText(
                "Projected %d edge(s) onto %s."
                % (count, self.editor.sketch.name))
        else:
            self.status_message.setText("Nothing projected onto this plane.")

    def _plane_from_selected_face(self) -> None:
        """Build a work plane straight off the face that was right-clicked."""
        faces = self.viewport.selected_faces()
        if not faces:
            return
        self.start_work_plane()
        self.viewport.selected_faces = lambda f=faces[0]: [f]
        try:
            self._plane_tool_pressed()
        finally:
            del self.viewport.selected_faces
        if self._plane_base is not None:
            self.viewport.end_axis_drag()
            self._plane_drag_finished(10.0)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if self._confirm_discard():
            self.spacemouse.stop()
            event.accept()
        else:
            event.ignore()
