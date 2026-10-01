"""The End of Part marker: where it lands when dropped, and Delete Below.

Let go of the marker on a feature and it goes after it, the way Inventor
does; only the line above a row puts it before.  Right-clicking it offers
to delete everything underneath.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import harness  # noqa: E402,F401

os.environ["DATUM_SETTINGS_ORG"] = "IITEG-tests"

from PySide6 import QtCore, QtGui, QtWidgets                       # noqa: E402

from datum.core.features import PrimitiveFeature                   # noqa: E402
from datum.ui.browser import ROLE_ID, ROLE_KIND                    # noqa: E402
from datum.ui.main_window import MainWindow                        # noqa: E402
from datum.ui.theme import stylesheet                              # noqa: E402

FAILS = []

app = QtWidgets.QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(stylesheet())
win = MainWindow()
win.resize(1400, 900)
win.show()
app.processEvents()
tree = win.browser


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def pump(n=3):
    for _ in range(n):
        app.processEvents()


def row_of(kind, fid=None):
    root = tree.topLevelItem(0)
    for i in range(root.childCount()):
        item = root.child(i)
        if item.data(0, ROLE_KIND) != kind:
            continue
        if fid is None or int(item.data(0, ROLE_ID)) == fid:
            return item
    return None


def drop_marker_on(target, position):
    """Drop the End of Part on a row, with the indicator where asked."""
    tree.setCurrentItem(row_of("end"))
    real_at, real_position = tree.itemAt, tree.dropIndicatorPosition
    tree.itemAt = lambda _p: target
    tree.dropIndicatorPosition = lambda: position
    event = QtGui.QDropEvent(QtCore.QPointF(5, 5), QtCore.Qt.MoveAction,
                             QtCore.QMimeData(), QtCore.Qt.LeftButton,
                             QtCore.Qt.NoModifier)
    try:
        tree.dropEvent(event)
    finally:
        tree.itemAt, tree.dropIndicatorPosition = real_at, real_position
    pump()


win.new_document(prompt=False)
made = []
for n in range(4):
    block = PrimitiveFeature(name="Block%d" % (n + 1))
    block.kind = "box"
    block.a, block.b, block.c = "10", "10", "10"
    block.origin = (str(n * 20), "0", "0")
    if n:
        block.operation = "join"
    win.document.add_feature(block)
    made.append(block)
win.rebuild()
pump()

View = QtWidgets.QAbstractItemView
print("dropping the marker")
drop_marker_on(row_of("feature", made[1].id), View.OnItem)
check("on a feature, it goes after that feature",
      win.document.rollback_index == 2, win.document.rollback_index)
drop_marker_on(row_of("feature", made[0].id), View.AboveItem)
check("on the line above a feature, it goes before it",
      win.document.rollback_index == 0, win.document.rollback_index)
drop_marker_on(row_of("feature", made[2].id), View.BelowItem)
check("on the line below, after it",
      win.document.rollback_index == 3, win.document.rollback_index)
drop_marker_on(row_of("feature", made[3].id), View.OnItem)
check("on the last feature, it is back at the end",
      win.document.rollback_index is None, win.document.rollback_index)

print()
print("Delete All Features Below")
win.set_rollback(2)
pump()
harness.answer(question=QtWidgets.QMessageBox.No)
win.delete_features_below()
check("asking first, and No keeps them",
      len(win.document.features) == 4 and harness.asked("question"))
check("the question names them",
      "Block3" in harness.text("question")
      and "Block4" in harness.text("question"), harness.text("question"))
harness.answer(question=QtWidgets.QMessageBox.Yes)
win.delete_features_below()
pump()
check("Yes deletes everything under the marker",
      [f.name for f in win.document.features] == ["Block1", "Block2"],
      [f.name for f in win.document.features])
check("and the marker is back at the end",
      win.document.rollback_index is None)
check("what is left still builds", win.document.last_report.ok)
win.undo()
pump()
check("one undo brings them all back", len(win.document.features) == 4,
      len(win.document.features))

print()
print("FAILED: " + ", ".join(FAILS) if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
