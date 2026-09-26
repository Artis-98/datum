"""Screenshot dLogic: the stair sample, its code, a rule and its form.

    python tests/shot_dlogic.py OUT_DIR
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402,F401

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from datum.core import rules  # noqa: E402
from datum.ui import icons, rules_ui  # noqa: E402
from datum.ui.main_window import MainWindow  # noqa: E402
from datum.ui.theme import stylesheet  # noqa: E402

SAMPLE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(
    __file__))), "examples", "railing", "Stair Railing.pdat")
OUT = sys.argv[1] if len(sys.argv) > 1 else "."
os.makedirs(OUT, exist_ok=True)

app = QtWidgets.QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(stylesheet())
app.setWindowIcon(icons.app_icon())
win = MainWindow()
win.resize(1600, 980)
win.show()


def pump(n=6):
    for _ in range(n):
        app.processEvents()


def grab(widget, name):
    """The widget, with the 3D view painted in where it would be black."""
    pump()
    win.viewport.redraw()
    pump()
    chrome = widget.grab()
    if widget is win:
        vp_path = os.path.join(OUT, "_vp.png")
        win.viewport.grab_image(vp_path)
        image = QtGui.QImage(vp_path)
        if not image.isNull():
            origin = win.viewport.mapTo(win, QtCore.QPoint(0, 0))
            p = QtGui.QPainter(chrome)
            p.drawImage(QtCore.QRect(origin.x(), origin.y(),
                                     win.viewport.width(),
                                     win.viewport.height()), image)
            p.end()
        os.remove(vp_path)
    chrome.save(os.path.join(OUT, name))
    print("wrote", name)


# untrusted first: what somebody sees opening a file they were sent
win.open_path(SAMPLE)
pump()
win.viewport.set_view("iso")
win.viewport.fit_all()
grab(win, "dlogic_untrusted.png")

win.rules_panel.trust()
pump()
win.viewport.set_view("iso")
win.viewport.fit_all()
grab(win, "dlogic_stair.png")

railing = [f for f in win.document.features if f.name == "Railing"][0]
win.edit_feature(railing.id)
pump(10)
dialog = win._active_dialog
dialog.resize(1100, 720)
dialog.preview()
grab(dialog, "dlogic_code.png")
dialog.cancel()
pump()

size = [r for r in win.document.rules if r.name == "Size"][0]
editor = rules_ui.RuleEditor(size, win.document, win)
editor.show()
editor.run_now = lambda: None
grab(editor, "dlogic_rule.png")
broken = rules.Rule(name="Broken", source="w = params.width\n"
                    "params.depth = w * nothing\n")
editor.script.editor.setPlainText(broken.source)
result = rules.run(broken, win.document)
editor.script.show_result("line %d: %s" % (result.line, result.error),
                          False, result.line)
grab(editor, "dlogic_error.png")
editor.close()

form = rules_ui.FormDialog(win, win.document, "Stair", [
    rules.note("Everything else follows: treads, stringers, balusters, "
               "rail."),
    rules.slider("steps", 3, 20, 1), rules.slider("rise", 150, 220, 5),
    rules.slider("going", 220, 320, 5), rules.slider("width", 600, 1400, 50),
    rules.slider("rail_height", 850, 1100, 10),
    rules.slider("baluster_gap", 80, 150, 5),
    rules.choice("sides", ["1", "2"])])
form.show()
grab(form, "dlogic_form.png")
form.close()
