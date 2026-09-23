"""Plane visibility, click-to-sketch on planes, and face-first picking."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402,F401


from PySide6 import QtCore, QtWidgets  # noqa: E402

from datum.core import kernel  # noqa: E402
from datum.core.document import Document  # noqa: E402
from datum.core.features import WorkPlaneFeature  # noqa: E402
from datum.core.naming import RefSet  # noqa: E402
from datum.ui.browser import (  # noqa: E402
    ROLE_ID, ROLE_KIND, ROLE_VISIBLE,
)
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


def make_box(c="20"):
    """A box, with the origin planes put back on.

    Creating a body auto-hides them (see test_plane_behaviour.py); this suite
    is about the manual controls, so it starts from everything visible.
    """
    win.new_document(prompt=False)
    win.new_primitive("box")
    d = win._active_dialog
    d.a.set_text("60")
    d.b.set_text("40")
    d.c.set_text(c)
    d.commit()
    pump()
    for key in ("XY", "XZ", "YZ"):
        if not win.document.plane_visible(key):
            win.toggle_plane_visibility(key)
    pump()


def top_face():
    return max(kernel.faces(win.document.shape),
               key=lambda f: kernel.shape_centre(f)[2])


def add_work_plane(offset="15"):
    refs = RefSet()
    refs.capture_from(win.document.shape, "face", [top_face()])
    wp = WorkPlaneFeature()
    wp.face_ref = refs.refs[0]
    wp.offset = offset
    win.document.add_feature(wp)
    win.rebuild()
    return wp


def plane_row(key):
    it = QtWidgets.QTreeWidgetItemIterator(win.browser)
    while it.value():
        item = it.value()
        if item.data(0, ROLE_KIND) == "plane" and item.data(0, ROLE_ID) == key:
            return item
        it += 1
    return None


# ==========================================================================
print("a new part shows all three origin planes")
win.new_document(prompt=False)
pump()
check("nothing hidden on a new part", win.document.hidden_planes == set(),
      win.document.hidden_planes)
check("all three drawn", shown() == {"XY", "XZ", "YZ"}, shown())
for key in ("XY", "XZ", "YZ"):
    check("%s reports visible" % key, win.document.plane_visible(key))

print("planes can be hidden and shown again")
win.toggle_plane_visibility("XZ")
pump()
check("XZ hidden in the model", not win.document.plane_visible("XZ"))
check("XZ removed from the view", shown() == {"XY", "YZ"}, shown())
row = plane_row("XZ")
check("browser marks it hidden", row is not None
      and row.data(0, ROLE_VISIBLE) is False)
check("browser labels it", "(hidden)" in row.text(0), row.text(0))

win.toggle_plane_visibility("XZ")
pump()
check("XZ visible again", win.document.plane_visible("XZ"))
check("XZ back in the view", shown() == {"XY", "XZ", "YZ"}, shown())
check("browser label restored", "(hidden)" not in plane_row("XZ").text(0))

print("a user-created plane is visible until hidden")
make_box()
pump()
wp = add_work_plane()
pump()
check("work plane drawn straight away", wp.name in shown(), shown())
win.toggle_plane_visibility(wp.name)
pump()
check("work plane hides", wp.name not in shown(), shown())
check("origin planes unaffected", {"XY", "XZ", "YZ"} <= shown(), shown())
win.toggle_plane_visibility(wp.name)
pump()
check("work plane comes back", wp.name in shown(), shown())

print("a rebuild does not disturb visibility")
win.document.params.add("t", "30")
win.document.features[0].c = "t"
win.rebuild()
pump()
check("still drawn after a rebuild", wp.name in shown(), shown())
win.toggle_plane_visibility("XY")
win.rebuild()
pump()
check("hidden plane stays hidden across a rebuild", "XY" not in shown(),
      shown())
win.toggle_plane_visibility("XY")
pump()

print("sketch mode clears every plane")
win.start_sketch_on_plane("XY")
pump()
check("no planes while sketching", shown() == set(), shown())
win.finish_sketch()
pump()
check("planes return afterwards", {"XY", "XZ", "YZ", wp.name} <= shown(),
      shown())

print("the plane picker takes over, then hands back")
win.start_sketch()
pump()
check("picker is up", win.viewport.plane_picker_active)
check("persistent sheets stood down", shown() == set(), shown())
win._on_escape()
pump()
check("picker gone", not win.viewport.plane_picker_active)
check("persistent sheets restored", {"XY", "XZ", "YZ"} <= shown(), shown())

print("visibility is saved with the part")
win.toggle_plane_visibility("YZ")
win.toggle_plane_visibility(wp.name)
path = os.path.join(tempfile.mkdtemp(prefix="forge_vis_"), "p.pdat")
path = win.document.save(path)
reloaded = Document.load(path)
check("hidden set round-trips",
      reloaded.hidden_planes == {"YZ", wp.name}, reloaded.hidden_planes)
check("visible_planes filters correctly",
      "YZ" not in reloaded.visible_planes()
      and "XY" in reloaded.visible_planes(),
      list(reloaded.visible_planes()))
win.toggle_plane_visibility("YZ")
win.toggle_plane_visibility(wp.name)

# ==========================================================================
print("opening a plane from the tree starts a sketch")
make_box()
wp = add_work_plane("18")
pump()

started = []
win.browser.sketch_on_plane_requested.connect(started.append)

row = plane_row("XZ")
win.browser._double_clicked(row, 0)
pump()
check("double-clicking a datum plane opens a sketch", win.editor.active)
check("on the right plane", win.editor.sketch.plane.name == "XZ Plane",
      win.editor.sketch.plane.name)
win.finish_sketch()
pump()

wp_row = None
it = QtWidgets.QTreeWidgetItemIterator(win.browser)
while it.value():
    item = it.value()
    if (item.data(0, ROLE_KIND) == "feature"
            and item.data(0, ROLE_ID) == wp.id):
        wp_row = item
        break
    it += 1
check("work plane has a row", wp_row is not None)

win.browser._double_clicked(wp_row, 0)
pump()
check("double-clicking a work plane asks for a sketch", started == [wp.name],
      started)
check("sketch mode entered", win.editor.active)
check("sketch sits on the work plane",
      abs(win.editor.sketch.plane.origin[2] - 38.0) < 1e-4,
      win.editor.sketch.plane.origin)
win.finish_sketch()
pump()

# ==========================================================================
print("faces are the resting selection mode")
make_box()
pump()
check("viewport filters to faces", win.viewport.selection_mode == "face",
      win.viewport.selection_mode)
win.set_pick_mode("edge")
check("a dialog can switch to edges", win.viewport.selection_mode == "edge")
win.set_pick_mode(None)
check("and it falls back to faces, not the whole body",
      win.viewport.selection_mode == "face", win.viewport.selection_mode)

win.viewport.set_view("iso")
pump()
face = top_face()
px, py = win.viewport.project(kernel.shape_centre(face))
win.viewport.context.MoveTo(int(px), int(py), win.viewport.view, False)
check("hovering the body detects something",
      win.viewport.context.HasDetected())
win.viewport.context.SelectDetected()
picked = win.viewport.selected_shapes()
check("what it picks is a face, not the solid",
      len(picked) == 1 and len(kernel.faces(picked[0])) == 1,
      "%d shape(s)" % len(picked))
win.viewport.clear_selection()

print("right-clicking a face offers a sketch")
win.viewport.selected_faces = lambda f=face: [f]
win._sketch_on_selected_face()
pump()
del win.viewport.selected_faces
check("sketch started from the face", win.editor.active)
check("plane matches the face height",
      abs(win.editor.sketch.plane.origin[2] - 20.0) < 1e-4,
      win.editor.sketch.plane.origin)
win.finish_sketch()
pump()

print("right-clicking a face can also make a work plane")
before = len([f for f in win.document.features
              if isinstance(f, WorkPlaneFeature)])
win.viewport.selected_faces = lambda f=top_face(): [f]
win._plane_from_selected_face()
pump()
# the helper clears the stub itself, and hasattr is no use here because the
# real method always exists on the class
win.viewport.__dict__.pop("selected_faces", None)
check("a work plane dialog opened", win._active_dialog is not None)
if win._active_dialog is not None:
    check("it is face-based", win._active_dialog.feature.face_ref is not None)
    win._active_dialog.commit()
    pump()
after = len([f for f in win.document.features
             if isinstance(f, WorkPlaneFeature)])
check("work plane added", after == before + 1, "%d -> %d" % (before, after))

print()
if FAILS:
    print("%d FAILURES: %s" % (len(FAILS), ", ".join(FAILS)))
    sys.exit(1)
print("all plane visibility tests passed")
sys.exit(0)
