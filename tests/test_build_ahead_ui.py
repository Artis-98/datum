"""Building ahead from a dLogic form, in the window.

A form on a part that takes a moment to rebuild has the spare cores
build the slider values either side of where it sits. Stepping onto one
shows it straight away, without the pause a drag otherwise waits for,
and gives the model a rebuild here would. Closing the form lets it all go.
"""
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WORK = tempfile.mkdtemp(prefix="datum_ahead_ui_")
os.environ["DATUM_CONFIG_DIR"] = os.path.join(WORK, "cfg")
os.environ["DATUM_DOCUMENTS"] = os.path.join(WORK, "Documents")
# workers on, which the harness otherwise switches off for window tests
os.environ["DATUM_NO_WORKERS"] = ""
os.environ["DATUM_WORKERS"] = "3"

import harness  # noqa: E402,F401

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from datum.core import ahead as core_ahead  # noqa: E402
from datum.core import kernel, rules, workers  # noqa: E402
from datum.core.document import Document  # noqa: E402
from datum.ui import rules_ui  # noqa: E402
from datum.ui.main_window import MainWindow  # noqa: E402
from datum.ui.theme import stylesheet  # noqa: E402

FAILS = []
app = QtWidgets.QApplication(sys.argv)
app.setStyleSheet(stylesheet())
win = MainWindow()
win.resize(1300, 850)
win.show()


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def pump(n=6):
    for _ in range(n):
        app.processEvents()


def settle(ahead, seconds=60.0):
    """Pump until nothing is being built ahead."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        pump(2)
        if all(f.done() for _r, f in list(ahead._held.values())):
            pump(2)
            return True
        time.sleep(0.02)
    return False


def built(rise):
    """Whether this rise has been built ahead and is waiting."""
    for request, future in list(form._ahead._held.values()):
        values = {p["name"]: p["expression"]
                  for p in request["data"]["parameters"]}
        if values.get("rise") == rise and future.done():
            return True
    return False


def local_volume():
    here = Document.load(stair)
    for name in win.document.params.names():
        here.params.set_expression(name,
                                   win.document.params[name].expression)
    here.rebuild()
    return round(kernel.volume(here.shape), 6)


def shown_volume():
    return round(kernel.volume(win.document.shape), 6)


sample = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(
    __file__))), "examples", "railing", "Stair Railing.pdat")
stair = os.path.join(WORK, "Stair Railing.pdat")
shutil.copy(sample, stair)
rules.trust_path(stair)

check("the stair opens", win.open_path(stair))
pump()
helpers = workers.pool()
helpers.start()
check("workers are ready", helpers.wait_ready(timeout=120) == 3)
time.sleep(1.0)       # the copy the window asked for when it opened
pump()
# however quick this machine builds a stair, build ahead for it
core_ahead.AHEAD_AFTER = 0.0
win.rebuild(keep_camera=True)
pump()


print("a form builds ahead while it is open")
form = rules_ui.FormDialog(win, win.document, "Stair",
                           [rules.slider("rise", 150, 220, 5)])
form.show()
pump()
ahead = form._ahead
check("the form has spare cores to build with",
      ahead is not None and win.document.ahead is ahead)
check("  and put them to work on the values either side at once",
      ahead is not None and ahead.started >= 1,
      ahead and ahead.started)
check("everything it asked for arrives", settle(ahead))

slider = form.widgets["rise"]
start = slider.value()
slider.setValue(start + 1)
# no events pumped: the pause a drag waits for has not happened
check("stepping onto a value built ahead shows it straight away",
      ahead.used == 1 and abs(win.document.params["rise"].value
                              - (150 + 5 * (start + 1))) < 1e-9,
      ahead.used)
check("  the model a rebuild here makes", shown_volume() == local_volume())
check("  and it is on screen",
      win.viewport.has_model
      and win.viewport._model_shape is win.document.shape)

print()
print("stepping along with the keyboard")
steps = 0
hits = ahead.used
for _ in range(5):
    settle(ahead)
    # the next few values are offered as cores come free
    wanted = repr(float(150 + 5 * (slider.value() + 1)))
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not built(wanted):
        pump(2)
        time.sleep(0.02)
    slider.setFocus()
    QtWidgets.QApplication.sendEvent(slider, QtGui.QKeyEvent(
        QtCore.QEvent.KeyPress, QtCore.Qt.Key_Right, QtCore.Qt.NoModifier))
    steps += 1
pump()
check("every step was one built ahead", ahead.used - hits == steps,
      (ahead.used - hits, steps))
check("  ending on the model a rebuild here makes",
      shown_volume() == local_volume())

print()
print("a value it did not get to is built the usual way")
slider.setValue(slider.minimum())
pump()
QtCore.QThread.msleep(200)
pump(20)
deadline = time.monotonic() + 30
while time.monotonic() < deadline and abs(
        shown_volume() - local_volume()) > 1e-6:
    pump(4)
    time.sleep(0.05)
check("the model is right however it got there",
      shown_volume() == local_volume())

print()
print("closing the form lets it all go")
held = [f for _r, f in ahead._held.values()]
form.accept()
pump()
check("the part builds nothing ahead once the form is gone",
      win.document.ahead is None and ahead.closed)
results = []
for future in held:
    try:
        results.append(future.result(timeout=60))
    except Exception:
        pass
time.sleep(0.5)
check("  and nothing built for it is left on disk",
      not any(os.path.exists(r.get("pack") or "") for r in results
              if r.get("pack")))

print()
print("cancel still puts everything back")
before = win.document.params["rise"].expression
form = rules_ui.FormDialog(win, win.document, "Stair",
                           [rules.slider("rise", 150, 220, 5)])
form.show()
pump()
settle(form._ahead)
form.widgets["rise"].setValue(form.widgets["rise"].value() + 1)
pump()
form.reject()
pump(10)
check("the parameter is as it was", win.document.params["rise"].expression
      == before, win.document.params["rise"].expression)
check("  and so is the model", shown_volume() == local_volume())

helpers.shutdown()

print()
print("FAILED: " + ", ".join(FAILS) if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
