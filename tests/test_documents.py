"""Many documents open at once: the tab strip, and Local Update.

The workflow being checked is Inventor's, not SolidWorks': one window, one
tab per file, editing a part from an assembly lands in the part's own tab,
and the assembly does not take that edit until it is told to.
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402,F401


# keep the test run out of the real recent-files list
os.environ["DATUM_SETTINGS_ORG"] = "IITEG-tests"

from PySide6 import QtCore, QtWidgets                              # noqa: E402

from datum.core import fileformat, kernel                          # noqa: E402
from datum.core.assembly import AssemblyDocument                   # noqa: E402
from datum.core.cam import CamDocument                             # noqa: E402
from datum.core.constraints3d import MATE                          # noqa: E402
from datum.core.document import Document                           # noqa: E402
from datum.core.features import (                                  # noqa: E402
    ExtrudeFeature, HoleFeature, PrimitiveFeature, SketchFeature,
)
from datum.core.sketch import STANDARD_PLANES, Sketch              # noqa: E402
from datum.ui import doctabs                                       # noqa: E402
from datum.ui.main_window import MainWindow, TAB_ASSEMBLE, TAB_MODEL  # noqa: E402
from datum.ui.theme import stylesheet                              # noqa: E402

FAILS = []
WORK = tempfile.mkdtemp(prefix="datum_docs_")

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


def pump(n=4):
    for _ in range(n):
        app.processEvents()


def settle(ms=500):
    """Pump the event loop by the clock rather than by a count of turns."""
    timer = QtCore.QElapsedTimer()
    timer.start()
    while timer.elapsed() < ms:
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 20)


def still(ms=4000):
    """Wait until the camera stops moving, and say where it stopped.

    Changing the view and restoring one both animate, so reading the camera
    after a fixed wait catches it somewhere along the swing - long enough on
    an idle machine, not nearly long enough when the suites run several at a
    time.  Waiting for two identical samples is what the test actually
    means, and it does not care how loaded the machine is.
    """
    timer = QtCore.QElapsedTimer()
    timer.start()
    last = None
    while timer.elapsed() < ms:
        settle(60)
        state = win.viewport.camera_state()
        if state is not None and state == last:
            return state
        last = state
    return last


def make_block(name, height):
    doc = Document()
    block = PrimitiveFeature()
    block.kind, block.a, block.b, block.c = "box", "60", "40", str(height)
    doc.add_feature(block)
    doc.rebuild()
    return doc.save(os.path.join(WORK, name))


def make_plate(name, thickness):
    doc = Document()
    outline = SketchFeature()
    outline.sketch = Sketch(STANDARD_PLANES["XY"], "Outline")
    outline.sketch.add_rectangle((0, 0), (120, 80))
    doc.add_feature(outline)
    extrude = ExtrudeFeature()
    extrude.distance = str(thickness)
    extrude.profiles.add(outline.id, (60.0, 40.0))
    doc.add_feature(extrude)
    doc.rebuild()
    return doc.save(os.path.join(WORK, name))


BLOCK = make_block("block", 10)
PEG = make_block("peg", 25)
PLATE = make_plate("plate", 3)


def close_everything():
    for entry in list(win.session.documents):
        entry.document.modified = False
        win.close_entry(entry)
    pump()


# ==========================================================================
print("a cold start is Home and nothing else")

check("Home is the first tab", win.doc_tabs.key_at(0) == doctabs.HOME,
      win.doc_tabs.key_at(0))
check("nothing is open yet", len(win.session) == 0, len(win.session))
check("so there are no document tabs", not win.doc_tabs.keys,
      win.doc_tabs.keys)
check("the strip is showing Home", win.doc_tabs.current_key == doctabs.HOME,
      win.doc_tabs.current_key)
check("Home shows the start page", win.on_start_page)
check("Home cannot be closed",
      win.doc_tabs.tabButton(0, QtWidgets.QTabBar.RightSide) is None)

win.new_document()
pump()
check("a new part gets a tab", len(win.doc_tabs.keys) == 1,
      len(win.doc_tabs.keys))
check("and it opens straight away", not win.on_start_page)
check("it is a part", win.in_part)
check("the tab says Part1.pdat",
      win.session.active.label == "Part1.pdat", win.session.active.label)
check("a document tab does have a close button",
      win.doc_tabs.tabButton(1, QtWidgets.QTabBar.RightSide) is not None)

win.doc_tabs.document_selected.emit(doctabs.HOME)
pump()
check("going back to Home shows the start page", win.on_start_page)
check("without closing anything", len(win.session) == 1)
win.doc_tabs.document_selected.emit(win.doc_tabs.keys[0])
pump()
check("and the document tab brings it back", not win.on_start_page)


# ==========================================================================
print("documents open beside each other, not instead of each other")

close_everything()
win.open_path(BLOCK)
win.open_path(PEG)
win.new_assembly()
pump()

check("three tabs", len(win.doc_tabs.keys) == 3, len(win.doc_tabs.keys))
check("three documents", len(win.session) == 3, len(win.session))
check("the newest is active",
      win.session.active.doc_type == fileformat.ASSEMBLY)
check("the earlier ones are still loaded",
      win.session.by_path(BLOCK) is not None
      and win.session.by_path(PEG) is not None)
check("each keeps its own type",
      sorted(e.doc_type for e in win.session)
      == [fileformat.ASSEMBLY, fileformat.PART, fileformat.PART],
      [e.doc_type for e in win.session])

print("switching tabs switches the whole workspace")
block_entry = win.session.by_path(BLOCK)
win.activate(block_entry)
pump()
check("the part is active", win.in_part)
check("the 3D Model tab is showing",
      win.ribbon.current_tab() == TAB_MODEL, win.ribbon.current_tab())
check("the feature tree is in the dock",
      win.browser_stack.currentWidget() is win.browser)
check("and it is the right document", win.document is block_entry.document)

assembly_entry = next(e for e in win.session
                      if e.doc_type == fileformat.ASSEMBLY)
win.activate(assembly_entry)
pump()
check("back to the assembly", win.in_assembly)
check("the Assemble tab is showing",
      win.ribbon.current_tab() == TAB_ASSEMBLE, win.ribbon.current_tab())
check("the component tree is in the dock",
      win.browser_stack.currentWidget() is win.assembly_panel)

print("each document remembers its own view")
win.activate(block_entry)
win.viewport.set_view("front")
front = still()
win.activate(assembly_entry)
still()
win.viewport.set_view("top")
still()
win.activate(block_entry)
now = still()
check("the part came back to the view it was left at",
      all(abs(a - b) < 1e-6 for a, b in zip(front[2], now[2])),
      (front[2], now[2]))

print("Ctrl+Tab cycles")
before = win.session.active
win.cycle_documents(1)
pump()
check("it moved on", win.session.active is not before)
win.cycle_documents(-1)
pump()
check("and back", win.session.active is before)


# ==========================================================================
print("opening a file that is already open goes to its tab")

count = len(win.session)
win.activate(assembly_entry)
pump()
check("opening it again returns True", win.open_path(BLOCK))
pump()
check("nothing new was loaded", len(win.session) == count, len(win.session))
check("and it went to the existing tab",
      win.session.active is block_entry)
check("the status says so", "already open" in win.status_message.text(),
      win.status_message.text())


# ==========================================================================
print("editing a part from an assembly")

close_everything()
QtWidgets.QFileDialog.getOpenFileNames = staticmethod(
    lambda *a, **k: ([PLATE, PEG], ""))
win.new_assembly()
win.assembly.path = os.path.join(WORK, "rig.adat")
win.assembly_ui.place_component()
pump()
assembly_entry = win.session.active
check("the assembly placed two components",
      len(win.assembly.occurrences) == 2, len(win.assembly.occurrences))
check("it is the only tab", len(win.doc_tabs.keys) == 1,
      len(win.doc_tabs.keys))

plate_occurrence = win.assembly.occurrences[0]
win.assembly_ui.open_component(plate_occurrence.id)
pump()
check("the part opened in its own tab", len(win.doc_tabs.keys) == 2,
      len(win.doc_tabs.keys))
check("and it is the active one", win.in_part)
plate_entry = win.session.by_path(PLATE)
check("it is the plate", win.session.active is plate_entry)
check("the status explains Local Update",
      "Local Update" in win.status_message.text(), win.status_message.text())

print("asking again goes back to the same tab, not a second copy")
win.activate(assembly_entry)
pump()
win.assembly_ui.open_component(plate_occurrence.id)
pump()
check("still two tabs", len(win.doc_tabs.keys) == 2, len(win.doc_tabs.keys))
check("and we are in the plate again", win.session.active is plate_entry)


# ==========================================================================
print("Local Update, the way the lightning bolt works")

win.activate(assembly_entry)
pump()
check("nothing to update yet", not win.qat_update.isEnabled())
check("and the status bar is quiet", not win.status_update.text(),
      win.status_update.text())

# thicken the plate in its own tab
win.activate(plate_entry)
pump()
win.document.features[1].distance = "25"
win.document.modified = True
win.rebuild()
pump()
check("the part itself is thicker",
      abs(kernel.bounding_box(win.document.shape)[5] - 25.0) < 1e-6,
      kernel.bounding_box(win.document.shape)[5])

win.activate(assembly_entry)
pump()
check("the assembly knows it is behind", win.qat_update.isEnabled())
check("the status bar says which part",
      "plate" in win.status_update.text(), win.status_update.text())
check("the tab is flagged too",
      win.session.is_stale(assembly_entry))

placed = win.assembly.occurrences[0]
check("but the assembly has NOT changed under us",
      abs(kernel.bounding_box(placed.shape)[5] - 3.0) < 1e-6,
      kernel.bounding_box(placed.shape)[5])

win.local_update()
pump()
placed = win.assembly.occurrences[0]
check("Local Update takes the change",
      abs(kernel.bounding_box(placed.shape)[5] - 25.0) < 1e-6,
      kernel.bounding_box(placed.shape)[5])
check("the button goes dark again", not win.qat_update.isEnabled())
check("and the status bar clears", not win.status_update.text(),
      win.status_update.text())
check("it says what it did", "Updated with" in win.status_message.text(),
      win.status_message.text())

print("and it works without the part ever being saved")
check("the part is still unsaved", win.session.by_path(PLATE).modified)
check("yet the assembly already has the new geometry",
      abs(kernel.bounding_box(win.assembly.occurrences[0].shape)[5] - 25.0)
      < 1e-6)

print("pressing it with nothing to do says so")
win.local_update()
pump()
check("it is honest about that",
      "up to date" in win.status_message.text(), win.status_message.text())

print("a constraint solved against the old part re-solves on update")
win.activate(plate_entry)
win.document.features[1].distance = "40"
win.document.modified = True
win.rebuild()
pump()
win.activate(assembly_entry)
pump()
check("out of date again", win.qat_update.isEnabled())
win.local_update()
pump()
check("and the placed body followed",
      abs(kernel.bounding_box(win.assembly.occurrences[0].shape)[5] - 40.0)
      < 1e-6, kernel.bounding_box(win.assembly.occurrences[0].shape)[5])


# ==========================================================================
print("a CAM sheet is told the same way")

# back to a 3 mm plate, and let the change through before nesting it
win.activate(plate_entry)
win.document.features[1].distance = "3"
win.rebuild()
pump()
win.activate(assembly_entry)
win.local_update()
pump()

win.new_cam()
win.cam.path = os.path.join(WORK, "nest.cdat")
win.cam.sheet_width, win.cam.sheet_height = "600", "400"
QtWidgets.QFileDialog.getOpenFileNames = staticmethod(
    lambda *a, **k: ([PLATE], ""))
win.cam_ui.add_part()
pump()
cam_entry = win.session.active
check("the sheet cut the plate", win.cam.last_report.toolpath.cuts,
      win.cam.last_report.message)
check("at 3 mm thick",
      abs(win.cam.parts[0].cut_face.thickness - 3.0) < 1e-6,
      win.cam.parts[0].cut_face.thickness)

win.activate(plate_entry)
win.document.features[1].distance = "6"
win.document.modified = True
win.rebuild()
pump()
win.activate(cam_entry)
pump()
check("the sheet knows it is behind", win.qat_update.isEnabled())
win.local_update()
pump()
check("and picks up the new thickness",
      abs(win.cam.parts[0].cut_face.thickness - 6.0) < 1e-6,
      win.cam.parts[0].cut_face.thickness)


# ==========================================================================
print("closing tabs")

count = len(win.session)
win.session.by_path(PLATE).document.modified = False
win.close_key(win.session.by_path(PLATE).key)
pump()
check("one fewer", len(win.session) == count - 1, len(win.session))
check("and one fewer tab", len(win.doc_tabs.keys) == count - 1)
check("something else became active", win.session.active is not None)

print("closing a modified document offers to save")
win.activate(assembly_entry)
win.assembly.modified = True
asked = []
original_question = QtWidgets.QMessageBox.question
QtWidgets.QMessageBox.question = staticmethod(
    lambda *a, **k: asked.append(a[2] if len(a) > 2 else "")
    or QtWidgets.QMessageBox.Cancel)
win.close_key(assembly_entry.key)
QtWidgets.QMessageBox.question = original_question
pump()
check("it asked", asked and "Save changes" in asked[0], asked)
check("and Cancel kept it open",
      win.session.by_key(assembly_entry.key) is not None)

QtWidgets.QMessageBox.question = staticmethod(
    lambda *a, **k: QtWidgets.QMessageBox.Discard)
win.close_key(assembly_entry.key)
QtWidgets.QMessageBox.question = original_question
pump()
check("Discard closed it",
      win.session.by_key(assembly_entry.key) is None)

print("closing the last document falls back to Home")
close_everything()
check("nothing is open", len(win.session) == 0, len(win.session))
check("Home is showing", win.on_start_page)
check("only the Home tab is left", not win.doc_tabs.keys)


# ==========================================================================
print("tab labels carry the file name and the dirty mark")

win.open_path(BLOCK)
pump()
entry = win.session.active
check("the tab is the file name", entry.label == "block.pdat", entry.label)
check("clean at first", not entry.modified)
win.document.modified = True
win._update_title()
pump()
index = win.doc_tabs._keys.index(entry.key)
check("a dirty document gets a star",
      win.doc_tabs.tabText(index).endswith("*"),
      win.doc_tabs.tabText(index))
check("and so does the window title", win.windowTitle().endswith("*"),
      win.windowTitle())


# ==========================================================================
close_everything()
win.close()
pump()
shutil.rmtree(WORK, ignore_errors=True)
print()
if FAILS:
    print("%d FAILED" % len(FAILS))
    for name in FAILS:
        print("   - %s" % name)
    sys.exit(1)
print("all document/tab checks passed")
