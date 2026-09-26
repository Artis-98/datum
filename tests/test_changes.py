"""Checks for the Inventor-alignment pass: sweep, loft, multi-edge picking,
the End-of-Part marker, the plane picker and the rearranged ribbon."""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402,F401


from PySide6 import QtCore, QtWidgets  # noqa: E402

from datum.core import kernel  # noqa: E402
from datum.core.features import (  # noqa: E402
    ChamferFeature, ExtrudeFeature, FilletFeature, LoftFeature,
    PatternFeature, SketchFeature, SweepFeature,
)
from datum.core.sketch import STANDARD_PLANES, Sketch, SketchPlane  # noqa: E402
from datum.ui.browser import ROLE_KIND  # noqa: E402
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


def volume():
    return kernel.volume(win.document.shape) if win.document.shape else 0.0


def add_sketch(plane, name, build):
    f = SketchFeature()
    f.sketch = Sketch(plane, name)
    build(f.sketch)
    win.document.add_feature(f)
    f.sketch.name = f.name
    win.rebuild()
    return f


# ==========================================================================
print("ribbon layout")
tabs = [win.ribbon.stack.widget(i).title
        for i in range(win.ribbon.stack.count())]
check("tab order matches Inventor",
      tabs == ["3D Model", "Sketch", "Assemble", "CAM", "Drawing",
               "Inspect", "Manage", "View"], tabs)
check("a part hides the assembly and CAM tabs",
      not win.ribbon._buttons["Assemble"].isVisible()
      and not win.ribbon._buttons["CAM"].isVisible())

model_tab = win.ribbon._tabs["3D Model"]
panels = [p.title for p in model_tab.panels]
# Return is last and apart from the rest on purpose: it is not a
# modelling command, it is the way back out of an in-place edit, and it
# is hidden unless one is going on.
check("3D Model holds the feature panels, then Return",
      panels == ["Sketch", "Create", "Modify", "Work Features", "Pattern",
                 "Return"], panels)
check("no File panel on 3D Model", "File" not in panels)
check("no Edit panel on 3D Model", "Edit" not in panels)

check("File button exists", win.ribbon.file_button.text() == "File")
check("File button has a menu", win.ribbon.file_button.menu() is not None)
labels = [a.text().split("\t")[0] for a in win.file_menu.actions()
          if not a.isSeparator()]
check("File menu has the document commands",
      all(x in labels for x in ("New...", "Open...", "Save", "Save As...",
                                "Import...", "Export...", "Exit")), labels)
qat_buttons = [win.ribbon.quick_access.itemAt(i).widget()
               for i in range(win.ribbon.quick_access.count())]
qat_buttons = [b for b in qat_buttons
               if isinstance(b, QtWidgets.QToolButton)]
check("quick access toolbar populated", len(qat_buttons) == 10,
      len(qat_buttons))
check("Local Update is one of them, and dark until it is needed",
      win.qat_update is not None and not win.qat_update.isEnabled())
check("quick access sits on its own row above the tabs",
      win.ribbon.quick_access.parent() is win.ribbon.qat_bar.layout()
      or win.ribbon.qat_bar.isAncestorOf(qat_buttons[0]),
      "parent=%s" % qat_buttons[0].parent())
check("qat row is above the tab strip",
      win.ribbon.qat_bar.y() < win.ribbon.strip.y(),
      "%d vs %d" % (win.ribbon.qat_bar.y(), win.ribbon.strip.y()))

# ==========================================================================
print("plane picker on Start Sketch")
win.new_document(prompt=False)
win.start_sketch()
pump()
check("three datum planes offered",
      len(win.viewport._plane_picker) == 3, list(win.viewport._plane_picker))
check("picker is flagged active", win.viewport.plane_picker_active)
win._on_escape()
pump()
check("Esc clears the picker", not win.viewport.plane_picker_active)

win.start_sketch()
pump()
win._pending_plane_pick = False
win.viewport.hide_plane_picker()
win.start_sketch_on_plane("XZ")
pump()
check("picking a plane enters sketch mode", win.editor.active)
check("sketch is on the chosen plane",
      win.editor.sketch.plane.name == "XZ Plane",
      win.editor.sketch.plane.name)
win.finish_sketch()

# ==========================================================================
print("sweep")
win.new_document(prompt=False)
add_sketch(STANDARD_PLANES["XY"], "Profile",
           lambda s: s.add_circle((0, 0), 4))
add_sketch(STANDARD_PLANES["XZ"], "Path",
           lambda s: s.add_line((0, 0), (0, 60), weld=False))

win.new_feature(SweepFeature)   # the profile is picked explicitly below
dlg = win._active_dialog
check("sweep dialog opened", dlg is not None)
sketches = win.document.sketch_features()
# the profile is picked as a region now, not chosen from a sketch list
profile_regions = [r for r in win.available_regions()
                   if r["sketch_id"] == sketches[0].id]
for region in profile_regions:
    dlg.on_profile_clicked(region["sketch_id"], region["centre"])
dlg.path.setCurrentIndex(dlg.path.findData(sketches[1].id))
dlg.preview()
pump()
expect = math.pi * 16 * 60
check("swept a 60mm circular rod", abs(volume() - expect) < 12.0,
      "%.1f vs %.1f  (%s)" % (volume(), expect, dlg.status.text()))
dlg.commit()
pump()

print("loft")
win.new_document(prompt=False)
add_sketch(STANDARD_PLANES["XY"], "Bottom",
           lambda s: s.add_rectangle((-20, -20), (20, 20)))
add_sketch(SketchPlane((0, 0, 40), (0, 0, 1), (1, 0, 0), "Top"), "Top",
           lambda s: s.add_rectangle((-10, -10), (10, 10)))

win.new_feature(LoftFeature)
dlg = win._active_dialog
for i in range(dlg.sections.count()):
    dlg.sections.item(i).setCheckState(QtCore.Qt.Checked)
dlg.preview()
pump()
# frustum volume = h/3 * (A1 + A2 + sqrt(A1*A2))
expect = 40.0 / 3.0 * (1600 + 400 + math.sqrt(1600 * 400))
check("lofted a square frustum", abs(volume() - expect) < 30.0,
      "%.1f vs %.1f  (%s)" % (volume(), expect, dlg.status.text()))
dlg.commit()
pump()

# ==========================================================================
print("multi-edge fillet and chamfer picking")
win.new_document(prompt=False)
win.new_primitive("box")
dlg = win._active_dialog
dlg.a.set_text("50")
dlg.b.set_text("50")
dlg.c.set_text("20")
dlg.commit()
pump()
box_volume = volume()

win.new_feature(FilletFeature)
dlg = win._active_dialog
pump()
verticals = [e for e in kernel.edges(win.document.shape)
             if abs(kernel.edge_length(e) - 20) < 0.01]
check("box has 4 vertical edges", len(verticals) == 4, len(verticals))


def click_edge(edge):
    """Drive one pick through the same path a mouse click takes.

    The stub yields the edge once and then goes empty, mirroring what the
    real viewport does when the handler clears the selection.
    """
    vp = win.viewport
    remaining = [edge]

    def stub():
        return [remaining.pop()] if remaining else []

    vp.selected_edges = stub
    win._on_viewport_selection()
    pump(1)
    del vp.selected_edges


# a real projected click first, to prove picking itself still works
win.viewport.set_view("iso")
pump()
probe = verticals[0]
px, py = win.viewport.project(kernel.shape_centre(probe))
win.viewport.context.MoveTo(int(px), int(py), win.viewport.view, False)
detected = win.viewport.context.HasDetected()
check("an edge is detectable under the cursor", detected)
win.viewport.clear_selection()

# then feed the edges in one at a time, as clicking each would
for i, edge in enumerate(verticals):
    click_edge(edge)
    check("pick %d accumulated" % (i + 1), len(dlg.feature.refs) == i + 1,
          len(dlg.feature.refs))

dlg.size.set_text("6")
dlg.preview()
pump()
check("all four fillets applied",
      abs(volume() - (box_volume - 4 * (36 - math.pi * 9) * 20)) < 1.0,
      volume())
dlg.commit()
pump()
filleted = volume()

win.new_feature(ChamferFeature)
dlg = win._active_dialog
pump()
top_edges = [e for e in kernel.edges(win.document.shape)
             if abs(kernel.shape_centre(e)[2] - 20) < 0.01]
check("top face has edges to chamfer", len(top_edges) >= 3, len(top_edges))
for edge in top_edges[:3]:
    click_edge(edge)
check("chamfer took three edges", len(dlg.feature.refs) == 3,
      len(dlg.feature.refs))

# clicking a picked edge again removes it
click_edge(top_edges[0])
check("clicking again deselects", len(dlg.feature.refs) == 2,
      len(dlg.feature.refs))
dlg.size.set_text("2")
dlg.preview()
pump()
check("chamfer previewed on two edges", dlg.ok_button.isEnabled(),
      dlg.status.text())
dlg.commit()
pump()
check("chamfer removed material", volume() < filleted)

# ==========================================================================
print("End of Part marker")
win.new_document(prompt=False)
win.new_primitive("box")
win._active_dialog.commit()
pump()
win.new_primitive("cylinder")
d = win._active_dialog
d.operation.set_value("join")
d.commit()
pump()
both = volume()


def marker_row():
    root = win.browser.topLevelItem(0)
    for i in range(root.childCount()):
        if root.child(i).data(0, ROLE_KIND) == "end":
            return i
    return -1


root = win.browser.topLevelItem(0)
check("marker sits last when rolled to the end",
      marker_row() == root.childCount() - 1, marker_row())

win.set_rollback(1)
pump()
check("rollback shows only the box", volume() < both, volume())
root = win.browser.topLevelItem(0)
rows = [root.child(i).data(0, ROLE_KIND) for i in range(root.childCount())]
check("marker moved up the tree", rows.index("end") < len(rows) - 1, rows)
check("marker sits after exactly one feature",
      rows[:rows.index("end")].count("feature") == 1, rows)

win.browser.rollback_requested.emit(None)
pump()
check("dropping the marker at the end restores the model",
      abs(volume() - both) < 1.0, volume())

print("feature reordering")
ids = [f.id for f in win.document.features]
win.reorder_feature(ids[1], 0)
pump()
check("feature moved to the front",
      win.document.features[0].id == ids[1],
      [f.name for f in win.document.features])

print("browser drag plumbing")
check("browser accepts drops", win.browser.acceptDrops())
check("browser drag enabled", win.browser.dragEnabled())

# ==========================================================================
print("pattern presets from the ribbon")
win.new_document(prompt=False)
win.new_primitive("box")
win._active_dialog.commit()
pump()
win.new_feature(PatternFeature, mode="circular")
dlg = win._active_dialog
check("circular preset applied", dlg.feature.mode == "circular")
check("preset names the feature", "Circular" in dlg.feature.name,
      dlg.feature.name)
dlg.cancel()
pump()

print()
if FAILS:
    print("%d FAILURES: %s" % (len(FAILS), ", ".join(FAILS)))
    sys.exit(1)
print("all change tests passed")
sys.exit(0)
