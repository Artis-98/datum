"""The assembly workspace driven through a real MainWindow.

Everything here goes through the same objects the user's clicks reach: the
start page, the ribbon, the assembly browser and the constraint dialog.
Where a real mouse pick cannot be simulated headlessly the viewport's own
selection call is stubbed, never the handler under test.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402,F401


# keep the test run out of the real recent-files list
os.environ["DATUM_SETTINGS_ORG"] = "IITEG-tests"

from PySide6 import QtCore, QtWidgets                              # noqa: E402

from OCP.TopLoc import TopLoc_Location                             # noqa: E402

from datum.core import constraints3d, fileformat, kernel           # noqa: E402
from datum.core.assembly import AssemblyDocument                   # noqa: E402
from datum.core.constraints3d import FLUSH, INSERT, MATE           # noqa: E402
from datum.core.document import Document                           # noqa: E402
from datum.core.features import PrimitiveFeature                   # noqa: E402
from datum.ui.assembly_ui import ConstraintDialog, PlacementDialog  # noqa: E402
from datum.ui.main_window import (                                 # noqa: E402
    ASSEMBLY_TABS, TAB_ASSEMBLE, TAB_MODEL, MainWindow,
)
from datum.ui.theme import stylesheet                              # noqa: E402

FAILS = []
WORK = tempfile.mkdtemp(prefix="datum_asmui_")

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


def make_part(kind, a, b, c, name):
    doc = Document()
    feature = PrimitiveFeature()
    feature.kind = kind
    feature.a, feature.b, feature.c = str(a), str(b), str(c)
    doc.add_feature(feature)
    doc.rebuild()
    return doc.save(os.path.join(WORK, name))


PLATE = make_part("box", 60, 40, 8, "plate")
PEG = make_part("cylinder", 5, 25, 0, "peg")


def stub_pick(pairs):
    """Pretend the user clicked these (occurrence, local sub-shape) pairs.

    The sub-shapes are handed over located where the component sits, exactly
    as OCCT returns them from a real pick, so the dialog's own code has to do
    the work of stripping the placement back off.
    """
    doc = win.assembly
    located = []
    for occurrence_id, shape in pairs:
        occurrence = doc.occurrence(occurrence_id)
        located.append((occurrence_id,
                        shape.Moved(occurrence.placement.location())))
    win.viewport.selected_component_shapes = lambda: located


def clear_pick():
    win.viewport.selected_component_shapes = lambda: []


def occ(index):
    """Always re-read from the document.

    Cancelling a dialog restores a snapshot, which builds fresh occurrence
    objects; anything holding one from before is looking at a ghost.  The
    application itself never does - it looks components up by id - so the
    test should not either.
    """
    return win.assembly.occurrences[index]


# ==========================================================================
print("the start page offers assemblies for real now")

win.show_start_page()
pump()
check("the Assembly tile is live", win.start_page.assembly_tile.isEnabled())
check("its tooltip is no longer an apology",
      "no assembly editor" not in win.start_page.assembly_tile.toolTip(),
      win.start_page.assembly_tile.toolTip())
check("so is the Drawing tile", win.start_page.drawing_tile.isEnabled())

win.start_page.new_requested.emit(fileformat.ASSEMBLY)
pump()
check("a new assembly opens the modeller", not win.on_start_page)
check("and the window knows it is an assembly", win.in_assembly)
check("the document is an AssemblyDocument",
      isinstance(win.assembly, AssemblyDocument))


# ==========================================================================
print("the workspace swaps over")

check("the Assemble tab is showing",
      win.ribbon.current_tab() == TAB_ASSEMBLE, win.ribbon.current_tab())
check("the 3D Model tab is put away",
      not win.ribbon._buttons[TAB_MODEL].isVisible())
check("every assembly tab is offered",
      all(win.ribbon._buttons[t].isVisible() for t in ASSEMBLY_TABS))
check("the browser shows the component tree",
      win.browser_stack.currentWidget() is win.assembly_ui.browser)
check("the dock is retitled", win.browser_dock.windowTitle() == "Assembly",
      win.browser_dock.windowTitle())
check("the part body is off screen", win.viewport.model_ais is None)
check("the title says Assembly1", "Assembly1" in win.windowTitle(),
      win.windowTitle())
check("an empty assembly says so",
      "Empty assembly" in win.assembly.last_report.message,
      win.assembly.last_report.message)


# ==========================================================================
print("placing components")

QtWidgets.QFileDialog.getOpenFileNames = staticmethod(
    lambda *a, **k: ([PLATE, PEG], ""))
win.assembly_ui.place_component()
pump()

doc = win.assembly
check("both landed", len(doc.occurrences) == 2, len(doc.occurrences))
check("the first is grounded", doc.occurrences[0].grounded)
check("the second is free", not doc.occurrences[1].grounded)
check("they are not on top of each other",
      doc.occurrences[1].placement.position[0] > 60,
      doc.occurrences[1].placement.position)
check("both are drawn", len(win.viewport._component_ais) == 2,
      len(win.viewport._component_ais))
check("each has its own tint",
      win.viewport.component_colour(0) != win.viewport.component_colour(1))
check("the tree lists them", win.assembly_ui.browser.topLevelItem(0).childCount()
      >= 3)
check("the document is dirty", doc.modified)

print("an assembly refuses to place itself")
doc.path = os.path.join(WORK, "rig.adat")
warned = []
original_warning = QtWidgets.QMessageBox.warning
QtWidgets.QMessageBox.warning = staticmethod(
    lambda *a, **k: warned.append(a[2] if len(a) > 2 else "")
    or QtWidgets.QMessageBox.Ok)
QtWidgets.QFileDialog.getOpenFileNames = staticmethod(
    lambda *a, **k: ([doc.path], ""))
win.assembly_ui.place_component()
QtWidgets.QMessageBox.warning = original_warning
pump()
check("it says no", warned and "cannot place itself" in warned[0], warned)
check("and nothing was added", len(doc.occurrences) == 2)


# ==========================================================================
print("the constraint dialog, picked through the viewport")

plate, peg = doc.occurrences
win.assembly_ui.constrain(MATE)
pump()
dialog = win.assembly_ui.dialog
check("a constraint dialog opened", isinstance(dialog, ConstraintDialog))
check("the first field is armed straight away", dialog.first.picking)
check("the viewport is picking faces and edges",
      win.viewport.selection_mode == "assembly", win.viewport.selection_mode)
check("OK is off until both picks are made", not dialog.ok_button.isEnabled())

top = max(kernel.faces(plate.shape),
          key=lambda f: kernel.shape_centre(f)[2])
bottom = min(kernel.faces(peg.shape),
             key=lambda f: kernel.shape_centre(f)[2])

stub_pick([(plate.id, top)])
dialog.on_selection()
pump()
check("the first pick registered", dialog.constraint.a.valid)
check("it kept a plane", dialog.constraint.a.frame.kind == "plane",
      dialog.constraint.a.frame.kind)
check("the frame is in part coordinates",
      abs(dialog.constraint.a.frame.origin[2] - 8.0) < 1e-6,
      dialog.constraint.a.frame.origin)
check("focus moved to the second field", dialog.second.picking)

print("both picks on one component are refused")
stub_pick([(plate.id, bottom)])
dialog.on_selection()
pump()
check("it explains why", "same component" in dialog.status.text(),
      dialog.status.text())
check("and the second pick is still empty", not dialog.constraint.b.valid)

stub_pick([(peg.id, bottom)])
dialog.on_selection()
pump()
clear_pick()
check("the second pick registered", dialog.constraint.b.valid)
check("OK is live now", dialog.ok_button.isEnabled())
check("the constraint is already in the document",
      len(doc.constraints) == 1, len(doc.constraints))

seat = peg.placement.apply_point(dialog.constraint.b.frame.origin)
check("and the peg has already moved onto the plate",
      abs(seat[2] - 8.0) < 1e-4, seat)
check("the status says what happened",
      "DOF" in dialog.status.text() or "constrained" in dialog.status.text(),
      dialog.status.text())

print("the offset drives the gap live")
dialog.offset.set_text("12")
pump()
seat = peg.placement.apply_point(dialog.constraint.b.frame.origin)
check("the peg floats 12 above", abs(seat[2] - 20.0) < 1e-4, seat)

dialog.offset.set_text("0")
pump()


# ==========================================================================
print("Mate and Flush are one type with two solutions, as Inventor has it")

check("Flush is not offered as its own type",
      "flush" not in constraints3d.TYPES, constraints3d.TYPES)
check("the Type row says Mate", dialog.kind.value() == MATE,
      dialog.kind.value())
check("the Solution row is shown for a mate", dialog.solution.isVisible())
check("and starts on Mate", dialog.solution.value() == MATE,
      dialog.solution.value())

print("an arrow is drawn out of each picked face")
arrows = win.assembly_ui._arrows
check("one per pick", len(arrows) == 2, len(arrows))
frames = [doc.world_frame(a) for a, _colour in arrows]
check("both resolve in world space", all(f is not None for f in frames))
dot = sum(frames[0].direction[i] * frames[1].direction[i] for i in range(3))
check("a mate leaves them pointing at each other", dot < -0.99, dot)

print("switching the solution to Flush turns the second face round")
dialog.solution.set_value(FLUSH)
dialog._solution_changed(FLUSH)
pump()
check("the constraint became a flush", dialog.constraint.kind == FLUSH,
      dialog.constraint.kind)
check("the Type row still says Mate", dialog.kind.value() == MATE,
      dialog.kind.value())
frames = [doc.world_frame(a) for a, _colour in win.assembly_ui._arrows]
dot = sum(frames[0].direction[i] * frames[1].direction[i] for i in range(3))
check("and now they point the same way", dot > 0.99, dot)

print("flush measures the offset the same way")
dialog.offset.set_text("5")
pump()
seat = peg.placement.apply_point(dialog.constraint.b.frame.origin)
base = plate.placement.apply_point(dialog.constraint.a.frame.origin)
check("5mm apart along the shared normal",
      abs((seat[2] - base[2]) - 5.0) < 1e-4, (base[2], seat[2]))

dialog.solution.set_value(MATE)
dialog._solution_changed(MATE)
dialog.offset.set_text("0")
pump()
check("back to a mate", dialog.constraint.kind == MATE)
dialog.commit()
pump()
check("the dialog closed", win.assembly_ui.dialog is None)
check("the constraint stayed", len(doc.constraints) == 1)
check("it is named Mate:1", doc.constraints[0].name == "Mate:1",
      doc.constraints[0].name)
# Closing a dialog used to drop the viewport to picking faces.  That left
# components unpickable, so a right-click could not tell which one it was
# over and the menu lost Open Part, Suppress and Delete.  Picking whole
# components is the resting state with an assembly open.
check("it goes back to picking components, not faces",
      win.viewport.selection_mode == "assembly",
      win.viewport.selection_mode)
check("the tree nests it under both parts",
      win.assembly_ui.browser.topLevelItem(0).child(1).childCount() == 1
      and win.assembly_ui.browser.topLevelItem(0).child(2).childCount() == 1)

print("the ribbon's Flush button opens a mate on the flush solution")
win.assembly_ui.constrain(FLUSH)
pump()
flush_dialog = win.assembly_ui.dialog
check("the Type row shows Mate", flush_dialog.kind.value() == MATE,
      flush_dialog.kind.value())
check("with Flush already chosen", flush_dialog.solution.value() == FLUSH,
      flush_dialog.solution.value())
flush_dialog.cancel()
pump()
check("and cancelling added nothing", len(doc.constraints) == 1,
      len(doc.constraints))


# ==========================================================================
print("cancelling a constraint leaves nothing behind")

before = len(doc.constraints)
where = list(peg.placement.position)
win.assembly_ui.constrain(INSERT)
pump()
dialog = win.assembly_ui.dialog
rim = [e for e in kernel.edges(peg.shape)
       if constraints3d.frame_from_shape(e)
       and constraints3d.frame_from_shape(e).kind == "circle"]
stub_pick([(plate.id, top)])
dialog.on_selection()
stub_pick([(peg.id, rim[0])])
dialog.on_selection()
pump()
clear_pick()
check("insert refuses a plane against a circle",
      not dialog.ok_button.isEnabled(), dialog.status.text())
check("and says so plainly", "does not apply" in dialog.status.text(),
      dialog.status.text())
dialog.cancel()
pump()
check("nothing was added", len(doc.constraints) == before)
check("and the peg is where it was",
      all(abs(a - b) < 1e-6 for a, b in zip(peg.placement.position, where)),
      peg.placement.position)


# ==========================================================================
print("free move drags a component, free rotate spins it")

doc = win.assembly
plate_id, peg_id = occ(0).id, occ(1).id

win.toggle_component_tool("move")
pump()
check("the tool is armed", win.assembly_ui.tool == "move")
check("the ribbon button is lit", win.move_button.isChecked())
check("the viewport is in move mode",
      win.viewport.component_tool == "move", win.viewport.component_tool)

start = list(occ(1).placement.position)
win.viewport.component_drag_started.emit(peg_id)
win.viewport.component_drag_moved.emit((5.0, 0.0, 0.0))
pump()
check("it followed the drag",
      abs(occ(1).placement.position[0] - start[0] - 5.0) < 1e-6,
      occ(1).placement.position)
win.viewport.component_drag_finished.emit()
pump()
seat = occ(1).placement.apply_point(doc.constraints[0].b.frame.origin)
check("but the solve pulls it back onto the face",
      abs(seat[2] - 8.0) < 1e-4, seat)
check("the drag is undoable", doc.can_undo)

win.toggle_component_tool("move")
pump()
check("pressing it again disarms", win.assembly_ui.tool is None)
check("and the button clears", not win.move_button.isChecked())

win.toggle_component_tool("rotate")
pump()
turned = list(occ(1).placement.rotation)
win.viewport.component_drag_started.emit(peg_id)
win.viewport.component_drag_moved.emit((0.0, 0.0, 0.4))
pump()
check("it turned", any(abs(a - b) > 1e-6
                       for a, b in zip(occ(1).placement.rotation, turned)),
      occ(1).placement.rotation)
win.viewport.component_drag_finished.emit()
pump()
win.assembly_ui.on_escape()
pump()
check("escape puts the tool away", win.assembly_ui.tool is None)


# ==========================================================================
print("grounding, hiding and suppressing")

win.assembly_ui.toggle_ground(peg_id)
pump()
check("the peg is grounded now", occ(1).grounded)
check("the status says so", "grounded" in win.status_message.text(),
      win.status_message.text())
win.assembly_ui.toggle_ground(peg_id)
pump()
check("and ungrounded again", not occ(1).grounded)

win.assembly_ui.toggle_visibility(peg_id)
pump()
check("hidden components leave the view",
      len(win.viewport._component_ais) == 1, len(win.viewport._component_ais))
check("but stay in the tree", len(doc.occurrences) == 2)
win.assembly_ui.toggle_visibility(peg_id)
pump()
check("and come back", len(win.viewport._component_ais) == 2)

win.assembly_ui.isolate(plate_id)
pump()
check("isolate shows one", len(win.viewport._component_ais) == 1)
win.assembly_ui.isolate(plate_id)
pump()
check("and restores on the second call",
      len(win.viewport._component_ais) == 2)

win.assembly_ui.toggle_suppress(peg_id)
pump()
check("a suppressed component is not placed",
      win.assembly.last_report.placed == 1, win.assembly.last_report.placed)
check("its constraint is marked",
      doc.constraints[0].error, doc.constraints[0].error)
win.assembly_ui.toggle_suppress(peg_id)
pump()
check("unsuppressing clears the error", not doc.constraints[0].error,
      doc.constraints[0].error)


# ==========================================================================
print("the exact placement dialog")

win.assembly_ui.place_dialog(plate_id)
pump()
placement = win.assembly_ui.dialog
check("it opened", isinstance(placement, PlacementDialog))
check("it warns that grounded means grounded",
      "grounded" in placement.note.text(), placement.note.text())
placement.fields[0].set_text("15")
pump()
check("the plate moved", abs(occ(0).placement.position[0] - 15.0) < 1e-6,
      occ(0).placement.position)
placement.cancel()
pump()
check("cancel puts it back", abs(occ(0).placement.position[0]) < 1e-6,
      occ(0).placement.position)


# ==========================================================================
print("saving, closing and reopening")

QtWidgets.QFileDialog.getSaveFileName = staticmethod(
    lambda *a, **k: (os.path.join(WORK, "rig.adat"), ""))
check("it saves", win.save_document())
check("the file is there", os.path.exists(os.path.join(WORK, "rig.adat")))
check("the title lost its dirty mark", "*" not in win.windowTitle(),
      win.windowTitle())
check("it carries a thumbnail",
      fileformat.thumbnail_of(os.path.join(WORK, "rig.adat")) is not None)

win.new_document(prompt=False)
pump()
check("a new part leaves assembly mode", not win.in_assembly)
check("the feature tree is back",
      win.browser_stack.currentWidget() is win.browser)
check("the 3D Model tab is back",
      win.ribbon._buttons[TAB_MODEL].isVisible())
check("no components are left on screen",
      not win.viewport._component_ais, win.viewport._component_ais)

check("opening the .adat works", win.open_path(os.path.join(WORK, "rig.adat")))
pump()
check("we are in an assembly again", win.in_assembly)
check("with both components", len(win.assembly.occurrences) == 2)
check("and the constraint", len(win.assembly.constraints) == 1)
reopened = win.assembly.occurrences[1]
seat = reopened.placement.apply_point(win.assembly.constraints[0].b.frame.origin)
check("still seated on the plate", abs(seat[2] - 8.0) < 1e-4, seat)
check("the recent list remembers it as an assembly",
      any(e["path"].endswith("rig.adat") and e["type"] == fileformat.ASSEMBLY
          for e in win.start_page.projects.active().recent))


# ==========================================================================
print("a missing component is reported on open, and the rest still opens")

# reopening a file that is already open goes to its tab, so close it first
for open_entry in list(win.session.documents):
    open_entry.document.modified = False
    win.close_entry(open_entry)
pump()

os.rename(PEG, PEG + ".moved")
warned = []
QtWidgets.QMessageBox.warning = staticmethod(
    lambda *a, **k: warned.append(a[2] if len(a) > 2 else "")
    or QtWidgets.QMessageBox.Ok)
win.open_path(os.path.join(WORK, "rig.adat"))
QtWidgets.QMessageBox.warning = original_warning
pump()
check("it tells the user", warned and "could not find" in warned[0], warned)
check("it names the file", warned and "peg.pdat" in warned[0], warned)
check("the assembly still opened", win.in_assembly)
check("and the plate is still drawn",
      len(win.viewport._component_ais) == 1, len(win.viewport._component_ais))
os.rename(PEG + ".moved", PEG)


# ==========================================================================
print("part commands stay out of the way in an assembly")

win.new_assembly(prompt=False)
pump()
before = len(win.document.features)
win.start_sketch()
win.new_primitive("box")
win.start_work_plane()
pump()
check("no sketch was started", not win.editor.active)
check("no feature was added", len(win.document.features) == before,
      len(win.document.features))
check("and no plane picker is up", not win.viewport.plane_picker_active)

print("undo and redo follow the assembly")
QtWidgets.QFileDialog.getOpenFileNames = staticmethod(
    lambda *a, **k: ([PLATE], ""))
win.assembly_ui.place_component()
pump()
check("one component", len(win.assembly.occurrences) == 1)
win.undo()
pump()
check("undo removed it", not win.assembly.occurrences)
win.redo()
pump()
check("redo put it back", len(win.assembly.occurrences) == 1)


# ==========================================================================
print("the viewport right-click menu is the assembly one")

menu = QtWidgets.QMenu(win)
win.assembly_ui.context_menu(menu)
labels = [a.text() for a in menu.actions()]
check("it offers Place Component",
      any("Place Component" in t for t in labels), labels)
check("and Constrain", any("Constrain" in t for t in labels), labels)
check("but not Start Sketch", not any("Sketch" in t for t in labels), labels)


# ==========================================================================
import shutil                                                      # noqa: E402

win.assembly = None
win.close()
pump()
shutil.rmtree(WORK, ignore_errors=True)
print()
if FAILS:
    print("%d FAILED" % len(FAILS))
    for name in FAILS:
        print("   - %s" % name)
    sys.exit(1)
print("all assembly UI checks passed")
