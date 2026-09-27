"""Foreign files read in workers, with the window live.

Translating a STEP file is one core's work that used to freeze the
window for as long as it took. Imported as a part, a worker translates it
into the body cache and the window reads that; opened as an assembly, a
worker reads it and writes the parts out, reporting how far it has got,
and then every worker meshes the parts. What has to hold: the result is
what it always was, the window keeps turning over, and a rebuild asked
for while it waits is not started inside the one under way.
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WORK = tempfile.mkdtemp(prefix="datum_import_workers_")
os.environ["DATUM_CONFIG_DIR"] = os.path.join(WORK, "cfg")
os.environ["DATUM_DOCUMENTS"] = os.path.join(WORK, "Documents")
# workers on, which the harness otherwise switches off for window tests
os.environ["DATUM_NO_WORKERS"] = ""
os.environ["DATUM_WORKERS"] = "2"

import harness  # noqa: E402,F401

from PySide6 import QtCore, QtWidgets  # noqa: E402

from datum.core import fileio, geometry, kernel, workers  # noqa: E402
from datum.core.assembly import AssemblyDocument  # noqa: E402
from datum.core.features import ImportFeature  # noqa: E402
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


def pump(n=8):
    for _ in range(n):
        app.processEvents()


pump()
helpers = workers.pool()
helpers.start()
check("workers are ready", helpers.wait_ready(timeout=120) == 2)
sent = []
real_submit = helpers.submit


def spying(op, **args):
    sent.append(op)
    return real_submit(op, **args)


helpers.submit = spying
ticks = []
clock = QtCore.QTimer()
clock.setInterval(10)
clock.timeout.connect(lambda: ticks.append(time.perf_counter()))


print("a STEP file imported as a part")
posts = os.path.join(WORK, "posts.step")
fileio.write_shape(geometry.cylinder(5, 30).repeat(4, x=20).shape, posts)
expected = kernel.volume(fileio.read_step(posts))
win.new_document()
pump()
feature = ImportFeature()
feature.path = posts
feature.operation = "new"
win.document.add_feature(feature)
asked = []


def meanwhile():
    # a slider, say, asking for a rebuild while the first one waits
    asked.append(win.document._rebuilding)
    win.document.rebuild()


QtCore.QTimer.singleShot(0, meanwhile)
clock.start()
started = time.perf_counter()
win.rebuild(keep_camera=False)
took = time.perf_counter() - started
clock.stop()
check("a worker translated it", "translate" in sent, sent)
check("  and the part is what the file holds",
      win.document.shape is not None
      and abs(kernel.volume(win.document.shape) - expected) < 1e-6)
check("the window kept turning over meanwhile",
      len([t for t in ticks if started <= t <= started + took]) >= 3)
check("a rebuild asked for meanwhile waited for this one",
      asked == [True] and not win.document._rebuilding, asked)


print()
print("a STEP file opened as an assembly")
loose = os.path.join(WORK, "Blocks.step")
fileio.write_shape(geometry.box(10, 10, 10).repeat(5, x=20).shape, loose)
sent.clear()
ticks.clear()
told = []
real_report = QtWidgets.QProgressDialog.setLabelText


def telling(dialog, text):
    told.append(text)
    return real_report(dialog, text)


QtWidgets.QProgressDialog.setLabelText = telling
clock.start()
started = time.perf_counter()
opened = win.import_as_assembly(loose, os.path.join(WORK, "Blocks"))
took = time.perf_counter() - started
clock.stop()
QtWidgets.QProgressDialog.setLabelText = real_report
check("it opens", opened and win.assembly is not None)
check("a worker read it and wrote it out", "import_assembly" in sent, sent)
check("  and the workers meshed its parts", sent.count("build") == 5, sent)
check("  telling the window how far they had got",
      any(t.startswith("Meshing parts") for t in told), told[-5:])
built = AssemblyDocument.load(win.assembly.path)
built.rebuild()
check("the assembly is the file, block for block",
      len(kernel.explore(built.shape, kernel.TopAbs_SOLID)) == 5
      and abs(kernel.volume(built.shape) - 5000.0) < 1e-6)
check("the window kept turning over meanwhile",
      len([t for t in ticks if started <= t <= started + took]) >= 3)

helpers.submit = real_submit
helpers.shutdown()

print()
print("FAILED: " + ", ".join(FAILS) if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
