"""Every rectangle and slot variant shows itself while it is being drawn.

The basic rectangle always did; the centre rectangle and the slots were
clicked blind.  For each variant this moves to the last click, reads the
rubber band, then clicks and compares: the preview has to cover the same
ground as the geometry the click actually makes.
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import harness  # noqa: E402,F401

os.environ["DATUM_SETTINGS_ORG"] = "IITEG-tests"

from PySide6 import QtCore, QtWidgets                              # noqa: E402

from datum.ui.main_window import MainWindow                        # noqa: E402
from datum.ui.sketcher import VARIANT_PREVIEWS                     # noqa: E402
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
vp = win.viewport


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def pump(n=2):
    for _ in range(n):
        app.processEvents()


EDGES = []
_real_edge = vp.draw_edge


def spy(a, b, colour, width=1.0, preview=False, **kwargs):
    if preview and not kwargs.get("dashed"):
        EDGES.append((tuple(a), tuple(b)))
    return _real_edge(a, b, colour, width, preview=preview, **kwargs)


vp.draw_edge = spy


def box(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def same_box(a, b, tol=0.6):
    return all(abs(a[i] - b[i]) < tol for i in range(4))


# each variant, and the clicks that make one; the last click is the one
# the preview has to predict
CASES = {
    "rect": [(0, 0), (30, 20)],
    "slot": [(0, 0), (40, 0), (10, 5)],
    "rect3": [(0, 0), (30, 10), (25, 30)],
    "rect_centre": [(10, 10), (30, 25)],
    "rect3_centre": [(0, 0), (20, 0), (5, 8)],
    "slot_overall": [(0, 0), (40, 0), (10, 5)],
    "slot_centre": [(0, 0), (20, 0), (5, 6)],
    "slot_arc3": [(30, 0), (0, 30), (21.2132, 21.2132), (26, 26)],
    "slot_arc_centre": [(0, 0), (30, 0), (0, 40), (0, 36)],
}
check("every variant with a preview is covered",
      set(VARIANT_PREVIEWS) <= set(CASES), set(VARIANT_PREVIEWS) - set(CASES))

for tool, clicks in CASES.items():
    print()
    print(tool)
    win.new_document(prompt=False)
    win.start_sketch_on_plane("XY")
    pump()
    ed.snap_grid = False
    ed.set_tool(tool)
    sketch = ed.sketch
    for u, v in clicks[:-1]:
        ed._on_move(u, v, QtCore.Qt.NoModifier)
        ed._on_click(u, v, QtCore.Qt.NoModifier)
    EDGES.clear()
    last = clicks[-1]
    ed._on_move(last[0], last[1], QtCore.Qt.NoModifier)
    preview = [ed.sketch.plane.to_2d(p) for edge in EDGES for p in edge]
    check("it shows a shape before the last click", len(EDGES) >= 4,
          len(EDGES))
    ed._on_click(last[0], last[1], QtCore.Qt.NoModifier)
    pump()
    made = [p for eid in sketch.entities
            for p in sketch.entity_polyline(eid, 96)]
    check("and makes the shape it showed",
          bool(made) and bool(preview)
          and same_box(box(preview), box(made)),
          (preview and box(preview), made and box(made)))

print()
print("one click in, a guide shows where the next one goes")
win.new_document(prompt=False)
win.start_sketch_on_plane("XY")
pump()
ed.snap_grid = False
ed.set_tool("slot_centre")
ed._on_move(0, 0, QtCore.Qt.NoModifier)
ed._on_click(0, 0, QtCore.Qt.NoModifier)
dashed = []


def dashed_spy(a, b, colour, width=1.0, preview=False, **kwargs):
    if preview and kwargs.get("dashed"):
        dashed.append((tuple(a), tuple(b)))
    return _real_edge(a, b, colour, width, preview=preview, **kwargs)


vp.draw_edge = dashed_spy
ed._on_move(20, 0, QtCore.Qt.NoModifier)
flat = [ed.sketch.plane.to_2d(p) for edge in dashed for p in edge]
check("the centre line runs both ways from the middle",
      flat and abs(min(p[0] for p in flat) + 20) < 1e-6
      and abs(max(p[0] for p in flat) - 20) < 1e-6, flat)
vp.draw_edge = _real_edge
ed.escape()

print()
print("FAILED: " + ", ".join(FAILS) if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
