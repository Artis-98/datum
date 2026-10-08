"""Planar region decomposition: overlapping curves give selectable areas."""
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
    ExtrudeFeature, ProfileSelection, SketchFeature,
)
from datum.core.sketch import STANDARD_PLANES, Sketch  # noqa: E402
from datum.ui.main_window import MainWindow  # noqa: E402
from datum.ui.theme import stylesheet  # noqa: E402

FAILS = []
WORK = tempfile.mkdtemp(prefix="datum_reg_")

app = QtWidgets.QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(stylesheet())
win = MainWindow()
win.resize(1300, 850)
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


def near(a, b, tol=1.0):
    return abs(a - b) < tol


def venn_sketch(radius=30.0, offset=20.0):
    s = Sketch(STANDARD_PLANES["XY"], "Venn")
    for i in range(3):
        a = math.radians(90 + 120 * i)
        s.add_circle((offset * math.cos(a), offset * math.sin(a)), radius)
    return s


# ==========================================================================
print("three overlapping circles give seven regions")
s = venn_sketch()
regions = kernel.sketch_regions(s)
check("seven regions", len(regions) == 7, len(regions))

areas = sorted(round(r["area"], 1) for r in regions)
check("one small centre region", areas.count(areas[0]) == 1, areas)
check("three equal two-way overlaps",
      len({round(a) for a in areas[1:4]}) == 1, areas[1:4])
check("three equal outer petals",
      len({round(a) for a in areas[4:]}) == 1, areas[4:])

union = math.pi * 30 * 30 * 3
check("they sum to less than three whole circles",
      sum(r["area"] for r in regions) < union,
      sum(r["area"] for r in regions))

print("every region has a point that really lies on it")
from OCP.BRepTopAdaptor import BRepTopAdaptor_FClass2d  # noqa: E402
from OCP.BRepAdaptor import BRepAdaptor_Surface  # noqa: E402
from OCP.ElSLib import ElSLib  # noqa: E402
from OCP.TopAbs import TopAbs_OUT  # noqa: E402
from OCP.gp import gp_Pnt, gp_Pnt2d  # noqa: E402

inside_ok = True
for region in regions:
    face = region["face"]
    classifier = BRepTopAdaptor_FClass2d(face, 1e-6)
    plane = BRepAdaptor_Surface(face).Plane()
    point = gp_Pnt(*s.plane.to_3d(region["centre"][0], region["centre"][1]))
    u, v = ElSLib.Parameters_s(plane, point)
    if classifier.Perform(gp_Pnt2d(u, v)) == TopAbs_OUT:
        inside_ok = False
check("identity points are inside their region", inside_ok)

print("identity points are all distinct")
points = [r["centre"] for r in regions]
closest = min(math.dist(a, b)
              for i, a in enumerate(points) for b in points[i + 1:])
check("no two regions share a point", closest > 2.0, closest)

# ==========================================================================
print("the usual cases still behave")
cases = [
    ("single rectangle", lambda k: k.add_rectangle((0, 0), (50, 20)), 1),
    ("two separate rectangles",
     lambda k: (k.add_rectangle((0, 0), (20, 20)),
                k.add_rectangle((40, 0), (60, 20))), 2),
    ("rectangle with a hole",
     lambda k: (k.add_rectangle((0, 0), (60, 40)),
                k.add_circle((30, 20), 8)), 2),
    ("rectangle split by a line",
     lambda k: (k.add_rectangle((0, 0), (40, 20)),
                k.add_line((20, -5), (20, 25), weld=False)), 2),
    ("three nested rectangles",
     lambda k: (k.add_rectangle((0, 0), (60, 60)),
                k.add_rectangle((10, 10), (50, 50)),
                k.add_rectangle((20, 20), (40, 40))), 3),
    ("two overlapping circles",
     lambda k: (k.add_circle((-10, 0), 20), k.add_circle((10, 0), 20)), 3),
]
for name, build, expect in cases:
    probe = Sketch(STANDARD_PLANES["XY"], name)
    build(probe)
    got = kernel.sketch_regions(probe)
    check(name, len(got) == expect,
          "%d regions, expected %d" % (len(got), expect))

print("dangling and construction geometry is ignored")
probe = Sketch(STANDARD_PLANES["XY"], "Extras")
probe.add_rectangle((0, 0), (30, 30))
probe.add_line((60, 0), (80, 20), weld=False)
probe.add_line((0, 15), (30, 15), construction=True, weld=False)
got = kernel.sketch_regions(probe)
check("only the rectangle counts", len(got) == 1, len(got))
check("and it is not split by the construction line",
      near(got[0]["area"], 900.0), got[0]["area"])

print("it works on a plane that is not XY")
probe = Sketch(STANDARD_PLANES["XZ"], "Other")
probe.add_circle((0, 0), 10)
probe.add_circle((8, 0), 10)
check("three regions off-plane too",
      len(kernel.sketch_regions(probe)) == 3,
      len(kernel.sketch_regions(probe)))

# ==========================================================================
print("selecting any subset of the seven extrudes just that subset")
win.new_document(prompt=False)
feature = SketchFeature()
feature.name = "Venn"
feature.sketch = venn_sketch()
win.document.add_feature(feature)
win.rebuild()

regions = win.available_regions()
check("all seven offered to the UI", len(regions) == 7, len(regions))

ordered = sorted(regions, key=lambda r: r["area"])
extrude = ExtrudeFeature()
extrude.distance = "4"
win.document.add_feature(extrude)


def use(indices):
    extrude.profiles = ProfileSelection()
    for i in indices:
        entry = ordered[i]
        extrude.profiles.add(entry["sketch_id"], entry["centre"])
    report = win.rebuild()
    return report, (kernel.volume(win.document.shape)
                    if win.document.shape else 0.0)


report, volume = use([0])
check("one region builds", report.ok, report.message)
check("its volume is that region x 4",
      near(volume, ordered[0]["area"] * 4, 2.0),
      "%.1f vs %.1f" % (volume, ordered[0]["area"] * 4))

report, volume = use([0, 1, 2])
check("three regions build", report.ok, report.message)
check("volume is the sum of the three",
      near(volume, sum(r["area"] for r in ordered[:3]) * 4, 3.0),
      volume)

report, volume = use([1, 3, 5])
check("a scattered subset builds", report.ok, report.message)
check("volume matches that subset",
      near(volume, (ordered[1]["area"] + ordered[3]["area"]
                    + ordered[5]["area"]) * 4, 3.0), volume)

report, volume = use(range(7))
check("all seven build", report.ok, report.message)
total = sum(r["area"] for r in ordered) * 4
check("volume is the whole union", near(volume, total, 4.0),
      "%.1f vs %.1f" % (volume, total))

print("the choice survives a save and reload")
report, volume = use([0, 2, 4])
path = win.document.save(os.path.join(WORK, "venn.pdat"))
reloaded = Document.load(path)
check("same volume after reload",
      near(kernel.volume(reloaded.shape), volume, 1.0),
      kernel.volume(reloaded.shape))

print("it survives a change to the sketch, too")
before = len(extrude.profiles)
feature.sketch.entities[sorted(feature.sketch.entities)[0]].radius = 32.0
report = win.rebuild()
check("still builds after resizing a circle", report.ok, report.message)
check("the same number of profiles is still resolved",
      len(extrude.profiles) == before, len(extrude.profiles))

# ==========================================================================
print("clicking regions in the viewport actually registers")
win.new_document(prompt=False)
feature = SketchFeature()
feature.name = "Venn"
feature.sketch = venn_sketch()
win.document.add_feature(feature)
win.rebuild()
win.viewport.set_view("top")
win.viewport.finish_animation()
pump()

win.new_feature(ExtrudeFeature)
dlg = win._active_dialog
pump()
check("picking is armed as soon as the dialog opens", dlg.profiles.picking)
check("with the biggest region proposed", len(dlg.feature.profiles) == 1,
      len(dlg.feature.profiles))
dlg._clear_profiles()          # the clicks below choose their own
pump()
check("regions are already on screen",
      len(win.viewport._profile_display) == 7,
      len(win.viewport._profile_display))


def click_region(index):
    """Click a region through the real OCCT pick path, no Ctrl held."""
    region = win._profile_regions[index]
    world = feature.sketch.plane.to_3d(region["centre"][0],
                                       region["centre"][1])
    px, py = win.viewport.project(world)
    win.viewport.context.MoveTo(int(px), int(py), win.viewport.view, False)
    detected = win.viewport.context.HasDetected()
    win.viewport.context.SelectDetected()
    win.viewport.view.Redraw()
    picked = win.viewport.picked_profiles()
    win._on_viewport_selection()
    pump(1)
    return detected, picked


detected, picked = click_region(0)
check("a region is detected under the cursor", detected)
check("picked_profiles resolves it", picked == [0], picked)
check("and it reached the feature", len(dlg.feature.profiles) == 1,
      len(dlg.feature.profiles))
check("the field says so", "1 profile" in dlg.profiles.count.text(),
      dlg.profiles.count.text())

print("more plain clicks accumulate, no Ctrl")
for index in (2, 4):
    click_region(index)
check("three profiles now", len(dlg.feature.profiles) == 3,
      len(dlg.feature.profiles))
dlg.distance.set_text("6")
dlg.preview()
pump()
expect = sum(win._profile_regions[i]["area"] for i in (0, 2, 4)) * 6
check("they extrude", near(kernel.volume(win.document.shape), expect, 3.0),
      "%.1f vs %.1f" % (kernel.volume(win.document.shape), expect))
check("the feature has no error", dlg.feature.error == "", dlg.feature.error)

print("clicking one of them again removes it")
click_region(2)
check("back to two", len(dlg.feature.profiles) == 2,
      len(dlg.feature.profiles))
dlg.commit()
pump()
check("committed cleanly", win._active_dialog is None)
check("picking stopped", not win.viewport.profile_picking)

print("the viewport offers each region separately")
win.new_document(prompt=False)
feature = SketchFeature()
feature.name = "Venn"
feature.sketch = venn_sketch()
win.document.add_feature(feature)
win.rebuild()

win.new_feature(ExtrudeFeature)
dlg = win._active_dialog
pump()
dlg.profiles.set_picking(True)
pump()
check("seven pickable patches on screen",
      len(win.viewport._profile_display) == 7,
      len(win.viewport._profile_display))
dlg._clear_profiles()          # drop the proposed one, to pick by hand
pump()
check("none selected once that is cleared", len(dlg.feature.profiles) == 0)

for index in (0, 3, 6):
    entry = win._profile_regions[index]
    dlg.on_profile_clicked(entry["sketch_id"], entry["centre"])
pump()
check("three picked", len(dlg.feature.profiles) == 3,
      len(dlg.feature.profiles))
dlg.distance.set_text("4")
dlg.preview()
pump()
expect = sum(win._profile_regions[i]["area"] for i in (0, 3, 6)) * 4
check("preview matches the three picked",
      near(kernel.volume(win.document.shape), expect, 3.0),
      "%.1f vs %.1f" % (kernel.volume(win.document.shape), expect))

entry = win._profile_regions[3]
dlg.on_profile_clicked(entry["sketch_id"], entry["centre"])
dlg.preview()
pump()
check("clicking one again removes it", len(dlg.feature.profiles) == 2,
      len(dlg.feature.profiles))
dlg.commit()
pump()
check("committed", win._active_dialog is None)

print()
if FAILS:
    print("%d FAILURES: %s" % (len(FAILS), ", ".join(FAILS)))
    sys.exit(1)
print("all region tests passed")
sys.exit(0)
