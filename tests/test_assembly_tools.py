"""One assembly tool at a time, and dragging what nothing is holding.

Free Move and the constraint dialog both want the viewport's picking.
Each command only ever cancelled the other half, so opening Constrain with
Free Move still armed left the dialog unable to select anything, which
reads as the constraint being broken rather than as two tools fighting.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import harness  # noqa: E402,F401

os.environ["DATUM_SETTINGS_ORG"] = "IITEG-asmtools-tests"

from PySide6 import QtWidgets                                  # noqa: E402

from datum.core.document import Document                       # noqa: E402
from datum.core.features import PrimitiveFeature               # noqa: E402
from datum.ui.main_window import MainWindow                    # noqa: E402
from datum.ui.theme import stylesheet                          # noqa: E402

FAILS = []
WORK = tempfile.mkdtemp(prefix="datum_asmtools_")

app = QtWidgets.QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(stylesheet())
win = MainWindow()
win.resize(1400, 900)
win.show()
app.processEvents()


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def pump(n=5):
    for _ in range(n):
        app.processEvents()


def make_part(name, w, d, h):
    doc = Document()
    f = PrimitiveFeature()
    f.kind, f.a, f.b, f.c, f.operation = "box", str(w), str(d), str(h), "new"
    doc.add_feature(f)
    doc.rebuild()
    return doc.save(os.path.join(WORK, name))


BLOCK = make_part("block.pdat", 40, 30, 10)
PLATE = make_part("plate.pdat", 60, 60, 5)


# ==========================================================================
print("one tool at a time")

win.new_assembly()
pump(8)
doc = win.assembly
doc.path = os.path.join(WORK, "rig.adat")
ui = win.assembly_ui

# two components, placed straight onto the document
from datum.core.assembly import Occurrence                     # noqa: E402
from datum.core import fileformat                              # noqa: E402

for source in (BLOCK, PLATE):
    o = Occurrence(id=doc.new_id(),
                   ref=fileformat.ComponentRef(
                       path=fileformat.relative_path(source, WORK),
                       name=os.path.basename(source)),
                   name=doc.unique_name(
                       os.path.splitext(os.path.basename(source))[0]))
    doc.occurrences.append(o)
doc.rebuild()
ui.rebuild()
pump(6)
check("two components are in the assembly", len(doc.occurrences) == 2,
      len(doc.occurrences))

ui.begin_tool("move")
pump(3)
check("Free Move is armed", ui.tool == "move", ui.tool)

ui.constrain()
pump(4)
check("opening Constrain cancelled Free Move", ui.tool is None, ui.tool)
check("and the constraint dialog is the thing that is open",
      ui.dialog is not None, ui.dialog)

ui.begin_tool("move")
pump(4)
check("arming Free Move closes the constraint dialog", ui.dialog is None,
      ui.dialog)
check("and Free Move is the armed one", ui.tool == "move", ui.tool)
ui.begin_tool(None)
pump(3)


# ==========================================================================
print("what nothing is holding can be dragged with no tool armed")

first, second = doc.occurrences[0], doc.occurrences[1]
check("a loose component is freely movable",
      ui.freely_movable(first.id))

print("grounding it takes that away")
first.grounded = True
check("a grounded one is not", not ui.freely_movable(first.id))
first.grounded = False

print("so does a constraint naming it")
from datum.core.assembly import Attachment, AssemblyConstraint  # noqa: E402
from datum.core import constraints3d                            # noqa: E402

c = AssemblyConstraint(id=doc.new_id(), kind=constraints3d.MATE,
                       a=Attachment(occurrence=first.id),
                       b=Attachment(occurrence=second.id))
doc.constraints.append(c)
check("a constrained component is not freely movable",
      not ui.freely_movable(first.id))
check("nor is the other end of it", not ui.freely_movable(second.id))

print("suppressing the constraint gives it back")
c.suppressed = True
check("it is free again", ui.freely_movable(first.id))
c.suppressed = False

print("and suppressing the component itself does not make it draggable")
first.suppressed = True
check("a suppressed component is not movable",
      not ui.freely_movable(first.id))
first.suppressed = False
doc.constraints.remove(c)

print("the viewport is told how to ask")
check("the callback is wired",
      callable(getattr(win.viewport, "component_freely_movable", None)))
check("and it refuses while a tool is armed",
      ui._free_drag_allowed(first.id))
ui.begin_tool("move")
check("with Free Move on, the no-tool path stands aside",
      not ui._free_drag_allowed(first.id))
ui.begin_tool(None)


# ==========================================================================
print("the right-click menu offers what the tree does")

menu = QtWidgets.QMenu()
win.viewport.selected_components = lambda: [first.id]
ui.context_menu(menu)
labels = [a.text() for a in menu.actions() if a.text()]
for wanted in ("Grounded", "Visible", "Suppressed", "Open Part", "Delete"):
    check("it offers %s" % wanted, wanted in labels, labels)


# ==========================================================================
print("copy and paste a component")

import harness as _h                                           # noqa: E402

before = len(doc.occurrences)
ui.browser.selected_occurrence_ids = lambda: [first.id]
ui.copy_selected()
check("something went on the clipboard", len(ui._clipboard) == 1,
      len(ui._clipboard))

ui.paste()
pump(5)
check("pasting added one", len(doc.occurrences) == before + 1,
      (before, len(doc.occurrences)))
pasted = doc.occurrences[-1]
check("the copy points at the same file",
      pasted.ref.name == first.ref.name, (pasted.ref.name, first.ref.name))
check("but has a name of its own", pasted.name != first.name,
      (pasted.name, first.name))
check("and it is not sitting exactly on the original",
      list(pasted.placement.position) != list(first.placement.position),
      (pasted.placement.position, first.placement.position))

print("pasting again gives a third, not a duplicate name")
ui.paste()
pump(5)
names = [o.name for o in doc.occurrences]
check("every name is still unique", len(names) == len(set(names)), names)


# ==========================================================================
print("Save and Replace points a component at its own copy")

target = os.path.join(WORK, "block copy.pdat")
if os.path.exists(target):
    os.remove(target)

# answer the file dialog with our path, the way the person would
real = QtWidgets.QFileDialog.getSaveFileName
QtWidgets.QFileDialog.getSaveFileName = staticmethod(
    lambda *a, **k: (target, ""))
try:
    was = pasted.ref.name
    ui.save_and_replace(pasted.id)
    pump(5)
finally:
    QtWidgets.QFileDialog.getSaveFileName = real

check("the new file was written", os.path.exists(target))
check("and the component now uses it",
      pasted.ref.name == os.path.basename(target), pasted.ref.name)
check("while the original is untouched",
      first.ref.name == was, (first.ref.name, was))
check("the copy is a real part file",
      os.path.getsize(target) == os.path.getsize(BLOCK),
      (os.path.getsize(target), os.path.getsize(BLOCK)))

print("asked for with nothing selected, it waits for a pick")
ui.browser.selected_occurrence_ids = lambda: []
win.viewport.selected_components = lambda: []
ui.save_and_replace()
check("it is waiting", ui._pending_replace)
check("and Escape lets go of it", ui.cancel_pending())
check("with nothing left pending", not ui._pending_replace)


# ==========================================================================
print("the view rests on picking components, so the menu knows what it is over")

ui.begin_tool("move")
pump(3)
ui.begin_tool(None)
pump(3)
check("with no tool, components are what gets picked",
      win.viewport.selection_mode == "assembly",
      win.viewport.selection_mode)

ui.set_picking(False)
pump(3)
check("and turning picking off does not drop it to faces",
      win.viewport.selection_mode == "assembly",
      win.viewport.selection_mode)

ui.dialog_finished(None)
pump(3)
check("nor does closing a dialog", win.viewport.selection_mode == "assembly",
      win.viewport.selection_mode)


# ==========================================================================
print("the menu survives the selection being rebuilt out from under it")
# Right-clicking a component selects it, selecting one makes the assembly
# redraw, and redrawing rebuilds every AIS object and throws the selection
# away.  By the time the menu is built there is nothing selected, which is
# why it used to come up with none of the component entries on it.

from PySide6 import QtCore                                      # noqa: E402

win.viewport.selected_components = lambda: []      # as it is after a redraw
win.viewport.menu_component = lambda: first.id     # but the cursor is there
menu = QtWidgets.QMenu()
ui.context_menu(menu)
labels = [a.text() for a in menu.actions() if a.text()]
for wanted in ("Grounded", "Visible", "Suppressed", "Open Part", "Delete"):
    check("with nothing selected it still offers %s" % wanted,
          wanted in labels, labels)

print("and with nothing under the cursor it offers only the general ones")
win.viewport.menu_component = lambda: None
menu = QtWidgets.QMenu()
ui.context_menu(menu)
labels = [a.text() for a in menu.actions() if a.text()]
check("no component entries", "Open Part" not in labels, labels)
check("but Place Component is still there",
      "Place Component..." in labels, labels)


# ==========================================================================
print("a component wears its own appearance, not a palette colour")

from datum.core import materials                                # noqa: E402

items = []
real_set = win.viewport.set_components
win.viewport.set_components = lambda i, keep_camera=True: items.extend(i)
try:
    ui.show_components()
    pump(3)
finally:
    win.viewport.set_components = real_set

check("every component was handed an appearance rather than a tint",
      items and all("appearance" in i for i in items),
      [sorted(i) for i in items[:1]])
check("and none of them a rotating palette colour",
      not any(i.get("colour") for i in items))


# ==========================================================================
print("Delete removes what is picked, without an interrogation")

before = len(doc.occurrences)
spare = doc.occurrences[-1]
ui.browser.selected_occurrence_ids = lambda: [spare.id]
did = ui.delete_selected()
pump(4)
check("it reported that it did something", did)
check("and the component is gone", len(doc.occurrences) == before - 1,
      (before, len(doc.occurrences)))
check("it says what happened rather than asking first",
      "Deleted" in win.status_message.text(), win.status_message.text())
check("and mentions the way back",
      "Ctrl+Z" in win.status_message.text(), win.status_message.text())

print("with nothing picked it does nothing at all")
ui.browser.selected_occurrence_ids = lambda: []
win.viewport.selected_components = lambda: []
count = len(doc.occurrences)
check("it says so", not ui.delete_selected())
check("and nothing went", len(doc.occurrences) == count)


# ==========================================================================
print("a box drag selects, and the selection survives the redraw")

check("box selection is on for an assembly",
      win.viewport.box_select_enabled)
check("and the view rests on picking components",
      win.viewport.selection_mode == "assembly",
      win.viewport.selection_mode)

# the thing that used to break it: showing the assembly rebuilds every
# AIS object, and the selection lives on those
kept = []
win.viewport.selected_components = lambda: list(kept)
captured = []
win.viewport.select_components = lambda ids: captured.extend(ids)
kept[:] = [doc.occurrences[0].id]
ui.show_components()
pump(3)
check("what was picked is put back after the redraw",
      captured == [doc.occurrences[0].id], captured)


# ==========================================================================
print("editing a part in place")

from datum.core import kernel                                  # noqa: E402
from datum.core.assembly import in_frame_of                    # noqa: E402

ui.rebuild()
pump(6)
first = win.assembly.occurrences[0]
before = kernel.bounding_box(win.assembly.shape)
count_before = len(win.assembly.occurrences)

context = in_frame_of(win.assembly, first.id)
check("the context is everything but the part itself",
      len(context) == count_before - 1, len(context))
check("and none of it is the part", all(oid != first.id for oid, _s in context))

win.edit_in_place(first.id)
pump(8)

check("the window is editing a part now", win.in_part, win.in_part)
check("and not the assembly", not win.in_assembly)
check("it is the part that component places",
      os.path.basename(win.document.path or "") == "block.pdat",
      win.document.path)
check("the rest of the assembly is drawn around it",
      len(win.viewport._component_ais) == count_before - 1,
      len(win.viewport._component_ais))
# Every context piece is recorded as locked, which is what keeps the
# selection mode from arming it.  Note that a selection made in the
# assembly before stepping in here can still be reported by
# selected_components until something clears it; the context cannot be
# picked, but the stale reading is untidy and is not fixed yet.
check("every piece of context is locked against picking",
      len(win.viewport._component_locked) == count_before - 1,
      win.viewport._component_locked)
check("the strip says what is being edited", win.in_place is not None)

# the whole point: a change made here has to reach the assembly
win.document.features[0].c = "40"
win.rebuild()
pump(8)
win.finish_in_place()
pump(10)

check("we are back in the assembly", win.in_assembly, win.in_assembly)
check("the ghosted context went with it",
      win.in_place == [] and win._ghosts == [],
      (win.in_place, len(win._ghosts)))
after = kernel.bounding_box(win.assembly.shape)
check("and the assembly is built from the edited part",
      abs((after[5] - after[2]) - (before[5] - before[2])) > 25.0,
      "%.1f tall before, %.1f after"
      % (before[5] - before[2], after[5] - after[2]))


# ==========================================================================
print("the tree keeps the assembly while a part inside it is edited")

from datum.ui.assembly_browser import ROLE_KIND, ROLE_ID, ROLE_OWNER  # noqa

# step back in: the section above ended by returning to the assembly
first = win.assembly.occurrences[0]
held = len(win.assembly.occurrences)
win.edit_in_place(first.id)
pump(8)

tree = win.assembly_ui.browser
rows = [tree.topLevelItem(0).child(i)
        for i in range(tree.topLevelItem(0).childCount())]
kinds = [r.data(0, ROLE_KIND) for r in rows]
check("every component is still in the tree",
      kinds.count("occurrence") == held, kinds)
check("it switched to the Modeling view", tree.mode == "modeling", tree.mode)
check("and knows which component is live",
      tree.active == first.id, tree.active)

live = next(r for r in rows if r.data(0, ROLE_ID) == first.id)
feature_rows = [live.child(i) for i in range(live.childCount())]
check("the live part shows its features in place",
      [r.data(0, ROLE_KIND) for r in feature_rows] == ["feature"],
      [r.data(0, ROLE_KIND) for r in feature_rows])
check("and they know which component they belong to",
      feature_rows[0].data(0, ROLE_OWNER) == first.id if feature_rows else False)
check("only the live one is unfolded",
      all(not r.isExpanded() for r in rows
          if r.data(0, ROLE_KIND) == "occurrence"
          and r.data(0, ROLE_ID) != first.id))

opened = []
tree.feature_activated.connect(opened.append)
tree._double_clicked(feature_rows[0], 0)
check("double-clicking a feature of the live part opens it",
      opened == [feature_rows[0].data(0, ROLE_ID)], opened)

other = next((r for r in rows if r.data(0, ROLE_KIND) == "occurrence"
              and r.data(0, ROLE_ID) != first.id), None)
if other is not None and other.childCount():
    stepped = []
    tree.occurrence_activated.connect(stepped.append)
    tree._double_clicked(other.child(0), 0)
    check("and one belonging to another part activates that part instead",
          stepped == [other.data(0, ROLE_ID)], stepped)

win.finish_in_place()
pump(8)
check("Return puts the tree back to the Assembly view",
      tree.mode == "assembly" and tree.active is None,
      (tree.mode, tree.active))


# ==========================================================================
print("and down through a sub-assembly, one level at a time")

from datum.core.assembly import AssemblyDocument                # noqa: E402
from datum.core.constraints3d import Placement                 # noqa: E402

inner = AssemblyDocument()
# base_dir follows the path, so the path comes first
inner.path = os.path.join(WORK, "inner.adat")
for source in (BLOCK, PLATE):
    placed = inner.place(source, os.path.basename(source))
    placed.grounded = True
inner.occurrences[1].placement = Placement([0.0, 0.0, 20.0], [0.0, 0.0, 0.0])
inner.rebuild()
INNER = inner.save(os.path.join(WORK, "inner.adat"))

win.new_assembly()
pump(8)
outer = win.assembly
outer.path = os.path.join(WORK, "outer.adat")
for source in (INNER, PLATE):
    placed = outer.place(source, os.path.basename(source))
    placed.grounded = True
outer.occurrences[1].placement = Placement([200.0, 0.0, 0.0], [0.0, 0.0, 0.0])
win.assembly_ui.rebuild()
pump(8)

top_high = kernel.bounding_box(outer.shape)[5]
sub = outer.occurrences[0]
check("the outer assembly places a sub-assembly and a part",
      len(outer.occurrences) == 2, len(outer.occurrences))

win.edit_in_place(sub.id)
pump(8)
check("stepping into a sub-assembly stays in assembly mode",
      win.in_assembly, win.in_assembly)
check("and shows what that sub-assembly holds",
      len(win.assembly.occurrences) == 2, len(win.assembly.occurrences))
check("with the level above ghosted around it",
      len(win._ghosts) == 1, len(win._ghosts))
check("one level down", len(win.in_place) == 1, len(win.in_place))

deep = win.assembly.occurrences[0]
win.edit_in_place(deep.id)
pump(8)
check("stepping again lands on the part", win.in_part, win.in_part)
check("two levels down", len(win.in_place) == 2, len(win.in_place))
check("and everything above it is ghosted, from both levels",
      len(win._ghosts) == 2, len(win._ghosts))
check("none of which can be picked",
      len(win.viewport._component_locked) == 2,
      len(win.viewport._component_locked))

win.document.features[0].c = "60"
win.rebuild()
pump(8)

win.finish_in_place()
pump(8)
check("Return comes up exactly one level", len(win.in_place) == 1,
      len(win.in_place))
check("which is the sub-assembly again", win.in_assembly, win.in_assembly)

win.finish_in_place()
pump(10)
check("and again to the top", win.in_place == [], win.in_place)
check("the Return panel is out of the way again",
      not any(panel.isVisible() for panel in win._in_place_panels))
check("and the edit came all the way up with it",
      kernel.bounding_box(win.assembly.shape)[5] > top_high + 15.0,
      "%.1f then %.1f"
      % (top_high, kernel.bounding_box(win.assembly.shape)[5]))


# ==========================================================================
print()
if FAILS:
    print("%d FAILED" % len(FAILS))
    for name in FAILS:
        print("   - %s" % name)
    sys.exit(1)
print("all assembly tool checks passed")
