"""The Output row: Body Name on an empty part, Boolean once there is one."""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402,F401


os.environ["DATUM_SETTINGS_ORG"] = "IITEG-tests"

from PySide6 import QtCore, QtWidgets                              # noqa: E402

from datum.core import kernel                                      # noqa: E402
from datum.core.features import (                                  # noqa: E402
    CUT, INTERSECT, JOIN, NEW_BODY, ExtrudeFeature, SketchFeature,
)
from datum.core.sketch import STANDARD_PLANES, Sketch              # noqa: E402
from datum.ui.dialogs import OP_OPTIONS                            # noqa: E402
from datum.ui.main_window import MainWindow                        # noqa: E402
from datum.ui.theme import stylesheet                              # noqa: E402

FAILS = []
WORK = tempfile.mkdtemp(prefix="datum_bool_")

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


def rect_sketch(name, x0, y0, x1, y1):
    feature = SketchFeature()
    feature.name = name
    feature.sketch = Sketch(STANDARD_PLANES["XY"], name)
    feature.sketch.add_rectangle((x0, y0), (x1, y1))
    win.document.add_feature(feature)
    win.rebuild()
    return feature


def extrude_dialog():
    win.new_feature(ExtrudeFeature)
    pump()
    dlg = win._active_dialog
    if dlg is not None:
        # a new feature proposes a profile; these pick their own
        dlg._clear_profiles()
        pump()
    return dlg


# ==========================================================================
print("the four options are in Inventor's order")

keys = [option[0] for option in OP_OPTIONS]
check("Join, Cut, Intersect, New Body",
      keys == [JOIN, CUT, INTERSECT, NEW_BODY], keys)
labels = [option[2].split(" - ")[0] for option in OP_OPTIONS]
check("and they read that way in the tooltips",
      labels == ["Join", "Cut", "Intersect", "New Body"], labels)


# ==========================================================================
print("an empty part is not asked which Boolean to use")

win.new_document(prompt=False)
pump()
first = rect_sketch("Outline", 0, 0, 60, 40)

dialog = extrude_dialog()
check("the dialog opened", dialog is not None)
check("there is no Boolean row", dialog.operation is None)
check("there is a Body Name row instead", dialog.body_name is not None)
check("which starts at Solid1", dialog.body_name.text() == "Solid1",
      dialog.body_name.text())
check("the row is labelled Body Name",
      dialog.form._labels[id(dialog.body_name)].text() == "Body Name",
      dialog.form._labels[id(dialog.body_name)].text())

dialog.body_name.setText("Housing")
dialog.distance.set_text("20")
win.select_all_profiles()
pump()
dialog.commit()
pump()
check("it built", win.document.last_report.ok,
      win.document.last_report.message)
check("one body", len(win.document.bodies) == 1, len(win.document.bodies))
check("named what was typed", win.document.bodies[0].name == "Housing",
      win.document.bodies[0].name)
check("the browser does not show a Solid Bodies folder for one body",
      not any("Solid Bodies" in i.text(0)
              for i in win.browser._iter_items()))


# ==========================================================================
print("once a body exists the Boolean row appears")

second = rect_sketch("Second", 40, 0, 100, 40)
dialog = extrude_dialog()
check("now there is a Boolean row", dialog.operation is not None)
check("and no Body Name row", dialog.body_name is None)
check("it is labelled Boolean",
      dialog.form._labels[id(dialog.operation)].text() == "Boolean",
      dialog.form._labels[id(dialog.operation)].text())
check("Join is the default", dialog.operation.value() == JOIN,
      dialog.operation.value())

dialog.distance.set_text("20")
for region in win.available_regions():
    if abs(region["centre"][0] - 70.0) < 20.0:
        dialog.on_profile_clicked(region["sketch_id"], region["centre"])
pump()
check("Join keeps one body", len(win.document.bodies) == 1,
      len(win.document.bodies))
joined = kernel.volume(win.document.shape)
check("and adds material without counting the overlap twice",
      abs(joined - 100 * 40 * 20) < 1e-3, joined)

print("switching to New Body splits them live")
dialog.operation.set_value(NEW_BODY)
dialog.preview()
pump()
check("two bodies now", len(win.document.bodies) == 2,
      len(win.document.bodies))
check("the overlap is counted twice, because they are separate solids",
      abs(kernel.volume(win.document.shape)
          - (60 * 40 * 20 + 60 * 40 * 20)) < 1e-3,
      kernel.volume(win.document.shape))
check("the second is auto-named Solid1",
      win.document.bodies[1].name == "Solid1",
      [b.name for b in win.document.bodies])

print("and Cut takes it away")
dialog.operation.set_value(CUT)
dialog.preview()
pump()
check("one body again", len(win.document.bodies) == 1,
      len(win.document.bodies))
check("with the overlap removed",
      abs(kernel.volume(win.document.shape) - (40 * 40 * 20)) < 1e-3,
      kernel.volume(win.document.shape))

print("Intersect keeps only the overlap")
dialog.operation.set_value(INTERSECT)
dialog.preview()
pump()
check("the shared block is what is left",
      abs(kernel.volume(win.document.shape) - (20 * 40 * 20)) < 1e-3,
      kernel.volume(win.document.shape))

dialog.operation.set_value(NEW_BODY)
dialog.commit()
pump()
check("committed as two bodies", len(win.document.bodies) == 2,
      len(win.document.bodies))


# ==========================================================================
print("the browser shows the bodies once there are two")

folder = [i for i in win.browser._iter_items()
          if "Solid Bodies" in i.text(0)]
check("the folder is there", len(folder) == 1, len(folder))
check("it counts them", "Solid Bodies(2)" in folder[0].text(0),
      folder[0].text(0))
names = [folder[0].child(i).text(0) for i in range(folder[0].childCount())]
check("and lists them by name", names == ["Housing", "Solid1"], names)


# ==========================================================================
print("reopening the dialog remembers what was chosen")

fid = win.document.features[-1].id
win.edit_feature(fid)
pump()
dialog = win._active_dialog
check("the Boolean row is back", dialog.operation is not None)
check("still on New Body", dialog.operation.value() == NEW_BODY,
      dialog.operation.value())
dialog.cancel()
pump()


# ==========================================================================
print("editing the very first feature still offers only a name")

first_solid = next(f for f in win.document.features
                   if isinstance(f, ExtrudeFeature))
win.edit_feature(first_solid.id)
pump()
dialog = win._active_dialog
check("no Boolean row on the first solid", dialog.operation is None)
check("its name is shown", dialog.body_name.text() == "Housing",
      dialog.body_name.text())
dialog.cancel()
pump()


# ==========================================================================
print("it round-trips through a save")

QtWidgets.QFileDialog.getSaveFileName = staticmethod(
    lambda *a, **k: (os.path.join(WORK, "twobody.pdat"), ""))
check("saved", win.save_document())
before = [b.name for b in win.document.bodies]
for entry in list(win.session.documents):
    entry.document.modified = False
    win.close_entry(entry)
pump()
check("reopened", win.open_path(os.path.join(WORK, "twobody.pdat")))
pump()
check("both bodies came back", len(win.document.bodies) == 2,
      len(win.document.bodies))
check("with their names",
      [b.name for b in win.document.bodies] == before,
      [b.name for b in win.document.bodies])


# ==========================================================================
for entry in list(win.session.documents):
    entry.document.modified = False
    win.close_entry(entry)
win.close()
pump()
shutil.rmtree(WORK, ignore_errors=True)
print()
if FAILS:
    print("%d FAILED" % len(FAILS))
    for name in FAILS:
        print("   - %s" % name)
    sys.exit(1)
print("all boolean UI checks passed")
