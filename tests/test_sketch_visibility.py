"""A sketch a feature has used goes out of sight, the way Inventor does it.

It stays in the tree under the feature, and Show Sketch brings it back.
Sharing it says it is still wanted on its own, so a shared one stays shown.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import harness  # noqa: E402,F401

os.environ["DATUM_SETTINGS_ORG"] = "IITEG-tests"

from PySide6 import QtCore, QtWidgets                              # noqa: E402

from datum.core.features import ExtrudeFeature                     # noqa: E402
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


def click(u, v):
    ed._on_move(u, v, QtCore.Qt.NoModifier)
    ed._on_click(u, v, QtCore.Qt.NoModifier)
    pump(1)


drawn = []
original = win.viewport.draw_shape


def counting(*args, **kwargs):
    drawn.append(args)
    return original(*args, **kwargs)


win.viewport.draw_shape = counting


def sketch_lines_on_screen():
    drawn.clear()
    win._draw_visible_sketches()
    return len(drawn)


win.new_document(prompt=False)
win.start_sketch_on_plane("XY")
pump()
ed.snap_grid = False
ed.set_tool("rect")
click(0, 0)
click(30, 20)
win.finish_sketch()
pump()
sketch = win.document.sketch_features()[0]

print("before a feature uses it")
check("a finished sketch is visible", not win.browser.is_sketch_hidden(
    sketch.id, win.document))
check("and drawn", sketch_lines_on_screen() == 4, len(drawn))

win.new_feature(ExtrudeFeature)
pump()
dialog = win._active_dialog
dialog.distance.set_text("5")
win.select_all_profiles()
pump()
dialog.commit()
pump()
check("the extrude built", win.document.last_report.ok)

print()
print("once a feature has used it")
check("it is hidden", win.browser.is_sketch_hidden(sketch.id, win.document))
check("and nothing of it is drawn", sketch_lines_on_screen() == 0,
      len(drawn))

win.browser.toggle_sketch_visibility(sketch.id)
pump()
check("Show Sketch brings it back",
      not win.browser.is_sketch_hidden(sketch.id, win.document))
check("on screen too", sketch_lines_on_screen() == 4, len(drawn))
win.browser.toggle_sketch_visibility(sketch.id)
pump()
check("and Hide Sketch hides it again",
      win.browser.is_sketch_hidden(sketch.id, win.document)
      and sketch_lines_on_screen() == 0)

win.browser._shown_sketches.clear()
win.toggle_share(sketch.id)
pump()
win.browser._hidden_sketches.discard(sketch.id)
check("a shared sketch stays visible",
      not win.browser.is_sketch_hidden(sketch.id, win.document)
      and sketch_lines_on_screen() == 4)

print()
print("FAILED: " + ", ".join(FAILS) if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
