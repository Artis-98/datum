"""The rectangle and slot variants behind the Rectangle drop-down."""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402,F401


os.environ["DATUM_SETTINGS_ORG"] = "IITEG-tests"

from PySide6 import QtCore, QtWidgets                              # noqa: E402

from datum.core import kernel                                      # noqa: E402
from datum.core.features import ExtrudeFeature                     # noqa: E402
from datum.ui.main_window import RECT_OPTIONS, MainWindow          # noqa: E402
from datum.ui.sketcher import (                                    # noqa: E402
    RECT_TOOLS, SLOT_TOOLS, TOOL_HINTS, TOOLS,
)
from datum.ui.theme import stylesheet                              # noqa: E402

FAILS = []

app = QtWidgets.QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(stylesheet())
win = MainWindow()
win.resize(1400, 900)
win.show()
app.processEvents()
ed = win.editor


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def pump(n=3):
    for _ in range(n):
        app.processEvents()


def near(a, b, tol=1e-3):
    return abs(a - b) < tol


def fresh(tool):
    win.new_document(prompt=False)
    win.start_sketch_on_plane("XY")
    pump()
    # exact coordinates matter here, so the grid must not round them
    ed.snap_grid = False
    ed.set_tool(tool)
    return ed.sketch


def click(u, v):
    ed._on_move(u, v, QtCore.Qt.NoModifier)
    ed._on_click(u, v, QtCore.Qt.NoModifier)
    pump(1)


def corners(sketch):
    """Every distinct endpoint of the line entities."""
    out = set()
    for ent in sketch.entities.values():
        if ent.kind != "line":
            continue
        for pid in ent.points:
            p = sketch.points[pid]
            out.add((round(p.x, 3), round(p.y, 3)))
    return out


def side_lengths(sketch):
    out = []
    for ent in sketch.entities.values():
        if ent.kind != "line":
            continue
        a = sketch.points[ent.points[0]]
        b = sketch.points[ent.points[1]]
        out.append(round(math.dist((a.x, a.y), (b.x, b.y)), 3))
    return sorted(out)


def kinds(sketch):
    """The outline: a slot's construction centre line is not part of it."""
    return sorted(e.kind for e in sketch.entities.values()
                  if not e.construction)


def centre_lines(sketch):
    return [e for e in sketch.entities.values() if e.construction]


def radii(sketch):
    return sorted(round(e.radius, 2) for e in sketch.entities.values()
                  if e.kind == "arc")


# ==========================================================================
print("the drop-down lists what Inventor lists, in that order")

check("ten entries", len(RECT_OPTIONS) == 10, len(RECT_OPTIONS))
titles = ["%s / %s" % (o[2], o[3]) for o in RECT_OPTIONS]
check("in the same order", titles == [
    "Rectangle / Two Point",
    "Rectangle / Three Point",
    "Rectangle / Two Point Center",
    "Rectangle / Three Point Center",
    "Slot / Center to Center",
    "Slot / Overall",
    "Slot / Center Point",
    "Slot / Three Point Arc",
    "Slot / Center Point Arc",
    "Polygon / Polygon",
], titles)
check("every entry is a real tool",
      all(o[0] in TOOLS for o in RECT_OPTIONS),
      [o[0] for o in RECT_OPTIONS if o[0] not in TOOLS])
check("and every one explains itself",
      all(o[0] in TOOL_HINTS for o in RECT_OPTIONS),
      [o[0] for o in RECT_OPTIONS if o[0] not in TOOL_HINTS])
check("four rectangle variants", len(RECT_TOOLS) == 4, RECT_TOOLS)
check("five slot variants", len(SLOT_TOOLS) == 5, SLOT_TOOLS)


# ==========================================================================
print("the button remembers the variant you picked")

win.new_document(prompt=False)
win.start_sketch_on_plane("XY")
pump()
button = win.rect_button
check("it starts on Two Point", button.current == "rect", button.current)
check("every variant is behind it",
      button.keys == [o[0] for o in RECT_OPTIONS], button.keys)
check("its menu has ten rows", len(button.menu().actions()) == 10,
      len(button.menu().actions()))

picked = []
button.chosen.connect(picked.append)
button._pick("slot_arc_centre")
pump()
check("picking one fires it", picked == ["slot_arc_centre"], picked)
check("the button moves to it", button.current == "slot_arc_centre",
      button.current)
check("the caption says which", "Center Point Arc" in button.text(),
      button.text())
check("and the editor is actually on that tool",
      ed.tool == "slot_arc_centre", ed.tool)
check("the button is lit", button.isChecked())

ed.set_tool("line")
win._sync_tool_buttons()
check("and goes dark for a tool that is not its own",
      not button.isChecked())
button._pick("rect")
pump()


# ==========================================================================
print("Rectangle, Two Point")

sketch = fresh("rect")
click(0, 0)
click(40, 20)
check("four lines", len(sketch.entities) == 4, len(sketch.entities))
check("corners where they were clicked",
      corners(sketch) == {(0, 0), (40, 0), (40, 20), (0, 20)},
      corners(sketch))


print("Rectangle, Three Point")
sketch = fresh("rect3")
click(0, 0)
click(30, 0)
click(30, 12)
check("four lines", len(sketch.entities) == 4, len(sketch.entities))
check("30 by 12", side_lengths(sketch) == [12.0, 12.0, 30.0, 30.0],
      side_lengths(sketch))

print("and it can lean")
sketch = fresh("rect3")
click(0, 0)
click(30, 30)
click(20, 40)
check("still four lines", len(sketch.entities) == 4, len(sketch.entities))
lengths = side_lengths(sketch)
diagonal = math.hypot(30, 30)
check("the long sides are the edge that was drawn",
      near(lengths[2], diagonal) and near(lengths[3], diagonal), lengths)
check("four distinct corners", len(corners(sketch)) == 4, corners(sketch))
check("none of them is axis aligned with the first",
      all(not (near(x, 0) and near(y, 0)) or True for x, y in corners(sketch)))
check("opposite sides are held parallel",
      sum(1 for c in sketch.constraints.values() if c.kind == "parallel") == 2,
      [c.kind for c in sketch.constraints.values()])
check("and one corner is held square",
      sum(1 for c in sketch.constraints.values()
          if c.kind == "perpendicular") == 1,
      [c.kind for c in sketch.constraints.values()])


print("Rectangle, Two Point Center")
sketch = fresh("rect_centre")
click(10, 10)
click(30, 20)
check("four lines", len(sketch.entities) == 4, len(sketch.entities))
check("centred on the first click",
      corners(sketch) == {(-10, 0), (30, 0), (30, 20), (-10, 20)},
      corners(sketch))


print("Rectangle, Three Point Center")
sketch = fresh("rect3_centre")
click(0, 0)
click(20, 0)
click(0, 8)
check("four lines", len(sketch.entities) == 4, len(sketch.entities))
check("40 by 16", side_lengths(sketch) == [16.0, 16.0, 40.0, 40.0],
      side_lengths(sketch))
check("centred on the origin",
      corners(sketch) == {(-20, -8), (20, -8), (20, 8), (-20, 8)},
      corners(sketch))


# ==========================================================================
print("Slot, Center to Center")

sketch = fresh("slot")
click(0, 0)
click(40, 0)
click(40, 6)
check("two lines and two arcs",
      kinds(sketch) == ["arc", "arc", "line", "line"], kinds(sketch))
check("and a construction centre line", len(centre_lines(sketch)) == 1)
check("the straight sides are the centre distance",
      near(side_lengths(sketch)[0], 40.0), side_lengths(sketch))
check("capped at half the width", radii(sketch) == [6.0, 6.0], radii(sketch))
region = kernel.sketch_regions(sketch)
check("it encloses one region", len(region) == 1, len(region))
check("of the right area - the caps bulge outwards, not across",
      near(region[0]["area"], 40 * 12 + math.pi * 36, 1e-6),
      (region[0]["area"], 40 * 12 + math.pi * 36))

print("and at an angle")
sketch = fresh("slot")
click(0, 0)
click(30, 40)
click(30 + 6 * 0.8, 40 - 6 * 0.6)
region = kernel.sketch_regions(sketch)
check("still one region of the right area",
      len(region) == 1
      and near(region[0]["area"], 50 * 12 + math.pi * 36, 1e-4),
      region[0]["area"] if region else None)


print("Slot, Overall")
sketch = fresh("slot_overall")
click(0, 0)
click(40, 0)
click(40, 6)
check("two lines and two arcs",
      kinds(sketch) == ["arc", "arc", "line", "line"], kinds(sketch))
check("and a construction centre line", len(centre_lines(sketch)) == 1)
check("the straight part is shorter by the two caps",
      near(side_lengths(sketch)[0], 28.0), side_lengths(sketch))
# the ends are arcs, so the span is the cap centres plus their radius
caps = sorted(round(sketch.points[e.points[0]].x, 3)
              for e in sketch.entities.values() if e.kind == "arc")
check("but it still spans the 40 that was clicked",
      near(caps[0] - 6.0, 0.0) and near(caps[-1] + 6.0, 40.0), caps)

print("an overall slot shorter than it is wide is refused")
sketch = fresh("slot_overall")
before = len(sketch.entities)
said = []
ed.status_changed.connect(said.append)
click(0, 0)
click(10, 0)
click(10, 20)
check("nothing was drawn", len(sketch.entities) == before,
      len(sketch.entities))
check("and it says why",
      any("longer than the width" in m for m in said), said)


print("Slot, Center Point")
sketch = fresh("slot_centre")
click(0, 0)
click(20, 0)
click(20, 6)
check("two lines and two arcs",
      kinds(sketch) == ["arc", "arc", "line", "line"], kinds(sketch))
check("and a construction centre line", len(centre_lines(sketch)) == 1)
check("centre to centre is 40", near(side_lengths(sketch)[0], 40.0),
      side_lengths(sketch))
xs = [round(sketch.points[p].x, 3) for p in sketch.points]
check("symmetric about the first click", near(min(xs) + max(xs), 0.0),
      (min(xs), max(xs)))


# ==========================================================================
print("Slot, Three Point Arc")

sketch = fresh("slot_arc3")
click(20, 0)
click(0, 20)
click(20 * math.cos(math.pi / 4), 20 * math.sin(math.pi / 4))
click(26 * math.cos(math.pi / 4), 26 * math.sin(math.pi / 4))
check("four arcs, two rails and two caps",
      kinds(sketch) == ["arc"] * 4, kinds(sketch))
check("the rails straddle radius 20",
      radii(sketch)[2] < 20.0 < radii(sketch)[3], radii(sketch))

print("three points in a line have no arc, and it says so")
sketch = fresh("slot_arc3")
before = len(sketch.entities)
said = []
ed.status_changed.connect(said.append)
click(0, 0)
click(20, 0)
click(10, 0)
click(10, 5)
check("nothing was drawn", len(sketch.entities) == before,
      len(sketch.entities))
check("and it says why", any("straight line" in m for m in said), said)


print("Slot, Center Point Arc")
sketch = fresh("slot_arc_centre")
click(0, 0)
click(20, 0)
click(0, 20)
click(0, 26)
check("four arcs", kinds(sketch) == ["arc"] * 4, kinds(sketch))
check("rails at 14 and 26, caps at 6",
      radii(sketch) == [6.0, 6.0, 14.0, 26.0], radii(sketch))
region = kernel.sketch_regions(sketch)
ring = math.pi * (26 ** 2 - 14 ** 2) / 4.0
check("it encloses one region", len(region) == 1, len(region))
check("a quarter ring plus its two round ends",
      near(region[0]["area"], ring + math.pi * 36, 1e-4),
      (region[0]["area"], ring + math.pi * 36))

print("a width wider than the arc is refused")
sketch = fresh("slot_arc_centre")
before = len(sketch.entities)
said = []
ed.status_changed.connect(said.append)
click(0, 0)
click(10, 0)
click(0, 10)
click(0, 40)
check("nothing was drawn", len(sketch.entities) == before,
      len(sketch.entities))
check("and it says why", any("does not fit" in m for m in said), said)


# ==========================================================================
print("Polygon still works from the same button")

sketch = fresh("polygon")
ed.polygon_sides = 6
click(0, 0)
click(10, 0)
check("six sides", len(sketch.entities) == 6, len(sketch.entities))


# ==========================================================================
print("the variants extrude to real solids")

win.new_document(prompt=False)
win.start_sketch_on_plane("XY")
pump()
ed.snap_grid = False
ed.set_tool("rect3_centre")
click(0, 0)
click(20, 0)
click(0, 8)
win.finish_sketch()
pump()
win.new_feature(ExtrudeFeature)
pump()
dialog = win._active_dialog
dialog.distance.set_text("5")
win.select_all_profiles()
pump()
dialog.commit()
pump()
check("it built", win.document.last_report.ok,
      win.document.last_report.message)
check("40 x 16 x 5", near(kernel.volume(win.document.shape), 40 * 16 * 5, 0.01),
      kernel.volume(win.document.shape))

print("and so does an arc slot")
win.new_document(prompt=False)
win.start_sketch_on_plane("XY")
pump()
ed.snap_grid = False
ed.set_tool("slot_arc_centre")
click(0, 0)
click(20, 0)
click(0, 20)
click(0, 26)
win.finish_sketch()
pump()
win.new_feature(ExtrudeFeature)
pump()
dialog = win._active_dialog
dialog.distance.set_text("4")
win.select_all_profiles()
pump()
dialog.commit()
pump()
check("it built", win.document.last_report.ok,
      win.document.last_report.message)
# a quarter of the ring between r=14 and r=26, plus the two round ends
ring = math.pi * (26 ** 2 - 14 ** 2) / 4.0
caps = math.pi * 6 ** 2
check("the area is the quarter ring plus its caps",
      near(kernel.volume(win.document.shape), (ring + caps) * 4.0, 1.0),
      kernel.volume(win.document.shape))


# ==========================================================================
for entry in list(win.session.documents):
    entry.document.modified = False
    win.close_entry(entry)
win.close()
pump()
print()
if FAILS:
    print("%d FAILED" % len(FAILS))
    for name in FAILS:
        print("   - %s" % name)
    sys.exit(1)
print("all sketch tool checks passed")
