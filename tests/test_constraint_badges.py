"""Seeing what is holding a line, and being able to let one go.

Selecting geometry shows a glyph for every constraint on it.  Clicking a
glyph picks that constraint, and Delete removes it - leaving the geometry
where it is, which is the point: an accidental constraint should not cost
you the line it landed on.
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402,F401


os.environ["DATUM_SETTINGS_ORG"] = "IITEG-tests"

from PySide6 import QtCore, QtWidgets                              # noqa: E402

from datum.ui.main_window import MainWindow                        # noqa: E402
from datum.ui.sketcher import CONSTRAINT_BADGES                    # noqa: E402
from datum.ui.theme import C, stylesheet                           # noqa: E402

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


def pump(n=3):
    for _ in range(n):
        app.processEvents()


def fresh():
    win.new_document(prompt=False)
    win.start_sketch_on_plane("XY")
    pump()
    ed.snap_grid = False
    ed.set_tool("select")
    return ed.sketch


# every text draw is recorded, so the glyphs can be read back
TEXTS = []
_real_draw_text = vp.draw_text


def spy(text, position, colour, size=14.0, preview=False, **kwargs):
    if not preview:
        TEXTS.append({"text": text, "pos": tuple(position), "colour": colour})
    return _real_draw_text(text, position, colour, size, preview=preview,
                           **kwargs)


vp.draw_text = spy


def redraw():
    TEXTS.clear()
    ed.render()
    pump(1)
    return [t["text"] for t in TEXTS]


# ==========================================================================
print("nothing is shown until something is selected")

sketch = fresh()
line = sketch.add_line((0.0, 0.0), (0.0, 60.0))
sketch.add_constraint("vertical", entities=[line])
ed.solve()

check("no glyphs on an unselected sketch", redraw() == [], redraw())
check("and none are laid out", ed._badges == [], ed._badges)


# ==========================================================================
print("selecting the line shows what is holding it")

ed.selected_entities = [line]
glyphs = redraw()
check("one glyph appears", len(glyphs) == 1, glyphs)
check("and it is the vertical one",
      glyphs == [CONSTRAINT_BADGES["vertical"]], glyphs)
check("it is laid out beside the line, not on it",
      ed._badges and abs(ed._badges[0][1][0]) > 1e-6, ed._badges)


print("several constraints line up in a row")
second = sketch.add_line((0.0, 60.0), (40.0, 60.0))
sketch.add_constraint("horizontal", entities=[second])
sketch.add_constraint("perpendicular", entities=[line, second])
ed.solve()
ed.selected_entities = [second]
glyphs = redraw()
check("both of its constraints show", len(glyphs) == 2, glyphs)
check("horizontal and perpendicular",
      sorted(glyphs) == sorted([CONSTRAINT_BADGES["horizontal"],
                                CONSTRAINT_BADGES["perpendicular"]]), glyphs)
spots = [b[1] for b in ed._badges]
check("and they do not sit on top of each other",
      math.dist(spots[0], spots[1]) > 1e-6, spots)

print("the shared one is not drawn twice")
ed.selected_entities = [line, second]
glyphs = redraw()
check("three between them, not four", len(glyphs) == 3, glyphs)
check("perpendicular appears once",
      glyphs.count(CONSTRAINT_BADGES["perpendicular"]) == 1, glyphs)


print("a line also shows what is holding its ends")

sketch = fresh()
upright = sketch.add_line((0.0, 0.0), (0.0, 60.0))
across = sketch.add_line((0.0, 60.0), (50.0, 60.0))
sketch.add_constraint("vertical", entities=[upright])
sketch.add_constraint("coincident",
                      points=[sketch.entities[upright].points[1],
                              sketch.entities[across].points[0]])
ed.solve()
ed.selected_entities = [upright]
glyphs = redraw()
check("the vertical and the corner coincident both show",
      sorted(glyphs) == sorted([CONSTRAINT_BADGES["vertical"],
                                CONSTRAINT_BADGES["coincident"]]), glyphs)

print("and they sit beside the middle of the line, not at its tip")
spot = ed._badges[0][1]
middle = 30.0
check("the row starts level with the midpoint",
      abs(spot[1] - middle) < 12.0, (spot, middle))


# ==========================================================================
print("selecting a point shows every coincident on it")

sketch = fresh()
a = sketch.add_line((0.0, 0.0), (40.0, 0.0))
b = sketch.add_line((40.0, 0.0), (40.0, 40.0))
c = sketch.add_line((40.0, 0.0), (80.0, 30.0))
hub = sketch.entities[a].points[1]
sketch.add_constraint("coincident", points=[hub, sketch.entities[b].points[0]])
sketch.add_constraint("coincident", points=[hub, sketch.entities[c].points[0]])
ed.solve()

ed.clear_selection()
ed.selected_points = [hub]
glyphs = redraw()
check("both coincidents show", len(glyphs) == 2, glyphs)
check("and they are the coincident glyph",
      set(glyphs) == {CONSTRAINT_BADGES["coincident"]}, glyphs)


# ==========================================================================
print("a glyph can be picked, and it goes blue")

sketch = fresh()
line = sketch.add_line((0.0, 0.0), (0.0, 60.0))
cid = sketch.add_constraint("vertical", entities=[line])
ed.solve()
ed.selected_entities = [line]
redraw()

spot = ed._badges[0][1]
check("it can be found under the cursor",
      ed.pick_badge(spot[0], spot[1]) == cid, ed.pick_badge(*spot))
check("and not from far away", ed.pick_badge(spot[0] + 500.0, spot[1]) is None)

ed._tool_select(spot, QtCore.Qt.NoModifier)
pump()
check("clicking it picks the constraint", ed.selected_constraint == cid,
      ed.selected_constraint)
check("and the line itself was not re-picked",
      ed.selected_entities == [line], ed.selected_entities)
TEXTS.clear()
ed.render()
pump(1)
check("it draws blue", TEXTS and TEXTS[0]["colour"] == C.sketch_picked,
      TEXTS)

print("hovering one turns it green")
ed.selected_constraint = None
ed._on_move(spot[0], spot[1], QtCore.Qt.NoModifier)
hovered = ed._hover_badge
pump()
check("it is the hovered glyph", hovered == cid, hovered)
TEXTS.clear()
ed.render()
pump(1)
check("drawn green", TEXTS and TEXTS[0]["colour"] == C.sketch_hover, TEXTS)
check("and the line underneath is not hovered instead",
      ed._hover_entity is None, ed._hover_entity)


# ==========================================================================
print("Delete removes the constraint and leaves the line alone")

ed._tool_select(spot, QtCore.Qt.NoModifier)
pump()
check("picked again", ed.selected_constraint == cid)
before_entities = len(ed.sketch.entities)
said = []
ed.status_changed.connect(said.append)
ed.delete_selected()
pump()
check("the constraint is gone", cid not in ed.sketch.constraints,
      list(ed.sketch.constraints))
check("the line is still there", len(ed.sketch.entities) == before_entities,
      len(ed.sketch.entities))
check("nothing is picked any more", ed.selected_constraint is None)
check("and it says what it did", any("Removed" in m for m in said), said)

print("and the line really is free to move now")
ed.solve()
check("the sketch has degrees of freedom back", ed.sketch.dof > 0,
      ed.sketch.dof)


# ==========================================================================
print("with no glyph picked, Delete still deletes geometry")

sketch = fresh()
line = sketch.add_line((0.0, 0.0), (40.0, 0.0))
keep = sketch.add_line((0.0, 30.0), (40.0, 30.0))
ed.selected_entities = [line]
ed.selected_constraint = None
ed.delete_selected()
pump()
check("the line went", line not in ed.sketch.entities,
      list(ed.sketch.entities))
check("the other one stayed", keep in ed.sketch.entities,
      list(ed.sketch.entities))


# ==========================================================================
print("dimensions are not shown as glyphs - they draw themselves")

sketch = fresh()
line = sketch.add_line((0.0, 0.0), (40.0, 0.0))
sketch.add_constraint("horizontal", entities=[line])
ends = sketch.entities[line].points
sketch.add_constraint("distance", points=list(ends), value=40.0)
ed.solve()
ed.selected_entities = [line]
TEXTS.clear()
ed.render()
pump(1)
symbols = [t["text"] for t in TEXTS]
check("the horizontal glyph is there",
      CONSTRAINT_BADGES["horizontal"] in symbols, symbols)
check("but there is no glyph for the dimension",
      len([t for t in symbols if t in CONSTRAINT_BADGES.values()]) == 1,
      symbols)
check("the dimension label is drawn instead",
      any("40" in t for t in symbols), symbols)


# ==========================================================================
print("clearing the selection puts the glyphs away")

ed.clear_selection()
check("nothing is picked", ed.selected_constraint is None)
redraw()
check("and no glyphs are laid out", ed._badges == [], ed._badges)
check("though the dimension label is still there, as it should be",
      any("40" in t["text"] for t in TEXTS), [t["text"] for t in TEXTS])


# ==========================================================================
print("every constraint kind has a glyph")

from datum.core.sketch import CONSTRAINT_KINDS, DIMENSION_KINDS    # noqa: E402

# a dimension shows its number, not a glyph
dimension_kinds = set(DIMENSION_KINDS)
missing = [k for k in CONSTRAINT_KINDS
           if k not in dimension_kinds and k not in CONSTRAINT_BADGES]
check("none are left without one", missing == [], missing)
check("and they are all short enough to read",
      all(len(v) <= 2 for v in CONSTRAINT_BADGES.values()),
      CONSTRAINT_BADGES)


# ==========================================================================
vp.draw_text = _real_draw_text
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
print("all constraint glyph checks passed")
