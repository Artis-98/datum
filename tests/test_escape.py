"""Esc stops the operation in progress, wherever the keyboard focus is.

The view always handled Esc, but only while it had the focus, and a click
in the tree is enough to take it away.  Then Esc did nothing at all.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import harness  # noqa: E402,F401

os.environ["DATUM_SETTINGS_ORG"] = "IITEG-tests"

from PySide6 import QtCore, QtGui, QtWidgets                       # noqa: E402

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
ed = win.editor


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def pump(n=3):
    for _ in range(n):
        app.processEvents()


def escape_on(widget):
    widget.setFocus()
    pump()
    QtWidgets.QApplication.sendEvent(widget, QtGui.QKeyEvent(
        QtCore.QEvent.KeyPress, QtCore.Qt.Key_Escape, QtCore.Qt.NoModifier))
    pump()


win.new_document(prompt=False)
win.start_sketch_on_plane("XY")
pump()

print("from the tree")
ed.set_tool("line")
check("a tool is running", ed.tool == "line")
escape_on(win.browser)
check("Esc with the tree focused stops it", ed.tool == "select", ed.tool)
check("and it is still a sketch, Esc never leaves one", ed.active)

print()
print("from the view, as before")
ed.set_tool("circle")
escape_on(win.viewport)
check("Esc in the view still stops it", ed.tool == "select", ed.tool)

print()
print("typing is left alone")
field = QtWidgets.QLineEdit(win)
field.show()
ed.set_tool("line")
escape_on(field)
check("Esc in a text field belongs to the field",
      ed.tool == "line", ed.tool)
field.deleteLater()

other = QtWidgets.QWidget()
other.setFocusPolicy(QtCore.Qt.StrongFocus)
other.show()
other.activateWindow()
escape_on(other)
check("another window's Esc is its own", ed.tool == "line", ed.tool)
other.close()

print()
print("FAILED: " + ", ".join(FAILS) if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
