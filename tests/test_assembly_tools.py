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
print()
if FAILS:
    print("%d FAILED" % len(FAILS))
    for name in FAILS:
        print("   - %s" % name)
    sys.exit(1)
print("all assembly tool checks passed")
