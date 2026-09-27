"""A rebuild in a worker, with the window alive while it runs.

What has to hold: the model that comes back is the model a rebuild here
would have made; the window keeps turning over while it waits; nothing
that could change the model gets in while it waits, except a dLogic form,
whose last value is built as soon as the first build lands; and a worker
dying in the middle costs a slower rebuild, never a wrong or missing one.
"""
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WORK = tempfile.mkdtemp(prefix="datum_remote_")
os.environ["DATUM_CONFIG_DIR"] = os.path.join(WORK, "cfg")
os.environ["DATUM_DOCUMENTS"] = os.path.join(WORK, "Documents")
# workers on, which the harness otherwise switches off for window tests
os.environ["DATUM_NO_WORKERS"] = ""
os.environ["DATUM_WORKERS"] = "2"

import harness  # noqa: E402,F401

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from datum.core import document as core_document  # noqa: E402
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


def local_volume(changes):
    here = Document.load(stair)
    for name, value in changes.items():
        here.params.set_expression(name, value)
    here.rebuild()
    return kernel.volume(here.shape)


sample = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(
    __file__))), "examples", "railing", "Stair Railing.pdat")
stair = os.path.join(WORK, "Stair Railing.pdat")
shutil.copy(sample, stair)
rules.trust_path(stair)

check("the window installed a worker for slow rebuilds",
      core_document.REMOTE is not None)
check("the stair opens", win.open_path(stair))
pump()
helpers = workers.pool()
helpers.start()
check("workers are ready", helpers.wait_ready(timeout=120) >= 1)
time.sleep(1.0)       # the copy the window asked for when it opened
pump()

# every rebuild goes out, however quick, so the path is the one tested
core_document.REMOTE_AFTER = 0.0
went = []
real = win._remote_rebuild


def spy(document):
    went.append(document)
    return real(document)


core_document.REMOTE = spy


print("a rebuild done in a worker")
ticks = []
clock = QtCore.QTimer()
clock.setInterval(10)
clock.timeout.connect(lambda: ticks.append(time.perf_counter()))
clock.start()
win.document.params.set_expression("steps", "20")
win.document.params.set_expression("sides", "2")
started = time.perf_counter()
win.rebuild(keep_camera=True)
took = time.perf_counter() - started
clock.stop()
check("it went to a worker", len(went) >= 1, len(went))
check("the model is what a rebuild here makes",
      abs(kernel.volume(win.document.shape)
          - local_volume({"steps": "20", "sides": "2"})) < 1e-6)
check("and it is on screen", win.viewport.model_ais is not None)
during = [t for t in ticks if started <= t <= started + took]
check("the window kept turning over while it waited",
      len(during) >= 3, "%d ticks in %.2f s" % (len(during), took))


print()
print("what could change the model waits; a form does not")
undo_before = len(win.document._undo)
win.document.push_undo()
win.document.params.set_expression("steps", "12")
form = rules_ui.FormDialog(win, win.document, "Stair",
                           [rules.slider("rise", 150, 220, 5)])
form.show()
pump()
slider = form.widgets["rise"]
pressed = []


def poke():
    # a shortcut for Undo, and a slider drag, while the worker is busy
    event = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Z,
                            QtCore.Qt.ControlModifier)
    before = win.document.params["steps"].expression
    QtWidgets.QApplication.sendEvent(win, event)
    pressed.append((win.viewport.busy, before,
                    win.document.params["steps"].expression))
    slider.setValue(slider.maximum())


# poked from inside the wait, so it is certainly while the worker works
real_wait = win._wait_responsive
poked = []


def waiting(future):
    if not poked:
        poked.append(True)
        QtCore.QTimer.singleShot(0, poke)
    return real_wait(future)


win._wait_responsive = waiting
went.clear()
win.document.params.set_expression("steps", "14")
win.rebuild(keep_camera=True)
win._wait_responsive = real_wait
pump()
check("a shortcut pressed while it rebuilt did nothing",
      pressed and win.document.params["steps"].expression == "14",
      win.document.params["steps"].expression)
check("the slider moved during it was built as soon as it landed",
      len(went) >= 2 and abs(win.document.params["rise"].value - 220.0)
      < 1e-9, (len(went), win.document.params["rise"].value))
check("  giving the model for the slider's value",
      abs(kernel.volume(win.document.shape)
          - local_volume({"steps": "14", "sides": "2", "rise": "220.0"}))
      < 1e-6)
form.reject()
pump()


print()
print("a worker lost in the middle costs time, not the model")
victim = helpers._affinity.get(win.document.remote_key)
went.clear()
QtCore.QTimer.singleShot(0, lambda: victim.process.kill())
win.document.params.set_expression("going", "270")
win.rebuild(keep_camera=True)
pump()
check("the rebuild still happened, here, for the model as it stands",
      abs(kernel.volume(win.document.shape) - local_volume(
          {n: win.document.params[n].expression
           for n in win.document.params.names()})) < 1e-6)
check("and the window is not stuck holding input",
      not win.viewport.busy)

helpers.shutdown()


print()
print("FAILED: " + ", ".join(FAILS) if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
