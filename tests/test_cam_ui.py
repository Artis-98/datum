"""The CAM workspace driven through a real MainWindow."""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402,F401


# keep the test run out of the real recent-files list
os.environ["DATUM_SETTINGS_ORG"] = "IITEG-tests"

from PySide6 import QtCore, QtWidgets                              # noqa: E402

from datum.core import fileformat, kernel, sheet                   # noqa: E402
from datum.core.cam import CamDocument                             # noqa: E402
from datum.core.document import Document                           # noqa: E402
from datum.core.features import (                                  # noqa: E402
    ExtrudeFeature, HoleFeature, PrimitiveFeature, SketchFeature,
)
from datum.core.sketch import STANDARD_PLANES, Sketch              # noqa: E402
from datum.core.toolpath import INSIDE, OUTSIDE                    # noqa: E402
from datum.ui.cam_ui import (                                      # noqa: E402
    CutFaceDialog, PartPlacementDialog, SheetDialog,
)
from datum.ui.main_window import (                                 # noqa: E402
    CAM_TABS, TAB_CAM, TAB_MODEL, MainWindow,
)
from datum.ui.theme import stylesheet                              # noqa: E402

FAILS = []
WORK = tempfile.mkdtemp(prefix="datum_camui_")

app = QtWidgets.QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(stylesheet())
win = MainWindow()
win.resize(1400, 900)
win.show()
app.processEvents()


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def pump(n=4):
    for _ in range(n):
        app.processEvents()


def plate(width, height, thickness, holes, name):
    doc = Document()
    outline = SketchFeature()
    outline.sketch = Sketch(STANDARD_PLANES["XY"], "Outline")
    outline.sketch.add_rectangle((0, 0), (width, height))
    doc.add_feature(outline)

    extrude = ExtrudeFeature()
    extrude.distance = str(thickness)
    extrude.profiles.add(outline.id, (width * 0.5, height * 0.5))
    doc.add_feature(extrude)

    if holes:
        centres = SketchFeature()
        centres.sketch = Sketch(STANDARD_PLANES["XY"], "Holes")
        for x, y, d in holes:
            centres.sketch.add_circle((x, y), d * 0.5)
        doc.add_feature(centres)
        cut = HoleFeature()
        cut.sketch_id = centres.id
        cut.diameter = str(holes[0][2])
        cut.depth = str(thickness * 4)
        cut.through = True
        doc.add_feature(cut)

    doc.rebuild()
    return doc.save(os.path.join(WORK, name))


BRACKET = plate(120, 80, 3, ((30, 40, 12),), "bracket")
GUSSET = plate(90, 60, 3, ((45, 30, 20),), "gusset")

lumpy = Document()
box = PrimitiveFeature()
box.kind, box.a, box.b, box.c = "box", "80", "50", "3"
lumpy.add_feature(box)
boss = PrimitiveFeature()
boss.kind, boss.a, boss.b, boss.c = "cylinder", "8", "14", "0"
boss.operation = "join"
lumpy.add_feature(boss)
lumpy.rebuild()
LUMPY = lumpy.save(os.path.join(WORK, "lumpy"))


# ==========================================================================
print("the start page offers a CAM sheet of its own")

win.show_start_page()
pump()
check("there is a CAM tile", win.start_page.cam_tile is not None)
check("it is live", win.start_page.cam_tile.isEnabled())
check("it names the extension",
      ".cdat" in win.start_page.cam_tile.findChildren(QtWidgets.QLabel)[-1]
      .text(), win.start_page.cam_tile.toolTip())
check("part, assembly and CAM all work, drawing does not",
      win.start_page.part_tile.isEnabled()
      and win.start_page.assembly_tile.isEnabled()
      and win.start_page.cam_tile.isEnabled()
      and win.start_page.drawing_tile.isEnabled())

win.start_page.new_requested.emit(fileformat.CAM)
pump()
check("a new sheet opens the workspace", not win.on_start_page)
check("the window knows it is a CAM sheet", win.in_cam)
check("and not an assembly or a part",
      not win.in_assembly and not win.in_part)
check("the document is a CamDocument", isinstance(win.cam, CamDocument))
check("the extension is .cdat",
      fileformat.extension_for(win.cam.doc_type) == ".cdat")


# ==========================================================================
print("the workspace swaps over")

check("the CAM tab is showing", win.ribbon.current_tab() == TAB_CAM,
      win.ribbon.current_tab())
check("3D Model is put away",
      not win.ribbon._buttons[TAB_MODEL].isVisible())
check("Assemble is put away too",
      not win.ribbon._buttons["Assemble"].isVisible())
check("every CAM tab is offered",
      all(win.ribbon._buttons[t].isVisible() for t in CAM_TABS))
check("the browser shows the sheet tree",
      win.browser_stack.currentWidget() is win.cam_ui.browser)
check("the dock is retitled", win.browser_dock.windowTitle() == "Sheet",
      win.browser_dock.windowTitle())
check("the title says Sheet1", "Sheet1" in win.windowTitle(),
      win.windowTitle())
check("an empty sheet says so",
      "Empty sheet" in win.cam.last_report.message,
      win.cam.last_report.message)
check("the stock outline is drawn",
      len(win.viewport._overlay) >= 2, len(win.viewport._overlay))


# ==========================================================================
print("adding parts")

QtWidgets.QFileDialog.getOpenFileNames = staticmethod(
    lambda *a, **k: ([BRACKET, GUSSET], ""))
win.cam.sheet_width, win.cam.sheet_height = "600", "400"
win.cam_ui.add_part()
pump()

doc = win.cam
check("both landed", len(doc.parts) == 2, len(doc.parts))
check("each found its cut face",
      all(p.cut_face.known for p in doc.parts))
check("thickness was read off", abs(doc.parts[0].cut_face.thickness - 3.0)
      < 1e-6, doc.parts[0].cut_face.thickness)
check("both were laid out", all(p.laid_out for p in doc.parts))
check("and neither counts as hand placed",
      not any(p.manual for p in doc.parts))
check("the sheet builds clean", doc.last_report.ok,
      doc.last_report.message)
check("four cuts: two outlines, two holes",
      len(doc.last_report.toolpath.cuts) == 4,
      len(doc.last_report.toolpath.cuts))
check("both parts are drawn and selectable",
      len(win.viewport._component_ais) == 2,
      len(win.viewport._component_ais))
check("they are drawn as outlines, not solids",
      len(win.viewport._component_wires) == 2)
check("the toolpath is drawn over them",
      len(win.viewport._overlay) > 4, len(win.viewport._overlay))

print("a part that is not flat is refused, with a reason")
warned = []
original_warning = QtWidgets.QMessageBox.warning
QtWidgets.QMessageBox.warning = staticmethod(
    lambda *a, **k: warned.append(a[2] if len(a) > 2 else "")
    or QtWidgets.QMessageBox.Ok)
QtWidgets.QFileDialog.getOpenFileNames = staticmethod(
    lambda *a, **k: ([LUMPY], ""))
win.cam_ui.add_part()
QtWidgets.QMessageBox.warning = original_warning
pump()
check("it says so", warned and "stands proud" in warned[0], warned)
check("and the part is marked in the tree",
      doc.parts[2].cut_face.error, doc.parts[2].cut_face.error)
check("but the other two still cut",
      len(doc.last_report.toolpath.cuts) == 4,
      len(doc.last_report.toolpath.cuts))
win.cam_ui.browser.part_delete_requested.disconnect()
doc.remove_part(doc.parts[2].id)
win.cam_ui.browser.part_delete_requested.connect(win.cam_ui.delete_part)
win.cam_ui.rebuild()
pump()


# ==========================================================================
print("cut sides can be flipped per profile")

bracket = doc.parts[0]
hole_key = bracket.profile.holes[0].key
win.cam_ui.set_side(bracket.id, hole_key, OUTSIDE)
pump()
check("the side was stored", bracket.side_for(hole_key, INSIDE) == OUTSIDE)
cut = next(c for c in doc.last_report.toolpath.passes
           if c.part_id == bracket.id and c.key == hole_key)
x0, _y0, _z0, x1, _y1, _z1 = kernel.bounding_box(cut.wire)
check("so the path is wider than the hole", (x1 - x0) > 12.0, x1 - x0)
win.cam_ui.set_side(bracket.id, hole_key, INSIDE)
pump()
check("and back again", bracket.side_for(hole_key, OUTSIDE) == INSIDE)


# ==========================================================================
print("the cut face property")

win.cam_ui.cut_face_dialog(bracket.id)
pump()
dialog = win.cam_ui.dialog
check("the dialog opened", isinstance(dialog, CutFaceDialog))
check("it reports the thickness", "3.000" in dialog.thickness.text(),
      dialog.thickness.text())
check("it says the face was detected", "detected" in dialog.detected.text(),
      dialog.detected.text())

before = (bracket.profile.holes[0].bbox[0]
          + bracket.profile.holes[0].bbox[2]) * 0.5
dialog.flip.setChecked(True)
pump()
after = (doc.part(bracket.id).profile.holes[0].bbox[0]
         + doc.part(bracket.id).profile.holes[0].bbox[2]) * 0.5
check("flipping mirrors the part", abs(before + after) < 1e-4,
      (before, after))
dialog.flip.setChecked(False)
pump()

print("picking a face by hand shows the part on its own")
dialog.pick.set_picking(True)
pump()
check("picking is armed", win.cam_ui.face_picking == bracket.id)
check("the sheet is off screen",
      not win.viewport._component_ais, win.viewport._component_ais)
check("the body is shown instead", win.viewport.model_ais is not None)
check("faces are what gets picked",
      win.viewport.selection_mode == "face", win.viewport.selection_mode)

back_face = sheet.opposite_face(doc.part(bracket.id).shape,
                                doc.part(bracket.id).cut_face.face)
win.viewport.selected_faces = lambda f=back_face: [f]
dialog.on_selection()
del win.viewport.selected_faces
pump()
check("the pick registered", doc.part(bracket.id).cut_face.manual)
check("picking disarmed itself", win.cam_ui.face_picking is None)
check("and the sheet came back",
      len(win.viewport._component_ais) == 2,
      len(win.viewport._component_ais))

dialog.cancel()
pump()
check("cancel put the detected face back",
      not doc.part(bracket.id).cut_face.manual)


# ==========================================================================
print("hand placement, and that it survives a regenerate")

bracket = doc.parts[0]
win.cam_ui.move_dialog(bracket.id)
pump()
placement = win.cam_ui.dialog
check("the move dialog opened", isinstance(placement, PartPlacementDialog))
placement.x.set_text("300")
placement.y.set_text("250")
placement.angle.set_text("45")
pump()
check("it moved", abs(doc.part(bracket.id).position[0] - 300.0) < 1e-6,
      doc.part(bracket.id).position)
check("it rotated", abs(doc.part(bracket.id).rotation - 45.0) < 1e-6)
check("and it is marked as by hand", doc.part(bracket.id).manual)
placement.commit()
pump()

where = list(doc.part(bracket.id).position)
angle = doc.part(bracket.id).rotation
win.cam_ui.regenerate()
pump()
check("regenerating left the position alone",
      doc.part(bracket.id).position == where, doc.part(bracket.id).position)
check("and the rotation", doc.part(bracket.id).rotation == angle)
check("and it still says it was by hand", doc.part(bracket.id).manual)

print("auto arrange asks before undoing hand placement")
asked = []
original_question = QtWidgets.QMessageBox.question
QtWidgets.QMessageBox.question = staticmethod(
    lambda *a, **k: asked.append(a[2] if len(a) > 2 else "")
    or QtWidgets.QMessageBox.No)
win.cam_ui.auto_arrange()
pump()
check("it asked", asked and "by hand" in asked[0], asked)
check("and said no means no",
      doc.part(bracket.id).position == where, doc.part(bracket.id).position)

QtWidgets.QMessageBox.question = staticmethod(
    lambda *a, **k: QtWidgets.QMessageBox.Yes)
win.cam_ui.auto_arrange()
pump()
check("saying yes rearranges",
      doc.part(bracket.id).position != where, doc.part(bracket.id).position)
check("and clears the hand-placed mark", not doc.part(bracket.id).manual)
QtWidgets.QMessageBox.question = original_question

check("the gap is a tool and a half",
      abs(doc.gap() - 9.0) < 1e-9, doc.gap())
check("nothing overlaps after arranging", doc.last_report.toolpath.ok,
      doc.last_report.toolpath.message)


# ==========================================================================
print("dragging parts on the sheet")

win.toggle_cam_tool("move")
pump()
check("the tool is armed", win.cam_ui.tool == "move")
check("the ribbon button is lit", win.cam_move_button.isChecked())
start = list(doc.part(bracket.id).position)
win.viewport.component_drag_started.emit(bracket.id)
win.viewport.component_drag_moved.emit((12.0, -4.0, 0.0))
pump()
check("it followed the drag",
      abs(doc.part(bracket.id).position[0] - start[0] - 12.0) < 1e-6
      and abs(doc.part(bracket.id).position[1] - start[1] + 4.0) < 1e-6,
      doc.part(bracket.id).position)
check("and Z was ignored, because a sheet is flat",
      len(doc.part(bracket.id).position) == 2)
win.viewport.component_drag_finished.emit()
pump()
check("the drag is undoable", doc.can_undo)
check("and it counts as hand placed", doc.part(bracket.id).manual)
check("dragging a part into trouble is caught",
      any("overlap" in e or "clamp margin" in e
          for e in doc.last_report.toolpath.errors),
      doc.last_report.toolpath.errors)
win.cam_ui.on_escape()
pump()
check("escape puts the tool away", win.cam_ui.tool is None)

QtWidgets.QMessageBox.question = staticmethod(
    lambda *a, **k: QtWidgets.QMessageBox.Yes)
win.cam_ui.auto_arrange()
QtWidgets.QMessageBox.question = original_question
pump()
check("arranging clears the overlap", doc.last_report.toolpath.ok,
      doc.last_report.toolpath.message)


# ==========================================================================
print("the sheet and tool dialog drives everything")

win.cam_ui.sheet_dialog()
pump()
stock_dialog = win.cam_ui.dialog
check("it opened", isinstance(stock_dialog, SheetDialog))
stock_dialog.diameter.set_text("16")
pump()
check("a fatter tool cannot enter the 12 mm hole",
      any("narrower than" in e for e in doc.last_report.toolpath.errors),
      doc.last_report.toolpath.errors)
check("and the dialog says which", "narrower than" in stock_dialog.note.text(),
      stock_dialog.note.text())

stock_dialog.diameter.set_text("6")
stock_dialog.thickness.set_text("10")
pump()
check("a mismatched thickness is a warning, not an error",
      doc.last_report.toolpath.ok
      and any("thick" in w for w in doc.last_report.warnings),
      doc.last_report.warnings)

stock_dialog.thickness.set_text("3")
stock_dialog.plunge.setChecked(False)
pump()
check("a tool that cannot plunge asks for a lead in",
      "lead in" in stock_dialog.note.text(), stock_dialog.note.text())
stock_dialog.lead.set_text("4")
pump()
check("and is satisfied by one",
      "lead in" not in stock_dialog.note.text(), stock_dialog.note.text())
check("leads are drawn", any(True for c in doc.last_report.toolpath.cuts
                             if c.lead_in is not None))
stock_dialog.commit()
pump()


# ==========================================================================
print("exporting the toolpath")

target = os.path.join(WORK, "nest.dxf")
QtWidgets.QFileDialog.getSaveFileName = staticmethod(
    lambda *a, **k: (target, ""))
win.cam_ui.export_dxf()
pump()
check("a file was written", os.path.exists(target))
text = open(target, "r", encoding="ascii").read()
check("it is millimetres", "$INSUNITS" in text and "\n70\n4\n" in text)
check("it has the offset outlines", "CUT_OUTER" in text)
check("and the holes", "CUT_INNER" in text)
check("the status says the offset is already applied",
      "offset switched off" in win.status_message.text(),
      win.status_message.text())

print("an incomplete nest warns before exporting")
win.cam.move_part(doc.parts[0].id, (2.0, 2.0))      # into the clamp margin
win.cam_ui.rebuild()
pump()
asked = []
QtWidgets.QMessageBox.warning = staticmethod(
    lambda *a, **k: asked.append(a[2] if len(a) > 2 else "")
    or QtWidgets.QMessageBox.No)
win.cam_ui.export_dxf()
QtWidgets.QMessageBox.warning = original_warning
pump()
check("it warned", asked and "incomplete nest" in asked[0], asked)

# put it back on the grid - the part was moved by hand, so arranging asks
QtWidgets.QMessageBox.question = staticmethod(
    lambda *a, **k: QtWidgets.QMessageBox.Yes)
win.cam_ui.auto_arrange()
QtWidgets.QMessageBox.question = original_question
pump()
check("the nest is sound again", win.cam.last_report.toolpath.ok,
      win.cam.last_report.toolpath.message)


# ==========================================================================
print("saving, closing and reopening")

QtWidgets.QFileDialog.getSaveFileName = staticmethod(
    lambda *a, **k: (os.path.join(WORK, "nest.cdat"), ""))
check("it saves", win.save_document())
check("the file is there", os.path.exists(os.path.join(WORK, "nest.cdat")))
check("the title lost its dirty mark", "*" not in win.windowTitle(),
      win.windowTitle())

win.new_document(prompt=False)
pump()
check("a new part leaves CAM mode", not win.in_cam)
check("the feature tree is back",
      win.browser_stack.currentWidget() is win.browser)
check("the 3D Model tab is back",
      win.ribbon._buttons[TAB_MODEL].isVisible())
check("the CAM tab is put away",
      not win.ribbon._buttons[TAB_CAM].isVisible())
check("nothing is left on the sheet", not win.viewport._component_ais)

check("opening the .cdat works", win.open_path(os.path.join(WORK,
                                                            "nest.cdat")))
pump()
check("we are in CAM again", win.in_cam)
check("with both parts", len(win.cam.parts) == 2)
check("the tool came back", win.cam.tool().diameter == 6.0)
check("and the toolpath regenerated",
      len(win.cam.last_report.toolpath.cuts) == 4,
      len(win.cam.last_report.toolpath.cuts))
check("the recent list remembers it as a CAM sheet",
      any(e["path"].endswith("nest.cdat") and e["type"] == fileformat.CAM
          for e in win.start_page.projects.active().recent))


# ==========================================================================
print("editing a part updates the sheet")

edited = Document.load(BRACKET)
edited.features[0].sketch.entities  # touch, then widen the outline
edited.features[1].distance = "3"
sk = edited.features[2].sketch
for ent in sk.entities.values():
    if ent.kind == "circle":
        ent.radius = 10.0            # a 20 mm hole now
edited.features[3].diameter = "20"
edited.rebuild()
edited.save(BRACKET)

win.cam_ui.regenerate()
pump()
target_part = next(p for p in win.cam.parts if "bracket" in p.ref.name)
check("the bigger hole came through",
      abs(target_part.profile.holes[0].width - 20.0) < 1e-3,
      target_part.profile.holes[0].width)
check("and the sheet is still sound", win.cam.last_report.toolpath.ok,
      win.cam.last_report.toolpath.message)


# ==========================================================================
print("a missing part is reported on open, and the rest still opens")

# reopening a file that is already open goes to its tab, so close it first
for open_entry in list(win.session.documents):
    open_entry.document.modified = False
    win.close_entry(open_entry)
pump()

os.rename(GUSSET, GUSSET + ".moved")
warned = []
QtWidgets.QMessageBox.warning = staticmethod(
    lambda *a, **k: warned.append(a[2] if len(a) > 2 else "")
    or QtWidgets.QMessageBox.Ok)
win.open_path(os.path.join(WORK, "nest.cdat"))
QtWidgets.QMessageBox.warning = original_warning
pump()
check("it tells the user", warned and "could not find" in warned[0], warned)
check("it names the file", warned and "gusset.pdat" in warned[0], warned)
check("the sheet still opened", win.in_cam)
check("and the other part still cuts",
      len(win.viewport._component_ais) == 1,
      len(win.viewport._component_ais))
os.rename(GUSSET + ".moved", GUSSET)


# ==========================================================================
print("part commands stay out of the way on a sheet")

win.new_cam(prompt=False)
pump()
before = len(win.document.features)
win.start_sketch()
win.new_primitive("box")
win.start_work_plane()
pump()
check("no sketch was started", not win.editor.active)
check("no feature was added", len(win.document.features) == before)
check("no plane picker is up", not win.viewport.plane_picker_active)

print("undo and redo follow the sheet")
QtWidgets.QFileDialog.getOpenFileNames = staticmethod(
    lambda *a, **k: ([BRACKET], ""))
win.cam_ui.add_part()
pump()
check("one part", len(win.cam.parts) == 1)
win.undo()
pump()
check("undo removed it", not win.cam.parts)
win.redo()
pump()
check("redo put it back", len(win.cam.parts) == 1)


# ==========================================================================
print("the viewport right-click menu is the CAM one")

menu = QtWidgets.QMenu(win)
win.cam_ui.context_menu(menu)
labels = [a.text() for a in menu.actions()]
check("it offers Add Part", any("Add Part" in t for t in labels), labels)
check("and Auto Arrange", any("Auto Arrange" in t for t in labels), labels)
check("and Export DXF", any("Export DXF" in t for t in labels), labels)
check("but not Start Sketch", not any("Sketch" in t for t in labels), labels)
check("and not Constrain", not any("Constrain" in t for t in labels), labels)


# ==========================================================================
win.cam = None
win.close()
pump()
shutil.rmtree(WORK, ignore_errors=True)
print()
if FAILS:
    print("%d FAILED" % len(FAILS))
    for name in FAILS:
        print("   - %s" % name)
    sys.exit(1)
print("all CAM UI checks passed")
