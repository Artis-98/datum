"""The app's own save path: real rendered thumbnails and dialog-free errors."""
import io
import json
import os
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# every modal answers itself, so a run never stops to ask
import harness  # noqa: E402,F401


# keep the test run out of the real recent-files list
os.environ["DATUM_SETTINGS_ORG"] = "IITEG-tests"

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from datum import APP_NAME  # noqa: E402
from datum.core import fileformat  # noqa: E402
from datum.core.assembly import AssemblyDocument  # noqa: E402
from datum.core.features import ExtrudeFeature  # noqa: E402
from datum.ui.main_window import MainWindow  # noqa: E402
from datum.ui.theme import stylesheet  # noqa: E402

FAILS = []
WORK = tempfile.mkdtemp(prefix="datum_app_")

app = QtWidgets.QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(stylesheet())
win = MainWindow()
win.resize(1200, 800)
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


# ==========================================================================
print("branding")
check("app is named DATUM", APP_NAME == "DATUM", APP_NAME)
check("window title uses it", "DATUM" in win.windowTitle(), win.windowTitle())

print("a real part saves with a rendered preview")
win.new_document(prompt=False)
win.start_sketch_on_plane("XY")
win.set_sketch_tool("rect")
win.editor._on_move(-30, -20, QtCore.Qt.NoModifier)
win.editor._on_click(-30, -20, QtCore.Qt.NoModifier)
win.editor._on_move(30, 20, QtCore.Qt.NoModifier)
win.editor._on_click(30, 20, QtCore.Qt.NoModifier)
win.finish_sketch()
pump()
win.new_feature(ExtrudeFeature)
win.select_all_profiles()
win._active_dialog.distance.set_text("14")
win._active_dialog.commit()
pump()
check("a body exists to photograph", win.document.shape is not None)

thumbnail = win._thumbnail()
check("a thumbnail was rendered", thumbnail is not None
      and len(thumbnail) > 500, len(thumbnail or b""))
image = QtGui.QImage.fromData(thumbnail or b"")
check("it is a valid image", not image.isNull())
check("it is 256x256",
      image.width() == fileformat.THUMBNAIL_SIZE
      and image.height() == fileformat.THUMBNAIL_SIZE,
      "%dx%d" % (image.width(), image.height()))

path = os.path.join(WORK, "bracket.pdat")
win.document.path = path
check("save succeeded", win.save_document())
check("the file exists", os.path.exists(path))
check("it is a ZIP", zipfile.is_zipfile(path))

with zipfile.ZipFile(path) as archive:
    stored = archive.read("thumbnail.png")
    manifest = json.loads(archive.read("manifest.json"))
check("the archive carries the preview", len(stored) > 500, len(stored))
stored_image = QtGui.QImage.fromData(stored)
check("the stored preview is a real 256px image",
      not stored_image.isNull() and stored_image.width() == 256,
      stored_image.width())
check("manifest says part", manifest["type"] == "part")
check("manifest names DATUM", manifest["format"] == "DATUM")

print("reopening it through the app")
win.new_document(prompt=False)
pump()
check("opened cleanly", win.open_path(path))
check("the model came back", win.document.shape is not None)
check("path remembered", win.document.path == path, win.document.path)

print("the app refuses what it should")
future = os.path.join(WORK, "future.pdat")
with zipfile.ZipFile(future, "w") as archive:
    archive.writestr("manifest.json", json.dumps({
        "format": "DATUM", "version": fileformat.SCHEMA_VERSION + 3,
        "type": "part", "units": "mm", "created": "x", "modified": "x"}))
    archive.writestr("geometry.json", "{}")

warnings = []
original = QtWidgets.QMessageBox.warning
QtWidgets.QMessageBox.warning = staticmethod(
    lambda *a, **k: warnings.append(a[2] if len(a) > 2 else "")
    or QtWidgets.QMessageBox.Ok)
info = []
original_info = QtWidgets.QMessageBox.information
QtWidgets.QMessageBox.information = staticmethod(
    lambda *a, **k: info.append(a[2] if len(a) > 2 else "")
    or QtWidgets.QMessageBox.Ok)
critical = []
original_critical = QtWidgets.QMessageBox.critical
QtWidgets.QMessageBox.critical = staticmethod(
    lambda *a, **k: critical.append(a[2] if len(a) > 2 else "")
    or QtWidgets.QMessageBox.Ok)

check("a newer file is not opened", not win.open_path(future))
check("and the user is told why",
      warnings and "newer version" in warnings[0], warnings)

assembly_path = os.path.join(WORK, "frame.adat")
asm = AssemblyDocument()
asm.path = assembly_path
asm.add_component(path, "Bracket")
asm.save(assembly_path)
check("an assembly opens as an assembly", win.open_path(assembly_path))
check("and the window switches workspace", win.in_assembly)
check("with its component placed",
      len(win.assembly.occurrences) == 1, len(win.assembly.occurrences))

# back to a part, so the checks that follow are about part loading again
win.open_path(path)
check("and back to a part again", not win.in_assembly)

junk = os.path.join(WORK, "junk.pdat")
with open(junk, "w", encoding="utf-8") as handle:
    handle.write("nope")
check("junk is refused", not win.open_path(junk))
check("with a clear reason",
      critical and "not a DATUM document" in critical[0], critical)

QtWidgets.QMessageBox.warning = original
QtWidgets.QMessageBox.information = original_info
QtWidgets.QMessageBox.critical = original_critical

print("the still-open document was left alone by the failures")
check("model intact", win.document.shape is not None)
check("path intact", win.document.path == path, win.document.path)

print()
if FAILS:
    print("%d FAILURES: %s" % (len(FAILS), ", ".join(FAILS)))
    sys.exit(1)
print("all app save tests passed")
sys.exit(0)
