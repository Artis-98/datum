"""Five fixes from the board, cards #111 to #115.

#111 a projected circle keeps its radius, like any other projection;
#112 a feature's profile picking offers the sketches in sight, not every
     sketch in the part, but always the ones the feature itself uses;
#113 finishing a sketch goes back to the view it was entered from;
#114 dProperties and Open File Location from the tree and the File menu;
#115 two concentric circles dimension across the ring between them.
"""
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WORK = tempfile.mkdtemp(prefix="datum_batch2_")
os.environ["DATUM_CONFIG_DIR"] = os.path.join(WORK, "cfg")
os.environ["DATUM_DOCUMENTS"] = os.path.join(WORK, "Documents")

import harness  # noqa: E402,F401

from PySide6 import QtWidgets  # noqa: E402
from OCP.GeomAbs import GeomAbs_CurveType  # noqa: E402
from OCP.BRepAdaptor import BRepAdaptor_Curve  # noqa: E402
from OCP.TopAbs import TopAbs_EDGE  # noqa: E402
from OCP.TopoDS import TopoDS  # noqa: E402

from datum.core import kernel  # noqa: E402
from datum.core.features import (ExtrudeFeature, PrimitiveFeature,  # noqa
                                 SketchFeature)
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


def same_view(a, b):
    """Looking the same way, the same way up, at about the same place.

    Direction and up exactly; where it is centred and how far zoomed in
    within a couple of percent, which is all a resize or a refit between
    the two moments leaves.
    """
    if a is None or b is None:
        return False

    def unit(v):
        n = math.sqrt(sum(c * c for c in v)) or 1.0
        return [c / n for c in v]

    look_a = unit([a[1][i] - a[0][i] for i in range(3)])
    look_b = unit([b[1][i] - b[0][i] for i in range(3)])
    same_way = sum(p * q for p, q in zip(look_a, look_b)) > 0.9999
    same_up = sum(p * q for p, q in zip(unit(a[2]), unit(b[2]))) > 0.9999
    scale = max(a[3], b[3], 1e-9)
    near = math.dist(a[1], b[1]) < 0.02 * scale
    zoom = abs(a[3] - b[3]) < 0.02 * scale
    return same_way and same_up and near and zoom


pump()
win.new_document()
pump()


print("#113 and #115: a sketch on XY, entered from the front view")
win.viewport.set_view("front")
pump()
before = win.viewport.camera_state()
win.start_sketch_on_plane("XY")
win.viewport.finish_animation()
pump()
editor = win.editor
sketch = editor.sketch
check("the sketch is open, looking down on it",
      editor.active and not same_view(win.viewport.camera_state(), before))

inner = sketch.add_circle((0.0, 0.0), 10.0)
outer = sketch.add_circle((0.0, 0.0), 25.0)
editor._pending = [("entity", outer), ("entity", inner)]
target = editor._dimension_target()
check("two concentric circles offer the gap between them",
      target and target["kind"] == "radial_gap"
      and target["entities"] == [inner, outer]
      and abs(target["current"] - 15.0) < 1e-9, target)
cid = editor._add_dimension("radial_gap", [], target["entities"],
                            target["current"], "20")
gap = sketch.entities[outer].radius - sketch.entities[inner].radius
check("  and setting it to 20 moves them 20 apart, inner still inside",
      cid is not None and abs(gap - 20.0) < 1e-6, gap)
check("  as a dimension with a name of its own",
      cid is not None and sketch.constraints[cid].name.startswith("d"),
      cid is not None and sketch.constraints[cid].name)
editor._render_dimension(sketch.constraints[cid])
pump()

apart = sketch.add_circle((60.0, 0.0), 5.0)
editor._pending = [("entity", inner), ("entity", apart)]
target = editor._dimension_target()
check("two circles apart measure centre to centre",
      target and target["kind"] == "distance"
      and abs(target["current"] - 60.0) < 1e-6, target)
line = sketch.add_line((-50.0, -40.0), (100.0, -40.0))
editor._pending = [("entity", apart), ("entity", line)]
target = editor._dimension_target()
check("a circle and a line measure from its centre square onto the line",
      target and target["kind"] == "distance_pl"
      and abs(abs(target["current"]) - 40.0) < 1e-6, target)
editor._pending = []

win.finish_sketch()
win.viewport.finish_animation()
pump()
check("#113 finishing the sketch goes back to the view it came from",
      same_view(win.viewport.camera_state(), before),
      (win.viewport.camera_state(), before))


print()
print("#111: a projected circle keeps its radius")
win.new_document()
pump()
post = PrimitiveFeature()
post.kind = "cylinder"
post.a, post.b, post.c = "12", "20", "0"
post.operation = "new"
win.document.add_feature(post)
win.rebuild(keep_camera=False)
pump()
top = None
for edge in kernel.explore(win.document.shape, TopAbs_EDGE):
    curve = BRepAdaptor_Curve(TopoDS.Edge_s(edge))
    if curve.GetType() == GeomAbs_CurveType.GeomAbs_Circle and \
            abs(curve.Value(curve.FirstParameter()).Z() - 20.0) < 1e-6:
        top = edge
        break
win.start_sketch_on_plane("XY")
win.viewport.finish_animation()
pump()
editor = win.editor
sketch = editor.sketch
made = editor.project_one(top, body=win.document.shape)
circles = [e for e in sketch.entities.values() if e.kind == "circle"]
check("the circular edge projects as one circle",
      made == 1 and len(circles) == 1
      and abs(circles[0].radius - 12.0) < 1e-6, (made, len(circles)))
editor.solve()
check("  with nothing left free on it", sketch.dof == 0, sketch.dof)
pulled = sketch.add_circle((40.0, 0.0), 5.0)
sketch.add_constraint("tangent", entities=[circles[0].id, pulled])
sketch.add_constraint("distance", points=[circles[0].points[0],
                                          sketch.entities[pulled].points[0]],
                      value=30.0)
editor.solve()
check("  a constraint pulling on it moves the drawn circle, not the shadow",
      abs(circles[0].radius - 12.0) < 1e-6
      and abs(sketch.entities[pulled].radius - 18.0) < 1e-6,
      (circles[0].radius, sketch.entities[pulled].radius))
win.finish_sketch()
win.viewport.finish_animation()
pump()


print()
print("#112: profiles from the sketches in sight")
win.new_document()
pump()
win.start_sketch_on_plane("XY")
win.viewport.finish_animation()
first_sketch = win._sketch_feature_id
win.editor.sketch.add_rectangle((0.0, 0.0), (40.0, 30.0)) \
    if hasattr(win.editor.sketch, "add_rectangle") else None
if not any(e.kind == "line" for e in win.editor.sketch.entities.values()):
    s = win.editor.sketch
    corners = [(0.0, 0.0), (40.0, 0.0), (40.0, 30.0), (0.0, 30.0)]
    for i in range(4):
        s.add_line(corners[i], corners[(i + 1) % 4])
win.finish_sketch()
win.viewport.finish_animation()
pump()
block = ExtrudeFeature()
block.sketch_id = first_sketch
block.distance = "10"
block.operation = "new"
win.document.add_feature(block)
for region in win.available_regions():
    block.profiles.add(region["sketch_id"], region["centre"]) \
        if hasattr(block.profiles, "add") else None
win.rebuild()
pump()
win.start_sketch_on_plane("XZ")
win.viewport.finish_animation()
second_sketch = win._sketch_feature_id
win.editor.sketch.add_circle((20.0, 50.0), 8.0)
win.finish_sketch()
win.viewport.finish_animation()
pump()
check("the first sketch is tucked away under the extrude that used it",
      win.browser.is_sketch_hidden(first_sketch, win.document))


class _Asking:
    def __init__(self, feature):
        self.feature = feature


win._active_dialog = _Asking(ExtrudeFeature())
offered = {r["sketch_id"] for r in win.available_regions()}
check("a new feature is offered only the sketch in sight",
      offered == {second_sketch}, offered)
win._active_dialog = _Asking(block)
offered = {r["sketch_id"] for r in win.available_regions()}
check("  editing the extrude still offers the sketch it already uses",
      first_sketch in offered, offered)
win._active_dialog = None


print()
print("#114: dProperties and Open File Location")
part = os.path.join(WORK, "Bracket.pdat")
win.document.path = part
win.save_document()
pump()
menu = win.browser.menu_for(win.browser.topLevelItem(0))
seen = [a.text() for a in menu.actions()] if menu is not None else []
check("right-clicking the part offers dProperties and Open File Location",
      "dProperties..." in seen and "Open File Location" in seen, seen)
where = next(a for a in menu.actions() if a.text() == "Open File Location")
check("  Open File Location is live once the part has a file",
      where.isEnabled())
labels = [a.text().split("\t")[0] for a in win.file_menu.actions()]
check("  and the File menu has dProperties", "dProperties..." in labels,
      labels)
revealed = []
win._reveal = revealed.append
win.open_file_location(win.document.path)
check("Open File Location shows the part's own file",
      revealed and os.path.normcase(revealed[0])
      == os.path.normcase(os.path.abspath(part)), revealed)
win.show_properties()
pump()
check("dProperties opens the properties window",
      getattr(win, "properties_window", None) is not None
      and win.properties_window.isVisible())
win.properties_window.hide()

print()
print("FAILED: " + ", ".join(FAILS) if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
