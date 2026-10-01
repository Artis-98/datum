"""A sketch starts facing the side it was looked at from.

A flat face's own axis points whichever way its surface was made, into
the part as often as out of it, and a sketch on it used to take that side:
the view swung round behind the part. Now a new sketch on a face faces the
camera and keeps facing that way through rebuilds, a sketch on an origin
plane seen from below is drawn from below, and files made before any of
this read exactly as they did.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WORK = tempfile.mkdtemp(prefix="datum_sketch_side_")
os.environ["DATUM_CONFIG_DIR"] = os.path.join(WORK, "cfg")
os.environ["DATUM_DOCUMENTS"] = os.path.join(WORK, "Documents")

import harness  # noqa: E402,F401

from PySide6 import QtWidgets  # noqa: E402
from OCP.TopAbs import TopAbs_REVERSED  # noqa: E402

from datum.core import kernel  # noqa: E402
from datum.core.features import (PrimitiveFeature, SketchFeature,  # noqa
                                 plane_from_face)
from datum.ui.main_window import MainWindow  # noqa: E402
from datum.ui.theme import stylesheet  # noqa: E402

FAILS = []
app = QtWidgets.QApplication(sys.argv)
app.setStyleSheet(stylesheet())
win = MainWindow()
win.resize(1300, 850)
win.show()


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def pump(n=8):
    for _ in range(n):
        app.processEvents()


def toward_eye():
    camera = win.viewport.view.Camera()
    eye, centre = camera.Eye(), camera.Center()
    return (eye.X() - centre.X(), eye.Y() - centre.Y(), eye.Z() - centre.Z())


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def outward(face):
    n = plane_from_face(face).normal
    return tuple(-c for c in n) if face.Orientation() == TopAbs_REVERSED \
        else n


def sketch_from_outside(face):
    """Look at a face square-on from outside the part, and sketch on it."""
    out = outward(face)
    # the swing back from the last sketch, done, as any view command does
    # before it turns the camera
    win.viewport.finish_animation()
    win.viewport.view.SetProj(*out)
    win.viewport.fit_all()
    pump()
    win.sketch_on_face(face)
    win.viewport.finish_animation()
    pump()
    return win.document.feature(win._sketch_feature_id), out


pump()
win.new_document()
pump()
block = PrimitiveFeature()
block.kind = "box"
block.a, block.b, block.c = "40", "30", "20"
block.operation = "new"
win.document.add_feature(block)
win.rebuild(keep_camera=False)
pump()
faces = [f for f in kernel.faces(win.document.shape)
         if plane_from_face(f) is not None]
inward = next(f for f in faces if f.Orientation() == TopAbs_REVERSED)
outwards = next(f for f in faces if f.Orientation() != TopAbs_REVERSED)
check("the box has faces whose own axis points into it",
      dot(plane_from_face(inward).normal, outward(inward)) < 0)


print("a face whose axis points into the part")
sketch, out = sketch_from_outside(inward)
check("the sketch faces the side it was looked at from",
      dot(sketch.sketch.plane.normal, out) > 0.999,
      sketch.sketch.plane.normal)
check("  and the view stays on that side, not behind the part",
      dot(toward_eye(), out) > 0, toward_eye())
check("  remembering it faces the other way from the face's axis",
      sketch.reverse_face and sketch.to_dict().get("reverse_face") is True)
win.finish_sketch()
pump()
win.rebuild()
check("a rebuild keeps it facing that way",
      dot(sketch.sketch.plane.normal, out) > 0.999,
      sketch.sketch.plane.normal)
again = SketchFeature()
again.load_fields(sketch.to_dict())
check("  and so does the file", again.reverse_face)


print()
print("a face whose axis already points out")
sketch, out = sketch_from_outside(outwards)
check("the sketch faces out, as it always did",
      dot(sketch.sketch.plane.normal, out) > 0.999
      and not sketch.reverse_face)
check("  and says nothing new in the file",
      "reverse_face" not in sketch.to_dict())
check("  the view stays on the side it was on", dot(toward_eye(), out) > 0)
win.finish_sketch()
pump()


print()
print("an origin plane looked at from below")
win.viewport.set_view("bottom")
pump()
win.start_sketch_on_plane("XY")
win.viewport.finish_animation()
pump()
check("is drawn from below, not swung round to the top",
      toward_eye()[2] < 0, toward_eye())
plane = win.document.feature(win._sketch_feature_id).sketch.plane
check("  while the plane itself is still the XY plane",
      dot(plane.normal, (0.0, 0.0, 1.0)) > 0.999, plane.normal)
win.finish_sketch()
pump()

old = SketchFeature()
data = sketch.to_dict()
data.pop("reverse_face", None)
old.load_fields(data)
check("a sketch from an older file is not turned round", not old.reverse_face)

print()
print("FAILED: " + ", ".join(FAILS) if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
