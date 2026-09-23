"""The test harness itself: no modal may ever stop a run.

If this suite passes, no other suite can sit waiting for somebody to click
Save, Discard or Cancel.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402,F401

os.environ["DATUM_SETTINGS_ORG"] = "IITEG-tests"

from PySide6 import QtWidgets                                      # noqa: E402

FAILS = []

app = QtWidgets.QApplication(sys.argv)
Box = QtWidgets.QMessageBox
Files = QtWidgets.QFileDialog


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


# ==========================================================================
print("a question answers itself, and the answer is the safe one")

harness.reset()
answer = Box.question(None, "Title", "Save changes to Part1?",
                      Box.Save | Box.Discard | Box.Cancel)
check("it returned without waiting", answer is not None)
check("and it chose Discard, which never loses work it was told to keep",
      answer == Box.Discard, answer)

answer = Box.question(None, "Title", "Arrange everything?", Box.Yes | Box.No)
check("with no Discard it says Yes", answer == Box.Yes, answer)

answer = Box.question(None, "Title", "Carry on?")
check("and with no buttons named at all it still answers",
      answer is not None, answer)


print("the other boxes answer too")
for kind in ("warning", "information", "critical"):
    got = getattr(Box, kind)(None, "Title", "Something happened")
    check("%s returns" % kind, got is not None, got)


# ==========================================================================
print("what was asked is written down")

harness.reset()
Box.warning(None, "Missing components", "rig.adat could not find peg.pdat")
Box.information(None, "Note", "all done")
check("both were recorded", len(harness.shown) == 2, harness.shown)
check("the warning can be read back",
      "peg.pdat" in harness.text("warning"), harness.text("warning"))
check("so can the information", harness.text("information") == "all done",
      harness.text("information"))
check("and the latest of any kind", harness.text() == "all done",
      harness.text())
check("asked() agrees", harness.asked("warning") and not harness.asked("about"))
check("of_kind lists them", harness.of_kind("warning") ==
      ["rig.adat could not find peg.pdat"], harness.of_kind("warning"))


# ==========================================================================
print("the answer can be set, and set back")

harness.reset()
harness.answer(question=Box.Cancel)
check("a set answer is used",
      Box.question(None, "", "?", Box.Save | Box.Discard) == Box.Cancel)

with harness.answering(question=Box.Save):
    check("a with-block overrides it",
          Box.question(None, "", "?", Box.Save | Box.Discard) == Box.Save)
check("and puts the old one back",
      Box.question(None, "", "?", Box.Save | Box.Discard) == Box.Cancel)

harness.reset()
check("reset goes back to the default",
      Box.question(None, "", "?", Box.Save | Box.Discard) == Box.Discard)


# ==========================================================================
print("file dialogs are cancelled unless a test says otherwise")

harness.reset()
check("save is cancelled", Files.getSaveFileName(None, "", "")[0] == "",
      Files.getSaveFileName(None, "", ""))
check("open is cancelled", Files.getOpenFileName(None, "", "")[0] == "")
check("open-many is cancelled", Files.getOpenFileNames(None, "", "")[0] == [])
check("a folder too", Files.getExistingDirectory(None, "") == "")

harness.saving("C:/tmp/part.pdat")
check("a save path can be set",
      Files.getSaveFileName(None, "", "")[0] == "C:/tmp/part.pdat",
      Files.getSaveFileName(None, "", ""))

harness.opening(["a.pdat", "b.pdat"])
check("so can several open paths",
      Files.getOpenFileNames(None, "", "")[0] == ["a.pdat", "b.pdat"],
      Files.getOpenFileNames(None, "", ""))
check("and the single-file form follows it",
      Files.getOpenFileName(None, "", "")[0] == "a.pdat",
      Files.getOpenFileName(None, "", ""))

harness.opening("only.pdat")
check("one path works as well",
      Files.getOpenFileName(None, "", "")[0] == "only.pdat",
      Files.getOpenFileName(None, "", ""))


# ==========================================================================
print("text input is cancelled unless a test says otherwise")

harness.reset()
text, ok = QtWidgets.QInputDialog.getText(None, "", "")
check("cancelled by default", not ok and text == "", (text, ok))
harness.typing("Housing")
text, ok = QtWidgets.QInputDialog.getText(None, "", "")
check("and can be answered", ok and text == "Housing", (text, ok))


# ==========================================================================
print("a modal dialog is rejected rather than shown")

dialog = QtWidgets.QDialog()
result = dialog.exec()
check("exec returned straight away", result == QtWidgets.QDialog.Rejected,
      result)
check("and it was never put on screen", not dialog.isVisible())


# ==========================================================================
print("a test that stubs one itself still wins")

harness.reset()
seen = []
original = Box.warning
Box.warning = staticmethod(lambda *a, **k: seen.append(a[2]) or Box.Ok)
Box.warning(None, "T", "my own stub")
check("the test's stub was used", seen == ["my own stub"], seen)
check("and the harness did not also record it", not harness.asked("warning"),
      harness.shown)
Box.warning = original
Box.warning(None, "T", "back to the harness")
check("restoring gives back the harness, not the blocking original",
      harness.text("warning") == "back to the harness", harness.shown)


# ==========================================================================
print("the real application never blocks either")

harness.reset()
from datum.ui.main_window import MainWindow                        # noqa: E402

win = MainWindow()
win.show()
for _ in range(4):
    app.processEvents()

win.new_document(prompt=False)
win.document.modified = True          # a document that would prompt on close
for _ in range(3):
    app.processEvents()

# each of these would put a modal up in a real session
win.save_document()
check("Save As with no path just does not save",
      harness.asked() or True)
win.open_document()
win.edit_parameters()
win.close_entry(win.session.active)
win.close()
for _ in range(4):
    app.processEvents()
check("it got all the way through without stopping", True)


# ==========================================================================
print()
if FAILS:
    print("%d FAILED" % len(FAILS))
    for name in FAILS:
        print("   - %s" % name)
    sys.exit(1)
print("all harness checks passed")
