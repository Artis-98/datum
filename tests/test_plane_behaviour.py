"""Origin planes standing down after the first body, and pickable planes."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402,F401


from PySide6 import QtCore, QtWidgets  # noqa: E402

from datum.core import kernel  # noqa: E402
from datum.core.document import Document  # noqa: E402
from datum.core.features import (  # noqa: E402
    ExtrudeFeature, SketchFeature, WorkPlaneFeature,
)
from datum.core.naming import RefSet  # noqa: E402
from datum.ui.main_window import MainWindow  # noqa: E402
from datum.ui.theme import stylesheet  # noqa: E402

FAILS = []
app = QtWidgets.QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(stylesheet())
win = MainWindow()
win.resize(1400, 880)
win.show()
app.processEvents()


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def pump(n=3):
    for _ in range(n):
        app.processEvents()


def shown():
    return set(win.viewport._plane_display)


def click(u, v):
    win.editor._on_move(u, v, QtCore.Qt.NoModifier)
    win.editor._on_click(u, v, QtCore.Qt.NoModifier)
    pump(1)


def top_face():
    return max(kernel.faces(win.document.shape),
               key=lambda f: kernel.shape_centre(f)[2])


def pick_at(point3d):
    """Click a world point through the real OCCT picking path."""
    # camera moves are animated, so settle before projecting to pixels
    win.viewport.finish_animation()
    pump(1)
    px, py = win.viewport.project(point3d)
    win.viewport.context.MoveTo(int(px), int(py), win.viewport.view, False)
    detected = win.viewport.context.HasDetected()
    win.viewport.context.SelectDetected()
    win.viewport.view.Redraw()
    return detected


# ==========================================================================
print("origin planes stand down once the part has a body")
win.new_document(prompt=False)
pump()
check("all three visible on a new part", shown() == {"XY", "XZ", "YZ"}, shown())
check("auto-hide not fired yet", not win.document.origin_autohidden)

win.start_sketch_on_plane("XY")
pump()
win.set_sketch_tool("rect")
click(-30, -20)
click(30, 20)
win.finish_sketch()
pump()
# the first sketch is what retires the origin planes; waiting for a body
# made them flicker back between finishing a sketch and using it
check("auto-hide fired on the first sketch", win.document.origin_autohidden)
check("gone as soon as the sketch is finished", shown() == set(), shown())
check("no body yet", win.document.shape is None)

win.new_feature(ExtrudeFeature)
win.select_all_profiles()
dlg = win._active_dialog
pump()
check("and they stay gone while a feature dialog is open",
      shown() == set(), shown())
dlg.distance.set_text("12")
dlg.commit()
pump()
check("a body now exists", win.document.shape is not None)
check("still gone after the feature is made", shown() == set(), shown())
check("recorded as hidden",
      {"XY", "XZ", "YZ"} <= win.document.hidden_planes,
      win.document.hidden_planes)

print("the user stays in charge afterwards")
win.toggle_plane_visibility("XZ")
pump()
check("XZ can be turned back on", "XZ" in shown(), shown())
win.rebuild()
pump()
check("a later rebuild does not re-hide it", "XZ" in shown(), shown())

print("auto-hide only ever happens once")
win.toggle_plane_visibility("XY")
pump()
check("XY back on too", {"XY", "XZ"} <= shown(), shown())
win.new_primitive("box")
win._active_dialog.commit()
pump()
check("adding more geometry leaves them alone", {"XY", "XZ"} <= shown(),
      shown())

print("undo puts them back")
win.new_document(prompt=False)
win.start_sketch_on_plane("XY")
win.set_sketch_tool("rect")
click(-20, -20)
click(20, 20)
win.finish_sketch()
pump()
win.new_feature(ExtrudeFeature)
win.select_all_profiles()
win._active_dialog.distance.set_text("10")
win._active_dialog.commit()
pump()
check("hidden after the extrude", shown() == set(), shown())
win.undo()
pump()
check("undoing the extrude leaves them hidden - the sketch remains",
      shown() == set(), shown())
check("flag still set while a sketch exists", win.document.origin_autohidden)

for _ in range(4):
    if not win.document.sketch_features():
        break
    win.undo()
    pump()
check("undoing back past the sketch brings them back",
      shown() == {"XY", "XZ", "YZ"}, shown())
check("and clears the flag", not win.document.origin_autohidden)

print("the flag is saved with the part")
win.redo()
pump()
path = os.path.join(tempfile.mkdtemp(prefix="forge_autohide_"), "p.pdat")
path = win.document.save(path)
reloaded = Document.load(path)
check("flag round-trips", reloaded.origin_autohidden)
check("hidden set round-trips",
      {"XY", "XZ", "YZ"} <= reloaded.hidden_planes, reloaded.hidden_planes)

# ==========================================================================
print("a work plane behaves like a face")
win.new_document(prompt=False)
win.new_primitive("box")
d = win._active_dialog
d.a.set_text("60")
d.b.set_text("40")
d.c.set_text("20")
d.commit()
pump()

refs = RefSet()
refs.capture_from(win.document.shape, "face", [top_face()])
wp = WorkPlaneFeature()
wp.face_ref = refs.refs[0]
wp.offset = "25"
win.document.add_feature(wp)
win.rebuild()
pump()
check("work plane is on screen", wp.name in shown(), shown())

ais = win.viewport._plane_display[wp.name]
check("it is a real displayed object", ais is not None)

win.viewport.set_view("iso")
pump()
plane = win.document.planes[wp.name]
detected = pick_at(plane.origin)
check("hovering the plane detects it", detected)
check("clicking it identifies the plane",
      win.viewport.picked_plane() == wp.name, win.viewport.picked_plane())

print("but a plane never leaks into face selection")
check("selected_faces ignores it", win.viewport.selected_faces() == [],
      len(win.viewport.selected_faces()))
check("selected_shapes ignores it too",
      win.viewport.selected_shapes() == [],
      len(win.viewport.selected_shapes()))

print("its right-click menu offers the right things")
menu = QtWidgets.QMenu(win)
win._add_plane_menu(menu, wp.name)
labels = [a.text() for a in menu.actions()]
check("offers a sketch", any("New Sketch on" in t for t in labels), labels)
check("offers visibility", "Visible" in labels, labels)
check("offers edit", "Edit Plane" in labels, labels)
check("offers delete", "Delete Plane" in labels, labels)
visible_action = [a for a in menu.actions() if a.text() == "Visible"][0]
check("visibility action is checkable and ticked",
      visible_action.isCheckable() and visible_action.isChecked())

print("sketching on it from the menu works")
win.viewport.clear_selection()
win.start_sketch_on_plane(wp.name)
pump()
check("sketch mode entered", win.editor.active)
check("on the work plane",
      abs(win.editor.sketch.plane.origin[2] - 45.0) < 1e-4,
      win.editor.sketch.plane.origin)
check("planes cleared while sketching", shown() == set(), shown())
win.finish_sketch()
pump()
check("plane returns after finishing", wp.name in shown(), shown())

print("origin planes are pickable in the same way")
win.toggle_plane_visibility("XZ")
pump()
check("XZ shown again", "XZ" in shown(), shown())
win.viewport.clear_selection()
# aim at a corner of the sheet, clear of the box - the plane's own origin
# sits inside the solid, where the body would win the pick
xz = win.document.planes["XZ"]
detected = pick_at(xz.to_3d(-40.0, -30.0))
picked = win.viewport.picked_plane()
check("an origin plane can be picked too", picked == "XZ",
      "detected=%s picked=%s" % (detected, picked))
win.viewport.clear_selection()

print("model faces still pick cleanly")
win.toggle_plane_visibility("XZ")
win.toggle_plane_visibility(wp.name)
pump()
check("planes off", shown() == set(), shown())
face = top_face()
centre = kernel.shape_centre(face)
detected = pick_at(centre)
faces = win.viewport.selected_faces()
check("a model face is selected", len(faces) == 1,
      "detected=%s faces=%d at %s -> px %s"
      % (detected, len(faces), centre, win.viewport.project(centre)))
check("nothing is mistaken for a plane",
      win.viewport.picked_plane() is None, win.viewport.picked_plane())
win.viewport.clear_selection()

print()
if FAILS:
    print("%d FAILURES: %s" % (len(FAILS), ", ".join(FAILS)))
    sys.exit(1)
print("all plane behaviour tests passed")
sys.exit(0)
