"""Work planes built off model faces, and the drag-off gesture."""
import math
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
    ExtrudeFeature, SketchFeature, WorkPlaneFeature, plane_from_face,
)
from datum.core.naming import RefSet  # noqa: E402
from datum.core.sketch import STANDARD_PLANES, Sketch  # noqa: E402
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


def near(a, b, tol=1e-4):
    return abs(a - b) < tol


def make_box(a="60", b="40", c="20"):
    win.new_document(prompt=False)
    win.new_primitive("box")
    d = win._active_dialog
    d.a.set_text(a)
    d.b.set_text(b)
    d.c.set_text(c)
    d.commit()
    pump()


def top_face():
    return max(kernel.faces(win.document.shape),
               key=lambda f: kernel.shape_centre(f)[2])


def side_face():
    return max(kernel.faces(win.document.shape),
               key=lambda f: kernel.shape_centre(f)[0])


# ==========================================================================
print("a work plane can sit on a model face")
make_box()
face = top_face()
refs = RefSet()
refs.capture_from(win.document.shape, "face", [face])

feature = WorkPlaneFeature()
feature.face_ref = refs.refs[0]
feature.offset = "15"
win.document.add_feature(feature)
report = win.rebuild()
check("built off a face", report.ok, report.message)
check("labelled as a face", feature.base_label == "model face",
      feature.base_label)

plane = win.document.planes.get(feature.name)
check("plane registered", plane is not None, list(win.document.planes))
check("plane sits 15mm above the top face", near(plane.origin[2], 35.0),
      plane.origin)
check("plane normal follows the face", near(abs(plane.normal[2]), 1.0),
      plane.normal)

print("the offset is parametric")
win.document.params.add("gap", "25")
feature.offset = "gap"
report = win.rebuild()
plane = win.document.planes[feature.name]
check("expression drives the offset", near(plane.origin[2], 45.0),
      plane.origin)
win.document.params.set_expression("gap", "5")
win.rebuild()
check("changing the parameter moves the plane",
      near(win.document.planes[feature.name].origin[2], 25.0),
      win.document.planes[feature.name].origin)

print("flip reverses the offset")
feature.flip = True
win.rebuild()
check("flipped offset goes the other way",
      near(win.document.planes[feature.name].origin[2], 15.0),
      win.document.planes[feature.name].origin)
feature.flip = False
win.rebuild()

print("the plane follows the face when the model changes")
box = win.document.features[0]
box.c = "50"
report = win.rebuild()
check("rebuild after resizing the box", report.ok, report.message)
check("plane tracked the moved face",
      near(win.document.planes[feature.name].origin[2], 55.0),
      win.document.planes[feature.name].origin)

print("a side face gives a vertical plane")
make_box()
refs = RefSet()
refs.capture_from(win.document.shape, "face", [side_face()])
vertical = WorkPlaneFeature()
vertical.face_ref = refs.refs[0]
vertical.offset = "10"
win.document.add_feature(vertical)
win.rebuild()
plane = win.document.planes[vertical.name]
check("vertical plane normal points along X", near(abs(plane.normal[0]), 1.0),
      plane.normal)
check("vertical plane offset 10mm past the face",
      near(plane.origin[0], 70.0), plane.origin)

print("sketching on a work plane")
sketch = SketchFeature()
sketch.name = "On Work Plane"
sketch.sketch = Sketch(plane, "On Work Plane")
sketch.sketch.add_circle((0, 0), 8)
win.document.add_feature(sketch)
extrude = ExtrudeFeature()
extrude.sketch_id = sketch.id
extrude.distance = "12"
# New Body: a separate solid in the same part, not a replacement for the
# one already there
extrude.operation = "new"
win.document.add_feature(extrude)
report = win.rebuild()
check("extruded from the work plane", report.ok, report.message)
check("it became a second body", len(win.document.bodies) == 2,
      len(win.document.bodies))
check("which is a cylinder off the work plane",
      near(kernel.volume(win.document.bodies[-1].shape),
           math.pi * 64 * 12, 3.0),
      kernel.volume(win.document.bodies[-1].shape))

print("a lost face warns instead of taking the tree down")
vertical.face_ref.centre = (9999.0, 9999.0, 9999.0)
report = win.rebuild()
check("a warning is raised",
      any("check the plane" in w for w in report.warnings), report.warnings)
check("the rest of the tree still built", win.document.shape is not None)

# ==========================================================================
print("axis drag maths")
make_box()
win.viewport.set_view("iso")
pump()

origin = (0.0, 0.0, 20.0)
normal = (0.0, 0.0, 1.0)
win.viewport.begin_axis_drag(origin, normal)
check("drag reports active", win.viewport.axis_dragging)

# project a known point on the axis back to the screen, then ask what
# distance that pixel maps to - it must come back where it started
for expected in (0.0, 12.0, -8.0, 30.0):
    world = tuple(origin[i] + normal[i] * expected for i in range(3))
    px, py = win.viewport.project(world)
    got = win.viewport.axis_distance(int(px), int(py))
    check("pixel at %+.0fmm maps back" % expected,
          got is not None and near(got, expected, 0.35),
          "got %s" % got)

win.viewport.end_axis_drag()
check("drag cleared", not win.viewport.axis_dragging)
check("no distance once the drag ended",
      win.viewport.axis_distance(100, 100) is None)

# ==========================================================================
print("the pick-and-drag gesture end to end")
make_box()
win.start_work_plane()
pump()
check("plane tool armed", win._plane_tool_active)
check("viewport is in plane-tool mode", win.viewport.plane_tool)
check("datum planes offered as bases", win.viewport.plane_picker_active)

# stand in for pressing on the top face
face = top_face()
win.viewport.selected_faces = lambda f=face: [f]
win._plane_tool_pressed()
del win.viewport.selected_faces
pump()
check("base captured from the face", win._plane_base is not None)
check("axis drag started", win.viewport.axis_dragging)
check("picker hidden once the drag begins",
      not win.viewport.plane_picker_active)

win._plane_drag_moved(18.0)
# read both straight away: pumping lets a real mouse move over the window
# through, and that is another drag update which would overwrite them
live_status = win.status_message.text()
live_preview = len(win.viewport._preview)
pump()
check("preview drawn while dragging", live_preview >= 2, live_preview)
check("status shows the live offset", "18.00" in live_status, live_status)

before = len(win.document.features)
win.viewport.axis_drag_finished.emit(18.0)
pump()
check("plane created on release", len(win.document.features) == before + 1)
check("dialog opened for fine tuning", win._active_dialog is not None)

dialog = win._active_dialog
feature = dialog.feature
check("offset came from the drag", near(float(feature.offset), 18.0, 0.05),
      feature.offset)
check("it is a face-based plane", feature.face_ref is not None)
check("dialog says so", dialog.base_label.text() == "Model face",
      dialog.base_label.text())
check("datum combo disabled for a face base", not dialog.base.isEnabled())

dialog.offset.set_text("22.5")
dialog.commit()
pump()
plane = win.document.planes.get(feature.name)
check("typed offset overrides the drag", near(plane.origin[2], 42.5),
      plane.origin)
check("tool mode ended", not win._plane_tool_active
      and not win.viewport.plane_tool)

print("a plain click without dragging still works")
make_box()
win.start_work_plane()
pump()
face = top_face()
win.viewport.selected_faces = lambda f=face: [f]
win._plane_tool_pressed()
del win.viewport.selected_faces
win.viewport.axis_drag_finished.emit(0.0)
pump()
check("click-only gets a default offset",
      near(float(win._active_dialog.feature.offset), 10.0, 0.01),
      win._active_dialog.feature.offset)
win._active_dialog.cancel()
pump()
check("cancel removed the plane",
      not any(isinstance(f, WorkPlaneFeature) for f in win.document.features))

print("Esc cancels the tool cleanly")
win.start_work_plane()
pump()
win._on_escape()
pump()
check("tool disarmed", not win._plane_tool_active)
check("viewport back to normal", not win.viewport.plane_tool
      and not win.viewport.plane_picker_active)

print("work planes are offered when starting a sketch")
make_box()
refs = RefSet()
refs.capture_from(win.document.shape, "face", [top_face()])
wp = WorkPlaneFeature()
wp.face_ref = refs.refs[0]
wp.offset = "12"
win.document.add_feature(wp)
win.rebuild()
win.start_sketch()
pump()
check("the work plane shows up in the picker",
      wp.name in win.viewport._plane_picker,
      list(win.viewport._plane_picker))
win._on_escape()
pump()

print("save / load keeps the face reference")
path = os.path.join(tempfile.mkdtemp(prefix="forge_wp_"), "p.pdat")
path = win.document.save(path)
reloaded = Document.load(path)
restored = [f for f in reloaded.features if isinstance(f, WorkPlaneFeature)][0]
check("face reference survives a round trip", restored.face_ref is not None)
check("reloaded plane lands in the same place",
      near(reloaded.planes[restored.name].origin[2],
           win.document.planes[wp.name].origin[2]),
      reloaded.planes[restored.name].origin)

print()
if FAILS:
    print("%d FAILURES: %s" % (len(FAILS), ", ".join(FAILS)))
    sys.exit(1)
print("all work plane tests passed")
sys.exit(0)
